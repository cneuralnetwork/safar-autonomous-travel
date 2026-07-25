from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.models import UserIdentity


class AuthConfigurationError(RuntimeError):
    pass


def require_google_identity(user: dict[str, Any]) -> dict[str, Any]:
    """Return the Google identity or reject sessions created by any other provider."""
    identities = user.get("identities") or []
    google_identity = next(
        (item for item in identities if item.get("provider") == "google"),
        None,
    )
    if not google_identity:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Google-authenticated accounts can use Safar",
        )
    return google_identity.get("identity_data") or {}


class GoogleAuthStartRequest(BaseModel):
    proof_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class GoogleAuthStartResponse(BaseModel):
    attempt_id: str
    authorization_url: str
    expires_in: int = 300


class GoogleAuthAttemptResponse(BaseModel):
    status: str
    session: dict[str, Any] | None = None
    user: UserIdentity | None = None
    error: str | None = None


@dataclass
class LoginAttempt:
    id: str
    proof_hash: str
    state: str
    nonce: str
    expires_at: datetime
    status: str = "pending"
    session: dict[str, Any] | None = None
    user: UserIdentity | None = None
    error: str | None = None
    exchanged: bool = False


class GoogleAuthBridge:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.attempts: dict[str, LoginAttempt] = {}
        self.state_index: dict[str, str] = {}
        self.client = httpx.AsyncClient(timeout=20)

    def start(self, proof_hash: str) -> GoogleAuthStartResponse:
        if not self.settings.google_auth_ready:
            raise AuthConfigurationError(
                "Google sign-in is not configured. Add Google and Supabase credentials."
            )
        now = datetime.now(UTC)
        self._purge(now)
        attempt_id = secrets.token_urlsafe(24)
        state_value = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        attempt = LoginAttempt(
            id=attempt_id,
            proof_hash=proof_hash,
            state=state_value,
            nonce=nonce,
            expires_at=now + timedelta(minutes=5),
        )
        self.attempts[attempt_id] = attempt
        self.state_index[state_value] = attempt_id
        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.google_callback_url,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state_value,
            "nonce": nonce,
            "access_type": "online",
            "include_granted_scopes": "true",
            "prompt": "select_account",
        }
        return GoogleAuthStartResponse(
            attempt_id=attempt_id,
            authorization_url=f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}",
        )

    async def callback(
        self, state_value: str | None, code: str | None, error: str | None
    ) -> LoginAttempt:
        if not state_value or state_value not in self.state_index:
            raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
        attempt_id = self.state_index.pop(state_value)
        attempt = self.attempts[attempt_id]
        if attempt.expires_at < datetime.now(UTC):
            attempt.status = "failed"
            attempt.error = "The sign-in attempt expired."
            return attempt
        if error or not code:
            attempt.status = "failed"
            attempt.error = "Google sign-in was cancelled." if error == "access_denied" else error
            return attempt
        try:
            token_response = await self.client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret,
                    "redirect_uri": self.settings.google_callback_url,
                    "grant_type": "authorization_code",
                },
            )
            token_response.raise_for_status()
            google_tokens = token_response.json()
            identity = await self._validate_google_id_token(
                google_tokens["id_token"], expected_nonce=attempt.nonce
            )
            session_response = await self.client.post(
                f"{self.settings.supabase_url.rstrip('/')}/auth/v1/token",
                params={"grant_type": "id_token"},
                headers={
                    "apikey": self.settings.supabase_publishable_key or "",
                    "Content-Type": "application/json",
                },
                json={
                    "provider": "google",
                    "id_token": google_tokens["id_token"],
                    "nonce": attempt.nonce,
                },
            )
            session_response.raise_for_status()
            raw_session = session_response.json()
            attempt.session = {
                "access_token": raw_session["access_token"],
                "refresh_token": raw_session["refresh_token"],
            }
            attempt.user = UserIdentity(
                id=str(raw_session["user"]["id"]),
                email=identity["email"],
                name=identity.get("name") or identity["email"].split("@")[0],
                avatar_url=identity.get("picture"),
                google_sub=identity["sub"],
            )
            attempt.status = "completed"
        except (httpx.HTTPError, KeyError, ValueError) as auth_error:
            attempt.status = "failed"
            attempt.error = f"Google sign-in could not be completed: {auth_error}"
        return attempt

    async def exchange(self, attempt_id: str, proof: str) -> GoogleAuthAttemptResponse:
        attempt = self.attempts.get(attempt_id)
        if not attempt or attempt.expires_at < datetime.now(UTC):
            raise HTTPException(status_code=404, detail="Sign-in attempt expired")
        actual_hash = hashlib.sha256(proof.encode()).hexdigest()
        if not hmac.compare_digest(attempt.proof_hash, actual_hash):
            raise HTTPException(status_code=403, detail="Invalid login proof")
        if attempt.status == "completed":
            if attempt.exchanged:
                raise HTTPException(status_code=410, detail="Session has already been exchanged")
            attempt.exchanged = True
            response = GoogleAuthAttemptResponse(
                status="completed", session=attempt.session, user=attempt.user
            )
            attempt.session = None
            return response
        return GoogleAuthAttemptResponse(status=attempt.status, error=attempt.error)

    async def _validate_google_id_token(
        self, id_token: str, *, expected_nonce: str
    ) -> dict[str, Any]:
        response = await self.client.get(
            "https://oauth2.googleapis.com/tokeninfo", params={"id_token": id_token}
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("aud") != self.settings.google_client_id:
            raise ValueError("Google token audience mismatch")
        if payload.get("nonce") != expected_nonce:
            raise ValueError("Google token nonce mismatch")
        if payload.get("email_verified") not in (True, "true"):
            raise ValueError("Google email is not verified")
        return payload

    def _purge(self, now: datetime) -> None:
        expired = [key for key, attempt in self.attempts.items() if attempt.expires_at < now]
        for key in expired:
            self.state_index.pop(self.attempts[key].state, None)
            self.attempts.pop(key, None)


async def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> UserIdentity:
    if settings.auth_disabled:
        if settings.production:
            raise HTTPException(status_code=500, detail="AUTH_DISABLED is forbidden in production")
        return UserIdentity(
            id="00000000-0000-0000-0000-000000000001",
            email="test@safar.local",
            name="Test Traveller",
            google_sub="test-google-sub",
        )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google authentication is required",
        )
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Supabase authentication is not configured")
    access_token = authorization.split(" ", 1)[1]
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "apikey": settings.supabase_publishable_key,
            },
        )
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Session is invalid or expired")
    user = response.json()
    metadata = user.get("user_metadata") or {}
    google_data = require_google_identity(user)
    return UserIdentity(
        id=str(user["id"]),
        email=user.get("email") or google_data.get("email") or "",
        name=metadata.get("full_name") or metadata.get("name") or "Traveller",
        avatar_url=metadata.get("avatar_url") or metadata.get("picture"),
        google_sub=google_data.get("sub") or google_data.get("provider_id"),
    )

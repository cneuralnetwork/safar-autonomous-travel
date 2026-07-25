from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import Settings
from app.models import Itinerary, UserIdentity
from app.store import Store, TokenCipher


@dataclass
class CalendarAttempt:
    state: str
    user: UserIdentity
    expires_at: datetime
    status: str = "pending"
    error: str | None = None


class CalendarService:
    scope = "https://www.googleapis.com/auth/calendar.events.owned"

    def __init__(self, settings: Settings, store: Store, cipher: TokenCipher) -> None:
        self.settings = settings
        self.store = store
        self.cipher = cipher
        self.attempts: dict[str, CalendarAttempt] = {}
        self.client = httpx.AsyncClient(timeout=20)

    async def close(self) -> None:
        await self.client.aclose()

    async def status(self, user_id: str) -> dict[str, Any]:
        connection = await self.store.get_calendar_connection(user_id)
        attempts = [attempt for attempt in self.attempts.values() if attempt.user.id == user_id]
        latest = max(attempts, key=lambda attempt: attempt.expires_at, default=None)
        if latest and latest.status == "pending" and latest.expires_at < datetime.now(UTC):
            latest.status = "failed"
            latest.error = "Calendar connection expired. Please try again."
        return {
            "connected": bool(connection),
            "authorization_status": latest.status if latest else None,
            "error": latest.error if latest else None,
        }

    def start(self, user: UserIdentity) -> dict[str, Any]:
        if not self.settings.google_client_id or not self.settings.google_client_secret:
            raise RuntimeError("Google Calendar OAuth is not configured")
        state_value = secrets.token_urlsafe(32)
        self.attempts[state_value] = CalendarAttempt(
            state=state_value,
            user=user,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.calendar_callback_url,
            "response_type": "code",
            "scope": f"openid email profile {self.scope}",
            "state": state_value,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "login_hint": user.email,
        }
        return {
            "authorization_url": (
                f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
            ),
            "expires_in": 600,
        }

    async def callback(
        self, state_value: str | None, code: str | None, error: str | None
    ) -> CalendarAttempt:
        attempt = self.attempts.get(state_value or "")
        if not attempt or attempt.expires_at < datetime.now(UTC):
            raise ValueError("Invalid or expired Calendar OAuth state")
        if error or not code:
            attempt.status = "failed"
            attempt.error = "Calendar permission was denied."
            return attempt
        try:
            response = await self.client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret,
                    "redirect_uri": self.settings.calendar_callback_url,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            tokens = response.json()
            identity_response = await self.client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": tokens.get("id_token", "")},
            )
            identity_response.raise_for_status()
            identity = identity_response.json()
        except (httpx.HTTPError, KeyError, ValueError):
            attempt.status = "failed"
            attempt.error = (
                "Google Calendar could not be connected. Check the OAuth "
                "configuration and try again."
            )
            return attempt
        if attempt.user.google_sub and identity.get("sub") != attempt.user.google_sub:
            attempt.status = "failed"
            attempt.error = "Calendar account must match the signed-in Google account."
            return attempt
        existing_encrypted = await self.store.get_calendar_connection(attempt.user.id)
        existing = self.cipher.decrypt(existing_encrypted) if existing_encrypted else {}
        if not tokens.get("refresh_token") and existing.get("refresh_token"):
            tokens["refresh_token"] = existing["refresh_token"]
        tokens["expires_at"] = (
            datetime.now(UTC) + timedelta(seconds=int(tokens.get("expires_in", 3600)))
        ).isoformat()
        tokens["google_sub"] = identity.get("sub")
        tokens["email"] = identity.get("email")
        await self.store.save_calendar_connection(attempt.user.id, self.cipher.encrypt(tokens))
        attempt.status = "completed"
        return attempt

    async def disconnect(self, user_id: str) -> None:
        encrypted = await self.store.get_calendar_connection(user_id)
        if encrypted:
            payload = self.cipher.decrypt(encrypted)
            token = payload.get("refresh_token") or payload.get("access_token")
            if token:
                await self.client.post(
                    "https://oauth2.googleapis.com/revoke", params={"token": token}
                )
        await self.store.delete_calendar_connection(user_id)

    async def create_itinerary_events(
        self, user_id: str, run_id: str, itinerary: Itinerary
    ) -> list[str]:
        encrypted = await self.store.get_calendar_connection(user_id)
        if not encrypted:
            raise PermissionError("Google Calendar is not connected")
        tokens = self.cipher.decrypt(encrypted)
        access_token = await self._valid_access_token(user_id, tokens)
        links: list[str] = []
        for day in itinerary.days:
            for item in day.items:
                if item.category == "buffer":
                    continue
                event_id = self._event_id(user_id, run_id, item.id)
                response = await self.client.put(
                    (f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}"),
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "summary": item.title,
                        "description": item.description,
                        "location": item.location,
                        "start": {
                            "dateTime": item.start_at.isoformat(),
                            "timeZone": itinerary.timezone,
                        },
                        "end": {
                            "dateTime": item.end_at.isoformat(),
                            "timeZone": itinerary.timezone,
                        },
                        "extendedProperties": {
                            "private": {
                                "createdBy": "Safar",
                                "safarRunId": run_id,
                                "safarItemId": item.id,
                            }
                        },
                    },
                )
                response.raise_for_status()
                links.append(response.json().get("htmlLink", ""))
        return [link for link in links if link]

    @staticmethod
    def _event_id(user_id: str, run_id: str, item_id: str) -> str:
        # Google accepts base32hex characters for client-supplied event IDs.
        # A stable ID makes a partially failed approved batch safe to retry.
        return hashlib.sha256(f"safar:{user_id}:{run_id}:{item_id}".encode()).hexdigest()[:40]

    async def _valid_access_token(self, user_id: str, tokens: dict[str, Any]) -> str:
        expires_at = datetime.fromisoformat(tokens["expires_at"])
        if expires_at > datetime.now(UTC) + timedelta(minutes=2):
            return str(tokens["access_token"])
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise PermissionError("Calendar access expired; reconnect Google Calendar")
        response = await self.client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
        refreshed = response.json()
        tokens["access_token"] = refreshed["access_token"]
        tokens["expires_at"] = (
            datetime.now(UTC) + timedelta(seconds=int(refreshed.get("expires_in", 3600)))
        ).isoformat()
        await self.store.save_calendar_connection(user_id, self.cipher.encrypt(tokens))
        return str(tokens["access_token"])

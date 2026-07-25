import hashlib
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import HTTPException

from app.auth import GoogleAuthBridge, require_google_identity
from app.config import Settings


def test_google_identity_is_required_even_for_a_valid_supabase_session() -> None:
    with pytest.raises(HTTPException) as error:
        require_google_identity(
            {
                "id": "user-id",
                "identities": [
                    {
                        "provider": "email",
                        "identity_data": {"email": "traveller@example.com"},
                    }
                ],
            }
        )

    assert error.value.status_code == 403
    assert error.value.detail == "Only Google-authenticated accounts can use Safar"


def test_google_identity_data_is_returned() -> None:
    identity = require_google_identity(
        {
            "identities": [
                {
                    "provider": "google",
                    "identity_data": {
                        "sub": "google-subject",
                        "email": "traveller@example.com",
                    },
                }
            ]
        }
    )

    assert identity["sub"] == "google-subject"


async def test_google_callback_forwards_the_original_nonce_to_supabase() -> None:
    settings = Settings(
        public_base_url="https://api.safar.example",
        google_client_id="google-client-id",
        google_client_secret="google-client-secret",
        supabase_url="https://safar.supabase.co",
        supabase_publishable_key="supabase-publishable-key",
    )
    bridge = GoogleAuthBridge(settings)
    start = bridge.start(hashlib.sha256(b"proof").hexdigest())
    attempt = bridge.attempts[start.attempt_id]
    hashed_nonce = hashlib.sha256(attempt.nonce.encode()).hexdigest()

    authorization_params = parse_qs(urlparse(start.authorization_url).query)
    assert authorization_params["nonce"] == [hashed_nonce]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"id_token": "google-id-token"})
        if request.url == httpx.URL(
            "https://oauth2.googleapis.com/tokeninfo?id_token=google-id-token"
        ):
            return httpx.Response(
                200,
                json={
                    "aud": settings.google_client_id,
                    "nonce": hashed_nonce,
                    "email_verified": True,
                    "email": "traveller@example.com",
                    "name": "Safar Traveller",
                    "sub": "google-subject",
                },
            )
        if request.url == httpx.URL(
            "https://safar.supabase.co/auth/v1/token?grant_type=id_token"
        ):
            payload = json.loads(request.content)
            assert payload == {
                "provider": "google",
                "id_token": "google-id-token",
                "nonce": attempt.nonce,
            }
            return httpx.Response(
                200,
                json={
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "user": {"id": "00000000-0000-0000-0000-000000000123"},
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    await bridge.client.aclose()
    bridge.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await bridge.callback(attempt.state, "authorization-code", None)
    finally:
        await bridge.client.aclose()

    assert result.status == "completed"
    assert result.user is not None
    assert result.user.email == "traveller@example.com"

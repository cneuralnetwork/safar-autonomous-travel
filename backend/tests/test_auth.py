import pytest
from fastapi import HTTPException

from app.auth import require_google_identity


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

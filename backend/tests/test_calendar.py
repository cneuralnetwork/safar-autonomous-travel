from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx

from app.calendar_service import CalendarService
from app.config import Settings
from app.models import Itinerary, ItineraryDay, ItineraryItem, UserIdentity
from app.store import SQLiteStore, TokenCipher


async def test_calendar_event_writes_are_idempotent_upserts(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "calendar.db"))
    await store.initialize()
    cipher = TokenCipher(None)
    service = CalendarService(Settings(), store, cipher)
    await store.save_calendar_connection(
        "user-1",
        cipher.encrypt(
            {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
        ),
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={"htmlLink": "https://calendar.google.com/event?eid=test"},
        )

    await service.client.aclose()
    service.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    itinerary = Itinerary(
        days=[
            ItineraryDay(
                date=date(2026, 8, 14),
                title="Arrival",
                items=[
                    ItineraryItem(
                        id="item-1",
                        title="Fly CCU → GOI",
                        description="Outbound flight",
                        start_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
                        end_at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
                        category="flight",
                    )
                ],
            )
        ]
    )

    first = await service.create_itinerary_events("user-1", "run-1", itinerary)
    second = await service.create_itinerary_events("user-1", "run-1", itinerary)
    await service.client.aclose()

    assert first == second
    assert len(requests) == 2
    assert all(request.method == "PUT" for request in requests)
    assert requests[0].url == requests[1].url
    assert requests[0].url.path.endswith(
        CalendarService._event_id("user-1", "run-1", "item-1")
    )


async def test_calendar_permission_denial_is_immediately_observable(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "calendar-denial.db"))
    await store.initialize()
    service = CalendarService(
        Settings(
            google_client_id="client-id",
            google_client_secret="client-secret",
            public_base_url="https://safar.example",
        ),
        store,
        TokenCipher(None),
    )
    user = UserIdentity(
        id="user-1",
        email="traveller@example.com",
        google_sub="google-subject",
    )
    connection = service.start(user)
    state = parse_qs(urlparse(connection["authorization_url"]).query)["state"][0]

    attempt = await service.callback(state, None, "access_denied")
    status = await service.status(user.id)

    assert attempt.status == "failed"
    assert status == {
        "connected": False,
        "authorization_status": "failed",
        "error": "Calendar permission was denied.",
    }


async def test_calendar_token_exchange_failure_is_immediately_observable(
    tmp_path,
) -> None:
    store = SQLiteStore(str(tmp_path / "calendar-token-failure.db"))
    await store.initialize()
    service = CalendarService(
        Settings(
            google_client_id="client-id",
            google_client_secret="client-secret",
            public_base_url="https://safar.example",
        ),
        store,
        TokenCipher(None),
    )
    user = UserIdentity(
        id="user-1",
        email="traveller@example.com",
        google_sub="google-subject",
    )
    connection = service.start(user)
    state = parse_qs(urlparse(connection["authorization_url"]).query)["state"][0]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            request=request,
            json={"error": "invalid_grant"},
        )

    await service.client.aclose()
    service.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    attempt = await service.callback(state, "invalid-code", None)
    status = await service.status(user.id)
    await service.client.aclose()

    assert attempt.status == "failed"
    assert status["authorization_status"] == "failed"
    assert "OAuth configuration" in str(status["error"])

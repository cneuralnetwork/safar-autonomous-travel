import asyncio
from pathlib import Path

import pytest

from app.calendar_service import CalendarService
from app.config import Settings
from app.interpreter import RequestInterpreter
from app.models import (
    ApprovalDecision,
    RunStatus,
    SendMessageRequest,
    UserIdentity,
)
from app.orchestrator import CalendarConnectionRequired, Orchestrator
from app.store import SQLiteStore, TokenCipher
from app.travel_tools import TravelToolRegistry


async def build_orchestrator(path: Path) -> tuple[Orchestrator, UserIdentity]:
    settings = Settings(
        app_env="test",
        auth_disabled=True,
        database_path=path,
        travel_provider_mode="demo",
    )
    store = SQLiteStore(str(path))
    await store.initialize()
    calendar = CalendarService(settings, store, TokenCipher(None))
    orchestrator = Orchestrator(
        store,
        RequestInterpreter(settings),
        TravelToolRegistry(settings),
        calendar,
    )
    user = UserIdentity(
        id="00000000-0000-0000-0000-000000000001",
        email="traveller@example.com",
        name="Traveller",
        google_sub="google-1",
    )
    return orchestrator, user


async def wait_for_status(
    orchestrator: Orchestrator,
    user: UserIdentity,
    conversation_id,
    expected: RunStatus,
) -> None:
    for _ in range(80):
        snapshot = await orchestrator.snapshot(user, conversation_id)
        if snapshot.active_run and snapshot.active_run.status == expected:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"run did not reach {expected}")


async def test_end_to_end_demo_run_records_retry_and_approval(tmp_path: Path) -> None:
    orchestrator, user = await build_orchestrator(tmp_path / "safar-test.db")
    snapshot = await orchestrator.create_conversation(user, None, True)
    run = await orchestrator.handle_message(
        user,
        snapshot.conversation.id,
        SendMessageRequest(
            text=(
                "plan a 3-day trip from Kolkata to Goa next weekend for two people "
                "under ₹30,000, avoid flights before 8 am and stay near the beach"
            ),
            idempotency_key="test-message-001",
            resilience_demo=True,
        ),
    )

    assert run.graph is not None
    await wait_for_status(orchestrator, user, snapshot.conversation.id, RunStatus.awaiting_approval)
    ready = await orchestrator.snapshot(user, snapshot.conversation.id)
    active = ready.active_run
    assert active is not None
    assert active.retries >= 1
    assert active.selected_package is not None
    assert active.selected_package.total_price <= 30_000
    assert active.itinerary is not None
    assert active.approval is not None

    with pytest.raises(CalendarConnectionRequired):
        await orchestrator.approve(
            user,
            active.id,
            ApprovalDecision(decision="approve", payload_hash=active.approval.payload_hash),
        )

    cancelled = await orchestrator.approve(
        user,
        active.id,
        ApprovalDecision(decision="cancel", payload_hash=active.approval.payload_hash),
    )
    assert cancelled.status == RunStatus.completed
    report = await orchestrator.report(user, active.id)
    assert report.tools_called >= 3
    assert report.retries >= 1
    assert report.calendar_event_links == []


async def test_message_idempotency_does_not_create_second_run(tmp_path: Path) -> None:
    orchestrator, user = await build_orchestrator(tmp_path / "idempotency.db")
    snapshot = await orchestrator.create_conversation(user, None, False)
    payload = SendMessageRequest(
        text="plan a 3-day trip from Kolkata to Goa next weekend under ₹30,000",
        idempotency_key="stable-message-key",
    )
    first = await orchestrator.handle_message(user, snapshot.conversation.id, payload)
    second = await orchestrator.handle_message(user, snapshot.conversation.id, payload)

    assert first.id == second.id
    persisted = await orchestrator.snapshot(user, snapshot.conversation.id)
    user_messages = [message for message in persisted.messages if message.role == "user"]
    assert len(user_messages) == 1
    assert user_messages[0].run_id == first.id

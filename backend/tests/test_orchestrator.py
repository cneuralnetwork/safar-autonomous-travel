import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.agent_model import (
    AgentPlanDraft,
    ModelMetrics,
    PlanTaskDraft,
    StructuredModelResult,
    TravelConstraintPatch,
    TurnInterpretation,
)
from app.calendar_service import CalendarService
from app.config import Settings
from app.interpreter import RequestInterpreter
from app.models import (
    ApprovalDecision,
    RunState,
    RunStatus,
    SendMessageRequest,
    TaskStatus,
    UserIdentity,
)
from app.orchestrator import CalendarConnectionRequired, Orchestrator
from app.planner import build_task_graph
from app.store import SQLiteStore, TokenCipher
from app.travel_tools import NoResultsError, TemporaryToolError, TravelToolRegistry


async def build_orchestrator(path: Path) -> tuple[Orchestrator, UserIdentity]:
    settings = Settings(
        app_env="test",
        auth_disabled=True,
        database_path=path,
        travel_provider_mode="demo",
        sarvam_api_key=None,
        serpapi_api_key=None,
        google_maps_api_key=None,
        amadeus_client_id=None,
        amadeus_client_secret=None,
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


async def test_usual_preferences_are_reused_in_a_new_conversation(tmp_path: Path) -> None:
    orchestrator, user = await build_orchestrator(tmp_path / "preferences.db")
    first = await orchestrator.create_conversation(
        user,
        (
            "plan a 3-day trip from Kolkata to Goa next weekend for two people "
            "under ₹30,000, avoid flights before 8 am and stay near the beach"
        ),
        False,
    )
    assert first.active_run is not None

    second = await orchestrator.create_conversation(
        user,
        (
            "plan another 3-day trip to Jaipur next weekend under ₹35,000 "
            "with my usual preferences"
        ),
        False,
    )

    run = second.active_run
    assert run is not None
    assert run.constraints.origin == "Kolkata"
    assert run.constraints.origin_airport == "CCU"
    assert run.constraints.destination == "Jaipur"
    assert run.constraints.earliest_departure is not None
    assert run.constraints.earliest_departure.hour == 8
    assert run.constraints.hotel_area_preference == "beach"
    assert run.constraints.missing_fields == []


async def test_running_workflow_is_resumed_from_persistent_state(tmp_path: Path) -> None:
    database = tmp_path / "recovery.db"
    first, user = await build_orchestrator(database)
    snapshot = await first.create_conversation(user, None, False)
    constraints = await first.interpreter.interpret(
        "plan a 3-day trip from Kolkata to Goa next weekend under ₹30,000"
    )
    run = RunState(
        conversation_id=snapshot.conversation.id,
        user_id=user.id,
        status=RunStatus.running,
        constraints=constraints,
        graph=build_task_graph(constraints),
    )
    run.graph.tasks[2].status = TaskStatus.running
    await first.store.save_run(run)

    recovered, _ = await build_orchestrator(database)
    assert await recovered.recover_pending_runs() == 1
    await wait_for_status(
        recovered,
        user,
        snapshot.conversation.id,
        RunStatus.awaiting_approval,
    )
    resumed = await recovered.snapshot(user, snapshot.conversation.id)

    assert any(
        message.payload.get("event", {}).get("task_id") == "workflow_recovery"
        for message in resumed.messages
    )


async def test_approval_edit_starts_a_revised_run_with_existing_constraints(
    tmp_path: Path,
) -> None:
    orchestrator, user = await build_orchestrator(tmp_path / "approval-edit.db")
    snapshot = await orchestrator.create_conversation(
        user,
        (
            "plan a 3-day trip from Kolkata to Goa next weekend under ₹30,000, "
            "avoid flights before 8 am"
        ),
        False,
    )
    await wait_for_status(
        orchestrator,
        user,
        snapshot.conversation.id,
        RunStatus.awaiting_approval,
    )
    original = (await orchestrator.snapshot(user, snapshot.conversation.id)).active_run
    assert original is not None
    assert original.approval is not None

    revised = await orchestrator.approve(
        user,
        original.id,
        ApprovalDecision(
            decision="edit",
            payload_hash=original.approval.payload_hash,
            edit_message="Increase the budget by ₹5,000",
        ),
    )

    assert revised.id != original.id
    assert revised.constraints.origin == "Kolkata"
    assert revised.constraints.destination == "Goa"
    assert revised.constraints.budget == 35_000
    assert revised.constraints.earliest_departure is not None
    assert revised.constraints.earliest_departure.hour == 8


async def test_place_search_failure_keeps_the_valid_trip_and_labels_flexible_time(
    tmp_path: Path,
) -> None:
    orchestrator, user = await build_orchestrator(tmp_path / "place-fallback.db")
    orchestrator.tools.places.search = AsyncMock(
        side_effect=TemporaryToolError("places timeout")
    )
    snapshot = await orchestrator.create_conversation(
        user,
        "plan a 3-day trip from Kolkata to Goa next weekend under ₹30,000",
        False,
    )

    await wait_for_status(
        orchestrator,
        user,
        snapshot.conversation.id,
        RunStatus.awaiting_approval,
    )
    run = (await orchestrator.snapshot(user, snapshot.conversation.id)).active_run

    assert run is not None
    assert run.itinerary is not None
    assert any(
        item.title == "Flexible time in Goa"
        for day in run.itinerary.days
        for item in day.items
    )
    assert any("Place search fallback" in error for error in run.errors)


async def test_sarvam_controls_interpretation_and_planning_with_durable_telemetry(
    tmp_path: Path,
) -> None:
    orchestrator, user = await build_orchestrator(tmp_path / "model-harness.db")

    def metrics(phase: str) -> ModelMetrics:
        return ModelMetrics(
            phase=phase,
            model="sarvam-105b",
            prompt_version=f"test-{phase}-v1",
            status="completed",
            attempts=1,
            latency_ms=42,
            prompt_tokens=20,
            completion_tokens=10,
            total_tokens=30,
        )

    model_agent = AsyncMock()
    model_agent.interpret.return_value = StructuredModelResult(
        value=TurnInterpretation(
            intent="plan_trip",
            constraints=TravelConstraintPatch(
                origin="Chennai",
                destination="Jaipur",
                budget=40_000,
                adults=1,
            ),
            explicit_fields=["origin", "destination", "budget"],
            inferred_fields=["start_date", "end_date", "adults"],
            assumptions=["Using Friday through Sunday for next weekend."],
            assistant_message="I have the route, weekend, and budget.",
        ),
        metrics=metrics("interpretation"),
    )
    model_agent.plan.return_value = StructuredModelResult(
        value=AgentPlanDraft(
            goal="Plan a Chennai to Jaipur weekend trip",
            assumptions=[],
            tasks=[
                PlanTaskDraft(
                    id="flights",
                    title="Search current flights",
                    description="Search round-trip flight options.",
                    tool_name="search_flights",
                ),
                PlanTaskDraft(
                    id="hotels",
                    title="Search available stays",
                    description="Search hotel options.",
                    tool_name="search_hotels",
                ),
            ],
        ),
        metrics=metrics("planning"),
    )
    orchestrator.interpreter.gateway.api_key = "test-only"
    orchestrator.interpreter.agent = model_agent
    orchestrator.agent = model_agent

    snapshot = await orchestrator.create_conversation(
        user,
        "I wanna go to Jaipur from Chennai, budget 40000, next weekend",
        False,
    )
    await wait_for_status(
        orchestrator,
        user,
        snapshot.conversation.id,
        RunStatus.awaiting_approval,
    )
    run = (await orchestrator.snapshot(user, snapshot.conversation.id)).active_run

    assert run is not None
    assert run.harness_version == 2
    assert run.model_calls == 2
    assert run.agent_cycles == 2
    assert run.constraints.origin == "Chennai"
    assert run.constraints.destination == "Jaipur"
    assert run.graph is not None
    assert run.graph.goal == "Plan a Chennai to Jaipur weekend trip"
    assert model_agent.interpret.await_count == 1
    assert model_agent.plan.await_count == 1

    calls = await orchestrator.store.list_model_calls(run.id, user.id)
    events = await orchestrator.store.list_events(run.id, user.id)
    assert [call.phase for call in calls] == ["interpretation", "planning"]
    assert {"model_completed", "plan_created", "approval_required"} <= {
        event.type for event in events
    }


async def test_replan_retries_inside_one_run_lease(tmp_path: Path) -> None:
    orchestrator, user = await build_orchestrator(tmp_path / "lease-loop.db")
    run_id = uuid4()
    orchestrator.store.claim_run = AsyncMock(return_value=True)
    orchestrator.store.release_run = AsyncMock(return_value=None)
    orchestrator._execute = AsyncMock(side_effect=[True, False])

    await orchestrator._execute_claimed(run_id, user)

    assert orchestrator._execute.await_count == 2
    orchestrator.store.claim_run.assert_awaited_once_with(
        run_id,
        orchestrator.worker_id,
    )
    orchestrator.store.release_run.assert_awaited_once_with(
        run_id,
        orchestrator.worker_id,
    )


class EmptyTravelProvider:
    name = "empty"

    async def search_flights(self, _constraints):
        raise NoResultsError("no flights")

    async def search_hotels(self, _constraints):
        raise NoResultsError("no hotels")


async def test_exhausted_travel_providers_return_to_chat_for_replanning(
    tmp_path: Path,
) -> None:
    orchestrator, user = await build_orchestrator(tmp_path / "provider-failure.db")
    orchestrator.tools.providers = [EmptyTravelProvider()]
    snapshot = await orchestrator.create_conversation(
        user,
        "plan a 3-day trip from Kolkata to Goa next weekend under ₹30,000",
        False,
    )

    await wait_for_status(
        orchestrator,
        user,
        snapshot.conversation.id,
        RunStatus.awaiting_input,
    )
    result = await orchestrator.snapshot(user, snapshot.conversation.id)

    assert result.active_run is not None
    assert result.active_run.graph is not None
    assert result.messages[-1].kind == "clarification"
    assert "retries and fallbacks" in result.messages[-1].text

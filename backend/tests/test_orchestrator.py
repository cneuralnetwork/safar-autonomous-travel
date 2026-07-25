import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

from app.agent_model import (
    AgentPlanDraft,
    ModelMetrics,
    PlanTaskDraft,
    SarvamModelError,
    StructuredModelResult,
    TravelConstraintPatch,
    TurnInterpretation,
)
from app.config import Settings
from app.interpreter import RequestInterpreter
from app.models import (
    ApprovalDecision,
    RunState,
    RunStatus,
    SendMessageRequest,
    TaskStatus,
    TripSelectionRequest,
    UserIdentity,
)
from app.orchestrator import Orchestrator
from app.planner import build_task_graph
from app.store import SQLiteStore
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
    orchestrator = Orchestrator(
        store,
        RequestInterpreter(settings),
        TravelToolRegistry(settings),
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


async def wait_for_selection_stage(
    orchestrator: Orchestrator,
    user: UserIdentity,
    conversation_id,
    expected: str,
) -> RunState:
    for _ in range(80):
        snapshot = await orchestrator.snapshot(user, conversation_id)
        run = snapshot.active_run
        if run and run.status == RunStatus.awaiting_input and run.selection_stage == expected:
            return run
        await asyncio.sleep(0.05)
    raise AssertionError(f"run did not reach selection stage {expected}")


async def choose_cheapest_trip(
    orchestrator: Orchestrator,
    user: UserIdentity,
    conversation_id,
) -> RunState:
    outbound_run = await wait_for_selection_stage(
        orchestrator,
        user,
        conversation_id,
        "outbound_flight",
    )
    outbound = min(outbound_run.outbound_flights, key=lambda item: item.total_price)
    await orchestrator.select_option(
        user,
        outbound_run.id,
        TripSelectionRequest(kind="outbound_flight", option_id=outbound.id),
    )

    return_run = await wait_for_selection_stage(
        orchestrator,
        user,
        conversation_id,
        "return_flight",
    )
    inbound = min(return_run.return_flights, key=lambda item: item.total_price)
    await orchestrator.select_option(
        user,
        return_run.id,
        TripSelectionRequest(kind="return_flight", option_id=inbound.id),
    )

    hotel_run = await wait_for_selection_stage(
        orchestrator,
        user,
        conversation_id,
        "hotel",
    )
    hotel = min(hotel_run.hotels, key=lambda item: item.total_price)
    await orchestrator.select_option(
        user,
        hotel_run.id,
        TripSelectionRequest(kind="hotel", option_id=hotel.id),
    )
    await wait_for_status(
        orchestrator,
        user,
        conversation_id,
        RunStatus.awaiting_approval,
    )
    ready = await orchestrator.snapshot(user, conversation_id)
    assert ready.active_run is not None
    return ready.active_run


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
    active = await choose_cheapest_trip(
        orchestrator,
        user,
        snapshot.conversation.id,
    )
    assert active.retries >= 1
    assert active.selected_package is not None
    assert active.selected_package.total_price <= 30_000
    assert active.itinerary is not None
    assert active.approval is not None
    assert active.selected_outbound_id is not None
    assert active.selected_return_id is not None
    assert active.selected_hotel_id is not None
    assert active.selected_package.flight.inbound

    exported = await orchestrator.approve(
        user,
        active.id,
        ApprovalDecision(decision="approve", payload_hash=active.approval.payload_hash),
    )
    assert exported.status == RunStatus.completed
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


async def test_traveller_chooses_each_leg_and_can_change_it_in_chat(
    tmp_path: Path,
) -> None:
    orchestrator, user = await build_orchestrator(tmp_path / "guided-choices.db")
    snapshot = await orchestrator.create_conversation(
        user,
        (
            "plan a 3-day trip from Kolkata to Goa next weekend for two people "
            "under ₹60,000 and avoid flights before 8 am"
        ),
        False,
    )

    outbound_run = await wait_for_selection_stage(
        orchestrator,
        user,
        snapshot.conversation.id,
        "outbound_flight",
    )
    chosen_outbound = outbound_run.outbound_flights[1]
    same_run = await orchestrator.handle_message(
        user,
        snapshot.conversation.id,
        SendMessageRequest(
            text="choose option 2",
            idempotency_key="guided-outbound-choice",
        ),
    )
    assert same_run.id == outbound_run.id

    return_run = await wait_for_selection_stage(
        orchestrator,
        user,
        snapshot.conversation.id,
        "return_flight",
    )
    chosen_return = return_run.return_flights[2]
    await orchestrator.handle_message(
        user,
        snapshot.conversation.id,
        SendMessageRequest(
            text="choose return option 3",
            idempotency_key="guided-return-choice",
        ),
    )

    hotel_run = await wait_for_selection_stage(
        orchestrator,
        user,
        snapshot.conversation.id,
        "hotel",
    )
    chosen_hotel = hotel_run.hotels[1]
    await orchestrator.handle_message(
        user,
        snapshot.conversation.id,
        SendMessageRequest(
            text="pick the second stay",
            idempotency_key="guided-hotel-choice",
        ),
    )
    await wait_for_status(
        orchestrator,
        user,
        snapshot.conversation.id,
        RunStatus.awaiting_approval,
    )
    ready = (await orchestrator.snapshot(user, snapshot.conversation.id)).active_run
    assert ready is not None
    assert ready.approval is not None
    assert ready.selected_outbound_id == chosen_outbound.id
    assert ready.selected_return_id == chosen_return.id
    assert ready.selected_hotel_id == chosen_hotel.id
    assert ready.selected_package is not None
    assert (
        ready.selected_package.flight.total_price
        == chosen_outbound.total_price + chosen_return.total_price
    )
    previous_hash = ready.approval.payload_hash

    await orchestrator.handle_message(
        user,
        snapshot.conversation.id,
        SendMessageRequest(
            text="change my return to option 1",
            idempotency_key="guided-return-change",
        ),
    )
    await wait_for_status(
        orchestrator,
        user,
        snapshot.conversation.id,
        RunStatus.awaiting_approval,
    )
    revised = (await orchestrator.snapshot(user, snapshot.conversation.id)).active_run
    assert revised is not None
    assert revised.id == ready.id
    assert revised.selected_return_id == return_run.return_flights[0].id
    assert revised.approval is not None
    assert revised.approval.payload_hash != previous_hash


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
        ("plan another 3-day trip to Jaipur next weekend under ₹35,000 with my usual preferences"),
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
    await choose_cheapest_trip(
        recovered,
        user,
        snapshot.conversation.id,
    )
    resumed = await recovered.snapshot(user, snapshot.conversation.id)

    assert any(
        message.payload.get("event", {}).get("task_id") == "workflow_recovery"
        for message in resumed.messages
    )


async def test_recovery_preserves_a_completed_traveller_choice(tmp_path: Path) -> None:
    database = tmp_path / "selection-recovery.db"
    first, user = await build_orchestrator(database)
    snapshot = await first.create_conversation(user, None, False)
    constraints = await first.interpreter.interpret(
        "plan a 3-day trip from Kolkata to Goa next weekend under ₹40,000"
    )
    outbound = await first.tools.demo.search_outbound_flights(constraints)
    run = RunState(
        conversation_id=snapshot.conversation.id,
        user_id=user.id,
        status=RunStatus.planned,
        phase="executing",
        harness_version=3,
        constraints=constraints,
        graph=build_task_graph(constraints),
        outbound_flights=outbound,
        selected_outbound_id=outbound[1].id,
    )
    for task_id in (
        "resolve_locations",
        "outbound_flight_search",
        "choose_outbound_flight",
    ):
        task = next(task for task in run.graph.tasks if task.id == task_id)
        task.status = TaskStatus.completed
    await first.store.save_run(run)

    recovered, _ = await build_orchestrator(database)
    assert await recovered.recover_pending_runs() == 1
    resumed = await wait_for_selection_stage(
        recovered,
        user,
        snapshot.conversation.id,
        "return_flight",
    )

    assert resumed.selected_outbound_id == outbound[1].id
    assert resumed.outbound_flights


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
    await choose_cheapest_trip(
        orchestrator,
        user,
        snapshot.conversation.id,
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
    orchestrator.tools.places.search = AsyncMock(side_effect=TemporaryToolError("places timeout"))
    snapshot = await orchestrator.create_conversation(
        user,
        "plan a 3-day trip from Kolkata to Goa next weekend under ₹30,000",
        False,
    )

    await choose_cheapest_trip(
        orchestrator,
        user,
        snapshot.conversation.id,
    )
    run = (await orchestrator.snapshot(user, snapshot.conversation.id)).active_run

    assert run is not None
    assert run.itinerary is not None
    assert any(
        item.title == "Flexible time in Goa" for day in run.itinerary.days for item in day.items
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
                    id="outbound",
                    title="Search flights there",
                    description="Search one-way outbound flight options.",
                    tool_name="search_outbound_flights",
                ),
                PlanTaskDraft(
                    id="return",
                    title="Search flights back",
                    description="Search one-way return flight options.",
                    tool_name="search_return_flights",
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
    await choose_cheapest_trip(
        orchestrator,
        user,
        snapshot.conversation.id,
    )
    run = (await orchestrator.snapshot(user, snapshot.conversation.id)).active_run

    assert run is not None
    assert run.harness_version == 3
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


async def test_sarvam_plan_failure_uses_the_validated_fallback_graph(
    tmp_path: Path,
) -> None:
    orchestrator, user = await build_orchestrator(tmp_path / "model-plan-fallback.db")
    model_agent = AsyncMock()
    model_agent.interpret.return_value = StructuredModelResult(
        value=TurnInterpretation(
            intent="plan_trip",
            constraints=TravelConstraintPatch(
                origin="Kolkata",
                destination="Goa",
                visual_theme="coast",
            ),
            explicit_fields=["origin", "destination"],
            assistant_message="I understood the route and dates.",
        ),
        metrics=ModelMetrics(
            phase="interpretation",
            model="sarvam-105b",
            prompt_version="test-interpretation-v1",
            status="completed",
            attempts=1,
            latency_ms=42,
        ),
    )
    failed_metrics = ModelMetrics(
        phase="planning",
        model="sarvam-105b",
        prompt_version="test-planning-v1",
        status="failed",
        attempts=2,
        latency_ms=4_000,
        error_code="truncated_model_output",
        error_message="Structured output ended early",
    )
    model_agent.plan.side_effect = SarvamModelError(
        "Structured output ended early",
        failed_metrics,
    )
    orchestrator.interpreter.gateway.api_key = "test-only"
    orchestrator.interpreter.agent = model_agent
    orchestrator.agent = model_agent

    snapshot = await orchestrator.create_conversation(
        user,
        "Plan a trip from Kolkata to Goa next weekend under ₹30,000",
        False,
    )
    await choose_cheapest_trip(
        orchestrator,
        user,
        snapshot.conversation.id,
    )
    run = (await orchestrator.snapshot(user, snapshot.conversation.id)).active_run

    assert run is not None
    assert run.graph is not None
    assert run.status == RunStatus.awaiting_approval
    assert any("validated fallback workflow" in item for item in run.assumptions)
    events = await orchestrator.store.list_events(run.id, user.id)
    assert "model_failed" in {event.type for event in events}
    assert "plan_created" in {event.type for event in events}


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

    async def search_outbound_flights(self, _constraints):
        raise NoResultsError("no outbound flights")

    async def search_return_flights(self, _constraints):
        raise NoResultsError("no return flights")

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

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx

from app.agent_model import ModelMetrics, ReplanDecision, SarvamModelError
from app.interpreter import RequestInterpreter
from app.itinerary import create_itinerary
from app.models import (
    AgentEvent,
    AgentPhase,
    ApprovalDecision,
    ApprovalPayload,
    ChatMessage,
    Conversation,
    ConversationSnapshot,
    ExecutionReport,
    FlightLegOption,
    HotelOption,
    MessageKind,
    ModelCallRecord,
    OperationEvent,
    RunState,
    RunStatus,
    SelectionKind,
    SendMessageRequest,
    TaskNode,
    TaskStatus,
    TransportMode,
    TravelConstraints,
    TripSelectionRequest,
    UserIdentity,
    UserPreferences,
    utc_now,
)
from app.planner import build_task_graph
from app.solver import SolverResult, compare_packages
from app.store import Store
from app.travel_tools import (
    NoResultsError,
    TemporaryToolError,
    ToolError,
    TravelToolRegistry,
    combine_flight_legs,
)


class Orchestrator:
    def __init__(
        self,
        store: Store,
        interpreter: RequestInterpreter,
        tools: TravelToolRegistry,
    ) -> None:
        self.store = store
        self.interpreter = interpreter
        self.agent = interpreter.agent
        self.tools = tools
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.background_tasks: set[asyncio.Task[Any]] = set()

    async def close(self) -> None:
        for task in self.background_tasks:
            task.cancel()
        if self.background_tasks:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        await self.tools.close()

    async def recover_pending_runs(self) -> int:
        recovered = 0
        for run in await self.store.list_recoverable_runs():
            if not run.graph:
                continue
            legacy_graph = not self._task_or_none(run, "outbound_flight_search")
            if legacy_graph:
                run.graph = build_task_graph(run.constraints)
            run.harness_version = 3
            for task in run.graph.tasks:
                if task.id in {"understand_request", "resolve_constraints"}:
                    continue
                if not legacy_graph and task.status not in {
                    TaskStatus.running,
                    TaskStatus.retrying,
                    TaskStatus.failed,
                }:
                    continue
                task.status = TaskStatus.waiting
                task.attempts = 0
                task.provider = None
                task.summary = None
                task.reason = None
                task.started_at = None
                task.completed_at = None
            run.status = RunStatus.planned
            run.phase = AgentPhase.executing
            if legacy_graph:
                run.outbound_flights = []
                run.return_flights = []
                run.selected_outbound_id = None
                run.selected_return_id = None
                run.selected_hotel_id = None
                run.selection_stage = None
                run.flights = []
                run.hotels = []
                run.places = []
                run.packages = []
                run.selected_package = None
                run.itinerary = None
                run.approval = None
            await self.store.save_run(run)
            await self._persist_graph(run)
            event = OperationEvent(
                task_id="workflow_recovery",
                status=TaskStatus.retrying,
                summary="Resumed the workflow after a service restart.",
                reason="Persistent task state was recovered before any external write.",
            )
            await self._message(
                run,
                MessageKind.operation,
                event.summary,
                {"event": event.model_dump(mode="json")},
                role="tool",
            )
            self._schedule_execution(
                run.id,
                UserIdentity(id=run.user_id, email=""),
            )
            recovered += 1
        return recovered

    async def create_conversation(
        self, user: UserIdentity, initial_message: str | None, resilience_demo: bool
    ) -> ConversationSnapshot:
        conversation = Conversation(user_id=user.id)
        await self.store.create_conversation(conversation)
        if initial_message:
            await self.handle_message(
                user,
                conversation.id,
                SendMessageRequest(
                    text=initial_message,
                    idempotency_key=f"initial-{conversation.id}",
                    resilience_demo=resilience_demo,
                ),
            )
        return await self.snapshot(user, conversation.id)

    async def snapshot(self, user: UserIdentity, conversation_id: UUID) -> ConversationSnapshot:
        conversation = await self.store.get_conversation(conversation_id, user.id)
        if not conversation:
            raise LookupError("Conversation not found")
        return ConversationSnapshot(
            conversation=conversation,
            messages=await self.store.list_messages(conversation_id, user.id),
            active_run=await self.store.get_active_run(conversation_id, user.id),
        )

    async def handle_message(
        self,
        user: UserIdentity,
        conversation_id: UUID,
        request: SendMessageRequest,
        *,
        base_constraints_override: TravelConstraints | None = None,
    ) -> RunState:
        conversation = await self.store.get_conversation(conversation_id, user.id)
        if not conversation:
            raise LookupError("Conversation not found")
        current_run = await self.store.get_active_run(conversation_id, user.id)
        user_message = ChatMessage(
            conversation_id=conversation_id,
            run_id=current_run.id if current_run else None,
            role="user",
            text=request.text,
        )
        persisted = await self.store.add_message(user_message, request.idempotency_key)
        if persisted.id != user_message.id:
            existing_run = await self.store.get_active_run(conversation_id, user.id)
            if not existing_run:
                raise RuntimeError("Idempotent message exists without a run")
            return existing_run

        if (
            current_run
            and current_run.graph
            and current_run.status in {RunStatus.awaiting_input, RunStatus.awaiting_approval}
        ):
            selection = self._selection_from_text(current_run, request.text)
            if selection:
                kind, option_id = selection
                return await self.select_option(
                    user,
                    current_run.id,
                    TripSelectionRequest(kind=kind, option_id=option_id),
                )
            transport_request = self._transport_request_from_text(
                current_run,
                request.text,
            )
            if transport_request:
                kind, mode = transport_request
                return await self._handle_transport_request(
                    user,
                    current_run,
                    kind,
                    mode,
                )

        memory_applied = False
        saved_preferences = await self.store.get_user_preferences(user.id)
        preference_confirmation_requested = False
        if current_run and current_run.preference_confirmation_pending:
            preference_decision = self._preference_confirmation_decision(request.text)
            if preference_decision is None:
                await self._message(
                    current_run,
                    MessageKind.clarification,
                    self._saved_preferences_confirmation(saved_preferences),
                    {"quick_replies": [], "confirmation": "saved_preferences"},
                )
                return current_run
            current_run.preference_confirmation_pending = False
            current_run.saved_preferences_applied = bool(preference_decision and saved_preferences)
            if preference_decision and saved_preferences:
                base_constraints = self._apply_confirmed_preferences(
                    current_run.constraints,
                    saved_preferences,
                )
                memory_applied = True
                current_run.assumptions = list(
                    dict.fromkeys(
                        [
                            *current_run.assumptions,
                            "Applied saved preferences after user confirmation",
                        ]
                    )
                )
            else:
                base_constraints = current_run.constraints
            await self.store.save_run(current_run)
        else:
            base_constraints = base_constraints_override or (
                current_run.constraints
                if current_run
                and current_run.status in {RunStatus.awaiting_input, RunStatus.awaiting_approval}
                else None
            )
            preference_confirmation_requested = bool(
                saved_preferences
                and self._requests_saved_preferences(request.text)
                and not (
                    current_run
                    and current_run.status == RunStatus.awaiting_input
                    and current_run.graph is None
                    and current_run.saved_preferences_applied
                )
            )
        run = (
            current_run
            if (
                current_run
                and current_run.status == RunStatus.awaiting_input
                and current_run.graph is None
            )
            else RunState(
                conversation_id=conversation_id,
                user_id=user.id,
                harness_version=3,
                resilience_demo=bool(request.resilience_demo),
            )
        )
        run.status = RunStatus.interpreting
        run.phase = AgentPhase.interpreting
        await self.store.save_run(run)
        user_message.run_id = run.id
        await self.store.update_message_run_id(user_message.id, run.id, user.id)
        await self._event(
            run,
            event_type="model_started" if self.interpreter.has_model else "interpreter_started",
            status="running",
            summary=(
                "Sarvam is interpreting your trip request."
                if self.interpreter.has_model
                else "The local interpreter is validating your trip request."
            ),
            payload={"model": self.interpreter.settings.sarvam_model},
        )

        recent_messages = await self.store.list_messages(conversation_id, user.id)
        try:
            interpretation = await self.interpreter.interpret_turn(
                request.text,
                base_constraints,
                preferences=(
                    saved_preferences.model_dump(mode="json")
                    if memory_applied and saved_preferences
                    else {}
                ),
                recent_messages=[
                    {"role": message.role, "content": message.text}
                    for message in recent_messages[-12:]
                    if message.role in {"user", "assistant"}
                ],
            )
        except SarvamModelError as error:
            await self._record_model_call(run, error.metrics)
            run.status = RunStatus.paused
            run.phase = AgentPhase.paused
            run.errors.append(str(error))
            await self.store.save_run(run)
            await self._message(
                run,
                MessageKind.error,
                (
                    "Sarvam could not interpret this request after automatic retries. "
                    "Try again shortly."
                ),
                {"error_code": error.metrics.error_code, "recoverable": True},
            )
            return run

        if interpretation.model_metrics:
            await self._record_model_call(run, interpretation.model_metrics)
        constraints = interpretation.constraints
        run.constraints = constraints
        run.assumptions = list(dict.fromkeys([*run.assumptions, *interpretation.assumptions]))
        await self.store.save_run(run)
        conversation.last_message = request.text
        conversation.updated_at = utc_now()
        if constraints.destination:
            conversation.destination = constraints.destination
            conversation.visual_theme = constraints.visual_theme
            conversation.title = f"{constraints.destination} trip"
        await self.store.update_conversation(conversation)

        await self._message(
            run,
            MessageKind.interpretation,
            interpretation.assistant_message,
            {
                "constraints": constraints.model_dump(mode="json"),
                "missing_fields": constraints.missing_fields,
                "memory_applied": memory_applied,
                "assumptions": interpretation.assumptions,
                "model": (
                    interpretation.model_metrics.model if interpretation.model_metrics else None
                ),
            },
        )
        if preference_confirmation_requested:
            run.preference_confirmation_pending = True
            run.saved_preferences_applied = False
            run.status = RunStatus.awaiting_input
            run.phase = AgentPhase.awaiting_input
            await self.store.save_run(run)
            await self._message(
                run,
                MessageKind.clarification,
                self._saved_preferences_confirmation(saved_preferences),
                {"quick_replies": [], "confirmation": "saved_preferences"},
            )
            return run
        if constraints.missing_fields:
            run.status = RunStatus.awaiting_input
            run.phase = AgentPhase.awaiting_input
            await self.store.save_run(run)
            await self._message(
                run,
                MessageKind.clarification,
                self._clarification(constraints),
                {
                    "quick_replies": (
                        interpretation.quick_replies or self._quick_replies(constraints)
                    )
                },
            )
            return run

        await self._remember_preferences(user.id, constraints)
        run.status = RunStatus.planning
        run.phase = AgentPhase.planning
        await self.store.save_run(run)
        await self._event(
            run,
            event_type="model_started" if self.interpreter.has_model else "planner_started",
            status="running",
            summary=(
                "Sarvam is creating the execution graph."
                if self.interpreter.has_model
                else "The safe planner is creating the execution graph."
            ),
        )
        try:
            if self.interpreter.has_model:
                planned = await self.agent.plan(
                    constraints=constraints.model_dump(mode="json"),
                    assumptions=run.assumptions,
                )
                await self._record_model_call(run, planned.metrics)
                run.assumptions = list(
                    dict.fromkeys([*run.assumptions, *planned.value.assumptions])
                )
                run.graph = build_task_graph(constraints, planned.value)
            else:
                run.graph = build_task_graph(constraints)
        except SarvamModelError as error:
            await self._record_model_call(run, error.metrics)
            run.assumptions = list(
                dict.fromkeys(
                    [
                        *run.assumptions,
                        (
                            "Safar used its validated fallback workflow after "
                            "the model plan ended early."
                        ),
                    ]
                )
            )
            run.graph = build_task_graph(constraints)

        run.status = RunStatus.planned
        run.phase = AgentPhase.executing
        await self.store.save_run(run)
        await self._persist_graph(run)
        await self._event(
            run,
            event_type="plan_created",
            status="completed",
            summary=f"Created a validated {run.graph.estimated_steps}-step task graph.",
            payload={"graph": run.graph.model_dump(mode="json")},
        )
        await self._message(
            run,
            MessageKind.task_graph,
            f"I created a {run.graph.estimated_steps}-step execution plan.",
            {"graph": run.graph.model_dump(mode="json")},
        )
        self._schedule_execution(run.id, user)
        return run

    def _schedule_execution(self, run_id: UUID, user: UserIdentity) -> None:
        task = asyncio.create_task(self._execute_claimed(run_id, user))
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    async def _execute_claimed(self, run_id: UUID, user: UserIdentity) -> None:
        if not await self.store.claim_run(run_id, self.worker_id):
            return
        heartbeat = asyncio.create_task(self._lease_heartbeat(run_id))
        try:
            should_retry = True
            while should_retry:
                should_retry = await self._execute(run_id, user)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await self.store.release_run(run_id, self.worker_id)

    async def _lease_heartbeat(self, run_id: UUID) -> None:
        while True:
            await asyncio.sleep(45)
            if not await self.store.claim_run(run_id, self.worker_id):
                return

    async def _execute(self, run_id: UUID, user: UserIdentity) -> bool:
        run = await self.store.get_run(run_id, user.id)
        if not run or not run.graph:
            return False
        run.status = RunStatus.running
        run.phase = AgentPhase.executing
        await self.store.save_run(run)
        try:
            location_task = self._task_or_none(run, "resolve_locations")
            if location_task and location_task.status != TaskStatus.completed:
                await self._start_task(run, location_task)
                origin_airport, destination_airport = await self.tools.resolve_locations(
                    run.constraints
                )
                run.constraints.origin_airport = origin_airport
                run.constraints.destination_airport = destination_airport
                run.graph.constraints = run.constraints
                await self._complete_task(
                    run,
                    location_task,
                    (
                        f"Resolved airport route {origin_airport} → {destination_airport}"
                        if origin_airport and destination_airport
                        else "No complete airport pair; ground transport fallback is available"
                    ),
                    (
                        "Airport identifiers were resolved by the controlled location registry"
                        if origin_airport and destination_airport
                        else (
                            "Safar will search verified railway schedules and mapped road "
                            "connections instead of inventing airport codes"
                        )
                    ),
                )

            if not run.outbound_flights:
                run.outbound_flights = await self._run_search_task(
                    run,
                    "outbound_flight_search",
                    "search_outbound_flights",
                )
                run.outbound_flights = self._filter_flight_options(
                    run.outbound_flights,
                    run.constraints,
                )
                if not run.outbound_flights:
                    raise NoResultsError("No outbound journey matches the requested departure time")
                await self.store.save_run(run)
                await self._await_selection(run, "outbound_flight")
                return False
            if not self._selected_outbound(run):
                run.selected_outbound_id = None
                await self._await_selection(run, "outbound_flight")
                return False

            if not run.return_flights:
                run.return_flights = await self._run_search_task(
                    run,
                    "return_flight_search",
                    "search_return_flights",
                )
                run.return_flights = self._filter_flight_options(
                    run.return_flights,
                    run.constraints,
                )
                if not run.return_flights:
                    raise NoResultsError("No return journey matches the requested departure time")
                await self.store.save_run(run)
                await self._await_selection(run, "return_flight")
                return False
            if not self._selected_return(run):
                run.selected_return_id = None
                await self._await_selection(run, "return_flight")
                return False

            if not run.hotels:
                run.hotels = await self._run_search_task(
                    run,
                    "hotel_search",
                    "search_hotels",
                )
                run.hotels = [hotel for hotel in run.hotels if hotel.available]
                if not run.hotels:
                    raise NoResultsError("No available stays matched the trip")
                await self.store.save_run(run)
                await self._await_selection(run, "hotel")
                return False
            if not self._selected_hotel(run):
                run.selected_hotel_id = None
                await self._await_selection(run, "hotel")
                return False

            if not run.places:
                run.places = await self._run_places_task(run)
                await self.store.save_run(run)

            outbound = self._selected_outbound(run)
            inbound = self._selected_return(run)
            hotel = self._selected_hotel(run)
            if not outbound or not inbound or not hotel:
                raise ValueError("A selected travel option is no longer available")
            combined_flight = combine_flight_legs(outbound, inbound)
            run.flights = [combined_flight]

            compare_task = self._task(run, "compare_packages")
            await self._start_task(run, compare_task)
            solver_result = compare_packages([combined_flight], [hotel], run.constraints)
            if not solver_result.valid:
                fallback_reason = self._impossible_budget_message(
                    run.constraints,
                    solver_result,
                )
                decision = await self._model_replan(
                    run,
                    failure={
                        "kind": "no_valid_package",
                        "task_id": compare_task.id,
                        "rejection_summary": solver_result.rejection_summary,
                    },
                    fallback_message=fallback_reason,
                )
                reason = decision.user_message if decision else fallback_reason
                compare_task.status = TaskStatus.failed
                compare_task.summary = "No valid package satisfies every hard constraint"
                compare_task.reason = reason
                compare_task.completed_at = utc_now()
                run.status = RunStatus.awaiting_input
                run.phase = AgentPhase.awaiting_input
                await self.store.save_task(run, compare_task)
                await self.store.save_run(run)
                await self._message(
                    run,
                    MessageKind.clarification,
                    reason,
                    {
                        "quick_replies": [
                            *(
                                decision.quick_replies
                                if decision and decision.quick_replies
                                else [
                                    "Change outbound to option 1",
                                    "Change return to option 1",
                                    "Change stay to option 1",
                                    "Increase the budget by ₹5,000",
                                ]
                            )
                        ][:4],
                        "rejections": solver_result.rejection_summary,
                    },
                )
                return False
            run.packages = solver_result.valid
            run.selected_package = solver_result.valid[0]
            await self._complete_task(
                run,
                compare_task,
                "Validated your three selected options",
                (
                    "The chosen outbound journey, return journey, and stay "
                    "satisfy the hard constraints"
                ),
            )
            await self._message(
                run,
                MessageKind.budget,
                (
                    (
                        f"Your selected trip is ₹{run.selected_package.total_price:,}, "
                        f"leaving ₹{run.selected_package.remaining_budget:,}."
                    )
                    if run.selected_package.remaining_budget is not None
                    else (f"Your selected trip is ₹{run.selected_package.total_price:,}.")
                ),
                {
                    "selected_package": run.selected_package.model_dump(mode="json"),
                    "alternatives": [],
                    "rejected_count": solver_result.rejected_count,
                    "rejection_summary": solver_result.rejection_summary,
                },
            )

            itinerary_task = self._task(run, "create_itinerary")
            await self._start_task(run, itinerary_task)
            run.itinerary = create_itinerary(run.selected_package, run.places, run.constraints)
            await self._complete_task(
                run,
                itinerary_task,
                f"Created a {len(run.itinerary.days)}-day itinerary",
                "Grouped activities geographically and preserved airport buffers",
            )
            await self._message(
                run,
                MessageKind.itinerary,
                f"Your {len(run.itinerary.days)}-day itinerary is ready.",
                {"itinerary": run.itinerary.model_dump(mode="json")},
            )

            approval_task = self._task(run, "request_approval")
            approval_task.status = TaskStatus.awaiting_approval
            approval_task.started_at = utc_now()
            payload_hash = hashlib.sha256(
                (run.selected_package.model_dump_json() + run.itinerary.model_dump_json()).encode()
            ).hexdigest()
            event_count = sum(
                1 for day in run.itinerary.days for item in day.items if item.category != "buffer"
            )
            run.approval = ApprovalPayload(
                event_count=event_count,
                estimated_trip_total=run.selected_package.total_price,
                payload_hash=payload_hash,
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            run.status = RunStatus.awaiting_approval
            run.phase = AgentPhase.awaiting_approval
            await self.store.save_task(run, approval_task)
            await self.store.save_run(run)
            await self._event(
                run,
                event_type="approval_required",
                status="awaiting_approval",
                summary=f"Calendar file ready with {event_count} trip events.",
                task_id=approval_task.id,
                payload={"approval": run.approval.model_dump(mode="json")},
            )
            await self._message(
                run,
                MessageKind.approval,
                f"Ready to export {event_count} events as a portable calendar file.",
                {"approval": run.approval.model_dump(mode="json")},
            )
            return False
        except ToolError as error:
            error_message = self._safe_external_error(error)
            run.errors.append(error_message)
            decision = await self._model_replan(
                run,
                failure={
                    "kind": "travel_provider_failure",
                    "error": error_message,
                },
                fallback_message=(
                    "The travel providers could not return a complete set of options "
                    "after retries and fallbacks. Change a constraint and I’ll try again."
                ),
            )
            if decision and decision.action in {"retry", "switch_provider"} and run.replans <= 3:
                for task_id in (
                    "outbound_flight_search",
                    "return_flight_search",
                    "hotel_search",
                ):
                    task = self._task_or_none(run, task_id)
                    if task and task.status == TaskStatus.failed:
                        task.status = TaskStatus.waiting
                        task.reason = None
                        task.completed_at = None
                        await self.store.save_task(run, task)
                run.status = RunStatus.planned
                run.phase = AgentPhase.executing
                await self.store.save_run(run)
                await self._event(
                    run,
                    event_type="replan_applied",
                    status="completed",
                    summary=decision.explanation,
                    payload={"action": decision.action},
                )
                return True

            run.status = RunStatus.awaiting_input
            run.phase = AgentPhase.awaiting_input
            await self.store.save_run(run)
            await self._message(
                run,
                MessageKind.clarification,
                (
                    decision.user_message
                    if decision
                    else (
                        "The travel providers could not return a complete set of options "
                        "after retries and fallbacks. Change a constraint and I’ll try again."
                    )
                ),
                {
                    "quick_replies": (
                        decision.quick_replies
                        if decision and decision.quick_replies
                        else [
                            "Allow earlier flights",
                            "Show hotels a little farther away",
                            "I’ll give different dates",
                        ]
                    ),
                    "error": error_message,
                },
            )
            return False
        except Exception as error:
            error_code = type(error).__name__
            run.errors.append(f"Unexpected internal error: {error_code}")
            run.status = RunStatus.failed
            run.phase = AgentPhase.failed
            await self.store.save_run(run)
            await self._event(
                run,
                event_type="run_failed",
                status="failed",
                summary="The agent run stopped safely.",
                reason="An unexpected internal error occurred.",
                payload={"error_code": error_code},
            )
            await self._message(
                run,
                MessageKind.error,
                "I could not finish this run safely.",
                {"error_code": error_code, "recoverable": False},
            )
            return False

    async def select_option(
        self,
        user: UserIdentity,
        run_id: UUID,
        request: TripSelectionRequest,
    ) -> RunState:
        run = await self.store.get_run(run_id, user.id)
        if not run or not run.graph:
            raise LookupError("Trip selection request not found")
        if run.status not in {RunStatus.awaiting_input, RunStatus.awaiting_approval}:
            raise ValueError("Safar is not waiting for a travel choice")

        options = self._selection_options(run, request.kind)
        selected = next(
            (option for option in options if option.id == request.option_id),
            None,
        )
        if not selected:
            raise ValueError("That option is no longer available")

        previous_stage = run.selection_stage
        if request.kind == "outbound_flight":
            run.selected_outbound_id = selected.id
            task_id = "choose_outbound_flight"
            label = self._flight_label(selected)
            noun = "journey there"
        elif request.kind == "return_flight":
            run.selected_return_id = selected.id
            task_id = "choose_return_flight"
            label = self._flight_label(selected)
            noun = "journey back"
        else:
            run.selected_hotel_id = selected.id
            task_id = "choose_hotel"
            label = selected.name
            noun = "stay"

        selection_task = self._task(run, task_id)
        selection_task.status = TaskStatus.completed
        selection_task.summary = f"Selected {label}"
        selection_task.reason = "Chosen by the traveller; it can still be changed in chat"
        selection_task.completed_at = utc_now()
        await self.store.save_task(run, selection_task)

        self._invalidate_final_plan(run)
        for downstream_id in (
            "compare_packages",
            "create_itinerary",
            "request_approval",
            "add_calendar",
            "create_report",
        ):
            downstream = self._task_or_none(run, downstream_id)
            if downstream:
                await self.store.save_task(run, downstream)
        if previous_stage == request.kind:
            run.selection_stage = None
        else:
            run.selection_stage = previous_stage
        run.status = RunStatus.planned
        run.phase = AgentPhase.executing
        await self.store.save_run(run)
        await self._event(
            run,
            event_type="option_selected",
            status="completed",
            summary=f"Traveller selected {label}.",
            task_id=task_id,
            payload={
                "kind": request.kind,
                "option_id": selected.id,
                "label": label,
            },
        )
        await self._message(
            run,
            MessageKind.selection_confirmation,
            (
                f"Got it — {label} is your {noun}. "
                "You can change it here any time before final approval."
            ),
            {
                "kind": request.kind,
                "option_id": selected.id,
                "label": label,
            },
        )
        self._schedule_execution(run.id, user)
        return run

    async def _await_selection(
        self,
        run: RunState,
        kind: SelectionKind,
    ) -> None:
        if kind == "outbound_flight":
            task_id = "choose_outbound_flight"
            message_kind = MessageKind.flight_selection
            options = run.outbound_flights[:6]
            text = (
                "Choose your journey there. Safar may combine flights, trains, and "
                "road connections when a direct route is unavailable."
            )
            payload = {
                "leg": "outbound",
                "selection_kind": kind,
                "flights": [option.model_dump(mode="json") for option in options],
            }
        elif kind == "return_flight":
            task_id = "choose_return_flight"
            message_kind = MessageKind.flight_selection
            options = run.return_flights[:6]
            text = (
                "Now choose your journey back. Return routes are searched separately "
                "and may use a different transport mix."
            )
            payload = {
                "leg": "return",
                "selection_kind": kind,
                "flights": [option.model_dump(mode="json") for option in options],
            }
        else:
            task_id = "choose_hotel"
            message_kind = MessageKind.hotel_selection
            options = run.hotels[:6]
            text = "Choose where you want to stay. You can tap a card or name it in chat."
            payload = {
                "selection_kind": kind,
                "hotels": [option.model_dump(mode="json") for option in options],
            }

        task = self._task(run, task_id)
        task.status = TaskStatus.awaiting_input
        task.started_at = task.started_at or utc_now()
        task.summary = "Waiting for your choice"
        task.reason = "Safar will not choose this travel option on your behalf"
        run.selection_stage = kind
        run.status = RunStatus.awaiting_input
        run.phase = AgentPhase.awaiting_input
        await self.store.save_task(run, task)
        await self.store.save_run(run)
        await self._event(
            run,
            event_type="selection_required",
            status="awaiting_input",
            summary=text,
            task_id=task_id,
            payload={"kind": kind, "option_count": len(options)},
        )
        await self._message(run, message_kind, text, payload)

    @staticmethod
    def _filter_flight_options(
        options: list[FlightLegOption],
        constraints: TravelConstraints,
    ) -> list[FlightLegOption]:
        return [
            option
            for option in options
            if (
                not constraints.earliest_departure
                or option.departure_at.time() >= constraints.earliest_departure
            )
            and (
                not constraints.latest_departure
                or option.departure_at.time() <= constraints.latest_departure
            )
        ]

    @staticmethod
    def _selected_outbound(run: RunState) -> FlightLegOption | None:
        return next(
            (option for option in run.outbound_flights if option.id == run.selected_outbound_id),
            None,
        )

    @staticmethod
    def _selected_return(run: RunState) -> FlightLegOption | None:
        return next(
            (option for option in run.return_flights if option.id == run.selected_return_id),
            None,
        )

    @staticmethod
    def _selected_hotel(run: RunState) -> HotelOption | None:
        return next(
            (hotel for hotel in run.hotels if hotel.id == run.selected_hotel_id),
            None,
        )

    @staticmethod
    def _selection_options(
        run: RunState,
        kind: SelectionKind,
    ) -> list[FlightLegOption] | list[HotelOption]:
        if kind == "outbound_flight":
            return run.outbound_flights
        if kind == "return_flight":
            return run.return_flights
        return run.hotels

    @staticmethod
    def _flight_label(option: FlightLegOption) -> str:
        first = option.segments[0]
        service_number = f" {first.flight_number}" if first.flight_number else ""
        modes = " + ".join(dict.fromkeys(segment.mode.title() for segment in option.segments))
        return (
            f"{modes}: {first.airline}{service_number} "
            f"at {first.departure_at.strftime('%-I:%M %p')}"
        )

    @staticmethod
    def _invalidate_final_plan(run: RunState) -> None:
        run.flights = []
        run.packages = []
        run.selected_package = None
        run.itinerary = None
        run.approval = None
        run.calendar_event_links = []
        run.completed_at = None
        for task_id in (
            "compare_packages",
            "create_itinerary",
            "request_approval",
            "add_calendar",
            "create_report",
        ):
            task = Orchestrator._task_or_none(run, task_id)
            if task:
                task.status = TaskStatus.waiting
                task.summary = None
                task.reason = None
                task.started_at = None
                task.completed_at = None

    def _selection_from_text(
        self,
        run: RunState,
        text: str,
    ) -> tuple[SelectionKind, str] | None:
        normalized = " ".join(text.lower().split())
        kind: SelectionKind | None = None
        if re.search(
            r"\b(return|inbound|flight back|train back|bus back|coming back)\b",
            normalized,
        ):
            kind = "return_flight"
        elif re.search(
            r"\b(outbound|departure|flight there|train there|bus there|going flight)\b",
            normalized,
        ):
            kind = "outbound_flight"
        elif re.search(r"\b(hotel|stay|room|property)\b", normalized):
            kind = "hotel"
        elif run.selection_stage:
            kind = run.selection_stage
        if not kind:
            return None

        options = self._selection_options(run, kind)
        if not options:
            return None
        if re.search(r"\b(cheapest|lowest price|least expensive)\b", normalized):
            selected = min(options, key=lambda option: option.total_price)
            return kind, selected.id

        index: int | None = None
        numeric = re.search(r"(?:option|choice)\s*#?\s*(\d+)", normalized)
        if numeric:
            index = int(numeric.group(1)) - 1
        else:
            ordinals = {
                "first": 0,
                "second": 1,
                "third": 2,
                "fourth": 3,
                "fifth": 4,
                "sixth": 5,
            }
            index = next(
                (
                    value
                    for word, value in ordinals.items()
                    if re.search(rf"\b{word}\b", normalized)
                ),
                None,
            )
        if index is not None and 0 <= index < len(options):
            return kind, options[index].id

        for option in options:
            if isinstance(option, HotelOption):
                searchable = [option.name, option.address]
            else:
                searchable = [segment.airline for segment in option.segments] + [
                    segment.flight_number or "" for segment in option.segments
                ]
            if any(value and value.lower() in normalized for value in searchable):
                return kind, option.id
        return None

    @staticmethod
    def _transport_request_from_text(
        run: RunState,
        text: str,
    ) -> tuple[SelectionKind | None, TransportMode] | None:
        normalized = " ".join(text.lower().split())
        directive = re.search(
            (
                r"\b(take|choose|pick|use|show|prefer|want|switch|change|travel|"
                r"go|going|via|by)\b"
            ),
            normalized,
        )
        explicit_leg = re.search(
            (
                r"\b(trains?|rail|railway|flights?|planes?|bus(?:es)?|coaches)\s+"
                r"(there|back|outbound|return|inbound)\b"
            ),
            normalized,
        )
        if not directive and not explicit_leg:
            return None

        mode: TransportMode | None = None
        if re.search(r"\b(trains?|rail|railway)\b", normalized):
            mode = "train"
        elif re.search(r"\b(bus(?:es)?|coaches)\b", normalized):
            mode = "bus"
        elif re.search(r"\b(flights?|fly|planes?)\b", normalized):
            mode = "flight"
        if not mode:
            return None

        kind: SelectionKind | None = None
        if re.search(
            r"\b(return|inbound|back|coming back)\b",
            normalized,
        ):
            kind = "return_flight"
        elif re.search(
            r"\b(outbound|departure|there|going there)\b",
            normalized,
        ):
            kind = "outbound_flight"
        elif run.selection_stage in {"outbound_flight", "return_flight"}:
            kind = run.selection_stage
        return kind, mode

    async def _handle_transport_request(
        self,
        user: UserIdentity,
        run: RunState,
        kind: SelectionKind | None,
        mode: TransportMode,
    ) -> RunState:
        if kind not in {"outbound_flight", "return_flight"}:
            await self._message(
                run,
                MessageKind.clarification,
                (
                    f"Should I use {mode} for the journey there or the journey back? "
                    "Your current plan is unchanged."
                ),
                {"quick_replies": [], "transport_mode": mode},
            )
            return run

        options = [
            option
            for option in self._selection_options(run, kind)
            if isinstance(option, FlightLegOption) and mode in option.modes
        ]
        if len(options) == 1:
            return await self.select_option(
                user,
                run.id,
                TripSelectionRequest(kind=kind, option_id=options[0].id),
            )
        if len(options) > 1:
            if kind == "outbound_flight":
                run.outbound_flights = options
                run.selected_outbound_id = None
            else:
                run.return_flights = options
                run.selected_return_id = None
            self._invalidate_final_plan(run)
            await self.store.save_run(run)
            await self._await_selection(run, kind)
            return run

        leg = "return" if kind == "return_flight" else "outbound"
        task_id = "return_flight_search" if kind == "return_flight" else "outbound_flight_search"
        task = self._task(run, task_id)
        task.status = TaskStatus.retrying
        task.started_at = task.started_at or utc_now()
        task.reason = f"Traveller requested {mode} for the active {leg} leg"
        task.provider = self.tools.rail.name if mode == "train" and self.tools.rail else mode
        run.status = RunStatus.running
        run.phase = AgentPhase.executing
        await self.store.save_task(run, task)
        await self.store.save_run(run)
        await self._event(
            run,
            event_type="transport_search_started",
            status="running",
            summary=f"Searching verified {mode} options for the {leg} leg.",
            task_id=task_id,
            payload={"leg": leg, "mode": mode},
        )

        try:
            if mode == "train":
                await self._resolve_rail_station_candidates(run)
            run.provider_calls += 1
            refreshed = await self.tools.search_transport_journeys(
                run.constraints,
                leg,
                mode,
            )
            refreshed = self._filter_flight_options(refreshed, run.constraints)
            if not refreshed:
                raise NoResultsError(
                    f"No verified {mode} option matches the requested departure time"
                )
        except (ToolError, httpx.HTTPError) as error:
            reason = self._safe_external_error(error)
            task.status = TaskStatus.completed
            task.summary = f"No verified {mode} option was found"
            task.reason = reason
            task.completed_at = utc_now()
            run.status = RunStatus.awaiting_input
            run.phase = AgentPhase.awaiting_input
            await self.store.save_task(run, task)
            await self.store.save_run(run)
            await self._message(
                run,
                MessageKind.clarification,
                (
                    f"I couldn’t find a verified {mode} schedule for this {leg} leg. "
                    "I kept your current choices instead of restarting the trip."
                ),
                {
                    "quick_replies": [],
                    "transport_mode": mode,
                    "leg": leg,
                    "reason": reason,
                },
            )
            return run

        refreshed.sort(
            key=lambda option: (
                option.total_price,
                option.departure_at,
            )
        )
        if kind == "outbound_flight":
            run.outbound_flights = refreshed
            run.selected_outbound_id = None
        else:
            run.return_flights = refreshed
            run.selected_return_id = None
        self._invalidate_final_plan(run)
        task.status = TaskStatus.completed
        task.summary = f"Found {len(refreshed)} verified {mode} options"
        task.reason = "The active leg was refreshed without restarting the trip"
        task.completed_at = utc_now()
        await self.store.save_task(run, task)
        await self.store.save_run(run)
        await self._event(
            run,
            event_type="transport_search_completed",
            status="completed",
            summary=f"Found {len(refreshed)} verified {mode} options.",
            task_id=task_id,
            payload={"leg": leg, "mode": mode, "option_count": len(refreshed)},
        )
        await self._await_selection(run, kind)
        return run

    async def approve(
        self, user: UserIdentity, run_id: UUID, decision: ApprovalDecision
    ) -> RunState:
        run = await self.store.get_run(run_id, user.id)
        if not run or not run.graph or not run.approval or not run.itinerary:
            raise LookupError("Approval request not found")
        if run.status != RunStatus.awaiting_approval:
            raise ValueError("This approval has already been resolved")
        if run.approval.expires_at < datetime.now(UTC):
            raise ValueError("Approval expired; regenerate the plan")
        if decision.payload_hash != run.approval.payload_hash:
            raise ValueError("The plan changed after this approval card was created")
        approval_task = self._task(run, "request_approval")
        if decision.decision == "cancel":
            approval_task.status = TaskStatus.completed
            approval_task.summary = "User skipped the calendar file export"
            self._task(run, "add_calendar").status = TaskStatus.skipped
            run.status = RunStatus.completed
            run.phase = AgentPhase.completed
            run.completed_at = utc_now()
            await self.store.save_run(run)
            await self._message(
                run,
                MessageKind.calendar,
                "Calendar export was skipped. Your trip plan is still saved.",
                {"created": 0, "format": "ics"},
            )
            await self._finalize_report(run)
            return run
        if decision.decision == "edit":
            if not decision.edit_message:
                raise ValueError("Describe the requested change")
            approval_task.status = TaskStatus.skipped
            run.status = RunStatus.completed
            run.phase = AgentPhase.completed
            run.completed_at = utc_now()
            await self.store.save_run(run)
            return await self.handle_message(
                user,
                run.conversation_id,
                SendMessageRequest(
                    text=decision.edit_message,
                    idempotency_key=f"edit-{run.id}-{hashlib.sha1(decision.edit_message.encode()).hexdigest()}",
                    resilience_demo=run.resilience_demo,
                ),
                base_constraints_override=run.constraints,
            )

        approval_task.status = TaskStatus.completed
        approval_task.summary = "User requested a portable calendar file"
        approval_task.completed_at = utc_now()
        calendar_task = self._task(run, "add_calendar")
        await self._start_task(run, calendar_task)
        run.calendar_event_links = []
        await self._complete_task(
            run,
            calendar_task,
            f"Prepared {run.approval.event_count} events for calendar export",
            "The portable file is generated on the user’s device",
        )
        run.status = RunStatus.completed
        run.phase = AgentPhase.finalizing
        run.completed_at = utc_now()
        await self.store.save_run(run)
        await self._message(
            run,
            MessageKind.calendar,
            "Your portable calendar file is ready to download.",
            {
                "created": run.approval.event_count,
                "format": "ics",
                "links": [],
            },
        )
        await self._finalize_report(run)
        run.phase = AgentPhase.completed
        await self.store.save_run(run)
        return run

    async def report(self, user: UserIdentity, run_id: UUID) -> ExecutionReport:
        run = await self.store.get_run(run_id, user.id)
        if (
            not run
            or not run.graph
            or not run.selected_package
            or not run.itinerary
            or run.status != RunStatus.completed
        ):
            raise LookupError("Completed execution report not found")
        prices = [package.total_price for package in run.packages]
        return ExecutionReport(
            run_id=run.id,
            goal=run.graph.goal,
            interpreted_constraints=run.constraints,
            task_graph=run.graph,
            selected_package=run.selected_package,
            itinerary=run.itinerary,
            calendar_event_links=run.calendar_event_links,
            tools_called=run.provider_calls,
            model_calls=run.model_calls,
            retries=run.retries,
            replans=run.replans,
            assumptions=run.assumptions,
            rejected_packages=max(0, len(run.flights) * len(run.hotels) - len(run.packages)),
            estimated_savings=max(prices) - run.selected_package.total_price if prices else 0,
            elapsed_seconds=round(
                ((run.completed_at or utc_now()) - run.started_at).total_seconds(), 2
            ),
        )

    async def _run_places_task(self, run: RunState) -> list[Any]:
        task = self._task_or_none(run, "place_search")
        if not task:
            return []
        await self._start_task(run, task)
        try:
            places = await self._retry_single(
                run,
                task,
                "openstreetmap",
                lambda: self.tools.places.search(run.constraints),
            )
            await self._complete_task(
                run,
                task,
                (
                    f"Found {len(places)} verified itinerary places"
                    if places
                    else "No verified places were added in this environment"
                ),
                (
                    "Every activity has a named OpenStreetMap feature and coordinates"
                    if places
                    else "Safar leaves itinerary time flexible instead of inventing places"
                ),
            )
            return places
        except (ToolError, httpx.HTTPError) as error:
            error_message = self._safe_external_error(error)
            run.errors.append(f"Place search fallback: {error_message}")
            decision = await self._model_replan(
                run,
                failure={
                    "kind": "optional_place_search_failure",
                    "task_id": task.id,
                    "error": error_message,
                },
                fallback_message=(
                    "Place search was unavailable, so I’ll preserve flexible time near the hotel."
                ),
            )
            await self._complete_task(
                run,
                task,
                (
                    decision.user_message
                    if decision
                    else "Place search was unavailable; continuing with flexible time blocks"
                ),
                "The flight and hotel plan remains valid; no place data was invented",
            )
            return []

    async def _run_search_task(self, run: RunState, task_id: str, method_name: str) -> list[Any]:
        task = self._task(run, task_id)
        await self._start_task(run, task)
        errors: list[str] = []
        journey_task = task_id in {
            "outbound_flight_search",
            "return_flight_search",
        }
        has_airport_pair = bool(
            run.constraints.origin_airport and run.constraints.destination_airport
        )
        search_providers = self.tools.providers if not journey_task or has_airport_pair else []
        if journey_task and not has_airport_pair:
            errors.append("No complete airport pair exists for the requested cities")
        for provider_index, provider in enumerate(search_providers):
            method: Callable[[TravelConstraints], Awaitable[list[Any]]] = getattr(
                provider, method_name
            )
            for attempt in range(task.retry_policy + 1):
                task.attempts += 1
                task.provider = provider.name
                run.provider_calls += 1
                try:
                    if (
                        run.resilience_demo
                        and task_id == "outbound_flight_search"
                        and provider_index == 0
                        and attempt == 0
                    ):
                        raise TemporaryToolError("Injected provider timeout")
                    results = await method(run.constraints)
                    if task_id == "hotel_search":
                        results = await self.tools.enrich_hotel_distances(
                            results,
                            run.constraints,
                        )
                    if task_id in {
                        "outbound_flight_search",
                        "return_flight_search",
                        "hotel_search",
                    }:
                        results = sorted(
                            results,
                            key=lambda option: option.total_price,
                        )
                    await self._complete_task(
                        run,
                        task,
                        f"{provider.name} returned {len(results)} options",
                        (
                            "Fallback provider succeeded"
                            if provider_index
                            else "Primary provider succeeded"
                        ),
                    )
                    return results
                except (
                    TemporaryToolError,
                    httpx.TimeoutException,
                    httpx.NetworkError,
                ) as error:
                    error_message = self._safe_external_error(error)
                    errors.append(f"{provider.name}: {error_message}")
                    if attempt < task.retry_policy:
                        run.retries += 1
                        task.status = TaskStatus.retrying
                        task.reason = error_message
                        await self.store.save_task(run, task)
                        await self.store.save_run(run)
                        await self._operation(
                            run,
                            task,
                            f"{provider.name} failed: {error_message}. Retrying.",
                            "Temporary network/provider error",
                        )
                        await asyncio.sleep(0.25 * (attempt + 1))
                        continue
                    break
                except (NoResultsError, ToolError, httpx.HTTPStatusError) as error:
                    error_message = self._safe_external_error(error)
                    errors.append(f"{provider.name}: {error_message}")
                    await self._operation(
                        run,
                        task,
                        f"{provider.name} could not provide options. Switching provider.",
                        error_message,
                    )
                    break
            if provider_index < len(search_providers) - 1:
                run.retries += 1
                task.status = TaskStatus.retrying
                await self.store.save_task(run, task)
                await self.store.save_run(run)
        if journey_task:
            leg = "return" if task_id == "return_flight_search" else "outbound"
            await self._resolve_rail_station_candidates(run)
            task.status = TaskStatus.retrying
            task.provider = (
                "railradar+openstreetmap-connectors" if self.tools.rail else "verified-alternatives"
            )
            task.reason = (
                "No usable direct flight was found; decomposing the route into "
                "verified railway legs and mapped first/last-mile connectors"
            )
            await self.store.save_task(run, task)
            await self._operation(
                run,
                task,
                (
                    "No direct flight worked. Checking verified railway schedules "
                    "and mapped first/last-mile connections."
                ),
                (
                    "Rail schedules come from RailRadar; OpenStreetMap routing is "
                    "used only for connectors, never as a made-up coach timetable"
                ),
            )
            try:
                task.attempts += 1
                run.provider_calls += 1 + int(self.tools.rail is not None)
                results = await self.tools.search_fallback_journeys(
                    run.constraints,
                    leg,
                )
                await self._complete_task(
                    run,
                    task,
                    f"Built {len(results)} verified railway journey options",
                    (
                        "The route was decomposed into selectable scheduled rail "
                        "legs; estimated fares and connectors are labelled"
                    ),
                )
                return results
            except (ToolError, httpx.HTTPError) as error:
                errors.append(f"multimodal fallback: {self._safe_external_error(error)}")
        task.status = TaskStatus.failed
        task.reason = "; ".join(errors)
        task.completed_at = utc_now()
        await self.store.save_task(run, task)
        await self.store.save_run(run)
        raise ToolError(task.reason or f"{task_id} failed")

    async def _resolve_rail_station_candidates(self, run: RunState) -> None:
        if (
            not self.tools.rail
            or not self.interpreter.has_model
            or run.station_resolution_attempted
        ):
            return
        cities = [city for city in (run.constraints.origin, run.constraints.destination) if city]
        if len(cities) != 2:
            return
        run.station_resolution_attempted = True
        await self.store.save_run(run)
        await self._event(
            run,
            event_type="model_started",
            status="running",
            summary="Sarvam is proposing railway station candidates.",
            payload={"phase": "station_resolution"},
        )
        try:
            resolution = await self.agent.resolve_rail_stations(cities=cities)
            await self._record_model_call(run, resolution.metrics)
            by_city = {
                candidate.city.casefold().strip(): candidate
                for candidate in resolution.value.candidates
            }
            accepted: dict[str, list[str]] = {}
            for city in cities:
                candidate = by_city.get(city.casefold().strip())
                proposed = candidate.station_codes if candidate else []
                accepted[city] = await self.tools.rail.validate_station_candidates(
                    city,
                    proposed,
                )
            run.constraints.origin_station_codes = accepted.get(cities[0], [])
            run.constraints.destination_station_codes = accepted.get(cities[1], [])
            if run.graph:
                run.graph.constraints = run.constraints
            run.assumptions = list(
                dict.fromkeys(
                    [
                        *run.assumptions,
                        (
                            "Sarvam proposed railway station codes; RailRadar "
                            "validated them before search"
                        ),
                    ]
                )
            )
            await self.store.save_run(run)
            await self._event(
                run,
                event_type="station_candidates_validated",
                status="completed",
                summary=(
                    "RailRadar validated "
                    f"{sum(len(value) for value in accepted.values())} "
                    "Sarvam station candidates."
                ),
                payload={
                    "origin_candidates": run.constraints.origin_station_codes,
                    "destination_candidates": (run.constraints.destination_station_codes),
                },
            )
        except SarvamModelError as error:
            await self._record_model_call(run, error.metrics)
            run.assumptions = list(
                dict.fromkeys(
                    [
                        *run.assumptions,
                        (
                            "Sarvam station resolution was unavailable; "
                            "RailRadar's validated deterministic lookup was used"
                        ),
                    ]
                )
            )
            await self.store.save_run(run)
        except (ToolError, httpx.HTTPError) as error:
            run.errors.append(f"Station candidate validation: {self._safe_external_error(error)}")
            await self.store.save_run(run)

    async def _retry_single(
        self,
        run: RunState,
        task: TaskNode,
        provider: str,
        function: Callable[[], Awaitable[list[Any]]],
    ) -> list[Any]:
        for attempt in range(task.retry_policy + 1):
            run.provider_calls += 1
            task.attempts += 1
            task.provider = provider
            try:
                return await function()
            except (
                TemporaryToolError,
                httpx.TimeoutException,
                httpx.NetworkError,
            ) as error:
                if attempt >= task.retry_policy:
                    raise
                run.retries += 1
                task.status = TaskStatus.retrying
                error_message = self._safe_external_error(error)
                task.reason = error_message
                await self.store.save_task(run, task)
                await self._operation(
                    run,
                    task,
                    f"{provider} timed out. Retrying.",
                    error_message,
                )
                await asyncio.sleep(0.25 * (attempt + 1))
            except httpx.HTTPStatusError as error:
                raise ToolError(
                    f"{provider} rejected the place search: {error.response.status_code}"
                ) from error
        raise ToolError(f"{provider} failed")

    async def _start_task(self, run: RunState, task: TaskNode) -> None:
        task.status = TaskStatus.running
        task.started_at = utc_now()
        await self.store.save_task(run, task)
        await self.store.save_run(run)
        await self._operation(run, task, f"{task.title} started.", task.description)

    async def _complete_task(
        self, run: RunState, task: TaskNode, summary: str, reason: str
    ) -> None:
        task.status = TaskStatus.completed
        task.summary = summary
        task.reason = reason
        task.completed_at = utc_now()
        await self.store.save_task(run, task)
        await self.store.save_run(run)
        await self._operation(run, task, summary, reason)

    async def _persist_graph(self, run: RunState) -> None:
        if not run.graph:
            return
        for task in run.graph.tasks:
            await self.store.save_task(run, task)

    async def _record_model_call(
        self,
        run: RunState,
        metrics: ModelMetrics,
    ) -> None:
        call = ModelCallRecord(
            run_id=run.id,
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            phase=metrics.phase,
            model=metrics.model,
            prompt_version=metrics.prompt_version,
            status=metrics.status,
            attempts=metrics.attempts,
            latency_ms=metrics.latency_ms,
            prompt_tokens=metrics.prompt_tokens,
            completion_tokens=metrics.completion_tokens,
            total_tokens=metrics.total_tokens,
            error_code=metrics.error_code,
            error_message=metrics.error_message,
        )
        await self.store.add_model_call(call)
        run.model_calls += 1
        run.agent_cycles += 1
        run.retries += max(0, metrics.attempts - 1)
        await self.store.save_run(run)
        await self._event(
            run,
            event_type=("model_completed" if metrics.status == "completed" else "model_failed"),
            status=metrics.status,
            summary=(
                f"Sarvam completed {metrics.phase} in {metrics.latency_ms / 1000:.1f}s."
                if metrics.status == "completed"
                else f"Sarvam could not complete {metrics.phase}."
            ),
            reason=metrics.error_message,
            payload={
                "model": metrics.model,
                "phase": metrics.phase,
                "attempts": metrics.attempts,
                "latency_ms": metrics.latency_ms,
                "prompt_tokens": metrics.prompt_tokens,
                "completion_tokens": metrics.completion_tokens,
                "total_tokens": metrics.total_tokens,
                "error_code": metrics.error_code,
            },
        )

    async def _model_replan(
        self,
        run: RunState,
        *,
        failure: dict[str, Any],
        fallback_message: str,
    ) -> ReplanDecision | None:
        if (
            not self.interpreter.has_model
            or run.model_calls >= 8
            or run.agent_cycles >= 12
            or run.replans >= 3
        ):
            return None
        previous_status = run.status
        previous_phase = run.phase
        run.status = RunStatus.replanning
        run.phase = AgentPhase.replanning
        await self.store.save_run(run)
        await self._event(
            run,
            event_type="model_started",
            status="running",
            summary="Sarvam is evaluating a safe recovery route.",
            payload={"failure": failure},
        )
        try:
            result = await self.agent.replan(
                constraints=run.constraints.model_dump(mode="json"),
                graph=run.graph.model_dump(mode="json") if run.graph else {},
                failure=failure,
                attempts_remaining=run.replans < 2,
            )
            await self._record_model_call(run, result.metrics)
            run.replans += 1
            run.status = previous_status
            run.phase = previous_phase
            await self.store.save_run(run)
            await self._event(
                run,
                event_type="replan_created",
                status="completed",
                summary=result.value.explanation,
                payload={
                    "action": result.value.action,
                    "task_id": result.value.task_id,
                    "provider": result.value.provider,
                    "constraint_to_relax": result.value.constraint_to_relax,
                },
            )
            return result.value
        except SarvamModelError as error:
            await self._record_model_call(run, error.metrics)
            run.errors.append(f"Replanning failed: {error}")
            run.status = previous_status
            run.phase = previous_phase
            await self.store.save_run(run)
            await self._event(
                run,
                event_type="replan_unavailable",
                status="failed",
                summary=fallback_message,
                reason=error.metrics.error_message,
            )
            return None

    async def _event(
        self,
        run: RunState,
        *,
        event_type: str,
        status: str,
        summary: str,
        reason: str | None = None,
        task_id: str | None = None,
        provider: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AgentEvent:
        event = await self.store.add_event(
            AgentEvent(
                run_id=run.id,
                conversation_id=run.conversation_id,
                user_id=run.user_id,
                type=event_type,
                phase=run.phase,
                status=status,
                summary=summary,
                reason=reason,
                task_id=task_id,
                provider=provider,
                payload=payload or {},
            )
        )
        run.last_event_id = event.id
        await self.store.save_run(run)
        return event

    async def _operation(
        self, run: RunState, task: TaskNode, summary: str, reason: str | None
    ) -> None:
        event = OperationEvent(task_id=task.id, status=task.status, summary=summary, reason=reason)
        await self._event(
            run,
            event_type="task_updated",
            status=task.status.value,
            summary=summary,
            reason=reason,
            task_id=task.id,
            provider=task.provider,
            payload={"event": event.model_dump(mode="json")},
        )
        await self._message(
            run,
            MessageKind.operation,
            summary,
            {"event": event.model_dump(mode="json")},
            role="tool",
        )

    async def _message(
        self,
        run: RunState,
        kind: MessageKind,
        text: str,
        payload: dict[str, Any],
        *,
        role: str = "assistant",
    ) -> ChatMessage:
        message = ChatMessage(
            conversation_id=run.conversation_id,
            run_id=run.id,
            role=role,
            kind=kind,
            text=text,
            payload=payload,
        )
        return await self.store.add_message(message)

    async def _finalize_report(self, run: RunState) -> None:
        report_task = self._task(run, "create_report")
        report_task.status = TaskStatus.completed
        report_task.summary = "Execution report created"
        report_task.completed_at = utc_now()
        await self.store.save_task(run, report_task)
        await self.store.save_run(run)
        user = UserIdentity(id=run.user_id, email="")
        report = await self.report(user, run.id)
        await self._message(
            run,
            MessageKind.report,
            "Full execution report is ready.",
            {"report": report.model_dump(mode="json")},
        )

    @staticmethod
    def _task(run: RunState, task_id: str) -> TaskNode:
        if not run.graph:
            raise ValueError("Run has no task graph")
        return next(task for task in run.graph.tasks if task.id == task_id)

    @staticmethod
    def _task_or_none(run: RunState, task_id: str) -> TaskNode | None:
        if not run.graph:
            return None
        return next((task for task in run.graph.tasks if task.id == task_id), None)

    @staticmethod
    def _safe_external_error(error: Exception) -> str:
        if isinstance(error, httpx.HTTPError):
            return f"{type(error).__name__} while contacting the provider"
        message = str(error).strip()
        return (message or type(error).__name__)[:300]

    @staticmethod
    def _clarification(constraints: TravelConstraints) -> str:
        missing = set(constraints.missing_fields)
        if "adults" in missing:
            return (
                "How many people are travelling? "
                "Mention adults and children separately if relevant."
            )
        if {"start_date", "end_date"} & missing:
            return "What exact dates should I plan for?"
        if "origin" in missing:
            return "Where will you be travelling from?"
        if "destination" in missing:
            return "Where would you like to go?"
        if "budget" in missing:
            return "What is the maximum total budget for the trip?"
        if "origin_airport" in missing:
            return "Which departure airport should I use?"
        if "destination_airport" in missing:
            return "Which destination airport should I use?"
        return "I need one more detail before I can safely search."

    @staticmethod
    def _quick_replies(constraints: TravelConstraints) -> list[str]:
        if {"start_date", "end_date"} & set(constraints.missing_fields):
            return ["Next weekend", "This weekend", "I’ll give exact dates"]
        if "budget" in constraints.missing_fields:
            return ["₹20,000", "₹30,000", "₹45,000"]
        return []

    @staticmethod
    def _impossible_budget_message(constraints: TravelConstraints, result: SolverResult) -> str:
        common = max(
            result.rejection_summary,
            key=result.rejection_summary.get,
            default="availability",
        )
        if constraints.budget is None:
            return (
                "I couldn’t find a valid flight-and-hotel combination without breaking "
                f"a hard constraint. The most common conflict was: {common}. "
                "Which constraint may I change?"
            )
        return (
            f"I couldn’t find a package under ₹{constraints.budget:,} without breaking "
            f"a hard constraint. The most common conflict was: {common}. "
            "Which constraint may I change?"
        )

    @staticmethod
    def _requests_saved_preferences(text: str) -> bool:
        return bool(
            re.search(
                r"\b(?:usual|saved|previous|past)\s+preferences?\b",
                text,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _preference_confirmation_decision(text: str) -> bool | None:
        normalized = " ".join(text.casefold().split())
        if re.search(
            r"^(?:no|nope|nah)\b|"
            r"\b(?:do not|don't|dont)\s+(?:apply|use)\b|"
            r"\b(?:skip|ignore)\s+(?:them|those|my (?:usual|saved) preferences?)\b",
            normalized,
        ):
            return False
        if re.search(
            r"^(?:yes|yep|yeah|sure|okay|ok)\b|"
            r"\b(?:apply|use)\s+(?:them|those|my (?:usual|saved) preferences?)\b",
            normalized,
        ):
            return True
        return None

    @staticmethod
    def _saved_preferences_confirmation(
        preferences: UserPreferences | None,
    ) -> str:
        if not preferences:
            return (
                "I couldn't find any saved preferences for this account, so I won't "
                "apply any. Tell me the preferences you want for this trip."
            )
        details: list[str] = []
        if preferences.home_city:
            details.append(f"depart from {preferences.home_city}")
        if preferences.avoid_early_flights:
            earliest = str(preferences.preferences.get("earliest_departure") or "08:00")
            details.append(f"avoid flights before {earliest}")
        if preferences.hotel_preference:
            details.append(f"stay near {preferences.hotel_preference}")
        extra = list(preferences.preferences.get("preferences", []))
        if extra:
            details.append(", ".join(extra))
        summary = "; ".join(details) or "no reusable trip settings"
        return (
            f"I found these saved preferences: {summary}. "
            "Would you like me to apply them to this trip? Reply in chat with yes or no."
        )

    @classmethod
    def _apply_confirmed_preferences(
        cls,
        current: TravelConstraints,
        preferences: UserPreferences,
    ) -> TravelConstraints:
        saved = cls._constraints_from_preferences(preferences)
        values = current.model_dump(
            mode="python",
            exclude={"missing_fields", "inferred_fields"},
        )
        applied_fields: list[str] = []
        for field in (
            "origin",
            "origin_airport",
            "earliest_departure",
            "hotel_area_preference",
            "max_hotel_distance_km",
        ):
            if values.get(field) is None and getattr(saved, field) is not None:
                values[field] = getattr(saved, field)
                applied_fields.append(field)
        values["preferences"] = list(dict.fromkeys([*current.preferences, *saved.preferences]))
        values["inferred_fields"] = list(dict.fromkeys([*current.inferred_fields, *applied_fields]))
        merged = TravelConstraints.model_validate(values)
        merged.missing_fields = merged.required_missing()
        return merged

    @staticmethod
    def _constraints_from_preferences(preferences: UserPreferences) -> TravelConstraints:
        saved = preferences.preferences
        return TravelConstraints(
            origin=preferences.home_city,
            origin_airport=preferences.preferred_airport,
            earliest_departure=(
                datetime.strptime(
                    str(saved.get("earliest_departure", "08:00")),
                    "%H:%M",
                ).time()
                if preferences.avoid_early_flights
                else None
            ),
            hotel_area_preference=preferences.hotel_preference,
            max_hotel_distance_km=saved.get("max_hotel_distance_km"),
            preferences=list(saved.get("preferences", [])),
            inferred_fields=[
                field
                for field, value in {
                    "origin": preferences.home_city,
                    "origin_airport": preferences.preferred_airport,
                    "earliest_departure": preferences.avoid_early_flights,
                    "hotel_area_preference": preferences.hotel_preference,
                }.items()
                if value
            ],
        )

    async def _remember_preferences(self, user_id: str, constraints: TravelConstraints) -> None:
        existing = await self.store.get_user_preferences(user_id)
        preferences = UserPreferences(
            user_id=user_id,
            home_city=(
                existing.home_city if existing and existing.home_city else constraints.origin
            ),
            preferred_airport=(
                existing.preferred_airport
                if existing and existing.preferred_airport
                else constraints.origin_airport
            ),
            avoid_early_flights=bool(
                constraints.earliest_departure or (existing and existing.avoid_early_flights)
            ),
            hotel_preference=(
                constraints.hotel_area_preference
                or (existing.hotel_preference if existing else None)
            ),
            preferences={
                "earliest_departure": (
                    constraints.earliest_departure.strftime("%H:%M")
                    if constraints.earliest_departure
                    else (existing.preferences.get("earliest_departure") if existing else None)
                ),
                "max_hotel_distance_km": (
                    constraints.max_hotel_distance_km
                    if constraints.max_hotel_distance_km is not None
                    else (existing.preferences.get("max_hotel_distance_km") if existing else None)
                ),
                "preferences": sorted(
                    set(constraints.preferences)
                    | set(existing.preferences.get("preferences", []) if existing else [])
                ),
            },
        )
        await self.store.save_user_preferences(preferences)

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.calendar_service import CalendarService
from app.interpreter import RequestInterpreter
from app.itinerary import create_itinerary
from app.models import (
    ApprovalDecision,
    ApprovalPayload,
    ChatMessage,
    Conversation,
    ConversationSnapshot,
    ExecutionReport,
    MessageKind,
    OperationEvent,
    RunState,
    RunStatus,
    SendMessageRequest,
    TaskNode,
    TaskStatus,
    TravelConstraints,
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
)


class CalendarConnectionRequired(RuntimeError):
    pass


class Orchestrator:
    def __init__(
        self,
        store: Store,
        interpreter: RequestInterpreter,
        tools: TravelToolRegistry,
        calendar: CalendarService,
    ) -> None:
        self.store = store
        self.interpreter = interpreter
        self.tools = tools
        self.calendar = calendar
        self.background_tasks: set[asyncio.Task[Any]] = set()

    async def recover_pending_runs(self) -> int:
        recovered = 0
        for run in await self.store.list_recoverable_runs():
            if not run.graph:
                continue
            for task in run.graph.tasks:
                if task.id in {"understand_request", "resolve_constraints"}:
                    continue
                task.status = TaskStatus.waiting
                task.attempts = 0
                task.provider = None
                task.summary = None
                task.reason = None
                task.started_at = None
                task.completed_at = None
            run.status = RunStatus.planned
            run.flights = []
            run.hotels = []
            run.places = []
            run.packages = []
            run.selected_package = None
            run.itinerary = None
            run.approval = None
            await self.store.save_run(run)
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
        self, user: UserIdentity, conversation_id: UUID, request: SendMessageRequest
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

        base_constraints = (
            current_run.constraints
            if current_run and current_run.status == RunStatus.awaiting_input
            else None
        )
        memory_applied = False
        if base_constraints is None and "usual preference" in request.text.lower():
            preferences = await self.store.get_user_preferences(user.id)
            if preferences:
                base_constraints = self._constraints_from_preferences(preferences)
                memory_applied = True
        constraints = await self.interpreter.interpret(request.text, base_constraints)
        run = (
            current_run
            if current_run and current_run.status == RunStatus.awaiting_input
            else RunState(
                conversation_id=conversation_id,
                user_id=user.id,
                resilience_demo=bool(request.resilience_demo),
            )
        )
        run.constraints = constraints
        run.status = RunStatus.interpreting
        await self.store.save_run(run)
        user_message.run_id = run.id
        await self.store.update_message_run_id(user_message.id, run.id, user.id)
        conversation.last_message = request.text
        conversation.updated_at = utc_now()
        if constraints.destination:
            conversation.destination = constraints.destination
            conversation.title = f"{constraints.destination} trip"
        await self.store.update_conversation(conversation)

        await self._message(
            run,
            MessageKind.interpretation,
            (
                f"I understood a {constraints.duration_days or 'multi-day'} trip from "
                f"{constraints.origin or 'your origin'} to "
                f"{constraints.destination or 'your destination'}."
            ),
            {
                "constraints": constraints.model_dump(mode="json"),
                "missing_fields": constraints.missing_fields,
                "memory_applied": memory_applied,
            },
        )
        if constraints.missing_fields:
            run.status = RunStatus.awaiting_input
            await self.store.save_run(run)
            await self._message(
                run,
                MessageKind.clarification,
                self._clarification(constraints),
                {"quick_replies": self._quick_replies(constraints)},
            )
            return run

        await self._remember_preferences(user.id, constraints)
        run.graph = build_task_graph(constraints)
        run.status = RunStatus.planned
        await self.store.save_run(run)
        await self._message(
            run,
            MessageKind.task_graph,
            f"I created a {run.graph.estimated_steps}-step execution plan.",
            {"graph": run.graph.model_dump(mode="json")},
        )
        self._schedule_execution(run.id, user)
        return run

    def _schedule_execution(self, run_id: UUID, user: UserIdentity) -> None:
        task = asyncio.create_task(self._execute(run_id, user))
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    async def _execute(self, run_id: UUID, user: UserIdentity) -> None:
        run = await self.store.get_run(run_id, user.id)
        if not run or not run.graph:
            return
        run.status = RunStatus.running
        await self.store.save_run(run)
        try:
            flights_task = asyncio.create_task(
                self._run_search_task(run, "flight_search", "search_flights")
            )
            hotels_task = asyncio.create_task(
                self._run_search_task(run, "hotel_search", "search_hotels")
            )
            run.flights, run.hotels = await asyncio.gather(flights_task, hotels_task)
            await self.store.save_run(run)
            await self._message(
                run,
                MessageKind.flight_options,
                f"Shortlisted {len(run.flights)} flight options.",
                {"flights": [item.model_dump(mode="json") for item in run.flights[:6]]},
            )
            await self._message(
                run,
                MessageKind.hotel_options,
                f"Shortlisted {len(run.hotels)} available hotels.",
                {"hotels": [item.model_dump(mode="json") for item in run.hotels[:6]]},
            )

            compare_task = self._task(run, "compare_packages")
            await self._start_task(run, compare_task)
            solver_result = compare_packages(run.flights, run.hotels, run.constraints)
            if not solver_result.valid:
                reason = self._impossible_budget_message(run.constraints, solver_result)
                compare_task.status = TaskStatus.failed
                compare_task.summary = "No valid package satisfies every hard constraint"
                compare_task.reason = reason
                compare_task.completed_at = utc_now()
                run.status = RunStatus.awaiting_input
                await self.store.save_run(run)
                await self._message(
                    run,
                    MessageKind.clarification,
                    reason,
                    {
                        "quick_replies": [
                            "Increase the budget by ₹5,000",
                            "Show hotels a little farther away",
                            "Allow earlier flights",
                        ],
                        "rejections": solver_result.rejection_summary,
                    },
                )
                return
            run.packages = solver_result.valid
            run.selected_package = solver_result.valid[0]
            await self._complete_task(
                run,
                compare_task,
                f"Compared {len(run.flights) * len(run.hotels)} combinations",
                (
                    f"Selected the highest-ranked package; "
                    f"{solver_result.rejected_count} combinations violated constraints"
                ),
            )
            await self._message(
                run,
                MessageKind.budget,
                (
                    f"The best valid package is ₹{run.selected_package.total_price:,}, "
                    f"leaving ₹{run.selected_package.remaining_budget:,}."
                ),
                {
                    "selected_package": run.selected_package.model_dump(mode="json"),
                    "alternatives": [
                        package.model_dump(mode="json") for package in run.packages[1:4]
                    ],
                    "rejected_count": solver_result.rejected_count,
                    "rejection_summary": solver_result.rejection_summary,
                },
            )

            places_task = self._task(run, "place_search")
            await self._start_task(run, places_task)
            run.places = await self._retry_single(
                run, places_task, "google_places", lambda: self.tools.places.search(run.constraints)
            )
            await self._complete_task(
                run,
                places_task,
                f"Found {len(run.places)} itinerary candidates",
                "Selected well-rated activities with usable location data",
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
            await self.store.save_run(run)
            await self._message(
                run,
                MessageKind.approval,
                f"Ready to add {event_count} events to Google Calendar.",
                {"approval": run.approval.model_dump(mode="json")},
            )
        except Exception as error:
            run.errors.append(str(error))
            run.status = RunStatus.failed
            await self.store.save_run(run)
            await self._message(
                run,
                MessageKind.error,
                "I could not finish this run safely.",
                {"error": str(error), "recoverable": isinstance(error, ToolError)},
            )

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
            approval_task.summary = "User cancelled Calendar creation"
            self._task(run, "add_calendar").status = TaskStatus.skipped
            run.status = RunStatus.completed
            run.completed_at = utc_now()
            await self.store.save_run(run)
            await self._message(
                run,
                MessageKind.calendar,
                "Calendar creation was cancelled. Your trip plan is still saved.",
                {"created": 0},
            )
            await self._finalize_report(run)
            return run
        if decision.decision == "edit":
            if not decision.edit_message:
                raise ValueError("Describe the requested change")
            approval_task.status = TaskStatus.skipped
            run.status = RunStatus.completed
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
            )

        calendar_status = await self.calendar.status(user.id)
        if not calendar_status["connected"]:
            raise CalendarConnectionRequired("Connect Google Calendar, then approve again")
        approval_task.status = TaskStatus.completed
        approval_task.summary = "User explicitly approved Calendar creation"
        approval_task.completed_at = utc_now()
        calendar_task = self._task(run, "add_calendar")
        await self._start_task(run, calendar_task)
        run.calendar_event_links = await self.calendar.create_itinerary_events(
            user.id, str(run.id), run.itinerary
        )
        await self._complete_task(
            run,
            calendar_task,
            f"Created {len(run.calendar_event_links)} Google Calendar events",
            "Only the approved itinerary payload was written",
        )
        run.status = RunStatus.completed
        run.completed_at = utc_now()
        await self.store.save_run(run)
        await self._message(
            run,
            MessageKind.calendar,
            f"Added {len(run.calendar_event_links)} events to Google Calendar.",
            {"links": run.calendar_event_links},
        )
        await self._finalize_report(run)
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
            retries=run.retries,
            rejected_packages=max(0, len(run.flights) * len(run.hotels) - len(run.packages)),
            estimated_savings=max(prices) - run.selected_package.total_price if prices else 0,
            elapsed_seconds=round(
                ((run.completed_at or utc_now()) - run.started_at).total_seconds(), 2
            ),
        )

    async def _run_search_task(self, run: RunState, task_id: str, method_name: str) -> list[Any]:
        task = self._task(run, task_id)
        await self._start_task(run, task)
        errors: list[str] = []
        for provider_index, provider in enumerate(self.tools.providers):
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
                        and task_id == "flight_search"
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
                except TemporaryToolError as error:
                    errors.append(f"{provider.name}: {error}")
                    if attempt < task.retry_policy:
                        run.retries += 1
                        task.status = TaskStatus.retrying
                        task.reason = str(error)
                        await self.store.save_run(run)
                        await self._operation(
                            run,
                            task,
                            f"{provider.name} failed: {error}. Retrying.",
                            "Temporary network/provider error",
                        )
                        await asyncio.sleep(0.25 * (attempt + 1))
                        continue
                    break
                except (NoResultsError, ToolError) as error:
                    errors.append(f"{provider.name}: {error}")
                    await self._operation(
                        run,
                        task,
                        f"{provider.name} could not provide options. Switching provider.",
                        str(error),
                    )
                    break
            if provider_index < len(self.tools.providers) - 1:
                run.retries += 1
                task.status = TaskStatus.retrying
                await self.store.save_run(run)
        task.status = TaskStatus.failed
        task.reason = "; ".join(errors)
        task.completed_at = utc_now()
        await self.store.save_run(run)
        raise ToolError(task.reason or f"{task_id} failed")

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
            except TemporaryToolError as error:
                if attempt >= task.retry_policy:
                    raise
                run.retries += 1
                task.status = TaskStatus.retrying
                await self._operation(run, task, f"{provider} timed out. Retrying.", str(error))
                await asyncio.sleep(0.25 * (attempt + 1))
        raise ToolError(f"{provider} failed")

    async def _start_task(self, run: RunState, task: TaskNode) -> None:
        task.status = TaskStatus.running
        task.started_at = utc_now()
        await self.store.save_run(run)
        await self._operation(run, task, f"{task.title} started.", task.description)

    async def _complete_task(
        self, run: RunState, task: TaskNode, summary: str, reason: str
    ) -> None:
        task.status = TaskStatus.completed
        task.summary = summary
        task.reason = reason
        task.completed_at = utc_now()
        await self.store.save_run(run)
        await self._operation(run, task, summary, reason)

    async def _operation(
        self, run: RunState, task: TaskNode, summary: str, reason: str | None
    ) -> None:
        event = OperationEvent(task_id=task.id, status=task.status, summary=summary, reason=reason)
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
    def _clarification(constraints: TravelConstraints) -> str:
        missing = set(constraints.missing_fields)
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
        common = max(result.rejection_summary, key=result.rejection_summary.get, default="budget")
        return (
            f"I couldn’t find a package under ₹{constraints.budget:,} without breaking "
            f"a hard constraint. The most common conflict was: {common}. "
            "Which constraint may I change?"
        )

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

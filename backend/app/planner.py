from __future__ import annotations

from app.agent_model import AgentPlanDraft
from app.models import TaskGraph, TaskNode, TaskStatus, TravelConstraints

ALLOWED_TOOLS = {
    "interpret_request",
    "resolve_constraints",
    "resolve_locations",
    "search_outbound_flights",
    "choose_outbound_flight",
    "search_return_flights",
    "choose_return_flight",
    "search_hotels",
    "choose_hotel",
    "compare_options",
    "search_places",
    "create_itinerary",
    "request_approval",
    "add_calendar_events",
    "create_report",
}


def build_task_graph(
    constraints: TravelConstraints,
    draft: AgentPlanDraft | None = None,
) -> TaskGraph:
    destination = constraints.destination or "your destination"
    draft_by_tool = {task.tool_name: task for task in draft.tasks} if draft else {}

    def task(
        *,
        task_id: str,
        tool_name: str,
        title: str,
        description: str,
        dependencies: list[str],
        optional: bool = False,
        status: TaskStatus = TaskStatus.waiting,
        summary: str | None = None,
    ) -> TaskNode:
        planned = draft_by_tool.get(tool_name)
        return TaskNode(
            id=task_id,
            title=planned.title if planned else title,
            description=planned.description if planned else description,
            tool_name=tool_name,
            arguments=planned.arguments if planned else {},
            dependencies=dependencies,
            optional=planned.optional if planned else optional,
            retry_policy=planned.retry_policy if planned else 2,
            status=status,
            summary=summary,
        )

    tasks = [
        task(
            task_id="understand_request",
            tool_name="interpret_request",
            title="Understand request",
            description="Extract the trip goal, constraints, and preferences with Sarvam",
            dependencies=[],
            status=TaskStatus.completed,
            summary="Sarvam converted the request into validated constraints",
        ),
        task(
            task_id="resolve_constraints",
            tool_name="resolve_constraints",
            title="Validate trip constraints",
            description="Verify dates, travellers, preferences, and optional budget",
            dependencies=["understand_request"],
            status=TaskStatus.completed,
            summary="Required route and date constraints are available",
        ),
        task(
            task_id="resolve_locations",
            tool_name="resolve_locations",
            title="Resolve transport hubs",
            description="Resolve safe airport and railway starting points for the requested cities",
            dependencies=["resolve_constraints"],
            status=(
                TaskStatus.completed
                if constraints.origin_airport and constraints.destination_airport
                else TaskStatus.waiting
            ),
            summary=(
                "Airport codes resolved"
                if constraints.origin_airport and constraints.destination_airport
                else None
            ),
        ),
        task(
            task_id="outbound_flight_search",
            tool_name="search_outbound_flights",
            title="Find journeys there",
            description=(
                "Search one-way flights, then decompose unavailable routes into "
                "RailRadar train and mapped road legs"
            ),
            dependencies=["resolve_locations"],
        ),
        task(
            task_id="choose_outbound_flight",
            tool_name="choose_outbound_flight",
            title="Choose your journey there",
            description="Pause so the traveller can choose or change the outbound journey",
            dependencies=["outbound_flight_search"],
        ),
        task(
            task_id="return_flight_search",
            tool_name="search_return_flights",
            title="Find journeys back",
            description=(
                "Search the return independently, including railway and road "
                "fallbacks when no direct flight works"
            ),
            dependencies=["choose_outbound_flight"],
        ),
        task(
            task_id="choose_return_flight",
            tool_name="choose_return_flight",
            title="Choose your journey back",
            description="Pause so the traveller can choose or change the return journey",
            dependencies=["return_flight_search"],
        ),
        task(
            task_id="hotel_search",
            tool_name="search_hotels",
            title="Find stays",
            description="Search available hotels matching the area preference",
            dependencies=["choose_return_flight"],
        ),
        task(
            task_id="choose_hotel",
            tool_name="choose_hotel",
            title="Choose your stay",
            description="Pause so the traveller can choose or change the hotel",
            dependencies=["hotel_search"],
        ),
        task(
            task_id="place_search",
            tool_name="search_places",
            title=f"Find things to do in {destination}",
            description="Search relevant activities and landmarks with verified locations",
            dependencies=["choose_hotel"],
            optional=True,
        ),
        task(
            task_id="compare_packages",
            tool_name="compare_options",
            title="Compare combinations",
            description="Validate and rank transport-and-stay packages deterministically",
            dependencies=["choose_return_flight", "choose_hotel"],
        ),
        task(
            task_id="create_itinerary",
            tool_name="create_itinerary",
            title="Create itinerary",
            description="Build a geographically sensible day-by-day schedule",
            dependencies=["compare_packages", "place_search"],
        ),
        task(
            task_id="request_approval",
            tool_name="request_approval",
            title="Offer calendar export",
            description="Let the traveller review the plan before exporting it",
            dependencies=["create_itinerary"],
        ),
        task(
            task_id="add_calendar",
            tool_name="add_calendar_events",
            title="Prepare calendar file",
            description="Prepare a portable ICS file for any calendar app",
            dependencies=["request_approval"],
        ),
        task(
            task_id="create_report",
            tool_name="create_report",
            title="Create execution report",
            description="Summarize model calls, tools, retries, decisions, and artifacts",
            dependencies=["add_calendar"],
        ),
    ]
    if any(item.tool_name not in ALLOWED_TOOLS for item in tasks):
        raise ValueError("Planner emitted an unregistered tool")
    return TaskGraph(
        goal=(draft.goal if draft else f"Plan a trip from {constraints.origin} to {destination}"),
        constraints=constraints,
        tasks=tasks,
        estimated_steps=len(tasks),
    )

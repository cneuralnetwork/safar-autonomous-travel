from __future__ import annotations

from app.models import TaskGraph, TaskNode, TaskStatus, TravelConstraints

ALLOWED_TOOLS = {
    "interpret_request",
    "resolve_constraints",
    "search_flights",
    "search_hotels",
    "compare_options",
    "search_places",
    "create_itinerary",
    "request_approval",
    "add_calendar_events",
    "create_report",
}


def build_task_graph(constraints: TravelConstraints) -> TaskGraph:
    destination = constraints.destination or "your destination"
    tasks = [
        TaskNode(
            id="understand_request",
            title="Understand request",
            description="Extract the trip goal and preferences",
            tool_name="interpret_request",
            status=TaskStatus.completed,
            summary="Travel request converted into validated constraints",
        ),
        TaskNode(
            id="resolve_constraints",
            title="Resolve dates and budget",
            description="Validate dates, travellers, airport codes, and total budget",
            tool_name="resolve_constraints",
            dependencies=["understand_request"],
            status=TaskStatus.completed,
            summary="Required trip constraints are available",
        ),
        TaskNode(
            id="flight_search",
            title="Search flights",
            description="Search outbound and return flight options",
            tool_name="search_flights",
            arguments={"provider": "auto"},
            dependencies=["resolve_constraints"],
        ),
        TaskNode(
            id="hotel_search",
            title="Search hotels",
            description="Search available hotels matching the preferred area",
            tool_name="search_hotels",
            arguments={"provider": "auto"},
            dependencies=["resolve_constraints"],
        ),
        TaskNode(
            id="compare_packages",
            title="Compare combinations",
            description="Validate and rank flight-hotel packages deterministically",
            tool_name="compare_options",
            dependencies=["flight_search", "hotel_search"],
        ),
        TaskNode(
            id="place_search",
            title=f"Find things to do in {destination}",
            description="Search relevant activities and landmarks",
            tool_name="search_places",
            dependencies=["resolve_constraints"],
        ),
        TaskNode(
            id="create_itinerary",
            title="Create itinerary",
            description="Build a geographically sensible day-by-day schedule",
            tool_name="create_itinerary",
            dependencies=["compare_packages", "place_search"],
        ),
        TaskNode(
            id="request_approval",
            title="Request approval",
            description="Ask before creating Google Calendar events",
            tool_name="request_approval",
            dependencies=["create_itinerary"],
        ),
        TaskNode(
            id="add_calendar",
            title="Add to Calendar",
            description="Create only the explicitly approved Google Calendar events",
            tool_name="add_calendar_events",
            dependencies=["request_approval"],
        ),
        TaskNode(
            id="create_report",
            title="Create execution report",
            description="Summarize tools, retries, decisions, and final artifacts",
            tool_name="create_report",
            dependencies=["add_calendar"],
        ),
    ]
    if any(task.tool_name not in ALLOWED_TOOLS for task in tasks):
        raise ValueError("Planner emitted an unregistered tool")
    return TaskGraph(
        goal=f"Plan a trip from {constraints.origin} to {destination}",
        constraints=constraints,
        tasks=tasks,
        estimated_steps=len(tasks),
    )

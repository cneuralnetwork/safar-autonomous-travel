from __future__ import annotations

from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    interpreting = "interpreting"
    awaiting_input = "awaiting_input"
    planning = "planning"
    planned = "planned"
    running = "running"
    replanning = "replanning"
    awaiting_approval = "awaiting_approval"
    paused = "paused"
    completed = "completed"
    failed = "failed"


class AgentPhase(StrEnum):
    interpreting = "interpreting"
    planning = "planning"
    executing = "executing"
    replanning = "replanning"
    awaiting_input = "awaiting_input"
    awaiting_approval = "awaiting_approval"
    finalizing = "finalizing"
    completed = "completed"
    paused = "paused"
    failed = "failed"


class TaskStatus(StrEnum):
    waiting = "waiting"
    running = "running"
    completed = "completed"
    retrying = "retrying"
    failed = "failed"
    awaiting_approval = "awaiting_approval"
    skipped = "skipped"


class MessageKind(StrEnum):
    text = "text"
    interpretation = "interpretation"
    clarification = "clarification"
    task_graph = "task_graph"
    operation = "operation"
    flight_options = "flight_options"
    hotel_options = "hotel_options"
    budget = "budget"
    itinerary = "itinerary"
    approval = "approval"
    calendar = "calendar"
    report = "report"
    error = "error"


class UserIdentity(BaseModel):
    id: str
    email: str
    name: str = "Traveller"
    avatar_url: str | None = None
    google_sub: str | None = None


class UserPreferences(BaseModel):
    user_id: str
    home_city: str | None = None
    preferred_airport: str | None = None
    avoid_early_flights: bool = False
    hotel_preference: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)


class TravelConstraints(BaseModel):
    task_type: Literal["travel_planning"] = "travel_planning"
    origin: str | None = None
    origin_airport: str | None = None
    destination: str | None = None
    destination_airport: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    duration_days: int | None = Field(default=None, ge=1, le=30)
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)
    budget: int | None = Field(default=None, ge=1000)
    currency: Literal["INR"] = "INR"
    earliest_departure: time | None = None
    latest_departure: time | None = None
    hotel_area_preference: str | None = None
    max_hotel_distance_km: float | None = Field(default=None, ge=0.1, le=50)
    preferences: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    inferred_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def sync_duration(self) -> TravelConstraints:
        if self.start_date and self.end_date:
            calculated = (self.end_date - self.start_date).days + 1
            if calculated > 0:
                self.duration_days = calculated
        return self

    def required_missing(self) -> list[str]:
        required = {
            "origin": self.origin,
            "destination": self.destination,
            "start_date": self.start_date,
            "end_date": self.end_date,
        }
        return [key for key, value in required.items() if value is None]


class TaskNode(BaseModel):
    id: str
    title: str
    description: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    optional: bool = False
    retry_policy: int = Field(default=2, ge=0, le=4)
    status: TaskStatus = TaskStatus.waiting
    attempts: int = 0
    provider: str | None = None
    summary: str | None = None
    reason: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class TaskGraph(BaseModel):
    goal: str
    constraints: TravelConstraints
    tasks: list[TaskNode]
    estimated_steps: int

    @model_validator(mode="after")
    def validate_dependencies(self) -> TaskGraph:
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("Task IDs must be unique")
        allowed = set(ids)
        for task in self.tasks:
            unknown = set(task.dependencies) - allowed
            if unknown:
                raise ValueError(f"Unknown dependencies for {task.id}: {unknown}")
            if task.id in task.dependencies:
                raise ValueError(f"Task {task.id} cannot depend on itself")
        return self


class FlightSegment(BaseModel):
    airline: str
    flight_number: str | None = None
    departure_airport: str
    arrival_airport: str
    departure_at: datetime
    arrival_at: datetime
    duration_minutes: int


class FlightOption(BaseModel):
    id: str
    provider: str
    outbound: list[FlightSegment]
    inbound: list[FlightSegment] = Field(default_factory=list)
    total_price: int
    currency: Literal["INR"] = "INR"
    stops: int = 0
    baggage: str | None = None
    booking_url: str | None = None
    score: float = 0

    @property
    def departure_at(self) -> datetime:
        return self.outbound[0].departure_at


class HotelOption(BaseModel):
    id: str
    provider: str
    name: str
    address: str
    rating: float = Field(ge=0, le=5)
    review_count: int = 0
    nightly_price: int
    total_price: int
    currency: Literal["INR"] = "INR"
    distance_to_preference_km: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    image_url: str | None = None
    booking_url: str | None = None
    available: bool = True


class PackageOption(BaseModel):
    id: str
    flight: FlightOption
    hotel: HotelOption
    on_trip_reserve: int
    local_transfer_reserve: int
    total_price: int
    remaining_budget: int | None = None
    score: float
    rejection_reasons: list[str] = Field(default_factory=list)


class PlaceOption(BaseModel):
    id: str
    name: str
    address: str
    category: str
    rating: float | None = None
    latitude: float
    longitude: float
    duration_minutes: int = 120
    image_url: str | None = None
    maps_url: str | None = None


class ItineraryItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    description: str
    start_at: datetime
    end_at: datetime
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    category: Literal["flight", "hotel", "activity", "transfer", "meal", "buffer"]


class ItineraryDay(BaseModel):
    date: date
    title: str
    items: list[ItineraryItem]


class Itinerary(BaseModel):
    timezone: str = "Asia/Kolkata"
    days: list[ItineraryDay]


class ApprovalPayload(BaseModel):
    action: Literal["add_calendar_events"] = "add_calendar_events"
    event_count: int
    estimated_trip_total: int
    currency: Literal["INR"] = "INR"
    disclaimer: str = "No booking or payment will be made."
    payload_hash: str
    expires_at: datetime


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "edit", "cancel"]
    payload_hash: str
    edit_message: str | None = None


class ChatMessage(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID
    run_id: UUID | None = None
    role: Literal["user", "assistant", "system", "tool"]
    kind: MessageKind = MessageKind.text
    text: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class Conversation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    user_id: str
    title: str = "New trip"
    destination: str | None = None
    last_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RunState(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID
    user_id: str
    status: RunStatus = RunStatus.interpreting
    phase: AgentPhase = AgentPhase.interpreting
    harness_version: int = 1
    constraints: TravelConstraints = Field(default_factory=TravelConstraints)
    graph: TaskGraph | None = None
    flights: list[FlightOption] = Field(default_factory=list)
    hotels: list[HotelOption] = Field(default_factory=list)
    places: list[PlaceOption] = Field(default_factory=list)
    packages: list[PackageOption] = Field(default_factory=list)
    selected_package: PackageOption | None = None
    itinerary: Itinerary | None = None
    approval: ApprovalPayload | None = None
    calendar_event_links: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    provider_calls: int = 0
    retries: int = 0
    agent_cycles: int = 0
    model_calls: int = 0
    replans: int = 0
    assumptions: list[str] = Field(default_factory=list)
    last_event_id: int | None = None
    resilience_demo: bool = False
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class ConversationSnapshot(BaseModel):
    conversation: Conversation
    messages: list[ChatMessage]
    active_run: RunState | None = None


class CreateConversationRequest(BaseModel):
    initial_message: str | None = None
    resilience_demo: bool = False


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=8, max_length=128)
    resilience_demo: bool | None = None

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()


class OperationEvent(BaseModel):
    task_id: str
    status: TaskStatus
    summary: str
    reason: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)


class AgentEvent(BaseModel):
    id: int | None = None
    run_id: UUID
    conversation_id: UUID
    user_id: str
    type: str
    phase: AgentPhase
    status: str
    summary: str
    reason: str | None = None
    task_id: str | None = None
    provider: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class AgentEventPage(BaseModel):
    items: list[AgentEvent]
    next_after: int


class ModelCallRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    conversation_id: UUID
    user_id: str
    phase: str
    model: str
    prompt_version: str
    status: Literal["completed", "failed"]
    attempts: int = 1
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ExecutionReport(BaseModel):
    run_id: UUID
    goal: str
    interpreted_constraints: TravelConstraints
    task_graph: TaskGraph
    selected_package: PackageOption
    itinerary: Itinerary
    calendar_event_links: list[str]
    tools_called: int
    model_calls: int
    retries: int
    replans: int
    assumptions: list[str]
    rejected_packages: int
    estimated_savings: int
    elapsed_seconds: float
    generated_at: datetime = Field(default_factory=utc_now)

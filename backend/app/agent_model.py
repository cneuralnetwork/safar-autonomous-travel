from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass
from datetime import date, time
from time import perf_counter
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.models import VisualTheme

AllowedToolName = Literal[
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
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TravelConstraintPatch(StrictModel):
    origin: str | None = None
    origin_airport: str | None = None
    destination: str | None = None
    destination_airport: str | None = None
    visual_theme: VisualTheme | None = None
    start_date: date | None = None
    end_date: date | None = None
    duration_days: int | None = Field(default=None, ge=1, le=30)
    adults: int | None = Field(default=None, ge=1, le=9)
    children: int | None = Field(default=None, ge=0, le=8)
    budget: int | None = Field(default=None, ge=1000)
    earliest_departure: time | None = None
    latest_departure: time | None = None
    hotel_area_preference: str | None = None
    max_hotel_distance_km: float | None = Field(default=None, ge=0.1, le=50)
    preferences: list[str] = Field(default_factory=list)


class TurnInterpretation(StrictModel):
    intent: Literal["plan_trip", "modify_trip", "answer_clarification", "other"]
    constraints: TravelConstraintPatch
    explicit_fields: list[str] = Field(default_factory=list)
    inferred_fields: list[str] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    assistant_message: str
    quick_replies: list[str] = Field(default_factory=list)


class PlanTaskDraft(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,48}$")
    title: str
    description: str
    tool_name: AllowedToolName
    arguments: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    retry_policy: int = Field(default=2, ge=0, le=4)
    optional: bool = False


class AgentPlanDraft(StrictModel):
    goal: str
    assumptions: list[str] = Field(default_factory=list)
    tasks: list[PlanTaskDraft] = Field(min_length=1, max_length=16)


class ReplanDecision(StrictModel):
    action: Literal["retry", "switch_provider", "skip_optional", "ask_user", "fail"]
    explanation: str
    user_message: str
    quick_replies: list[str] = Field(default_factory=list)
    task_id: str | None = None
    provider: str | None = None
    constraint_to_relax: str | None = None


class StationCodeCandidate(StrictModel):
    city: str
    station_codes: list[str] = Field(default_factory=list, max_length=4)
    confidence: Literal["high", "medium", "low"]
    needs_road_connector: bool = False
    connector_city: str | None = None


class StationResolution(StrictModel):
    candidates: list[StationCodeCandidate] = Field(min_length=1, max_length=2)


@dataclass(frozen=True)
class ModelMetrics:
    phase: str
    model: str
    prompt_version: str
    status: Literal["completed", "failed"]
    attempts: int
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class StructuredModelResult[T: BaseModel]:
    value: T
    metrics: ModelMetrics


class SarvamModelError(RuntimeError):
    def __init__(self, message: str, metrics: ModelMetrics) -> None:
        super().__init__(message)
        self.metrics = metrics


class SarvamGateway:
    """Small, observable wrapper around Sarvam's OpenAI-compatible chat endpoint."""

    endpoint = "https://api.sarvam.ai/v1/chat/completions"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_key = settings.sarvam_api_key
        self.model = settings.sarvam_model
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(65, connect=10),
            headers={
                "api-subscription-key": self.api_key or "",
                "Content-Type": "application/json",
            },
        )

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def close(self) -> None:
        await self.client.aclose()

    async def structured[T: BaseModel](
        self,
        *,
        phase: str,
        prompt_version: str,
        schema: type[T],
        system: str,
        payload: dict[str, Any],
        reasoning_effort: Literal["low", "medium", "high"] | None,
        max_tokens: int = 2600,
    ) -> StructuredModelResult[T]:
        if not self.enabled:
            metrics = ModelMetrics(
                phase=phase,
                model=self.model,
                prompt_version=prompt_version,
                status="failed",
                attempts=0,
                latency_ms=0,
                error_code="not_configured",
                error_message="Sarvam is not configured",
            )
            raise SarvamModelError("Sarvam is not configured", metrics)

        started = perf_counter()
        attempts = 0
        last_code = "unknown_error"
        last_message = "Sarvam request failed"
        schema_name = re.sub(r"[^a-zA-Z0-9_]", "_", schema.__name__).lower()
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                },
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "n": 1,
            "reasoning_effort": reasoning_effort,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
        }

        retryable_status_codes = {408, 409, 429, 500, 502, 503, 504}
        for attempt in range(3):
            attempts = attempt + 1
            finish_reason: str | None = None
            try:
                response = await self.client.post(self.endpoint, json=request_body)
                if response.status_code >= 400:
                    error_payload = _safe_json(response)
                    error_detail = error_payload.get("error")
                    if isinstance(error_detail, dict):
                        last_code = str(error_detail.get("code") or f"http_{response.status_code}")
                        last_message = str(error_detail.get("message") or "Sarvam request failed")
                    else:
                        last_code = f"http_{response.status_code}"
                        last_message = (
                            str(error_detail) if error_detail else "Sarvam request failed"
                        )
                    if response.status_code not in retryable_status_codes:
                        break
                    if attempt < 2:
                        await asyncio.sleep((2**attempt) + random.uniform(0.05, 0.25))
                        continue
                    break

                body = response.json()
                choice = body["choices"][0]
                finish_reason = choice.get("finish_reason")
                content = choice["message"].get("content")
                if not content:
                    raise ValueError("Sarvam returned no structured content")
                value = schema.model_validate_json(content)
                usage = body.get("usage") or {}
                metrics = ModelMetrics(
                    phase=phase,
                    model=str(body.get("model") or self.model),
                    prompt_version=prompt_version,
                    status="completed",
                    attempts=attempts,
                    latency_ms=round((perf_counter() - started) * 1000),
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    total_tokens=int(usage.get("total_tokens") or 0),
                )
                return StructuredModelResult(value=value, metrics=metrics)
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                last_code = "network_error"
                last_message = str(error) or type(error).__name__
                if attempt < 2:
                    await asyncio.sleep((2**attempt) + random.uniform(0.05, 0.25))
                    continue
            except (KeyError, ValueError, ValidationError, json.JSONDecodeError) as error:
                if finish_reason == "length":
                    last_code = "truncated_model_output"
                    last_message = (
                        "Sarvam reached the output-token limit before completing "
                        "the structured response"
                    )
                else:
                    last_code = "invalid_model_output"
                    last_message = str(error)[:300]
                if attempt < 1:
                    request_body["messages"].append(
                        {
                            "role": "system",
                            "content": (
                                "The previous response did not validate. Return exactly one JSON "
                                "object matching the supplied schema; include no markdown."
                            ),
                        }
                    )
                    continue
                break

        metrics = ModelMetrics(
            phase=phase,
            model=self.model,
            prompt_version=prompt_version,
            status="failed",
            attempts=attempts,
            latency_ms=round((perf_counter() - started) * 1000),
            error_code=last_code,
            error_message=last_message[:300],
        )
        raise SarvamModelError(last_message, metrics)


class SarvamAgent:
    def __init__(self, gateway: SarvamGateway) -> None:
        self.gateway = gateway

    async def interpret(
        self,
        *,
        today: date,
        user_message: str,
        existing_constraints: dict[str, Any],
        preferences: dict[str, Any],
        recent_messages: list[dict[str, str]],
    ) -> StructuredModelResult[TurnInterpretation]:
        return await self.gateway.structured(
            phase="interpretation",
            prompt_version="travel-turn-v3",
            schema=TurnInterpretation,
            reasoning_effort=None,
            max_tokens=4096,
            system=(
                "You are Safar's travel request interpreter. Extract only facts supported by the "
                "message or existing confirmed state. Never assume the number of travellers; "
                "leave adults unresolved unless the user explicitly states a count. Friday "
                "through Sunday is valid for 'next weekend'. Preserve confirmed values unless "
                "the user changes them. Budget is optional. Never invent an origin, destination, "
                "exact date, airport code, price, or provider result. When a destination is known, "
                "classify its dominant "
                "travel visual as exactly one visual_theme: coast for beaches and seaside cities; "
                "mountains for hill, alpine, or high-altitude destinations; heritage for forts, "
                "temples, and historic architecture; nature for forests, backwaters, and lush "
                "landscapes; or city for modern urban destinations. Chennai is coast. Write a "
                "concise, natural assistant_message. Ask at "
                "most one consolidated question when a required route/date fact is truly "
                "ambiguous. Return no chain-of-thought."
            ),
            payload={
                "today": today.isoformat(),
                "timezone": "Asia/Kolkata",
                "existing_constraints": existing_constraints,
                "saved_preferences": preferences,
                "recent_messages": recent_messages[-12:],
                "user_message": user_message,
                "required_for_search": [
                    "origin",
                    "destination",
                    "start_date",
                    "end_date",
                    "adults",
                ],
            },
        )

    async def plan(
        self,
        *,
        constraints: dict[str, Any],
        assumptions: list[str],
    ) -> StructuredModelResult[AgentPlanDraft]:
        return await self.gateway.structured(
            phase="planning",
            prompt_version="travel-plan-v2",
            schema=AgentPlanDraft,
            reasoning_effort="low",
            max_tokens=3200,
            system=(
                "Create a compact dependency DAG for a travel-planning run using only the "
                "registered tools. Search an outbound journey first, pause for the traveller's "
                "choice, then make a separate return-journey request and pause again. A journey "
                "search tries flights first and may safely fall back to RailRadar railway "
                "schedules plus OpenStreetMap road connectors when no direct route works. "
                "Search hotels only after both transport choices, then pause for the stay choice. "
                "compare_options validates the three chosen items; create_itinerary depends on "
                "a valid package and place search; request_approval must precede "
                "add_calendar_events. "
                "The calendar step prepares a portable ICS export and never writes directly "
                "to a calendar account. Do not add "
                "booking, payment, messaging, code execution, browsing, or arbitrary tools. "
                "Return operational descriptions, not chain-of-thought."
            ),
            payload={
                "constraints": constraints,
                "assumptions": assumptions,
                "registered_tools": [
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
                ],
                "hard_rules": {
                    "max_tasks": 16,
                    "selection_order": [
                        "outbound_flight",
                        "return_flight",
                        "hotel",
                    ],
                    "approval_before_calendar": True,
                },
            },
        )

    async def resolve_rail_stations(
        self,
        *,
        cities: list[str],
    ) -> StructuredModelResult[StationResolution]:
        return await self.gateway.structured(
            phase="station_resolution",
            prompt_version="rail-station-resolution-v1",
            schema=StationResolution,
            reasoning_effort="low",
            max_tokens=4096,
            system=(
                "Propose Indian Railways station codes for exactly the supplied cities. "
                "Return at most four codes per city, ordered by usefulness for an intercity "
                "traveller. Never return an airport code. For a destination without a practical "
                "railhead, propose a gateway railway station and mark needs_road_connector=true. "
                "If uncertain, return an empty station_codes list and low confidence. These are "
                "only candidates: a deterministic RailRadar lookup will validate every code. "
                "Return no chain-of-thought."
            ),
            payload={"cities": cities[:2]},
        )

    async def replan(
        self,
        *,
        constraints: dict[str, Any],
        graph: dict[str, Any],
        failure: dict[str, Any],
        attempts_remaining: bool,
    ) -> StructuredModelResult[ReplanDecision]:
        return await self.gateway.structured(
            phase="replanning",
            prompt_version="travel-replan-v2",
            schema=ReplanDecision,
            reasoning_effort="low",
            system=(
                "Choose the safest next action after a travel tool failure. Never relax a hard "
                "user constraint automatically. Provider switching, retrying transient failures, "
                "and skipping optional place enrichment are safe. Any budget, date, route, "
                "traveller, departure-time, or hotel-area relaxation requires ask_user. Produce "
                "a concise user-facing explanation and no chain-of-thought."
            ),
            payload={
                "constraints": constraints,
                "task_graph": graph,
                "failure": failure,
                "attempts_remaining": attempts_remaining,
                "allowed_actions": [
                    "retry",
                    "switch_provider",
                    "skip_optional",
                    "ask_user",
                    "fail",
                ],
            },
        )


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    except ValueError:
        return {}

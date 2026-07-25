from datetime import date
from unittest.mock import AsyncMock

from app.agent_model import (
    ModelMetrics,
    SarvamModelError,
    StructuredModelResult,
    TravelConstraintPatch,
    TurnInterpretation,
)
from app.config import Settings
from app.interpreter import RequestInterpreter


async def test_extracts_complete_goa_prompt() -> None:
    interpreter = RequestInterpreter(Settings(sarvam_api_key=None))
    constraints = await interpreter.interpret(
        (
            "plan a 3-day trip from Kolkata to Goa next weekend for two people "
            "under ₹30,000. avoid flights before 8 am and stay near the beach"
        ),
        today=date(2026, 7, 25),
    )

    assert constraints.origin == "Kolkata"
    assert constraints.destination == "Goa"
    assert constraints.origin_airport == "CCU"
    assert constraints.destination_airport == "GOI"
    assert constraints.visual_theme == "coast"
    assert constraints.start_date == date(2026, 7, 31)
    assert constraints.end_date == date(2026, 8, 2)
    assert constraints.adults == 2
    assert constraints.budget == 30_000
    assert constraints.earliest_departure.hour == 8
    assert constraints.hotel_area_preference == "beach"
    assert constraints.missing_fields == []


async def test_requests_dates_when_next_month_is_ambiguous() -> None:
    interpreter = RequestInterpreter(Settings(sarvam_api_key=None))
    constraints = await interpreter.interpret(
        "find a weekend trip from Kolkata to Goa next month under ₹20,000",
        today=date(2026, 7, 25),
    )

    assert "start_date" in constraints.missing_fields
    assert "end_date" in constraints.missing_fields


async def test_in_chat_constraint_relaxations_modify_the_existing_trip() -> None:
    interpreter = RequestInterpreter(Settings(sarvam_api_key=None))
    original = await interpreter.interpret(
        (
            "plan a 3-day trip from Kolkata to Goa next weekend under ₹20,000, "
            "avoid flights before 8 am and stay near the beach"
        ),
        today=date(2026, 7, 25),
    )

    higher_budget = await interpreter.interpret(
        "Increase the budget by ₹5,000",
        original,
        today=date(2026, 7, 25),
    )
    farther_hotel = await interpreter.interpret(
        "Show hotels a little farther away",
        higher_budget,
        today=date(2026, 7, 25),
    )
    earlier_flights = await interpreter.interpret(
        "Allow earlier flights",
        farther_hotel,
        today=date(2026, 7, 25),
    )

    assert higher_budget.budget == 25_000
    assert farther_hotel.max_hotel_distance_km == 4
    assert earlier_flights.earliest_departure is None
    assert earlier_flights.origin == "Kolkata"
    assert earlier_flights.destination == "Goa"


async def test_sarvam_fields_merge_with_validated_next_weekend_dates() -> None:
    interpreter = RequestInterpreter(Settings(sarvam_api_key="test-key"))
    interpreter.agent.interpret = AsyncMock(
        return_value=StructuredModelResult(
            value=TurnInterpretation(
                intent="plan_trip",
                constraints=TravelConstraintPatch(
                    origin="Chennai",
                    origin_airport="DEL",
                    destination="Jaipur",
                    destination_airport="BOM",
                    visual_theme="heritage",
                    budget=40_000,
                    adults=1,
                ),
                explicit_fields=["origin", "destination", "budget", "adults"],
                inferred_fields=[],
                assumptions=[],
                assistant_message=(
                    "I’ll plan Chennai to Jaipur next weekend within ₹40,000."
                ),
            ),
            metrics=ModelMetrics(
                phase="interpretation",
                model="sarvam-105b",
                prompt_version="travel-turn-v2",
                status="completed",
                attempts=1,
                latency_ms=320,
            ),
        )
    )

    outcome = await interpreter.interpret_turn(
        "I wanna go to Jaipur from Chennai for one adult, Budget 40000, next weekend",
        today=date(2026, 7, 25),
    )

    assert outcome.constraints.origin == "Chennai"
    assert outcome.constraints.origin_airport == "MAA"
    assert outcome.constraints.destination == "Jaipur"
    assert outcome.constraints.destination_airport == "JAI"
    assert outcome.constraints.visual_theme == "heritage"
    assert outcome.constraints.start_date == date(2026, 7, 31)
    assert outcome.constraints.end_date == date(2026, 8, 2)
    assert outcome.constraints.budget == 40_000
    assert outcome.constraints.missing_fields == []
    assert outcome.model_metrics is not None
    interpreter.agent.interpret.assert_awaited_once()
    await interpreter.gateway.close()


async def test_destination_change_refreshes_the_visual_theme() -> None:
    interpreter = RequestInterpreter(Settings(sarvam_api_key=None))
    original = await interpreter.interpret(
        "plan a trip from Kolkata to Chennai next weekend",
        today=date(2026, 7, 25),
    )
    changed = await interpreter.interpret(
        "Actually, go to Manali from Kolkata next weekend",
        original,
        today=date(2026, 7, 25),
    )

    assert original.visual_theme == "coast"
    assert changed.destination == "Manali"
    assert changed.visual_theme == "mountains"


async def test_sarvam_visual_theme_overrides_the_generic_fallback() -> None:
    interpreter = RequestInterpreter(Settings(sarvam_api_key="test-key"))
    interpreter.agent.interpret = AsyncMock(
        return_value=StructuredModelResult(
            value=TurnInterpretation(
                intent="plan_trip",
                constraints=TravelConstraintPatch(
                    origin="Chennai",
                    destination="Rameswaram",
                    visual_theme="coast",
                ),
                explicit_fields=["origin", "destination"],
                assistant_message="I’ll plan a coastal Rameswaram trip.",
            ),
            metrics=ModelMetrics(
                phase="interpretation",
                model="sarvam-105b",
                prompt_version="travel-turn-v3",
                status="completed",
                attempts=1,
                latency_ms=280,
            ),
        )
    )

    outcome = await interpreter.interpret_turn(
        "Plan a trip from Chennai to Rameswaram next weekend",
        today=date(2026, 7, 25),
    )

    assert outcome.constraints.destination == "Rameswaram"
    assert outcome.constraints.visual_theme == "coast"
    await interpreter.gateway.close()


async def test_sarvam_failure_falls_back_to_deterministic_interpretation() -> None:
    interpreter = RequestInterpreter(Settings(sarvam_api_key="test-key"))
    failed_metrics = ModelMetrics(
        phase="interpretation",
        model="sarvam-105b",
        prompt_version="travel-turn-v3",
        status="failed",
        attempts=2,
        latency_ms=50_000,
        error_code="truncated_model_output",
        error_message="Structured output ended early",
    )
    interpreter.agent.interpret = AsyncMock(
        side_effect=SarvamModelError(
            "Structured output ended early",
            failed_metrics,
        )
    )

    outcome = await interpreter.interpret_turn(
        "I wanna go to Goa from Guwahati",
        today=date(2026, 7, 25),
    )

    assert outcome.constraints.origin == "Guwahati"
    assert outcome.constraints.destination == "Goa"
    assert outcome.constraints.visual_theme == "coast"
    assert outcome.constraints.missing_fields == [
        "start_date",
        "end_date",
        "adults",
    ]
    assert outcome.model_metrics == failed_metrics
    assert outcome.quick_replies
    await interpreter.gateway.close()


async def test_budget_is_optional_when_route_and_dates_are_known() -> None:
    interpreter = RequestInterpreter(Settings(sarvam_api_key=None))
    constraints = await interpreter.interpret(
        "plan a trip from Kolkata to Goa next weekend for one person",
        today=date(2026, 7, 25),
    )

    assert constraints.budget is None
    assert constraints.missing_fields == []


async def test_model_cannot_infer_an_unstated_traveller_count() -> None:
    interpreter = RequestInterpreter(Settings(sarvam_api_key="test-key"))
    interpreter.agent.interpret = AsyncMock(
        return_value=StructuredModelResult(
            value=TurnInterpretation(
                intent="plan_trip",
                constraints=TravelConstraintPatch(
                    origin="Kolkata",
                    destination="Goa",
                    adults=1,
                ),
                explicit_fields=["origin", "destination"],
                inferred_fields=["adults"],
                assumptions=["Assumed 1 adult traveller"],
                assistant_message="I understood the route and dates.",
            ),
            metrics=ModelMetrics(
                phase="interpretation",
                model="sarvam-105b",
                prompt_version="travel-turn-v3",
                status="completed",
                attempts=1,
                latency_ms=100,
            ),
        )
    )

    outcome = await interpreter.interpret_turn(
        "Plan a trip from Kolkata to Goa next weekend",
        today=date(2026, 7, 25),
    )

    assert outcome.constraints.adults is None
    assert outcome.constraints.missing_fields == ["adults"]
    assert not any(
        "adult" in assumption.lower() or "traveller" in assumption.lower()
        for assumption in outcome.assumptions
    )
    await interpreter.gateway.close()


async def test_short_reply_answers_the_traveller_clarification() -> None:
    interpreter = RequestInterpreter(Settings(sarvam_api_key=None))
    current = await interpreter.interpret(
        "Plan a trip from Kolkata to Goa next weekend",
        today=date(2026, 7, 25),
    )

    assert current.adults is None
    assert current.missing_fields == ["adults"]

    resolved = await interpreter.interpret(
        "2",
        current=current,
        today=date(2026, 7, 25),
    )

    assert resolved.adults == 2
    assert resolved.missing_fields == []

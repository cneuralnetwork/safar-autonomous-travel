from datetime import date

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

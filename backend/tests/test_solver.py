from datetime import date, time

from app.models import HotelOption, TravelConstraints
from app.solver import compare_packages
from app.travel_tools import DemoTravelProvider


async def test_solver_never_violates_hard_constraints() -> None:
    constraints = TravelConstraints(
        origin="Kolkata",
        destination="Goa",
        origin_airport="CCU",
        destination_airport="GOI",
        start_date=date(2026, 7, 31),
        end_date=date(2026, 8, 2),
        adults=2,
        budget=30_000,
        earliest_departure=time(8, 0),
        hotel_area_preference="beach",
        max_hotel_distance_km=2,
    )
    provider = DemoTravelProvider()
    flights = await provider.search_flights(constraints)
    hotels = await provider.search_hotels(constraints)

    result = compare_packages(flights, hotels, constraints)

    assert result.valid
    assert result.rejected_count > 0
    for package in result.valid:
        assert package.total_price <= constraints.budget
        assert package.flight.departure_at.time() >= constraints.earliest_departure
        assert package.hotel.distance_to_preference_km <= 2


async def test_solver_explains_impossible_budget() -> None:
    constraints = TravelConstraints(
        origin="Kolkata",
        destination="Goa",
        origin_airport="CCU",
        destination_airport="GOI",
        start_date=date(2026, 7, 31),
        end_date=date(2026, 8, 2),
        adults=2,
        budget=10_000,
        earliest_departure=time(8, 0),
    )
    provider = DemoTravelProvider()
    result = compare_packages(
        await provider.search_flights(constraints),
        await provider.search_hotels(constraints),
        constraints,
    )

    assert result.valid == []
    assert result.rejection_summary["package exceeds the total budget"] > 0


async def test_solver_rejects_unverified_distance_when_area_is_a_hard_constraint() -> None:
    constraints = TravelConstraints(
        origin="Kolkata",
        destination="Goa",
        origin_airport="CCU",
        destination_airport="GOI",
        start_date=date(2026, 7, 31),
        end_date=date(2026, 8, 2),
        adults=1,
        budget=100_000,
        hotel_area_preference="beach",
        max_hotel_distance_km=2,
    )
    provider = DemoTravelProvider()
    flights = await provider.search_flights(constraints)
    hotel = HotelOption(
        id="unknown-distance",
        provider="test",
        name="Unknown Distance Hotel",
        address="Goa",
        rating=4.5,
        nightly_price=2_000,
        total_price=4_000,
        distance_to_preference_km=None,
    )

    result = compare_packages(flights, [hotel], constraints)

    assert result.valid == []
    assert result.rejection_summary["hotel distance could not be verified"] > 0

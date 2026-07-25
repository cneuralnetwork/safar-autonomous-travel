from __future__ import annotations

from dataclasses import dataclass

from app.models import FlightOption, HotelOption, PackageOption, TravelConstraints


@dataclass
class SolverResult:
    valid: list[PackageOption]
    rejected_count: int
    rejection_summary: dict[str, int]


def compare_packages(
    flights: list[FlightOption],
    hotels: list[HotelOption],
    constraints: TravelConstraints,
) -> SolverResult:
    budget = constraints.budget or 0
    days = constraints.duration_days or 1
    on_trip_reserve = 1500 * constraints.adults * days
    local_transfer_reserve = 1200
    valid: list[PackageOption] = []
    rejected_count = 0
    rejection_summary: dict[str, int] = {}

    for flight in flights:
        for hotel in hotels:
            reasons: list[str] = []
            if not hotel.available:
                reasons.append("hotel unavailable")
            if (
                constraints.earliest_departure
                and flight.departure_at.timetz().replace(tzinfo=None)
                < constraints.earliest_departure
            ):
                reasons.append("flight departs before preferred time")
            if (
                constraints.max_hotel_distance_km is not None
                and hotel.distance_to_preference_km is None
            ):
                reasons.append("hotel distance could not be verified")
            elif (
                constraints.max_hotel_distance_km is not None
                and hotel.distance_to_preference_km is not None
                and hotel.distance_to_preference_km > constraints.max_hotel_distance_km
            ):
                reasons.append("hotel is outside the preferred area")
            total = (
                flight.total_price + hotel.total_price + on_trip_reserve + local_transfer_reserve
            )
            if total > budget:
                reasons.append("package exceeds the total budget")
            if reasons:
                rejected_count += 1
                for reason in reasons:
                    rejection_summary[reason] = rejection_summary.get(reason, 0) + 1
                continue
            valid.append(
                PackageOption(
                    id=f"{flight.id}:{hotel.id}",
                    flight=flight,
                    hotel=hotel,
                    on_trip_reserve=on_trip_reserve,
                    local_transfer_reserve=local_transfer_reserve,
                    total_price=total,
                    remaining_budget=budget - total,
                    score=0,
                )
            )

    if not valid:
        return SolverResult(
            valid=[], rejected_count=rejected_count, rejection_summary=rejection_summary
        )

    prices = [package.total_price for package in valid]
    minimum, maximum = min(prices), max(prices)
    price_span = max(1, maximum - minimum)
    for package in valid:
        price_score = 1 - ((package.total_price - minimum) / price_span)
        hotel_score = package.hotel.rating / 5
        stops_score = 1 if package.flight.stops == 0 else max(0, 1 - package.flight.stops * 0.35)
        time_score = (
            1
            if not constraints.earliest_departure
            else min(
                1,
                max(
                    0,
                    (package.flight.departure_at.hour - constraints.earliest_departure.hour + 1)
                    / 5,
                ),
            )
        )
        convenience_score = 0.65 * stops_score + 0.35 * time_score
        if package.hotel.distance_to_preference_km is None:
            preference_score = 0.6
        else:
            limit = constraints.max_hotel_distance_km or 5
            preference_score = max(0, 1 - package.hotel.distance_to_preference_km / limit)
        package.score = round(
            100
            * (
                0.45 * price_score
                + 0.25 * hotel_score
                + 0.20 * convenience_score
                + 0.10 * preference_score
            ),
            2,
        )
    valid.sort(key=lambda package: (-package.score, package.total_price))
    return SolverResult(
        valid=valid[:12],
        rejected_count=rejected_count,
        rejection_summary=rejection_summary,
    )

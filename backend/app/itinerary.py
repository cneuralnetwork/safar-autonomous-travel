from __future__ import annotations

from datetime import datetime, time, timedelta

from app.models import (
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    PackageOption,
    PlaceOption,
    TravelConstraints,
)


def _closest_route(places: list[PlaceOption]) -> list[PlaceOption]:
    if len(places) < 2:
        return places
    remaining = places[1:]
    route = [places[0]]
    while remaining:
        current = route[-1]
        nearest = min(
            remaining,
            key=lambda place: (
                (place.latitude - current.latitude) ** 2
                + (place.longitude - current.longitude) ** 2
            ),
        )
        route.append(nearest)
        remaining.remove(nearest)
    return route


def create_itinerary(
    selected: PackageOption,
    places: list[PlaceOption],
    constraints: TravelConstraints,
) -> Itinerary:
    if not constraints.start_date or not constraints.end_date:
        raise ValueError("Trip dates are required")
    route = _closest_route(places)
    days: list[ItineraryDay] = []
    trip_dates = [
        constraints.start_date + timedelta(days=index)
        for index in range((constraints.end_date - constraints.start_date).days + 1)
    ]
    place_index = 0
    for day_index, trip_date in enumerate(trip_dates):
        items: list[ItineraryItem] = []
        first_day = day_index == 0
        last_day = day_index == len(trip_dates) - 1
        if first_day:
            outbound = selected.flight.outbound
            items.append(
                ItineraryItem(
                    title=(f"Fly {outbound[0].departure_airport} → {outbound[-1].arrival_airport}"),
                    description=(
                        f"{outbound[0].airline}. Arrive early enough for security and boarding."
                    ),
                    start_at=outbound[0].departure_at,
                    end_at=outbound[-1].arrival_at,
                    location=outbound[0].departure_airport,
                    category="flight",
                )
            )
            transfer_start = outbound[-1].arrival_at + timedelta(minutes=20)
            items.append(
                ItineraryItem(
                    title="Airport to hotel",
                    description="Reserved transfer and check-in buffer.",
                    start_at=transfer_start,
                    end_at=transfer_start + timedelta(minutes=75),
                    location=selected.hotel.address,
                    category="transfer",
                )
            )
            hotel_start = max(
                transfer_start + timedelta(minutes=75),
                datetime.combine(trip_date, time(14, 0), tzinfo=outbound[0].departure_at.tzinfo),
            )
            hotel_end = datetime.combine(
                constraints.end_date,
                time(11, 0),
                tzinfo=outbound[0].departure_at.tzinfo,
            )
            items.append(
                ItineraryItem(
                    title=f"Stay at {selected.hotel.name}",
                    description=(
                        f"Check-in details for {selected.hotel.name}. "
                        f"Estimated stay: ₹{selected.hotel.total_price:,}."
                    ),
                    start_at=hotel_start,
                    end_at=hotel_end,
                    location=selected.hotel.address,
                    latitude=selected.hotel.latitude,
                    longitude=selected.hotel.longitude,
                    category="hotel",
                )
            )

        activities_for_day = 1 if first_day or last_day else 2
        cursor = datetime.combine(
            trip_date,
            time(16, 30) if first_day else time(9, 30),
            tzinfo=selected.flight.outbound[0].departure_at.tzinfo,
        )
        if last_day and selected.flight.inbound:
            latest_end = selected.flight.inbound[0].departure_at - timedelta(hours=3)
        else:
            latest_end = datetime.combine(
                trip_date,
                time(20, 30),
                tzinfo=selected.flight.outbound[0].departure_at.tzinfo,
            )
        for _ in range(activities_for_day):
            if place_index >= len(route):
                break
            place = route[place_index]
            duration = timedelta(minutes=place.duration_minutes)
            if cursor + duration > latest_end:
                break
            items.append(
                ItineraryItem(
                    title=place.name,
                    description=(
                        f"{place.category.replace('_', ' ').title()}"
                        + (f" · rated {place.rating}" if place.rating else "")
                    ),
                    start_at=cursor,
                    end_at=cursor + duration,
                    location=place.address,
                    latitude=place.latitude,
                    longitude=place.longitude,
                    category="activity",
                )
            )
            cursor += duration + timedelta(minutes=45)
            place_index += 1

        if last_day and selected.flight.inbound:
            inbound = selected.flight.inbound
            transfer_end = inbound[0].departure_at - timedelta(hours=2)
            items.append(
                ItineraryItem(
                    title="Hotel to airport",
                    description="Transfer plus airport arrival buffer.",
                    start_at=transfer_end - timedelta(minutes=75),
                    end_at=transfer_end,
                    location=inbound[0].departure_airport,
                    category="transfer",
                )
            )
            items.append(
                ItineraryItem(
                    title=(f"Fly {inbound[0].departure_airport} → {inbound[-1].arrival_airport}"),
                    description=f"{inbound[0].airline}. Return journey.",
                    start_at=inbound[0].departure_at,
                    end_at=inbound[-1].arrival_at,
                    location=inbound[0].departure_airport,
                    category="flight",
                )
            )
        items.sort(key=lambda item: item.start_at)
        days.append(
            ItineraryDay(
                date=trip_date,
                title=(
                    "Arrival and settle in"
                    if first_day
                    else "Last looks and return"
                    if last_day
                    else f"Explore {constraints.destination}"
                ),
                items=items,
            )
        )
    return Itinerary(days=days)

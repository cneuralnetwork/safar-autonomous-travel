from datetime import date

import httpx

from app.models import TravelConstraints
from app.travel_tools import SerpApiProvider


def _leg(
    origin: str,
    destination: str,
    departure: str,
    arrival: str,
    flight_number: str,
) -> dict:
    return {
        "departure_airport": {"id": origin, "time": departure},
        "arrival_airport": {"id": destination, "time": arrival},
        "airline": "Test Air",
        "flight_number": flight_number,
        "duration": 150,
    }


async def test_serpapi_resolves_real_round_trip_legs_without_double_counting() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("departure_token"):
            return httpx.Response(
                200,
                json={
                    "best_flights": [
                        {
                            "flights": [
                                _leg(
                                    "GOI",
                                    "CCU",
                                    "2026-08-02 15:30",
                                    "2026-08-02 18:00",
                                    "TA 202",
                                )
                            ],
                            "price": 15_000,
                            "booking_token": "booking-token",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "best_flights": [
                    {
                        "flights": [
                            _leg(
                                "CCU",
                                "GOI",
                                "2026-07-31 09:00",
                                "2026-07-31 11:30",
                                "TA 101",
                            )
                        ],
                        "price": 12_000,
                        "departure_token": "outbound-token",
                    }
                ]
            },
        )

    provider = SerpApiProvider("test-key")
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    constraints = TravelConstraints(
        origin="Kolkata",
        destination="Goa",
        origin_airport="CCU",
        destination_airport="GOI",
        start_date=date(2026, 7, 31),
        end_date=date(2026, 8, 2),
        adults=2,
        budget=30_000,
    )

    results = await provider.search_flights(constraints)
    await provider.client.aclose()

    assert len(requests) == 2
    assert results[0].outbound[0].departure_airport == "CCU"
    assert results[0].inbound[0].departure_airport == "GOI"
    assert results[0].total_price == 15_000


async def test_serpapi_uses_two_independent_one_way_requests_for_user_choices() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        origin = request.url.params["departure_id"]
        destination = request.url.params["arrival_id"]
        travel_date = request.url.params["outbound_date"]
        is_return = origin == "GOI"
        return httpx.Response(
            200,
            json={
                "best_flights": [
                    {
                        "flights": [
                            _leg(
                                origin,
                                destination,
                                f"{travel_date} {'15:30' if is_return else '09:00'}",
                                f"{travel_date} {'18:00' if is_return else '11:30'}",
                                "TA 202" if is_return else "TA 101",
                            )
                        ],
                        "price": 8_000 if is_return else 7_000,
                    }
                ]
            },
        )

    provider = SerpApiProvider("test-key")
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    constraints = TravelConstraints(
        origin="Kolkata",
        destination="Goa",
        origin_airport="CCU",
        destination_airport="GOI",
        start_date=date(2026, 7, 31),
        end_date=date(2026, 8, 2),
        adults=2,
        budget=30_000,
    )

    outbound = await provider.search_outbound_flights(constraints)
    inbound = await provider.search_return_flights(constraints)
    await provider.client.aclose()

    assert len(requests) == 2
    assert all(request.url.params["type"] == "2" for request in requests)
    assert requests[0].url.params["departure_id"] == "CCU"
    assert requests[0].url.params["arrival_id"] == "GOI"
    assert requests[1].url.params["departure_id"] == "GOI"
    assert requests[1].url.params["arrival_id"] == "CCU"
    assert outbound[0].total_price == 7_000
    assert inbound[0].total_price == 8_000

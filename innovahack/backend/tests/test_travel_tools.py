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

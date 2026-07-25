from datetime import date, timedelta

import httpx

from app.config import Settings
from app.models import FlightSegment, TravelConstraints
from app.travel_tools import (
    OpenStreetMapPlacesProvider,
    OpenStreetMapRoadProvider,
    RailRadarProvider,
    SerpApiProvider,
)


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


async def test_openstreetmap_places_are_real_named_coordinates() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "nominatim.test":
            return httpx.Response(
                200,
                json=[
                    {
                        "display_name": "Goa, India",
                        "lat": "15.3000",
                        "lon": "74.0000",
                        "boundingbox": [
                            "14.8971",
                            "15.7999",
                            "73.6890",
                            "74.3400",
                        ],
                    }
                ],
            )
        return httpx.Response(
            200,
            json={
                "elements": [
                    {
                        "type": "node",
                        "id": 12345,
                        "lat": 15.5007,
                        "lon": 73.9116,
                        "tags": {
                            "name": "Reis Magos Fort",
                            "historic": "fort",
                            "addr:city": "Reis Magos",
                        },
                    },
                    {
                        "type": "way",
                        "id": 98765,
                        "center": {"lat": 15.5553, "lon": 73.7517},
                        "tags": {
                            "name": "Anjuna Beach",
                            "natural": "beach",
                        },
                    },
                ]
            },
        )

    provider = OpenStreetMapPlacesProvider(
        Settings(
            app_env="production",
            public_base_url="https://safar.test",
            travel_provider_mode="auto",
            nominatim_base_url="https://nominatim.test",
            overpass_base_url="https://overpass.test/api",
        )
    )
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    places = await provider.search(TravelConstraints(destination="Goa"))
    await provider.client.aclose()

    assert len(requests) == 2
    assert requests[0].url.path == "/search"
    assert requests[1].url.path == "/api/interpreter"
    assert [place.name for place in places] == ["Reis Magos Fort", "Anjuna Beach"]
    assert places[0].latitude == 15.5007
    assert places[0].longitude == 73.9116
    assert places[0].maps_url == "https://www.openstreetmap.org/node/12345"
    assert places[1].maps_url == "https://www.openstreetmap.org/way/98765"


async def test_railradar_returns_verified_train_schedule_with_estimated_fare() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/lookup/stations":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "HWH": "Howrah Junction",
                        "MAO": "Madgaon Junction",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "trains": [
                        {
                            "train": {
                                "number": "18047",
                                "name": "Amaravati Express",
                                "type": "Superfast Express",
                            },
                            "from": {
                                "departure": "23:30",
                                "day": 2,
                            },
                            "to": {
                                "arrival": "05:15",
                                "day": 4,
                            },
                            "distance": 2104.0,
                            "duration": 1785,
                            "totalHaltsBetween": 24,
                        }
                    ]
                },
            },
        )

    settings = Settings(app_env="test", travel_provider_mode="demo")
    places = OpenStreetMapPlacesProvider(settings)
    road = OpenStreetMapRoadProvider(settings, places)
    provider = RailRadarProvider("rr_live_test", "https://railradar.test", road)
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer rr_live_test"},
    )
    constraints = TravelConstraints(
        origin="Kolkata",
        destination="Goa",
        start_date=date(2026, 7, 31),
        end_date=date(2026, 8, 2),
        adults=2,
    )

    results = await provider.search_leg(constraints, "outbound")
    await provider.client.aclose()
    await road.client.aclose()
    await places.client.aclose()

    assert len(requests) == 2
    assert requests[0].headers["authorization"] == "Bearer rr_live_test"
    assert requests[1].url.path == "/v1/trains/between/HWH/MAO"
    assert requests[1].url.params["date"] == "2026-07-31"
    assert requests[1].url.params["byCity"] == "true"
    assert results[0].segments[0].mode == "train"
    assert results[0].segments[0].data_source == "RailRadar"
    assert results[0].segments[0].departure_at.date() == date(2026, 7, 31)
    assert results[0].segments[0].arrival_at.date() == date(2026, 8, 2)
    assert results[0].fare_is_estimate is True
    assert results[0].schedule_is_live is False
    assert results[0].intermediate_stops == 24
    assert results[0].total_price > 0


async def test_railradar_adds_a_road_leg_for_a_destination_without_a_railhead() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/lookup/stations":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "HWH": "Howrah Junction",
                        "ERS": "Ernakulam Junction",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "trains": [
                        {
                            "train": {
                                "number": "12660",
                                "name": "Gurudev Express",
                                "type": "Superfast Express",
                            },
                            "from": {"departure": "06:10", "day": 1},
                            "to": {"arrival": "03:20", "day": 3},
                            "distance": 2290,
                            "duration": 2710,
                            "totalHaltsBetween": 28,
                        }
                    ]
                },
            },
        )

    class FakeRoadProvider:
        async def connector_segment(
            self,
            origin: str,
            destination: str,
            *,
            depart_at=None,
            arrive_by=None,
        ):
            assert depart_at is not None
            return (
                FlightSegment(
                    airline="Road connector",
                    departure_airport=origin,
                    arrival_airport=destination,
                    departure_at=depart_at,
                    arrival_at=depart_at + timedelta(minutes=210),
                    duration_minutes=210,
                    mode="bus",
                    departure_name=origin,
                    arrival_name=destination,
                    data_quality="estimated",
                    data_source="OpenStreetMap + OSRM",
                ),
                600,
            )

    provider = RailRadarProvider(
        "rr_live_test",
        "https://railradar.test",
        FakeRoadProvider(),  # type: ignore[arg-type]
    )
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    results = await provider.search_leg(
        TravelConstraints(
            origin="Kolkata",
            destination="Munnar",
            start_date=date(2026, 7, 31),
            end_date=date(2026, 8, 3),
            adults=1,
        ),
        "outbound",
    )
    await provider.client.aclose()

    assert [segment.mode for segment in results[0].segments] == ["train", "bus"]
    assert results[0].route_type == "multimodal"
    assert results[0].stops == 1
    assert results[0].segments[1].departure_name == "Ernakulam Junction"
    assert results[0].segments[1].arrival_name == "Munnar"


async def test_railradar_rejects_sarvam_codes_that_do_not_match_the_city() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "HWH": "Howrah Junction",
                    "SDAH": "Sealdah",
                    "DD": "Daund Junction",
                },
            },
        )

    provider = RailRadarProvider(
        "rr_live_test",
        "https://railradar.test",
        object(),  # type: ignore[arg-type]
    )
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer rr_live_test"},
    )

    accepted = await provider.validate_station_candidates(
        "Kolkata",
        ["HWH", "SDAH", "CCU", "DD"],
    )
    await provider.client.aclose()

    assert accepted == ["HWH", "SDAH"]

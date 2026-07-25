from __future__ import annotations

import asyncio
import hashlib
import math
import random
from datetime import datetime, time, timedelta
from typing import Any
from uuid import uuid4

import httpx

from app.config import Settings
from app.interpreter import AIRPORTS
from app.models import (
    FlightOption,
    FlightSegment,
    HotelOption,
    PlaceOption,
    TravelConstraints,
)

IST = datetime.now().astimezone().tzinfo


class ToolError(RuntimeError):
    pass


class TemporaryToolError(ToolError):
    pass


class NoResultsError(ToolError):
    pass


class InvalidToolArguments(ToolError):
    pass


def _seed(constraints: TravelConstraints, namespace: str) -> random.Random:
    raw = (
        f"{namespace}|{constraints.origin}|{constraints.destination}|"
        f"{constraints.start_date}|{constraints.end_date}|{constraints.adults}"
    )
    value = int(hashlib.sha256(raw.encode()).hexdigest()[:12], 16)
    return random.Random(value)


class DemoTravelProvider:
    name = "demo"

    async def search_flights(self, constraints: TravelConstraints) -> list[FlightOption]:
        if not constraints.start_date or not constraints.end_date:
            raise InvalidToolArguments("Dates are required for flight search")
        rng = _seed(constraints, "flights")
        airlines = [
            ("IndiGo", "6E"),
            ("Air India", "AI"),
            ("Akasa Air", "QP"),
            ("SpiceJet", "SG"),
            ("Vistara", "UK"),
            ("IndiGo", "6E"),
        ]
        departure_hours = [6, 8, 9, 11, 14, 18]
        results: list[FlightOption] = []
        origin = constraints.origin_airport or "ORG"
        destination = constraints.destination_airport or "DST"
        choices = zip(airlines, departure_hours, strict=True)
        for index, ((airline, code), hour) in enumerate(choices):
            duration = 150 + rng.randrange(0, 60)
            outbound_start = datetime.combine(
                constraints.start_date, time(hour=hour, minute=(index * 10) % 60), tzinfo=IST
            )
            outbound_end = outbound_start + timedelta(minutes=duration)
            inbound_start = datetime.combine(
                constraints.end_date, time(hour=14 + index % 4, minute=(index * 7) % 60), tzinfo=IST
            )
            inbound_end = inbound_start + timedelta(minutes=duration + 5)
            per_person = 4600 + index * 550 + rng.randrange(0, 600)
            results.append(
                FlightOption(
                    id=f"demo-flight-{index + 1}",
                    provider=self.name,
                    outbound=[
                        FlightSegment(
                            airline=airline,
                            flight_number=f"{code} {200 + index * 17}",
                            departure_airport=origin,
                            arrival_airport=destination,
                            departure_at=outbound_start,
                            arrival_at=outbound_end,
                            duration_minutes=duration,
                        )
                    ],
                    inbound=[
                        FlightSegment(
                            airline=airline,
                            flight_number=f"{code} {310 + index * 19}",
                            departure_airport=destination,
                            arrival_airport=origin,
                            departure_at=inbound_start,
                            arrival_at=inbound_end,
                            duration_minutes=duration + 5,
                        )
                    ],
                    total_price=per_person * constraints.adults,
                    stops=0 if index < 4 else 1,
                    baggage="7 kg cabin · 15 kg check-in",
                )
            )
        await asyncio.sleep(0.45)
        return results

    async def search_hotels(self, constraints: TravelConstraints) -> list[HotelOption]:
        if not constraints.start_date or not constraints.end_date:
            raise InvalidToolArguments("Dates are required for hotel search")
        rng = _seed(constraints, "hotels")
        destination = constraints.destination or "Destination"
        names = [
            f"Casa {destination}",
            "The Shoreline House",
            "Palm & Tide",
            "Serein Stay",
            "The Local Chapter",
            "Driftwood Rooms",
        ]
        results: list[HotelOption] = []
        nights = max(1, (constraints.end_date - constraints.start_date).days)
        for index, name in enumerate(names):
            nightly = 2100 + index * 480 + rng.randrange(0, 350)
            distance = round(0.4 + index * 0.65, 1)
            results.append(
                HotelOption(
                    id=f"demo-hotel-{index + 1}",
                    provider=self.name,
                    name=name,
                    address=f"{5 + index}, {destination} Coast Road",
                    rating=round(4.7 - index * 0.12, 1),
                    review_count=180 + index * 127,
                    nightly_price=nightly,
                    total_price=nightly * nights,
                    distance_to_preference_km=distance,
                    latitude=15.49 + index * 0.006,
                    longitude=73.77 + index * 0.008,
                    available=index != 5,
                )
            )
        await asyncio.sleep(0.4)
        return results


class SerpApiProvider:
    name = "serpapi"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=35)

    async def search_flights(self, constraints: TravelConstraints) -> list[FlightOption]:
        params = {
            "engine": "google_flights",
            "api_key": self.api_key,
            "departure_id": constraints.origin_airport,
            "arrival_id": constraints.destination_airport,
            "outbound_date": constraints.start_date.isoformat() if constraints.start_date else None,
            "return_date": constraints.end_date.isoformat() if constraints.end_date else None,
            "type": "1",
            "currency": "INR",
            "gl": "in",
            "hl": "en",
            "adults": constraints.adults,
            "children": constraints.children,
            "sort_by": "2",
        }
        response = await self.client.get("https://serpapi.com/search.json", params=params)
        body = self._response_body(response, "flight search")
        outbound_candidates = (body.get("best_flights") or []) + (body.get("other_flights") or [])
        # A round-trip search returns outbound choices first. SerpApi requires a
        # second request with each departure_token to retrieve genuine return legs.
        # Resolve a bounded shortlist concurrently to avoid inventing an inbound leg.
        return_searches = await asyncio.gather(
            *[
                self._return_options(params, outbound)
                for outbound in outbound_candidates[:4]
                if outbound.get("departure_token")
            ],
            return_exceptions=True,
        )
        results: list[FlightOption] = []
        temporary_errors: list[Exception] = []
        for paired in return_searches:
            if isinstance(paired, Exception):
                if isinstance(paired, TemporaryToolError):
                    temporary_errors.append(paired)
                continue
            results.extend(
                normalized
                for outbound, inbound in paired[:2]
                if (
                    normalized := self._normalize_round_trip(
                        outbound,
                        inbound,
                        constraints,
                    )
                )
                is not None
            )
        if not results:
            if temporary_errors:
                raise TemporaryToolError(str(temporary_errors[0]))
            raise NoResultsError("SerpApi returned no matching flights")
        return results[:14]

    async def _return_options(
        self,
        base_params: dict[str, Any],
        outbound: dict[str, Any],
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        params = dict(base_params)
        params["departure_token"] = outbound["departure_token"]
        response = await self.client.get("https://serpapi.com/search.json", params=params)
        body = self._response_body(response, "return flight search")
        candidates = (body.get("best_flights") or []) + (body.get("other_flights") or [])
        return [(outbound, inbound) for inbound in candidates]

    @staticmethod
    def _response_body(response: httpx.Response, operation: str) -> dict[str, Any]:
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise TemporaryToolError(f"SerpApi {operation} returned {response.status_code}")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ToolError(f"SerpApi {operation} failed: {response.status_code}") from error
        body = response.json()
        if body.get("error"):
            raise ToolError(str(body["error"]))
        return body

    def _normalize_round_trip(
        self,
        outbound_item: dict[str, Any],
        inbound_item: dict[str, Any],
        constraints: TravelConstraints,
    ) -> FlightOption | None:
        outbound = self._normalize_segments(outbound_item, constraints)
        inbound = self._normalize_segments(
            inbound_item,
            constraints,
            reverse=True,
        )
        price = inbound_item.get("price") or outbound_item.get("price")
        if not outbound or not inbound or not price:
            return None
        identifier = hashlib.sha1(f"{outbound_item}|{inbound_item}".encode()).hexdigest()[:12]
        extensions = list(outbound_item.get("extensions") or []) + list(
            inbound_item.get("extensions") or []
        )
        baggage = next(
            (str(item) for item in extensions if "bag" in str(item).lower()),
            "Check fare rules with the provider",
        )
        return FlightOption(
            id=f"serp-{identifier}",
            provider=self.name,
            outbound=outbound,
            inbound=inbound,
            # SerpApi prices already reflect the passenger counts in the search.
            total_price=math.ceil(float(price)),
            stops=max(0, len(outbound) - 1),
            baggage=baggage,
        )

    def _normalize_segments(
        self,
        item: dict[str, Any],
        constraints: TravelConstraints,
        *,
        reverse: bool = False,
    ) -> list[FlightSegment] | None:
        raw_segments = item.get("flights") or []
        if not raw_segments:
            return None
        segments: list[FlightSegment] = []
        for raw in raw_segments:
            departure = raw.get("departure_airport") or {}
            arrival = raw.get("arrival_airport") or {}
            try:
                departure_at = self._parse_datetime(departure["time"])
                arrival_at = self._parse_datetime(arrival["time"])
            except (KeyError, ValueError):
                return None
            segments.append(
                FlightSegment(
                    airline=raw.get("airline") or "Airline",
                    flight_number=raw.get("flight_number"),
                    departure_airport=departure.get("id")
                    or (constraints.destination_airport if reverse else constraints.origin_airport)
                    or "ORG",
                    arrival_airport=arrival.get("id")
                    or (constraints.origin_airport if reverse else constraints.destination_airport)
                    or "DST",
                    departure_at=departure_at,
                    arrival_at=arrival_at,
                    duration_minutes=int(raw.get("duration") or 0),
                )
            )
        return segments

    async def search_hotels(self, constraints: TravelConstraints) -> list[HotelOption]:
        params = {
            "engine": "google_hotels",
            "api_key": self.api_key,
            "q": (
                f"hotels near {constraints.hotel_area_preference} in {constraints.destination}"
                if constraints.hotel_area_preference
                else f"hotels in {constraints.destination}"
            ),
            "check_in_date": (
                constraints.start_date.isoformat() if constraints.start_date else None
            ),
            "check_out_date": (constraints.end_date.isoformat() if constraints.end_date else None),
            "adults": constraints.adults,
            "children": constraints.children,
            "currency": "INR",
            "gl": "in",
            "hl": "en",
            "sort_by": "3",
        }
        response = await self.client.get("https://serpapi.com/search.json", params=params)
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise TemporaryToolError(f"SerpApi hotel search returned {response.status_code}")
        response.raise_for_status()
        body = response.json()
        if body.get("error"):
            raise ToolError(str(body["error"]))
        nights = max(
            1,
            ((constraints.end_date or constraints.start_date) - constraints.start_date).days
            if constraints.start_date
            else 1,
        )
        results: list[HotelOption] = []
        for index, item in enumerate((body.get("properties") or [])[:14]):
            rate = item.get("rate_per_night") or {}
            total = item.get("total_rate") or {}
            nightly = rate.get("extracted_lowest") or rate.get("extracted_before_taxes_fees")
            if not nightly:
                continue
            coords = item.get("gps_coordinates") or {}
            images = item.get("images") or []
            results.append(
                HotelOption(
                    id=f"serp-hotel-{hashlib.sha1(str(item).encode()).hexdigest()[:12]}",
                    provider=self.name,
                    name=item.get("name") or f"Hotel option {index + 1}",
                    address=item.get("address") or constraints.destination or "",
                    rating=float(item.get("overall_rating") or 0),
                    review_count=int(item.get("reviews") or 0),
                    nightly_price=int(float(nightly)),
                    total_price=int(
                        float(
                            total.get("extracted_lowest")
                            or total.get("extracted_before_taxes_fees")
                            or nightly * nights
                        )
                    ),
                    distance_to_preference_km=None,
                    latitude=coords.get("latitude"),
                    longitude=coords.get("longitude"),
                    image_url=images[0].get("thumbnail") if images else None,
                    booking_url=item.get("link"),
                )
            )
        if not results:
            raise NoResultsError("SerpApi returned no available hotels")
        return results

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace(" ", "T"))
        return parsed.replace(tzinfo=parsed.tzinfo or IST)


class AmadeusProvider:
    name = "amadeus"

    def __init__(self, client_id: str, client_secret: str, environment: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        host = "api.amadeus.com" if environment == "production" else "test.api.amadeus.com"
        self.base_url = f"https://{host}"
        self.client = httpx.AsyncClient(timeout=30)
        self._token: str | None = None
        self._token_expires = datetime.min.replace(tzinfo=IST)

    async def _headers(self) -> dict[str, str]:
        if not self._token or self._token_expires <= datetime.now(tz=IST):
            response = await self.client.post(
                f"{self.base_url}/v1/security/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            response.raise_for_status()
            body = response.json()
            self._token = body["access_token"]
            self._token_expires = datetime.now(tz=IST) + timedelta(
                seconds=int(body.get("expires_in", 1700))
            )
        return {"Authorization": f"Bearer {self._token}"}

    async def search_flights(self, constraints: TravelConstraints) -> list[FlightOption]:
        response = await self.client.get(
            f"{self.base_url}/v2/shopping/flight-offers",
            headers=await self._headers(),
            params={
                "originLocationCode": constraints.origin_airport,
                "destinationLocationCode": constraints.destination_airport,
                "departureDate": constraints.start_date,
                "returnDate": constraints.end_date,
                "adults": constraints.adults,
                "children": constraints.children or None,
                "currencyCode": "INR",
                "max": 12,
            },
        )
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise TemporaryToolError(f"Amadeus flight search returned {response.status_code}")
        response.raise_for_status()
        body = response.json()
        results: list[FlightOption] = []
        for item in body.get("data", []):
            itineraries = item.get("itineraries") or []
            if not itineraries:
                continue
            groups = [self._normalize_itinerary(itinerary) for itinerary in itineraries[:2]]
            results.append(
                FlightOption(
                    id=f"amadeus-{item['id']}",
                    provider=self.name,
                    outbound=groups[0],
                    inbound=groups[1] if len(groups) > 1 else [],
                    total_price=math.ceil(float(item["price"]["grandTotal"])),
                    stops=max(0, len(groups[0]) - 1),
                    baggage="See airline fare conditions",
                )
            )
        if not results:
            raise NoResultsError("Amadeus returned no matching flights")
        return results

    def _normalize_itinerary(self, itinerary: dict[str, Any]) -> list[FlightSegment]:
        output: list[FlightSegment] = []
        for segment in itinerary.get("segments") or []:
            departure_at = datetime.fromisoformat(segment["departure"]["at"]).replace(tzinfo=IST)
            arrival_at = datetime.fromisoformat(segment["arrival"]["at"]).replace(tzinfo=IST)
            output.append(
                FlightSegment(
                    airline=segment.get("carrierCode") or "Airline",
                    flight_number=(
                        f"{segment.get('carrierCode', '')} {segment.get('number', '')}".strip()
                    ),
                    departure_airport=segment["departure"]["iataCode"],
                    arrival_airport=segment["arrival"]["iataCode"],
                    departure_at=departure_at,
                    arrival_at=arrival_at,
                    duration_minutes=max(1, int((arrival_at - departure_at).total_seconds() / 60)),
                )
            )
        return output

    async def search_hotels(self, constraints: TravelConstraints) -> list[HotelOption]:
        list_response = await self.client.get(
            f"{self.base_url}/v1/reference-data/locations/hotels/by-city",
            headers=await self._headers(),
            params={"cityCode": constraints.destination_airport, "radius": 20, "radiusUnit": "KM"},
        )
        if list_response.status_code >= 400:
            raise TemporaryToolError(
                f"Amadeus hotel catalogue returned {list_response.status_code}"
            )
        ids = [item["hotelId"] for item in list_response.json().get("data", [])[:20]]
        if not ids:
            raise NoResultsError("Amadeus hotel catalogue returned no properties")
        response = await self.client.get(
            f"{self.base_url}/v3/shopping/hotel-offers",
            headers=await self._headers(),
            params={
                "hotelIds": ",".join(ids),
                "adults": constraints.adults,
                "checkInDate": constraints.start_date,
                "checkOutDate": constraints.end_date,
                "currency": "INR",
                "bestRateOnly": "true",
            },
        )
        response.raise_for_status()
        results: list[HotelOption] = []
        nights = max(1, (constraints.end_date - constraints.start_date).days)
        for item in response.json().get("data", []):
            hotel = item.get("hotel") or {}
            offers = item.get("offers") or []
            if not offers:
                continue
            price = offers[0].get("price") or {}
            total = math.ceil(float(price.get("total") or price.get("base") or 0))
            results.append(
                HotelOption(
                    id=f"amadeus-hotel-{hotel.get('hotelId', uuid4())}",
                    provider=self.name,
                    name=hotel.get("name") or "Hotel",
                    address=constraints.destination or "",
                    rating=float(hotel.get("rating") or 0),
                    nightly_price=math.ceil(total / nights),
                    total_price=total,
                    latitude=hotel.get("latitude"),
                    longitude=hotel.get("longitude"),
                )
            )
        if not results:
            raise NoResultsError("Amadeus returned no available hotel offers")
        return results


class PlacesProvider:
    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.google_maps_api_key
        self.production = settings.production
        self.client = httpx.AsyncClient(timeout=25)

    async def search(self, constraints: TravelConstraints) -> list[PlaceOption]:
        if not self.api_key:
            if self.production:
                raise ToolError("Google Places API is not configured")
            return self._demo_places(constraints)
        response = await self.client.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": (
                    "places.id,places.displayName,places.formattedAddress,"
                    "places.location,places.rating,places.primaryType,"
                    "places.googleMapsUri,places.photos"
                ),
            },
            json={
                "textQuery": (
                    f"top attractions, beaches and local experiences in {constraints.destination}"
                ),
                "pageSize": 12,
                "languageCode": "en",
                "regionCode": "IN",
            },
        )
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise TemporaryToolError(f"Google Places returned {response.status_code}")
        response.raise_for_status()
        results: list[PlaceOption] = []
        for item in response.json().get("places") or []:
            location = item.get("location") or {}
            if "latitude" not in location or "longitude" not in location:
                continue
            results.append(
                PlaceOption(
                    id=item.get("id") or str(uuid4()),
                    name=(item.get("displayName") or {}).get("text") or "Place",
                    address=item.get("formattedAddress") or "",
                    category=item.get("primaryType") or "attraction",
                    rating=item.get("rating"),
                    latitude=location["latitude"],
                    longitude=location["longitude"],
                    maps_url=item.get("googleMapsUri"),
                )
            )
        if not results:
            raise NoResultsError("Google Places returned no activities")
        return results

    async def enrich_hotel_distances(
        self,
        hotels: list[HotelOption],
        constraints: TravelConstraints,
    ) -> list[HotelOption]:
        if (
            not constraints.hotel_area_preference
            or constraints.max_hotel_distance_km is None
            or all(hotel.distance_to_preference_km is not None for hotel in hotels)
        ):
            return hotels
        if not self.api_key:
            return hotels
        response = await self.client.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": "places.location",
            },
            json={
                "textQuery": (
                    f"{constraints.hotel_area_preference} places in {constraints.destination}"
                ),
                "pageSize": 10,
                "languageCode": "en",
                "regionCode": "IN",
            },
        )
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise TemporaryToolError(
                f"Google Places distance verification returned {response.status_code}"
            )
        response.raise_for_status()
        reference_points = [
            (float(location["latitude"]), float(location["longitude"]))
            for item in response.json().get("places") or []
            if (location := item.get("location"))
            and "latitude" in location
            and "longitude" in location
        ]
        if not reference_points:
            return hotels
        for hotel in hotels:
            if (
                hotel.distance_to_preference_km is None
                and hotel.latitude is not None
                and hotel.longitude is not None
            ):
                hotel.distance_to_preference_km = round(
                    min(
                        _haversine_km(
                            hotel.latitude,
                            hotel.longitude,
                            latitude,
                            longitude,
                        )
                        for latitude, longitude in reference_points
                    ),
                    1,
                )
        return hotels

    def _demo_places(self, constraints: TravelConstraints) -> list[PlaceOption]:
        destination = constraints.destination or "Goa"
        names = [
            ("Sunrise beach walk", "beach"),
            ("Old quarter heritage trail", "museum"),
            ("Local market & food crawl", "market"),
            ("Riverside sunset point", "viewpoint"),
            ("Coastal fort", "historical_landmark"),
            ("Neighbourhood café", "restaurant"),
            ("Art and craft studio", "art_gallery"),
            ("Nature reserve", "park"),
        ]
        return [
            PlaceOption(
                id=f"demo-place-{index + 1}",
                name=name,
                address=f"{destination}, India",
                category=category,
                rating=round(4.8 - index * 0.08, 1),
                latitude=15.48 + index * 0.012,
                longitude=73.76 + index * 0.009,
                duration_minutes=90 if category == "restaurant" else 120,
            )
            for index, (name, category) in enumerate(names)
        ]


class TravelToolRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.demo = DemoTravelProvider()
        self.providers: list[Any] = []
        if settings.serpapi_api_key:
            self.providers.append(SerpApiProvider(settings.serpapi_api_key))
        if settings.amadeus_client_id and settings.amadeus_client_secret:
            self.providers.append(
                AmadeusProvider(
                    settings.amadeus_client_id,
                    settings.amadeus_client_secret,
                    settings.amadeus_env,
                )
            )
        if not settings.production or settings.travel_provider_mode == "demo":
            self.providers.append(self.demo)
        self.places = PlacesProvider(settings)

    def provider_names(self) -> list[str]:
        return [provider.name for provider in self.providers]

    async def close(self) -> None:
        clients = [
            provider.client
            for provider in self.providers
            if hasattr(provider, "client")
        ]
        clients.append(self.places.client)
        await asyncio.gather(
            *(client.aclose() for client in clients),
            return_exceptions=True,
        )

    async def resolve_locations(
        self,
        constraints: TravelConstraints,
    ) -> tuple[str, str]:
        origin = constraints.origin_airport or AIRPORTS.get(
            (constraints.origin or "").lower()
        )
        destination = constraints.destination_airport or AIRPORTS.get(
            (constraints.destination or "").lower()
        )
        if not origin or not destination:
            unresolved = []
            if not origin:
                unresolved.append(constraints.origin or "departure city")
            if not destination:
                unresolved.append(constraints.destination or "destination city")
            raise NoResultsError(
                "Could not safely resolve an airport for " + " and ".join(unresolved)
            )
        return origin, destination

    async def enrich_hotel_distances(
        self,
        hotels: list[HotelOption],
        constraints: TravelConstraints,
    ) -> list[HotelOption]:
        return await self.places.enrich_hotel_distances(hotels, constraints)


def _haversine_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    radius_km = 6371.0088
    latitude_a_radians = math.radians(latitude_a)
    latitude_b_radians = math.radians(latitude_b)
    latitude_delta = math.radians(latitude_b - latitude_a)
    longitude_delta = math.radians(longitude_b - longitude_a)
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude_a_radians)
        * math.cos(latitude_b_radians)
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(value))

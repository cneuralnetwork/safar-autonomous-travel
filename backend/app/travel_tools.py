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
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise TemporaryToolError(f"SerpApi flight search returned {response.status_code}")
        response.raise_for_status()
        body = response.json()
        if body.get("error"):
            raise ToolError(str(body["error"]))
        candidates = (body.get("best_flights") or []) + (body.get("other_flights") or [])
        results = [
            normalized
            for item in candidates[:14]
            if (normalized := self._normalize_flight(item, constraints)) is not None
        ]
        if not results:
            raise NoResultsError("SerpApi returned no matching flights")
        return results

    def _normalize_flight(
        self, item: dict[str, Any], constraints: TravelConstraints
    ) -> FlightOption | None:
        raw_segments = item.get("flights") or []
        if not raw_segments or not item.get("price"):
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
                    departure_airport=departure.get("id") or constraints.origin_airport or "ORG",
                    arrival_airport=arrival.get("id") or constraints.destination_airport or "DST",
                    departure_at=departure_at,
                    arrival_at=arrival_at,
                    duration_minutes=int(raw.get("duration") or 0),
                )
            )
        return_date = constraints.end_date or segments[-1].arrival_at.date()
        return_departure = datetime.combine(return_date, time(15, 30), tzinfo=IST)
        total_duration = max(120, sum(segment.duration_minutes for segment in segments))
        airline = segments[0].airline
        inbound = [
            FlightSegment(
                airline=airline,
                flight_number=None,
                departure_airport=constraints.destination_airport or "DST",
                arrival_airport=constraints.origin_airport or "ORG",
                departure_at=return_departure,
                arrival_at=return_departure + timedelta(minutes=total_duration),
                duration_minutes=total_duration,
            )
        ]
        return FlightOption(
            id=f"serp-{hashlib.sha1(str(item).encode()).hexdigest()[:12]}",
            provider=self.name,
            outbound=segments,
            inbound=inbound,
            total_price=int(float(item["price"])) * constraints.adults,
            stops=max(0, len(segments) - 1),
            baggage="Check fare rules with the provider",
            booking_url=item.get("booking_token"),
        )

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

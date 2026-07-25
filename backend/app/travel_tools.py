from __future__ import annotations

import asyncio
import difflib
import hashlib
import math
import random
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from time import monotonic
from typing import Any
from uuid import uuid4

import httpx

from app.config import Settings
from app.interpreter import AIRPORTS
from app.models import (
    FlightLegOption,
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

    async def search_outbound_flights(
        self,
        constraints: TravelConstraints,
    ) -> list[FlightLegOption]:
        return await self._search_flight_leg(constraints, "outbound")

    async def search_return_flights(
        self,
        constraints: TravelConstraints,
    ) -> list[FlightLegOption]:
        return await self._search_flight_leg(constraints, "return")

    async def _search_flight_leg(
        self,
        constraints: TravelConstraints,
        leg: str,
    ) -> list[FlightLegOption]:
        if not constraints.start_date or not constraints.end_date:
            raise InvalidToolArguments("Dates are required for flight search")
        is_return = leg == "return"
        rng = _seed(constraints, f"{leg}-flights")
        airlines = [
            ("IndiGo", "6E"),
            ("Air India", "AI"),
            ("Akasa Air", "QP"),
            ("SpiceJet", "SG"),
            ("Air India Express", "IX"),
            ("IndiGo", "6E"),
        ]
        departure_hours = [7, 9, 10, 12, 15, 18] if is_return else [6, 8, 9, 11, 14, 18]
        travel_date = constraints.end_date if is_return else constraints.start_date
        origin = (constraints.destination_airport if is_return else constraints.origin_airport) or (
            "DST" if is_return else "ORG"
        )
        destination = (
            constraints.origin_airport if is_return else constraints.destination_airport
        ) or ("ORG" if is_return else "DST")
        results: list[FlightLegOption] = []
        for index, ((airline, code), hour) in enumerate(
            zip(airlines, departure_hours, strict=True)
        ):
            duration = 145 + rng.randrange(0, 75)
            departure_at = datetime.combine(
                travel_date,
                time(hour=hour, minute=(index * (13 if is_return else 11)) % 60),
                tzinfo=IST,
            )
            arrival_at = departure_at + timedelta(minutes=duration)
            per_person = (
                (3_250 if is_return else 3_050)
                + index * (390 if is_return else 360)
                + rng.randrange(0, 520)
            )
            results.append(
                FlightLegOption(
                    id=f"demo-{leg}-flight-{index + 1}",
                    provider=self.name,
                    leg="return" if is_return else "outbound",
                    segments=[
                        FlightSegment(
                            airline=airline,
                            flight_number=f"{code} {(430 if is_return else 200) + index * 17}",
                            departure_airport=origin,
                            arrival_airport=destination,
                            departure_at=departure_at,
                            arrival_at=arrival_at,
                            duration_minutes=duration,
                        )
                    ],
                    total_price=per_person * constraints.adults,
                    stops=0,
                    baggage="7 kg cabin · 15 kg check-in",
                )
            )
        await asyncio.sleep(0.3)
        return results

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

    async def search_outbound_flights(
        self,
        constraints: TravelConstraints,
    ) -> list[FlightLegOption]:
        return await self._search_one_way(constraints, "outbound")

    async def search_return_flights(
        self,
        constraints: TravelConstraints,
    ) -> list[FlightLegOption]:
        return await self._search_one_way(constraints, "return")

    async def _search_one_way(
        self,
        constraints: TravelConstraints,
        leg: str,
    ) -> list[FlightLegOption]:
        is_return = leg == "return"
        travel_date = constraints.end_date if is_return else constraints.start_date
        departure_id = constraints.destination_airport if is_return else constraints.origin_airport
        arrival_id = constraints.origin_airport if is_return else constraints.destination_airport
        if not travel_date or not departure_id or not arrival_id:
            raise InvalidToolArguments("Resolved route and date are required for flight search")
        params = {
            "engine": "google_flights",
            "api_key": self.api_key,
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "outbound_date": travel_date.isoformat(),
            "type": "2",
            "currency": "INR",
            "gl": "in",
            "hl": "en",
            "adults": constraints.adults,
            "children": constraints.children,
            "sort_by": "2",
        }
        response = await self.client.get("https://serpapi.com/search.json", params=params)
        body = self._response_body(response, f"{leg} flight search")
        candidates = (body.get("best_flights") or []) + (body.get("other_flights") or [])
        results = [
            normalized
            for item in candidates[:14]
            if (normalized := self._normalize_leg(item, constraints, leg)) is not None
        ]
        if not results:
            raise NoResultsError(f"SerpApi returned no {leg} flights")
        return results

    def _normalize_leg(
        self,
        item: dict[str, Any],
        constraints: TravelConstraints,
        leg: str,
    ) -> FlightLegOption | None:
        is_return = leg == "return"
        segments = self._normalize_segments(item, constraints, reverse=is_return)
        price = item.get("price")
        if not segments or not price:
            return None
        identifier = hashlib.sha1(f"{leg}|{item}".encode()).hexdigest()[:12]
        extensions = list(item.get("extensions") or [])
        baggage = next(
            (str(value) for value in extensions if "bag" in str(value).lower()),
            "Check fare rules with the provider",
        )
        return FlightLegOption(
            id=f"serp-{leg}-{identifier}",
            provider=self.name,
            leg="return" if is_return else "outbound",
            segments=segments,
            total_price=math.ceil(float(price)),
            stops=max(0, len(segments) - 1),
            baggage=baggage,
        )

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

    async def search_outbound_flights(
        self,
        constraints: TravelConstraints,
    ) -> list[FlightLegOption]:
        return await self._search_one_way(constraints, "outbound")

    async def search_return_flights(
        self,
        constraints: TravelConstraints,
    ) -> list[FlightLegOption]:
        return await self._search_one_way(constraints, "return")

    async def _search_one_way(
        self,
        constraints: TravelConstraints,
        leg: str,
    ) -> list[FlightLegOption]:
        is_return = leg == "return"
        travel_date = constraints.end_date if is_return else constraints.start_date
        origin = constraints.destination_airport if is_return else constraints.origin_airport
        destination = constraints.origin_airport if is_return else constraints.destination_airport
        if not travel_date or not origin or not destination:
            raise InvalidToolArguments("Resolved route and date are required for flight search")
        response = await self.client.get(
            f"{self.base_url}/v2/shopping/flight-offers",
            headers=await self._headers(),
            params={
                "originLocationCode": origin,
                "destinationLocationCode": destination,
                "departureDate": travel_date,
                "adults": constraints.adults,
                "children": constraints.children or None,
                "currencyCode": "INR",
                "max": 12,
            },
        )
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise TemporaryToolError(f"Amadeus {leg} flight search returned {response.status_code}")
        response.raise_for_status()
        results: list[FlightLegOption] = []
        for item in response.json().get("data", []):
            itineraries = item.get("itineraries") or []
            if not itineraries:
                continue
            segments = self._normalize_itinerary(itineraries[0])
            if not segments:
                continue
            results.append(
                FlightLegOption(
                    id=f"amadeus-{leg}-{item['id']}",
                    provider=self.name,
                    leg="return" if is_return else "outbound",
                    segments=segments,
                    total_price=math.ceil(float(item["price"]["grandTotal"])),
                    stops=max(0, len(segments) - 1),
                    baggage="See airline fare conditions",
                )
            )
        if not results:
            raise NoResultsError(f"Amadeus returned no {leg} flights")
        return results

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


class OpenStreetMapPlacesProvider:
    def __init__(self, settings: Settings) -> None:
        self.nominatim_base_url = settings.nominatim_base_url.rstrip("/")
        self.overpass_base_url = settings.overpass_base_url.rstrip("/")
        self.use_demo = (
            settings.app_env.lower() == "test"
            or settings.travel_provider_mode == "demo"
        )
        self.client = httpx.AsyncClient(
            timeout=35,
            headers={
                "User-Agent": (
                    "SafarTravelPlanner/1.0 "
                    f"(travel itinerary service; {settings.public_base_url})"
                ),
                "Accept-Language": "en",
            },
        )
        self._nominatim_lock = asyncio.Lock()
        self._last_nominatim_request = 0.0
        self._nominatim_cache: dict[str, list[dict[str, Any]]] = {}

    async def search(self, constraints: TravelConstraints) -> list[PlaceOption]:
        if self.use_demo:
            return self._demo_places(constraints)
        if not constraints.destination:
            raise InvalidToolArguments("A destination is required for place search")

        destination = await self._nominatim_search(
            f"{constraints.destination}, India",
            limit=1,
        )
        if not destination:
            raise NoResultsError(
                f"OpenStreetMap could not locate {constraints.destination}"
            )
        bounding_box = destination[0].get("boundingbox") or []
        if len(bounding_box) != 4:
            raise NoResultsError(
                f"OpenStreetMap returned no usable boundary for {constraints.destination}"
            )
        south, north, west, east = (float(value) for value in bounding_box)
        bbox = f"{south},{west},{north},{east}"
        query = f"""
[out:json][timeout:25];
(
  nwr["tourism"~"attraction|museum|gallery|viewpoint|zoo|theme_park"]({bbox});
  nwr["historic"]({bbox});
  nwr["natural"="beach"]({bbox});
  nwr["leisure"~"park|nature_reserve"]({bbox});
  nwr["amenity"="marketplace"]({bbox});
);
out center tags 80;
""".strip()
        response = await self.client.post(
            f"{self.overpass_base_url}/interpreter",
            data={"data": query},
        )
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise TemporaryToolError(
                f"OpenStreetMap Overpass returned {response.status_code}"
            )
        response.raise_for_status()
        results: list[PlaceOption] = []
        seen_names: set[str] = set()
        for item in response.json().get("elements") or []:
            tags = item.get("tags") or {}
            name = tags.get("name:en") or tags.get("name")
            center = item.get("center") or {}
            latitude = item.get("lat", center.get("lat"))
            longitude = item.get("lon", center.get("lon"))
            if not name or latitude is None or longitude is None:
                continue
            normalized_name = str(name).casefold()
            if normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)
            category = self._osm_category(tags)
            osm_type = item.get("type")
            osm_id = item.get("id")
            address = ", ".join(
                dict.fromkeys(
                    str(value)
                    for value in (
                        tags.get("addr:housename"),
                        tags.get("addr:street"),
                        tags.get("addr:suburb"),
                        tags.get("addr:city"),
                        constraints.destination,
                    )
                    if value
                )
            )
            results.append(
                PlaceOption(
                    id=f"osm-{osm_type}-{osm_id or uuid4()}",
                    name=str(name),
                    address=address,
                    category=category,
                    latitude=float(latitude),
                    longitude=float(longitude),
                    duration_minutes=self._duration_for_category(category),
                    maps_url=(
                        f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
                        if osm_type in {"node", "way", "relation"} and osm_id
                        else (
                            "https://www.openstreetmap.org/"
                            f"?mlat={latitude}&mlon={longitude}#map=17/{latitude}/{longitude}"
                        )
                    ),
                )
            )
            if len(results) >= 12:
                break
        if not results:
            raise NoResultsError(
                f"OpenStreetMap returned no named activities in {constraints.destination}"
            )
        return results

    async def geocode(self, query: str) -> tuple[float, float] | None:
        results = await self._nominatim_search(query, limit=1)
        if not results:
            return None
        latitude = results[0].get("lat")
        longitude = results[0].get("lon")
        if latitude is None or longitude is None:
            return None
        return float(latitude), float(longitude)

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
        if self.use_demo:
            return hotels
        locations = await self._nominatim_search(
            (
                f"{constraints.hotel_area_preference} in "
                f"{constraints.destination}, India"
            ),
            limit=8,
        )
        reference_points = [
            (float(item["lat"]), float(item["lon"]))
            for item in locations
            if item.get("lat") is not None and item.get("lon") is not None
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

    async def _nominatim_search(
        self,
        query: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        cache_key = f"{query.casefold()}|{limit}"
        if cache_key in self._nominatim_cache:
            return self._nominatim_cache[cache_key]
        async with self._nominatim_lock:
            if cache_key in self._nominatim_cache:
                return self._nominatim_cache[cache_key]
            remaining = 1.05 - (monotonic() - self._last_nominatim_request)
            if remaining > 0:
                await asyncio.sleep(remaining)
            try:
                response = await self.client.get(
                    f"{self.nominatim_base_url}/search",
                    params={
                        "q": query,
                        "format": "jsonv2",
                        "addressdetails": "1",
                        "limit": str(limit),
                    },
                )
            finally:
                self._last_nominatim_request = monotonic()
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise TemporaryToolError(
                f"OpenStreetMap Nominatim returned {response.status_code}"
            )
        response.raise_for_status()
        results = response.json()
        if not isinstance(results, list):
            raise ToolError("OpenStreetMap Nominatim returned invalid data")
        self._nominatim_cache[cache_key] = results
        return results

    @staticmethod
    def _osm_category(tags: dict[str, Any]) -> str:
        for key in ("tourism", "historic", "natural", "leisure", "amenity"):
            value = tags.get(key)
            if value:
                return str(value)
        return "attraction"

    @staticmethod
    def _duration_for_category(category: str) -> int:
        if category in {"museum", "gallery", "zoo", "theme_park"}:
            return 150
        if category in {"marketplace", "viewpoint", "beach"}:
            return 90
        return 120

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


STATION_ALIASES: dict[str, list[str]] = {
    "agra": ["AGC", "AF"],
    "ahmedabad": ["ADI"],
    "amritsar": ["ASR"],
    "bengaluru": ["SBC", "YPR", "KJM"],
    "bangalore": ["SBC", "YPR", "KJM"],
    "bhopal": ["BPL", "RKMP"],
    "bhubaneswar": ["BBS"],
    "chandigarh": ["CDG"],
    "chennai": ["MAS", "MS", "TBM"],
    "coimbatore": ["CBE"],
    "dehradun": ["DDN"],
    "delhi": ["NDLS", "DLI", "NZM", "ANVT"],
    "new delhi": ["NDLS", "DLI", "NZM", "ANVT"],
    "ernakulam": ["ERS", "ERN"],
    "goa": ["MAO", "THVM", "VSG", "KRMI"],
    "guwahati": ["GHY", "KYQ"],
    "hyderabad": ["SC", "HYB", "KCG"],
    "indore": ["INDB"],
    "jaipur": ["JP"],
    "jaisalmer": ["JSM"],
    "jodhpur": ["JU"],
    "kochi": ["ERS", "ERN"],
    "kolkata": ["HWH", "SDAH", "KOAA", "SHM"],
    "lucknow": ["LKO", "LJN"],
    "madgaon": ["MAO"],
    "mumbai": ["CSMT", "MMCT", "BDTS", "LTT", "BVI"],
    "mysuru": ["MYS"],
    "mysore": ["MYS"],
    "nagpur": ["NGP"],
    "patna": ["PNBE", "PPTA"],
    "pondicherry": ["PDY"],
    "puducherry": ["PDY"],
    "pune": ["PUNE"],
    "ranchi": ["RNC"],
    "rishikesh": ["YNRK", "RKSH"],
    "srinagar": ["SINA"],
    "surat": ["ST"],
    "udaipur": ["UDZ"],
    "varanasi": ["BSB", "BSBS"],
}

# Destinations without a practical city-centre railhead are decomposed into a
# RailRadar train leg plus a road connector. The road leg is calculated from
# OpenStreetMap/OSRM and is always labelled as an estimate.
RAIL_GATEWAYS: dict[str, tuple[str, str]] = {
    "coorg": ("MYS", "Mysuru"),
    "darjeeling": ("NJP", "New Jalpaiguri"),
    "dharamshala": ("PTK", "Pathankot"),
    "gangtok": ("NJP", "New Jalpaiguri"),
    "gulmarg": ("SINA", "Srinagar"),
    "ladakh": ("JAT", "Jammu Tawi"),
    "leh": ("JAT", "Jammu Tawi"),
    "manali": ("CDG", "Chandigarh"),
    "mcleodganj": ("PTK", "Pathankot"),
    "munnar": ("ERS", "Ernakulam"),
    "ooty": ("CBE", "Coimbatore"),
    "shimla": ("KLK", "Kalka"),
    "spiti": ("CDG", "Chandigarh"),
}

SOUTH_RAILHEADS = {
    "CBE",
    "ERS",
    "MAO",
    "MAS",
    "MYS",
    "PDY",
    "SBC",
    "SC",
    "TBM",
    "YPR",
}
NORTH_RAILHEADS = {
    "AGC",
    "ASR",
    "CDG",
    "DLI",
    "JAT",
    "JP",
    "KLK",
    "NDLS",
    "NZM",
}
EAST_RAILHEADS = {
    "BBS",
    "GHY",
    "HWH",
    "KOAA",
    "PNBE",
    "RNC",
    "SDAH",
}


def _normalized_place(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


@dataclass(frozen=True)
class RailEndpoint:
    code: str
    name: str
    requested_city: str
    road_connector_required: bool = False


class OpenStreetMapRoadProvider:
    name = "openstreetmap-road"

    def __init__(
        self,
        settings: Settings,
        places: OpenStreetMapPlacesProvider,
    ) -> None:
        self.base_url = settings.osrm_base_url.rstrip("/")
        self.places = places
        self.client = httpx.AsyncClient(timeout=35)
        self._route_cache: dict[str, tuple[float, int]] = {}

    async def search_leg(
        self,
        constraints: TravelConstraints,
        leg: str,
    ) -> list[FlightLegOption]:
        is_return = leg == "return"
        origin = constraints.destination if is_return else constraints.origin
        destination = constraints.origin if is_return else constraints.destination
        travel_date = constraints.end_date if is_return else constraints.start_date
        if not origin or not destination or not travel_date:
            raise InvalidToolArguments("Cities and date are required for a road fallback")
        distance_km, duration_minutes = await self.route_metrics(origin, destination)
        travellers = (constraints.adults or 1) + constraints.children
        services = [
            ("Intercity coach", 7, 1.0),
            ("AC seater coach", 14, 1.18),
            ("Overnight sleeper coach", 21, 1.34),
        ]
        results: list[FlightLegOption] = []
        for index, (service, hour, fare_factor) in enumerate(services):
            departure_at = datetime.combine(
                travel_date,
                time(hour=hour, minute=index * 10),
                tzinfo=IST,
            )
            arrival_at = departure_at + timedelta(minutes=duration_minutes)
            estimated_fare = math.ceil(
                max(250, distance_km * 1.55 * fare_factor) * travellers
            )
            segment = FlightSegment(
                airline=service,
                flight_number=None,
                departure_airport=origin,
                arrival_airport=destination,
                departure_at=departure_at,
                arrival_at=arrival_at,
                duration_minutes=duration_minutes,
                mode="bus",
                service_name=service,
                departure_name=origin,
                arrival_name=destination,
                data_quality="estimated",
                data_source="OpenStreetMap + OSRM",
                distance_km=round(distance_km, 1),
            )
            identifier = hashlib.sha1(
                f"{leg}|{origin}|{destination}|{travel_date}|{service}".encode()
            ).hexdigest()[:12]
            results.append(
                FlightLegOption(
                    id=f"osm-bus-{identifier}",
                    provider=self.name,
                    leg="return" if is_return else "outbound",
                    segments=[segment],
                    total_price=estimated_fare,
                    stops=0,
                    baggage="Confirm luggage allowance with the coach operator",
                    route_type="direct",
                    fare_is_estimate=True,
                    schedule_is_live=False,
                    source_note=(
                        "Road distance and duration use OpenStreetMap routing; "
                        "coach time and fare are planning estimates."
                    ),
                )
            )
        return results

    async def connector_segment(
        self,
        origin: str,
        destination: str,
        *,
        depart_at: datetime | None = None,
        arrive_by: datetime | None = None,
    ) -> tuple[FlightSegment, int]:
        distance_km, duration_minutes = await self.route_metrics(origin, destination)
        if arrive_by:
            arrival_at = arrive_by
            departure_at = arrival_at - timedelta(minutes=duration_minutes)
        elif depart_at:
            departure_at = depart_at
            arrival_at = departure_at + timedelta(minutes=duration_minutes)
        else:
            raise InvalidToolArguments("A connector needs a departure or arrival time")
        fare = math.ceil(max(150, distance_km * 1.75))
        return (
            FlightSegment(
                airline="Road connector",
                departure_airport=origin,
                arrival_airport=destination,
                departure_at=departure_at,
                arrival_at=arrival_at,
                duration_minutes=duration_minutes,
                mode="bus",
                service_name="Road connector",
                departure_name=origin,
                arrival_name=destination,
                data_quality="estimated",
                data_source="OpenStreetMap + OSRM",
                distance_km=round(distance_km, 1),
            ),
            fare,
        )

    async def route_metrics(self, origin: str, destination: str) -> tuple[float, int]:
        cache_key = f"{_normalized_place(origin)}|{_normalized_place(destination)}"
        if cache_key in self._route_cache:
            return self._route_cache[cache_key]
        origin_coords, destination_coords = await asyncio.gather(
            self.places.geocode(f"{origin}, India"),
            self.places.geocode(f"{destination}, India"),
        )
        if not origin_coords or not destination_coords:
            raise NoResultsError(f"Could not map the road route from {origin} to {destination}")
        origin_lat, origin_lon = origin_coords
        destination_lat, destination_lon = destination_coords
        response = await self.client.get(
            (
                f"{self.base_url}/route/v1/driving/"
                f"{origin_lon},{origin_lat};{destination_lon},{destination_lat}"
            ),
            params={"overview": "false", "steps": "false"},
        )
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise TemporaryToolError(f"OSRM road routing returned {response.status_code}")
        response.raise_for_status()
        routes = response.json().get("routes") or []
        if not routes:
            raise NoResultsError(f"No drivable route was found from {origin} to {destination}")
        distance_km = float(routes[0]["distance"]) / 1000
        driving_minutes = max(1, math.ceil(float(routes[0]["duration"]) / 60))
        # Scheduled coaches are slower than a private car and take rest stops.
        bus_minutes = math.ceil(driving_minutes * 1.22)
        bus_minutes += max(0, bus_minutes // 240) * 20
        result = (distance_km, bus_minutes)
        self._route_cache[cache_key] = result
        return result


class RailRadarProvider:
    name = "railradar"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        road: OpenStreetMapRoadProvider,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.road = road
        self.client = httpx.AsyncClient(
            timeout=35,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self._stations: dict[str, str] | None = None

    async def search_leg(
        self,
        constraints: TravelConstraints,
        leg: str,
    ) -> list[FlightLegOption]:
        is_return = leg == "return"
        origin_city = constraints.destination if is_return else constraints.origin
        destination_city = constraints.origin if is_return else constraints.destination
        travel_date = constraints.end_date if is_return else constraints.start_date
        if not origin_city or not destination_city or not travel_date:
            raise InvalidToolArguments("Cities and date are required for railway search")
        stations = await self._station_lookup()
        origin_candidates = (
            constraints.destination_station_codes
            if is_return
            else constraints.origin_station_codes
        )
        destination_candidates = (
            constraints.origin_station_codes
            if is_return
            else constraints.destination_station_codes
        )
        origin = self._resolve_endpoint(origin_city, stations, origin_candidates)
        destination = self._resolve_endpoint(
            destination_city,
            stations,
            destination_candidates,
        )
        if not origin or not destination:
            unresolved = origin_city if not origin else destination_city
            raise NoResultsError(f"RailRadar could not resolve a railhead for {unresolved}")

        response = await self.client.get(
            f"{self.base_url}/v1/trains/between/{origin.code}/{destination.code}",
            params={
                "date": travel_date.isoformat(),
                "live": "false",
                "byCity": "true",
            },
        )
        if response.status_code == 404:
            raise NoResultsError(
                f"RailRadar found no train from {origin.name} to {destination.name} "
                f"on {travel_date.isoformat()}"
            )
        body = self._response_body(response, "trains-between-stations")
        data = body.get("data") or {}
        results: list[FlightLegOption] = []
        for item in (data.get("trains") or [])[:10]:
            normalized = await self._normalize_train(
                item,
                origin,
                destination,
                travel_date,
                constraints,
                leg,
            )
            if normalized:
                results.append(normalized)
        if not results:
            raise NoResultsError(
                f"RailRadar returned no usable trains for {origin.code} → {destination.code}"
            )
        results.sort(
            key=lambda option: (
                option.departure_at,
                sum(segment.duration_minutes for segment in option.segments),
            )
        )
        return results

    async def _station_lookup(self) -> dict[str, str]:
        if self._stations is not None:
            return self._stations
        response = await self.client.get(f"{self.base_url}/v1/lookup/stations")
        body = self._response_body(response, "station lookup")
        stations = body.get("data")
        if not isinstance(stations, dict):
            raise ToolError("RailRadar returned an invalid station lookup")
        self._stations = {
            str(code).upper(): str(name)
            for code, name in stations.items()
            if code and name
        }
        return self._stations

    def _resolve_endpoint(
        self,
        city: str,
        stations: dict[str, str],
        candidate_codes: list[str] | None = None,
    ) -> RailEndpoint | None:
        normalized = _normalized_place(city)
        for code in candidate_codes or []:
            normalized_code = code.upper()
            if normalized_code in stations:
                return RailEndpoint(
                    code=normalized_code,
                    name=stations[normalized_code],
                    requested_city=city,
                    road_connector_required=normalized in RAIL_GATEWAYS,
                )
        if normalized in RAIL_GATEWAYS:
            code, gateway = RAIL_GATEWAYS[normalized]
            return RailEndpoint(
                code=code,
                name=stations.get(code, gateway),
                requested_city=city,
                road_connector_required=True,
            )
        aliases = STATION_ALIASES.get(normalized, [])
        for code in aliases:
            if code in stations:
                return RailEndpoint(
                    code=code,
                    name=stations[code],
                    requested_city=city,
                )
        scored: list[tuple[float, str, str]] = []
        for code, name in stations.items():
            normalized_name = _normalized_place(name)
            contains = normalized in normalized_name or normalized_name.startswith(normalized)
            score = difflib.SequenceMatcher(None, normalized, normalized_name).ratio()
            if contains:
                score += 0.45
            if score >= 0.72:
                scored.append((score, code, name))
        if not scored:
            return None
        _, code, name = max(scored)
        return RailEndpoint(code=code, name=name, requested_city=city)

    async def validate_station_candidates(
        self,
        city: str,
        candidate_codes: list[str],
    ) -> list[str]:
        stations = await self._station_lookup()
        normalized_city = _normalized_place(city)
        known_codes = set(STATION_ALIASES.get(normalized_city, []))
        if normalized_city in RAIL_GATEWAYS:
            known_codes.add(RAIL_GATEWAYS[normalized_city][0])
        airport_codes = set(AIRPORTS.values())
        accepted: list[str] = []
        for raw_code in candidate_codes[:4]:
            code = raw_code.strip().upper()
            station_name = stations.get(code)
            if not station_name or code in airport_codes:
                continue
            normalized_name = _normalized_place(station_name)
            semantic_match = (
                normalized_city in normalized_name
                or normalized_name.startswith(normalized_city)
                or difflib.SequenceMatcher(
                    None,
                    normalized_city,
                    normalized_name,
                ).ratio()
                >= 0.72
            )
            if code in known_codes or semantic_match:
                accepted.append(code)
        return list(dict.fromkeys(accepted))

    async def _normalize_train(
        self,
        item: dict[str, Any],
        origin: RailEndpoint,
        destination: RailEndpoint,
        travel_date: Any,
        constraints: TravelConstraints,
        leg: str,
    ) -> FlightLegOption | None:
        train = item.get("train") or {}
        source = item.get("from") or {}
        target = item.get("to") or {}
        number = str(train.get("number") or "")
        name = str(train.get("name") or "Indian Railways train")
        departure_clock = source.get("departure")
        arrival_clock = target.get("arrival")
        if not number or not departure_clock or not arrival_clock:
            return None
        try:
            source_day = int(source.get("day") or 1)
            target_day = int(target.get("day") or source_day)
            departure_at = self._service_datetime(
                travel_date,
                str(departure_clock),
                1,
            )
            arrival_at = self._service_datetime(
                travel_date,
                str(arrival_clock),
                1 + max(0, target_day - source_day),
            )
        except (TypeError, ValueError):
            return None
        while arrival_at <= departure_at:
            arrival_at += timedelta(days=1)
        duration = int(
            item.get("duration")
            or max(1, (arrival_at - departure_at).total_seconds() // 60)
        )
        distance = float(item.get("distance") or 0)
        travellers = (constraints.adults or 1) + constraints.children
        rail_fare = self._estimated_fare(
            distance,
            str(train.get("type") or ""),
            number,
        ) * travellers
        segments: list[FlightSegment] = []
        connector_price = 0
        if origin.road_connector_required:
            connector, price = await self.road.connector_segment(
                origin.requested_city,
                origin.name,
                arrive_by=departure_at - timedelta(minutes=45),
            )
            segments.append(connector)
            connector_price += price * travellers
        segments.append(
            FlightSegment(
                airline=name,
                flight_number=number,
                departure_airport=origin.code,
                arrival_airport=destination.code,
                departure_at=departure_at,
                arrival_at=arrival_at,
                duration_minutes=duration,
                mode="train",
                service_name=name,
                departure_name=origin.name,
                arrival_name=destination.name,
                data_quality="scheduled",
                data_source="RailRadar",
                distance_km=distance or None,
            )
        )
        if destination.road_connector_required:
            connector, price = await self.road.connector_segment(
                destination.name,
                destination.requested_city,
                depart_at=arrival_at + timedelta(minutes=30),
            )
            segments.append(connector)
            connector_price += price * travellers
        modes = {segment.mode for segment in segments}
        return FlightLegOption(
            id=f"railradar-{leg}-{number}-{origin.code}-{destination.code}",
            provider=self.name,
            leg="return" if leg == "return" else "outbound",
            segments=segments,
            total_price=math.ceil(rail_fare + connector_price),
            stops=max(0, len(segments) - 1),
            intermediate_stops=int(item.get("totalHaltsBetween") or 0),
            baggage="Confirm luggage rules for each selected service",
            booking_url=f"https://railradar.in/train-status/{number}",
            route_type="multimodal" if len(modes) > 1 else "direct",
            fare_is_estimate=True,
            schedule_is_live=False,
            source_note=(
                "Train schedule from RailRadar. Fare is an estimate because "
                "RailRadar does not provide ticket price or seat availability."
            ),
        )

    @staticmethod
    def _service_datetime(travel_date: Any, clock: str, service_day: int) -> datetime:
        hour, minute = (int(part) for part in clock.split(":", 1))
        return datetime.combine(
            travel_date + timedelta(days=max(0, service_day - 1)),
            time(hour=hour, minute=minute),
            tzinfo=IST,
        )

    @staticmethod
    def _estimated_fare(distance_km: float, train_type: str, number: str) -> int:
        normalized_type = train_type.casefold()
        if any(value in normalized_type for value in ("rajdhani", "vande", "shatabdi")):
            rate = 1.05
        elif any(value in normalized_type for value in ("superfast", "express", "duronto")):
            rate = 0.72
        else:
            rate = 0.48
        variance = 1 + ((int(number[-2:]) if number[-2:].isdigit() else 0) % 5) * 0.035
        return math.ceil(max(150, distance_km * rate * variance + 70))

    @staticmethod
    def _response_body(response: httpx.Response, operation: str) -> dict[str, Any]:
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise TemporaryToolError(
                f"RailRadar {operation} returned {response.status_code}"
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ToolError(
                f"RailRadar {operation} failed: {response.status_code}"
            ) from error
        body = response.json()
        if not body.get("success", True):
            error = body.get("error") or {}
            raise ToolError(str(error.get("message") or "RailRadar request failed"))
        return body


class TravelToolRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.demo = DemoTravelProvider()
        self.places = OpenStreetMapPlacesProvider(settings)
        self.road = OpenStreetMapRoadProvider(settings, self.places)
        self.rail = (
            RailRadarProvider(
                settings.railradar_api_key,
                settings.railradar_base_url,
                self.road,
            )
            if settings.railradar_api_key
            else None
        )
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
        if settings.travel_provider_mode == "demo" or (
            not settings.production and not self.providers
        ):
            self.providers.append(self.demo)

    def provider_names(self) -> list[str]:
        names = [provider.name for provider in self.providers]
        if self.rail:
            names.append(self.rail.name)
        names.append(self.road.name)
        return names

    async def close(self) -> None:
        clients = [provider.client for provider in self.providers if hasattr(provider, "client")]
        clients.extend([self.places.client, self.road.client])
        if self.rail:
            clients.append(self.rail.client)
        unique_clients = list({id(client): client for client in clients}.values())
        await asyncio.gather(
            *(client.aclose() for client in unique_clients),
            return_exceptions=True,
        )

    async def resolve_locations(
        self,
        constraints: TravelConstraints,
    ) -> tuple[str | None, str | None]:
        origin = constraints.origin_airport or AIRPORTS.get((constraints.origin or "").lower())
        destination = constraints.destination_airport or AIRPORTS.get(
            (constraints.destination or "").lower()
        )
        return origin, destination

    async def search_fallback_journeys(
        self,
        constraints: TravelConstraints,
        leg: str,
    ) -> list[FlightLegOption]:
        results: list[FlightLegOption] = []
        errors: list[str] = []
        if self.rail:
            try:
                results.extend(await self.rail.search_leg(constraints, leg))
            except (ToolError, httpx.HTTPError) as error:
                errors.append(str(error))
        try:
            results.extend(await self.road.search_leg(constraints, leg))
        except (ToolError, httpx.HTTPError) as error:
            errors.append(str(error))
        if not results:
            raise NoResultsError(
                "; ".join(errors)
                or "No flight, railway, or road journey could be built safely"
            )
        results.sort(
            key=lambda option: (
                option.fare_is_estimate,
                option.total_price,
                sum(segment.duration_minutes for segment in option.segments),
            )
        )
        return results

    async def enrich_hotel_distances(
        self,
        hotels: list[HotelOption],
        constraints: TravelConstraints,
    ) -> list[HotelOption]:
        return await self.places.enrich_hotel_distances(hotels, constraints)


def combine_flight_legs(
    outbound: FlightLegOption,
    inbound: FlightLegOption,
) -> FlightOption:
    return FlightOption(
        id=f"{outbound.id}:{inbound.id}",
        provider=(
            outbound.provider
            if outbound.provider == inbound.provider
            else f"{outbound.provider}+{inbound.provider}"
        ),
        outbound=outbound.segments,
        inbound=inbound.segments,
        total_price=outbound.total_price + inbound.total_price,
        stops=outbound.stops + inbound.stops,
        baggage=(
            outbound.baggage
            if outbound.baggage == inbound.baggage
            else "Check each selected leg's fare rules"
        ),
        route_type=(
            "multimodal"
            if (
                outbound.route_type == "multimodal"
                or inbound.route_type == "multimodal"
                or len(
                    {
                        segment.mode
                        for segment in [*outbound.segments, *inbound.segments]
                    }
                )
                > 1
            )
            else (
                "connected"
                if outbound.route_type == "connected"
                or inbound.route_type == "connected"
                else "direct"
            )
        ),
        fare_is_estimate=outbound.fare_is_estimate or inbound.fare_is_estimate,
        schedule_is_live=outbound.schedule_is_live and inbound.schedule_is_live,
        source_note=" · ".join(
            dict.fromkeys(
                note
                for note in (outbound.source_note, inbound.source_note)
                if note
            )
        )
        or None,
        intermediate_stops=outbound.intermediate_stops + inbound.intermediate_stops,
    )


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

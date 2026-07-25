from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from typing import Any

from dateutil import parser as date_parser
from openai import AsyncOpenAI

from app.config import Settings
from app.models import TravelConstraints

AIRPORTS: dict[str, str] = {
    "ahmedabad": "AMD",
    "bengaluru": "BLR",
    "bangalore": "BLR",
    "chandigarh": "IXC",
    "chennai": "MAA",
    "delhi": "DEL",
    "new delhi": "DEL",
    "goa": "GOI",
    "dabolim": "GOI",
    "hyderabad": "HYD",
    "jaipur": "JAI",
    "kochi": "COK",
    "cochin": "COK",
    "kolkata": "CCU",
    "calcutta": "CCU",
    "lucknow": "LKO",
    "mumbai": "BOM",
    "pune": "PNQ",
    "srinagar": "SXR",
    "varanasi": "VNS",
}

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
}


class RequestInterpreter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = (
            AsyncOpenAI(api_key=settings.sarvam_api_key, base_url="https://api.sarvam.ai/v1")
            if settings.sarvam_api_key
            else None
        )

    async def interpret(
        self,
        text: str,
        current: TravelConstraints | None = None,
        *,
        today: date | None = None,
    ) -> TravelConstraints:
        reference_day = today or date.today()
        deterministic = self._deterministic(text, current, reference_day)
        if current and self._is_constraint_adjustment(text):
            return deterministic
        if not self.client:
            return deterministic
        try:
            prompt = {
                "today": reference_day.isoformat(),
                "existing_constraints": (current.model_dump(mode="json") if current else {}),
                "new_user_message": text,
                "rules": [
                    "Only infer dates from unambiguous relative expressions.",
                    "Do not invent an origin, destination, budget, or exact dates.",
                    "All prices are total trip budgets in INR.",
                    "Keep previously known values unless the new message changes them.",
                    "missing_fields must list required fields that remain unknown.",
                ],
            }
            response = await self.client.chat.completions.create(
                model=self.settings.sarvam_model,
                temperature=0,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract travel constraints. Return only data matching the provided "
                            "JSON schema. Never include chain-of-thought."
                        ),
                    },
                    {"role": "user", "content": json.dumps(prompt)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "travel_constraints",
                        "strict": True,
                        "schema": TravelConstraints.model_json_schema(),
                    },
                },
            )
            content = response.choices[0].message.content
            parsed = TravelConstraints.model_validate_json(content or "{}")
            return self._finalize(parsed)
        except Exception:
            return deterministic

    def _deterministic(
        self, text: str, current: TravelConstraints | None, today: date
    ) -> TravelConstraints:
        values: dict[str, Any] = (
            current.model_dump(mode="python", exclude={"missing_fields", "inferred_fields"})
            if current
            else {}
        )
        inferred: list[str] = []
        cleaned = " ".join(text.strip().split())
        lower = cleaned.lower()

        route_patterns = [
            (
                r"\bfrom\s+([a-zA-Z ]+?)\s+to\s+([a-zA-Z ]+?)"
                r"(?=\s+(?:for|under|on|next|this|with|and)\b|[,.]|$)"
            ),
            (
                r"\bto\s+([a-zA-Z ]+?)\s+from\s+([a-zA-Z ]+?)"
                r"(?=\s+(?:for|under|on|next|this|with|and)\b|[,.]|$)"
            ),
        ]
        first = re.search(route_patterns[0], cleaned, flags=re.IGNORECASE)
        second = re.search(route_patterns[1], cleaned, flags=re.IGNORECASE)
        if first:
            values["origin"] = self._clean_place(first.group(1))
            values["destination"] = self._clean_place(first.group(2))
        elif second:
            values["destination"] = self._clean_place(second.group(1))
            values["origin"] = self._clean_place(second.group(2))
        else:
            destination_only = re.search(
                r"\b(?:trip|travel|go|going)\s+to\s+([a-zA-Z ]+?)"
                r"(?=\s+(?:for|under|on|next|this|with|and|from)\b|[,.]|$)",
                cleaned,
                flags=re.IGNORECASE,
            )
            if destination_only:
                values["destination"] = self._clean_place(destination_only.group(1))

        budget_increase = re.search(
            r"increase\s+(?:the\s+)?budget\s+by\s*(?:₹|rs\.?|inr)?\s*([\d,]+)",
            lower,
        )
        if budget_increase:
            increment = int(budget_increase.group(1).replace(",", ""))
            values["budget"] = int(values.get("budget") or 0) + increment
        else:
            budget_match = re.search(
                r"(?:under|below|budget(?:\s+of|\s+to)?|within|for)"
                r"\s*(?:₹|rs\.?|inr)?\s*([\d,]+)",
                lower,
            ) or re.search(r"(?:₹|rs\.?|inr)\s*([\d,]+)", lower)
            if budget_match:
                values["budget"] = int(budget_match.group(1).replace(",", ""))

        duration_match = re.search(r"\b(\d+)\s*[- ]?\s*day", lower)
        if duration_match:
            values["duration_days"] = int(duration_match.group(1))
        else:
            for word, number in NUMBER_WORDS.items():
                if re.search(rf"\b{word}\s*[- ]?\s*day", lower):
                    values["duration_days"] = number
                    break

        people_match = re.search(
            r"\b(\d+)\s+(?:people|persons|adults|travellers|travelers)\b", lower
        )
        if people_match:
            values["adults"] = int(people_match.group(1))
        else:
            for word, number in NUMBER_WORDS.items():
                if re.search(rf"\bfor\s+{word}\s+(?:people|adults|travellers|travelers)\b", lower):
                    values["adults"] = number
                    break

        time_match = re.search(
            r"(?:avoid|no)\s+flights?\s+before\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
            lower,
        )
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            if time_match.group(3) == "pm" and hour < 12:
                hour += 12
            values["earliest_departure"] = time(hour=hour % 24, minute=minute)
        elif "allow earlier flights" in lower:
            values["earliest_departure"] = None

        if "near the beach" in lower or "near beach" in lower:
            values["hotel_area_preference"] = "beach"
            values["max_hotel_distance_km"] = values.get("max_hotel_distance_km") or 2.0
        elif "city centre" in lower or "city center" in lower:
            values["hotel_area_preference"] = "city centre"
            values["max_hotel_distance_km"] = values.get("max_hotel_distance_km") or 3.0
        elif (
            "hotel" in lower
            and ("farther away" in lower or "further away" in lower)
        ):
            values["max_hotel_distance_km"] = float(
                values.get("max_hotel_distance_km") or 3.0
            ) + 2.0

        relative = self._relative_dates(lower, today, values.get("duration_days"))
        if relative:
            values["start_date"], values["end_date"] = relative
            inferred.extend(["start_date", "end_date"])
        else:
            exact_dates = re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", lower)
            if len(exact_dates) >= 2:
                values["start_date"] = date.fromisoformat(exact_dates[0])
                values["end_date"] = date.fromisoformat(exact_dates[1])
            elif len(exact_dates) == 1 and values.get("duration_days"):
                start = date.fromisoformat(exact_dates[0])
                values["start_date"] = start
                values["end_date"] = start + timedelta(days=int(values["duration_days"]) - 1)
                inferred.append("end_date")
            else:
                readable_dates = re.findall(
                    r"\b(?:\d{1,2}(?:st|nd|rd|th)?\s+"
                    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
                    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
                    r"nov(?:ember)?|dec(?:ember)?)(?:\s+20\d{2})?)\b",
                    lower,
                )
                if readable_dates:
                    start = date_parser.parse(
                        readable_dates[0], default=datetime(today.year, 1, 1)
                    ).date()
                    if start < today:
                        start = start.replace(year=start.year + 1)
                    values["start_date"] = start
                    if values.get("duration_days"):
                        values["end_date"] = start + timedelta(
                            days=int(values["duration_days"]) - 1
                        )
                        inferred.append("end_date")

        preferences = list(values.get("preferences") or [])
        for phrase in [
            "nonstop flights",
            "window seat",
            "breakfast included",
            "quiet hotel",
            "nightlife",
            "local food",
        ]:
            if phrase in lower and phrase not in preferences:
                preferences.append(phrase)
        values["preferences"] = preferences

        if values.get("origin"):
            values["origin_airport"] = AIRPORTS.get(str(values["origin"]).lower())
        if values.get("destination"):
            values["destination_airport"] = AIRPORTS.get(str(values["destination"]).lower())
        values["inferred_fields"] = inferred
        return self._finalize(TravelConstraints.model_validate(values))

    def _relative_dates(
        self, text: str, today: date, duration_days: int | None
    ) -> tuple[date, date] | None:
        if "next weekend" in text:
            days_until_friday = (4 - today.weekday()) % 7
            if days_until_friday == 0:
                days_until_friday = 7
            start = today + timedelta(days=days_until_friday)
            days = duration_days or 3
            return start, start + timedelta(days=days - 1)
        if "this weekend" in text:
            days_until_friday = (4 - today.weekday()) % 7
            start = today + timedelta(days=days_until_friday)
            days = duration_days or 3
            return start, start + timedelta(days=days - 1)
        if "tomorrow" in text:
            start = today + timedelta(days=1)
            days = duration_days or 1
            return start, start + timedelta(days=days - 1)
        return None

    def _finalize(self, constraints: TravelConstraints) -> TravelConstraints:
        constraints.origin_airport = constraints.origin_airport or AIRPORTS.get(
            (constraints.origin or "").lower()
        )
        constraints.destination_airport = constraints.destination_airport or AIRPORTS.get(
            (constraints.destination or "").lower()
        )
        constraints.missing_fields = constraints.required_missing()
        if constraints.origin and not constraints.origin_airport:
            constraints.missing_fields.append("origin_airport")
        if constraints.destination and not constraints.destination_airport:
            constraints.missing_fields.append("destination_airport")
        return constraints

    @staticmethod
    def _clean_place(value: str) -> str:
        return value.strip(" ,.-").title()

    @staticmethod
    def _is_constraint_adjustment(text: str) -> bool:
        lower = text.lower()
        return any(
            phrase in lower
            for phrase in (
                "increase the budget by",
                "increase budget by",
                "allow earlier flights",
                "farther away",
                "further away",
            )
        )

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from dateutil import parser as date_parser

from app.agent_model import (
    ModelMetrics,
    SarvamAgent,
    SarvamGateway,
    SarvamModelError,
    TurnInterpretation,
)
from app.config import Settings
from app.models import TravelConstraints, VisualTheme

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

VISUAL_THEME_DESTINATIONS: dict[str, set[str]] = {
    "coast": {
        "alibaug",
        "andaman",
        "bali",
        "chennai",
        "daman",
        "goa",
        "gokarna",
        "kovalam",
        "lakshadweep",
        "mahabalipuram",
        "maldives",
        "mangalore",
        "mumbai",
        "pondicherry",
        "puducherry",
        "santorini",
        "varkala",
        "visakhapatnam",
    },
    "mountains": {
        "auli",
        "darjeeling",
        "dharamshala",
        "gangtok",
        "gulmarg",
        "kashmir",
        "ladakh",
        "leh",
        "manali",
        "mcleodganj",
        "mussoorie",
        "nainital",
        "shimla",
        "srinagar",
        "switzerland",
    },
    "heritage": {
        "agra",
        "ajmer",
        "amritsar",
        "hampi",
        "jaipur",
        "jaisalmer",
        "jodhpur",
        "khajuraho",
        "kyoto",
        "lucknow",
        "mysore",
        "udaipur",
        "varanasi",
    },
    "nature": {
        "alleppey",
        "assam",
        "cherrapunji",
        "coorg",
        "kerala",
        "kochi",
        "kumarakom",
        "meghalaya",
        "munnar",
        "ooty",
        "thekkady",
        "wayanad",
    },
    "city": {
        "ahmedabad",
        "bangalore",
        "bengaluru",
        "delhi",
        "dubai",
        "hyderabad",
        "kolkata",
        "london",
        "new delhi",
        "paris",
        "pune",
        "singapore",
        "tokyo",
    },
}


def visual_theme_for_destination(destination: str | None) -> VisualTheme | None:
    normalized = " ".join((destination or "").lower().split())
    if not normalized:
        return None
    for theme, destinations in VISUAL_THEME_DESTINATIONS.items():
        if any(
            normalized == candidate or candidate in normalized or normalized in candidate
            for candidate in destinations
        ):
            return theme
    return "city"


@dataclass(frozen=True)
class InterpretationOutcome:
    constraints: TravelConstraints
    assistant_message: str
    quick_replies: list[str]
    assumptions: list[str]
    model_metrics: ModelMetrics | None = None


class RequestInterpreter:
    def __init__(
        self,
        settings: Settings,
        gateway: SarvamGateway | None = None,
    ) -> None:
        self.settings = settings
        self.gateway = gateway or SarvamGateway(settings)
        self.agent = SarvamAgent(self.gateway)

    @property
    def has_model(self) -> bool:
        return self.gateway.enabled

    async def interpret(
        self,
        text: str,
        current: TravelConstraints | None = None,
        *,
        today: date | None = None,
    ) -> TravelConstraints:
        outcome = await self.interpret_turn(text, current, today=today)
        return outcome.constraints

    async def interpret_turn(
        self,
        text: str,
        current: TravelConstraints | None = None,
        *,
        today: date | None = None,
        preferences: dict[str, Any] | None = None,
        recent_messages: list[dict[str, str]] | None = None,
    ) -> InterpretationOutcome:
        reference_day = today or date.today()
        deterministic = self._deterministic(text, current, reference_day)
        assumptions = self._default_assumptions(text, current, deterministic)
        if not self.has_model:
            return InterpretationOutcome(
                constraints=deterministic,
                assistant_message=self._fallback_message(deterministic),
                quick_replies=self._fallback_quick_replies(deterministic),
                assumptions=assumptions,
            )

        try:
            result = await self.agent.interpret(
                today=reference_day,
                user_message=text,
                existing_constraints=current.model_dump(mode="json") if current else {},
                preferences=preferences or {},
                recent_messages=recent_messages or [],
            )
        except SarvamModelError as error:
            # The deterministic pass has already extracted safe route/date
            # facts. A malformed or temporarily unavailable model response
            # should be observable, but must not block the traveller.
            return InterpretationOutcome(
                constraints=deterministic,
                assistant_message=self._fallback_message(deterministic),
                quick_replies=self._fallback_quick_replies(deterministic),
                assumptions=assumptions,
                model_metrics=error.metrics,
            )
        merged = self._merge_model_constraints(
            text=text,
            current=current,
            deterministic=deterministic,
            model=result.value,
        )
        model_assumptions = result.value.assumptions
        if merged.adults is None:
            model_assumptions = [
                assumption
                for assumption in model_assumptions
                if not re.search(
                    r"\b(?:adults?|travellers?|travelers?|people|persons?)\b",
                    assumption,
                    flags=re.IGNORECASE,
                )
            ]
        combined_assumptions = list(dict.fromkeys([*assumptions, *model_assumptions]))
        quick_replies = result.value.quick_replies if merged.missing_fields else []
        if merged.missing_fields and not quick_replies:
            quick_replies = self._fallback_quick_replies(merged)
        return InterpretationOutcome(
            constraints=merged,
            assistant_message=(
                result.value.assistant_message.strip() or self._fallback_message(merged)
            ),
            quick_replies=quick_replies[:4],
            assumptions=combined_assumptions[:8],
            model_metrics=result.metrics,
        )

    def _deterministic(
        self, text: str, current: TravelConstraints | None, today: date
    ) -> TravelConstraints:
        previous_destination = current.destination if current else None
        values: dict[str, Any] = (
            current.model_dump(mode="python", exclude={"missing_fields", "inferred_fields"})
            if current
            else {}
        )
        inferred: list[str] = []
        cleaned = " ".join(text.strip().split())
        lower = cleaned.lower()

        if current:
            missing = set(current.missing_fields or current.required_missing())
            direct_place = next(
                (place for place in sorted(AIRPORTS, key=len, reverse=True) if lower == place),
                None,
            )
            if direct_place:
                if "origin" in missing:
                    values["origin"] = self._clean_place(direct_place)
                elif "destination" in missing:
                    values["destination"] = self._clean_place(direct_place)

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

        if not values.get("origin"):
            origin_only = re.search(
                r"\bfrom\s+([a-zA-Z ]+?)"
                r"(?=\s+(?:for|under|below|on|next|this|with|and)\b|[,.]|$)",
                cleaned,
                flags=re.IGNORECASE,
            )
            if origin_only:
                values["origin"] = self._clean_place(origin_only.group(1))

        if not values.get("destination"):
            destination_before_trip = next(
                (
                    place
                    for place in sorted(AIRPORTS, key=len, reverse=True)
                    if re.search(rf"\b{re.escape(place)}\s+trip\b", lower)
                ),
                None,
            )
            if destination_before_trip:
                values["destination"] = self._clean_place(destination_before_trip)

        budget_increase = re.search(
            r"increase\s+(?:the\s+)?budget\s+by\s*(?:₹|rs\.?|inr)?\s*([\d,]+)",
            lower,
        )
        if budget_increase:
            increment = int(budget_increase.group(1).replace(",", ""))
            updated_budget = int(values.get("budget") or 0) + increment
            if updated_budget >= 1000:
                values["budget"] = updated_budget
        else:
            extracted_budget = self._extract_budget(lower)
            if extracted_budget is not None:
                values["budget"] = extracted_budget

        duration_match = re.search(r"\b(\d+)\s*[- ]?\s*day", lower)
        if duration_match:
            duration_days = int(duration_match.group(1))
            if 1 <= duration_days <= 30:
                values["duration_days"] = duration_days
        else:
            for word, number in NUMBER_WORDS.items():
                if re.search(rf"\b{word}\s*[- ]?\s*day", lower):
                    values["duration_days"] = number
                    break

        explicit_adults = re.search(r"\b(\d+)\s+adults?\b", lower)
        explicit_children = re.search(r"\b(\d+)\s+(?:children|child|kids?)\b", lower)
        people_match = re.search(r"\b(\d+)\s+(?:people|persons?|travellers?|travelers?)\b", lower)
        if explicit_adults:
            adult_count = int(explicit_adults.group(1))
            if 1 <= adult_count <= 9:
                values["adults"] = adult_count
        elif people_match:
            adult_count = int(people_match.group(1))
            if 1 <= adult_count <= 9:
                values["adults"] = adult_count
        else:
            for word, number in NUMBER_WORDS.items():
                if re.search(
                    rf"\b(?:for\s+)?{word}\s+"
                    r"(?:people|persons?|adults?|travellers?|travelers?)\b",
                    lower,
                ):
                    values["adults"] = number
                    break
        if (
            values.get("adults") is None
            and current
            and "adults" in (current.missing_fields or current.required_missing())
        ):
            contextual_count = re.fullmatch(
                r"\s*(?:we(?:'re| are)\s+|there are\s+|for\s+)?"
                r"(\d+|one|two|three|four|five|six|seven|eight|nine)"
                r"(?:\s+(?:of us|adults?|people|persons?|travellers?|travelers?))?"
                r"\s*[.!]?\s*",
                lower,
            )
            if contextual_count:
                raw_count = contextual_count.group(1)
                adult_count = int(raw_count) if raw_count.isdigit() else NUMBER_WORDS[raw_count]
                if 1 <= adult_count <= 9:
                    values["adults"] = adult_count
        if explicit_children:
            child_count = int(explicit_children.group(1))
            if 0 <= child_count <= 8:
                values["children"] = child_count
        else:
            for word, number in NUMBER_WORDS.items():
                if re.search(rf"\b{word}\s+(?:children|child|kids?)\b", lower):
                    values["children"] = number
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
        elif "hotel" in lower and ("farther away" in lower or "further away" in lower):
            values["max_hotel_distance_km"] = (
                float(values.get("max_hotel_distance_km") or 3.0) + 2.0
            )

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
            if (
                not previous_destination
                or str(values["destination"]).casefold() != previous_destination.casefold()
            ):
                values["visual_theme"] = visual_theme_for_destination(str(values["destination"]))
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
        # Airport identifiers are execution inputs, so only the controlled
        # registry may supply them. Model-suggested codes never bypass lookup.
        constraints.origin_airport = AIRPORTS.get((constraints.origin or "").lower())
        constraints.destination_airport = AIRPORTS.get((constraints.destination or "").lower())
        constraints.visual_theme = constraints.visual_theme or visual_theme_for_destination(
            constraints.destination
        )
        constraints.missing_fields = constraints.required_missing()
        return constraints

    def _merge_model_constraints(
        self,
        *,
        text: str,
        current: TravelConstraints | None,
        deterministic: TravelConstraints,
        model: TurnInterpretation,
    ) -> TravelConstraints:
        values: dict[str, Any] = (
            current.model_dump(mode="python", exclude={"missing_fields", "inferred_fields"})
            if current
            else {}
        )
        patch = model.constraints.model_dump(mode="python", exclude_none=True)
        date_fields = {"start_date", "end_date"}
        deterministic_dates_available = bool(deterministic.start_date and deterministic.end_date)
        for field, value in patch.items():
            if field in date_fields and not (
                deterministic_dates_available
                or (current and getattr(current, field))
                or self._contains_explicit_date(text)
            ):
                continue
            if field == "adults" and not (
                (current and current.adults is not None) or self._contains_explicit_travellers(text)
            ):
                continue
            if field == "children" and not self._contains_explicit_travellers(text):
                continue
            values[field] = value

        deterministic_values = deterministic.model_dump(
            mode="python",
            exclude={"missing_fields", "inferred_fields"},
        )
        for field, value in deterministic_values.items():
            current_value = getattr(current, field, None) if current else None
            if (current is None and value is not None) or (
                current is not None and current_value != value
            ):
                values[field] = value

        model_preferences = list(patch.get("preferences") or [])
        deterministic_preferences = list(deterministic.preferences)
        values["preferences"] = list(
            dict.fromkeys([*deterministic_preferences, *model_preferences])
        )
        destination = values.get("destination")
        destination_changed = bool(
            destination
            and (
                not current
                or not current.destination
                or str(destination).casefold() != current.destination.casefold()
            )
        )
        if patch.get("visual_theme"):
            # Sarvam's strict theme classification takes precedence over the
            # conservative local fallback used when the model is unavailable.
            values["visual_theme"] = patch["visual_theme"]
        elif destination_changed:
            values["visual_theme"] = visual_theme_for_destination(str(destination))
        values["inferred_fields"] = list(
            dict.fromkeys(
                [
                    *deterministic.inferred_fields,
                    *model.inferred_fields,
                ]
            )
        )
        return self._finalize(TravelConstraints.model_validate(values))

    @staticmethod
    def _contains_explicit_date(text: str) -> bool:
        lower = text.lower()
        return bool(
            re.search(r"\b20\d{2}-\d{2}-\d{2}\b", lower)
            or re.search(
                r"\b\d{1,2}(?:st|nd|rd|th)?\s+"
                r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
                r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
                r"nov(?:ember)?|dec(?:ember)?)\b",
                lower,
            )
        )

    @staticmethod
    def _contains_explicit_travellers(text: str) -> bool:
        return bool(
            re.search(
                r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine)\s+"
                r"(?:people|persons?|adults?|children|child|kids?|travellers?|travelers?)\b",
                text.lower(),
            )
        )

    @staticmethod
    def _extract_budget(text: str) -> int | None:
        amount = (
            r"(?P<amount>\d[\d,]*(?:\.\d+)?)"
            r"\s*(?P<scale>k|thousand|lakh|lac)?"
        )
        patterns = (
            (
                r"(?:^|\b)(?:under|below|up\s+to|"
                r"within(?:\s+a)?(?:\s+budget)?(?:\s+of)?|"
                r"budget(?:\s+(?:of|to|is))?)"
                r"\s*[:=\-]?\s*(?:₹|rs\.?|inr)?\s*" + amount + r"\b"
            ),
            r"(?:₹|rs\.?|inr)\s*" + amount + r"\b",
        )
        match = next(
            (candidate for pattern in patterns if (candidate := re.search(pattern, text))),
            None,
        )
        if not match:
            return None

        numeric = float(match.group("amount").replace(",", ""))
        multiplier = {
            "k": 1_000,
            "thousand": 1_000,
            "lakh": 100_000,
            "lac": 100_000,
        }.get(match.group("scale") or "", 1)
        budget = int(numeric * multiplier)
        return budget if budget >= 1_000 else None

    @staticmethod
    def _fallback_message(constraints: TravelConstraints) -> str:
        return (
            f"I understood a {constraints.duration_days or 'multi-day'} trip from "
            f"{constraints.origin or 'your origin'} to "
            f"{constraints.destination or 'your destination'}."
        )

    @staticmethod
    def _fallback_quick_replies(constraints: TravelConstraints) -> list[str]:
        missing = set(constraints.missing_fields)
        if {"start_date", "end_date"} & missing:
            return ["Next weekend", "This weekend", "I’ll give exact dates"]
        return []

    @staticmethod
    def _default_assumptions(
        text: str,
        current: TravelConstraints | None,
        constraints: TravelConstraints,
    ) -> list[str]:
        lower = text.lower()
        assumptions: list[str] = []
        if "next weekend" in lower and constraints.start_date and constraints.end_date:
            assumptions.append(
                "Interpreted next weekend as "
                f"{constraints.start_date.isoformat()} to {constraints.end_date.isoformat()}"
            )
        return assumptions

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

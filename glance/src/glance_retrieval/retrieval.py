"""Part B: parse natural-language intent, retrieve multiple evidence streams, and rerank."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from .embeddings import EncoderPair
from .schemas import ImageRecord, QueryGarment, QueryIntent, ScoreBreakdown, SearchResult
from .store import VectorHit, VectorStore
from .taxonomy import (
    ACTIVITY_ALIASES,
    CATEGORY_ALIASES,
    COLOR_ALIASES,
    COLORS,
    SCENE_ALIASES,
    STYLE_ALIASES,
    canonical_category,
    canonical_color,
    find_mentions,
)


class IntentParser(Protocol):
    def parse(self, query: str) -> QueryIntent: ...


def _first_mention(text: str, aliases: dict[str, str]) -> str | None:
    mentions = find_mentions(text, aliases)
    return mentions[0][2] if mentions else None


class RuleIntentParser:
    """Transparent fallback parser for the controlled retrieval vocabulary."""

    def parse(self, query: str) -> QueryIntent:
        category_mentions = find_mentions(query, CATEGORY_ALIASES)
        color_aliases = {color: color for color in COLORS} | COLOR_ALIASES
        color_mentions = find_mentions(query, color_aliases)
        garments: list[QueryGarment] = []
        for category_start, _, category in category_mentions:
            nearest_color: str | None = None
            nearest_distance = 10_000
            for color_start, _, color in color_mentions:
                distance = abs(category_start - color_start)
                if distance < nearest_distance and distance <= 42:
                    nearest_color, nearest_distance = canonical_color(color), distance
            garments.append(QueryGarment(category=canonical_category(category), color=nearest_color))
        # A simple spelling of raincoat should additionally expose the broader style axis.
        style = _first_mention(query, STYLE_ALIASES)
        if "raincoat" in query.lower() and not style:
            style = "outerwear"
        return QueryIntent(
            raw_query=query,
            scene=_first_mention(query, SCENE_ALIASES),
            activity=_first_mention(query, ACTIVITY_ALIASES),
            style=style,
            garments=garments,
            free_text=query,
            parser="rules",
        )


class QwenIntentParser:
    """Optional local text-only parser; it always falls back to the transparent rule parser."""

    def __init__(self, model_name: str, fallback: IntentParser | None = None) -> None:
        self.model_name = model_name
        self.fallback = fallback or RuleIntentParser()
        self._pipeline = None

    def _load(self) -> None:  # pragma: no cover - model download/runtime path
        if self._pipeline is None:
            from transformers import pipeline

            self._pipeline = pipeline("text-generation", model=self.model_name, device_map="auto")

    def parse(self, query: str) -> QueryIntent:  # pragma: no cover - optional model runtime path
        prompt = f"""Extract fashion search intent as JSON only. Valid keys: scene, activity, style,
garments (array of {{category,color,attributes}}). Use null or [] when absent.
Query: {query!r}"""
        try:
            self._load()
            response = self._pipeline(prompt, max_new_tokens=180, do_sample=False)[0]["generated_text"]
            body = response[response.find("{") : response.rfind("}") + 1]
            data = json.loads(body)
            intent = QueryIntent(
                raw_query=query,
                scene=data.get("scene"),
                activity=data.get("activity"),
                style=data.get("style"),
                garments=[QueryGarment.model_validate(item) for item in data.get("garments", [])],
                free_text=query,
                parser="qwen",
            )
            return intent
        except Exception:
            return self.fallback.parse(query)


def _unit(score: float) -> float:
    """Qdrant cosine scores in [-1, 1] become comparable [0, 1] components."""

    return max(0.0, min(1.0, (score + 1.0) / 2.0))


@dataclass
class _Candidate:
    generic: float = 0.0
    fashion: float = 0.0
    rrf: float = 0.0
    garment_vectors: dict[int, float] = field(default_factory=dict)


class AttributeAwareRetriever:
    """Late-interaction, evidence-aware retrieval that preserves color–garment bindings."""

    CANDIDATE_LIMIT = 200
    RRF_K = 60

    def __init__(
        self,
        *,
        records: list[ImageRecord],
        store: VectorStore,
        encoders: EncoderPair,
        parser: IntentParser | None = None,
    ) -> None:
        self.records = {record.image_id: record for record in records}
        self.store = store
        self.encoders = encoders
        self.parser = parser or RuleIntentParser()

    @staticmethod
    def _image_id(hit: VectorHit) -> str:
        return str(hit.payload.get("image_id", hit.point_id))

    def _add_image_hits(self, candidates: dict[str, _Candidate], hits: list[VectorHit], field: str) -> None:
        for rank, hit in enumerate(hits, start=1):
            image_id = self._image_id(hit)
            if image_id not in self.records:
                continue
            candidate = candidates[image_id]
            setattr(candidate, field, max(getattr(candidate, field), _unit(hit.score)))
            candidate.rrf += 1 / (self.RRF_K + rank)

    @staticmethod
    def _category_matches(query_category: str, record_category: str) -> bool:
        return canonical_category(query_category) == canonical_category(record_category)

    def _garment_satisfaction(self, record: ImageRecord, request: QueryGarment, vector_score: float) -> tuple[float, list[str]]:
        matching = [garment for garment in record.garments if self._category_matches(request.category, garment.category)]
        if not matching:
            return 0.0, []
        color_match = not request.color or any(canonical_color(garment.color) == canonical_color(request.color) for garment in matching)
        requested_attributes = {attribute.lower() for attribute in request.attributes}
        available_attributes = {attribute.lower() for garment in matching for attribute in garment.attributes}
        attributes_match = not requested_attributes or requested_attributes.issubset(available_attributes)
        label_score = 1.0 if color_match and attributes_match else (0.45 if color_match else 0.1)
        score = label_score if vector_score == 0 else 0.45 * label_score + 0.55 * vector_score
        matched = [request.category]
        if request.color and color_match:
            matched.insert(0, request.color)
        return score, [" ".join(matched)]

    def _metadata_match(self, record: ImageRecord, intent: QueryIntent) -> tuple[float, list[str]]:
        checks: list[bool] = []
        matched: list[str] = []
        if intent.scene:
            ok = record.scene == intent.scene
            checks.append(ok)
            if ok:
                matched.append(intent.scene)
        if intent.style:
            ok = intent.style in record.styles
            checks.append(ok)
            if ok:
                matched.append(intent.style)
        if intent.activity:
            ok = intent.activity in record.activities
            checks.append(ok)
            if ok:
                matched.append(intent.activity)
        for garment in intent.garments:
            ok, details = self._garment_satisfaction(record, garment, 0)
            checks.append(ok >= 0.99)
            if ok >= 0.99:
                matched.extend(details)
        return (sum(checks) / len(checks) if checks else 0.0), matched

    def search(self, query: str, *, k: int = 8) -> tuple[QueryIntent, list[SearchResult]]:
        intent = self.parser.parse(query)
        candidates: dict[str, _Candidate] = defaultdict(_Candidate)
        generic_vector = self.encoders.generic.embed_texts([intent.raw_query])[0]
        fashion_vector = self.encoders.fashion.embed_texts([intent.raw_query])[0]
        self._add_image_hits(candidates, self.store.search_images("generic", generic_vector, self.CANDIDATE_LIMIT), "generic")
        self._add_image_hits(candidates, self.store.search_images("fashion", fashion_vector, self.CANDIDATE_LIMIT), "fashion")

        for request_index, garment in enumerate(intent.garments):
            garment_vector = self.encoders.fashion.embed_texts([garment.phrase])[0]
            for rank, hit in enumerate(self.store.search_garments(garment_vector, self.CANDIDATE_LIMIT), start=1):
                image_id = self._image_id(hit)
                if image_id not in self.records:
                    continue
                candidate = candidates[image_id]
                candidate.garment_vectors[request_index] = max(candidate.garment_vectors.get(request_index, 0.0), _unit(hit.score))
                candidate.rrf += 1 / (self.RRF_K + rank)

        scored: list[tuple[str, ScoreBreakdown, list[str]]] = []
        for image_id, candidate in candidates.items():
            record = self.records[image_id]
            metadata, matched = self._metadata_match(record, intent)
            if intent.garments:
                garment_scores: list[float] = []
                garment_matches: list[str] = []
                for index, request in enumerate(intent.garments):
                    score, details = self._garment_satisfaction(record, request, candidate.garment_vectors.get(index, 0.0))
                    garment_scores.append(score)
                    garment_matches.extend(details)
                # The minimum makes an AND query: one correct garment cannot hide a wrong second garment.
                garment_satisfaction = min(garment_scores) if garment_scores else 0.0
                final = 0.30 * candidate.generic + 0.25 * candidate.fashion + 0.30 * garment_satisfaction + 0.15 * metadata
                matched.extend(garment_matches)
            else:
                garment_satisfaction = 0.0
                final = 0.50 * candidate.generic + 0.35 * candidate.fashion + 0.15 * metadata
            breakdown = ScoreBreakdown(
                generic_similarity=candidate.generic,
                fashion_similarity=candidate.fashion,
                garment_satisfaction=garment_satisfaction,
                metadata_match=metadata,
                final_score=final,
            )
            scored.append((image_id, breakdown, list(dict.fromkeys(matched))))

        scored.sort(key=lambda item: (item[1].final_score, item[1].metadata_match, item[1].garment_satisfaction), reverse=True)
        results: list[SearchResult] = []
        for rank, (image_id, breakdown, matched) in enumerate(scored[:k], start=1):
            record = self.records[image_id]
            results.append(
                SearchResult(
                    image_id=image_id,
                    image_path=record.image_path,
                    rank=rank,
                    score=breakdown.final_score,
                    caption=record.caption,
                    scene=record.scene,
                    styles=record.styles,
                    garments=record.garments,
                    matched_attributes=matched,
                    score_breakdown=breakdown,
                )
            )
        return intent, results


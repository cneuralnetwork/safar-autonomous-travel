"""Stable data contracts shared by curation, indexing, retrieval, and the API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Confidence = Literal["native", "audited", "high", "medium", "low"]
Source = Literal["fashionpedia", "openimages", "coco", "synthetic", "fixture"]


class BoundingBox(BaseModel):
    """Normalized x/y/width/height box. Coordinates are always in [0, 1]."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @field_validator("width")
    @classmethod
    def fits_horizontally(cls, value: float, info: Any) -> float:
        if info.data.get("x", 0) + value > 1.0001:
            raise ValueError("bounding box extends beyond right edge")
        return value

    @field_validator("height")
    @classmethod
    def fits_vertically(cls, value: float, info: Any) -> float:
        if info.data.get("y", 0) + value > 1.0001:
            raise ValueError("bounding box extends beyond bottom edge")
        return value


class Garment(BaseModel):
    id: str
    category: str
    color: str | None = None
    attributes: list[str] = Field(default_factory=list)
    bbox: BoundingBox | None = None
    crop_path: str | None = None
    confidence: Confidence = "medium"


class ImageRecord(BaseModel):
    """One searchable image and its evidence-backed semantic metadata."""

    model_config = ConfigDict(extra="forbid")

    image_id: str
    image_path: str
    source: Source
    scene: str | None = None
    activities: list[str] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)
    garments: list[Garment] = Field(default_factory=list)
    caption: str = ""
    tags: list[str] = Field(default_factory=list)
    confidence: Confidence = "medium"
    audited: bool = False
    attribution: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def path(self) -> Path:
        return Path(self.image_path)


class QueryGarment(BaseModel):
    category: str
    color: str | None = None
    attributes: list[str] = Field(default_factory=list)

    @property
    def phrase(self) -> str:
        parts = [self.color, *self.attributes, self.category]
        return " ".join(part for part in parts if part)


class QueryIntent(BaseModel):
    raw_query: str
    scene: str | None = None
    activity: str | None = None
    style: str | None = None
    garments: list[QueryGarment] = Field(default_factory=list)
    free_text: str = ""
    parser: Literal["rules", "qwen"] = "rules"


class ScoreBreakdown(BaseModel):
    generic_similarity: float = 0.0
    fashion_similarity: float = 0.0
    garment_satisfaction: float = 0.0
    metadata_match: float = 0.0
    final_score: float = 0.0


class SearchResult(BaseModel):
    image_id: str
    image_path: str
    image_url: str | None = None
    rank: int
    score: float
    caption: str
    scene: str | None = None
    styles: list[str] = Field(default_factory=list)
    garments: list[Garment] = Field(default_factory=list)
    matched_attributes: list[str] = Field(default_factory=list)
    score_breakdown: ScoreBreakdown


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    k: int = Field(default=8, ge=1, le=30)
    scenes: list[str] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    intent: QueryIntent
    results: list[SearchResult]
    corpus_size: int
    elapsed_ms: float = Field(ge=0)
    model_profile: str

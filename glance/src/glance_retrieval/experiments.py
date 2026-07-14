"""Reproducible real-data preparation and evaluation judgments for ML experiments."""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .colors import infer_palette_colors
from .evaluation import EvaluationQuery
from .schemas import BoundingBox, Garment, ImageRecord
from .taxonomy import RETRIEVABLE_CATEGORIES, canonical_category

# These priority lists deliberately favor garments a person would describe in a query.  Tiny
# accessories are still retained if an image has no principal garment, but do not consume the
# short caption budget ahead of a shirt, coat, or pair of trousers.
_GARMENT_PRIORITY = (
    "coat", "jacket", "dress", "jumpsuit", "shirt", "t-shirt", "sweater", "cardigan",
    "vest", "pants", "shorts", "skirt", "tie", "scarf", "hat", "bag", "shoes", "belt",
    "tights", "gloves", "socks", "watch", "glasses", "umbrella",
)
_PRIORITY_INDEX = {category: index for index, category in enumerate(_GARMENT_PRIORITY)}


@dataclass(frozen=True)
class _FashionpediaCandidate:
    record: ImageRecord
    image_bytes: bytes


@dataclass(frozen=True)
class _QrelSpec:
    query_id: str
    text: str
    category: str
    garments: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True)
class _ContextQrelSpec:
    query_id: str
    text: str
    category: str
    scene: str | None = None
    style: str | None = None
    activity: str | None = None
    garments: tuple[tuple[str, str | None], ...] = ()


# The search corpus is the held-out Fashionpedia validation subset.  Native category masks define
# relevance; named colors come from the deterministic palette applied only inside those masks' boxes.
# Each item is chosen to have at least three relevant images in the 700-image corpus.
_FASHION_QREL_SPECS = (
    _QrelSpec("black-dress", "A person wearing a black dress.", "attribute", (("dress", "black"),)),
    _QrelSpec("gray-jacket", "A person wearing a gray jacket.", "attribute", (("jacket", "gray"),)),
    _QrelSpec("white-t-shirt", "A person wearing a white t-shirt.", "attribute", (("t-shirt", "white"),)),
    _QrelSpec("brown-coat", "A person wearing a brown coat.", "attribute", (("coat", "brown"),)),
    _QrelSpec("black-pants", "A person wearing black pants.", "attribute", (("pants", "black"),)),
    _QrelSpec("blue-pants", "A person wearing blue pants.", "attribute", (("pants", "blue"),)),
    _QrelSpec("red-dress", "A person wearing a red dress.", "attribute", (("dress", "red"),)),
    _QrelSpec("white-shirt", "A person wearing a white shirt.", "attribute", (("shirt", "white"),)),
    _QrelSpec("beige-skirt", "A person wearing a beige skirt.", "attribute", (("skirt", "beige"),)),
    _QrelSpec("black-cardigan", "A person wearing a black cardigan.", "attribute", (("cardigan", "black"),)),
    _QrelSpec("purple-dress", "A person wearing a purple dress.", "attribute", (("dress", "purple"),)),
    _QrelSpec("yellow-shoes", "A person wearing yellow shoes.", "attribute", (("shoes", "yellow"),)),
    _QrelSpec(
        "black-jacket-black-pants",
        "A person wearing a black jacket and black pants.",
        "compositional",
        (("jacket", "black"), ("pants", "black")),
    ),
    _QrelSpec(
        "gray-jacket-gray-tshirt",
        "A person wearing a gray jacket and a gray t-shirt.",
        "compositional",
        (("jacket", "gray"), ("t-shirt", "gray")),
    ),
    _QrelSpec(
        "black-pants-black-tshirt",
        "A person wearing black pants and a black t-shirt.",
        "compositional",
        (("pants", "black"), ("t-shirt", "black")),
    ),
    _QrelSpec(
        "black-dress-black-tights",
        "A person wearing a black dress and black tights.",
        "compositional",
        (("dress", "black"), ("tights", "black")),
    ),
    _QrelSpec(
        "black-coat-black-pants",
        "A person wearing a black coat and black pants.",
        "compositional",
        (("coat", "black"), ("pants", "black")),
    ),
    _QrelSpec(
        "black-skirt-black-tshirt",
        "A person wearing a black skirt and a black t-shirt.",
        "compositional",
        (("skirt", "black"), ("t-shirt", "black")),
    ),
    _QrelSpec(
        "gray-pants-gray-tshirt",
        "A person wearing gray pants and a gray t-shirt.",
        "compositional",
        (("pants", "gray"), ("t-shirt", "gray")),
    ),
    _QrelSpec(
        "black-bag-black-coat",
        "A person carrying a black bag and wearing a black coat.",
        "compositional",
        (("bag", "black"), ("coat", "black")),
    ),
    _QrelSpec(
        "black-shirt-black-pants",
        "A person wearing a black shirt and black pants.",
        "compositional",
        (("shirt", "black"), ("pants", "black")),
    ),
    _QrelSpec(
        "white-tshirt-beige-shoes",
        "A person wearing a white t-shirt and beige shoes.",
        "compositional",
        (("t-shirt", "white"), ("shoes", "beige")),
    ),
)


# These predicates are frozen before retrieval.  Relevance is exhaustive within the selected
# 300-image Open Images context corpus: every record whose retained QA metadata satisfies the
# predicate is relevant.  Sparse combinations are skipped only when they cannot support the caller's
# explicit minimum-relevant threshold.
_CONTEXT_QREL_SPECS = (
    _ContextQrelSpec("scene-office", "People inside a modern office.", "scene", scene="office"),
    _ContextQrelSpec("scene-city", "People on an urban city street.", "scene", scene="urban_street"),
    _ContextQrelSpec("scene-park", "People outdoors in a park.", "scene", scene="park"),
    _ContextQrelSpec("scene-home", "People in a home interior.", "scene", scene="home"),
    _ContextQrelSpec(
        "formal-office",
        "Professional formal attire inside an office.",
        "contextual",
        scene="office",
        style="formal",
    ),
    _ContextQrelSpec(
        "casual-city",
        "A casual outfit on a city street.",
        "contextual",
        scene="urban_street",
        style="casual",
    ),
    _ContextQrelSpec(
        "outerwear-city",
        "Outerwear worn on an urban street.",
        "contextual",
        scene="urban_street",
        style="outerwear",
    ),
    _ContextQrelSpec(
        "casual-home",
        "A casual outfit at home.",
        "contextual",
        scene="home",
        style="casual",
    ),
    _ContextQrelSpec(
        "sitting-park",
        "Someone sitting in a park.",
        "activity",
        scene="park",
        activity="sitting",
    ),
    _ContextQrelSpec(
        "walking-city",
        "Someone walking along a city street.",
        "activity",
        scene="urban_street",
        activity="walking",
    ),
    _ContextQrelSpec(
        "blue-shirt-context",
        "A person wearing a blue shirt in a contextual photograph.",
        "attribute",
        garments=(("shirt", "blue"),),
    ),
    _ContextQrelSpec(
        "white-shirt-formal",
        "A white shirt as part of a formal outfit.",
        "contextual",
        style="formal",
        garments=(("shirt", "white"),),
    ),
)


def _normalized_xyxy(box: list[float], width: float, height: float) -> BoundingBox | None:
    """Convert Hugging Face Fashionpedia's x1/y1/x2/y2 boxes into the shared contract."""

    if len(box) != 4 or width <= 0 or height <= 0:
        return None
    x1, y1, x2, y2 = (float(value) for value in box)
    x1, y1 = max(0.0, x1), max(0.0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    return BoundingBox(x=x1 / width, y=y1 / height, width=(x2 - x1) / width, height=(y2 - y1) / height)


def _styles(categories: set[str]) -> list[str]:
    values: list[str] = []
    if categories.intersection({"coat", "jacket"}):
        values.append("outerwear")
    if "tie" in categories:
        values.append("formal")
    if categories.intersection({"t-shirt", "shorts", "sweater", "cardigan"}):
        values.append("casual")
    return values


def _primary_category(categories: set[str]) -> str:
    return min(categories, key=lambda category: (_PRIORITY_INDEX.get(category, len(_PRIORITY_INDEX)), category))


def _sort_garments(garments: list[Garment], maximum: int) -> list[Garment]:
    return sorted(
        garments,
        key=lambda garment: (
            _PRIORITY_INDEX.get(garment.category, len(_PRIORITY_INDEX)),
            -(garment.bbox.width * garment.bbox.height if garment.bbox else 0.0),
            garment.id,
        ),
    )[:maximum]


def _balanced_sample(grouped: dict[str, list[_FashionpediaCandidate]], *, limit: int, seed: int) -> list[_FashionpediaCandidate]:
    rng = random.Random(seed)
    for group in grouped.values():
        rng.shuffle(group)
    result: list[_FashionpediaCandidate] = []
    categories = sorted(grouped)
    while len(result) < limit and any(grouped.values()):
        for category in categories:
            if grouped[category] and len(result) < limit:
                result.append(grouped[category].pop())
    if len(result) < limit:
        raise ValueError(f"Only found {len(result)} usable Fashionpedia records; expected {limit}.")
    return result


def build_hf_fashionpedia_train_records(
    parquet_path: Path,
    category_annotations: Path,
    images_dir: Path,
    *,
    limit: int = 5_000,
    seed: int = 19,
    max_garments: int = 4,
) -> list[ImageRecord]:
    """Materialize a balanced, image-disjoint Fashionpedia train subset from a public parquet shard.

    The Hugging Face mirror stores JPEG bytes plus x1/y1/x2/y2 object boxes, but no attribute-name
    table.  The official validation annotation's category taxonomy is identical and is used only to
    map numeric category IDs.  No validation images or labels are included in the returned records.
    """

    if limit < 1 or max_garments < 1:
        raise ValueError("limit and max_garments must be positive")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - setup dependent
        raise RuntimeError("Install pyarrow to curate the Fashionpedia parquet training shard.") from exc

    taxonomy_payload = json.loads(category_annotations.read_text(encoding="utf-8"))
    category_names = {int(item["id"]): str(item["name"]) for item in taxonomy_payload["categories"]}
    table = pq.read_table(parquet_path, columns=["image_id", "image", "width", "height", "objects"])
    grouped: dict[str, list[_FashionpediaCandidate]] = defaultdict(list)
    for row in table.to_pylist():
        image = row.get("image") or {}
        image_bytes = image.get("bytes") if isinstance(image, dict) else None
        width, height = float(row["width"]), float(row["height"])
        objects: dict[str, Any] = row.get("objects") or {}
        categories = objects.get("category") or []
        boxes = objects.get("bbox") or []
        garments: list[Garment] = []
        for index, (category_id, box) in enumerate(zip(categories, boxes, strict=False)):
            category = canonical_category(category_names.get(int(category_id), "unknown"))
            if category not in RETRIEVABLE_CATEGORIES:
                continue
            bbox = _normalized_xyxy(list(box), width, height)
            if bbox is None:
                continue
            image_id = int(row["image_id"])
            garments.append(
                Garment(
                    id=f"fashionpedia-train-{image_id}-{index}",
                    category=category,
                    bbox=bbox,
                    confidence="native",
                )
            )
        if not garments or not image_bytes:
            continue
        garments = _sort_garments(garments, maximum=max_garments)
        categories_present = {garment.category for garment in garments}
        image_id = int(row["image_id"])
        record = ImageRecord(
            image_id=f"fashionpedia-train-{image_id}",
            image_path=str(images_dir / f"fashionpedia-train-{image_id}.jpg"),
            source="fashionpedia",
            styles=_styles(categories_present),
            garments=garments,
            caption=" ".join(garment.category for garment in garments),
            confidence="native",
            attribution="Fashionpedia / CVDF (official train split via Hugging Face mirror)",
            extra={"fashionpedia_image_id": image_id, "split": "train", "parquet": str(parquet_path)},
        )
        grouped[_primary_category(categories_present)].append(_FashionpediaCandidate(record=record, image_bytes=bytes(image_bytes)))

    selected = _balanced_sample(grouped, limit=limit, seed=seed)
    images_dir.mkdir(parents=True, exist_ok=True)
    records: list[ImageRecord] = []
    for candidate in selected:
        record = candidate.record
        image_path = Path(record.image_path)
        if not image_path.exists() or image_path.stat().st_size < 512:
            image_path.write_bytes(candidate.image_bytes)
        colors = infer_palette_colors(image_path, [garment.bbox for garment in record.garments])
        garments = [
            garment.model_copy(update={"color": color})
            for garment, color in zip(record.garments, colors, strict=True)
        ]
        caption = " and ".join(f"{garment.color or ''} {garment.category}".strip() for garment in garments)
        records.append(record.model_copy(update={"garments": garments, "caption": caption}))
    return records


def _has_requirements(record: ImageRecord, requirements: tuple[tuple[str, str | None], ...]) -> bool:
    for category, color in requirements:
        if not any(garment.category == category and (color is None or garment.color == color) for garment in record.garments):
            return False
    return True


def select_fashionpedia_validation_records(records: list[ImageRecord]) -> list[ImageRecord]:
    """Return the real validation-fashion corpus used for complete-label benchmark runs."""

    selected = [record for record in records if record.source == "fashionpedia"]
    if not selected:
        raise ValueError("No Fashionpedia records were found in the supplied corpus.")
    return selected


def build_fashionpedia_validation_qrels(records: list[ImageRecord], *, minimum_relevant: int = 3) -> list[EvaluationQuery]:
    """Create complete within-corpus qrels for a held-out Fashionpedia validation retrieval test."""

    fashionpedia = select_fashionpedia_validation_records(records)
    qrels: list[EvaluationQuery] = []
    for spec in _FASHION_QREL_SPECS:
        relevant = [record.image_id for record in fashionpedia if _has_requirements(record, spec.garments)]
        if len(relevant) < minimum_relevant:
            raise ValueError(
                f"Qrel {spec.query_id} has {len(relevant)} relevant images; expected at least {minimum_relevant}."
            )
        qrels.append(
            EvaluationQuery(
                query_id=spec.query_id,
                text=spec.text,
                relevant_image_ids=sorted(relevant),
                split="fashionpedia_validation",
                category=spec.category,
            )
        )
    return qrels


def _matches_context_spec(record: ImageRecord, spec: _ContextQrelSpec) -> bool:
    if record.source not in {"openimages", "coco"}:
        return False
    if spec.scene and record.scene != spec.scene:
        return False
    if spec.style and spec.style not in record.styles:
        return False
    if spec.activity and spec.activity not in record.activities:
        return False
    return _has_requirements(record, spec.garments)


def build_context_qrels(records: list[ImageRecord], *, minimum_relevant: int = 3) -> list[EvaluationQuery]:
    """Build real context qrels from the final QA-gated context corpus.

    Unlike the controlled five-image assignment probes, these judgments cover all matching records
    in the real context subset.  The fixed spec list avoids selecting queries after seeing retrieval
    rankings; a combination is emitted only if its labels support the requested minimum positives.
    """

    if minimum_relevant < 1:
        raise ValueError("minimum_relevant must be positive")
    context = [record for record in records if record.source in {"openimages", "coco"}]
    if not context:
        raise ValueError("No real context records were found in the supplied corpus.")
    qrels: list[EvaluationQuery] = []
    for spec in _CONTEXT_QREL_SPECS:
        relevant = sorted(record.image_id for record in context if _matches_context_spec(record, spec))
        if len(relevant) < minimum_relevant:
            continue
        qrels.append(
            EvaluationQuery(
                query_id=spec.query_id,
                text=spec.text,
                relevant_image_ids=relevant,
                split="real_context",
                category=spec.category,
            )
        )
    scene_ids = {item.query_id for item in qrels if item.category == "scene"}
    required_scene_ids = {"scene-office", "scene-city", "scene-park", "scene-home"}
    if scene_ids != required_scene_ids:
        missing = ", ".join(sorted(required_scene_ids - scene_ids))
        raise ValueError(f"Context corpus does not support all required scene qrels: {missing}")
    return qrels


def audit_final_corpus(
    records: list[ImageRecord],
    *,
    training_records: list[ImageRecord] | None = None,
    expected_total: int = 1_000,
    expected_fashionpedia: int = 700,
    expected_per_scene: int = 75,
    minimum_person_area: float = 0.08,
) -> dict[str, object]:
    """Produce machine-checkable evidence for the final dataset/index contract."""

    sources = Counter(record.source for record in records)
    context = [record for record in records if record.source != "fashionpedia"]
    scene_counts = Counter(record.scene for record in context)
    duplicate_count = len(records) - len({record.image_id for record in records})
    missing_images = [record.image_id for record in records if not record.path.is_file()]
    garments = [garment for record in records for garment in record.garments]
    crop_paths = [Path(garment.crop_path) for garment in garments if garment.crop_path]
    missing_crops = [str(path) for path in crop_paths if not path.is_file()]
    annotated_context = [record for record in records if record.source in {"openimages", "coco"}]
    annotation_models = Counter(
        str(record.extra.get("vlm_annotation", {}).get("model") or "missing") for record in annotated_context
    )
    annotation_statuses = Counter(
        str(record.extra.get("vlm_annotation", {}).get("status") or "missing") for record in annotated_context
    )
    qa_tiers = Counter(
        str(record.extra.get("context_qa", {}).get("tier") or "missing") for record in annotated_context
    )
    validation_ids = {
        int(record.extra["fashionpedia_image_id"])
        for record in records
        if record.source == "fashionpedia" and "fashionpedia_image_id" in record.extra
    }
    training_ids = {
        int(record.extra["fashionpedia_image_id"])
        for record in (training_records or [])
        if record.source == "fashionpedia" and "fashionpedia_image_id" in record.extra
    }
    overlap = sorted(validation_ids.intersection(training_ids))
    required_scenes = {"office", "urban_street", "park", "home"}
    checks = {
        "total_is_expected": len(records) == expected_total,
        "image_ids_are_unique": duplicate_count == 0,
        "all_images_exist": not missing_images,
        "fashionpedia_count_is_expected": sources["fashionpedia"] == expected_fashionpedia,
        "context_count_is_expected": len(context) == expected_total - expected_fashionpedia,
        "context_is_balanced": all(scene_counts[scene] == expected_per_scene for scene in required_scenes),
        "selected_real_context_pass_qa": all(
            bool(record.extra.get("context_qa", {}).get("accepted")) for record in annotated_context
        ),
        "selected_real_context_have_successful_model_provenance": all(
            record.extra.get("vlm_annotation", {}).get("status") == "success" for record in annotated_context
        ),
        "selected_real_context_have_visible_people": all(
            float(record.extra.get("person_box_area") or 0.0) >= minimum_person_area
            for record in annotated_context
        ),
        "training_and_validation_are_disjoint": not overlap,
        "all_recorded_crops_exist": not missing_crops,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "counts": {
            "images": len(records),
            "sources": dict(sorted(sources.items())),
            "context_scenes": {scene: scene_counts[scene] for scene in sorted(required_scenes)},
            "garments": len(garments),
            "recorded_crop_paths": len(crop_paths),
            "real_context_qa_tiers": dict(sorted(qa_tiers.items())),
            "real_context_annotation_statuses": dict(sorted(annotation_statuses.items())),
            "real_context_annotation_models": dict(sorted(annotation_models.items())),
            "training_validation_overlap": len(overlap),
        },
        "failures": {
            "duplicate_image_ids": duplicate_count,
            "missing_image_ids": missing_images[:20],
            "missing_crop_paths": missing_crops[:20],
            "overlapping_fashionpedia_ids": overlap[:20],
        },
    }

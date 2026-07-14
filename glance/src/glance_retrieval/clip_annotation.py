"""Batched dual-encoder context annotation for CPU-constrained fallback runs.

This module is intentionally separate from the generative VLM annotators.  It uses generic CLIP
for the whole-frame scene/activity evidence and FashionCLIP for the native Person crop.  The
result remains model-derived, schema-validated, and provenance-labelled; it is never presented as
a human audit or as a generative caption.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image

from .annotation import VisionAnnotation, VisionGarment, merge_annotation, normalize_annotation
from .embeddings import EncoderPair
from .schemas import BoundingBox, ImageRecord

CLIP_CONTEXT_MODEL = "clip-ensemble/generic-clip+fashionclip-lora@context-v1"

SCENE_PROMPTS: dict[str, tuple[str, ...]] = {
    "office": (
        "a person inside a modern office",
        "a workplace interior with desks or a conference room",
        "professional business people in an office",
    ),
    "urban_street": (
        "a person walking on a city street",
        "an outdoor urban sidewalk or crosswalk",
        "a downtown street scene with buildings",
    ),
    "park": (
        "a person outdoors in a green public park",
        "a person in a garden with trees and grass",
        "a person sitting or walking near a park bench",
    ),
    "home": (
        "a person inside a home",
        "a domestic living room or bedroom interior",
        "a person in a house or apartment",
    ),
    "other": (
        "a person at a restaurant cafe store school airport or public building",
        "a person at a beach stadium concert stage or sports venue",
        "a person in a location that is not an office street park or home",
    ),
}

ACTIVITY_PROMPTS: dict[str, tuple[str, ...]] = {
    "sitting": ("a person sitting", "a seated person"),
    "walking": ("a person walking", "someone taking a walk"),
    "standing": ("a person standing", "a standing person"),
    "working": ("a person working", "someone doing office work"),
    "posing": ("a person posing for a photograph", "a fashion pose"),
    "talking": ("people talking", "a person in conversation"),
}

STYLE_PROMPTS: dict[str, tuple[str, ...]] = {
    "formal": ("a formal professional outfit", "business attire"),
    "casual": ("a casual relaxed outfit", "casual weekend clothing"),
    "outerwear": ("an outfit featuring outerwear", "a coat or jacket outfit"),
}

GARMENT_CATEGORIES = (
    "blazer",
    "shirt",
    "t-shirt",
    "hoodie",
    "sweater",
    "jacket",
    "coat",
    "dress",
    "pants",
    "shorts",
    "skirt",
    "tie",
    "shoes",
    "hat",
    "bag",
)

GARMENT_COLORS = (
    "black",
    "white",
    "gray",
    "beige",
    "brown",
    "red",
    "orange",
    "yellow",
    "green",
    "blue",
    "purple",
    "pink",
    "multicolor",
)

_CATEGORY_GROUP = {
    "blazer": "upper",
    "shirt": "upper",
    "t-shirt": "upper",
    "hoodie": "upper",
    "sweater": "upper",
    "jacket": "upper",
    "coat": "upper",
    "dress": "onepiece",
    "pants": "lower",
    "shorts": "lower",
    "skirt": "lower",
    "tie": "accessory",
    "shoes": "accessory",
    "hat": "accessory",
    "bag": "accessory",
}

_NATIVE_CATEGORY_HINTS = {
    "suit": ("blazer", "pants"),
    "dress": ("dress",),
    "coat": ("coat",),
    "jacket": ("jacket",),
    "shirt": ("shirt",),
    "jeans": ("pants",),
    "footwear": ("shoes",),
    "shoe": ("shoes",),
    "hat": ("hat",),
    "tie": ("tie",),
    "handbag": ("bag",),
}

_SCENE_CAPTIONS = {
    "office": "inside an office",
    "urban_street": "on a city street",
    "park": "in a public park",
    "home": "inside a home",
    "other": "in another visible setting",
}


def _normalize_rows(values: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    return values / np.clip(norms, 1e-12, None)


def _prompt_centroids(embed_texts: Callable[[list[str]], np.ndarray], prompts: dict[str, tuple[str, ...]]) -> tuple[list[str], np.ndarray]:
    labels = list(prompts)
    vectors = []
    for label in labels:
        vectors.append(embed_texts(list(prompts[label])).mean(axis=0))
    return labels, _normalize_rows(np.asarray(vectors, dtype=np.float32))


def _softmax(scores: np.ndarray, *, temperature: float = 0.035) -> np.ndarray:
    scaled = (scores - np.max(scores)) / temperature
    exponentials = np.exp(scaled)
    return exponentials / np.clip(exponentials.sum(), 1e-12, None)


def _native_scene_prior(record: ImageRecord, labels: list[str]) -> np.ndarray:
    weights = np.zeros(len(labels), dtype=np.float32)
    for hypothesis in record.extra.get("scene_hypotheses", []):
        if not isinstance(hypothesis, dict) or hypothesis.get("scene") not in labels:
            continue
        index = labels.index(str(hypothesis["scene"]))
        try:
            weights[index] += max(0.0, float(hypothesis.get("score") or 0.0))
        except (TypeError, ValueError):
            continue
    if not weights.any() and record.scene in labels:
        weights[labels.index(record.scene)] = 1.0
    if weights.any():
        weights /= weights.sum()
    return weights


def _focus_crop(record: ImageRecord, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{record.image_id}__native-person-focus.jpg"
    if target.exists():
        return target
    payload = record.extra.get("person_box")
    box = BoundingBox.model_validate(payload) if payload else None
    with Image.open(record.path) as source:
        image = source.convert("RGB")
        if box is not None:
            margin_x = box.width * 0.12
            margin_y = box.height * 0.08
            left = max(0, round((box.x - margin_x) * image.width))
            top = max(0, round((box.y - margin_y) * image.height))
            right = min(image.width, round((box.x + box.width + margin_x) * image.width))
            bottom = min(image.height, round((box.y + box.height + margin_y) * image.height))
            if right > left and bottom > top:
                image = image.crop((left, top, right, bottom))
        image.save(target, quality=92)
    return target


def _native_category_boosts(record: ImageRecord) -> dict[str, float]:
    boosts: dict[str, float] = {}
    tag_text = " ".join(record.tags).lower()
    for phrase, categories in _NATIVE_CATEGORY_HINTS.items():
        if phrase not in tag_text:
            continue
        for category in categories:
            boosts[category] = max(boosts.get(category, 0.0), 0.025)
    return boosts


def _select_garments(record: ImageRecord, scores: np.ndarray) -> list[VisionGarment]:
    matrix = scores.reshape(len(GARMENT_CATEGORIES), len(GARMENT_COLORS))
    category_scores = matrix.max(axis=1)
    boosts = _native_category_boosts(record)
    category_scores = np.asarray(
        [score + boosts.get(category, 0.0) for category, score in zip(GARMENT_CATEGORIES, category_scores, strict=True)]
    )
    order = list(np.argsort(category_scores)[::-1])
    selected: list[int] = []
    used_groups: set[str] = set()
    best_score = float(category_scores[order[0]])
    for index in order:
        category = GARMENT_CATEGORIES[index]
        group = _CATEGORY_GROUP[category]
        if group in used_groups or float(category_scores[index]) < best_score - 0.055:
            continue
        if group == "onepiece" and used_groups.intersection({"upper", "lower"}):
            continue
        if group in {"upper", "lower"} and "onepiece" in used_groups:
            continue
        selected.append(index)
        used_groups.add(group)
        if len(selected) == 3:
            break
    if not selected:
        selected = [order[0]]
    garments = []
    for category_index in selected:
        color_index = int(np.argmax(matrix[category_index]))
        garments.append(
            VisionGarment(
                category=GARMENT_CATEGORIES[category_index],
                color=GARMENT_COLORS[color_index],
                attributes=["dual-encoder-zero-shot"],
            )
        )
    return garments


def _caption(scene: str, garments: list[VisionGarment], activity: str | None) -> str:
    clothing = " and ".join(f"a {item.color} {item.category}" for item in garments[:2])
    action = f" {activity}" if activity else ""
    return f"A person{action} wearing {clothing} {_SCENE_CAPTIONS[scene]}."


def annotate_context_with_clip(
    records: list[ImageRecord],
    *,
    encoders: EncoderPair,
    focus_dir: Path,
    batch_size: int = 16,
    checkpoint: Callable[[list[ImageRecord], int, int], None] | None = None,
) -> list[ImageRecord]:
    """Annotate context records in batches with explicit dual-encoder provenance."""

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    scene_labels, scene_vectors = _prompt_centroids(encoders.generic.embed_texts, SCENE_PROMPTS)
    activity_labels, activity_vectors = _prompt_centroids(encoders.generic.embed_texts, ACTIVITY_PROMPTS)
    style_labels, style_vectors = _prompt_centroids(encoders.fashion.embed_texts, STYLE_PROMPTS)
    garment_prompts = [
        f"a photo of a person wearing a {color} {category}"
        for category in GARMENT_CATEGORIES
        for color in GARMENT_COLORS
    ]
    garment_vectors = encoders.fashion.embed_texts(garment_prompts)

    enriched: list[ImageRecord] = []
    for offset in range(0, len(records), batch_size):
        batch = records[offset : offset + batch_size]
        started = perf_counter()
        focus_paths = [_focus_crop(record, focus_dir) for record in batch]
        generic_vectors = encoders.generic.embed_images([record.path for record in batch])
        fashion_vectors = encoders.fashion.embed_images(focus_paths)
        for index, record in enumerate(batch):
            scene_clip = _softmax(generic_vectors[index] @ scene_vectors.T)
            native_prior = _native_scene_prior(record, scene_labels)
            scene_probabilities = 0.82 * scene_clip + 0.18 * native_prior
            scene_order = np.argsort(scene_probabilities)[::-1]
            scene = scene_labels[int(scene_order[0])]
            scene_margin = float(scene_probabilities[scene_order[0]] - scene_probabilities[scene_order[1]])

            activity_probabilities = _softmax(generic_vectors[index] @ activity_vectors.T)
            activity_index = int(np.argmax(activity_probabilities))
            activity = activity_labels[activity_index] if activity_probabilities[activity_index] >= 0.30 else None

            style_probabilities = _softmax(fashion_vectors[index] @ style_vectors.T)
            style_index = int(np.argmax(style_probabilities))
            styles = [style_labels[style_index]] if style_probabilities[style_index] >= 0.34 else []

            garment_scores = fashion_vectors[index] @ garment_vectors.T
            garments = _select_garments(record, garment_scores)
            garment_margin = float(np.sort(garment_scores)[-1] - np.sort(garment_scores)[-2])
            confidence = "high" if scene_margin >= 0.18 and garment_margin >= 0.008 else "medium"
            annotation = normalize_annotation(
                VisionAnnotation(
                    scene=scene,
                    activities=[activity] if activity else [],
                    styles=styles,
                    garments=garments,
                    caption=_caption(scene, garments, activity),
                    confidence=confidence,
                )
            )
            annotated = merge_annotation(record, annotation)
            latency_ms = (perf_counter() - started) * 1_000 / len(batch)
            annotated = annotated.model_copy(
                update={
                    "extra": {
                        **annotated.extra,
                        "vlm_annotation": {
                            "status": "success",
                            "model": CLIP_CONTEXT_MODEL,
                            "annotation_family": "dual_encoder_zero_shot_fallback",
                            "candidate_scene": record.scene,
                            "predicted_scene": scene,
                            "used_native_person_crop": bool(record.extra.get("person_box")),
                            "runtime_device": "cpu",
                            "scene_margin": round(scene_margin, 6),
                            "scene_probabilities": {
                                label: round(float(probability), 6)
                                for label, probability in zip(scene_labels, scene_probabilities, strict=True)
                            },
                            "latency_ms": round(latency_ms, 3),
                        },
                    }
                }
            )
            enriched.append(annotated)
        if checkpoint:
            checkpoint(enriched, min(offset + len(batch), len(records)), len(records))
    return enriched

"""Dataset curation utilities; downloads are explicit and assets never enter Git."""

from __future__ import annotations

import csv
import io
import json
import random
import re
import textwrap
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from PIL import Image, ImageDraw

from .annotation import reconcile_scene
from .colors import infer_palette_color
from .io import write_jsonl
from .schemas import BoundingBox, Garment, ImageRecord
from .taxonomy import COLORS, RETRIEVABLE_CATEGORIES, canonical_category, canonical_color

# Open Images image-level labels are deliberately used only to *propose* and rank candidates.
# A scene is not accepted as ground truth until the subsequent VLM/human audit.  The values are
# ranked from scene-specific evidence to useful but ambiguous supporting evidence.
CONTEXT_HINTS: dict[str, dict[str, int]] = {
    "office": {
        "Office": 10,
        "Office building": 9,
        "Office chair": 8,
        "Desk": 7,
        "Computer desk": 7,
        "Computer monitor": 6,
        "Businessperson": 6,
        "Formal wear": 4,
        "Suit": 3,
        "Computer": 2,
        "Table": 1,
        "Chair": 1,
    },
    "urban_street": {
        "Street": 10,
        "Sidewalk": 10,
        "Road": 8,
        "Street light": 6,
        "Street sign": 6,
        "Downtown": 6,
        "Urban area": 5,
        "City": 4,
        "Cityscape": 4,
        "Building": 1,
    },
    "park": {
        "Park": 10,
        "National park": 10,
        "State park": 10,
        "Garden": 7,
        "Outdoor bench": 7,
        "Bench": 6,
        "Grass": 3,
        "Tree": 1,
    },
    "home": {
        "Home": 10,
        "Living room": 10,
        "Kitchen": 9,
        "Apartment": 8,
        "House": 6,
        "Bedroom": 6,
        "Couch": 4,
        "Bed": 4,
        "Coffee table": 3,
        "Furniture": 2,
        "Table": 1,
        "Chair": 1,
    },
}

# Labels that describe the setting itself, excluding ambiguous fashion/object proxies such as
# ``Suit`` (office), ``Building`` (street), or ``Tree`` (park).  They are used as a ranking signal
# for the final balanced sample, while the visual model and explicit review remain independent.
STRONG_CONTEXT_HINTS: dict[str, set[str]] = {
    "office": {"Office", "Office building", "Office chair", "Desk", "Computer desk", "Computer monitor", "Computer"},
    "urban_street": {"Street", "Sidewalk", "Road", "Street light", "Street sign", "Downtown", "Urban area", "City", "Cityscape"},
    "park": {"Park", "National park", "State park", "Garden", "Outdoor bench", "Bench"},
    "home": {"Home", "Living room", "Kitchen", "Apartment", "House", "Bedroom", "Couch", "Bed", "Coffee table"},
}


def _find_attr_color(attributes: list[str]) -> str | None:
    for attribute in attributes:
        words = attribute.lower().replace("-", " ").split()
        for word in words:
            color = canonical_color(word)
            if color in COLORS:
                return color
    return None


def _normalized_box(box: list[float], width: float, height: float) -> BoundingBox | None:
    if len(box) != 4 or width <= 0 or height <= 0:
        return None
    x, y, w, h = (float(value) for value in box)
    x, y = max(0.0, x / width), max(0.0, y / height)
    w, h = min(1 - x, w / width), min(1 - y, h / height)
    if w <= 0 or h <= 0:
        return None
    return BoundingBox(x=x, y=y, width=w, height=h)


def build_fashionpedia_records(
    annotations_path: Path,
    images_dir: Path,
    *,
    limit: int = 700,
    seed: int = 7,
    infer_colors: bool = True,
) -> list[ImageRecord]:
    """Build a deterministic, category-balanced Fashionpedia subset from native annotations.

    Full apparel/accessory instances are retained; Fashionpedia parts (for example sleeves and
    pockets) are intentionally excluded from the retrieval object list. Native attributes do not
    carry broad color labels consistently, so colors are inferred from the supplied instance box.
    """

    payload = json.loads(annotations_path.read_text(encoding="utf-8"))
    category_names = {item["id"]: item["name"] for item in payload["categories"]}
    attribute_names = {item["id"]: item["name"] for item in payload.get("attributes", [])}
    image_by_id = {item["id"]: item for item in payload["images"]}
    by_image: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for annotation in payload["annotations"]:
        if annotation["image_id"] in image_by_id:
            by_image[annotation["image_id"]].append(annotation)

    candidates: dict[str, list[ImageRecord]] = defaultdict(list)
    for image_id, image_annotations in by_image.items():
        image = image_by_id[image_id]
        image_path = images_dir / image["file_name"]
        if not image_path.exists():
            continue
        garments: list[Garment] = []
        for index, annotation in enumerate(image_annotations):
            raw_attributes = [attribute_names[attr] for attr in annotation.get("attribute_ids", []) if attr in attribute_names]
            category = canonical_category(category_names.get(annotation["category_id"], "unknown"))
            if category not in RETRIEVABLE_CATEGORIES:
                continue
            bbox = _normalized_box(annotation.get("bbox", []), image["width"], image["height"])
            color = _find_attr_color(raw_attributes)
            if color is None and infer_colors and bbox is not None:
                try:
                    color = infer_palette_color(image_path, bbox=bbox)
                except (OSError, ValueError):
                    # A malformed or exceptionally tiny source box must not stop corpus curation.
                    color = None
            garments.append(
                Garment(
                    id=f"fashionpedia-{image_id}-{index}",
                    category=category,
                    color=color,
                    attributes=[attribute.lower() for attribute in raw_attributes],
                    bbox=bbox,
                    confidence="native",
                )
            )
        if not garments:
            continue
        categories = {garment.category for garment in garments}
        styles: list[str] = []
        if categories.intersection({"coat", "jacket"}):
            styles.append("outerwear")
        if "tie" in categories:
            styles.append("formal")
        if categories.intersection({"t-shirt", "shorts", "sweater", "cardigan"}):
            styles.append("casual")
        record = ImageRecord(
            image_id=f"fashionpedia-{image_id}",
            image_path=str(image_path),
            source="fashionpedia",
            styles=styles,
            garments=garments,
            caption=" ".join(f"{garment.color or ''} {garment.category}".strip() for garment in garments),
            confidence="native",
            attribution="Fashionpedia / CVDF",
            extra={"fashionpedia_image_id": image_id, "file_name": image["file_name"]},
        )
        # Prefer torso/outerwear categories for balanced sampling rather than a shoe/accessory
        # that happened to appear first in the raw annotation order.
        selection_priority = [
            "coat", "jacket", "dress", "jumpsuit", "shirt", "t-shirt", "sweater", "cardigan",
            "vest", "pants", "shorts", "skirt", "tie", "shoes", "bag", "hat", "scarf",
        ]
        primary = next((category for category in selection_priority if category in categories), sorted(categories)[0])
        candidates[primary].append(record)

    rng = random.Random(seed)
    for records in candidates.values():
        rng.shuffle(records)
    selected: list[ImageRecord] = []
    categories = sorted(candidates)
    while len(selected) < limit and any(candidates.values()):
        for category in categories:
            if candidates[category] and len(selected) < limit:
                selected.append(candidates[category].pop())
    if len(selected) < limit:
        raise ValueError(f"Only found {len(selected)} Fashionpedia records; expected {limit}.")
    return selected


def select_openimages_candidates(
    image_labels_csv: Path,
    class_descriptions_csv: Path,
    image_info_csv: Path,
    *,
    per_scene: int = 600,
    box_annotations_csv: Path | None = None,
    min_person_area: float = 0.01,
) -> pd.DataFrame:
    """Create an auditable candidate pool from Open Images labels and source URLs.

    The output intentionally over-samples each scene. A VLM and human audit choose the final
    75 images per scene; image-level labels alone are not considered sufficient ground truth.
    """

    # The historic boxable-class CSV has no header, whereas the current full V7 vocabulary does.
    # Reading both forms in this way keeps the command compatible with either source while
    # allowing the much richer full vocabulary to improve the candidate pool.
    classes = pd.read_csv(class_descriptions_csv, header=None, names=["LabelName", "DisplayName"])
    classes = classes[classes["LabelName"].astype(str) != "LabelName"]
    ids_by_name = dict(zip(classes["DisplayName"], classes["LabelName"], strict=False))
    person_id = ids_by_name.get("Person")
    if not person_id:
        raise ValueError("Open Images class descriptions do not include Person")
    scene_ids = {
        scene: {ids_by_name[name]: weight for name, weight in hints.items() if name in ids_by_name}
        for scene, hints in CONTEXT_HINTS.items()
    }
    wanted_ids = {person_id, *(label for labels in scene_ids.values() for label in labels)}
    positives: dict[str, set[str]] = defaultdict(set)
    for chunk in pd.read_csv(image_labels_csv, chunksize=250_000):
        chunk = chunk[(chunk["Confidence"] == 1) & (chunk["LabelName"].isin(wanted_ids))]
        for image_id, label_name in zip(chunk["ImageID"], chunk["LabelName"], strict=False):
            positives[str(image_id)].add(str(label_name))

    rows: list[dict[str, str]] = []
    for image_id, labels in positives.items():
        if person_id not in labels:
            continue
        for scene, labels_for_scene in scene_ids.items():
            evidence_ids = labels.intersection(labels_for_scene)
            if evidence_ids:
                evidence_names = sorted(
                    (str(classes.loc[classes["LabelName"] == label, "DisplayName"].iloc[0]) for label in evidence_ids),
                    key=str.lower,
                )
                rows.append(
                    {
                        "ImageID": image_id,
                        "scene_hint": scene,
                        "scene_score": sum(labels_for_scene[label] for label in evidence_ids),
                        "label_evidence": "; ".join(evidence_names),
                    }
                )
    candidates = pd.DataFrame(rows).drop_duplicates()
    if box_annotations_csv:
        if not 0 <= min_person_area <= 1:
            raise ValueError("min_person_area must be between 0 and 1")
        # Image-level ``Person`` labels include tiny figures, posters, and people on monitors.
        # An independently annotated person box with a meaningful image area is a stronger and
        # still fully native signal that the source contains a retrievable outfit.
        person_areas: dict[str, float] = {}
        for chunk in pd.read_csv(box_annotations_csv, usecols=["ImageID", "LabelName", "XMin", "XMax", "YMin", "YMax"], chunksize=250_000):
            people = chunk[chunk["LabelName"] == person_id].copy()
            if people.empty:
                continue
            people["area"] = (people["XMax"] - people["XMin"]) * (people["YMax"] - people["YMin"])
            for image_id, area in people.groupby("ImageID")["area"].max().items():
                person_areas[str(image_id)] = max(person_areas.get(str(image_id), 0.0), float(area))
        candidates["person_box_area"] = candidates["ImageID"].map(person_areas).fillna(0.0)
        candidates = candidates[candidates["person_box_area"] >= min_person_area]
    info = pd.read_csv(image_info_csv)
    # Thumbnails are smaller, much more reliable than stale original Flickr assets, and retain
    # provenance in the manifest.  The original landing page and licence remain alongside them.
    url_col = next((column for column in ("Thumbnail300KURL", "OriginalURL", "URL") if column in info.columns), None)
    if not url_col:
        raise ValueError("Open Images image info must contain Thumbnail300KURL, OriginalURL, or URL")
    joined = candidates.merge(info, on="ImageID", how="inner")
    if url_col == "Thumbnail300KURL":
        # Some metadata rows do not carry a thumbnail even though a source asset is available.
        fallback_columns = [column for column in ("OriginalURL", "URL") if column in joined]
        joined["image_url"] = joined[url_col]
        for fallback in fallback_columns:
            joined["image_url"] = joined["image_url"].fillna(joined[fallback])
    else:
        joined["image_url"] = joined[url_col]
    selected = (
        joined.sort_values(["scene_hint", "scene_score", "ImageID"], ascending=[True, False, True])
        .groupby("scene_hint", group_keys=False)
        .head(per_scene)
    )
    return selected[
        [
            column
            for column in (
                "ImageID",
                "scene_hint",
                "scene_score",
                "label_evidence",
                "person_box_area",
                "image_url",
                "OriginalURL",
                "OriginalLandingURL",
                "License",
                "AuthorProfileURL",
            )
            if column in selected
        ]
    ]


def download_context_candidates(
    candidates_csv: Path,
    destination: Path,
    *,
    timeout: int = 30,
    workers: int = 10,
) -> Path:
    """Download a candidate pool concurrently with image validation and stable provenance.

    The pool can contain one image proposed for more than one scene.  Downloads are de-duplicated
    by ``ImageID`` while each manifest row keeps its own scene hypothesis for the audit stage.
    """

    destination.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(candidates_csv.open(encoding="utf-8")))

    def fetch(image_id: str, image_urls: tuple[str, ...]) -> tuple[str, str, str]:
        target = destination / f"openimages-{image_id}.jpg"
        if target.exists() and target.stat().st_size > 512:
            return image_id, str(target), "ok"
        last_error = "failed: MissingSchema"
        for image_url in image_urls:
            try:
                response = requests.get(
                    image_url,
                    timeout=timeout,
                    headers={"User-Agent": "Glance-fashion-retrieval/1.0 (educational dataset curation)"},
                )
                response.raise_for_status()
                if not response.headers.get("content-type", "").lower().startswith("image/"):
                    raise ValueError("response was not an image")
                # Flickr occasionally serves a plausible HTTP response containing an error document.
                # Verify it before making the file visible to later indexing commands.
                with Image.open(io.BytesIO(response.content)) as image:
                    image.verify()
                target.write_bytes(response.content)
                return image_id, str(target), "ok"
            except (OSError, ValueError, requests.RequestException) as exc:
                last_error = f"failed: {type(exc).__name__}"
        return image_id, "", last_error

    def values_for(row: dict[str, str]) -> tuple[str, ...]:
        values = []
        for column in ("image_url", "OriginalURL"):
            value = str(row.get(column) or "").strip()
            if value and value.lower() != "nan" and value not in values:
                values.append(value)
        return tuple(values)

    unique_urls = {row["ImageID"]: values_for(row) for row in rows}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        fetched = {
            image_id: (local_path, status)
            for image_id, local_path, status in executor.map(lambda item: fetch(*item), unique_urls.items())
        }
    completed: list[dict[str, str]] = []
    for row in rows:
        local_path, status = fetched[row["ImageID"]]
        row["local_path"] = local_path
        row["download_status"] = status
        completed.append(row)
    output = destination / "download_manifest.csv"
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted({key for row in completed for key in row}))
        writer.writeheader()
        writer.writerows(completed)
    return output


COCO_HOME_WEIGHTS = {
    "bed": 12.0,
    "couch": 11.0,
    "refrigerator": 10.0,
    "toilet": 10.0,
    "oven": 8.0,
    "microwave": 8.0,
    "sink": 6.0,
    "dining table": 6.0,
    "tv": 5.0,
    "chair": 2.0,
    "remote": 2.0,
    "book": 1.0,
}

COCO_SCENE_PATTERNS: dict[str, re.Pattern[str]] = {
    "office": re.compile(
        r"\b(?:office|desk|workstation|work station|conference room|meeting room|computer room)\b",
        re.IGNORECASE,
    ),
    "park": re.compile(
        r"\b(?:park|park bench|public garden|botanical garden|lawn|grassy field|grass field|wooded trail)\b",
        re.IGNORECASE,
    ),
    "urban_street": re.compile(
        r"\b(?:street|sidewalk|crosswalk|intersection|city road|urban road|downtown)\b",
        re.IGNORECASE,
    ),
}

COCO_SCENE_OBJECT_WEIGHTS: dict[str, dict[str, float]] = {
    "office": {
        "keyboard": 9.0,
        "laptop": 8.0,
        "mouse": 7.0,
        "chair": 2.0,
        "book": 2.0,
        "cell phone": 1.0,
        "dining table": 1.0,
    },
    "park": {
        "bench": 10.0,
        "frisbee": 4.0,
        "kite": 4.0,
        "dog": 2.0,
        "bicycle": 2.0,
        "sports ball": 1.0,
    },
    "urban_street": {
        "traffic light": 10.0,
        "stop sign": 10.0,
        "parking meter": 7.0,
        "bus": 6.0,
        "truck": 5.0,
        "car": 4.0,
        "motorcycle": 3.0,
        "bicycle": 2.0,
    },
}


def select_coco_scene_candidates(
    annotations_json: Path,
    captions_json: Path,
    *,
    scene: str,
    output: Path,
    limit: int = 140,
    minimum_person_area: float = 0.08,
    exclude_image_ids: set[int] | None = None,
) -> Path:
    """Select caption-grounded COCO context images with a visible native Person box."""

    if scene not in COCO_SCENE_PATTERNS:
        raise ValueError(f"Unsupported COCO context scene: {scene}")
    with annotations_json.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    with captions_json.open(encoding="utf-8") as handle:
        captions_payload = json.load(handle)
    captions_by_image: dict[int, list[str]] = defaultdict(list)
    for annotation in captions_payload["annotations"]:
        captions_by_image[int(annotation["image_id"])].append(str(annotation["caption"]).strip())

    excluded = exclude_image_ids or set()
    categories = {int(item["id"]): str(item["name"]) for item in payload["categories"]}
    images = {int(item["id"]): item for item in payload["images"]}
    annotations_by_image: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for annotation in payload["annotations"]:
        annotations_by_image[int(annotation["image_id"])].append(annotation)
    licenses = {int(item["id"]): str(item.get("url") or "") for item in payload.get("licenses", [])}
    pattern = COCO_SCENE_PATTERNS[scene]
    object_weights = COCO_SCENE_OBJECT_WEIGHTS[scene]
    candidates: list[dict[str, object]] = []
    for image_id, image_annotations in annotations_by_image.items():
        if image_id in excluded:
            continue
        image = images.get(image_id)
        captions = captions_by_image.get(image_id, [])
        caption_hits = [caption for caption in captions if pattern.search(caption)]
        if not image or not caption_hits:
            continue
        width, height = float(image["width"]), float(image["height"])
        person_boxes = []
        for annotation in image_annotations:
            if categories.get(int(annotation["category_id"])) != "person" or annotation.get("iscrowd"):
                continue
            box = _normalized_box(annotation.get("bbox", []), width, height)
            area = float(annotation.get("area") or 0.0) / (width * height)
            if box is not None and area >= minimum_person_area:
                person_boxes.append((box, area))
        if not person_boxes:
            continue
        person_box, person_area = max(person_boxes, key=lambda item: item[1])
        object_names = {
            categories[int(annotation["category_id"])]
            for annotation in image_annotations
            if int(annotation["category_id"]) in categories and categories[int(annotation["category_id"])] != "person"
        }
        context_objects = sorted(object_names.intersection(object_weights))
        score = 12.0 * len(caption_hits) + sum(object_weights[name] for name in context_objects)
        score += min(person_area, 0.5) * 10.0
        candidates.append(
            {
                "image_id": image_id,
                "file_name": image["file_name"],
                "coco_url": image.get("coco_url")
                or f"http://images.cocodataset.org/train2017/{image['file_name']}",
                "flickr_url": image.get("flickr_url") or "",
                "license_url": licenses.get(int(image.get("license") or 0), ""),
                "width": int(width),
                "height": int(height),
                "person_x": person_box.x,
                "person_y": person_box.y,
                "person_width": person_box.width,
                "person_height": person_box.height,
                "person_box_area": person_area,
                "scene": scene,
                "context_objects": ";".join(context_objects),
                "context_score": round(score, 6),
                "caption_hits_json": json.dumps(caption_hits, ensure_ascii=False),
                "captions_json": json.dumps(captions, ensure_ascii=False),
            }
        )
    candidates.sort(
        key=lambda item: (
            -float(item["context_score"]),
            -float(item["person_box_area"]),
            int(item["image_id"]),
        )
    )
    selected = candidates[:limit]
    if len(selected) < limit:
        raise ValueError(f"COCO annotations support only {len(selected)} {scene} candidates; requested {limit}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(selected[0]))
        writer.writeheader()
        writer.writerows(selected)
    return output


def select_coco_home_candidates(
    annotations_json: Path,
    *,
    output: Path,
    limit: int = 220,
    minimum_person_area: float = 0.08,
) -> Path:
    """Select COCO images with a visible person and independently annotated home objects."""

    with annotations_json.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    categories = {int(item["id"]): str(item["name"]) for item in payload["categories"]}
    images = {int(item["id"]): item for item in payload["images"]}
    annotations_by_image: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for annotation in payload["annotations"]:
        annotations_by_image[int(annotation["image_id"])].append(annotation)
    licenses = {int(item["id"]): str(item.get("url") or "") for item in payload.get("licenses", [])}
    candidates: list[dict[str, object]] = []
    for image_id, image_annotations in annotations_by_image.items():
        image = images.get(image_id)
        if not image:
            continue
        width, height = float(image["width"]), float(image["height"])
        people = [
            annotation
            for annotation in image_annotations
            if categories.get(int(annotation["category_id"])) == "person"
        ]
        person_boxes = [
            (_normalized_box(annotation.get("bbox", []), width, height), float(annotation.get("area") or 0.0) / (width * height))
            for annotation in people
            if not annotation.get("iscrowd")
        ]
        person_boxes = [(box, area) for box, area in person_boxes if box is not None and area >= minimum_person_area]
        if not person_boxes:
            continue
        person_box, person_area = max(person_boxes, key=lambda item: item[1])
        object_names = {
            categories[int(annotation["category_id"])]
            for annotation in image_annotations
            if int(annotation["category_id"]) in categories and categories[int(annotation["category_id"])] != "person"
        }
        strong_home = bool(object_names.intersection({"bed", "couch", "refrigerator", "toilet"}))
        kitchen = bool(object_names.intersection({"oven", "microwave"})) and "sink" in object_names
        dining = "dining table" in object_names and bool(object_names.intersection({"chair", "bowl", "cup", "fork", "knife", "spoon"}))
        living = "tv" in object_names and bool(object_names.intersection({"couch", "chair", "remote"}))
        if not (strong_home or kitchen or dining or living):
            continue
        home_objects = sorted(object_names.intersection(COCO_HOME_WEIGHTS))
        score = sum(COCO_HOME_WEIGHTS[name] for name in home_objects) + min(person_area, 0.5) * 10
        score += 3.0 * sum((strong_home, kitchen, dining, living))
        candidates.append(
            {
                "image_id": image_id,
                "file_name": image["file_name"],
                "coco_url": image.get("coco_url") or f"http://images.cocodataset.org/train2017/{image['file_name']}",
                "flickr_url": image.get("flickr_url") or "",
                "license_url": licenses.get(int(image.get("license") or 0), ""),
                "width": int(width),
                "height": int(height),
                "person_x": person_box.x,
                "person_y": person_box.y,
                "person_width": person_box.width,
                "person_height": person_box.height,
                "person_box_area": person_area,
                "home_objects": ";".join(home_objects),
                "home_score": round(score, 6),
            }
        )
    candidates.sort(key=lambda item: (-float(item["home_score"]), -float(item["person_box_area"]), int(item["image_id"])))
    selected = candidates[:limit]
    if len(selected) < limit:
        raise ValueError(f"COCO annotations support only {len(selected)} home candidates; requested {limit}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(selected[0]))
        writer.writeheader()
        writer.writerows(selected)
    return output


def download_coco_context_candidates(
    candidates_csv: Path,
    destination: Path,
    *,
    workers: int = 12,
    timeout: int = 30,
) -> Path:
    """Download a small, preselected COCO subset with image decoding and resumability."""

    destination.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(candidates_csv.open(encoding="utf-8")))

    def fetch(row: dict[str, str]) -> tuple[str, str]:
        target = destination / row["file_name"]
        if target.exists() and target.stat().st_size > 512:
            return str(target), "ok"
        try:
            response = requests.get(
                row["coco_url"],
                timeout=timeout,
                headers={"User-Agent": "Glance-fashion-retrieval/1.0 (educational dataset curation)"},
            )
            response.raise_for_status()
            with Image.open(io.BytesIO(response.content)) as image:
                image.verify()
            target.write_bytes(response.content)
            return str(target), "ok"
        except (OSError, ValueError, requests.RequestException) as exc:
            return "", f"failed: {type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        fetched = list(executor.map(fetch, rows))
    completed = []
    for row, (local_path, status) in zip(rows, fetched, strict=True):
        completed.append({**row, "local_path": local_path, "download_status": status})
    output = destination / "download_manifest.csv"
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(completed[0]))
        writer.writeheader()
        writer.writerows(completed)
    return output


def records_from_coco_context_manifest(manifest_csv: Path) -> list[ImageRecord]:
    """Convert a validated COCO scene subset into the shared context-record contract."""

    records: list[ImageRecord] = []
    for row in csv.DictReader(manifest_csv.open(encoding="utf-8")):
        if row.get("download_status") != "ok" or not row.get("local_path"):
            continue
        box = BoundingBox(
            x=float(row["person_x"]),
            y=float(row["person_y"]),
            width=float(row["person_width"]),
            height=float(row["person_height"]),
        )
        scene = row.get("scene") or "home"
        objects_text = row.get("context_objects") or row.get("home_objects", "")
        objects = [value for value in objects_text.split(";") if value]
        score = float(row.get("context_score") or row.get("home_score") or 0.0)
        native_captions = json.loads(row.get("captions_json") or "[]")
        image_id = f"coco-{int(row['image_id']):012d}"
        records.append(
            ImageRecord(
                image_id=image_id,
                image_path=row["local_path"],
                source="coco",
                scene=scene,
                tags=objects,
                caption=(native_captions[0] if native_captions else f"COCO {scene} candidate: " + ", ".join(objects)),
                confidence="low",
                attribution="COCO 2017" + (f" / {row['license_url']}" if row.get("license_url") else ""),
                extra={
                    "coco_image_id": int(row["image_id"]),
                    "source_url": row.get("coco_url"),
                    "landing_url": row.get("flickr_url"),
                    "license": row.get("license_url"),
                    "scene_score": score,
                    "label_evidence": "; ".join(objects),
                    "person_box_area": float(row["person_box_area"]),
                    "person_box": box.model_dump(mode="json"),
                    "person_box_source": "COCO 2017 native instance annotation",
                    "scene_hypotheses": [
                        {"scene": scene, "score": score, "label_evidence": "; ".join(objects)}
                    ],
                    "candidate_row_count": 1,
                    "native_captions": native_captions,
                    "caption_scene_hits": json.loads(row.get("caption_hits_json") or "[]"),
                },
            )
        )
    return records


def reconcile_context_manifest(
    candidates_csv: Path,
    destination: Path,
    *,
    output: Path | None = None,
) -> Path:
    """Recreate a usable manifest from already-downloaded candidates.

    This makes downloads resumable when a notebook, CI job, or network session is interrupted
    after assets have landed but before the final CSV is written.
    """

    rows = list(csv.DictReader(candidates_csv.open(encoding="utf-8")))
    for row in rows:
        target = destination / f"openimages-{row['ImageID']}.jpg"
        if target.exists() and target.stat().st_size > 512:
            row["local_path"] = str(target)
            row["download_status"] = "ok"
        else:
            row["local_path"] = ""
            row["download_status"] = "missing"
    output = output or destination / "download_manifest.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted({key for row in rows for key in row}))
        writer.writeheader()
        writer.writerows(rows)
    return output


def records_from_context_manifest(manifest_csv: Path) -> list[ImageRecord]:
    """Turn downloaded Open Images candidates into pre-annotation records."""

    records: list[ImageRecord] = []
    for row in csv.DictReader(manifest_csv.open(encoding="utf-8")):
        if row.get("download_status") != "ok" or not row.get("local_path"):
            continue
        image_id = f"openimages-{row['ImageID']}"
        records.append(
            ImageRecord(
                image_id=image_id,
                image_path=row["local_path"],
                source="openimages",
                scene=row.get("scene_hint") or None,
                tags=[tag.strip() for tag in row.get("label_evidence", "").split(";") if tag.strip()],
                caption=(
                    f"Open Images candidate with labels: {row['label_evidence']}"
                    if row.get("label_evidence")
                    else ""
                ),
                confidence="low",
                attribution="Open Images V7" + (f" / {row['AuthorProfileURL']}" if row.get("AuthorProfileURL") else ""),
                extra={
                    "openimages_image_id": row["ImageID"],
                    "license": row.get("License"),
                    "source_url": row.get("OriginalURL") or row.get("image_url"),
                    "landing_url": row.get("OriginalLandingURL"),
                    "scene_score": row.get("scene_score"),
                    "label_evidence": row.get("label_evidence"),
                    "person_box_area": row.get("person_box_area"),
                },
            )
        )
    return deduplicate_context_records(records)


def deduplicate_context_records(records: list[ImageRecord]) -> list[ImageRecord]:
    """Collapse multi-scene candidate rows without discarding their native label evidence."""

    grouped: dict[str, list[ImageRecord]] = defaultdict(list)
    order: list[str] = []
    for record in records:
        if record.image_id not in grouped:
            order.append(record.image_id)
        grouped[record.image_id].append(record)
    deduplicated: list[ImageRecord] = []
    for image_id in order:
        candidates = grouped[image_id]

        def score(record: ImageRecord) -> float:
            try:
                return float(record.extra.get("scene_score") or 0.0)
            except (TypeError, ValueError):
                return 0.0

        base = max(candidates, key=lambda record: (score(record), record.scene or ""))
        hypotheses = sorted(
            [
                {
                    "scene": record.scene,
                    "score": score(record),
                    "label_evidence": record.extra.get("label_evidence"),
                }
                for record in candidates
            ],
            key=lambda item: (-float(item["score"]), str(item["scene"])),
        )
        tags = list(dict.fromkeys(tag for record in candidates for tag in record.tags))
        deduplicated.append(
            base.model_copy(
                update={
                    "tags": tags,
                    "caption": "Open Images candidate with labels: " + "; ".join(tags),
                    "extra": {
                        **base.extra,
                        "scene_hypotheses": hypotheses,
                        "candidate_row_count": len(candidates),
                    },
                }
            )
        )
    return deduplicated


def attach_openimages_person_boxes(
    records: list[ImageRecord],
    box_annotations_csv: Path,
    class_descriptions_csv: Path,
) -> list[ImageRecord]:
    """Attach the largest native Open Images Person box to each local candidate record.

    The full frame is required for scene recognition, while this independently annotated crop gives
    the VLM enough pixels to inspect clothing.  Coordinates remain normalized and provenance stays
    in ``extra``; no VLM-generated localization is treated as ground truth.
    """

    classes = pd.read_csv(class_descriptions_csv, header=None, names=["LabelName", "DisplayName"])
    classes = classes[classes["LabelName"].astype(str) != "LabelName"]
    person_rows = classes[classes["DisplayName"] == "Person"]
    if person_rows.empty:
        raise ValueError("Open Images class descriptions do not include Person")
    person_label = str(person_rows.iloc[0]["LabelName"])
    wanted = {
        str(record.extra.get("openimages_image_id") or record.image_id.removeprefix("openimages-"))
        for record in records
        if record.source == "openimages"
    }
    largest: dict[str, BoundingBox] = {}
    largest_area: dict[str, float] = {}
    columns = ["ImageID", "LabelName", "XMin", "XMax", "YMin", "YMax"]
    for chunk in pd.read_csv(box_annotations_csv, usecols=columns, chunksize=250_000):
        people = chunk[(chunk["LabelName"] == person_label) & (chunk["ImageID"].astype(str).isin(wanted))]
        for row in people.itertuples(index=False):
            x_min, x_max = float(row.XMin), float(row.XMax)
            y_min, y_max = float(row.YMin), float(row.YMax)
            width, height = x_max - x_min, y_max - y_min
            area = width * height
            image_id = str(row.ImageID)
            if width <= 0 or height <= 0 or area <= largest_area.get(image_id, -1.0):
                continue
            largest[image_id] = BoundingBox(x=x_min, y=y_min, width=width, height=height)
            largest_area[image_id] = area
    enriched: list[ImageRecord] = []
    missing: list[str] = []
    for record in records:
        if record.source not in {"openimages", "coco"}:
            enriched.append(record)
            continue
        image_id = str(record.extra.get("openimages_image_id") or record.image_id.removeprefix("openimages-"))
        box = largest.get(image_id)
        if box is None:
            missing.append(record.image_id)
            enriched.append(record)
            continue
        enriched.append(
            record.model_copy(
                update={
                    "extra": {
                        **record.extra,
                        "person_box": box.model_dump(mode="json"),
                        "person_box_area": round(box.width * box.height, 8),
                        "person_box_source": "Open Images native bounding-box annotation",
                    }
                }
            )
        )
    if missing:
        preview = ", ".join(missing[:5])
        raise ValueError(f"Missing native Person boxes for {len(missing)} records ({preview})")
    return enriched


def select_context_records(
    records: list[ImageRecord],
    *,
    per_scene: int = 75,
    minimum_person_area: float = 0.08,
) -> list[ImageRecord]:
    """Select a balanced set with visible people and high-confidence visual scene evidence."""

    expected = {"office", "urban_street", "park", "home"}
    selected: list[ImageRecord] = []
    used_image_ids: set[str] = set()
    confidence_rank = {"audited": 4, "native": 3, "high": 2, "medium": 1, "low": 0}

    def numeric_extra(record: ImageRecord, key: str) -> float:
        try:
            return float(record.extra.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def qa_rank(record: ImageRecord) -> int:
        return {"native_vlm_agreement": 2, "vlm_only": 1}.get(
            str(record.extra.get("context_qa", {}).get("tier") or ""),
            0,
        )

    def visual_scene_metric(record: ImageRecord, key: str) -> float:
        annotation = record.extra.get("vlm_annotation", {})
        try:
            if key == "maximum_probability":
                probabilities = annotation.get("scene_probabilities", {})
                return max((float(value) for value in probabilities.values()), default=0.0)
            return float(annotation.get(key) or 0.0)
        except (AttributeError, TypeError, ValueError):
            return 0.0

    def passes_qa(record: ImageRecord) -> bool:
        qa = record.extra.get("context_qa")
        if isinstance(qa, dict) and not bool(qa.get("accepted")):
            return False
        if record.source not in {"openimages", "coco"}:
            return True
        return numeric_extra(record, "person_box_area") >= minimum_person_area

    def native_environment_rank(record: ImageRecord) -> int:
        if record.source != "openimages" or not record.scene:
            return 0
        return int(bool(set(record.tags).intersection(STRONG_CONTEXT_HINTS.get(record.scene, set()))))

    def source_scene_rank(record: ImageRecord) -> int:
        # Controlled probes are intentionally retained for the disclosed gold set.
        if record.source == "synthetic":
            return 4
        # COCO rows have native Person boxes plus either native home objects or human-caption
        # scene evidence, making them safer than Open Images' broad place/object labels.
        if record.source == "coco":
            return 3
        if native_environment_rank(record):
            return 2
        return 1

    for scene in sorted(expected):
        candidates = [
            record
            for record in records
            if record.scene == scene and record.image_id not in used_image_ids and passes_qa(record)
        ]
        candidates.sort(
            key=lambda record: (
                record.audited,
                source_scene_rank(record),
                native_environment_rank(record),
                visual_scene_metric(record, "scene_margin"),
                visual_scene_metric(record, "maximum_probability"),
                qa_rank(record),
                confidence_rank[record.confidence],
                numeric_extra(record, "scene_score"),
                numeric_extra(record, "person_box_area"),
                record.image_id,
            ),
            reverse=True,
        )
        if len(candidates) < per_scene:
            raise ValueError(f"Need {per_scene} verified {scene} images, but only found {len(candidates)}")
        chosen = candidates[:per_scene]
        selected.extend(chosen)
        used_image_ids.update(record.image_id for record in chosen)
    return selected


def merge_annotation_shards(
    source_records: list[ImageRecord],
    shards: list[list[ImageRecord]],
    *,
    require_success: bool = True,
) -> list[ImageRecord]:
    """Merge resumable annotation shards in the exact order of their source manifest.

    Silent partial merges are especially dangerous here because they can make a small experiment
    look like a complete corpus run.  This function therefore rejects missing, duplicate, unknown,
    or failed records by default.
    """

    source_ids = [record.image_id for record in source_records]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("Source annotation manifest contains duplicate image IDs")
    expected = set(source_ids)
    merged: dict[str, ImageRecord] = {}
    for shard_index, shard in enumerate(shards):
        for record in shard:
            if record.image_id not in expected:
                raise ValueError(f"Annotation shard {shard_index} contains unknown image ID {record.image_id}")
            if record.image_id in merged:
                raise ValueError(f"Duplicate annotated image ID across shards: {record.image_id}")
            status = record.extra.get("vlm_annotation", {}).get("status")
            if require_success and status != "success":
                raise ValueError(f"Annotation did not succeed for {record.image_id}: status={status!r}")
            merged[record.image_id] = record
    missing = [image_id for image_id in source_ids if image_id not in merged]
    if missing:
        preview = ", ".join(missing[:5])
        raise ValueError(f"Annotation shards are incomplete: missing {len(missing)} records ({preview})")
    return [merged[image_id] for image_id in source_ids]


def add_context_quality_evidence(records: list[ImageRecord]) -> list[ImageRecord]:
    """Attach deterministic post-VLM QA evidence without mislabeling it as human audit.

    The acceptance gate checks that inference actually succeeded and that the output contains a
    supported scene, a factual caption, and at least one physical garment.  Native Open Images scene
    agreement is retained as a stronger evidence tier for selection and reporting.
    """

    scenes = {"office", "urban_street", "park", "home"}
    enriched: list[ImageRecord] = []
    for record in records:
        reconciled_scene = reconcile_scene(record.scene, record.caption)
        if reconciled_scene != record.scene:
            record = record.model_copy(update={"scene": reconciled_scene})
        provenance = record.extra.get("vlm_annotation", {})
        candidate_scene = provenance.get("candidate_scene")
        hypothesis_scenes = {
            item.get("scene")
            for item in record.extra.get("scene_hypotheses", [])
            if isinstance(item, dict) and item.get("scene")
        } or {candidate_scene}
        physical_garments = [garment for garment in record.garments if garment.category != "other"]
        reasons: list[str] = []
        if provenance.get("status") != "success":
            reasons.append("vlm_error")
        if record.scene not in scenes:
            reasons.append("unsupported_scene")
        if not record.caption.strip():
            reasons.append("missing_caption")
        if not physical_garments:
            reasons.append("missing_physical_garment")
        if record.confidence == "low":
            reasons.append("low_confidence")
        agreement = record.scene in hypothesis_scenes and record.scene in scenes
        qa = {
            "accepted": not reasons,
            "tier": "native_vlm_agreement" if not reasons and agreement else ("vlm_only" if not reasons else "rejected"),
            "native_scene": candidate_scene,
            "native_scene_hypotheses": sorted(str(scene) for scene in hypothesis_scenes if scene),
            "visual_scene": record.scene,
            "scene_agreement": agreement,
            "reasons": reasons,
        }
        enriched.append(record.model_copy(update={"extra": {**record.extra, "context_qa": qa}}))
    return enriched


def write_audit_sheet(records: list[ImageRecord], output: Path) -> Path:
    """Emit a concise, editable QA CSV; the reviewer only checks uncertain/context-rich records."""

    output.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "image_id",
        "image_path",
        "native_scene",
        "scene",
        "scene_agreement",
        "person_box_area",
        "styles",
        "activities",
        "tags",
        "garments_json",
        "caption",
        "confidence",
        "approved",
        "notes",
    ]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for record in records:
            annotation = record.extra.get("vlm_annotation", {})
            writer.writerow(
                {
                    "image_id": record.image_id,
                    "image_path": record.image_path,
                    "native_scene": annotation.get("candidate_scene") or "",
                    "scene": record.scene or "",
                    "scene_agreement": annotation.get("candidate_scene") == record.scene,
                    "person_box_area": record.extra.get("person_box_area") or "",
                    "styles": ";".join(record.styles),
                    "activities": ";".join(record.activities),
                    "tags": ";".join(record.tags),
                    "garments_json": json.dumps([garment.model_dump(mode="json") for garment in record.garments]),
                    "caption": record.caption,
                    "confidence": record.confidence,
                    "approved": "",
                    "notes": "",
                }
            )
    return output


def render_context_contact_sheets(
    records: list[ImageRecord],
    output_dir: Path,
    *,
    per_page: int = 24,
) -> list[Path]:
    """Render compact, label-evidence contact sheets for a fast visual context audit.

    The sheets are intentionally a review aid, not a source of annotations.  Reviewers use the
    image plus Open Images label evidence to approve/correct the JSONL/CSV records before those
    fields enter the index.
    """

    if per_page < 1:
        raise ValueError("per_page must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    columns = 4 if per_page <= 16 else 6
    rows = max(1, (per_page + columns - 1) // columns)
    cell_width, cell_height = ((300, 275) if per_page <= 16 else (250, 235))
    thumb_height = 158
    sheets: list[Path] = []
    for scene in sorted({record.scene for record in records if record.scene}):
        scene_records = [record for record in records if record.scene == scene and record.path.exists()]
        scene_records.sort(
            key=lambda record: (
                -(float(record.extra.get("scene_score") or 0)),
                record.image_id,
            )
        )
        for page_offset in range(0, len(scene_records), per_page):
            page = scene_records[page_offset : page_offset + per_page]
            canvas = Image.new("RGB", (columns * cell_width, rows * cell_height), "#f6f0e7")
            draw = ImageDraw.Draw(canvas)
            for index, record in enumerate(page):
                x = (index % columns) * cell_width
                y = (index // columns) * cell_height
                draw.rectangle((x + 4, y + 4, x + cell_width - 4, y + cell_height - 4), fill="white", outline="#c8bda9")
                try:
                    with Image.open(record.path) as source:
                        image = source.convert("RGB")
                        image.thumbnail((cell_width - 16, thumb_height), Image.Resampling.LANCZOS)
                        paste_x = x + (cell_width - image.width) // 2
                        canvas.paste(image, (paste_x, y + 8))
                except OSError:
                    draw.text((x + 8, y + 8), "Unreadable image", fill="#a72b2b")
                short_id = str(record.extra.get("openimages_image_id") or record.image_id).replace("openimages-", "")[:16]
                annotation = record.extra.get("vlm_annotation", {})
                candidate_scene = annotation.get("candidate_scene") or "?"
                area = float(record.extra.get("person_box_area") or 0.0)
                draw.text(
                    (x + 8, y + thumb_height + 12),
                    f"{short_id} · {candidate_scene} -> {scene} · person {area:.2f}",
                    fill="#1d2a33",
                )
                garment_text = ", ".join(
                    f"{garment.color or '?'} {garment.category}" for garment in record.garments[:4]
                ) or "No garment"
                context_text = ", ".join([*record.styles, *record.activities]) or "No style/activity"
                draw.multiline_text(
                    (x + 8, y + thumb_height + 32),
                    "\n".join(
                        [
                            *textwrap.wrap(garment_text, width=42)[:2],
                            *textwrap.wrap(context_text, width=42)[:1],
                            *textwrap.wrap(record.caption, width=42)[:2],
                        ]
                    ),
                    fill="#50606b",
                    spacing=2,
                )
            page_number = page_offset // per_page + 1
            target = output_dir / f"{scene}-{page_number:02d}.jpg"
            canvas.save(target, quality=88)
            sheets.append(target)
    return sheets


def apply_audit_sheet(records: list[ImageRecord], audit_sheet: Path) -> list[ImageRecord]:
    """Apply explicit reviewer corrections without losing provenance from the original model pass."""

    rows = {row["image_id"]: row for row in csv.DictReader(audit_sheet.open(encoding="utf-8")) if row.get("image_id")}
    merged: list[ImageRecord] = []
    truthy = {"1", "true", "yes", "y", "approved"}
    for record in records:
        row = rows.get(record.image_id)
        if not row:
            merged.append(record)
            continue
        update: dict[str, Any] = {}
        for field in ("scene", "caption"):
            if row.get(field, "").strip():
                update[field] = row[field].strip()
        for field in ("styles", "activities"):
            if row.get(field, "").strip():
                update[field] = [value.strip() for value in row[field].split(";") if value.strip()]
        if row.get("garments_json", "").strip():
            try:
                update["garments"] = [Garment.model_validate(item) for item in json.loads(row["garments_json"])]
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"Invalid garments_json for {record.image_id}") from exc
        if row.get("approved", "").strip().lower() in truthy:
            update.update({"audited": True, "confidence": "audited"})
        merged.append(record.model_copy(update=update))
    return merged


def write_corpus(records: list[ImageRecord], path: Path) -> None:
    if len({record.image_id for record in records}) != len(records):
        raise ValueError("corpus contains duplicate image IDs")
    write_jsonl(path, records)

"""Part A: transform curated image records into multi-vector Qdrant points."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from PIL import Image

from .embeddings import EncoderPair
from .schemas import BoundingBox, Garment, ImageRecord
from .store import VectorStore

T = TypeVar("T")


def _batches(items: list[T], batch_size: int) -> list[list[T]]:
    return [items[offset : offset + batch_size] for offset in range(0, len(items), batch_size)]


@dataclass(frozen=True)
class IndexStats:
    image_points: int
    garment_points: int
    skipped_without_crop: int


def _persist_crop(record: ImageRecord, box: BoundingBox, target: Path) -> Path | None:
    if target.exists():
        return target
    with Image.open(record.image_path) as image:
        width, height = image.size
        left, top = int(box.x * width), int(box.y * height)
        right, bottom = int((box.x + box.width) * width), int((box.y + box.height) * height)
        crop = image.convert("RGB").crop((left, top, right, bottom))
        if min(crop.size) < 8:
            return None
        crop.save(target, quality=92)
    return target


def create_garment_crop(record: ImageRecord, garment: Garment, crop_dir: Path) -> Path | None:
    """Persist exact garment evidence or an explicitly marked native person-focus fallback."""

    if not Path(record.image_path).exists():
        return None
    crop_dir.mkdir(parents=True, exist_ok=True)
    if garment.bbox:
        return _persist_crop(record, garment.bbox, crop_dir / f"{record.image_id}__{garment.id}.jpg")
    person_box = record.extra.get("person_box")
    if record.source in {"openimages", "coco"} and person_box:
        try:
            box = BoundingBox.model_validate(person_box)
        except ValueError:
            return None
        return _persist_crop(record, box, crop_dir / f"{record.image_id}__native-person-focus.jpg")
    return None


def _image_payload(record: ImageRecord) -> dict[str, object]:
    return {
        "image_id": record.image_id,
        "scene": record.scene,
        "source": record.source,
        "styles": record.styles,
        "activities": record.activities,
        "garment_categories": [garment.category for garment in record.garments],
        "garment_colors": [garment.color for garment in record.garments if garment.color],
        "audited": record.audited,
    }


def index_records(
    records: list[ImageRecord],
    *,
    store: VectorStore,
    encoders: EncoderPair,
    crop_dir: Path,
    recreate: bool = False,
    batch_size: int = 32,
    progress: Callable[[str, int, int], None] | None = None,
) -> tuple[list[ImageRecord], IndexStats]:
    """Create batched whole-image/crop embeddings, returning crop-enriched records.

    Batching makes encoding and Qdrant writes viable for a six- or seven-figure corpus without
    changing retrieval semantics.  ``batch_size`` can be lowered for constrained GPUs/CPUs.
    """

    if not records:
        raise ValueError("cannot index an empty corpus")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    store.initialize(
        generic_dimension=encoders.generic.dimension,
        fashion_dimension=encoders.fashion.dimension,
        recreate=recreate,
    )
    for record in records:
        image_path = record.path
        if not image_path.exists():
            raise FileNotFoundError(f"Image missing for {record.image_id}: {image_path}")

    indexed_images = 0
    for batch in _batches(records, batch_size):
        paths = [record.path for record in batch]
        generic_vectors = encoders.generic.embed_images(paths)
        fashion_vectors = encoders.fashion.embed_images(paths)
        store.upsert_images(
            [
                (record.image_id, {"generic": generic_vectors[index], "fashion": fashion_vectors[index]}, _image_payload(record))
                for index, record in enumerate(batch)
            ]
        )
        indexed_images += len(batch)
        if progress:
            progress("whole-image embeddings", indexed_images, len(records))

    enriched: list[ImageRecord] = []
    crop_tasks: list[tuple[ImageRecord, Garment, Path]] = []
    skipped = 0
    for record in records:
        updated_garments: list[Garment] = []
        for garment in record.garments:
            crop_path = create_garment_crop(record, garment, crop_dir)
            if crop_path is None:
                skipped += 1
                updated_garments.append(garment)
                continue
            updated = garment.model_copy(update={"crop_path": str(crop_path)})
            crop_tasks.append((record, updated, crop_path))
            updated_garments.append(updated)
        enriched.append(record.model_copy(update={"garments": updated_garments}))

    crop_vectors: dict[Path, object] = {}
    unique_crop_paths = list(dict.fromkeys(crop_path for _, _, crop_path in crop_tasks))
    embedded_crops = 0
    for paths in _batches(unique_crop_paths, batch_size):
        vectors = encoders.fashion.embed_images(paths)
        crop_vectors.update(zip(paths, vectors, strict=True))
        embedded_crops += len(paths)
        if progress:
            progress("unique crop embeddings", embedded_crops, len(unique_crop_paths))
    upserted_garments = 0
    for batch in _batches(crop_tasks, batch_size):
        store.upsert_garments(
            [
                (
                    garment.id,
                    crop_vectors[crop_path],
                    {
                        "image_id": record.image_id,
                        "category": garment.category,
                        "color": garment.color,
                        "attributes": garment.attributes,
                        "crop_path": garment.crop_path,
                        "crop_scope": "garment" if garment.bbox else "native_person",
                    },
                )
                for record, garment, crop_path in batch
            ]
        )
        upserted_garments += len(batch)
        if progress:
            progress("garment points", upserted_garments, len(crop_tasks))
    return enriched, IndexStats(len(records), len(crop_tasks), skipped)

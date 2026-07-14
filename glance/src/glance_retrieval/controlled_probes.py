"""Small, explicitly disclosed synthetic probes for compositional acceptance testing.

These five images are not training data and are never presented as a benchmark result.  They are
held-out visual sanity checks for the assignment prompts that are sparsely represented in public
image-level labels (most notably ``red tie + white shirt`` and ``yellow raincoat``).
"""

from __future__ import annotations

from pathlib import Path

from .schemas import BoundingBox, Garment, ImageRecord


def _box(x: float, y: float, width: float, height: float) -> BoundingBox:
    return BoundingBox(x=x, y=y, width=width, height=height)


def build_controlled_probe_records(images_dir: Path) -> list[ImageRecord]:
    """Build records for project-local generated images after their visual inspection.

    The approximate boxes are deliberately included so the same crop-indexing path used for
    Fashionpedia can exercise local garment retrieval on the compositional probes.
    """

    definitions: list[dict[str, object]] = [
        {
            "slug": "yellow-raincoat",
            "file": "controlled-yellow-raincoat.png",
            "scene": "urban_street",
            "activities": ["walking"],
            "styles": ["outerwear", "casual"],
            "caption": "A person in a bright yellow raincoat walking on a wet city sidewalk.",
            "garments": [
                ("coat", "yellow", _box(0.23, 0.17, 0.54, 0.48)),
                ("pants", "black", _box(0.31, 0.62, 0.39, 0.29)),
                ("shoes", "gray", _box(0.34, 0.84, 0.31, 0.10)),
            ],
        },
        {
            "slug": "modern-office-business",
            "file": "controlled-modern-office-business.png",
            "scene": "office",
            "activities": ["standing"],
            "styles": ["formal"],
            "caption": "Professional business attire inside a modern office.",
            "garments": [
                ("blazer", "blue", _box(0.16, 0.19, 0.54, 0.42)),
                ("shirt", "white", _box(0.31, 0.22, 0.32, 0.29)),
                ("pants", "blue", _box(0.29, 0.51, 0.40, 0.35)),
                ("shoes", "brown", _box(0.34, 0.84, 0.34, 0.10)),
            ],
        },
        {
            "slug": "blue-shirt-park-bench",
            "file": "controlled-blue-shirt-park-bench.png",
            "scene": "park",
            "activities": ["sitting"],
            "styles": ["casual"],
            "caption": "Someone wearing a blue shirt sitting on a park bench.",
            "garments": [
                ("shirt", "blue", _box(0.10, 0.34, 0.62, 0.30)),
                ("pants", "black", _box(0.30, 0.57, 0.42, 0.27)),
                ("shoes", "white", _box(0.46, 0.72, 0.35, 0.17)),
            ],
        },
        {
            "slug": "casual-city-walk",
            "file": "controlled-casual-city-walk.png",
            "scene": "urban_street",
            "activities": ["walking"],
            "styles": ["casual"],
            "caption": "A casual weekend outfit for a city walk.",
            "garments": [
                ("hoodie", "green", _box(0.18, 0.18, 0.61, 0.34)),
                ("t-shirt", "white", _box(0.33, 0.26, 0.30, 0.26)),
                ("pants", "blue", _box(0.30, 0.48, 0.42, 0.35)),
                ("shoes", "white", _box(0.36, 0.83, 0.29, 0.11)),
            ],
        },
        {
            "slug": "red-tie-white-shirt",
            "file": "controlled-red-tie-white-shirt.png",
            "scene": "office",
            "activities": ["standing"],
            "styles": ["formal"],
            "caption": "A red tie and a white shirt in a formal office setting.",
            "garments": [
                ("blazer", "black", _box(0.20, 0.18, 0.59, 0.45)),
                ("shirt", "white", _box(0.31, 0.20, 0.32, 0.30)),
                ("tie", "red", _box(0.43, 0.26, 0.13, 0.26)),
                ("pants", "black", _box(0.29, 0.50, 0.42, 0.36)),
                ("shoes", "black", _box(0.34, 0.85, 0.33, 0.10)),
            ],
        },
    ]
    records: list[ImageRecord] = []
    for item in definitions:
        path = images_dir / str(item["file"])
        if not path.exists():
            raise FileNotFoundError(f"Controlled probe asset is missing: {path}")
        slug = str(item["slug"])
        garments = [
            Garment(
                id=f"controlled-{slug}-{index}",
                category=category,
                color=color,
                bbox=bbox,
                confidence="audited",
            )
            for index, (category, color, bbox) in enumerate(item["garments"])
        ]
        records.append(
            ImageRecord(
                image_id=f"controlled-{slug}",
                image_path=str(path),
                source="synthetic",
                scene=str(item["scene"]),
                activities=list(item["activities"]),
                styles=list(item["styles"]),
                garments=garments,
                caption=str(item["caption"]),
                tags=["controlled-probe", "held-out", "synthetic"],
                confidence="audited",
                audited=True,
                attribution="Controlled synthetic evaluation probe; prompt and manual review documented in the repository.",
                extra={"role": "held_out_controlled_probe", "training_excluded": True},
            )
        )
    return records

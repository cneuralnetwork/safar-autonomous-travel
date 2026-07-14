"""Tiny self-contained fixture corpus for demoing the site without model downloads."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .embeddings import DeterministicEmbedder, EncoderPair
from .indexer import index_records
from .retrieval import AttributeAwareRetriever
from .schemas import BoundingBox, Garment, ImageRecord
from .service import RetrievalService
from .store import InMemoryVectorStore


def _draw_look(path: Path, *, background: tuple[int, int, int], top: tuple[int, int, int], bottom: tuple[int, int, int], accent: tuple[int, int, int] | None = None) -> None:
    image = Image.new("RGB", (480, 640), background)
    draw = ImageDraw.Draw(image)
    # Intentional editorial abstraction, rather than pretending these are source dataset photos.
    draw.ellipse((186, 80, 294, 188), fill=(205, 164, 126))
    draw.rounded_rectangle((128, 180, 352, 420), radius=20, fill=top)
    draw.polygon([(162, 420), (250, 410), (242, 610), (140, 610)], fill=bottom)
    draw.polygon([(252, 410), (338, 420), (360, 610), (258, 610)], fill=bottom)
    if accent:
        draw.polygon([(235, 190), (265, 190), (258, 356), (250, 386), (242, 356)], fill=accent)
    draw.line((76, 556, 404, 556), fill=(255, 255, 255), width=3)
    image.save(path, quality=92)


def build_fixture_service(work_dir: Path) -> RetrievalService:
    """Build eight deterministic looks to verify the complete browser flow locally."""

    image_dir = work_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        ("formal-red-tie-white-shirt-office", "office", ["formal"], ["standing"], [("tie", "red"), ("shirt", "white")], (76, 88, 102), (245, 244, 237), (41, 44, 50), (193, 49, 49)),
        ("formal-blue-tie-red-shirt-office", "office", ["formal"], ["standing"], [("tie", "blue"), ("shirt", "red")], (76, 88, 102), (188, 58, 53), (42, 44, 50), (50, 102, 179)),
        ("yellow-raincoat-urban-street", "urban_street", ["outerwear", "casual"], ["walking"], [("coat", "yellow")], (90, 100, 104), (225, 194, 41), (45, 59, 76), None),
        ("casual-blue-shirt-park-bench", "park", ["casual"], ["sitting"], [("shirt", "blue"), ("pants", "black")], (91, 133, 93), (50, 102, 179), (31, 33, 35), None),
        ("casual-hoodie-home", "home", ["casual"], ["sitting"], [("hoodie", "gray"), ("pants", "blue")], (181, 151, 121), (130, 132, 135), (50, 102, 179), None),
        ("formal-blazer-office", "office", ["formal"], ["standing"], [("blazer", "black"), ("shirt", "white")], (76, 88, 102), (27, 29, 32), (44, 46, 48), None),
        ("casual-green-jacket-city", "urban_street", ["casual", "outerwear"], ["walking"], [("jacket", "green"), ("pants", "blue")], (98, 107, 116), (61, 132, 76), (50, 102, 179), None),
        ("pink-cardigan-home", "home", ["casual"], ["standing"], [("cardigan", "pink"), ("skirt", "black")], (186, 163, 145), (215, 112, 150), (31, 33, 35), None),
    ]
    records: list[ImageRecord] = []
    for name, scene, styles, activities, garment_specs, background, top, bottom, accent in specs:
        path = image_dir / f"{name}.jpg"
        _draw_look(path, background=background, top=top, bottom=bottom, accent=accent)
        records.append(
            ImageRecord(
                image_id=name,
                image_path=str(path),
                source="fixture",
                scene=scene,
                styles=styles,
                activities=activities,
                garments=[
                    Garment(
                        id=f"{name}-{category}-{index}",
                        category=category,
                        color=color,
                        bbox=BoundingBox(x=0.26, y=0.28, width=0.48, height=0.43),
                        confidence="audited",
                    )
                    for index, (category, color) in enumerate(garment_specs)
                ],
                caption=name.replace("-", " "),
                audited=True,
                confidence="audited",
            )
        )
    store = InMemoryVectorStore()
    encoders = EncoderPair(DeterministicEmbedder(128), DeterministicEmbedder(128))
    indexed, _ = index_records(records, store=store, encoders=encoders, crop_dir=work_dir / "crops", recreate=True)
    retriever = AttributeAwareRetriever(records=indexed, store=store, encoders=encoders)
    return RetrievalService(
        retriever=retriever,
        records={record.image_id: record for record in indexed},
        model_profile="Deterministic visual fixture",
    )

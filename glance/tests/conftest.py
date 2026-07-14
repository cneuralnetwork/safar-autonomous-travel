from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from glance_retrieval.embeddings import DeterministicEmbedder, EncoderPair
from glance_retrieval.indexer import index_records
from glance_retrieval.retrieval import AttributeAwareRetriever
from glance_retrieval.schemas import BoundingBox, Garment, ImageRecord
from glance_retrieval.store import InMemoryVectorStore


@pytest.fixture
def retrieval_fixture(tmp_path: Path):
    image_dir = tmp_path / "images"
    image_dir.mkdir()

    def record(name: str, *, scene: str, styles: list[str], activities: list[str], garments: list[tuple[str, str]]) -> ImageRecord:
        path = image_dir / f"{name}.jpg"
        Image.new("RGB", (120, 160), color=(120, 120, 120)).save(path)
        return ImageRecord(
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
                    bbox=BoundingBox(x=0, y=0, width=1, height=1),
                    confidence="audited",
                )
                for index, (category, color) in enumerate(garments)
            ],
            caption=f"{name.replace('-', ' ')}",
            audited=True,
            confidence="audited",
        )

    records = [
        record(
            "formal-red-tie-white-shirt-office",
            scene="office",
            styles=["formal"],
            activities=["standing"],
            garments=[("tie", "red"), ("shirt", "white")],
        ),
        record(
            "formal-blue-tie-red-shirt-office",
            scene="office",
            styles=["formal"],
            activities=["standing"],
            garments=[("tie", "blue"), ("shirt", "red")],
        ),
        record(
            "casual-blue-shirt-park-bench",
            scene="park",
            styles=["casual"],
            activities=["sitting"],
            garments=[("shirt", "blue"), ("pants", "black")],
        ),
        record(
            "yellow-raincoat-urban-street",
            scene="urban_street",
            styles=["outerwear", "casual"],
            activities=["walking"],
            garments=[("coat", "yellow")],
        ),
    ]
    store = InMemoryVectorStore()
    encoders = EncoderPair(DeterministicEmbedder(128), DeterministicEmbedder(128))
    indexed_records, _ = index_records(records, store=store, encoders=encoders, crop_dir=tmp_path / "crops", recreate=True)
    retriever = AttributeAwareRetriever(records=indexed_records, store=store, encoders=encoders)
    return retriever, indexed_records


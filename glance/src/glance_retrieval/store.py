"""Qdrant production store and a deliberately small in-memory reference store for tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

import numpy as np

IMAGE_COLLECTION = "glance_images"
GARMENT_COLLECTION = "glance_garments"


@dataclass(frozen=True)
class VectorHit:
    point_id: str
    score: float
    payload: dict[str, Any]


class VectorStore(Protocol):
    def initialize(self, *, generic_dimension: int, fashion_dimension: int, recreate: bool = False) -> None: ...

    def upsert_image(self, point_id: str, vectors: dict[str, np.ndarray], payload: dict[str, Any]) -> None: ...

    def upsert_garment(self, point_id: str, vector: np.ndarray, payload: dict[str, Any]) -> None: ...

    def upsert_images(self, points: list[tuple[str, dict[str, np.ndarray], dict[str, Any]]]) -> None: ...

    def upsert_garments(self, points: list[tuple[str, np.ndarray, dict[str, Any]]]) -> None: ...

    def search_images(self, vector_name: str, vector: np.ndarray, limit: int) -> list[VectorHit]: ...

    def search_garments(self, vector: np.ndarray, limit: int) -> list[VectorHit]: ...

    def close(self) -> None: ...


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(left, right) / max(float(np.linalg.norm(left) * np.linalg.norm(right)), 1e-12))


class InMemoryVectorStore:
    """Reference semantics for unit/integration tests, not a production substitute for Qdrant."""

    def __init__(self) -> None:
        self.images: dict[str, tuple[dict[str, np.ndarray], dict[str, Any]]] = {}
        self.garments: dict[str, tuple[np.ndarray, dict[str, Any]]] = {}

    def initialize(self, *, generic_dimension: int, fashion_dimension: int, recreate: bool = False) -> None:
        if recreate:
            self.images.clear()
            self.garments.clear()

    def upsert_image(self, point_id: str, vectors: dict[str, np.ndarray], payload: dict[str, Any]) -> None:
        self.images[point_id] = ({key: np.asarray(value, dtype=np.float32) for key, value in vectors.items()}, payload)

    def upsert_garment(self, point_id: str, vector: np.ndarray, payload: dict[str, Any]) -> None:
        self.garments[point_id] = (np.asarray(vector, dtype=np.float32), payload)

    def upsert_images(self, points: list[tuple[str, dict[str, np.ndarray], dict[str, Any]]]) -> None:
        for point_id, vectors, payload in points:
            self.upsert_image(point_id, vectors, payload)

    def upsert_garments(self, points: list[tuple[str, np.ndarray, dict[str, Any]]]) -> None:
        for point_id, vector, payload in points:
            self.upsert_garment(point_id, vector, payload)

    @staticmethod
    def _search(entries: dict[str, tuple[Any, dict[str, Any]]], vector: np.ndarray, limit: int, name: str | None = None) -> list[VectorHit]:
        hits = []
        for point_id, (stored, payload) in entries.items():
            target = stored[name] if name else stored
            hits.append(VectorHit(point_id, _cosine(vector, target), payload))
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit]

    def search_images(self, vector_name: str, vector: np.ndarray, limit: int) -> list[VectorHit]:
        return self._search(self.images, vector, limit, vector_name)

    def search_garments(self, vector: np.ndarray, limit: int) -> list[VectorHit]:
        return self._search(self.garments, vector, limit)

    def close(self) -> None:
        """Keep the test-store lifecycle compatible with Qdrant."""

        return None


class QdrantVectorStore:
    """Named-vector Qdrant implementation with filterable payload fields for scale."""

    def __init__(self, url: str) -> None:
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:  # pragma: no cover - dependency is installed by project setup
            raise RuntimeError("Install qdrant-client before starting the retrieval service.") from exc
        self._is_local = not url.startswith(("http://", "https://"))
        self.client = QdrantClient(url=url) if not self._is_local else QdrantClient(path=url)

    @staticmethod
    def _point_id(point_id: str) -> str:
        """Qdrant accepts unsigned integers or UUIDs, not arbitrary application strings."""

        return str(uuid5(NAMESPACE_URL, f"glance/{point_id}"))

    def initialize(self, *, generic_dimension: int, fashion_dimension: int, recreate: bool = False) -> None:
        from qdrant_client import models

        for collection in (IMAGE_COLLECTION, GARMENT_COLLECTION):
            if recreate and self.client.collection_exists(collection):
                self.client.delete_collection(collection)
        if not self.client.collection_exists(IMAGE_COLLECTION):
            self.client.create_collection(
                collection_name=IMAGE_COLLECTION,
                vectors_config={
                    "generic": models.VectorParams(size=generic_dimension, distance=models.Distance.COSINE, on_disk=True),
                    "fashion": models.VectorParams(size=fashion_dimension, distance=models.Distance.COSINE, on_disk=True),
                },
                hnsw_config=models.HnswConfigDiff(m=24, ef_construct=128),
            )
        if not self.client.collection_exists(GARMENT_COLLECTION):
            self.client.create_collection(
                collection_name=GARMENT_COLLECTION,
                vectors_config=models.VectorParams(size=fashion_dimension, distance=models.Distance.COSINE, on_disk=True),
                hnsw_config=models.HnswConfigDiff(m=24, ef_construct=128),
            )
        # Embedded Qdrant intentionally does not implement payload indexes. Avoid emitting a
        # misleading warning in the offline demo; server Qdrant receives the scalable indexes.
        if not self._is_local:
            for field in ("scene", "source", "styles", "activities", "garment_categories", "garment_colors"):
                self.client.create_payload_index(
                    collection_name=IMAGE_COLLECTION, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD
                )
            for field in ("image_id", "category", "color"):
                self.client.create_payload_index(
                    collection_name=GARMENT_COLLECTION, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD
                )

    def upsert_image(self, point_id: str, vectors: dict[str, np.ndarray], payload: dict[str, Any]) -> None:
        from qdrant_client import models

        self.client.upsert(
            collection_name=IMAGE_COLLECTION,
            points=[models.PointStruct(id=self._point_id(point_id), vector={key: value.tolist() for key, value in vectors.items()}, payload=payload)],
            wait=True,
        )

    def upsert_garment(self, point_id: str, vector: np.ndarray, payload: dict[str, Any]) -> None:
        from qdrant_client import models

        self.client.upsert(
            collection_name=GARMENT_COLLECTION,
            points=[models.PointStruct(id=self._point_id(point_id), vector=vector.tolist(), payload=payload)],
            wait=True,
        )

    def upsert_images(self, points: list[tuple[str, dict[str, np.ndarray], dict[str, Any]]]) -> None:
        """Write an embedding batch in a single Qdrant request for ingestion throughput."""

        if not points:
            return
        from qdrant_client import models

        self.client.upsert(
            collection_name=IMAGE_COLLECTION,
            points=[
                models.PointStruct(
                    id=self._point_id(point_id),
                    vector={key: value.tolist() for key, value in vectors.items()},
                    payload=payload,
                )
                for point_id, vectors, payload in points
            ],
            wait=True,
        )

    def upsert_garments(self, points: list[tuple[str, np.ndarray, dict[str, Any]]]) -> None:
        """Write localized crop embeddings in batches rather than one network round trip each."""

        if not points:
            return
        from qdrant_client import models

        self.client.upsert(
            collection_name=GARMENT_COLLECTION,
            points=[
                models.PointStruct(id=self._point_id(point_id), vector=vector.tolist(), payload=payload)
                for point_id, vector, payload in points
            ],
            wait=True,
        )

    @staticmethod
    def _hits(response: Any) -> list[VectorHit]:
        points = getattr(response, "points", response)
        return [VectorHit(str(point.id), float(point.score), dict(point.payload or {})) for point in points]

    def search_images(self, vector_name: str, vector: np.ndarray, limit: int) -> list[VectorHit]:
        response = self.client.query_points(
            collection_name=IMAGE_COLLECTION, query=vector.tolist(), using=vector_name, limit=limit, with_payload=True
        )
        return self._hits(response)

    def search_garments(self, vector: np.ndarray, limit: int) -> list[VectorHit]:
        response = self.client.query_points(
            collection_name=GARMENT_COLLECTION, query=vector.tolist(), limit=limit, with_payload=True
        )
        return self._hits(response)

    def close(self) -> None:
        """Flush and release embedded Qdrant before interpreter shutdown."""

        self.client.close()

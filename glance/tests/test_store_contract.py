from uuid import UUID

from glance_retrieval.store import QdrantVectorStore


def test_qdrant_application_ids_are_stable_uuids():
    first = QdrantVectorStore._point_id("fashionpedia-42")
    assert first == QdrantVectorStore._point_id("fashionpedia-42")
    assert first != QdrantVectorStore._point_id("fashionpedia-43")
    assert str(UUID(first)) == first


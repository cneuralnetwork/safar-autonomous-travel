import asyncio

import httpx

from glance_retrieval.api import create_app
from glance_retrieval.service import RetrievalService


def test_api_search_and_media_endpoint(retrieval_fixture):
    retriever, records = retrieval_fixture
    app = create_app(RetrievalService(retriever=retriever, records={record.image_id: record for record in records}))

    async def run() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            home = await client.get("/")
            assert home.status_code == 200
            assert "GLANCE" in home.text
            assert "runtime-pill" in home.text
            assert "result-dialog" in home.text
            health = await client.get("/api/health")
            assert health.status_code == 200
            assert health.json() == {
                "status": "ready",
                "index_loaded": True,
                "corpus_size": len(records),
                "model_profile": "Generic CLIP + FashionCLIP",
                "backend": "qdrant-server",
                "device": "auto",
            }
            response = await client.post("/api/search", json={"query": "A bright yellow raincoat", "k": 2})
            assert response.status_code == 200
            payload = response.json()
            assert payload["results"]
            assert payload["elapsed_ms"] >= 0
            assert payload["model_profile"] == "Generic CLIP + FashionCLIP"
            assert payload["results"][0]["image_url"].startswith("/api/images/")
            image = await client.get(payload["results"][0]["image_url"])
            assert image.status_code == 200
            assert image.headers["content-type"].startswith("image/")

    asyncio.run(run())

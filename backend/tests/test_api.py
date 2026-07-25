from httpx import ASGITransport, AsyncClient

from app.main import app


async def test_application_starts_and_reports_health() -> None:
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        response = await client.get("/health")
        origin = "http://localhost:8081"
        conversation_path = "/v1/conversations/not-a-uuid"
        preflight = await client.options(
            conversation_path,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["environment"] == "test"
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert "authorization" in preflight.headers["access-control-allow-headers"]

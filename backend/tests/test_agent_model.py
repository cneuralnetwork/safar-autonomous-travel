import json

import httpx

from app.agent_model import SarvamGateway, TravelConstraintPatch
from app.config import Settings


async def test_sarvam_gateway_retries_transient_errors_and_records_usage() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                json={"error": {"code": "rate_limit_exceeded_error", "message": "slow down"}},
            )
        return httpx.Response(
            200,
            json={
                "model": "sarvam-105b",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                TravelConstraintPatch(
                                    origin="Kolkata",
                                    destination="Goa",
                                ).model_dump(mode="json")
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 8,
                    "total_tokens": 20,
                },
            },
        )

    gateway = SarvamGateway(Settings(sarvam_api_key="test-key"))
    await gateway.client.aclose()
    gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await gateway.structured(
        phase="test",
        prompt_version="test-v1",
        schema=TravelConstraintPatch,
        system="Return the schema.",
        payload={"message": "Kolkata to Goa"},
        reasoning_effort=None,
    )

    assert result.value.origin == "Kolkata"
    assert result.metrics.attempts == 2
    assert result.metrics.total_tokens == 20
    await gateway.close()

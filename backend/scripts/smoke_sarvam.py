"""Run a credential-safe Sarvam interpretation smoke test.

The script intentionally prints only interpreted travel fields and model telemetry.
It never prints request headers, credentials, or raw provider responses.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.interpreter import RequestInterpreter


async def main() -> None:
    settings = Settings()
    if not settings.sarvam_api_key:
        raise RuntimeError("SARVAM_API_KEY is not configured")

    interpreter = RequestInterpreter(settings)
    try:
        outcome = await interpreter.interpret_turn(
            "I wanna go to Jaipur from Chennai, budget 40000, next weekend",
        )
    finally:
        await interpreter.agent.gateway.close()

    metrics = outcome.model_metrics
    if metrics is None:
        raise RuntimeError("Sarvam was not called")
    if outcome.constraints.missing_fields:
        raise RuntimeError(
            f"Unexpected missing fields: {outcome.constraints.missing_fields}"
        )

    print(
        json.dumps(
            {
                "origin": outcome.constraints.origin,
                "destination": outcome.constraints.destination,
                "start_date": str(outcome.constraints.start_date),
                "end_date": str(outcome.constraints.end_date),
                "budget": outcome.constraints.budget,
                "model": metrics.model,
                "status": metrics.status,
                "attempts": metrics.attempts,
                "latency_ms": metrics.latency_ms,
                "total_tokens": metrics.total_tokens,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())

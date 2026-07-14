"""ASGI entry point for the deterministic browser-verification fixture."""

from pathlib import Path

from .api import create_app
from .demo import build_fixture_service

app = create_app(build_fixture_service(Path(__file__).resolve().parent / "static" / "generated" / "demo_fixture"))


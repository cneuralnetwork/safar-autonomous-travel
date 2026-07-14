"""FastAPI application for the lightweight Glance demo website."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .schemas import SearchRequest, SearchResponse
from .service import RetrievalService
from .settings import Settings, get_settings


def create_app(service: RetrievalService | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        yield
        if application.state.service is not None:
            application.state.service.close()

    app = FastAPI(
        title="Glance Fashion Retrieval",
        version="0.1.0",
        description="Attribute-aware fashion and context retrieval with localized garment evidence.",
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

    async def get_service() -> RetrievalService:
        if app.state.service is None:
            try:
                app.state.service = RetrievalService.from_settings(app.state.settings)
            except Exception as exc:  # pragma: no cover - operational setup error
                raise HTTPException(status_code=503, detail=f"Search index unavailable: {exc}") from exc
        return app.state.service

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home() -> str:
        return (settings.static_dir / "index.html").read_text(encoding="utf-8")

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        active = app.state.service
        corpus_size: int | None = len(active.records) if active else None
        if corpus_size is None and settings.resolved_records_path.is_file():
            with settings.resolved_records_path.open(encoding="utf-8") as handle:
                corpus_size = sum(1 for line in handle if line.strip())
        backend = "qdrant-server" if settings.qdrant_url.startswith(("http://", "https://")) else "embedded-qdrant"
        profile = active.model_profile if active else "Generic CLIP + FashionCLIP"
        if active is None and settings.fashion_adapter:
            profile += " · measured LoRA"
        return {
            "status": "ready" if active is not None else "standby",
            "index_loaded": active is not None,
            "corpus_size": corpus_size,
            "model_profile": profile,
            "backend": backend,
            "device": settings.device,
        }

    @app.post("/api/search", response_model=SearchResponse)
    async def search(request: SearchRequest, active_service: RetrievalService = Depends(get_service)) -> SearchResponse:
        return active_service.search(request)

    @app.get("/api/images/{image_id}", include_in_schema=False)
    async def image(image_id: str, active_service: RetrievalService = Depends(get_service)) -> Response:
        record = active_service.records.get(image_id)
        if not record:
            raise HTTPException(status_code=404, detail="Unknown image")
        path = Path(record.image_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Image file is unavailable")
        media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return Response(content=path.read_bytes(), media_type=media_type)

    return app


app = create_app()

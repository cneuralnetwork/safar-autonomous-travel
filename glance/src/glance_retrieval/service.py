"""Application composition root; production models are loaded only when the API starts."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from .embeddings import make_encoder_pair
from .io import read_jsonl
from .retrieval import AttributeAwareRetriever, QwenIntentParser, RuleIntentParser
from .schemas import ImageRecord, SearchRequest, SearchResponse
from .settings import Settings
from .store import QdrantVectorStore


@dataclass
class RetrievalService:
    retriever: AttributeAwareRetriever
    records: dict[str, ImageRecord]
    model_profile: str = "Generic CLIP + FashionCLIP"

    @classmethod
    def from_settings(cls, settings: Settings) -> RetrievalService:
        records_list = read_jsonl(settings.resolved_records_path, ImageRecord)
        encoders = make_encoder_pair(
            settings.generic_model,
            settings.fashion_model,
            settings.device,
            settings.fashion_adapter,
        )
        parser = QwenIntentParser(settings.query_model) if settings.use_qwen_parser else RuleIntentParser()
        retriever = AttributeAwareRetriever(
            records=records_list,
            store=QdrantVectorStore(settings.qdrant_url),
            encoders=encoders,
            parser=parser,
        )
        profile = "Generic CLIP + FashionCLIP"
        if settings.fashion_adapter:
            profile += " · measured LoRA"
        return cls(
            retriever=retriever,
            records={record.image_id: record for record in records_list},
            model_profile=profile,
        )

    def search(self, request: SearchRequest) -> SearchResponse:
        started = perf_counter()
        # Fetch extra candidates before optional UI filtering so a filter does not return a sparse gallery.
        intent, results = self.retriever.search(request.query, k=min(request.k * 4, 30))
        if request.scenes:
            results = [result for result in results if result.scene in request.scenes]
        if request.styles:
            results = [result for result in results if set(request.styles).intersection(result.styles)]
        results = [
            result.model_copy(update={"image_url": f"/api/images/{result.image_id}"})
            for result in results[: request.k]
        ]
        return SearchResponse(
            intent=intent,
            results=results,
            corpus_size=len(self.records),
            elapsed_ms=(perf_counter() - started) * 1_000,
            model_profile=self.model_profile,
        )

    def close(self) -> None:
        """Release an embedded Qdrant client when an API process stops."""

        self.retriever.store.close()

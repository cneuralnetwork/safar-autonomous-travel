"""Offline retrieval evaluation and reproducible binary relevance metrics."""

from __future__ import annotations

import math
from pathlib import Path

from pydantic import BaseModel, Field

from .io import read_jsonl, write_json
from .retrieval import AttributeAwareRetriever


class EvaluationQuery(BaseModel):
    query_id: str
    text: str
    relevant_image_ids: list[str] = Field(min_length=1)
    split: str = "test"
    category: str = "semantic"


def load_qrels(path: Path) -> list[EvaluationQuery]:
    return read_jsonl(path, EvaluationQuery)


def _dcg(relevance: list[int]) -> float:
    return sum(value / math.log2(index + 2) for index, value in enumerate(relevance))


def score_rankings(rankings: dict[str, list[str]], qrels: list[EvaluationQuery]) -> dict[str, float]:
    metrics: dict[str, list[float]] = {
        "hit_rate_at_1": [],
        "hit_rate_at_5": [],
        "hit_rate_at_10": [],
        "recall_at_1": [],
        "recall_at_5": [],
        "recall_at_10": [],
        "ndcg_at_10": [],
    }
    for item in qrels:
        relevant = set(item.relevant_image_ids)
        ranking = rankings[item.query_id]
        for cutoff in (1, 5, 10):
            retrieved_relevant = len(relevant.intersection(ranking[:cutoff]))
            metrics[f"hit_rate_at_{cutoff}"].append(float(retrieved_relevant > 0))
            metrics[f"recall_at_{cutoff}"].append(retrieved_relevant / len(relevant))
        gains = [int(image_id in relevant) for image_id in ranking[:10]]
        ideal = [1] * min(len(relevant), 10)
        metrics["ndcg_at_10"].append(_dcg(gains) / _dcg(ideal) if ideal else 0.0)
    return {key: sum(values) / len(values) if values else 0.0 for key, values in metrics.items()}


def evaluate_retriever(retriever: AttributeAwareRetriever, qrels: list[EvaluationQuery]) -> dict[str, float]:
    rankings = attribute_aware_rankings(retriever, qrels)
    return score_rankings(rankings, qrels)


def attribute_aware_rankings(
    retriever: AttributeAwareRetriever,
    qrels: list[EvaluationQuery],
) -> dict[str, list[str]]:
    """Run each frozen query once and retain its exact top-10 IDs for reproducibility."""

    return {item.query_id: [result.image_id for result in retriever.search(item.text, k=10)[1]] for item in qrels}


def ranking_details(
    rankings: dict[str, list[str]],
    qrels: list[EvaluationQuery],
) -> list[dict[str, object]]:
    """Pair aggregate metrics with auditable per-query rankings and relevant ranks."""

    details: list[dict[str, object]] = []
    for item in qrels:
        relevant = set(item.relevant_image_ids)
        ranking = rankings[item.query_id]
        details.append(
            {
                "query_id": item.query_id,
                "text": item.text,
                "category": item.category,
                "relevant_count": len(relevant),
                "top_10": ranking,
                "relevant_ranks": [rank for rank, image_id in enumerate(ranking, start=1) if image_id in relevant],
            }
        )
    return details


def evaluate_global_variant(
    retriever: AttributeAwareRetriever, qrels: list[EvaluationQuery], *, vector_name: str
) -> dict[str, float]:
    """Evaluate a single global embedding branch against the same fixed qrels."""

    embedder = retriever.encoders.generic if vector_name == "generic" else retriever.encoders.fashion
    rankings: dict[str, list[str]] = {}
    for item in qrels:
        vector = embedder.embed_texts([item.text])[0]
        hits = retriever.store.search_images(vector_name, vector, limit=10)
        rankings[item.query_id] = [str(hit.payload.get("image_id", hit.point_id)) for hit in hits]
    return score_rankings(rankings, qrels)


def evaluate_variants(retriever: AttributeAwareRetriever, qrels: list[EvaluationQuery]) -> dict[str, dict[str, float]]:
    """Produce the report's three directly comparable ablations."""

    return {
        "vanilla_clip": evaluate_global_variant(retriever, qrels, vector_name="generic"),
        "fashionclip_only": evaluate_global_variant(retriever, qrels, vector_name="fashion"),
        "attribute_aware": evaluate_retriever(retriever, qrels),
    }


def evaluate_with_breakdown(retriever: AttributeAwareRetriever, qrels: list[EvaluationQuery]) -> dict[str, object]:
    """Evaluate overall plus category slices without mixing compositional and simple queries."""

    by_category: dict[str, dict[str, dict[str, float]]] = {}
    for category in sorted({item.category for item in qrels}):
        subset = [item for item in qrels if item.category == category]
        by_category[category] = evaluate_variants(retriever, subset)
    rankings = attribute_aware_rankings(retriever, qrels)
    return {
        "query_count": len(qrels),
        "overall": evaluate_variants(retriever, qrels),
        "by_category": by_category,
        "attribute_aware_rankings": ranking_details(rankings, qrels),
    }


def write_metrics(metrics: object, path: Path) -> None:
    write_json(path, metrics)

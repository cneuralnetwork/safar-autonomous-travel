from glance_retrieval.evaluation import (
    EvaluationQuery,
    evaluate_retriever,
    evaluate_variants,
    ranking_details,
    score_rankings,
)


def test_metric_calculation_is_binary_relevance_correct():
    qrels = [EvaluationQuery(query_id="q1", text="x", relevant_image_ids=["a", "b"])]
    metrics = score_rankings({"q1": ["a", "z", "b"]}, qrels)
    assert metrics["hit_rate_at_1"] == 1.0
    assert metrics["hit_rate_at_5"] == 1.0
    assert metrics["recall_at_1"] == 0.5
    assert metrics["recall_at_5"] == 1.0
    assert 0 < metrics["ndcg_at_10"] <= 1


def test_fixture_retriever_evaluates(retrieval_fixture):
    retriever, _ = retrieval_fixture
    qrels = [
        EvaluationQuery(
            query_id="yellow",
            text="A person in a bright yellow raincoat.",
            relevant_image_ids=["yellow-raincoat-urban-street"],
        )
    ]
    metrics = evaluate_retriever(retriever, qrels)
    assert metrics["hit_rate_at_5"] == 1.0
    assert metrics["recall_at_5"] == 1.0
    variants = evaluate_variants(retriever, qrels)
    assert set(variants) == {"vanilla_clip", "fashionclip_only", "attribute_aware"}


def test_ranking_details_expose_exact_top_ids_and_relevant_ranks():
    qrels = [EvaluationQuery(query_id="q", text="query", relevant_image_ids=["b", "d"], category="test")]
    details = ranking_details({"q": ["a", "b", "c", "d"]}, qrels)
    assert details == [
        {
            "query_id": "q",
            "text": "query",
            "category": "test",
            "relevant_count": 2,
            "top_10": ["a", "b", "c", "d"],
            "relevant_ranks": [2, 4],
        }
    ]

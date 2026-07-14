from glance_retrieval.retrieval import RuleIntentParser


def test_rule_parser_keeps_color_bound_to_the_nearest_garment():
    intent = RuleIntentParser().parse("A red tie and a white shirt in a formal setting.")
    assert intent.style == "formal"
    assert [(item.category, item.color) for item in intent.garments] == [("tie", "red"), ("shirt", "white")]


def test_compositional_rerank_beats_swapped_colours(retrieval_fixture):
    retriever, _ = retrieval_fixture
    intent, results = retriever.search("A red tie and a white shirt in a formal setting.", k=3)
    assert intent.garments[0].color == "red"
    assert results[0].image_id == "formal-red-tie-white-shirt-office"
    assert "red tie" in results[0].matched_attributes
    assert "white shirt" in results[0].matched_attributes
    assert results[0].score_breakdown.garment_satisfaction > results[1].score_breakdown.garment_satisfaction


def test_context_and_activity_are_matched(retrieval_fixture):
    retriever, _ = retrieval_fixture
    _, results = retriever.search("Someone wearing a blue shirt sitting on a park bench.", k=1)
    assert results[0].image_id == "casual-blue-shirt-park-bench"
    assert {"blue shirt", "park", "sitting"}.issubset(results[0].matched_attributes)


from glance_retrieval.demo import build_fixture_service


def test_all_official_assignment_queries_return_their_verified_fixture_match(tmp_path):
    service = build_fixture_service(tmp_path / "fixture")
    expectations = {
        "A person in a bright yellow raincoat.": "yellow-raincoat-urban-street",
        "Professional business attire inside a modern office.": "formal-red-tie-white-shirt-office",
        "Someone wearing a blue shirt sitting on a park bench.": "casual-blue-shirt-park-bench",
        "Casual weekend outfit for a city walk.": "casual-green-jacket-city",
        "A red tie and a white shirt in a formal setting.": "formal-red-tie-white-shirt-office",
    }
    for query, expected_id in expectations.items():
        _, results = service.retriever.search(query, k=1)
        assert results[0].image_id == expected_id


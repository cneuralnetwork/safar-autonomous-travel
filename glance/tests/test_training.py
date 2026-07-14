from glance_retrieval.training import compositional_caption, compositional_hard_negative


def test_hard_negative_swaps_colours_without_dropping_garments(retrieval_fixture):
    _, records = retrieval_fixture
    record = next(item for item in records if item.image_id == "formal-red-tie-white-shirt-office")
    positive = compositional_caption(record)
    negative = compositional_hard_negative(record)
    assert "red tie" in positive
    assert "white shirt" in positive
    assert "white tie" in negative
    assert "red shirt" in negative


import csv
import json

import numpy as np
import pytest
from PIL import Image

from glance_retrieval.annotation import (
    OllamaGPUUnavailableError,
    OllamaVisionAnnotator,
    VisionAnnotation,
    VisionGarment,
    normalize_annotation,
    reconcile_scene,
)
from glance_retrieval.clip_annotation import (
    GARMENT_CATEGORIES,
    GARMENT_COLORS,
    _select_garments,
    _softmax,
)
from glance_retrieval.dataset import (
    add_context_quality_evidence,
    merge_annotation_shards,
    reconcile_context_manifest,
    select_coco_home_candidates,
    select_coco_scene_candidates,
    select_context_records,
)
from glance_retrieval.experiments import audit_final_corpus, build_context_qrels
from glance_retrieval.indexer import create_garment_crop
from glance_retrieval.schemas import Garment, ImageRecord


def _record(image_id: str, scene: str, *, status: str = "success", with_garment: bool = True) -> ImageRecord:
    return ImageRecord(
        image_id=image_id,
        image_path=f"/tmp/{image_id}.jpg",
        source="openimages",
        scene=scene,
        styles=["casual"],
        activities=["walking"] if scene == "urban_street" else [],
        garments=[Garment(id=f"{image_id}-shirt", category="shirt", color="blue")] if with_garment else [],
        caption="A person wearing a blue shirt.",
        confidence="high",
        extra={
            "vlm_annotation": {
                "status": status,
                "model": "test-vision-model",
                "candidate_scene": scene,
                "predicted_scene": scene,
            }
        },
    )


def test_annotation_shards_must_be_complete_successful_and_keep_source_order():
    source = [_record("one", "office"), _record("two", "park")]
    merged = merge_annotation_shards(source, [[source[1]], [source[0]]])
    assert [record.image_id for record in merged] == ["one", "two"]
    with pytest.raises(ValueError, match="missing 1 records"):
        merge_annotation_shards(source, [[source[0]]])


def test_context_quality_gate_records_native_vlm_agreement_without_claiming_human_audit():
    accepted, rejected = add_context_quality_evidence(
        [_record("accepted", "home"), _record("rejected", "home", with_garment=False)]
    )
    assert accepted.extra["context_qa"]["tier"] == "native_vlm_agreement"
    assert accepted.extra["context_qa"]["accepted"] is True
    assert accepted.audited is False
    assert rejected.extra["context_qa"]["accepted"] is False
    assert "missing_physical_garment" in rejected.extra["context_qa"]["reasons"]


def test_real_context_qrels_cover_all_four_scenes():
    records = [
        _record("office", "office"),
        _record("city", "urban_street"),
        _record("park", "park"),
        _record("home", "home"),
    ]
    qrels = build_context_qrels(records, minimum_relevant=1)
    by_id = {item.query_id: item for item in qrels}
    assert {"scene-office", "scene-city", "scene-park", "scene-home"}.issubset(by_id)
    assert by_id["scene-city"].relevant_image_ids == ["city"]
    assert by_id["walking-city"].relevant_image_ids == ["city"]


def test_vlm_postprocessing_deduplicates_garments_and_reconciles_explicit_scene_words():
    normalized = normalize_annotation(
        VisionAnnotation(
            scene="home",
            activities=[],
            styles=[],
            garments=[
                VisionGarment(category="shirt", color="blue"),
                VisionGarment(category="shirt", color="blue", attributes=["button-down"]),
                VisionGarment(category="other", color="white"),
            ],
            caption="A person is sitting and working in an office.",
            confidence="high",
        )
    )
    assert normalized.scene == "office"
    assert normalized.activities == ["sitting", "working"]
    assert [(garment.category, garment.color) for garment in normalized.garments] == [("shirt", "blue")]


def test_openimages_garments_use_native_person_focus_when_no_garment_box_exists(tmp_path):
    image_path = tmp_path / "context.jpg"
    Image.new("RGB", (100, 200), "blue").save(image_path)
    record = ImageRecord(
        image_id="context",
        image_path=str(image_path),
        source="openimages",
        garments=[Garment(id="context-shirt", category="shirt", color="blue")],
        extra={"person_box": {"x": 0.2, "y": 0.1, "width": 0.5, "height": 0.8}},
    )
    crop = create_garment_crop(record, record.garments[0], tmp_path / "crops")
    assert crop is not None and crop.name == "context__native-person-focus.jpg"
    with Image.open(crop) as image:
        assert image.size == (50, 160)


def test_scene_reconciliation_uses_venue_words_not_substrings():
    assert reconcile_scene("home", "An officer speaks to a group.") == "home"
    assert reconcile_scene("home", "An Air Force officer salutes.") == "other"
    assert reconcile_scene("home", "A person waits in a hospital lobby.") == "other"
    assert reconcile_scene("home", "A person walks along a city street.") == "urban_street"


def test_final_corpus_audit_checks_balance_provenance_and_files(tmp_path):
    records = []
    for scene in ("office", "urban_street", "park", "home"):
        path = tmp_path / f"{scene}.jpg"
        Image.new("RGB", (20, 20), "white").save(path)
        records.append(_record(scene, scene).model_copy(update={"image_path": str(path)}))
    records = add_context_quality_evidence(records)
    audit = audit_final_corpus(
        records,
        expected_total=4,
        expected_fashionpedia=0,
        expected_per_scene=1,
        minimum_person_area=0,
    )
    assert audit["passed"] is True
    assert audit["counts"]["context_scenes"] == {"home": 1, "office": 1, "park": 1, "urban_street": 1}


def test_reconcile_context_can_write_a_named_reserve_manifest(tmp_path):
    candidates = tmp_path / "candidates.csv"
    with candidates.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ImageID", "scene_hint"])
        writer.writeheader()
        writer.writerow({"ImageID": "available", "scene_hint": "park"})
        writer.writerow({"ImageID": "missing", "scene_hint": "home"})
    (tmp_path / "openimages-available.jpg").write_bytes(b"x" * 513)
    output = tmp_path / "manifests" / "reserve.csv"

    written = reconcile_context_manifest(candidates, tmp_path, output=output)

    assert written == output
    with output.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["download_status"] == "ok"
    assert rows[0]["local_path"].endswith("openimages-available.jpg")
    assert rows[1]["download_status"] == "missing"


def test_ollama_gpu_guard_raises_a_fatal_specific_error(monkeypatch):
    annotator = OllamaVisionAnnotator()
    monkeypatch.setattr(annotator, "gpu_allocation_bytes", lambda: 0)

    with pytest.raises(OllamaGPUUnavailableError, match="refusing CPU fallback"):
        annotator.require_gpu_allocation()


def test_clip_fallback_probabilities_and_native_garment_hint_are_deterministic():
    probabilities = _softmax(np.asarray([0.1, 0.3, 0.2], dtype=np.float32))
    assert probabilities.argmax() == 1
    assert probabilities.sum() == pytest.approx(1.0)

    scores = np.zeros(len(GARMENT_CATEGORIES) * len(GARMENT_COLORS), dtype=np.float32)
    suit_record = _record("clip-fallback-suit", "office").model_copy(update={"tags": ["Suit"]})
    suit_garments = _select_garments(suit_record, scores)
    assert {garment.category for garment in suit_garments}.issuperset({"blazer", "pants"})

    shirt = GARMENT_CATEGORIES.index("shirt")
    blue = GARMENT_COLORS.index("blue")
    scores[shirt * len(GARMENT_COLORS) + blue] = 0.2
    record = _record("clip-fallback-shirt", "office").model_copy(update={"tags": []})
    garments = _select_garments(record, scores)

    assert garments[0].category == "shirt"
    assert garments[0].color == "blue"


def test_coco_home_selection_requires_a_visible_person_and_native_home_object(tmp_path):
    annotations = tmp_path / "instances.json"
    annotations.write_text(
        json.dumps(
            {
                "categories": [{"id": 1, "name": "person"}, {"id": 2, "name": "bed"}],
                "licenses": [{"id": 1, "url": "https://example.test/license"}],
                "images": [
                    {"id": 10, "file_name": "10.jpg", "width": 100, "height": 100, "license": 1},
                    {"id": 11, "file_name": "11.jpg", "width": 100, "height": 100, "license": 1},
                ],
                "annotations": [
                    {"image_id": 10, "category_id": 1, "bbox": [0, 0, 40, 80], "area": 3200, "iscrowd": 0},
                    {"image_id": 10, "category_id": 2, "bbox": [30, 30, 60, 50], "area": 3000, "iscrowd": 0},
                    {"image_id": 11, "category_id": 1, "bbox": [0, 0, 40, 80], "area": 3200, "iscrowd": 0},
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "coco-home.csv"

    select_coco_home_candidates(annotations, output=output, limit=1, minimum_person_area=0.08)

    with output.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["image_id"] for row in rows] == ["10"]
    assert rows[0]["home_objects"] == "bed"
    assert float(rows[0]["person_box_area"]) == pytest.approx(0.32)


def test_final_selection_prefers_native_coco_home_evidence_over_openimages_place_label():
    openimages_home = _record("openimages-home", "home")
    coco_home = _record("coco-home", "home").model_copy(update={"source": "coco"})
    records = [
        _record("office", "office"),
        _record("park", "park"),
        _record("street", "urban_street"),
        openimages_home,
        coco_home,
    ]

    selected = select_context_records(records, per_scene=1, minimum_person_area=0)

    home = next(record for record in selected if record.scene == "home")
    assert home.image_id == "coco-home"


def test_final_selection_ranks_real_environment_evidence_above_ambiguous_suit_label():
    weak = _record("weak-office", "office").model_copy(update={"tags": ["Suit"]})
    strong = _record("strong-office", "office").model_copy(update={"tags": ["Office", "Desk"]})
    records = [
        weak,
        strong,
        _record("home", "home").model_copy(update={"source": "coco"}),
        _record("park", "park"),
        _record("street", "urban_street"),
    ]

    selected = select_context_records(records, per_scene=1, minimum_person_area=0)

    office = next(record for record in selected if record.scene == "office")
    assert office.image_id == "strong-office"


def test_coco_scene_selection_requires_human_caption_evidence_and_visible_person(tmp_path):
    annotations = tmp_path / "instances.json"
    annotations.write_text(
        json.dumps(
            {
                "categories": [{"id": 1, "name": "person"}, {"id": 2, "name": "laptop"}],
                "licenses": [],
                "images": [
                    {"id": 10, "file_name": "10.jpg", "width": 100, "height": 100},
                    {"id": 11, "file_name": "11.jpg", "width": 100, "height": 100},
                ],
                "annotations": [
                    {"image_id": 10, "category_id": 1, "bbox": [0, 0, 40, 80], "area": 3200},
                    {"image_id": 10, "category_id": 2, "bbox": [30, 30, 50, 30], "area": 1500},
                    {"image_id": 11, "category_id": 1, "bbox": [0, 0, 40, 80], "area": 3200},
                ],
            }
        ),
        encoding="utf-8",
    )
    captions = tmp_path / "captions.json"
    captions.write_text(
        json.dumps(
            {
                "annotations": [
                    {"image_id": 10, "caption": "A person working at a desk with a laptop."},
                    {"image_id": 11, "caption": "A person standing on a beach."},
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "office.csv"

    select_coco_scene_candidates(annotations, captions, scene="office", output=output, limit=1)

    with output.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["image_id"] for row in rows] == ["10"]
    row = rows[0]
    assert row["scene"] == "office"
    assert "desk" in json.loads(row["caption_hits_json"])[0]

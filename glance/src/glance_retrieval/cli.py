"""Reproducible command-line entry points for the indexer and retriever workflows."""

from __future__ import annotations

import csv
import random
from pathlib import Path
from time import perf_counter, sleep

import typer
from rich import print

from .annotation import (
    MetadataOnlyAnnotator,
    OllamaGPUUnavailableError,
    OllamaVisionAnnotator,
    QwenVisionAnnotator,
    merge_annotation,
)
from .clip_annotation import annotate_context_with_clip
from .controlled_probes import build_controlled_probe_records
from .dataset import (
    add_context_quality_evidence,
    apply_audit_sheet,
    attach_openimages_person_boxes,
    build_fashionpedia_records,
    download_coco_context_candidates,
    download_context_candidates,
    merge_annotation_shards,
    reconcile_context_manifest,
    records_from_coco_context_manifest,
    records_from_context_manifest,
    render_context_contact_sheets,
    select_coco_home_candidates,
    select_coco_scene_candidates,
    select_context_records,
    select_openimages_candidates,
    write_audit_sheet,
    write_corpus,
)
from .demo import build_fixture_service
from .embeddings import make_encoder_pair
from .evaluation import evaluate_with_breakdown, load_qrels, write_metrics
from .experiments import (
    audit_final_corpus,
    build_context_qrels,
    build_fashionpedia_validation_qrels,
    build_hf_fashionpedia_train_records,
    select_fashionpedia_validation_records,
)
from .indexer import index_records
from .io import read_jsonl, write_jsonl
from .schemas import BoundingBox, ImageRecord
from .service import RetrievalService
from .settings import get_settings
from .store import QdrantVectorStore
from .training import build_caption_pairs, train_lora

app = typer.Typer(help="Glance fashion retrieval pipeline")


@app.command("curate-fashionpedia")
def curate_fashionpedia(
    annotations: Path = typer.Option(..., exists=True, readable=True),
    images: Path = typer.Option(..., exists=True, file_okay=False),
    output: Path = typer.Option(Path("data/processed/fashionpedia_700.jsonl")),
    limit: int = typer.Option(700, min=1),
    seed: int = typer.Option(7),
) -> None:
    records = build_fashionpedia_records(annotations, images, limit=limit, seed=seed)
    write_jsonl(output, records)
    print(f"[green]Wrote {len(records)} native Fashionpedia records to {output}[/green]")


@app.command("curate-fashionpedia-train")
def curate_fashionpedia_train(
    parquet: Path = typer.Option(..., exists=True, readable=True, help="Official Fashionpedia train parquet shard."),
    category_annotations: Path = typer.Option(
        Path("data/raw/fashionpedia/instances_attributes_val2020.json"),
        exists=True,
        readable=True,
        help="Official Fashionpedia category taxonomy JSON.",
    ),
    images: Path = typer.Option(Path("data/raw/fashionpedia/hf_train/images")),
    output: Path = typer.Option(Path("data/processed/fashionpedia_train_5000.jsonl")),
    limit: int = typer.Option(5_000, min=1),
    seed: int = typer.Option(19),
    max_garments: int = typer.Option(4, min=1, max=12),
) -> None:
    """Materialize a category-balanced official train split for the LoRA experiment."""

    records = build_hf_fashionpedia_train_records(
        parquet,
        category_annotations,
        images,
        limit=limit,
        seed=seed,
        max_garments=max_garments,
    )
    write_jsonl(output, records)
    print(f"[green]Wrote {len(records)} disjoint Fashionpedia train records to {output}[/green]")


@app.command("build-fashionpedia-qrels")
def build_fashionpedia_qrels(
    records_path: Path = typer.Option(Path("data/processed/corpus.jsonl"), exists=True, readable=True),
    output: Path = typer.Option(Path("data/evaluation/fashionpedia_val_native.jsonl")),
    minimum_relevant: int = typer.Option(3, min=1),
) -> None:
    """Create held-out Fashionpedia validation qrels with complete in-corpus relevance labels."""

    qrels = build_fashionpedia_validation_qrels(read_jsonl(records_path, ImageRecord), minimum_relevant=minimum_relevant)
    write_jsonl(output, qrels)
    print(f"[green]Wrote {len(qrels)} held-out Fashionpedia validation qrels to {output}[/green]")


@app.command("build-context-qrels")
def build_real_context_qrels(
    records_path: Path = typer.Option(Path("data/processed/corpus.jsonl"), exists=True, readable=True),
    output: Path = typer.Option(Path("data/evaluation/real_context.jsonl")),
    minimum_relevant: int = typer.Option(3, min=1),
) -> None:
    """Create exhaustive real-context qrels from the final QA-gated context subset."""

    qrels = build_context_qrels(read_jsonl(records_path, ImageRecord), minimum_relevant=minimum_relevant)
    write_jsonl(output, qrels)
    print(f"[green]Wrote {len(qrels)} real context qrels to {output}[/green]")


@app.command("verify-corpus")
def verify_corpus(
    records_path: Path = typer.Option(Path("data/processed/corpus.jsonl"), exists=True, readable=True),
    training_records_path: Path | None = typer.Option(None, exists=True, readable=True),
    output: Path = typer.Option(Path("artifacts/verification/final_corpus.json")),
) -> None:
    """Fail loudly unless every final corpus, provenance, balance, and file invariant holds."""

    records = read_jsonl(records_path, ImageRecord)
    training_records = read_jsonl(training_records_path, ImageRecord) if training_records_path else None
    audit = audit_final_corpus(records, training_records=training_records)
    write_metrics(audit, output)
    if not audit["passed"]:
        print(f"[red]Corpus verification failed -> {output}: {audit['checks']}[/red]")
        raise typer.Exit(code=1)
    print(f"[green]Corpus verification passed -> {output}: {audit['counts']}[/green]")


@app.command("extract-fashionpedia-validation")
def extract_fashionpedia_validation(
    records_path: Path = typer.Option(Path("data/processed/corpus.jsonl"), exists=True, readable=True),
    output: Path = typer.Option(Path("data/processed/fashionpedia_val_700.jsonl")),
) -> None:
    """Extract the real Fashionpedia validation images for a closed-world relevance benchmark."""

    records = select_fashionpedia_validation_records(read_jsonl(records_path, ImageRecord))
    write_jsonl(output, records)
    print(f"[green]Wrote {len(records)} Fashionpedia validation records to {output}[/green]")


@app.command("select-openimages")
def select_openimages(
    labels: Path = typer.Option(..., exists=True, readable=True),
    classes: Path = typer.Option(..., exists=True, readable=True),
    info: Path = typer.Option(..., exists=True, readable=True),
    boxes: Path | None = typer.Option(None, exists=True, readable=True, help="Optional native box CSV; filters tiny/nonphysical people."),
    min_person_area: float = typer.Option(0.01, min=0, max=1, help="Minimum normalized area for a native Person box."),
    output: Path = typer.Option(Path("data/manifests/openimages_context_candidates.csv")),
    per_scene: int = typer.Option(600, min=75),
) -> None:
    candidates = select_openimages_candidates(
        labels,
        classes,
        info,
        per_scene=per_scene,
        box_annotations_csv=boxes,
        min_person_area=min_person_area,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(output, index=False)
    print(f"[green]Wrote {len(candidates)} Open Images candidates to {output}[/green]")


@app.command("download-context")
def download_context(
    candidates: Path = typer.Option(..., exists=True, readable=True),
    destination: Path = typer.Option(Path("data/raw/openimages")),
    workers: int = typer.Option(10, min=1, max=32, help="Concurrent image downloads."),
) -> None:
    manifest = download_context_candidates(candidates, destination, workers=workers)
    print(f"[green]Downloaded candidates; manifest: {manifest}[/green]")


@app.command("select-coco-home")
def select_coco_home(
    annotations: Path = typer.Option(..., exists=True, readable=True),
    output: Path = typer.Option(Path("data/manifests/coco_home_candidates.csv")),
    limit: int = typer.Option(220, min=75),
    min_person_area: float = typer.Option(0.08, min=0.01, max=1),
) -> None:
    manifest = select_coco_home_candidates(
        annotations,
        output=output,
        limit=limit,
        minimum_person_area=min_person_area,
    )
    print(f"[green]Selected {limit} COCO home candidates -> {manifest}[/green]")


@app.command("select-coco-scene")
def select_coco_scene(
    annotations: Path = typer.Option(..., exists=True, readable=True),
    captions: Path = typer.Option(..., exists=True, readable=True),
    scene: str = typer.Option(..., help="One of: office, park, urban_street."),
    output: Path = typer.Option(...),
    limit: int = typer.Option(140, min=75),
    min_person_area: float = typer.Option(0.08, min=0.01, max=1),
    exclude_manifests: list[Path] | None = typer.Option(
        None,
        "--exclude-manifest",
        exists=True,
        readable=True,
        help="Repeat to prevent overlap with an earlier COCO scene manifest.",
    ),
) -> None:
    excluded: set[int] = set()
    for manifest_path in exclude_manifests or []:
        for row in csv.DictReader(manifest_path.open(encoding="utf-8")):
            if row.get("image_id"):
                excluded.add(int(row["image_id"]))
    manifest = select_coco_scene_candidates(
        annotations,
        captions,
        scene=scene,
        output=output,
        limit=limit,
        minimum_person_area=min_person_area,
        exclude_image_ids=excluded,
    )
    print(f"[green]Selected {limit} caption-grounded COCO {scene} candidates -> {manifest}[/green]")


@app.command("download-coco-context")
def download_coco_context(
    candidates: Path = typer.Option(..., exists=True, readable=True),
    destination: Path = typer.Option(Path("data/raw/coco/home")),
    workers: int = typer.Option(12, min=1, max=32),
) -> None:
    manifest = download_coco_context_candidates(candidates, destination, workers=workers)
    print(f"[green]Downloaded COCO context candidates -> {manifest}[/green]")


@app.command("prepare-coco-context")
def prepare_coco_context(
    manifest: Path = typer.Option(..., exists=True, readable=True),
    output: Path = typer.Option(Path("data/processed/coco_home_unannotated.jsonl")),
) -> None:
    records = records_from_coco_context_manifest(manifest)
    write_jsonl(output, records)
    scenes = sorted({record.scene for record in records})
    print(f"[green]Wrote {len(records)} COCO context records ({', '.join(scenes)}) -> {output}[/green]")


@app.command("reconcile-context")
def reconcile_context(
    candidates: Path = typer.Option(..., exists=True, readable=True),
    destination: Path = typer.Option(Path("data/raw/openimages")),
    output: Path | None = typer.Option(
        None,
        help="Optional manifest path; defaults to DESTINATION/download_manifest.csv.",
    ),
) -> None:
    manifest = reconcile_context_manifest(candidates, destination, output=output)
    print(f"[green]Reconciled local candidate files; manifest: {manifest}[/green]")


@app.command("prepare-context-records")
def prepare_context_records(
    manifest: Path = typer.Option(..., exists=True, readable=True),
    output: Path = typer.Option(Path("data/processed/openimages_unannotated.jsonl")),
) -> None:
    records = records_from_context_manifest(manifest)
    write_jsonl(output, records)
    print(f"[green]Wrote {len(records)} Open Images records to {output}[/green]")


@app.command("attach-person-boxes")
def attach_person_boxes(
    records_path: Path = typer.Option(..., exists=True, readable=True),
    boxes: Path = typer.Option(..., exists=True, readable=True),
    classes: Path = typer.Option(..., exists=True, readable=True),
    output: Path = typer.Option(Path("data/processed/openimages_unannotated_with_boxes.jsonl")),
) -> None:
    """Attach native Person boxes so the VLM receives both scene and clothing views."""

    records = attach_openimages_person_boxes(read_jsonl(records_path, ImageRecord), boxes, classes)
    write_jsonl(output, records)
    print(f"[green]Attached native Person boxes to {len(records)} records -> {output}[/green]")


@app.command("render-audit-sheets")
def render_audit_sheets(
    records_path: Path = typer.Option(..., exists=True, readable=True),
    output: Path = typer.Option(Path("data/audits/contact_sheets")),
    per_page: int = typer.Option(24, min=1, max=48),
) -> None:
    sheets = render_context_contact_sheets(read_jsonl(records_path, ImageRecord), output, per_page=per_page)
    print(f"[green]Rendered {len(sheets)} audit contact sheets to {output}[/green]")


@app.command("build-controlled-probes")
def build_controlled_probes(
    images: Path = typer.Option(Path("data/raw/synthetic"), exists=True, file_okay=False),
    output: Path = typer.Option(Path("data/processed/controlled_probes.jsonl")),
) -> None:
    records = build_controlled_probe_records(images)
    write_jsonl(output, records)
    print(f"[green]Wrote {len(records)} controlled, training-excluded probe records to {output}[/green]")


@app.command("annotate")
def annotate(
    records_path: Path = typer.Option(..., exists=True, readable=True),
    output: Path = typer.Option(...),
    qwen: bool = typer.Option(False, help="Use Qwen2.5-VL rather than the non-hallucinatory metadata fallback."),
    qwen_model: str = typer.Option("Qwen/Qwen2.5-VL-3B-Instruct", help="Hugging Face model ID or local Qwen2.5-VL snapshot."),
    device: str = typer.Option("auto", help="Inference device; use auto for Accelerate placement."),
    load_in_4bit: bool = typer.Option(False, help="Load Qwen with NF4 quantization to fit an 8 GB GPU."),
    ollama: bool = typer.Option(False, help="Use a local Ollama multimodal model for structured annotation."),
    ollama_model: str = typer.Option("gemma3:4b", help="Installed Ollama vision model name."),
    ollama_url: str = typer.Option("http://127.0.0.1:11434", help="Ollama service URL."),
    start: int = typer.Option(0, min=0, help="Record offset for a deterministic annotation shard."),
    limit: int | None = typer.Option(None, min=1, help="Maximum records to annotate in this shard."),
    resume: bool = typer.Option(False, help="Resume successful annotations already checkpointed in the output JSONL."),
    checkpoint_every: int = typer.Option(10, min=1, help="Atomically checkpoint after this many attempted records."),
    retries: int = typer.Option(2, min=0, max=6, help="Retries for transient local VLM failures."),
    retry_backoff: float = typer.Option(1.5, min=0, max=30, help="Seconds of linear backoff between VLM retries."),
    require_ollama_gpu: bool = typer.Option(
        False,
        help="Abort rather than silently continuing if Ollama reports zero VRAM allocation.",
    ),
    audit_sheet: Path | None = typer.Option(None),
    audit_count: int = typer.Option(150, min=0),
) -> None:
    all_records = read_jsonl(records_path, ImageRecord)
    records = all_records[start : start + limit if limit else None]
    if not records:
        raise typer.BadParameter("The selected annotation shard is empty.", param_hint="--start")
    if qwen and ollama:
        raise typer.BadParameter("Choose either --qwen or --ollama, not both.")
    if require_ollama_gpu and not ollama:
        raise typer.BadParameter("--require-ollama-gpu is valid only with --ollama.")
    if qwen:
        annotator = QwenVisionAnnotator(qwen_model, device, load_in_4bit=load_in_4bit)
    elif ollama:
        annotator = OllamaVisionAnnotator(ollama_model, ollama_url)
    else:
        annotator = MetadataOnlyAnnotator()
    model_label = annotator.model_name
    checkpointed: dict[str, ImageRecord] = {}
    if resume and output.exists():
        checkpointed = {record.image_id: record for record in read_jsonl(output, ImageRecord)}
        print(f"[cyan]Loaded {len(checkpointed)} checkpointed annotation records from {output}[/cyan]")
    enriched: list[ImageRecord] = []
    failures = 0
    skipped = 0
    for position, record in enumerate(records, start=1):
        prior = checkpointed.get(record.image_id)
        prior_status = prior.extra.get("vlm_annotation", {}).get("status") if prior else None
        prior_model = prior.extra.get("vlm_annotation", {}).get("model") if prior else None
        if prior and prior_status == "success" and prior_model == model_label:
            enriched.append(prior)
            skipped += 1
            continue
        try:
            started = perf_counter()
            person_box_payload = record.extra.get("person_box")
            person_box = BoundingBox.model_validate(person_box_payload) if person_box_payload else None
            annotation = None
            for attempt in range(retries + 1):
                try:
                    annotation = annotator.annotate(Path(record.image_path), person_box)
                    break
                except Exception as exc:
                    if require_ollama_gpu and ollama:
                        # A request failure is sometimes the first visible sign that Ollama lost
                        # CUDA.  Re-check before retrying so a guarded GPU run cannot spend minutes
                        # silently recomputing the same record on CPU.
                        annotator.require_gpu_allocation()
                    if attempt >= retries:
                        raise
                    delay = retry_backoff * (attempt + 1)
                    print(
                        f"[yellow]Transient annotation failure for {record.image_id}; "
                        f"retry {attempt + 1}/{retries} in {delay:.1f}s: {exc}[/yellow]"
                    )
                    sleep(delay)
            if annotation is None:  # pragma: no cover - loop either returns an annotation or raises
                raise RuntimeError("Annotator exhausted retries without returning a result")
            if require_ollama_gpu and ollama:
                gpu_allocation = annotator.require_gpu_allocation()
            elif ollama:
                # Keep runtime provenance even when CPU execution is explicitly allowed.
                gpu_allocation = annotator.gpu_allocation_bytes()
            else:
                gpu_allocation = None
            latency_ms = (perf_counter() - started) * 1_000
            annotated = merge_annotation(record, annotation)
            annotated = annotated.model_copy(
                update={
                    "extra": {
                        **annotated.extra,
                        "vlm_annotation": {
                            "status": "success",
                            "model": model_label,
                            "candidate_scene": record.scene,
                            "predicted_scene": annotation.scene,
                            "used_native_person_crop": person_box is not None,
                            "ollama_gpu_allocation_bytes": gpu_allocation,
                            "latency_ms": round(latency_ms, 3),
                        },
                    }
                }
            )
            enriched.append(annotated)
        except OllamaGPUUnavailableError:
            # This is a batch-level invariant, not a bad image. Preserve all prior successes and
            # stop immediately; recording a per-image error would make a later resume skip nothing
            # useful while allowing the remaining run to drift onto CPU.
            write_jsonl(output, enriched)
            raise
        except Exception as exc:
            failures += 1
            print(f"[yellow]Skipped annotation for {record.image_id}: {exc}[/yellow]")
            enriched.append(
                record.model_copy(
                    update={
                        "extra": {
                            **record.extra,
                            "vlm_annotation": {
                                "status": "error",
                                "model": model_label,
                                "message": str(exc)[:500],
                            },
                        }
                    }
                )
            )
        if position % checkpoint_every == 0 or position == len(records):
            write_jsonl(output, enriched)
            print(f"[cyan]Annotation checkpoint {position}/{len(records)} -> {output}[/cyan]")
    write_jsonl(output, enriched)
    if audit_sheet:
        rng = random.Random(7)
        sample = enriched.copy()
        rng.shuffle(sample)
        write_audit_sheet(sample[:audit_count], audit_sheet)
        print(f"[green]Wrote audit sheet for {min(audit_count, len(sample))} records to {audit_sheet}[/green]")
    print(
        f"[green]Annotated {len(enriched) - failures}/{len(enriched)} records "
        f"({skipped} resumed, {failures} failures) -> {output}[/green]"
    )


@app.command("merge-annotations")
def merge_annotations(
    shards: list[Path] = typer.Argument(..., exists=True, readable=True),
    records_path: Path = typer.Option(..., exists=True, readable=True),
    output: Path = typer.Option(Path("data/processed/openimages_annotated.jsonl")),
    allow_failures: bool = typer.Option(False, help="Permit failed records instead of enforcing a complete successful run."),
) -> None:
    """Merge complete annotation shards and attach reproducible context-QA evidence."""

    source_records = read_jsonl(records_path, ImageRecord)
    annotated_shards = [read_jsonl(path, ImageRecord) for path in shards]
    merged = merge_annotation_shards(source_records, annotated_shards, require_success=not allow_failures)
    merged = add_context_quality_evidence(merged)
    write_jsonl(output, merged)
    accepted = sum(bool(record.extra.get("context_qa", {}).get("accepted")) for record in merged)
    agreements = sum(bool(record.extra.get("context_qa", {}).get("scene_agreement")) for record in merged)
    print(
        f"[green]Merged {len(merged)} annotations -> {output} "
        f"({accepted} QA-accepted; {agreements} native/model scene agreements)[/green]"
    )


@app.command("annotate-clip-fallback")
def annotate_clip_fallback(
    records_path: Path = typer.Option(..., exists=True, readable=True),
    output: Path = typer.Option(Path("data/processed/openimages_hybrid_annotated.jsonl")),
    resume_from: Path | None = typer.Option(
        None,
        exists=True,
        readable=True,
        help="Preserve successful generative-VLM rows and fill only missing IDs with dual encoders.",
    ),
    focus_dir: Path = typer.Option(Path("data/processed/context_focus")),
    generic_model: str | None = typer.Option(None, help="Generic CLIP model or local snapshot."),
    fashion_model: str | None = typer.Option(None, help="FashionCLIP model or local snapshot."),
    fashion_adapter: Path | None = typer.Option(None, exists=True, file_okay=False),
    device: str = typer.Option("cpu"),
    batch_size: int = typer.Option(16, min=1),
) -> None:
    """Fill an interrupted VLM pass with a batched, provenance-labelled CLIP ensemble."""

    source_records = read_jsonl(records_path, ImageRecord)
    preserved: dict[str, ImageRecord] = {}
    if resume_from:
        preserved = {
            record.image_id: record
            for record in read_jsonl(resume_from, ImageRecord)
            if record.extra.get("vlm_annotation", {}).get("status") == "success"
        }
    unknown_ids = set(preserved).difference(record.image_id for record in source_records)
    if unknown_ids:
        raise typer.BadParameter(
            f"Resume file contains {len(unknown_ids)} IDs outside the source manifest",
            param_hint="--resume-from",
        )
    pending = [record for record in source_records if record.image_id not in preserved]
    settings = get_settings()
    encoders = make_encoder_pair(
        generic_model or settings.generic_model,
        fashion_model or settings.fashion_model,
        device,
        fashion_adapter or settings.fashion_adapter,
    )
    completed: dict[str, ImageRecord] = dict(preserved)

    def checkpoint(rows: list[ImageRecord], position: int, total: int) -> None:
        completed.update({record.image_id: record for record in rows})
        ordered = [completed[record.image_id] for record in source_records if record.image_id in completed]
        write_jsonl(output, ordered)
        print(
            f"[cyan]Dual-encoder checkpoint {position}/{total} pending "
            f"({len(ordered)}/{len(source_records)} total) -> {output}[/cyan]"
        )

    annotated = annotate_context_with_clip(
        pending,
        encoders=encoders,
        focus_dir=focus_dir,
        batch_size=batch_size,
        checkpoint=checkpoint,
    )
    completed.update({record.image_id: record for record in annotated})
    ordered = [completed[record.image_id] for record in source_records]
    write_jsonl(output, ordered)
    print(
        f"[green]Context annotation complete: {len(preserved)} preserved successes + "
        f"{len(annotated)} dual-encoder fallback = {len(ordered)} records -> {output}[/green]"
    )


@app.command("combine-context")
def combine_context(
    inputs: list[Path] = typer.Argument(..., exists=True, readable=True),
    output: Path = typer.Option(Path("data/processed/context_combined.jsonl")),
) -> None:
    """Combine independently curated context sources while rejecting duplicate image IDs."""

    records = [record for path in inputs for record in read_jsonl(path, ImageRecord)]
    if len(records) != len({record.image_id for record in records}):
        raise typer.BadParameter("Context inputs contain duplicate image IDs", param_hint="inputs")
    write_jsonl(output, records)
    source_counts: dict[str, int] = {}
    for record in records:
        source_counts[record.source] = source_counts.get(record.source, 0) + 1
    print(f"[green]Combined {len(records)} context records -> {output}: {source_counts}[/green]")


@app.command("apply-audit")
def apply_audit(
    records_path: Path = typer.Option(..., exists=True, readable=True),
    audit_sheet: Path = typer.Option(..., exists=True, readable=True),
    output: Path = typer.Option(...),
) -> None:
    records = apply_audit_sheet(read_jsonl(records_path, ImageRecord), audit_sheet)
    write_jsonl(output, records)
    audited = sum(record.audited for record in records)
    print(f"[green]Applied audit decisions: {audited}/{len(records)} records are audited -> {output}[/green]")


@app.command("assemble-corpus")
def assemble_corpus(
    fashionpedia: Path = typer.Option(..., exists=True, readable=True),
    context: Path = typer.Option(..., exists=True, readable=True),
    controlled_probes: Path | None = typer.Option(None, exists=True, readable=True, help="Optional disclosed held-out probes; selected within the 300 context slots."),
    output: Path = typer.Option(Path("data/processed/corpus.jsonl")),
) -> None:
    fashion_records = read_jsonl(fashionpedia, ImageRecord)
    context_records = read_jsonl(context, ImageRecord)
    if controlled_probes:
        context_records.extend(read_jsonl(controlled_probes, ImageRecord))
    if len(fashion_records) != 700:
        raise typer.BadParameter("Fashionpedia input must contain exactly 700 records", param_hint="--fashionpedia")
    selected_context = select_context_records(context_records, per_scene=75)
    corpus = fashion_records + selected_context
    if len(corpus) != 1000:
        raise RuntimeError("Corpus composition invariant failed")
    write_corpus(corpus, output)
    print(f"[green]Wrote 1,000-image corpus to {output}[/green]")


@app.command("train-lora")
def train_lora_command(
    records_path: Path = typer.Option(..., exists=True, readable=True),
    output: Path = typer.Option(Path("artifacts/fashionclip-lora")),
    base_model: str | None = typer.Option(
        None,
        help="Local FashionCLIP snapshot directory; defaults to GLANCE_FASHION_MODEL.",
    ),
    epochs: int = typer.Option(5, min=1),
    batch_size: int = typer.Option(8, min=1),
    resume_from: Path | None = typer.Option(None, exists=True, file_okay=False, help="Existing adapter/checkpoint directory."),
    start_batch: int | None = typer.Option(None, min=0, help="Optional deterministic batch offset override."),
    max_batches: int | None = typer.Option(None, min=1, help="Checkpoint after this many batches; resume to continue."),
    seed: int = typer.Option(19, help="Deterministic train-pair order."),
) -> None:
    records = read_jsonl(records_path, ImageRecord)
    settings = get_settings()
    metrics = train_lora(
        build_caption_pairs(records),
        output_dir=output,
        model_name=base_model or settings.fashion_model,
        epochs=epochs,
        batch_size=batch_size,
        resume_from=resume_from,
        start_batch=start_batch,
        max_batches=max_batches,
        seed=seed,
    )
    print(f"[green]LoRA complete: {metrics}[/green]")


@app.command("index")
def index(
    records_path: Path = typer.Option(Path("data/processed/corpus.jsonl"), exists=True, readable=True),
    crop_dir: Path = typer.Option(Path("data/processed/crops")),
    output: Path | None = typer.Option(None, help="Optional crop-enriched JSONL output; defaults to updating the input file."),
    recreate: bool = typer.Option(False, help="Recreate Qdrant collections before indexing."),
    qdrant_url: str | None = typer.Option(None),
    fashion_adapter: Path | None = typer.Option(None, exists=True, file_okay=False, help="Optional PEFT FashionCLIP adapter."),
    batch_size: int = typer.Option(32, min=1, help="Embedding and Qdrant upsert batch size."),
    start: int = typer.Option(0, min=0, help="Record offset for resumable shard indexing."),
    limit: int | None = typer.Option(None, min=1, help="Maximum records in this resumable index shard."),
) -> None:
    settings = get_settings()
    if fashion_adapter:
        settings = settings.model_copy(update={"fashion_adapter": str(fashion_adapter)})
    all_records = read_jsonl(records_path, ImageRecord)
    records = all_records[start : start + limit if limit else None]
    if not records:
        raise typer.BadParameter("The selected index shard is empty.", param_hint="--start")
    encoders = make_encoder_pair(
        settings.generic_model,
        settings.fashion_model,
        settings.device,
        settings.fashion_adapter,
    )
    store = QdrantVectorStore(qdrant_url or settings.qdrant_url)

    def report_index_progress(stage: str, position: int, total: int) -> None:
        interval = batch_size * 10
        if position == total or position % interval == 0:
            print(f"[cyan]Indexing {stage}: {position}/{total}[/cyan]")

    try:
        enriched, stats = index_records(
            records,
            store=store,
            encoders=encoders,
            crop_dir=crop_dir,
            recreate=recreate,
            batch_size=batch_size,
            progress=report_index_progress,
        )
    finally:
        store.close()
    if output:
        write_jsonl(output, enriched)
    elif start == 0 and limit is None:
        write_jsonl(records_path, enriched)
    print(
        f"[green]Indexed shard {start}:{start + len(records)} — {stats.image_points} images and "
        f"{stats.garment_points} garment crops ({stats.skipped_without_crop} skipped).[/green]"
    )


@app.command("evaluate")
def evaluate(
    qrels: Path = typer.Option(Path("data/evaluation/gold_queries.jsonl"), exists=True, readable=True),
    output: Path = typer.Option(Path("artifacts/evaluation/full_system.json")),
    records_path: Path | None = typer.Option(None),
    qdrant_url: str | None = typer.Option(None),
    fashion_adapter: Path | None = typer.Option(None, exists=True, file_okay=False, help="Optional PEFT FashionCLIP adapter."),
) -> None:
    settings = get_settings()
    updates: dict[str, object] = {}
    if records_path:
        updates["records_path"] = records_path
    if qdrant_url:
        updates["qdrant_url"] = qdrant_url
    if fashion_adapter:
        updates["fashion_adapter"] = str(fashion_adapter)
    if updates:
        settings = settings.model_copy(update=updates)
    service = RetrievalService.from_settings(settings)
    try:
        metrics = evaluate_with_breakdown(service.retriever, load_qrels(qrels))
    finally:
        service.close()
    write_metrics(metrics, output)
    print(f"[green]Evaluation metrics -> {output}: {metrics}[/green]")


@app.command("serve")
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    fixture: bool = typer.Option(False, help="Run the self-contained visual fixture instead of the real Qdrant corpus."),
) -> None:
    import uvicorn

    if fixture:
        from .api import create_app

        demo_service = build_fixture_service(Path("static/generated/demo_fixture"))
        uvicorn.run(create_app(demo_service), host=host, port=port, reload=False)
    else:
        uvicorn.run("glance_retrieval.api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()

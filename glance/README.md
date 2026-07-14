# Glance - Multimodal Fashion & Context Retrieval

Glance is an ML-first image retrieval system for fashion photographs. It can search a mixed corpus using natural language such as:

> "A red tie and a white shirt in a formal setting."

Unlike a single global CLIP embedding, Glance separately indexes whole-image context and individual garment crops, then reranks candidates with explicit color-to-garment bindings. This is designed to avoid the classic false positive: a blue tie with a red shirt ranking above the requested red tie with a white shirt.

## What is included

- `indexer/` behavior in `src/glance_retrieval`: Fashionpedia/Open Images curation, local annotation, palette colors, LoRA training, and Qdrant indexing.
- `retriever/` behavior in the same package: natural-language query parsing, multi-vector candidate fusion, AND-style compositional reranking, FastAPI API, and a small web demo.
- A reproducible 22-query held-out Fashionpedia benchmark, a separate exhaustive real-context benchmark, saved baseline/post-LoRA metrics, and a clearly labeled controlled acceptance set covering all five assignment prompts.
- [Submission PDF](output/pdf/glance_multimodal_fashion_retrieval.pdf) and its source at [docs/submission.tex](docs/submission.tex).

## Why this is better than vanilla CLIP

| Capability | Vanilla global CLIP | Glance |
| --- | --- | --- |
| Scene/vibe matching | One image embedding | Generic CLIP scene vector plus scene metadata |
| Fine-grained garments | Often entangled in one image vector | FashionCLIP full-image, exact garment crops, and native Person-focus vectors |
| "red tie + white shirt" | May match either color anywhere | Parses two garment requirements; final score uses the minimum garment match |
| Fashion adaptation | Zero-shot only | Optional LoRA workflow on Fashionpedia-derived attribute captions plus color-binding hard negatives |
| Explainability | Similarity score only | Returns matched scene/style/garment attributes and score components |

Fashionpedia contributes exact garment crops. Open Images has no fashion boxes, so Glance does not
invent them: it records a native `Person` crop as a coarser `native_person` evidence scope and reuses
that focused FashionCLIP vector for the VLM-labeled garments on that person. The main retrieval score
for a query containing clothing constraints is:

```text
0.30 * generic_scene_similarity
+ 0.25 * fashion_global_similarity
+ 0.30 * min(localized_garment_match_i)
+ 0.15 * structured_metadata_match
```

Using `min(...)` is deliberate: every requested garment must match. A highly similar white shirt cannot compensate for an incorrect tie color.

## Architecture

```text
                           INDEXER
Fashionpedia masks/attrs -----> exact garment records --------┐
Open Images full frame + native Person crop --> Gemma 3 JSON --+--> image + localized vectors --> Qdrant
                                             |                    generic CLIP / FashionCLIP (+ LoRA)
                                             +--> QA evidence + contact sheets

                          RETRIEVER
natural-language query --> rule/Qwen parser --> scene/style/garments
                                    |                 |
                     generic image search      crop search per garment
                                    \                 /
                              reciprocal-rank candidate fusion
                                           |
                             compositional metadata reranker
                                           |
                            FastAPI JSON + explainable image gallery
```

## Dataset plan

The final searchable corpus has exactly 1,000 images:

- 700 category-balanced images from [Fashionpedia](https://fashionpedia.github.io/home/Fashionpedia_download.html), which provides localized clothing masks and attributes.
- 295 [Open Images V7](https://storage.googleapis.com/openimages/web/download_v7.html) images selected from 504 unique, locally available candidates proposed by native scene labels and filtered to a meaningful native `Person` box (at least 3% of the frame). A local `gemma3:4b` VLM sees both the full frame and the native Person crop; unsupported venues and failed/empty annotations are rejected before balancing.
- 5 explicit, training-excluded synthetic acceptance probes for combinations that are sparse in the public labels: yellow raincoat, modern-office business attire, blue shirt on a park bench, casual city walk, and red tie plus white shirt. Their provenance and limitations are documented in [controlled-probe-prompts.md](docs/controlled-probe-prompts.md).

The 300 context slots remain balanced at 75 office, 75 urban-street, 75 park, and 75 home examples. The Open Images candidate pool is a proposal, not assumed ground truth: native/VLM agreement, deterministic contradiction checks, rendered contact sheets, and an editable audit CSV remain explicit evidence. Automated QA is never mislabeled as human audit. The five synthetic probes are visibly labelled `synthetic` in the JSONL and are excluded from LoRA training and broad benchmark claims.

The completed LoRA experiment uses 5,000 additional, non-overlapping Fashionpedia train images. One epoch completed all 625 batches and the resulting adapter was used to rebuild both the 700-image validation index and 1,000-image final demo index. Assets, embeddings, and model weights are deliberately excluded from Git; manifests, qrels, metrics, and scripts make the run reproducible. Review the underlying dataset terms before redistributing images.

## Quick start

Prerequisites: Python 3.11+, Docker for Qdrant, Ollama for the measured local VLM path, and a CUDA GPU for full annotation/LoRA training. A CPU is enough for tests and a tiny fixture demo.

```bash
uv sync --extra ml --extra dev
cp .env.example .env
docker compose up -d qdrant
ollama pull gemma3:4b
```

The default model IDs download through Hugging Face on first use. For an offline, reproducible run
(including the local FashionCLIP adapter), save the two public snapshots and switch the `.env`
values to the commented local paths:

```bash
huggingface-cli download openai/clip-vit-base-patch32 --local-dir models/openai-clip-vit-base-patch32
huggingface-cli download Marqo/marqo-fashionCLIP --local-dir models/marqo-fashionCLIP
# In .env: uncomment the two GLANCE_*_MODEL=models/... values.
```

For a no-Docker local demo, set `GLANCE_QDRANT_URL=qdrant_storage/local`; Qdrant runs embedded
on disk. Use the Docker URL for a multi-process or production-style deployment so payload indexes
and filtering are available.

Build the data in this order (paths below point to files downloaded from the official dataset sources):

```bash
# Fashion garment subset
uv run glance curate-fashionpedia \
  --annotations data/raw/fashionpedia/instances_attributes_val2020.json \
  --images data/raw/fashionpedia/val \
  --output data/processed/fashionpedia_700.jsonl

# Context candidate pool, download, and model-assisted annotation
uv run glance select-openimages --labels data/raw/openimages/validation-annotations-human-imagelabels.csv \
  --classes data/raw/openimages/class-descriptions.csv \
  --info data/raw/openimages/validation-images-with-rotation.csv \
  --boxes data/raw/openimages/validation-annotations-bbox.csv \
  --min-person-area 0.03 --per-scene 170
uv run glance download-context --candidates data/manifests/openimages_context_candidates.csv \
  --destination data/raw/openimages/candidates --workers 16
# If a network job is interrupted after files land, rebuild the manifest safely.
uv run glance reconcile-context --candidates data/manifests/openimages_context_candidates.csv \
  --destination data/raw/openimages/candidates
uv run glance prepare-context-records \
  --manifest data/raw/openimages/candidates/download_manifest.csv \
  --output data/processed/openimages_unannotated_unique.jsonl
uv run glance attach-person-boxes \
  --records-path data/processed/openimages_unannotated_unique.jsonl \
  --boxes data/raw/openimages/validation-annotations-bbox.csv \
  --classes data/raw/openimages/class-descriptions-boxable.csv \
  --output data/processed/openimages_unannotated_with_boxes.jsonl

# The measured path is resumable, retries transient local-server errors, and refuses CPU fallback.
uv run glance annotate \
  --records-path data/processed/openimages_unannotated_with_boxes.jsonl \
  --output data/processed/openimages_gemma3_v5_annotated.jsonl \
  --ollama --ollama-model gemma3:4b --checkpoint-every 5 --resume \
  --require-ollama-gpu
# Equivalent guarded/resumable launcher used for the measured run:
bash scripts/run_context_annotation_gpu.sh

# Optional reserve only if QA leaves a scene below 75 examples. This keeps the QA gate intact
# while widening the already-downloaded pool to people covering at least 1% of the frame.
uv run glance select-openimages --labels data/raw/openimages/validation-annotations-human-imagelabels.csv \
  --classes data/raw/openimages/class-descriptions.csv \
  --info data/raw/openimages/validation-images-with-rotation.csv \
  --boxes data/raw/openimages/validation-annotations-bbox.csv \
  --min-person-area 0.01 --per-scene 400 \
  --output data/manifests/openimages_context_reserve_candidates.csv
uv run glance reconcile-context --candidates data/manifests/openimages_context_reserve_candidates.csv \
  --destination data/raw/openimages/candidates \
  --output data/manifests/openimages_context_reserve_download_manifest.csv
uv run glance prepare-context-records \
  --manifest data/manifests/openimages_context_reserve_download_manifest.csv \
  --output data/processed/openimages_reserve_unannotated.jsonl
uv run glance attach-person-boxes --records-path data/processed/openimages_reserve_unannotated.jsonl \
  --boxes data/raw/openimages/validation-annotations-bbox.csv \
  --classes data/raw/openimages/class-descriptions-boxable.csv \
  --output data/processed/openimages_reserve_with_boxes.jsonl
# Reusing the same checkpoint skips every completed primary image and annotates only new IDs.
GLANCE_ANNOTATION_RECORDS=data/processed/openimages_reserve_with_boxes.jsonl \
  bash scripts/run_context_annotation_gpu.sh

# Use the same records file that drove the final annotation pass. If the optional reserve was
# used, replace the path below with `data/processed/openimages_reserve_with_boxes.jsonl`.
uv run glance merge-annotations data/processed/openimages_gemma3_v5_annotated.jsonl \
  --records-path data/processed/openimages_unannotated_with_boxes.jsonl \
  --output data/processed/openimages_gemma3_v5_qa.jsonl
uv run glance render-audit-sheets \
  --records-path data/processed/openimages_gemma3_v5_qa.jsonl \
  --output data/audits/context_v5 --per-page 16

# Optional explicit reviewer corrections can be applied after editing the generated audit CSV.
# Automated QA evidence remains distinct from the `audited` flag.
uv run glance annotate --records-path data/processed/openimages_unannotated_with_boxes.jsonl \
  --output data/processed/openimages_gemma3_v5_annotated.jsonl --ollama --resume \
  --audit-sheet data/audits/context_review.csv --audit-count 504
uv run glance apply-audit --records-path data/processed/openimages_gemma3_v5_qa.jsonl \
  --audit-sheet data/audits/context_review.csv --output data/processed/openimages_reviewed.jsonl
# Optional controlled probes are small, disclosed visual acceptance checks; never include them in training.
uv run glance build-controlled-probes --images data/raw/synthetic
uv run glance assemble-corpus --fashionpedia data/processed/fashionpedia_700.jsonl \
  --context data/processed/openimages_gemma3_v5_qa.jsonl \
  --controlled-probes data/processed/controlled_probes.jsonl
```

Train and index:

```bash
# Materialize 5,000 official training images from a Fashionpedia parquet shard.
uv run glance curate-fashionpedia-train \
  --parquet data/raw/fashionpedia/hf_train/data/train-00000-of-00007-fe108070118553c3.parquet \
  --category-annotations data/raw/fashionpedia/instances_attributes_val2020.json \
  --images data/raw/fashionpedia/hf_train/images \
  --output data/processed/fashionpedia_train_5000.jsonl

# Train the measured rank-16 adapter over FashionCLIP's differentiable MLP projections.
uv run glance train-lora --records-path data/processed/fashionpedia_train_5000.jsonl \
  --base-model models/marqo-fashionCLIP \
  --output artifacts/fashionclip-lora-5000-e1 --epochs 1 --batch-size 8

# Rebuild the complete corpus with the measured adapter.
uv run glance index --records-path data/processed/corpus.jsonl \
  --fashion-adapter artifacts/fashionclip-lora-5000-e1 --batch-size 8 --recreate

# Serve with the same adapter and Qdrant location used for indexing.
export GLANCE_FASHION_ADAPTER=artifacts/fashionclip-lora-5000-e1
uv run glance serve
```

The adapter output includes standard PEFT weights plus `glance_adapter_manifest.json`, which
records the local base checkpoint and targeted modules. Keep its base FashionCLIP snapshot fixed
when serving the adapter, and always reindex after changing either model or adapter.

### Try the measured website

In this prepared workspace, the complete 1,000-image LoRA demo starts with one command:

```bash
bash scripts/serve_gpu_demo.sh
```

Then open `http://127.0.0.1:8000`. The launcher checks the Qdrant index, corpus, local base
models, adapter, and CUDA before starting. Override `GLANCE_PORT`, `GLANCE_HOST`, or
`GLANCE_DEVICE` when needed. The first query loads both model towers; subsequent searches on the
tested RTX 4060 take roughly 100--300 ms of model time.

The website includes all five assignment prompts, scene/style filters, adjustable top-k, parsed
query intent, per-result score evidence, a full-image inspector, and shareable query URLs. The API
accepts:

```json
POST /api/search
{"query":"Someone wearing a blue shirt sitting on a park bench.","k":8}
```

It returns the parsed intent, image URLs, ranked results, matched attributes, and score components.

To inspect the website without downloading models or datasets, run the clearly labeled deterministic visual fixture:

```bash
PYTHONPATH=src python -m glance_retrieval.cli serve --fixture
```

## Evaluation

The checked-in experiment uses 700 Fashionpedia validation images and 22 frozen queries: 12 single-attribute and 10 compositional requests. Garment categories are official Fashionpedia annotations; colors are deterministically inferred inside the annotated garment boxes. The 5,000 training images are disjoint from this corpus.

```bash
uv run glance extract-fashionpedia-validation \
  --records-path data/processed/corpus.jsonl \
  --output data/processed/fashionpedia_val_700.jsonl
uv run glance build-fashionpedia-qrels \
  --records-path data/processed/fashionpedia_val_700.jsonl \
  --output data/evaluation/fashionpedia_val_native.jsonl

uv run glance evaluate \
  --qrels data/evaluation/fashionpedia_val_native.jsonl \
  --records-path data/processed/fashionpedia_val_700.jsonl \
  --fashion-adapter artifacts/fashionclip-lora-5000-e1 \
  --output artifacts/evaluation/fashionpedia_val_lora.json

# Independently report context retrieval on the final real Open Images subset.
uv run glance build-context-qrels \
  --records-path data/processed/corpus.jsonl \
  --output data/evaluation/openimages_context.jsonl --minimum-relevant 3
uv run glance evaluate \
  --qrels data/evaluation/openimages_context.jsonl \
  --records-path data/processed/corpus.jsonl \
  --fashion-adapter artifacts/fashionclip-lora-5000-e1 \
  --output artifacts/evaluation/openimages_context_lora.json

# Machine-check the 1,000-image balance, files, VLM provenance, QA, crops, and split isolation.
uv run glance verify-corpus \
  --records-path data/processed/corpus.jsonl \
  --training-records-path data/processed/fashionpedia_train_5000.jsonl \
  --output artifacts/verification/final_corpus.json
```

Measured overall results:

Hit@k is the fraction of queries with at least one relevant result in the first k; Recall@k is the fraction of all annotated relevant images recovered.

| System | H@1 | H@5 | H@10 | R@10 | nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Vanilla CLIP | 27.27% | 54.55% | 72.73% | 13.26% | 0.2482 |
| Base FashionCLIP only | 63.64% | 72.73% | 81.82% | 17.88% | 0.3741 |
| Base attribute-aware | 68.18% | 72.73% | 86.36% | 47.25% | 0.6875 |
| LoRA FashionCLIP only | 45.45% | 86.36% | 90.91% | 24.26% | 0.4315 |
| **LoRA attribute-aware** | **68.18%** | **81.82%** | **95.45%** | **52.07%** | **0.7315** |

On the compositional subset, the full system's H@5 improves from 50% to 70%, H@10 from 80% to 100%, true R@10 from 36.09% to 41.69%, and nDCG@10 from 0.4461 to 0.5093 after LoRA. FashionCLIP-only H@1 drops, so the result is a deeper-recall/ranking gain rather than an across-the-board improvement.

`data/evaluation/gold_queries.controlled.jsonl` is a separate visual acceptance check for the five sparse official prompts; it is not a substitute for a real held-out benchmark. The acceptance gate is a verified relevant result in top 5 for each official prompt and a higher compositional nDCG@10 than the vanilla CLIP baseline on the real audited set.

## Scaling to one million images

The 1,000-image demo uses the same Qdrant schema as a larger deployment. For one million images, keep the two collections, create payload indexes before ingestion, use HNSW (`m=24`, `ef_construct=128`), disk-backed vectors, scalar quantization, batched embedding/upsert writes, and retrieve only 200 candidates before local reranking. This retains the ML behavior while avoiding a custom vector database.

## Verification available in this workspace

```bash
PYTHONPATH=src pytest -vv
PYTHONPATH=src python -m glance_retrieval.cli --help
```

The fixture suite exercises the API, natural-language parsing, exact color/category bindings, hard-negative caption construction, batched indexing semantics, and offline metric calculations. The measured GPU run built a 700-image validation index with 3,047 usable garment crops and a final 1,000-image index with 3,066 usable crops. Each disclosed synthetic acceptance probe appeared at rank 1 for its matching official prompt under the full system; vanilla CLIP also passed that controlled set. This is a smoke test, not a real-world context benchmark.

## Repository

Submission repository: <https://github.com/cneuralnetwork/glance-multimodal-fashion-retrieval>

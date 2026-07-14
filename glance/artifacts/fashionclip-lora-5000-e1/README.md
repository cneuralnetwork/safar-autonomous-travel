# Glance FashionCLIP LoRA adapter

This directory contains the measured rank-16 PEFT LoRA adapter used by the
Glance multimodal fashion retrieval experiments. It adapts Marqo FashionCLIP's
OpenCLIP ViT-B/16 image and text towers for attribute-aware and compositional
fashion retrieval.

## Base model

The adapter requires the public `Marqo/marqo-fashionCLIP` base model. The base
weights and processor files are intentionally not redistributed here. Download
the snapshot to `models/marqo-fashionCLIP` using the command in the repository
README.

## Training run

- Data: 5,000 official Fashionpedia training images
- Validation: 700 disjoint official Fashionpedia validation images
- Supervision: captions assembled from annotated garment category and a
  deterministic palette color measured inside each garment box
- Objective: symmetric image-text contrastive loss plus color-binding hard
  negatives with margin `0.18`
- LoRA targets: `c_fc` and `c_proj` in both image and text towers
- Configuration: rank 16, alpha 32, dropout 0.05
- Optimization: 1 epoch, batch size 8, gradient accumulation 4, seed 19
- Completion: 625/625 batches, 5,000 pairs
- Mean training loss: 0.447713

## Held-out result

The strongest retrieval path combines adapted FashionCLIP similarity with
garment-crop, color, context, and relation-aware reranking. On the 22-query
held-out Fashionpedia suite it reached H@1/H@5/H@10 of
`0.6818/0.8182/0.9545`, R@10 of `0.5207`, and nDCG@10 of `0.7315`.

The adapter is not universally better in isolation: FashionCLIP-only H@1 fell
from `0.6364` to `0.4545`, while its H@5, H@10, R@10, and nDCG@10 improved.
The full system retained H@1 and improved every deeper metric. See
`artifacts/evaluation/README.md` and the submission PDF for the complete
comparison and metric definitions.

## Usage

Pass this directory through `--fashion-adapter` when indexing and evaluating;
the same adapter must be used on both image and text embeddings. Exact commands
are in the repository README.

Portable files in this directory:

- `adapter_model.safetensors` — LoRA weights
- `adapter_config.json` — PEFT configuration
- `glance_adapter_manifest.json` — training and architecture manifest
- `training_metrics.json` — measured training summary

## Limitations

The color labels are deterministic measurements rather than human judgments,
the held-out suite is small, and Fashionpedia does not provide rich place or
weather labels. The reported results therefore demonstrate controlled
fashion-attribute retrieval, not production-level geographic or weather
generalization. Dataset and base-model licenses remain applicable.

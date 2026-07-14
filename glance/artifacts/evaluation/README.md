# Experiment results

These files are the measured outputs used by the submission PDF.

## Protocol

- Training: 5,000 official Fashionpedia train images, one epoch, rank-16 LoRA,
  batch size 8, gradient accumulation 4, seed 19.
- Validation: 700 disjoint Fashionpedia validation images and 3,047 usable
  localized garment crops.
- Queries: 22 frozen queries in `data/evaluation/fashionpedia_val_native.jsonl`:
  12 single-attribute and 10 compositional queries.
- Relevance: official Fashionpedia garment categories plus deterministic
  palette colors inferred within annotated garment regions.
- Hardware for the final adapted index/evaluation: NVIDIA RTX 4060 Laptop GPU.

## Files

- `fashionpedia_val_base.json`: generic CLIP, base FashionCLIP, and the base
  attribute-aware system.
- `fashionpedia_val_lora.json`: the same protocol after loading the completed
  FashionCLIP LoRA adapter.
- `official_prompts_controlled_lora.json`: five disclosed synthetic probe
  prompts over the final 1,000-image corpus. This is a smoke test, not an
  independent benchmark.

## Overall comparison

Hit@k is binary query success; Recall@k is the fraction of every query's full
relevance set recovered.

| System | H@1 | H@5 | H@10 | R@10 | nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Vanilla CLIP | 27.27% | 54.55% | 72.73% | 13.26% | 0.2482 |
| Base FashionCLIP only | 63.64% | 72.73% | 81.82% | 17.88% | 0.3741 |
| Base attribute-aware | 68.18% | 72.73% | 86.36% | 47.25% | 0.6875 |
| LoRA FashionCLIP only | 45.45% | 86.36% | 90.91% | 24.26% | 0.4315 |
| LoRA attribute-aware | 68.18% | 81.82% | 95.45% | 52.07% | 0.7315 |

The adapter improves deeper recall and ranking quality in the complete system,
but FashionCLIP-only H@1 regresses. The report treats that as a limitation.

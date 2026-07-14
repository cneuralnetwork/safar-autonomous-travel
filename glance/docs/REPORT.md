# Glance submission report source notes

The final submission document is generated from `docs/submission.tex` into `output/pdf/glance_multimodal_fashion_retrieval.pdf`.

The report intentionally distinguishes:

- the measured 22-query, 700-image Fashionpedia validation benchmark;
- the exhaustive fixed-predicate benchmark over the final real Open Images context subset;
- the completed 1,000-image full-corpus indexing workflow; and
- the five synthetic, disclosed official-prompt probes, which are only an acceptance check.

The fashion benchmark uses official garment categories and palette-derived colors. Context candidates
are annotated by local `gemma3:4b` using both the full frame and a native Open Images Person crop,
then pass deterministic QA; this remains model-assisted evidence, not a claim of independent human
judgment. Baseline, post-LoRA, and real-context result files live in
`artifacts/evaluation/` and the completed adapter manifest lives in
`artifacts/fashionclip-lora-5000-e1/`.

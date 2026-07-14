# Controlled probe provenance

Five small synthetic images make the assignment’s sparse, fine-grained acceptance cases visible in a controlled way. They are included within the 300 context slots of the 1,000-image corpus, never used for LoRA training, and are explicitly tagged as `synthetic` / `held_out_controlled_probe` in JSONL. They are not a claim of real-world benchmark performance.

| Asset | Covered acceptance case | Manual attributes reviewed |
| --- | --- | --- |
| `controlled-yellow-raincoat.png` | bright yellow raincoat | yellow coat, wet urban street, walking |
| `controlled-modern-office-business.png` | business attire in a modern office | blue blazer, white shirt, office |
| `controlled-blue-shirt-park-bench.png` | blue shirt sitting on park bench | blue shirt, seated pose, park bench |
| `controlled-casual-city-walk.png` | casual weekend city walk | green hoodie, casual style, city street |
| `controlled-red-tie-white-shirt.png` | red tie + white shirt | red tie, white shirt, formal office |

Generation was performed with the project’s built-in image-generation workflow using photorealistic, single-subject prompts. Each asset was manually inspected before writing the record and approximate garment boxes. The raw generated assets are intentionally ignored by Git; run `glance build-controlled-probes` after placing the documented assets under `data/raw/synthetic/`.

Prompt intent is deliberately simple: one subject, one requested garment/color binding, no text/logos/watermarks, and a visible context. This makes the probes useful for checking parser and reranker behavior without pretending that they are naturally occurring labels.

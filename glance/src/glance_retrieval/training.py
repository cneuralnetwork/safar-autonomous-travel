"""LoRA adaptation on deterministic Fashionpedia captions and compositional hard negatives."""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .embeddings import OpenClipFashionEmbedder
from .schemas import ImageRecord


@dataclass(frozen=True)
class CaptionPair:
    image_path: Path
    positive: str
    hard_negative: str


def compositional_caption(record: ImageRecord) -> str:
    garments = []
    for garment in record.garments:
        parts = [garment.color, *garment.attributes[:2], garment.category]
        garments.append(" ".join(part for part in parts if part))
    outfit = " and ".join(garments) if garments else "an outfit"
    modifiers = []
    if record.styles:
        modifiers.append(record.styles[0])
    if record.scene:
        modifiers.append(f"in a {record.scene.replace('_', ' ')}")
    return f"a person wearing {outfit}" + (", " + " ".join(modifiers) if modifiers else "")


def compositional_hard_negative(record: ImageRecord) -> str:
    """Swap color bindings while retaining the same nouns, creating a meaningful near-miss."""

    garments = list(record.garments)
    colored = [garment for garment in garments if garment.color]
    if len(colored) >= 2:
        swapped = {colored[0].id: colored[1].color, colored[1].id: colored[0].color}
        phrases = [" ".join(part for part in [swapped.get(garment.id, garment.color), garment.category] if part) for garment in garments]
    elif colored:
        alternate = "white" if colored[0].color != "white" else "black"
        phrases = [" ".join(part for part in [alternate if garment.id == colored[0].id else garment.color, garment.category] if part) for garment in garments]
    else:
        phrases = [garment.category for garment in garments]
    return "a person wearing " + " and ".join(phrases or ["an outfit"])


def build_caption_pairs(records: list[ImageRecord]) -> list[CaptionPair]:
    pairs = []
    for record in records:
        path = Path(record.image_path)
        if path.is_file() and record.garments:
            pairs.append(CaptionPair(path, compositional_caption(record), compositional_hard_negative(record)))
    if not pairs:
        raise ValueError("No trainable records with images and garment labels")
    return pairs


def train_lora(
    pairs: list[CaptionPair],
    *,
    output_dir: Path,
    model_name: str = "models/marqo-fashionCLIP",
    epochs: int = 5,
    batch_size: int = 8,
    gradient_accumulation: int = 4,
    learning_rate: float = 1e-4,
    margin: float = 0.18,
    resume_from: Path | None = None,
    start_batch: int | None = None,
    max_batches: int | None = None,
    seed: int = 19,
) -> dict[str, int | float | bool]:
    """Adapt the local FashionCLIP transformer with contrastive + binding losses.

    This is intentionally a small adapter, not a full encoder fine-tune: it fits the internship
    scope and makes the model's compositional improvement measurable and reversible.

    Marqo's Hugging Face wrapper exposes its feature functions under ``torch.inference_mode()``,
    so it cannot be trained directly. We instead load the same downloaded OpenCLIP state dict and
    attach LoRA to its differentiable ``c_fc``/``c_proj`` MLP projections in both image and text
    towers. The frozen fused attention projections remain untouched; this is an intentional,
    reproducible compromise instead of a silently non-differentiable "training" path.

    ``max_batches`` and ``resume_from`` make a CPU run checkpointable.  The saved PEFT adapter,
    optimizer state, and deterministic batch offset let a constrained machine finish one complete
    epoch in short invocations rather than losing a real experiment to a job-time limit.
    """

    try:
        import torch
        from peft import LoraConfig, TaskType, get_peft_model
        from PIL import Image
        from torch.nn import functional as F
        from torch.optim import AdamW
        from torch.utils.data import DataLoader
    except ImportError as exc:  # pragma: no cover - optional GPU path
        raise RuntimeError("Install `.[ml]` to train the LoRA adapter.") from exc

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model_dir = Path(model_name)
    if not (model_dir / "model.safetensors").exists() or not (model_dir / "open_clip_config.json").exists():
        raise ValueError(
            "FashionCLIP LoRA training requires a local Marqo FashionCLIP snapshot with "
            "model.safetensors and open_clip_config.json."
        )
    if batch_size < 1 or epochs < 1 or gradient_accumulation < 1:
        raise ValueError("epochs, batch_size, and gradient_accumulation must be positive")
    if max_batches is not None and max_batches < 1:
        raise ValueError("max_batches must be positive when supplied")
    resume_path = Path(resume_from) if resume_from else None
    if resume_path and not (resume_path / "adapter_config.json").exists():
        raise ValueError(f"No saved PEFT adapter found at {resume_path}")

    base = OpenClipFashionEmbedder(
        model_dir,
        device=device,
        adapter_dir=resume_path,
        adapter_trainable=bool(resume_path),
    )
    processor = base.processor
    if resume_path:
        model = base.model.to(device)
    else:
        lora = LoraConfig(
            task_type=TaskType.FEATURE_EXTRACTION,
            r=16,
            lora_alpha=32,
            lora_dropout=0.05,
            target_modules=["c_fc", "c_proj"],
        )
        model = get_peft_model(base.model, lora).to(device)

    def collate(batch: list[CaptionPair]) -> tuple[object, object, object]:
        images = []
        for pair in batch:
            with Image.open(pair.image_path) as image:
                images.append(image.convert("RGB").copy())
        text_options = {
            "return_tensors": "pt",
            "padding": "max_length",
            "truncation": True,
            "max_length": 77,
        }
        positives = processor(text=[pair.positive for pair in batch], **text_options)
        negatives = processor(text=[pair.hard_negative for pair in batch], **text_options)
        image_inputs = processor(images=images, return_tensors="pt")
        return image_inputs, positives, negatives

    ordered_pairs = list(pairs)
    random.Random(seed).shuffle(ordered_pairs)
    epoch_pairs = ordered_pairs * epochs
    total_batches = math.ceil(len(epoch_pairs) / batch_size)
    state_path = (resume_path or output_dir) / "training_state.pt"
    state: dict[str, Any] = {}
    if resume_path and state_path.exists():
        state = torch.load(state_path, map_location=device, weights_only=False)
    if start_batch is None:
        start_batch = int(state.get("next_batch", 0))
    if not 0 <= start_batch < total_batches:
        raise ValueError(f"start_batch must be in [0, {total_batches - 1}], got {start_batch}")
    end_batch = min(total_batches, start_batch + max_batches) if max_batches else total_batches
    chunk_pairs = epoch_pairs[start_batch * batch_size : min(len(epoch_pairs), end_batch * batch_size)]
    loader = DataLoader(chunk_pairs, batch_size=batch_size, shuffle=False, collate_fn=collate)
    optimizer = AdamW((parameter for parameter in model.parameters() if parameter.requires_grad), lr=learning_rate)
    if state.get("optimizer"):
        optimizer.load_state_dict(state["optimizer"])

    model.train()
    optimizer.zero_grad(set_to_none=True)
    chunk_loss = 0.0
    step = 0
    for image_inputs, positives, negatives in loader:
        image_features = F.normalize(
            model.encode_image(image_inputs["pixel_values"].to(device)),
            dim=-1,
        )
        positive_features = F.normalize(model.encode_text(positives["input_ids"].to(device)), dim=-1)
        negative_features = F.normalize(model.encode_text(negatives["input_ids"].to(device)), dim=-1)
        logits = 20.0 * image_features @ positive_features.T
        labels = torch.arange(logits.shape[0], device=device)
        contrastive = (F.cross_entropy(logits, labels) + F.cross_entropy(logits.T, labels)) / 2
        true_scores = (image_features * positive_features).sum(dim=-1)
        negative_scores = (image_features * negative_features).sum(dim=-1)
        binding_loss = F.relu(margin - true_scores + negative_scores).mean()
        loss = contrastive + 0.5 * binding_loss
        (loss / gradient_accumulation).backward()
        step += 1
        if step % gradient_accumulation == 0:
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
        chunk_loss += float(loss.detach().cpu())
    if step % gradient_accumulation:
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(output_dir)
    processor.save_pretrained(output_dir)
    cumulative_loss = float(state.get("cumulative_loss", 0.0)) + chunk_loss
    cumulative_steps = int(state.get("cumulative_steps", 0)) + step
    complete = end_batch >= total_batches
    metrics: dict[str, int | float | bool] = {
        "train_loss": cumulative_loss / max(cumulative_steps, 1),
        "chunk_loss": chunk_loss / max(step, 1),
        "chunk_steps": step,
        "completed_batches": end_batch,
        "total_batches": total_batches,
        "pairs": len(epoch_pairs),
        "complete": complete,
    }
    (output_dir / "training_metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    torch.save(
        {
            "optimizer": optimizer.state_dict(),
            "next_batch": end_batch,
            "cumulative_loss": cumulative_loss,
            "cumulative_steps": cumulative_steps,
            "seed": seed,
            "total_batches": total_batches,
        },
        output_dir / "training_state.pt",
    )
    adapter_manifest = {
        "base_model_dir": str(model_dir),
        "architecture": "Marqo FashionCLIP / OpenCLIP ViT-B-16",
        "target_modules": ["c_fc", "c_proj"],
        "rank": 16,
        "margin": margin,
        "epochs": epochs,
        "batch_size": batch_size,
        "gradient_accumulation": gradient_accumulation,
        "seed": seed,
        "completed_batches": end_batch,
        "total_batches": total_batches,
    }
    (output_dir / "glance_adapter_manifest.json").write_text(
        json.dumps(adapter_manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return metrics

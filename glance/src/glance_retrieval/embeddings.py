"""Image/text encoder adapters. Heavy model imports are intentionally lazy."""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    @property
    def dimension(self) -> int: ...

    def embed_images(self, image_paths: list[Path]) -> np.ndarray: ...

    def embed_texts(self, texts: list[str]) -> np.ndarray: ...


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.clip(norms, 1e-12, None)


class TransformersCLIPEmbedder:
    """CLIP-compatible encoder supporting both generic CLIP and Marqo FashionCLIP."""

    def __init__(self, model_name: str, device: str = "auto") -> None:
        try:
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:  # pragma: no cover - optional ML dependency
            raise RuntimeError("Install `.[ml]` to use transformer embeddings.") from exc
        self._torch = torch
        self._device = "cuda" if device == "auto" and torch.cuda.is_available() else ("cpu" if device == "auto" else device)
        local_model = Path(model_name)
        trust_remote_code = "marqo" in model_name.lower() or (local_model.exists() and (local_model / "marqo_fashionCLIP.py").exists())
        self.model = AutoModel.from_pretrained(model_name, trust_remote_code=trust_remote_code)
        self.processor = AutoProcessor.from_pretrained(model_name, trust_remote_code=trust_remote_code, use_fast=False)
        self.model.to(self._device)
        self.model.eval()
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._dimension = int(self.embed_texts(["dimension probe"]).shape[1])
        return self._dimension

    def _to_device(self, inputs: object) -> object:
        return {key: value.to(self._device) for key, value in inputs.items()}  # type: ignore[union-attr]

    def _features(self, method_name: str, **kwargs: object) -> np.ndarray:
        method = getattr(self.model, method_name)
        # FashionCLIP exposes an explicit ``normalize`` argument, whereas Hugging Face's
        # stock CLIP methods do not.  Inspecting the signature avoids ignored-argument warnings
        # and lets both implementations share one adapter.
        try:
            supports_normalize = "normalize" in inspect.signature(method).parameters
        except (TypeError, ValueError):  # pragma: no cover - unusual remote-code wrappers
            supports_normalize = False
        with self._torch.inference_mode():
            features = method(**kwargs, normalize=True) if supports_normalize else method(**kwargs)
        if hasattr(features, "pooler_output"):
            features = features.pooler_output
        return _normalize(features.detach().float().cpu().numpy())

    def embed_images(self, image_paths: list[Path]) -> np.ndarray:
        from PIL import Image

        images = []
        for path in image_paths:
            with Image.open(path) as image:
                images.append(image.convert("RGB").copy())
        inputs = self._to_device(self.processor(images=images, return_tensors="pt", padding=True))
        vectors = self._features("get_image_features", pixel_values=inputs["pixel_values"])
        self._dimension = vectors.shape[1]
        return vectors

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        inputs = self._to_device(self.processor(text=texts, return_tensors="pt", padding=True, truncation=True))
        vectors = self._features("get_text_features", input_ids=inputs["input_ids"], attention_mask=inputs.get("attention_mask"))
        self._dimension = vectors.shape[1]
        return vectors


class OpenClipFashionEmbedder:
    """Local Marqo FashionCLIP adapter without a runtime Hub dependency.

    Marqo's remote-code wrapper initializes OpenCLIP from the Hub before loading its own state
    dictionary.  For a downloaded local snapshot, this adapter constructs the documented ViT-B/16
    architecture directly, loads ``model.safetensors``, and uses the repository's CLIP processor.
    This is both reproducible offline and avoids executing remote code at serving time.
    """

    def __init__(
        self,
        model_dir: str | Path,
        device: str = "auto",
        adapter_dir: str | Path | None = None,
        adapter_trainable: bool = False,
    ) -> None:
        try:
            import logging

            import open_clip
            import torch
            from safetensors.torch import load_file
            from transformers import CLIPProcessor
        except ImportError as exc:  # pragma: no cover - optional ML dependency
            raise RuntimeError("Install `.[ml]` including open-clip-torch to use local FashionCLIP.") from exc

        self.model_dir = Path(model_dir)
        weights_path = self.model_dir / "model.safetensors"
        if not weights_path.exists():
            raise FileNotFoundError(f"Local FashionCLIP weights are missing: {weights_path}")
        self._torch = torch
        self._device = "cuda" if device == "auto" and torch.cuda.is_available() else ("cpu" if device == "auto" else device)
        # OpenCLIP correctly notes that ``pretrained=None`` yields random weights. In this case
        # those weights are synchronously replaced by the local FashionCLIP state dict below, so
        # suppress only that transient startup warning.
        root_logger = logging.getLogger()
        prior_level = root_logger.level
        root_logger.setLevel(logging.ERROR)
        try:
            self.model = open_clip.create_model("ViT-B-16", pretrained=None, device=self._device)
        finally:
            root_logger.setLevel(prior_level)
        # The HF wrapper stores the OpenCLIP state dict under ``model.``; strip that prefix before
        # loading it into the actual OpenCLIP module. Strict loading catches model/revision drift.
        state = {key.removeprefix("model."): value for key, value in load_file(str(weights_path)).items()}
        self.model.load_state_dict(state, strict=True)
        self.processor = CLIPProcessor.from_pretrained(self.model_dir, use_fast=False)
        self._dimension = int(self.model.text_projection.shape[1])
        if adapter_dir:
            adapter_path = Path(adapter_dir)
            if not adapter_path.exists():
                raise FileNotFoundError(f"FashionCLIP adapter is missing: {adapter_path}")
            try:
                from peft import PeftModel
            except ImportError as exc:  # pragma: no cover - optional adapter runtime
                raise RuntimeError("Install `.[ml]` including peft to load a FashionCLIP LoRA adapter.") from exc
            self.model = PeftModel.from_pretrained(self.model, str(adapter_path), is_trainable=adapter_trainable)
        self.model.eval()

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_images(self, image_paths: list[Path]) -> np.ndarray:
        from PIL import Image

        images = []
        for path in image_paths:
            with Image.open(path) as image:
                images.append(image.convert("RGB").copy())
        inputs = self.processor(images=images, return_tensors="pt")
        with self._torch.inference_mode():
            features = self.model.encode_image(inputs["pixel_values"].to(self._device), normalize=True)
        return _normalize(features.detach().float().cpu().numpy())

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        # OpenCLIP expects a fixed 77-token sequence, unlike Transformers CLIP which permits
        # dynamic padding. The model card uses the same max-length convention.
        inputs = self.processor(
            text=texts,
            return_tensors="pt",
            padding="max_length",
            truncation=True,
            max_length=77,
        )
        with self._torch.inference_mode():
            features = self.model.encode_text(inputs["input_ids"].to(self._device), normalize=True)
        return _normalize(features.detach().float().cpu().numpy())


def make_fashion_embedder(
    model_name: str,
    device: str = "auto",
    adapter_dir: str | Path | None = None,
) -> Embedder:
    """Select the offline-safe FashionCLIP adapter when a local Marqo snapshot is provided."""

    model_path = Path(model_name)
    if model_path.exists() and (model_path / "model.safetensors").exists() and (model_path / "open_clip_config.json").exists():
        return OpenClipFashionEmbedder(model_path, device=device, adapter_dir=adapter_dir)
    if adapter_dir:
        raise ValueError("A FashionCLIP LoRA adapter requires a local base FashionCLIP snapshot.")
    return TransformersCLIPEmbedder(model_name, device=device)


def make_encoder_pair(
    generic_model: str,
    fashion_model: str,
    device: str = "auto",
    fashion_adapter: str | Path | None = None,
) -> EncoderPair:
    return EncoderPair(
        generic=TransformersCLIPEmbedder(generic_model, device=device),
        fashion=make_fashion_embedder(fashion_model, device=device, adapter_dir=fashion_adapter),
    )


class DeterministicEmbedder:
    """A repeatable fixture embedder for tests; never selected by the production service."""

    def __init__(self, dimension: int = 32) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def _embed(self, values: list[str]) -> np.ndarray:
        rows: list[np.ndarray] = []
        for value in values:
            vector = np.zeros(self.dimension, dtype=np.float32)
            tokens = value.lower().replace("_", " ").split()
            for token in tokens or [value]:
                index = int(hashlib.sha256(token.encode()).hexdigest(), 16) % self.dimension
                vector[index] += 1
            rows.append(vector)
        return _normalize(np.vstack(rows))

    def embed_images(self, image_paths: list[Path]) -> np.ndarray:
        return self._embed([path.stem.replace("-", " ") for path in image_paths])

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        return self._embed(texts)


class EncoderPair:
    """The two independent semantic spaces used by Glance's index."""

    def __init__(self, generic: Embedder, fashion: Embedder) -> None:
        self.generic = generic
        self.fashion = fashion

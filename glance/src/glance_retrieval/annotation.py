"""Local VLM annotation with schema validation and deterministic metadata merging."""

from __future__ import annotations

import base64
import io
import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .schemas import BoundingBox, Garment, ImageRecord
from .taxonomy import canonical_category, canonical_color

VisionGarmentCategory = Literal[
    "blazer",
    "shirt",
    "t-shirt",
    "hoodie",
    "sweater",
    "cardigan",
    "jacket",
    "coat",
    "vest",
    "dress",
    "jumpsuit",
    "pants",
    "shorts",
    "skirt",
    "tie",
    "scarf",
    "hat",
    "gloves",
    "belt",
    "shoes",
    "bag",
    "umbrella",
    "other",
]
VisionColor = Literal[
    "black",
    "white",
    "gray",
    "beige",
    "brown",
    "red",
    "orange",
    "yellow",
    "green",
    "blue",
    "purple",
    "pink",
    "multicolor",
    "other",
]
VisionActivity = Literal["sitting", "walking", "standing", "working", "posing", "talking", "other"]
VisionStyle = Literal["formal", "casual", "outerwear"]


class VisionGarment(BaseModel):
    category: VisionGarmentCategory
    color: VisionColor | None = None
    attributes: list[str] = Field(default_factory=list)


class VisionAnnotation(BaseModel):
    scene: Literal["office", "urban_street", "park", "home", "other"] | None = None
    activities: list[VisionActivity] = Field(max_length=3)
    styles: list[VisionStyle] = Field(max_length=3)
    garments: list[VisionGarment] = Field(max_length=6)
    caption: str = Field(min_length=5, max_length=240)
    confidence: Literal["high", "medium", "low"]


class VisionAnnotator(ABC):
    @abstractmethod
    def annotate(self, image_path: Path, focus_box: BoundingBox | None = None) -> VisionAnnotation:
        raise NotImplementedError


class OllamaGPUUnavailableError(RuntimeError):
    """Fatal guard failure: the requested Ollama model is no longer GPU-resident."""


class OllamaVisionAnnotator(VisionAnnotator):
    """Structured local vision annotation through Ollama's multimodal chat API."""

    ANNOTATION_VERSION = "fashion-context-v5-person-crop"
    PROMPT = """Inspect the supplied fashion photograph. When two images are present, the first is
the full scene and the second is a native-annotation crop of the main person; use the full image for
location and the crop for clothes. The scene is the physical location: office, urban_street, park,
home, other, or null. A garment category is a physical item such as shirt, blazer, pants, dress,
jacket, coat, tie, or shoes; never put a style word in garment.category. Attach each color to its
garment. List each visible physical garment once, with at most six principal items. Fill every JSON
field, using [] when no activity or style is supported, and always write one short factual caption.
Use only schema enum values. Never name or infer a city, country, nationality, landmark, company,
historical context, date, or event. Choose lower confidence when ambiguous. Do not guess unseen
facts."""

    def __init__(
        self,
        model_name: str = "gemma3:4b",
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 180.0,
        max_image_size: int = 512,
    ) -> None:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - core dependency in the project
            raise RuntimeError("Install requests to run Ollama annotation.") from exc
        self._requests = requests
        self.model_name = f"ollama/{model_name}@{self.ANNOTATION_VERSION}"
        self.runtime_model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_image_size = max_image_size

    def gpu_allocation_bytes(self) -> int:
        """Return Ollama's reported VRAM allocation for this model, or zero when CPU-only."""

        response = self._requests.get(f"{self.base_url}/api/ps", timeout=min(self.timeout, 15.0))
        response.raise_for_status()
        for model in response.json().get("models", []):
            name = str(model.get("name") or model.get("model") or "")
            if name == self.runtime_model_name or name.split(":", 1)[0] == self.runtime_model_name.split(":", 1)[0]:
                return int(model.get("size_vram") or 0)
        return 0

    def require_gpu_allocation(self) -> int:
        """Return VRAM allocation or abort before a batch can drift onto the CPU."""

        allocation = self.gpu_allocation_bytes()
        if not allocation:
            raise OllamaGPUUnavailableError(
                "Ollama loaded the vision model without a GPU allocation; refusing CPU fallback"
            )
        return allocation

    def annotate(
        self,
        image_path: Path,
        focus_box: BoundingBox | None = None,
    ) -> VisionAnnotation:  # pragma: no cover - local model runtime path
        from PIL import Image

        with Image.open(image_path) as source:
            full_image = source.convert("RGB")
        images = [full_image]
        if focus_box is not None:
            margin_x = focus_box.width * 0.12
            margin_y = focus_box.height * 0.08
            left = max(0, round((focus_box.x - margin_x) * full_image.width))
            top = max(0, round((focus_box.y - margin_y) * full_image.height))
            right = min(full_image.width, round((focus_box.x + focus_box.width + margin_x) * full_image.width))
            bottom = min(full_image.height, round((focus_box.y + focus_box.height + margin_y) * full_image.height))
            if right > left and bottom > top:
                images.append(full_image.crop((left, top, right, bottom)))
        encoded_images: list[str] = []
        for image in images:
            image.thumbnail((self.max_image_size, self.max_image_size), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=88, optimize=True)
            encoded_images.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
        response = self._requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.runtime_model_name,
                "messages": [{"role": "user", "content": self.PROMPT, "images": encoded_images}],
                "format": VisionAnnotation.model_json_schema(),
                "stream": False,
                "keep_alive": "30m",
                "options": {"temperature": 0, "num_predict": 300, "num_ctx": 4096},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return normalize_annotation(parse_annotation_json(payload["message"]["content"]))


class QwenVisionAnnotator(VisionAnnotator):
    """Qwen2.5-VL JSON annotator; instantiated only in the indexer process."""

    PROMPT = """Inspect this fashion photograph. Return JSON only, matching this schema:
{"scene":"office|urban_street|park|home|other|null","activities":["..."],
"styles":["formal|casual|outerwear"],"garments":[{"category":"...","color":"...|null","attributes":["..."]}],
"caption":"one factual sentence","confidence":"high|medium|low"}.
Do not guess details that are not visible. Keep color attached to its garment."""

    def __init__(
        self,
        model_name: str = "Qwen/Qwen2.5-VL-3B-Instruct",
        device: str = "auto",
        *,
        load_in_4bit: bool = False,
        min_pixels: int = 256 * 28 * 28,
        max_pixels: int = 512 * 28 * 28,
    ) -> None:
        try:
            import torch
            from transformers import (
                AutoProcessor,
                BitsAndBytesConfig,
                Qwen2_5_VLForConditionalGeneration,
            )
        except ImportError as exc:  # pragma: no cover - depends on optional ML extra
            raise RuntimeError("Install `.[ml]` to run Qwen annotation.") from exc
        if load_in_4bit and not torch.cuda.is_available():
            raise RuntimeError("4-bit Qwen annotation requires a CUDA device.")
        self._torch = torch
        self.model_name = model_name
        self.processor = AutoProcessor.from_pretrained(model_name, min_pixels=min_pixels, max_pixels=max_pixels)
        model_options: dict[str, object] = {"torch_dtype": "auto"}
        if load_in_4bit:
            model_options.update(
                {
                    "device_map": "auto",
                    "quantization_config": BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_compute_dtype=torch.float16,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_use_double_quant=True,
                    ),
                }
            )
        elif device == "auto":
            model_options["device_map"] = "auto"
        self.model = Qwen2_5_VLForConditionalGeneration.from_pretrained(model_name, **model_options)
        if device != "auto" and not load_in_4bit:
            self.model.to(device)
        self.model.eval()

    def annotate(
        self,
        image_path: Path,
        focus_box: BoundingBox | None = None,
    ) -> VisionAnnotation:  # pragma: no cover - heavyweight model path
        from PIL import Image

        with Image.open(image_path) as image:
            rgb = image.convert("RGB")
            visual_content: list[dict[str, object]] = [{"type": "image", "image": rgb}]
            if focus_box is not None:
                left = round(focus_box.x * rgb.width)
                top = round(focus_box.y * rgb.height)
                right = round((focus_box.x + focus_box.width) * rgb.width)
                bottom = round((focus_box.y + focus_box.height) * rgb.height)
                visual_content.append({"type": "image", "image": rgb.crop((left, top, right, bottom))})
            visual_content.append({"type": "text", "text": self.PROMPT})
            messages = [{"role": "user", "content": visual_content}]
            prompt = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            images = [item["image"] for item in visual_content if item["type"] == "image"]
            inputs = self.processor(text=[prompt], images=images, return_tensors="pt", padding=True)
            inputs = inputs.to(self.model.device)
            with self._torch.inference_mode():
                generated = self.model.generate(**inputs, max_new_tokens=300, do_sample=False)
            answer = self.processor.batch_decode(generated[:, inputs.input_ids.shape[1] :], skip_special_tokens=True)[0]
        return normalize_annotation(parse_annotation_json(answer))


class MetadataOnlyAnnotator(VisionAnnotator):
    """Safe fallback: preserves native labels but deliberately emits no invented visual facts."""

    model_name = "metadata-only"

    def annotate(self, image_path: Path, focus_box: BoundingBox | None = None) -> VisionAnnotation:
        return VisionAnnotation(activities=[], styles=[], garments=[], caption="No visual annotation available.", confidence="low")


def parse_annotation_json(text: str) -> VisionAnnotation:
    clean = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = clean.find("{"), clean.rfind("}")
    if start < 0 or end < start:
        raise ValueError("VLM response does not contain a JSON object")
    return VisionAnnotation.model_validate(json.loads(clean[start : end + 1]))


def normalize_annotation(annotation: VisionAnnotation) -> VisionAnnotation:
    """Remove duplicated/nonphysical garment rows and add transparent style implications."""

    garments_by_key: dict[tuple[str, str | None], VisionGarment] = {}
    for garment in annotation.garments:
        category = garment.category
        attributes = list(dict.fromkeys(attribute.strip().lower() for attribute in garment.attributes if attribute.strip()))
        if category == "other":
            continue
        if category == "shirt" and "hoodie" in attributes:
            category = "hoodie"
            attributes = [attribute for attribute in attributes if attribute != "hoodie"]
        key = (category, garment.color)
        prior = garments_by_key.get(key)
        if prior:
            attributes = list(dict.fromkeys([*prior.attributes, *attributes]))
        garments_by_key[key] = VisionGarment(category=category, color=garment.color, attributes=attributes)
    garments = list(garments_by_key.values())
    categories = {garment.category for garment in garments}
    styles = list(dict.fromkeys(annotation.styles))
    if categories.intersection({"coat", "jacket"}) and "outerwear" not in styles:
        styles.append("outerwear")
    if categories.intersection({"blazer", "tie"}) and "formal" not in styles:
        styles.append("formal")
    if categories.intersection({"t-shirt", "hoodie", "sweater", "cardigan", "shorts"}) and "casual" not in styles:
        styles.append("casual")
    caption = annotation.caption.lower()
    scene = reconcile_scene(annotation.scene, annotation.caption)
    activities = list(dict.fromkeys(annotation.activities))
    activity_phrases = {
        "sitting": ("sitting", "seated"),
        "walking": ("walking", "walks"),
        "standing": ("standing", "stands"),
        "working": ("working", "works at"),
    }
    for activity, phrases in activity_phrases.items():
        if activity not in activities and any(phrase in caption for phrase in phrases):
            activities.append(activity)
    return annotation.model_copy(
        update={"scene": scene, "garments": garments, "styles": styles, "activities": activities[:3]}
    )


def reconcile_scene(scene: str | None, caption: str) -> str | None:
    """Reconcile a scene enum with explicit venue evidence using whole-word phrases.

    Small VLMs sometimes emit an internally inconsistent pair such as ``home`` plus "at a
    restaurant."  The caption is constrained to a short visible description, so explicit venue
    phrases are a useful deterministic correction.  Word boundaries are essential: ``officer``
    must never count as ``office``.
    """

    text = caption.lower()

    def mentions(*phrases: str) -> bool:
        return any(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) for phrase in phrases)

    if mentions("city street", "urban street", "street", "sidewalk", "downtown", "crosswalk"):
        return "urban_street"
    if mentions(
        "restaurant",
        "cafe",
        "café",
        "runway",
        "auto show",
        "car show",
        "racetrack",
        "race track",
        "stadium",
        "concert",
        "stage",
        "waiting area",
        "lobby",
        "hospital",
        "school",
        "classroom",
        "church",
        "temple",
        "hotel",
        "pool",
        "beach",
        "lake",
        "boat",
        "airport",
        "station",
        "market",
        "store",
        "shop",
        "museum",
        "gym",
        "wedding",
        "party",
        "air force",
        "soldier",
        "military",
        "statue",
    ):
        return "other"
    if mentions("office", "workplace", "conference room"):
        return "office"
    if mentions("park", "public garden", "park bench", "garden"):
        return "park"
    if mentions("living room", "bedroom", "at home", "inside a home", "home interior", "apartment"):
        return "home"
    if scene in {"home", "office"} and mentions(
        "roof",
        "truck",
        "scooter",
        "motorcycle",
        "bicycle",
        "kayak",
        "building facade",
        "walking in a group",
        "riding",
    ):
        return "other"
    return scene


def merge_annotation(record: ImageRecord, annotation: VisionAnnotation) -> ImageRecord:
    """Native Fashionpedia clothing labels win; VLM enriches scene/style/context only."""

    native_garments = record.garments
    garments = native_garments or [
        Garment(
            id=f"{record.image_id}-vlm-{index}",
            category=canonical_category(garment.category),
            color=canonical_color(garment.color),
            attributes=garment.attributes,
            confidence=annotation.confidence,
        )
        for index, garment in enumerate(annotation.garments)
    ]
    return record.model_copy(
        update={
            "scene": annotation.scene or record.scene,
            "activities": annotation.activities or record.activities,
            "styles": annotation.styles or record.styles,
            "garments": garments,
            "caption": annotation.caption or record.caption,
            "confidence": record.confidence if record.confidence in {"native", "audited"} else annotation.confidence,
        }
    )

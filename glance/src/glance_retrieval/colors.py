"""Palette-level garment color inference from Fashionpedia masks or crop regions."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from .schemas import BoundingBox
from .taxonomy import COLORS

PALETTE_RGB: dict[str, tuple[int, int, int]] = {
    "black": (22, 22, 24), "white": (242, 242, 238), "gray": (130, 132, 135),
    "beige": (202, 183, 145), "brown": (105, 68, 47), "red": (191, 49, 49),
    "orange": (220, 119, 38), "yellow": (228, 193, 38), "green": (61, 132, 76),
    "blue": (50, 102, 179), "purple": (117, 76, 151), "pink": (215, 112, 150),
}


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Vectorized sRGB to CIE Lab conversion; adequate for coarse named colors."""

    values = rgb.astype(np.float32) / 255.0
    linear = np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)
    xyz = linear @ np.array(
        [[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]],
        dtype=np.float32,
    ).T
    xyz = xyz / np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack((116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])), axis=-1)


_PALETTE_LAB = _rgb_to_lab(np.array(list(PALETTE_RGB.values()), dtype=np.float32))


def _crop_pixels(image: Image.Image, bbox: BoundingBox | None, mask: np.ndarray | None) -> np.ndarray:
    array = np.asarray(image.convert("RGB"))
    if bbox:
        height, width = array.shape[:2]
        left, top = int(bbox.x * width), int(bbox.y * height)
        right, bottom = int((bbox.x + bbox.width) * width), int((bbox.y + bbox.height) * height)
        array = array[top:bottom, left:right]
        if mask is not None:
            mask = mask[top:bottom, left:right]
    if mask is not None:
        if mask.shape != array.shape[:2]:
            raise ValueError("mask dimensions must match image dimensions")
        pixels = array[mask.astype(bool)]
    else:
        pixels = array.reshape(-1, 3)
    return pixels


def _palette_color_from_image(image: Image.Image, *, bbox: BoundingBox | None, mask: np.ndarray | None) -> str:
    """Return the nearest display color without reopening a source image."""

    pixels = _crop_pixels(image, bbox, mask)
    if len(pixels) == 0:
        raise ValueError("cannot infer color from an empty garment region")
    median_lab = _rgb_to_lab(np.median(pixels, axis=0, keepdims=True))[0]
    distances = np.linalg.norm(_PALETTE_LAB - median_lab, axis=1)
    return COLORS[int(np.argmin(distances))]


def infer_palette_color(
    image_path: str | Path,
    *,
    bbox: BoundingBox | None = None,
    mask: np.ndarray | None = None,
) -> str:
    """Return the nearest of the project's twelve display colors.

    A median is deliberately used rather than a mean: small highlights and skin/background leakage
    from a crop have far less impact on the garment's reported color.
    """

    with Image.open(image_path) as image:
        return _palette_color_from_image(image, bbox=bbox, mask=mask)


def infer_palette_colors(image_path: str | Path, bboxes: list[BoundingBox | None]) -> list[str | None]:
    """Infer multiple box colors in one image decode for efficient train-set preparation."""

    with Image.open(image_path) as image:
        colors: list[str | None] = []
        for bbox in bboxes:
            try:
                colors.append(_palette_color_from_image(image, bbox=bbox, mask=None) if bbox else None)
            except (OSError, ValueError):
                colors.append(None)
        return colors

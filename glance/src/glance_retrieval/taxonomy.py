"""A compact controlled vocabulary and alias normalizer for fashion retrieval."""

from __future__ import annotations

import re

COLORS = (
    "black", "white", "gray", "beige", "brown", "red", "orange", "yellow", "green",
    "blue", "purple", "pink",
)

COLOR_ALIASES = {
    "grey": "gray", "navy": "blue", "teal": "blue", "cyan": "blue", "maroon": "red",
    "burgundy": "red", "cream": "beige", "tan": "beige", "khaki": "beige", "gold": "yellow",
    "violet": "purple", "lilac": "purple", "magenta": "pink",
}

CATEGORY_ALIASES = {
    "raincoat": "coat", "trench": "coat", "jacket": "jacket", "blazer": "blazer",
    "suit jacket": "blazer", "button down": "shirt", "button-down": "shirt", "shirt": "shirt",
    "dress shirt": "shirt", "t shirt": "t-shirt", "tee": "t-shirt", "hoodie": "hoodie",
    "sweatshirt": "hoodie", "jeans": "pants", "trousers": "pants", "slacks": "pants",
    "chinos": "pants", "pants": "pants", "tie": "tie", "dress": "dress", "skirt": "skirt", "coat": "coat",
    "overcoat": "coat", "cardigan": "cardigan", "sweater": "sweater", "shoes": "shoes",
    "sneakers": "shoes", "boots": "shoes", "bag": "bag", "hat": "hat",
    # Fashionpedia's comma-delimited category names map into the public query vocabulary.
    "shirt, blouse": "shirt", "top, t-shirt, sweatshirt": "t-shirt",
    "bag, wallet": "bag", "headband, head covering, hair accessory": "hat",
    "shoe": "shoes", "tights, stockings": "tights", "leg warmer": "tights",
    "cape": "coat", "sock": "socks", "glasses": "glasses", "glove": "gloves",
}

# Fashionpedia additionally annotates apparel parts and visual decorations. They are valuable for
# segmentation research but should not become independent retrieval objects in this assignment.
RETRIEVABLE_CATEGORIES = frozenset(
    {
        "shirt", "t-shirt", "sweater", "cardigan", "jacket", "vest", "pants", "shorts",
        "skirt", "coat", "dress", "jumpsuit", "glasses", "hat", "tie", "gloves", "watch",
        "belt", "tights", "socks", "shoes", "bag", "scarf", "umbrella",
    }
)

SCENE_ALIASES = {
    "office": "office", "workplace": "office", "conference room": "office",
    "urban": "urban_street", "city": "urban_street", "street": "urban_street",
    "sidewalk": "urban_street", "downtown": "urban_street", "park": "park",
    "garden": "park", "home": "home", "house": "home", "living room": "home",
    "apartment": "home",
}

STYLE_ALIASES = {
    "formal": "formal", "business": "formal", "professional": "formal", "smart": "formal",
    "casual": "casual", "weekend": "casual", "relaxed": "casual", "streetwear": "casual",
    "outerwear": "outerwear", "rain": "outerwear",
}

ACTIVITY_ALIASES = {
    "sitting": "sitting", "seated": "sitting", "sits": "sitting", "walking": "walking",
    "walk": "walking", "standing": "standing", "standing up": "standing",
}


def canonicalize(value: str, aliases: dict[str, str]) -> str:
    normalized = re.sub(r"\s+", " ", value.lower().strip())
    return aliases.get(normalized, normalized)


def find_mentions(text: str, aliases: dict[str, str]) -> list[tuple[int, int, str]]:
    """Return non-overlapping alias mentions, preferring longer phrases."""

    lower = text.lower()
    found: list[tuple[int, int, str]] = []
    for alias, canonical in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        for match in re.finditer(rf"(?<!\w){re.escape(alias)}(?!\w)", lower):
            start, end = match.span()
            if not any(start < existing_end and end > existing_start for existing_start, existing_end, _ in found):
                found.append((start, end, canonical))
    return sorted(found)


def canonical_color(value: str | None) -> str | None:
    return canonicalize(value, COLOR_ALIASES) if value else None


def canonical_category(value: str) -> str:
    return canonicalize(value, CATEGORY_ALIASES)

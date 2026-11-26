"""Cuisine matching across providers.

Provider taxonomies disagree at the edges ("Cafe" vs "Coffee", "Veg" vs "Vegetarian"),
so matching is accent/case-insensitive substring matching plus a deliberately small
alias set. No fuzzy scoring: a cuisine filter should be predictable.
"""

from __future__ import annotations

import unicodedata

# Only genuinely synonymous labels. Everything else must match literally.
ALIASES: dict[str, tuple[str, ...]] = {
    "cafe": ("cafe", "coffee"),
    "coffee": ("coffee", "cafe"),
    "veg": ("vegetarian", "veg"),
    "vegetarian": ("vegetarian", "veg"),
    "dessert": ("dessert", "ice cream"),
    "ice cream": ("ice cream", "dessert"),
}


def normalize(text: str) -> str:
    """'Café / Coffee' -> 'cafe coffee'."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = ascii_text.casefold().replace("&", " and ")
    for char in ",/|-":
        cleaned = cleaned.replace(char, " ")
    return " ".join(cleaned.split())


def expand(term: str) -> tuple[str, ...]:
    key = normalize(term)
    return ALIASES.get(key, (key,))


def matches(cuisines: list[str], requested: list[str]) -> bool:
    """True when any requested cuisine matches any restaurant cuisine (OR semantics)."""
    if not requested:
        return True
    offered = [normalize(c) for c in cuisines if c]
    if not offered:
        return False
    for term in requested:
        for wanted in expand(term):
            if not wanted:
                continue
            for cuisine in offered:
                if wanted == cuisine or wanted in cuisine or cuisine in wanted:
                    return True
    return False


def parse_cuisine_args(values: list[str] | None) -> list[str]:
    """Flatten repeated/comma-separated CLI values, preserving order and de-duplicating."""
    seen: set[str] = set()
    parsed: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            term = part.strip()
            key = normalize(term)
            if term and key not in seen:
                seen.add(key)
                parsed.append(term)
    return parsed

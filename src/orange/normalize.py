"""Parsing and geo helpers: turn provider strings into comparable numbers."""

from __future__ import annotations

import math
import re
from typing import Any

from .models import Offer, Restaurant

EARTH_RADIUS_KM = 6371.0088

_PERCENT = re.compile(r"(\d{1,3})\s*%")
_MONEY = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+)", re.IGNORECASE)
_PLAIN_MONEY = re.compile(r"^\s*(?:₹|rs\.?|inr)?\s*([\d,]+)\s*(?:for\s*(?:two|2))?\s*$", re.IGNORECASE)
_MIN_SPEND = re.compile(
    r"(?:above|over|minimum(?:\s+(?:spend|bill|order|value))?(?:\s+of)?)\s*(?:₹|rs\.?|inr)?\s*([\d,]{3,})",
    re.IGNORECASE,
)
_KM = re.compile(r"([\d.]+)\s*km", re.IGNORECASE)
_M = re.compile(r"([\d.]+)\s*m\b", re.IGNORECASE)

PERK_WORDS = (
    "free",
    "complimentary",
    "1+1",
    "2+1",
    "buy 1 get 1",
    "buy one get one",
    "unlimited",
    "bottomless",
    "welcome drink",
    "dessert",
    "mocktail",
)

_CONDITION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dine-in", ("dine-in", "dine in", "dinein")),
    ("takeaway-only", ("takeaway", "take-away", "parcel")),
    ("weekend-excluded", ("not valid on weekend", "weekends excluded", "excluding weekend", "except weekend")),
    ("weekday-only", ("mon-thu", "mon to thu", "monday to thursday", "weekdays only", "weekday only")),
    ("lunch-only", ("lunch only", "lunch-only", "valid for lunch", "lunch hours")),
    ("no-alcohol", ("not valid on alcohol", "excluding alcohol", "alcohol excluded", "on food only")),
    ("non-stackable", ("cannot be clubbed", "can not be clubbed", "not valid with other", "no other offer")),
    ("advance-booking", ("advance booking", "prior booking", "pre-book", "reservation required")),
    ("one-per-table", ("one per table", "once per table", "one coupon per")),
    ("up-to", ("up to", "upto")),
)
_CARD_WORDS = ("hdfc", "icici", "axis", "sbi", "kotak", "amex", "bank card", "credit card", "debit card")


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return round(2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a)), 3)


def parse_distance(value: Any) -> float | None:
    """'4.2 km' -> 4.2, '850 m' -> 0.85, 3.1 -> 3.1."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 3)
    text = str(value).strip()
    if not text:
        return None
    if match := _KM.search(text):
        return round(float(match.group(1)), 3)
    if match := _M.search(text):
        return round(float(match.group(1)) / 1000.0, 3)
    try:
        return round(float(text), 3)
    except ValueError:
        return None


def parse_cost_for_two(value: Any) -> float | None:
    """'₹1,200 for two' -> 1200.0 ; '1200' -> 1200.0."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if match := _PLAIN_MONEY.match(text):
        return float(match.group(1).replace(",", ""))
    if match := _MONEY.search(text):
        return float(match.group(1).replace(",", ""))
    digits = re.sub(r"[^\d.]", "", text)
    return float(digits) if digits else None


def parse_rating(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        value = value.get("value") or value.get("rating")
        return parse_rating(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def extract_conditions(text: str) -> list[str]:
    low = text.lower()
    found = [tag for tag, needles in _CONDITION_RULES if any(n in low for n in needles)]
    if any(word in low for word in _CARD_WORDS):
        found.append("card-required")
    return sorted(set(found))


def parse_offer(
    title: str,
    description: str = "",
    *,
    source: str = "offers",
    provider_rank: int = 0,
) -> Offer:
    """Best-effort parse of a free-text offer into a structured, comparable shape."""
    title = (title or "").strip()
    description = (description or "").strip()
    text = f"{title} {description}".strip()
    low = text.lower()
    conditions = extract_conditions(text)
    min_spend = None
    if match := _MIN_SPEND.search(text.replace(",", "")):
        min_spend = float(match.group(1))

    if match := _PERCENT.search(text):
        percent = float(match.group(1))
        return Offer(
            title=title,
            description=description,
            offer_type="percent",
            value=min(percent, 100.0),
            min_spend=min_spend,
            conditions=conditions,
            source=source,
            provider_rank=provider_rank,
        )

    if match := _MONEY.search(text):
        if any(word in low for word in ("off", "discount", "cashback", "save", "saving")):
            return Offer(
                title=title,
                description=description,
                offer_type="flat",
                value=float(match.group(1).replace(",", "")),
                min_spend=min_spend,
                conditions=conditions,
                source=source,
                provider_rank=provider_rank,
            )

    if any(word in low for word in PERK_WORDS):
        return Offer(
            title=title,
            description=description,
            offer_type="perk",
            value=0.0,
            min_spend=min_spend,
            conditions=conditions,
            source=source,
            provider_rank=provider_rank,
        )

    return Offer(
        title=title,
        description=description,
        offer_type="unknown",
        value=0.0,
        min_spend=min_spend,
        conditions=conditions,
        source=source,
        provider_rank=provider_rank,
    )


def normalize_distance(restaurant: Restaurant, anchor_lat: float, anchor_lng: float) -> None:
    """Prefer the provider's own road distance; fall back to great-circle."""
    if restaurant.distance_km is not None:
        restaurant.distance_source = "provider"
        return
    if restaurant.lat is not None and restaurant.lng is not None:
        restaurant.distance_km = haversine_km(anchor_lat, anchor_lng, restaurant.lat, restaurant.lng)
        restaurant.distance_source = "haversine"
    else:
        restaurant.distance_source = "unknown"


def attach_offer_context(restaurant: Restaurant) -> None:
    """Give every offer the restaurant's per-person spend for value scaling."""
    for offer in restaurant.offers:
        offer.per_person_ref = restaurant.per_person

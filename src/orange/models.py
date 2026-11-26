"""Domain models shared by every provider adapter."""

from __future__ import annotations

import dataclasses
import datetime
from dataclasses import dataclass, field
from typing import Any

from .timeutil import format_minutes, parse_minutes, window_label


@dataclass(slots=True)
class Offer:
    """A single dine-in offer, already parsed but not yet judged."""

    title: str
    description: str = ""
    offer_type: str = "unknown"  # percent | flat | perk | unknown
    value: float = 0.0  # percent points, or INR for flat offers
    min_spend: float | None = None
    conditions: list[str] = field(default_factory=list)
    source: str = "offers"  # offers | bankOffers | deals
    provider_rank: int = 0
    # What one person typically spends here — flat ₹ offers are worth more at cheap
    # restaurants, so percentage value must be relative to this, not the budget cap.
    per_person_ref: float | None = None

    # Filled in by a judge (rubric, LLM or replay).
    score: float = 0.0
    effective_pct: float = 0.0
    flags: list[str] = field(default_factory=list)
    rationale: str = ""
    judged_by: str = ""

    @property
    def text(self) -> str:
        return f"{self.title} {self.description}".strip()

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(slots=True)
class Slot:
    """A bookable reservation slot."""

    date: str
    display_time: str
    group: str = ""
    is_free: bool = True
    booking_price: float = 0.0
    inventory: int = 0
    slot_id: str = ""
    item_id: str = ""
    reservation_time: int = 0
    duration_min: int = 90

    @property
    def start_minutes(self) -> int | None:
        return parse_minutes(self.display_time)

    @property
    def end_label(self) -> str:
        start = self.start_minutes
        return format_minutes(start + self.duration_min) if start is not None else ""

    @property
    def window_label(self) -> str:
        end = self.end_label
        return f"{self.display_time}–{end}" if end else self.display_time

    def to_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data["window"] = self.window_label
        return data


@dataclass(slots=True)
class Restaurant:
    """Provider-agnostic restaurant record."""

    provider: str
    provider_id: str
    name: str
    cuisines: list[str] = field(default_factory=list)
    require_availability: bool = False
    locality: str = ""
    area: str = ""
    lat: float | None = None
    lng: float | None = None
    distance_km: float | None = None
    distance_source: str = "provider"  # provider | haversine
    cost_for_two: float | None = None
    rating: float | None = None
    rating_count: int = 0
    offers: list[Offer] = field(default_factory=list)
    slots: list[Slot] = field(default_factory=list)
    booking_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def per_person(self) -> float | None:
        if self.cost_for_two is None:
            return None
        return round(self.cost_for_two / 2.0, 2)

    @property
    def offer_score(self) -> float:
        return round(max((o.score for o in self.offers), default=0.0), 2)

    @property
    def best_offer(self) -> Offer | None:
        """The offer worth the most cash at this party's reference bill."""
        return max(self.offers, key=lambda o: (o.effective_pct, o.score, -o.provider_rank), default=None)

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        data = {
            "provider": self.provider,
            "provider_id": self.provider_id,
            "name": self.name,
            "cuisines": self.cuisines,
            "locality": self.locality,
            "area": self.area,
            "lat": self.lat,
            "lng": self.lng,
            "distance_km": self.distance_km,
            "distance_source": self.distance_source,
            "cost_for_two": self.cost_for_two,
            "per_person": self.per_person,
            "rating": self.rating,
            "rating_count": self.rating_count,
            "offer_score": self.offer_score,
            "offers": [o.to_dict() for o in self.offers],
            "slots": [s.to_dict() for s in self.slots],
            "booking_url": self.booking_url,
        }
        if include_raw:
            data["raw"] = self.raw
        return data


@dataclass(slots=True)
class QueryContext:
    """Everything the judge needs to reason about whether an offer is actually useful."""

    area: str = "Koramangala"
    lat: float = 12.9352
    lng: float = 77.6245
    budget_per_person: float = 1000.0
    radius_km: float = 5.0
    party_size: int = 2
    date: str = "2026-11-27"
    time: str = "20:00"
    time_end: str = "22:00"
    duration_min: int = 90
    cuisines: list[str] = field(default_factory=list)
    require_availability: bool = False
    # Expected spend per person. None means "use the provider's cost for two ÷ 2".
    spend_per_person: float | None = None

    @property
    def is_dinner(self) -> bool:
        hour = int(self.time.split(":")[0])
        return hour >= 17

    @property
    def budget_for_party(self) -> float:
        return self.budget_per_person * self.party_size

    def expected_bill(self, per_person: float | None) -> float:
        """Party bill at the restaurant's average spend.

        The real bill is unknown before ordering, so offers are valued against a stated
        reference: the provider's cost-for-two (per person × party), or ``--spend`` when
        the caller supplies an expectation.
        """
        reference = self.spend_per_person or per_person or self.budget_per_person
        return round(max(reference, 1.0) * self.party_size, 2)

    @property
    def weekday(self) -> str | None:
        try:
            return datetime.date.fromisoformat(self.date).strftime("%A")
        except ValueError:
            return None

    @property
    def is_weekend(self) -> bool | None:
        try:
            return datetime.date.fromisoformat(self.date).weekday() >= 5
        except ValueError:
            return None

    @property
    def start_minutes(self) -> int | None:
        return parse_minutes(self.time)

    @property
    def end_minutes(self) -> int | None:
        """End of the requested booking window; falls back to start + duration."""
        explicit = parse_minutes(self.time_end)
        if explicit is not None:
            return explicit
        start = self.start_minutes
        return start + self.duration_min if start is not None else None

    @property
    def window_label(self) -> str:
        end = format_minutes(self.end_minutes) if self.end_minutes is not None else ""
        label = window_label(self.time, end or self.time_end)
        return label or self.time

    def slot_in_window(self, slot: Slot) -> bool:
        """True when the slot starts inside [time, time_end]."""
        start, end = self.start_minutes, self.end_minutes
        slot_start = slot.start_minutes
        if start is None or slot_start is None:
            return False
        if end is None:
            return slot_start >= start
        return start <= slot_start <= end

    def to_judge_dict(self) -> dict[str, Any]:
        return {
            "area": self.area,
            "budget_per_person_inr": self.budget_per_person,
            "party_size": self.party_size,
            "budget_for_party_inr": self.budget_for_party,
            "requested_date": self.date,
            "requested_weekday": self.weekday,
            "is_weekend": self.is_weekend,
            "requested_time": self.time,
            "requested_time_end": self.time_end,
            "window": self.window_label,
            "meal": "dinner" if self.is_dinner else "lunch",
            "cuisines": list(self.cuisines),
            "require_availability": self.require_availability,
            "spend_per_person_inr": self.spend_per_person,
        }

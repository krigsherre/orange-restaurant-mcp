"""Conversation state for the chat agent: filters, cached discovery, last results."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ...domain.models import BookingOutcome
from ...domain.cuisine import parse_cuisine_args
from ...domain.models import QueryContext, Restaurant

DEFAULT_LAT = 12.9352
DEFAULT_LNG = 77.6245


def default_filters(today: str | None = None) -> dict[str, Any]:
    return {
        "area": "Koramangala",
        "lat": DEFAULT_LAT,
        "lng": DEFAULT_LNG,
        "budget": 1000.0,
        "radius": 5.0,
        "party": 2,
        "date": today or date.today().isoformat(),
        "time_start": "20:00",
        "time_end": "22:00",
        "duration": 90,
        "cuisines": [],
        "require_availability": False,
        "spend": None,
    }


@dataclass
class ChatSession:
    filters: dict[str, Any] = field(default_factory=dict)
    discovered: list[Restaurant] | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    kept: list[Restaurant] = field(default_factory=list)
    last_booking: BookingOutcome | None = None
    clarified: bool = False
    today: str | None = None

    def __post_init__(self) -> None:
        base = default_filters(self.today)
        base.update({key: value for key, value in self.filters.items() if value is not None})
        self.filters = base

    # ------------------------------------------------------------------ query
    def context(self) -> QueryContext:
        f = self.filters
        return QueryContext(
            area=f["area"],
            lat=float(f["lat"]),
            lng=float(f["lng"]),
            budget_per_person=float(f["budget"]),
            radius_km=float(f["radius"]),
            party_size=int(f["party"]),
            date=str(f["date"]),
            time=str(f["time_start"]),
            time_end=str(f["time_end"]),
            duration_min=int(f.get("duration") or 90),
            cuisines=list(f["cuisines"]),
            require_availability=bool(f["require_availability"]),
            spend_per_person=f.get("spend"),
        )

    # ------------------------------------------------------------------ updates
    def merge(self, **changes: Any) -> dict[str, Any]:
        """Apply only the supplied (non-None) filters; cuisines accept str or list."""
        alias = {"cuisine": "cuisines", "time": "time_start", "require_available": "require_availability"}
        for raw_key, value in changes.items():
            if value is None:
                continue
            key = alias.get(raw_key, raw_key)
            if key not in self.filters:
                continue
            if key == "cuisines":
                value = parse_cuisine_args(value if isinstance(value, list) else [str(value)])
            elif key in ("budget", "radius", "spend"):
                value = float(value)
            elif key in ("party", "duration"):
                value = int(value)
            elif key == "require_availability":
                value = bool(value)
            self.filters[key] = value
        return self.summary()

    def summary(self) -> dict[str, Any]:
        f = self.filters
        return {
            "cuisines": list(f["cuisines"]) or ["any"],
            "date": f["date"],
            "time_window": f"{f['time_start']}–{f['time_end']}",
            "budget_per_person": f["budget"],
            "party": f["party"],
            "radius_km": f["radius"],
            "require_available": f["require_availability"],
        }

    def reset(self) -> dict[str, Any]:
        self.filters = default_filters(self.today)
        self.rows = []
        self.kept = []
        self.last_booking = None
        self.clarified = False
        return self.summary()

    def snapshot(self) -> dict[str, Any]:
        """Small debug view for the /filters command."""
        return {
            "filters": self.summary(),
            "results_in_context": len(self.rows),
            "last_booking": dataclasses.asdict(self.last_booking) if self.last_booking else None,
        }

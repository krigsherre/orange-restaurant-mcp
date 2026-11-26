"""Repository ports: the only way business logic touches restaurant data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from ..domain.models import QueryContext, Restaurant, Slot


class RestaurantRepository(Protocol):
    """Read-side port implemented by Swiggy Dineout and District."""

    name: str
    server: str

    @property
    def mode(self) -> str:
        """'live' or 'fixture'."""

    def search(self) -> list[Restaurant]:
        """Discover restaurants near the repository's query anchor."""

    def slots_for(self, restaurant: Restaurant, date: str) -> list[Slot]:
        """Availability for one restaurant on/after ``date``."""


class ReservationRepository(Protocol):
    """Write-side port: only live repositories implement a real reservation."""

    def book(self, restaurant: Restaurant, slot: Slot, ctx: QueryContext):
        """Place a reservation and return a BookingOutcome."""


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# Historical alias kept for callers that used the provider terminology.
Provider = RestaurantRepository

"""Provider adapters: each aggregator behind one uniform search interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from ..models import Restaurant, Slot


class Provider(Protocol):
    name: str
    server: str

    def search(self) -> list[Restaurant]:
        """Return restaurants near the query anchor (unfiltered)."""

    def slots_for(self, restaurant: Restaurant, date: str) -> list[Slot]:
        """Return bookable slots for a restaurant on/after ``date``."""


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))

"""The single user-side search call.

One function applies every filter the user supplies — radius, budget, cuisine and the
availability window — and returns the survivors plus an auditable rejection list.

Availability needs per-restaurant slot lookups, so the caller injects a loader; the search
decides when to call it (always for a hard availability filter, capped otherwise).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .cuisine import matches as cuisine_matches
from .models import QueryContext, Restaurant, Slot
from .rank import pick_slot

SlotLoader = Callable[[Restaurant], list[Slot]]


@dataclass(slots=True)
class SearchOutcome:
    kept: list[Restaurant] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    probed: int = 0


def _reject(restaurant: Restaurant, reason: str, detail: str = "") -> dict[str, Any]:
    return {
        "provider": restaurant.provider,
        "name": restaurant.name,
        "reason": reason,
        "detail": detail,
        "distance_km": restaurant.distance_km,
        "per_person": restaurant.per_person,
    }


def _hard_reason(restaurant: Restaurant, ctx: QueryContext) -> str | None:
    """Cheap, provider-independent filters, in reporting-priority order."""
    if not restaurant.name:
        return "missing-name"
    if restaurant.distance_km is None:
        return "unverifiable-proximity"
    if restaurant.distance_km > ctx.radius_km:
        return "beyond-radius"
    if restaurant.cost_for_two is None:
        return "unknown-cost"
    if (restaurant.per_person or 0) > ctx.budget_per_person:
        return "over-budget"
    if not cuisine_matches(restaurant.cuisines, ctx.cuisines):
        return "cuisine-mismatch"
    return None


def search(
    restaurants: list[Restaurant],
    ctx: QueryContext,
    *,
    slot_loader: SlotLoader | None = None,
    max_probe: int = 40,
) -> SearchOutcome:
    """Apply radius → budget → cuisine → availability in one call.

    ``ctx.require_availability`` decides whether the window is a hard filter or just
    annotation. With a hard filter every survivor is probed, because an unprobed
    restaurant cannot honestly be called unavailable.
    """
    kept: list[Restaurant] = []
    rejected: list[dict[str, Any]] = []

    for restaurant in restaurants:
        reason = _hard_reason(restaurant, ctx)
        if reason:
            detail = ""
            if reason == "cuisine-mismatch":
                detail = f"serves {', '.join(restaurant.cuisines) or 'unknown'}"
            rejected.append(_reject(restaurant, reason, detail))
        else:
            kept.append(restaurant)

    probed = 0
    if slot_loader is not None:
        for restaurant in kept:
            if not ctx.require_availability and probed >= max_probe:
                break
            try:
                restaurant.slots = slot_loader(restaurant)
            except Exception:  # noqa: BLE001 - a failed lookup is "unknown", never a crash
                restaurant.slots = []
            probed += 1

    if ctx.require_availability:
        available: list[Restaurant] = []
        for restaurant in kept:
            if pick_slot(restaurant.slots, ctx) is None:
                rejected.append(_reject(restaurant, "no-availability", f"no slot between {ctx.window_label}"))
            else:
                available.append(restaurant)
        kept = available

    return SearchOutcome(kept=kept, rejected=rejected, probed=probed)

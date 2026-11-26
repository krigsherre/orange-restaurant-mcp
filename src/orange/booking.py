"""Availability-driven booking: user-selected restaurant or automated top pick.

Two entry paths:
  * interactive  — the user picks a rank/name from the listed shortlist
  * automated    — the highest-ranked restaurant that actually has a slot in the window

Nothing is booked unless ``confirm=True``; fixture mode always simulates.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import QueryContext, Restaurant, Slot

SIM_PREFIX = "SIM-"


class SelectionError(RuntimeError):
    """The requested restaurant cannot be booked (bad rank/name, or no slot)."""


@dataclass(slots=True)
class BookingOutcome:
    status: str  # confirmed | pending-payment | requires-user-action | confirmation-required | unavailable | simulation | failed
    provider: str
    restaurant: str
    provider_id: str
    date: str
    start_time: str
    end_time: str
    party: int
    slot_id: str = ""
    order_id: str = ""
    message: str = ""
    booking_url: str = ""
    simulated: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def booked(self) -> bool:
        return self.status in {"confirmed", "simulation"}

    def to_dict(self) -> dict[str, Any]:
        data = {
            "status": self.status,
            "provider": self.provider,
            "restaurant": self.restaurant,
            "provider_id": self.provider_id,
            "date": self.date,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "party": self.party,
            "slot_id": self.slot_id,
            "order_id": self.order_id,
            "message": self.message,
            "booking_url": self.booking_url,
            "simulated": self.simulated,
            "created_at": self.created_at,
        }
        return data


def _simulated_order_id(restaurant: Restaurant, slot: Slot, ctx: QueryContext) -> str:
    blob = f"{restaurant.provider}|{restaurant.provider_id}|{ctx.date}|{slot.display_time}|{ctx.party_size}"
    return SIM_PREFIX + hashlib.sha1(blob.encode()).hexdigest()[:10].upper()


def _outcome(restaurant: Restaurant, slot: Slot, ctx: QueryContext, status: str, message: str, **extra: Any) -> BookingOutcome:
    return BookingOutcome(
        status=status,
        provider=restaurant.provider,
        restaurant=restaurant.name,
        provider_id=restaurant.provider_id,
        date=ctx.date,
        start_time=slot.display_time,
        end_time=slot.end_label,
        party=ctx.party_size,
        slot_id=str(slot.slot_id),
        message=message,
        **extra,
    )


def select_row(
    rows: list[dict[str, Any]],
    *,
    auto: bool = False,
    rank: int | None = None,
    name: str | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    """Resolve the user's choice to exactly one ranked row."""
    bookable = [row for row in rows if row["availability"].get("status") == "available"]

    if auto:
        if not bookable:
            raise SelectionError("No restaurant has a bookable slot in the requested window.")
        return bookable[0]

    if rank is not None:
        if not 1 <= rank <= len(rows):
            raise SelectionError(f"Rank {rank} is out of range (1–{len(rows)}).")
        row = rows[rank - 1]
        if row["availability"].get("status") != "available":
            alternatives = ", ".join(f"#{r['rank']} {r['name']}" for r in bookable[:5]) or "none"
            raise SelectionError(
                f"#{rank} {row['name']} is not bookable — {row['availability'].get('detail', 'no slot')}. "
                f"Bookable options: {alternatives}"
            )
        return row

    if name:
        candidates = [row for row in rows if provider is None or row["provider"] == provider]
        folded = name.casefold().strip()
        exact = [row for row in candidates if row["name"].casefold() == folded]
        matches = exact or [row for row in candidates if folded in row["name"].casefold()]
        if not matches:
            available = ", ".join(f"#{r['rank']} {r['name']}" for r in bookable[:5]) or "none"
            raise SelectionError(f"No restaurant matched {name!r}. Bookable: {available}")
        if len(matches) > 1:
            options = "; ".join(f"#{r['rank']} {r['name']} ({r['provider']})" for r in matches)
            raise SelectionError(f"{name!r} is ambiguous — pick a rank or add a provider. Options: {options}")
        row = matches[0]
        if row["availability"].get("status") != "available":
            raise SelectionError(
                f"{row['name']} is not bookable — {row['availability'].get('detail', 'no slot')}."
            )
        return row

    raise SelectionError("No selection given: pass --auto, --select <rank>, or --select <name>.")


def book_restaurant(
    provider: Any,
    restaurant: Restaurant,
    slot: Slot | None,
    ctx: QueryContext,
    *,
    confirm: bool = False,
    simulate: bool = False,
) -> BookingOutcome:
    """Book (or simulate) one slot. Never mutates unless confirm is true and mode is live."""
    if slot is None:
        return BookingOutcome(
            status="unavailable",
            provider=restaurant.provider,
            restaurant=restaurant.name,
            provider_id=restaurant.provider_id,
            date=ctx.date,
            start_time="",
            end_time="",
            party=ctx.party_size,
            message=f"No slot between {ctx.window_label}.",
        )

    mode = getattr(provider, "mode", "fixture")
    if simulate or mode != "live":
        return _outcome(
            restaurant,
            slot,
            ctx,
            "simulation",
            f"Simulated booking — no live {restaurant.provider} session was used.",
            order_id=_simulated_order_id(restaurant, slot, ctx),
            simulated=True,
        )

    if not confirm:
        return _outcome(
            restaurant,
            slot,
            ctx,
            "confirmation-required",
            "Live booking needs --yes (or an explicit confirm) before anything is reserved.",
        )

    try:
        return provider.book(restaurant, slot, ctx)
    except Exception as exc:  # noqa: BLE001 - surface provider errors as a status, not a crash
        return _outcome(restaurant, slot, ctx, "failed", f"{type(exc).__name__}: {exc}")


def write_booking(out_dir: str | Path, outcome: BookingOutcome) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "booking.json"
    json_path.write_text(json.dumps(outcome.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    md_path = out / "booking.md"
    lines = [
        f"# Booking — {outcome.restaurant}",
        "",
        f"- **Status**: `{outcome.status}`",
        f"- **Provider**: `{outcome.provider}` (`{outcome.provider_id}`)",
        f"- **Slot**: {outcome.start_time}–{outcome.end_time} on {outcome.date}",
        f"- **Party**: {outcome.party}",
        f"- **Order**: `{outcome.order_id or '—'}`" + (" _(simulated)_" if outcome.simulated else ""),
        f"- **Message**: {outcome.message}",
    ]
    if outcome.booking_url:
        lines.append(f"- **Booking link**: {outcome.booking_url}")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path

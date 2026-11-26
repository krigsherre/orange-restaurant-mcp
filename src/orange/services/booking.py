"""Availability-driven booking: user-selected restaurant or automated top pick.

Two entry paths:
  * interactive  — the user picks a rank/name from the listed shortlist
  * automated    — the highest-ranked restaurant that actually has a slot in the window

Rows with a provider booking link (District) carry no slot to reserve, so ``--auto``
skips them and the skip is reported; selecting one explicitly hands the link back
instead of pretending to book it.

Nothing is booked unless ``confirm=True``; fixture mode always simulates a slot booking.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..domain.models import BookingOutcome, QueryContext, Restaurant, Slot

SIM_PREFIX = "SIM-"


class SelectionError(RuntimeError):
    """The requested restaurant cannot be booked (bad rank/name, or no slot)."""


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


# A row can be acted on when it has a slot to reserve — or when the provider hands the
# reservation back to the user through a booking link (District has no booking API).
ACTIONABLE_STATUSES = frozenset({"available", "via-booking-link"})
RESERVABLE_STATUS = "available"


def availability_status(row: dict[str, Any]) -> str:
    return (row.get("availability") or {}).get("status", "")


def auto_skips(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows ``--auto`` passes over before the first reservable one, each with its reason.

    Auto books the highest-ranked row it can actually reserve, so link-only rows are
    skipped; this makes the skip auditable instead of silent.
    """
    skipped: list[dict[str, Any]] = []
    for row in rows:
        status = availability_status(row)
        if status == RESERVABLE_STATUS:
            break
        skipped.append(
            {
                "rank": row["rank"],
                "name": row["name"],
                "provider": row["provider"],
                "status": status,
                "detail": (row.get("availability") or {}).get("detail", ""),
            }
        )
    return skipped


def select_row(
    rows: list[dict[str, Any]],
    *,
    auto: bool = False,
    rank: int | None = None,
    name: str | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    """Resolve the user's choice to exactly one ranked row."""
    bookable = [row for row in rows if availability_status(row) == RESERVABLE_STATUS]

    if auto:
        if not bookable:
            raise SelectionError("No restaurant has a reservable slot in the requested window.")
        return bookable[0]

    if rank is not None:
        if not 1 <= rank <= len(rows):
            raise SelectionError(f"Rank {rank} is out of range (1–{len(rows)}).")
        row = rows[rank - 1]
        if availability_status(row) not in ACTIONABLE_STATUSES:
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
        if availability_status(row) not in ACTIONABLE_STATUSES:
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
        # District publishes no slot API: the honest outcome is the restaurant's own
        # booking link. There is nothing to reserve, so ``simulate`` cannot fake one.
        if getattr(provider, "books_via_link", False):
            try:
                return provider.book(restaurant, None, ctx)
            except Exception as exc:  # noqa: BLE001 - surface provider errors as a status
                return BookingOutcome(
                    status="failed",
                    provider=restaurant.provider,
                    restaurant=restaurant.name,
                    provider_id=restaurant.provider_id,
                    date=ctx.date,
                    start_time="",
                    end_time="",
                    party=ctx.party_size,
                    message=f"{type(exc).__name__}: {exc}",
                )
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

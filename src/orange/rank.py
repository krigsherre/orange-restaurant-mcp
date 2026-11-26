"""Availability annotation and the deterministic ranking comparator."""

from __future__ import annotations

from typing import Any

from .judge import OfferJudge
from .models import QueryContext, Restaurant, Slot


def pick_slot(slots: list[Slot], ctx: QueryContext) -> Slot | None:
    """Choose the bookable slot inside [time, time_end], closest to the window start."""
    best: tuple[tuple[int, int, int], Slot] | None = None
    want = ctx.start_minutes
    for slot in slots:
        if slot.date and slot.date != ctx.date:
            continue
        if not ctx.slot_in_window(slot):
            continue
        slot_start = slot.start_minutes
        delta = abs(slot_start - want) if (slot_start is not None and want is not None) else 999
        key = (0 if slot.is_free else 1, delta, -slot.inventory)
        if best is None or key < best[0]:
            best = (key, slot)
    return best[1] if best else None


def availability_label(
    slots: list[Slot], slot: Slot | None, restaurant: Restaurant, ctx: QueryContext
) -> dict[str, Any]:
    if not slots:
        if restaurant.provider == "district" and restaurant.booking_url:
            return {"status": "via-booking-link", "detail": "Book via District link"}
        return {"status": "unknown", "detail": "No slot data returned"}
    if slot is None:
        return {"status": "no-slot-in-window", "detail": f"No slot between {ctx.window_label}"}
    price = "" if slot.is_free else f" · ₹{slot.booking_price:,.0f} prebook"
    return {
        "status": "available",
        "detail": f"{slot.window_label} · {slot.group or 'slot'}{price} · {slot.inventory} covers left",
        "slot": slot.to_dict(),
    }


def judge_all(restaurants: list[Restaurant], ctx: QueryContext, judge: OfferJudge) -> None:
    """Run the offer judge once over every offer in the shortlist."""
    judge.score([offer for restaurant in restaurants for offer in restaurant.offers], ctx)


def rank(restaurants: list[Restaurant], ctx: QueryContext, judge: OfferJudge) -> list[dict[str, Any]]:
    """Convenience wrapper: judge then rank."""
    judge_all(restaurants, ctx, judge)
    return rank_rows(restaurants, ctx)


def rank_rows(restaurants: list[Restaurant], ctx: QueryContext) -> list[dict[str, Any]]:
    """Sort: cash value at the reference bill desc -> score desc -> distance -> name -> ids.

    "Best offer" is the offer with the most cash value for this party, not the one with
    the highest usability score — a card-gated ₹300 is still ₹300 if you hold the card.
    """
    rows: list[dict[str, Any]] = []
    for restaurant in restaurants:
        best = restaurant.best_offer
        slot = pick_slot(restaurant.slots, ctx)
        rows.append(
            {
                "provider": restaurant.provider,
                "provider_id": restaurant.provider_id,
                "name": restaurant.name,
                "cuisines": restaurant.cuisines,
                "locality": restaurant.locality,
                "distance_km": restaurant.distance_km,
                "distance_source": restaurant.distance_source,
                "cost_for_two": restaurant.cost_for_two,
                "per_person": restaurant.per_person,
                "bill_reference": ctx.expected_bill(restaurant.per_person),
                "rating": restaurant.rating,
                "rating_count": restaurant.rating_count,
                "cash_pct": best.effective_pct if best else 0.0,
                "offer_score": best.score if best else 0.0,
                "offer_count": len(restaurant.offers),
                "best_offer": (
                    {
                        "title": best.title,
                        "source": best.source,
                        "type": best.offer_type,
                        "value": best.value,
                        "min_spend": best.min_spend,
                        "score": best.score,
                        "effective_pct": best.effective_pct,
                        "flags": best.flags,
                        "rationale": best.rationale,
                        "judged_by": best.judged_by,
                    }
                    if best
                    else None
                ),
                "availability": availability_label(restaurant.slots, slot, restaurant, ctx),
                "booking_url": restaurant.booking_url,
            }
        )

    rows.sort(
        key=lambda row: (
            -row["cash_pct"],
            -row["offer_score"],
            row["distance_km"] if row["distance_km"] is not None else float("inf"),
            row["name"].casefold(),
            row["provider"],
            row["provider_id"],
        )
    )
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows

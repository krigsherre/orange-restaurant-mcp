"""End-to-end orchestration: discover -> search (all filters) -> judge -> rank -> report."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .booking import BookingOutcome, book_restaurant, select_row, write_booking
from .judge import JudgeUnavailable, build_judge
from .models import QueryContext, Restaurant
from .providers import build_providers
from .rank import judge_all, pick_slot, rank_rows
from .report import write_csv, write_json, write_markdown
from .search import search


@dataclass(slots=True)
class RunResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    context: QueryContext | None = None
    booking: BookingOutcome | None = None
    restaurants: list[Restaurant] = field(default_factory=list)
    providers: list[Any] = field(default_factory=list)


def run(
    ctx: QueryContext,
    *,
    fixtures_dir: str | Path | None = None,
    judge_mode: str = "auto",
    verdicts: str | None = None,
    out_dir: str | Path = "out",
    live: bool = True,
    selection: dict[str, Any] | None = None,
    confirm: bool = False,
    simulate: bool = False,
) -> RunResult:
    providers, notes = build_providers(ctx, fixtures_dir=fixtures_dir, live=live)

    discovered: list[Restaurant] = []
    for provider in providers:
        found = provider.search()
        notes.append(f"{provider.name}: discovered {len(found)}")
        discovered.extend(found)

    # One search call owns every user filter: radius, budget, cuisine, availability.
    by_provider = {provider.name: provider for provider in providers}
    outcome = search(
        discovered,
        ctx,
        slot_loader=lambda restaurant: by_provider[restaurant.provider].slots_for(restaurant, ctx.date),
    )
    kept = outcome.kept
    rejected = outcome.rejected
    notes.append(f"availability probed: {outcome.probed}")

    judge = build_judge(judge_mode, verdicts_path=verdicts)
    judge_name = judge.name
    try:
        judge_all(kept, ctx, judge)
    except JudgeUnavailable as exc:
        notes.append(f"judge fallback -> rubric ({exc})")
        from .judge.rubric import RubricJudge

        judge = RubricJudge()
        judge_name = judge.name
        judge_all(kept, ctx, judge)

    rows = rank_rows(kept, ctx)

    booking = None
    if selection:
        row = select_row(rows, **selection)
        restaurant = next(r for r in kept if (r.provider, r.provider_id) == (row["provider"], row["provider_id"]))
        booking = book_restaurant(
            by_provider[restaurant.provider],
            restaurant,
            pick_slot(restaurant.slots, ctx),
            ctx,
            confirm=confirm,
            simulate=simulate,
        )
        write_booking(out_dir, booking)

    meta = {
        "judge": judge_name,
        "booking": booking.status if booking else None,
        "require_availability": ctx.require_availability,
        "sources": [f"{p.name} ({p.mode})" for p in providers],
        "notes": notes,
        "counts": {
            "discovered": len(discovered),
            "after_filters": len(kept),
            "rejected": len(rejected),
            "with_offers": sum(1 for r in rows if r["offer_count"] > 0),
            "bookable": sum(1 for r in rows if r["availability"]["status"] == "available"),
        },
        "constraints": {
            "radius_km": ctx.radius_km,
            "budget_per_person": ctx.budget_per_person,
            "party_size": ctx.party_size,
            "cuisines": ctx.cuisines,
            "window": ctx.window_label,
            "require_availability": ctx.require_availability,
        },
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "ranked.json", ctx, rows, rejected, meta)
    write_csv(out / "ranked.csv", rows)
    write_markdown(out / "ranked.md", ctx, rows, rejected, meta)

    return RunResult(
        rows=rows,
        rejected=rejected,
        meta=meta,
        context=ctx,
        booking=booking,
        restaurants=kept,
        providers=providers,
    )

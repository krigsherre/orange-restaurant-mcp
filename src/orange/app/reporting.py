"""Output writers: markdown for humans, CSV for spreadsheets, JSON for machines."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..domain.models import BookingOutcome, QueryContext


def _flags(row: dict[str, Any]) -> str:
    best = row.get("best_offer") or {}
    return ", ".join(best.get("flags") or []) or "—"


def write_json(path: Path, ctx: QueryContext, rows: list[dict[str, Any]], rejected: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    payload = {
        "query": ctx.to_judge_dict(),
        "anchor": {"lat": ctx.lat, "lng": ctx.lng, "radius_km": ctx.radius_km},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "meta": meta,
        "counts": {"ranked": len(rows), "rejected": len(rejected)},
        "results": rows,
        "rejected": rejected,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "rank",
        "name",
        "provider",
        "provider_id",
        "best_offer",
        "offer_score",
        "effective_pct",
        "bill_reference",
        "per_person",
        "distance_km",
        "rating",
        "availability",
        "flags",
        "rationale",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            best = row.get("best_offer") or {}
            writer.writerow(
                {
                    "rank": row["rank"],
                    "name": row["name"],
                    "provider": row["provider"],
                    "provider_id": row["provider_id"],
                    "best_offer": best.get("title", ""),
                    "offer_score": row["offer_score"],
                    "effective_pct": best.get("effective_pct", ""),
                    "bill_reference": row.get("bill_reference", ""),
                    "per_person": row["per_person"],
                    "distance_km": row["distance_km"],
                    "rating": row["rating"],
                    "availability": row["availability"].get("detail", ""),
                    "flags": _flags(row),
                    "rationale": best.get("rationale", ""),
                }
            )


def write_markdown(
    path: Path,
    ctx: QueryContext,
    rows: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    meta: dict[str, Any],
) -> None:
    judge_name = meta.get("judge", "rubric")
    lines: list[str] = []
    lines.append(f"# Koramangala restaurants — offer-ranked ({len(rows)} results)")
    lines.append("")
    lines.append(
        f"**Anchor** {ctx.lat}, {ctx.lng} · **radius** ≤ {ctx.radius_km:g} km · "
        f"**budget** ≤ ₹{ctx.budget_per_person:,.0f}/person · **party** {ctx.party_size} · "
        f"**when** {ctx.date} {ctx.window_label}"
    )
    if ctx.cuisines:
        lines.append(f"**Cuisine** {', '.join(ctx.cuisines)} (any match)")
    lines.append(f"**Offer judge**: `{judge_name}` · **Sources**: {', '.join(meta.get('sources', []))}")
    if meta.get("judge_fallback"):
        lines.append(f"**Scoring fallback**: `{judge_name}` — {meta['judge_fallback']}")
    if ctx.spend_per_person:
        lines.append(f"**Bill reference**: ₹{ctx.spend_per_person:,.0f}/person × {ctx.party_size} (`--spend`)")
    else:
        lines.append("**Bill reference**: each restaurant's cost for two (per person × party)")
    lines.append("")
    lines.append("Sorted by: cash value at the reference bill ↓, score ↓, distance ↑, name A–Z (then provider/id).")
    if rows:
        seen: set[str] = set()
        podium_rows = []
        for row in rows:
            key = row["name"].casefold()
            if key in seen:
                continue  # same restaurant twice (one row per provider) — show it once
            seen.add(key)
            podium_rows.append(row)
            if len(podium_rows) == 3:
                break
        podium = " · ".join(
            f"**{row['name']}** ({(row['best_offer'] or {}).get('title', 'no offer')}, {row['cash_pct']:.0f}% of bill)"
            for row in podium_rows
        )
        lines.append(f"**Top 3:** {podium}")
    lines.append("")
    lines.append("| # | Restaurant | Provider | Best offer | Cash % | Score | ₹/person | Dist | Slot (start–end) | Booking |")
    lines.append("|---:|---|---|---|---:|---:|---:|---:|---|---|")
    for row in rows:
        best = row.get("best_offer") or {}
        offer = best.get("title", "No offer")
        if best.get("score") is not None:
            offer = f"{offer} *({best['score']:.0f})*"
        slot = row["availability"].get("slot") or {}
        slot_text = slot.get("window") or "—"
        status = row["availability"].get("status", "unknown")
        if status == "available":
            booking = "free" if slot.get("is_free") else f"₹{slot.get('booking_price', 0):,.0f} prebook"
        else:
            booking = {
                "via-booking-link": "District link",
                "no-slot-in-window": "no slot",
                "unknown": "no data",
            }.get(status, status)
        lines.append(
            f"| {row['rank']} | **{row['name']}** | `{row['provider']}` | {offer} | "
            f"{row['cash_pct']:.0f}% | {row['offer_score']:.0f} | ₹{row['per_person']:,.0f} | "
            f"{row['distance_km']:.1f} km | {slot_text} | {booking} |"
        )

    lines.append("")
    lines.append("## Why each offer scored that way")
    lines.append("")
    for row in rows:
        best = row.get("best_offer") or {}
        if not best:
            lines.append(f"- **{row['name']}** — no offer listed; ranked below every offer-bearing restaurant.")
            continue
        flags = ", ".join(best.get("flags") or []) or "none"
        lines.append(
            f"- **{row['name']}** — {best.get('title')} → {best.get('effective_pct'):.0f}% of a "
            f"₹{row.get('bill_reference', 0):,.0f} bill, score {best.get('score'):.0f}, flags: {flags}. "
            f"{best.get('rationale', '')}"
        )

    if rejected:
        lines.append("")
        lines.append("## Excluded before ranking")
        lines.append("")
        reasons: dict[str, list[str]] = {}
        for item in rejected:
            reasons.setdefault(item["reason"], []).append(item["name"] or "(unnamed)")
        for reason, names in sorted(reasons.items()):
            lines.append(f"- **{reason}** ({len(names)}): {', '.join(sorted(names)[:8])}")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "Generated by `orange`. Fixture mode unless provider tokens were set — see README for the live switch."
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_booking(out_dir: str | Path, outcome: BookingOutcome) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "booking.json"
    json_path.write_text(json.dumps(outcome.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    md_path = out / "booking.md"
    slot = (
        f"{outcome.start_time}–{outcome.end_time}"
        if outcome.start_time or outcome.end_time
        else "— (reserve through the provider link)"
    )
    lines = [
        f"# Booking — {outcome.restaurant}",
        "",
        f"- **Status**: `{outcome.status}`",
        f"- **Provider**: `{outcome.provider}` (`{outcome.provider_id}`)",
        f"- **Slot**: {slot} on {outcome.date}",
        f"- **Party**: {outcome.party}",
        f"- **Order**: `{outcome.order_id or '—'}`" + (" _(simulated)_" if outcome.simulated else ""),
        f"- **Message**: {outcome.message}",
    ]
    if outcome.booking_url:
        lines.append(f"- **Booking link**: {outcome.booking_url}")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path

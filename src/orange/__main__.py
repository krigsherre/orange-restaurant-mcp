"""CLI: python -m orange --budget 1000 --radius 5 --time-start 20:00 --time-end 22:00"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .booking import SelectionError, select_row
from .cuisine import parse_cuisine_args
from .models import QueryContext
from .pipeline import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orange", description="Rank Koramangala restaurants by real offer value, then book.")
    parser.add_argument("--area", default="Koramangala")
    parser.add_argument("--lat", type=float, default=12.9352, help="Koramangala centre latitude (Swiggy's reference)")
    parser.add_argument("--lng", type=float, default=77.6245, help="Koramangala centre longitude")
    parser.add_argument("--budget", type=float, default=1000.0, help="INR per person")
    parser.add_argument("--radius", type=float, default=5.0, help="km")
    parser.add_argument("--party", type=int, default=2)
    parser.add_argument(
        "--cuisine",
        action="append",
        default=[],
        metavar="NAME[,NAME]",
        help="only restaurants serving these cuisines (repeatable, comma-separated, OR semantics)",
    )
    parser.add_argument("--date", default="2026-11-27", help="YYYY-MM-DD")
    parser.add_argument("--time", "--time-start", dest="time", default="20:00", help="booking window start, HH:MM")
    parser.add_argument("--time-end", default="22:00", help="booking window end, HH:MM (inclusive)")
    parser.add_argument("--duration", type=int, default=90, help="minutes per table, used to show slot end")
    parser.add_argument(
        "--require-available",
        action="store_true",
        help="drop restaurants with no bookable slot inside the window",
    )
    parser.add_argument("--auto", action="store_true", help="book the top-ranked restaurant that has a slot")
    parser.add_argument("--select", metavar="RANK|NAME", help="book a specific row: rank number or restaurant name")
    parser.add_argument("--provider", help="disambiguate --select NAME by provider")
    parser.add_argument("--book", action="store_true", help="actually reserve (fixtures simulate)")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation gate for a live booking")
    parser.add_argument("--simulate", action="store_true", help="force simulated booking even in live mode")
    parser.add_argument("--judge", choices=["auto", "llm", "rubric", "replay"], default="auto")
    parser.add_argument("--verdicts", help="JSON verdict file for --judge replay")
    parser.add_argument("--fixtures", help="fixtures directory")
    parser.add_argument("--out", default="out")
    parser.add_argument("--no-live", action="store_true", help="force fixtures even if tokens exist")
    parser.add_argument("--top", type=int, default=25, help="rows to print")
    return parser


def _selection(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.auto:
        return {"auto": True}
    if args.select:
        value = str(args.select).strip()
        if value.isdigit():
            return {"rank": int(value)}
        return {"name": value, "provider": args.provider}
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ctx = QueryContext(
        area=args.area,
        lat=args.lat,
        lng=args.lng,
        budget_per_person=args.budget,
        radius_km=args.radius,
        party_size=args.party,
        date=args.date,
        time=args.time,
        time_end=args.time_end,
        duration_min=args.duration,
        cuisines=parse_cuisine_args(args.cuisine),
        require_availability=args.require_available,
    )
    selection = _selection(args)
    if args.book and selection is None:
        print("--book needs --auto (top pick) or --select RANK|NAME (your pick).", file=sys.stderr)
        return 2

    try:
        result = run(
            ctx,
            fixtures_dir=args.fixtures,
            judge_mode=args.judge,
            verdicts=args.verdicts,
            out_dir=args.out,
            live=not args.no_live,
            selection=selection if args.book else None,
            confirm=args.yes,
            simulate=args.simulate,
        )
    except SelectionError as exc:
        print(f"Selection failed: {exc}", file=sys.stderr)
        return 1

    counts = result.meta["counts"]
    print(
        f"Discovered {counts['discovered']} · after filters {counts['after_filters']} · "
        f"ranked {len(result.rows)} · bookable {counts['bookable']}"
    )
    print(f"Judge: {result.meta['judge']} · Sources: {', '.join(result.meta['sources'])}")
    print(f"Window: {ctx.date} {ctx.window_label} · party {ctx.party_size} · ≤₹{ctx.budget_per_person:,.0f}/person")
    if ctx.cuisines:
        print(f"Cuisine filter: {', '.join(ctx.cuisines)}")
    print()

    header = f"{'#':>2}  {'Restaurant':<26} {'Provider':<16} {'Offer':<34} {'Score':>5} {'₹/p':>6} {'Dist':>5}  {'Slot':<20}"
    print(header)
    print("-" * len(header))
    for row in result.rows[: args.top]:
        best = row["best_offer"] or {}
        offer = (best.get("title") or "—")[:33]
        slot = (row["availability"].get("slot") or {}).get("window") or "—"
        print(
            f"{row['rank']:>2}  {row['name'][:26]:<26} {row['provider']:<16} {offer:<34} "
            f"{row['offer_score']:>5.0f} {row['per_person']:>6,.0f} {row['distance_km']:>5.1f}  {slot:<20}"
        )
    print()
    print(f"Wrote {args.out}/ranked.{{md,csv,json}}")

    if selection and not args.book:
        try:
            row = select_row(result.rows, **selection)
        except SelectionError as exc:
            print(f"\nSelection failed: {exc}", file=sys.stderr)
            return 1
        slot = row["availability"].get("slot") or {}
        print(
            f"\nDry run: would book #{row['rank']} {row['name']} ({row['provider']}) "
            f"at {slot.get('window', '?')}. Re-run with --book to reserve."
        )
    elif result.booking:
        booking = result.booking
        print(f"\nBooking: {booking.status.upper()} · {booking.restaurant} · {booking.start_time}–{booking.end_time}")
        print(f"  order: {booking.order_id or '—'} · {booking.message}")
        if booking.booking_url:
            print(f"  link: {booking.booking_url}")
        print(f"  wrote {args.out}/booking.{{json,md}}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

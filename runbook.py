#!/usr/bin/env python3
"""Run the canonical command set from a clean slate and record each command in JSON.

Usage:
    python3 runbook.py            # wipes out/, runs everything, writes out/commands.json
    python3 runbook.py --keep     # do not wipe out/ first
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
RUN = ROOT / "run.py"

COMMANDS: list[dict[str, Any]] = [
    {
        "name": "rank-default",
        "description": "Full Koramangala shortlist: ≤5 km, ≤₹1000/person, 8–10 PM window",
        "args": ["--out", "out"],
    },
    {
        "name": "rank-available",
        "description": "Same, but hard-filtered to restaurants with a bookable slot in the window",
        "args": ["--require-available", "--out", "out/available"],
    },
    {
        "name": "rank-narrow-window",
        "description": "Tighter 7:30–9:00 PM window with the availability filter on",
        "args": [
            "--time-start", "19:30",
            "--time-end", "21:00",
            "--require-available",
            "--out", "out/window-1930-2100",
        ],
    },
    {
        "name": "rank-cafe",
        "description": "Cuisine filter: cafes only",
        "args": ["--cuisine", "Cafe", "--out", "out/cafe"],
    },
    {
        "name": "rank-multi-cuisine",
        "description": "Cuisine OR filter across North Indian and Chinese",
        "args": ["--cuisine", "North Indian,Chinese", "--out", "out/north-indian-chinese"],
    },
    {
        "name": "rank-tight-budget",
        "description": "Stricter budget (₹800) and radius (3 km)",
        "args": ["--budget", "800", "--radius", "3", "--out", "out/budget-800"],
    },
    {
        "name": "filters-combined",
        "description": "All four filters at once: cuisine + window + budget + radius, availability required",
        "args": [
            "--cuisine", "North Indian,Chinese",
            "--budget", "900",
            "--radius", "3",
            "--time-start", "19:30",
            "--time-end", "21:30",
            "--require-available",
            "--out", "out/combined",
        ],
    },
    {
        "name": "book-auto",
        "description": "Automated booking: top-ranked restaurant that has a slot",
        "args": ["--book", "--auto", "--out", "out/auto"],
    },
    {
        "name": "book-select",
        "description": "User-selected booking: rank #3 from the default shortlist",
        "args": ["--book", "--select", "3", "--out", "out/select-3"],
    },
]


def command_string(args: list[str]) -> str:
    return "./run.py " + " ".join(shlex.quote(a) for a in args)


def summarize(run_out: Path) -> dict[str, Any]:
    """Pull the machine-readable result out of the artifacts a run wrote."""
    summary: dict[str, Any] = {}
    ranked_path = run_out / "ranked.json"
    if ranked_path.exists():
        data = json.loads(ranked_path.read_text())
        results = data.get("results", [])
        summary["counts"] = data.get("counts", {})
        summary["query"] = data.get("query", {})
        summary["bookable"] = sum(1 for r in results if r.get("availability", {}).get("status") == "available")
        summary["top"] = [
            {
                "rank": r["rank"],
                "name": r["name"],
                "provider": r["provider"],
                "offer_score": r["offer_score"],
                "per_person": r["per_person"],
                "distance_km": r["distance_km"],
                "slot": (r.get("availability", {}).get("slot") or {}).get("window"),
            }
            for r in results[:5]
        ]
    booking_path = run_out / "booking.json"
    if booking_path.exists():
        booking = json.loads(booking_path.read_text())
        summary["booking"] = {
            key: booking.get(key)
            for key in ("status", "restaurant", "start_time", "end_time", "party", "order_id", "simulated")
        }
    return summary


def artifacts(run_out: Path) -> list[str]:
    return sorted(str(p.relative_to(ROOT)) for p in run_out.rglob("*") if p.is_file())


def main(argv: list[str] | None = None) -> int:
    keep = "--keep" in (argv if argv is not None else sys.argv[1:])
    removed = False
    if OUT.exists():
        resolved = OUT.resolve()
        if resolved != (ROOT / "out").resolve():  # guard against a stray symlink
            print(f"refusing to delete unexpected path: {resolved}", file=sys.stderr)
            return 2
        if not keep:
            shutil.rmtree(resolved)
            removed = True

    records: list[dict[str, Any]] = []
    failures = 0
    for spec in COMMANDS:
        args = spec["args"]
        started = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, str(RUN), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        elapsed = round((time.perf_counter() - started) * 1000)
        run_out = ROOT / args[args.index("--out") + 1]
        record = {
            "name": spec["name"],
            "description": spec["description"],
            "command": command_string(args),
            "argv": args,
            "out_dir": str(run_out.relative_to(ROOT)),
            "exit_code": proc.returncode,
            "duration_ms": elapsed,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "result": summarize(run_out),
            "artifacts": artifacts(run_out) if run_out.exists() else [],
        }
        records.append(record)
        if proc.returncode != 0:
            failures += 1
        status = "ok" if proc.returncode == 0 else f"FAILED ({proc.returncode})"
        counts = record["result"].get("counts", {})
        print(f"{record['name']:<20} {status:<12} ranked={counts.get('ranked', '-'):<4} {elapsed:>5} ms")

    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cwd": str(ROOT),
        "python": sys.version.split()[0],
        "entrypoint": "./run.py",
        "prepared": {"removed_out_dir": removed, "kept_out_dir": keep and not removed},
        "commands": records,
        "summary": {
            "commands": len(records),
            "failed": failures,
            "artifacts": len({a for r in records for a in r["artifacts"]}),
        },
    }
    path = OUT / "commands.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {path.relative_to(ROOT)} ({len(records)} commands, {failures} failed)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Time parsing/formatting shared by the window filter, slot picker and reports."""

from __future__ import annotations

import re

_TIME_12H = re.compile(r"(\d{1,2}):(\d{2})\s*([APap][Mm])")


def parse_minutes(value: str) -> int | None:
    """'8:00 PM' -> 1200, '20:00' -> 1200, '9 PM' -> 1260. Returns minutes since midnight."""
    text = (value or "").strip()
    if not text:
        return None
    if match := _TIME_12H.search(text):
        hour, minute = int(match.group(1)), int(match.group(2))
        hour %= 12
        if match.group(3).lower() == "pm":
            hour += 12
        return hour * 60 + minute
    if match := re.match(r"^(\d{1,2}):(\d{2})$", text):
        return int(match.group(1)) * 60 + int(match.group(2))
    if match := re.match(r"^(\d{1,2})\s*([APap][Mm])$", text):
        hour = int(match.group(1)) % 12
        if match.group(2).lower() == "pm":
            hour += 12
        return hour * 60
    return None


def format_minutes(minutes: int | None) -> str:
    """1200 -> '8:00 PM'."""
    if minutes is None:
        return ""
    minutes %= 24 * 60
    hour, minute = divmod(minutes, 60)
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {suffix}"


def window(start: str, end: str) -> tuple[int | None, int | None]:
    return parse_minutes(start), parse_minutes(end)


def window_label(start: str, end: str) -> str:
    start_min, end_min = window(start, end)
    if start_min is None:
        return ""
    end_label = format_minutes(end_min) if end_min is not None else ""
    return f"{format_minutes(start_min)}–{end_label}" if end_label else format_minutes(start_min)

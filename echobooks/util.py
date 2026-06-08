"""Small parsing/formatting helpers shared across the UI."""

from __future__ import annotations

import re
from datetime import date

_HOURS_MIN = re.compile(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?", re.IGNORECASE)


def parse_runtime(text: str) -> int | None:
    """Parse '970', '16:10', or '16h 10m' into total minutes."""
    text = text.strip().lower()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if ":" in text:
        try:
            h, m = text.split(":", 1)
            return int(h) * 60 + int(m)
        except ValueError:
            return None
    match = _HOURS_MIN.fullmatch(text)
    if match and (match.group(1) or match.group(2)):
        hours = int(match.group(1) or 0)
        mins = int(match.group(2) or 0)
        return hours * 60 + mins
    return None


def format_runtime(minutes: int | None) -> str:
    if not minutes:
        return "—"
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def parse_date(text: str) -> date | None:
    text = text.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_rating(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return max(0.5, min(5.0, value))


def stars(rating: float | None) -> str:
    if not rating:
        return "—"
    full = int(rating)
    half = 1 if rating - full >= 0.5 else 0
    return "★" * full + ("½" if half else "")


def split_csv(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]

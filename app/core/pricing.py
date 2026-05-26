"""Parse human display prices into a comparable AED number.

Both feeds store price as a display *string* (`"Free"`, `"AED 95"`, `"From AED
1,200 per person"`) because that's what sources give us and what the UI shows.
To filter by budget we need a number, so we derive one alongside the string —
this is the single parser both events and services use, so "≤ AED 500" means the
same thing everywhere. Pure (no deps) to keep it importable from ingestion and
the service layer without cycles.
"""

import re

_FREE = re.compile(r"\bfree\b", re.IGNORECASE)
# First monetary amount in the string, tolerating thousands separators and
# decimals: "AED 1,200", "1200", "95.00", "AED50".
_AMOUNT = re.compile(r"\d[\d,]*(?:\.\d+)?")


def parse_aed(value) -> float | None:
    """Lowest AED amount in a display price, or None when there's no number.

    "Free" (and "complimentary"/"no charge") → 0.0. Returns None — *not* 0 — when
    the price is unknown/unparseable, so callers can tell "free" from "no price
    listed" and a budget filter won't silently treat unknowns as free.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None
    if _FREE.search(text) or "no charge" in text.lower() or "complimentary" in text.lower():
        return 0.0

    match = _AMOUNT.search(text)
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None

"""Renewals catalog — the central, tunable registry of trackable Dubai documents.

See DOCUMENTS.md. We track *expiry dates*, never the documents themselves. Each
`RenewalType` carries the Dubai-specific knobs: how far ahead to remind
(`lead_days`), how to renew (`kb_query` → offering #1), and where to get help
(`lead_vertical` → offering #3). Keep this file the single source of truth so
lead-times and copy are tuned in one place — mirrors `ingestion/registry.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

CONFIRMED = "confirmed"
ESTIMATED = "estimated"


@dataclass(frozen=True)
class RenewalType:
    key: str  # stored in documents.doc_type
    label: str  # display title (documents.title)
    recurrence: str  # "yearly" | "none" (multi-year items the user re-dates)
    lead_days: int  # surface/remind when expiry is within this many days
    kb_query: str  # powers "How to renew →" via kb_search (offering #1)
    kb_category: str | None  # optional kb_search category filter
    lead_vertical: str | None  # powers "Find help →" via create_lead (offering #3)
    onboarding_chip: bool = True  # show in the "add later" chip grid


# ─── The catalog ───────────────────────────────────────────────────────────
# lead_days are framed around the consequence avoided, not a generic "7 days":
# visa needs runway for the medical test + EID appointment; motor insurance must
# be valid *before* the Mulkiya renewal; passport uses the ~8-month rule.
RENEWAL_TYPES: dict[str, RenewalType] = {
    "visa": RenewalType(
        key="visa",
        label="Residency visa",
        recurrence="none",
        lead_days=60,
        kb_query="how to renew UAE residency visa Dubai",
        kb_category="visa",
        lead_vertical="relocation",  # typing centre / PRO services
    ),
    "emirates_id": RenewalType(
        key="emirates_id",
        label="Emirates ID",
        recurrence="none",
        lead_days=30,
        kb_query="how to renew Emirates ID Dubai",
        kb_category="visa",
        lead_vertical="relocation",
    ),
    "passport": RenewalType(
        key="passport",
        label="Passport",
        recurrence="none",
        lead_days=240,  # the ~8-month validity rule, not a true expiry window
        kb_query="passport validity requirement UAE residency 6 months",
        kb_category="visa",
        lead_vertical=None,  # home-country embassy; no partner
    ),
    "uae_driving_license": RenewalType(
        key="uae_driving_license",
        label="UAE driving licence",
        recurrence="none",
        lead_days=30,
        kb_query="how to renew UAE driving licence RTA Dubai",
        kb_category="transport",
        lead_vertical="relocation",
    ),
    "car_registration": RenewalType(
        key="car_registration",
        label="Car registration (Mulkiya)",
        recurrence="yearly",
        lead_days=30,
        kb_query="how to renew car registration Mulkiya RTA Dubai test insurance",
        kb_category="transport",
        lead_vertical="car_service",  # vehicle testing centre
    ),
    "motor_insurance": RenewalType(
        key="motor_insurance",
        label="Car insurance",
        recurrence="yearly",
        lead_days=30,
        kb_query="renew car insurance Dubai before registration",
        kb_category="insurance",
        lead_vertical="insurance",
    ),
    "health_insurance": RenewalType(
        key="health_insurance",
        label="Health insurance",
        recurrence="yearly",
        lead_days=45,  # gates the visa renewal
        kb_query="renew health insurance Dubai mandatory residency",
        kb_category="insurance",
        lead_vertical="insurance",
    ),
    "ejari_tenancy": RenewalType(
        key="ejari_tenancy",
        label="Tenancy / Ejari",
        recurrence="yearly",
        lead_days=30,
        kb_query="renew Ejari tenancy contract Dubai",
        kb_category="housing",
        lead_vertical="real_estate",
    ),
    "trade_license": RenewalType(
        key="trade_license",
        label="Trade licence",
        recurrence="yearly",
        lead_days=30,
        kb_query="renew trade licence Dubai freelance business",
        kb_category="visa",
        lead_vertical="relocation",
    ),
    "domestic_worker_visa": RenewalType(
        key="domestic_worker_visa",
        label="Domestic worker visa",
        recurrence="yearly",
        lead_days=45,
        kb_query="renew domestic worker maid visa Dubai sponsor",
        kb_category="visa",
        lead_vertical="relocation",
    ),
    "domestic_worker_insurance": RenewalType(
        key="domestic_worker_insurance",
        label="Domestic worker insurance",
        recurrence="yearly",
        lead_days=30,
        kb_query="domestic worker insurance Dubai mandatory",
        kb_category="insurance",
        lead_vertical="insurance",
    ),
}

# The anchor-date cascade: one visa expiry date populates the whole visa cluster.
# Derived (non-input) items are ESTIMATED so a guessed date never reads as fact.
VISA_ANCHOR_CASCADE: list[tuple[str, str]] = [
    ("visa", CONFIRMED),  # the date the user actually entered
    ("emirates_id", ESTIMATED),  # same date in practice
    ("health_insurance", ESTIMATED),  # must be valid to renew the visa
]


def get_type(doc_type: str | None) -> RenewalType | None:
    return RENEWAL_TYPES.get(doc_type) if doc_type else None


def needs_reminder(doc_type: str | None, expiry: date | None, *, today: date) -> bool:
    """True when an expiry has entered its lead-time window (reminder due)."""
    rt = get_type(doc_type)
    if rt is None or expiry is None:
        return False
    return today >= expiry - timedelta(days=rt.lead_days)

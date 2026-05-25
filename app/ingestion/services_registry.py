"""The Services vertical grid: (service category × Dubai area).

A bounded, finite registry — ~10 categories × ~10 areas — scraped on a slow
monthly TTL (see backend/INGESTION.md, "Services (Tier A-style)"). Bounding the
grid is the cost lever: ~100 cells × ~15 providers, scraped monthly, cached as
durable rows. Refine the lists here as early runs report thin cells.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str  # stored on the row + used in API/agent filters
    label: str  # human label for the UI
    query: str  # the Google Maps search term (prepended to the area)


# ~10 high-intent home/living service categories to start.
CATEGORIES: list[Category] = [
    Category("cleaning", "Cleaning", "cleaning services"),
    Category("ac_repair", "AC Repair", "AC repair"),
    Category("handyman", "Handyman", "handyman services"),
    Category("plumbing", "Plumbing", "plumber"),
    Category("electrician", "Electrician", "electrician"),
    Category("movers", "Movers", "movers and packers"),
    Category("pest_control", "Pest Control", "pest control"),
    Category("maid_service", "Maid Service", "maid service"),
    Category("car_service", "Car Service", "car service and repair"),
    Category("laundry", "Laundry", "laundry and dry cleaning"),
]

# ~10 Dubai areas where expats cluster.
AREAS: list[str] = [
    "Dubai Marina",
    "JLT",
    "Downtown Dubai",
    "Business Bay",
    "JVC",
    "Deira",
    "Bur Dubai",
    "Mirdif",
    "Dubai Hills",
    "Jumeirah",
]

CATEGORY_BY_KEY: dict[str, Category] = {c.key: c for c in CATEGORIES}


def search_term(category: Category, area: str) -> str:
    """e.g. 'AC repair in Dubai Marina, Dubai' — the Google Maps query for a cell."""
    return f"{category.query} in {area}, Dubai"


def grid() -> list[tuple[Category, str]]:
    """Every (category, area) cell in the registry."""
    return [(c, area) for c in CATEGORIES for area in AREAS]

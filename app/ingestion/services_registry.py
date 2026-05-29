"""The Services vertical grid: (service category × Dubai area).

A bounded, finite registry with category-specific TTLs and capped recurring
refresh depth (see backend/INGESTION.md, "Services (Tier A-style)"). Bounding
the grid is the cost lever; expanding this list is a product decision.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str  # registry/dry-run key
    label: str  # human label for the UI
    query: str  # the Google Maps search term (prepended to the area)
    category: str | None = None  # stored/API parent category; defaults to key
    subcategory: str = "general"
    areas: tuple[str, ...] | None = None
    # Refresh policy. Seeding can be fuller; recurring refreshes should be cheaper
    # and staggered by volatility.
    refresh_ttl_days: int | None = None
    refresh_per_cell: int | None = None

    @property
    def row_category(self) -> str:
        return self.category or self.key


_HOME_FAST_TTL = 45
_HOME_SLOW_TTL = 60
_PETS_TTL = 75
_MEDICAL_ACCESS_TTL = 45
_MEDICAL_STANDARD_TTL = 75


# High-intent home/living service categories.
CATEGORIES: list[Category] = [
    Category("cleaning", "Cleaning", "cleaning services", refresh_ttl_days=_HOME_FAST_TTL, refresh_per_cell=10),
    Category("ac_repair", "AC Repair", "AC repair", refresh_ttl_days=_HOME_FAST_TTL, refresh_per_cell=10),
    Category("handyman", "Handyman", "handyman services", refresh_ttl_days=_HOME_FAST_TTL, refresh_per_cell=10),
    Category("plumbing", "Plumbing", "plumber", refresh_ttl_days=_HOME_FAST_TTL, refresh_per_cell=10),
    Category("electrician", "Electrician", "electrician", refresh_ttl_days=_HOME_FAST_TTL, refresh_per_cell=10),
    Category("movers", "Movers", "movers and packers", refresh_ttl_days=_HOME_SLOW_TTL, refresh_per_cell=10),
    Category("pest_control", "Pest Control", "pest control", refresh_ttl_days=_HOME_FAST_TTL, refresh_per_cell=10),
    Category("maid_service", "Maid Service", "maid service", refresh_ttl_days=_HOME_SLOW_TTL, refresh_per_cell=10),
    Category("car_service", "Car Service", "car service and repair", refresh_ttl_days=_HOME_SLOW_TTL, refresh_per_cell=10),
    Category("laundry", "Laundry", "laundry and dry cleaning", refresh_ttl_days=_HOME_SLOW_TTL, refresh_per_cell=10),
    Category(
        "pets_vets",
        "Vets",
        "veterinary clinic",
        category="pets",
        subcategory="vets",
        refresh_ttl_days=_PETS_TTL,
        refresh_per_cell=8,
    ),
    Category(
        "pets_emergency_vets",
        "Emergency Vets",
        "24 hour emergency vet",
        category="pets",
        subcategory="emergency_vets",
        refresh_ttl_days=60,
        refresh_per_cell=8,
    ),
    Category(
        "pets_boarding_hotels",
        "Pet Hotels",
        "pet boarding and pet hotel",
        category="pets",
        subcategory="boarding_hotels",
        refresh_ttl_days=_PETS_TTL,
        refresh_per_cell=8,
    ),
    Category(
        "pets_sitters_walkers",
        "Pet Sitters",
        "pet sitting and dog walking",
        category="pets",
        subcategory="sitters_walkers",
        refresh_ttl_days=_PETS_TTL,
        refresh_per_cell=8,
    ),
    Category(
        "pets_grooming",
        "Pet Grooming",
        "pet grooming",
        category="pets",
        subcategory="grooming",
        refresh_ttl_days=_PETS_TTL,
        refresh_per_cell=8,
    ),
    Category(
        "pets_shelters_adoption",
        "Shelters & Adoption",
        "animal shelter pet adoption animal rescue",
        category="pets",
        subcategory="shelters_adoption",
        areas=("Dubai",),
        refresh_ttl_days=90,
        refresh_per_cell=8,
    ),
    Category(
        "medical_urgent_care",
        "Urgent Care",
        "urgent care clinic",
        category="medical",
        subcategory="urgent_care",
        refresh_ttl_days=_MEDICAL_ACCESS_TTL,
        refresh_per_cell=8,
    ),
    Category(
        "medical_clinics",
        "Clinics",
        "medical clinic",
        category="medical",
        subcategory="clinics",
        refresh_ttl_days=_MEDICAL_STANDARD_TTL,
        refresh_per_cell=8,
    ),
    Category(
        "medical_dentists",
        "Dentists",
        "dental clinic",
        category="medical",
        subcategory="dentists",
        refresh_ttl_days=_MEDICAL_STANDARD_TTL,
        refresh_per_cell=8,
    ),
    Category(
        "medical_pediatricians",
        "Pediatricians",
        "pediatric clinic",
        category="medical",
        subcategory="pediatricians",
        refresh_ttl_days=_MEDICAL_STANDARD_TTL,
        refresh_per_cell=8,
    ),
    Category(
        "medical_physiotherapy",
        "Physiotherapy",
        "physiotherapy clinic",
        category="medical",
        subcategory="physiotherapy",
        refresh_ttl_days=_MEDICAL_STANDARD_TTL,
        refresh_per_cell=8,
    ),
    Category(
        "medical_pharmacies",
        "Pharmacies",
        "pharmacy",
        category="medical",
        subcategory="pharmacies",
        refresh_ttl_days=_MEDICAL_ACCESS_TTL,
        refresh_per_cell=8,
    ),
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
    if area.strip().lower() == "dubai":
        return f"{category.query} in Dubai"
    return f"{category.query} in {area}, Dubai"


def grid() -> list[tuple[Category, str]]:
    """Every (category, area) cell in the registry."""
    return [(c, area) for c in CATEGORIES for area in (c.areas or tuple(AREAS))]

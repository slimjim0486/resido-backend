"""The Services vertical grid: (service category × Dubai area).

A bounded, finite registry — ~10 categories × ~10 areas — scraped on a slow
monthly TTL (see backend/INGESTION.md, "Services (Tier A-style)"). Bounding the
grid is the cost lever: ~100 cells × ~15 providers, scraped monthly, cached as
durable rows. Refine the lists here as early runs report thin cells.
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

    @property
    def row_category(self) -> str:
        return self.category or self.key


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
    Category("pets_vets", "Vets", "veterinary clinic", category="pets", subcategory="vets"),
    Category(
        "pets_emergency_vets",
        "Emergency Vets",
        "24 hour emergency vet",
        category="pets",
        subcategory="emergency_vets",
    ),
    Category(
        "pets_boarding_hotels",
        "Pet Hotels",
        "pet boarding and pet hotel",
        category="pets",
        subcategory="boarding_hotels",
    ),
    Category(
        "pets_sitters_walkers",
        "Pet Sitters",
        "pet sitting and dog walking",
        category="pets",
        subcategory="sitters_walkers",
    ),
    Category(
        "pets_grooming",
        "Pet Grooming",
        "pet grooming",
        category="pets",
        subcategory="grooming",
    ),
    Category(
        "pets_shelters_adoption",
        "Shelters & Adoption",
        "animal shelter pet adoption animal rescue",
        category="pets",
        subcategory="shelters_adoption",
        areas=("Dubai",),
    ),
    Category(
        "medical_urgent_care",
        "Urgent Care",
        "urgent care clinic",
        category="medical",
        subcategory="urgent_care",
    ),
    Category(
        "medical_clinics",
        "Clinics",
        "medical clinic",
        category="medical",
        subcategory="clinics",
    ),
    Category(
        "medical_dentists",
        "Dentists",
        "dental clinic",
        category="medical",
        subcategory="dentists",
    ),
    Category(
        "medical_pediatricians",
        "Pediatricians",
        "pediatric clinic",
        category="medical",
        subcategory="pediatricians",
    ),
    Category(
        "medical_physiotherapy",
        "Physiotherapy",
        "physiotherapy clinic",
        category="medical",
        subcategory="physiotherapy",
    ),
    Category(
        "medical_pharmacies",
        "Pharmacies",
        "pharmacy",
        category="medical",
        subcategory="pharmacies",
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

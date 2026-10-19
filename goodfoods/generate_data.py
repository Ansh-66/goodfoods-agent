"""Generate the 75-outlet GoodFoods directory as data/restaurants.json.

Deterministic (fixed seed), so the committed JSON can always be rebuilt
byte-for-byte. Areas are real Bengaluru neighbourhoods with approximate centre
coordinates; each outlet is jittered a few hundred metres off its area centre so
"nearby" means something.

Run:  python -m goodfoods.generate_data
"""

import json
import random

from .config import RESTAURANTS_JSON

SEED = 42
N_OUTLETS = 75

# (area, lat, lon) - approximate neighbourhood centres
AREAS = [
    ("Koramangala", 12.9352, 77.6245),
    ("Indiranagar", 12.9784, 77.6408),
    ("HSR Layout", 12.9116, 77.6474),
    ("Whitefield", 12.9698, 77.7500),
    ("Jayanagar", 12.9250, 77.5938),
    ("JP Nagar", 12.9063, 77.5857),
    ("MG Road", 12.9756, 77.6050),
    ("Malleshwaram", 13.0031, 77.5643),
    ("Basavanagudi", 12.9406, 77.5738),
    ("BTM Layout", 12.9166, 77.6101),
    ("Electronic City", 12.8452, 77.6602),
    ("Marathahalli", 12.9591, 77.6974),
    ("Bellandur", 12.9304, 77.6784),
    ("Hebbal", 13.0358, 77.5970),
    ("Yelahanka", 13.1007, 77.5963),
    ("Rajajinagar", 12.9911, 77.5550),
    ("Banashankari", 12.9255, 77.5468),
    ("Frazer Town", 12.9967, 77.6133),
    ("Sadashivanagar", 13.0068, 77.5813),
    ("Sarjapur Road", 12.9010, 77.6870),
    ("Bannerghatta Road", 12.8880, 77.5970),
    ("Kalyan Nagar", 13.0280, 77.6400),
    ("Ulsoor", 12.9810, 77.6200),
    ("Domlur", 12.9609, 77.6387),
]

# GoodFoods runs several sub-brands; the sub-brand fixes the cuisine.
# (concept name, cuisines, price band 1-4, opening, closing)
CONCEPTS = [
    ("Tandoor House", ["North Indian", "Mughlai"], 2, "12:00", "23:30"),
    ("Dakshin", ["South Indian"], 1, "07:30", "22:30"),
    ("Coast & Curry", ["Coastal", "Seafood"], 3, "12:00", "23:00"),
    ("Andhra Spice", ["Andhra", "South Indian"], 2, "11:30", "23:00"),
    ("Wok Street", ["Chinese", "Pan-Asian"], 2, "12:00", "23:00"),
    ("Trattoria", ["Italian", "Pizza"], 3, "12:00", "23:30"),
    ("The Grill Room", ["Continental", "Grill"], 4, "18:00", "00:00"),
    ("Mezze", ["Mediterranean", "Middle Eastern"], 3, "12:00", "23:00"),
    ("Sakura", ["Japanese", "Sushi"], 4, "12:30", "23:00"),
    ("Chettinad Table", ["Chettinad", "South Indian"], 2, "11:30", "23:00"),
    ("Green Bowl", ["Vegetarian", "Healthy"], 2, "08:00", "22:30"),
    ("Café GoodFoods", ["Cafe", "Bakery"], 1, "08:00", "22:00"),
]

COST_FOR_TWO = {1: (400, 700), 2: (800, 1300), 3: (1400, 2200), 4: (2500, 4000)}

FEATURES = [
    "veg_friendly",
    "outdoor_seating",
    "private_dining",
    "kid_friendly",
    "bar",
    "live_music",
    "parking",
    "wheelchair_accessible",
]


def build() -> list[dict]:
    rng = random.Random(SEED)
    outlets = []
    used_names = set()
    for i in range(N_OUTLETS):
        area, lat, lon = AREAS[i % len(AREAS)]  # ~3 outlets per area
        concept, cuisines, band, opens, closes = rng.choice(CONCEPTS)

        name = f"GoodFoods {concept}, {area}"
        if name in used_names:  # same concept twice in one area
            name = f"{name} II"
        used_names.add(name)

        features = {f: rng.random() < 0.4 for f in FEATURES}
        if "Vegetarian" in cuisines or band == 1:
            features["veg_friendly"] = True
        if band == 4:
            features["bar"] = True
        if concept == "Café GoodFoods":
            features["bar"] = False

        lo, hi = COST_FOR_TWO[band]
        outlets.append(
            {
                "id": f"R{i + 1:03d}",
                "name": name,
                "concept": concept,
                "area": area,
                "address": f"{rng.randint(1, 250)}, {rng.choice(['1st', '4th', '6th', '8th', '12th'])} "
                f"{rng.choice(['Main', 'Cross'])}, {area}, Bengaluru",
                "lat": round(lat + rng.uniform(-0.006, 0.006), 5),
                "lon": round(lon + rng.uniform(-0.006, 0.006), 5),
                "cuisines": cuisines,
                "price_band": band,
                "cost_for_two": rng.randrange(lo, hi + 1, 50),
                "capacity": rng.choice([40, 50, 60, 70, 80, 100, 120, 150]),
                "opens": opens,
                "closes": closes,
                "rating": round(rng.uniform(3.8, 4.8), 1),
                "phone": f"080-4{rng.randint(1000000, 9999999)}",
                "features": [f for f, on in features.items() if on],
            }
        )
    return outlets


def main() -> None:
    outlets = build()
    RESTAURANTS_JSON.parent.mkdir(exist_ok=True)
    RESTAURANTS_JSON.write_text(json.dumps(outlets, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(outlets)} outlets to {RESTAURANTS_JSON}")


if __name__ == "__main__":
    main()

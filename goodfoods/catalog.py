"""Finding outlets: ranked recommendations and outlet details.

Guest wording is normalised here (synonyms for cuisines, features, areas), so the
model can pass what the guest said ("biryani", "outdoor", "Koramangala 5th Block")
instead of having to know the exact catalogue values.
"""

import json
import re

from .availability import (
    check_party_size,
    distance_km,
    get_restaurant,
    is_bookable,
    last_seating,
    parse_date,
    parse_time,
    to_hhmm,
)
from .config import (
    BOOKING_HORIZON_DAYS,
    CHANGE_CUTOFF_HOURS,
    DINING_SLOTS,
    MAX_PARTY_SIZE,
    SLOT_MINUTES,
)
from .errors import BookingError, is_blank
from .generate_data import AREAS, FEATURES

CUISINE_SYNONYMS = {
    "biryani": ["mughlai", "andhra"],
    "pizza": ["italian"],
    "pasta": ["italian"],
    "sushi": ["japanese"],
    "fish": ["seafood", "coastal"],
    "coffee": ["cafe"],
    "breakfast": ["cafe", "south indian"],
    "dosa": ["south indian"],
    "steak": ["grill"],
    "asian": ["pan-asian"],
    "indian": ["north indian", "south indian"],
}
VEG_WORDS = {"veg", "vegetarian", "pure veg", "pure vegetarian", "vegan"}

FEATURE_SYNONYMS = {
    "veg": "veg_friendly",
    "vegetarian": "veg_friendly",
    "vegan": "veg_friendly",
    "outdoor": "outdoor_seating",
    "outdoors": "outdoor_seating",
    "private": "private_dining",
    "private room": "private_dining",
    "kids": "kid_friendly",
    "family": "kid_friendly",
    "family friendly": "kid_friendly",
    "alcohol": "bar",
    "drinks": "bar",
    "music": "live_music",
    "wheelchair": "wheelchair_accessible",
    "accessible": "wheelchair_accessible",
}

# Occasion words nudge the ranking towards features that suit them (soft, not a filter).
OCCASION_FEATURES = {
    "birthday": ["private_dining", "live_music"],
    "anniversary": ["private_dining", "bar"],
    "celebration": ["private_dining", "live_music"],
    "party": ["private_dining", "live_music"],
    "date": ["bar", "outdoor_seating"],
    "romantic": ["bar", "outdoor_seating"],
    "family": ["kid_friendly", "parking"],
    "kids": ["kid_friendly"],
    "business": ["private_dining", "parking"],
    "meeting": ["private_dining"],
    "client": ["private_dining"],
}

# Ranking weights, set by hand: 1 km costs 0.15 rating points, so a 4.6 outlet
# 4 km away ties a 4.0 outlet next door; each occasion-fitting feature adds 0.3.
KM_PENALTY = 0.15
OCCASION_BONUS = 0.3


def _normalise_features(features) -> list[str]:
    if is_blank(features):
        return []
    if isinstance(features, str):
        features = re.split(r"[,;]", features)
    out = []
    for raw in features:
        key = str(raw).strip().lower().replace("-", " ").replace("_", " ")
        key = FEATURE_SYNONYMS.get(key, key.replace(" ", "_"))
        if key not in FEATURES:
            raise BookingError("unknown_feature", f"Unknown feature '{raw}'.", valid_features=FEATURES)
        out.append(key)
    return out


def _match_area(area: str) -> tuple[str, float, float]:
    text = area.strip().lower()
    for name, lat, lon in AREAS:
        if name.lower() in text or text in name.lower():
            return name, lat, lon
    raise BookingError(
        "unknown_area",
        f"GoodFoods has no outlets in '{area}'.",
        areas_served=[a[0] for a in AREAS],
    )


def _cuisine_matches(query: str, cuisines: list[str]) -> bool:
    terms = [query, *CUISINE_SYNONYMS.get(query, [])]
    return any(t in c.lower() or c.lower() in t for t in terms for c in cuisines)


def recommend_restaurants(
    conn,
    area=None,
    cuisine=None,
    party_size=None,
    date_str=None,
    time_str=None,
    max_price_band=None,
    features=None,
    occasion=None,
    name=None,
    limit=3,
) -> dict:
    rows = conn.execute("SELECT * FROM restaurants").fetchall()
    wanted = _normalise_features(features)

    # Hard filters: what the guest said they need.
    if not is_blank(cuisine):
        query = str(cuisine).strip().lower()
        if query in VEG_WORDS:
            wanted.append("veg_friendly")
        else:
            rows = [r for r in rows if _cuisine_matches(query, json.loads(r["cuisines"]))]
            if not rows:
                served = conn.execute("SELECT cuisines FROM restaurants").fetchall()
                raise BookingError(
                    "no_match",
                    f"No GoodFoods outlet serves '{cuisine}'.",
                    cuisines_served=sorted({c for (cs,) in served for c in json.loads(cs)}),
                )
    if not is_blank(name):
        rows = [r for r in rows if str(name).strip().lower() in r["name"].lower()]
    if not is_blank(max_price_band):
        rows = [r for r in rows if r["price_band"] <= int(max_price_band)]
    party = None if is_blank(party_size) else check_party_size(party_size)
    if party:
        rows = [r for r in rows if r["capacity"] >= party]
    rows = [r for r in rows if set(wanted) <= set(json.loads(r["features"]))]

    origin = None if is_blank(area) else _match_area(str(area))
    day = start = None
    notes = []
    if not is_blank(date_str) and not is_blank(time_str):
        day, start = parse_date(date_str), parse_time(time_str)
    elif not is_blank(date_str) or not is_blank(time_str):
        notes.append("Availability not checked: it needs both a date and a time.")
    occasion_text = "" if is_blank(occasion) else str(occasion).lower()
    occasion_feats = {f for word, fs in OCCASION_FEATURES.items() if word in occasion_text for f in fs}

    # Soft ranking: rating, minus distance, plus occasion fit; free outlets first.
    scored = []
    for r in rows:
        feats = json.loads(r["features"])
        km = distance_km(origin[1], origin[2], r["lat"], r["lon"]) if origin else None
        fits = sorted(occasion_feats & set(feats))
        available = None if day is None else is_bookable(conn, r, day, start, party or 2)
        score = r["rating"] - KM_PENALTY * (km or 0) + OCCASION_BONUS * len(fits)
        scored.append((available is not False, score, r, km, fits, available))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)

    results = []
    for _, _, r, km, fits, available in scored[: max(1, min(int(limit or 3), 5))]:
        reason = [", ".join(json.loads(r["cuisines"]))]
        if km is not None:
            reason.append(f"{km:.1f} km from {origin[0]}")
        reason += [f"rated {r['rating']}", f"seats {r['capacity']}"]
        if wanted:
            reason.append("has " + ", ".join(f.replace("_", " ") for f in wanted))
        if fits:
            reason.append("suits the occasion: " + ", ".join(f.replace("_", " ") for f in fits))
        if available is not None:
            reason.append(f"{'free' if available else 'full'} at {to_hhmm(start)}")
        item = {
            "restaurant_id": r["id"],
            "name": r["name"],
            "area": r["area"],
            "cuisines": json.loads(r["cuisines"]),
            "price_band": r["price_band"],
            "cost_for_two": r["cost_for_two"],
            "rating": r["rating"],
            "reason": "; ".join(reason),
        }
        if km is not None:
            item["distance_km"] = round(km, 1)
        if available is not None:
            item["available"] = available
        results.append(item)

    out = {"ok": True, "count": len(results), "restaurants": results}
    if not results:
        out["message"] = "No outlet matches all of these filters. Suggest relaxing one."
    if notes:
        out["notes"] = notes
    return out


def get_restaurant_details(conn, restaurant_id: str) -> dict:
    r = get_restaurant(conn, restaurant_id)
    return {
        "ok": True,
        "restaurant_id": r["id"],
        "name": r["name"],
        "area": r["area"],
        "address": r["address"],
        "phone": r["phone"],
        "cuisines": json.loads(r["cuisines"]),
        "price_band": r["price_band"],
        "cost_for_two": r["cost_for_two"],
        "rating": r["rating"],
        "capacity": r["capacity"],
        "hours": f"{r['opens']}-{r['closes']}",
        "last_booking": to_hhmm(last_seating(r)),
        "features": json.loads(r["features"]),
        "policies": {
            "max_online_party_size": MAX_PARTY_SIZE,
            "table_held_for_minutes": DINING_SLOTS * SLOT_MINUTES,
            "booking_window_days": BOOKING_HORIZON_DAYS,
            "online_changes_until": f"{CHANGE_CUTOFF_HOURS} hours before the booking",
        },
    }

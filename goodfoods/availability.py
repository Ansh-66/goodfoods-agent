"""Time slots, seat capacity and overflow routing.

Capacity model: an outlet has N seats. A booking starts on a 30-minute slot and
holds its seats for DINING_SLOTS slots (90 minutes). A party fits at time T if,
in each of the slots T, T+30 and T+60, the seats already held plus the party
stay within N. Availability is computed from confirmed reservations on every
request, never stored, so it cannot drift out of sync.
"""

import json
import math
import re
import sqlite3
from datetime import date, timedelta

from .config import (
    ALTERNATIVE_WINDOW_MIN,
    BOOKING_HORIZON_DAYS,
    DINING_SLOTS,
    MAX_PARTY_SIZE,
    NEARBY_RADIUS_KM,
    SLOT_MINUTES,
    now,
)
from .errors import BookingError

# ------------------------------------------------------------------ time helpers


def to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise BookingError("invalid_date", f"'{value}' is not a date. Use YYYY-MM-DD, e.g. {now().date()}.") from None


def parse_time(value: str) -> int:
    """'19:30' -> 1170 minutes after midnight. Rejects times off the 30-minute grid."""
    text = str(value).strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise BookingError("invalid_time", f"'{value}' is not a time. Use 24-hour HH:MM, e.g. 19:30.")
    minutes = to_minutes(text)
    if minutes % SLOT_MINUTES:
        before = minutes - minutes % SLOT_MINUTES
        raise BookingError(
            "invalid_time",
            f"Bookings start on the hour or half hour. Nearest options: "
            f"{to_hhmm(before)} or {to_hhmm(before + SLOT_MINUTES)}.",
        )
    return minutes


# ------------------------------------------------------------------ outlet rules


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def get_restaurant(conn: sqlite3.Connection, restaurant_id: str) -> sqlite3.Row:
    """An outlet by id ("R004") or by name ("Wok Street Whitefield"). Models often pass the
    name they heard instead of looking the id up, so a unique name match is accepted."""
    key = str(restaurant_id).strip()
    row = conn.execute("SELECT * FROM restaurants WHERE id = ?", (key.upper(),)).fetchone()
    if row is not None:
        return row
    wanted = _squash(key)
    matches = [r for r in conn.execute("SELECT * FROM restaurants") if wanted and wanted in _squash(r["name"])]
    exact = [r for r in matches if _squash(r["name"]) in (wanted, "goodfoods" + wanted)]
    if len(exact) == 1 or len(matches) == 1:
        return (exact or matches)[0]
    if matches:
        raise BookingError(
            "ambiguous_restaurant",
            f"'{restaurant_id}' matches {len(matches)} outlets. Ask the guest which one.",
            candidates=[{"restaurant_id": r["id"], "name": r["name"]} for r in matches[:8]],
        )
    raise BookingError(
        "restaurant_not_found",
        f"No outlet '{restaurant_id}'. Use recommend_restaurants to find one.",
    )


def opening_minutes(r: sqlite3.Row) -> tuple[int, int]:
    """(open, close) in minutes. A close at or before the open means after midnight."""
    open_m, close_m = to_minutes(r["opens"]), to_minutes(r["closes"])
    return open_m, close_m + (24 * 60 if close_m <= open_m else 0)


def last_seating(r: sqlite3.Row) -> int:
    return opening_minutes(r)[1] - DINING_SLOTS * SLOT_MINUTES


def check_party_size(party_size: int, r: sqlite3.Row | None = None) -> int:
    party = int(party_size)
    if party < 1:
        raise BookingError("invalid_party_size", "Party size must be at least 1.")
    if party > MAX_PARTY_SIZE:
        raise BookingError(
            "party_too_large",
            f"Online bookings are for up to {MAX_PARTY_SIZE} guests. Larger groups are "
            "handled as events by GoodFoods staff: offer a hand-off.",
            max_party_size=MAX_PARTY_SIZE,
        )
    if r is not None and party > r["capacity"]:
        raise BookingError("exceeds_outlet_capacity", f"{r['name']} seats {r['capacity']}.")
    return party


def check_slot(r: sqlite3.Row, day: date, start: int) -> None:
    """The date and time are bookable at this outlet, ignoring how full it is."""
    current = now()
    if day < current.date():
        raise BookingError("date_in_past", f"{day} has passed. Today is {current.date()}.")
    if day > current.date() + timedelta(days=BOOKING_HORIZON_DAYS):
        raise BookingError("date_too_far", f"Bookings open {BOOKING_HORIZON_DAYS} days ahead.")
    if not opening_minutes(r)[0] <= start <= last_seating(r):
        raise BookingError(
            "outside_opening_hours",
            f"{r['name']} is open {r['opens']}-{r['closes']}; the last booking starts at {to_hhmm(last_seating(r))}.",
        )
    if day == current.date() and start <= current.hour * 60 + current.minute:
        raise BookingError("time_in_past", f"{to_hhmm(start)} today has already passed.")


# ------------------------------------------------------------------ capacity


def occupancy(conn, restaurant_id: str, day: date, exclude_id: str | None = None) -> dict[int, int]:
    """Seats held in each 30-minute slot of one day, from confirmed reservations."""
    rows = conn.execute(
        "SELECT id, time, party_size FROM reservations WHERE restaurant_id = ? AND date = ? AND status = 'confirmed'",
        (restaurant_id, day.isoformat()),
    ).fetchall()
    held: dict[int, int] = {}
    for row in rows:
        if row["id"] == exclude_id:  # a booking being modified does not block itself
            continue
        start = to_minutes(row["time"])
        for k in range(DINING_SLOTS):
            slot = start + k * SLOT_MINUTES
            held[slot] = held.get(slot, 0) + row["party_size"]
    return held


def seats_free(conn, r: sqlite3.Row, day: date, start: int, exclude_id: str | None = None) -> int:
    held = occupancy(conn, r["id"], day, exclude_id)
    return r["capacity"] - max(held.get(start + k * SLOT_MINUTES, 0) for k in range(DINING_SLOTS))


def is_bookable(conn, r, day: date, start: int, party: int, exclude_id: str | None = None) -> bool:
    try:
        check_slot(r, day, start)
    except BookingError:
        return False
    return seats_free(conn, r, day, start, exclude_id) >= party


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine (straight-line) distance."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def free_times_near(conn, r, day: date, start: int, party: int, exclude_id: str | None = None) -> list[str]:
    """Up to 3 free start times at this outlet within +/-2 hours, nearest first."""
    offsets = sorted(
        range(-ALTERNATIVE_WINDOW_MIN, ALTERNATIVE_WINDOW_MIN + 1, SLOT_MINUTES), key=lambda d: (abs(d), d)
    )
    return [to_hhmm(start + d) for d in offsets if d and is_bookable(conn, r, day, start + d, party, exclude_id)][:3]


def find_alternatives(conn, r, day: date, start: int, party: int, exclude_id: str | None = None) -> dict:
    """Overflow routing for a full outlet: (a) the nearest free times at the same outlet
    within +/-2 hours, and (b) the same time at other outlets within NEARBY_RADIUS_KM,
    same cuisine first, then by distance. Done in code so the model cannot skip it."""
    other_times = free_times_near(conn, r, day, start, party, exclude_id)

    my_cuisines = set(json.loads(r["cuisines"]))
    nearby = []
    for other in conn.execute("SELECT * FROM restaurants WHERE id != ?", (r["id"],)):
        km = distance_km(r["lat"], r["lon"], other["lat"], other["lon"])
        if km <= NEARBY_RADIUS_KM and is_bookable(conn, other, day, start, party):
            cuisines = json.loads(other["cuisines"])
            nearby.append(
                {
                    "restaurant_id": other["id"],
                    "name": other["name"],
                    "cuisines": cuisines,
                    "distance_km": round(km, 1),
                    "same_cuisine": bool(my_cuisines & set(cuisines)),
                }
            )
    nearby.sort(key=lambda o: (not o["same_cuisine"], o["distance_km"]))
    return {"same_outlet_other_times": other_times, "nearby_outlets_same_time": nearby[:3]}


def check_availability(conn, restaurant_id: str, date_str: str, time_str: str, party_size: int) -> dict:
    r = get_restaurant(conn, restaurant_id)
    day, start = parse_date(date_str), parse_time(time_str)
    party = check_party_size(party_size, r)
    check_slot(r, day, start)
    result = {
        "ok": True,
        "restaurant_id": r["id"],
        "name": r["name"],
        "date": day.isoformat(),
        "time": to_hhmm(start),
        "party_size": party,
    }
    if seats_free(conn, r, day, start) >= party:
        return {**result, "available": True}
    return {
        **result,
        "available": False,
        "reason": f"{r['name']} is fully booked for {party} at {to_hhmm(start)}.",
        "alternatives": find_alternatives(conn, r, day, start, party),
    }

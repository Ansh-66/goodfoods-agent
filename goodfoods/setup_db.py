"""Build data/goodfoods.db from scratch: schema, the 75 outlets, and seeded bookings.

Seeded bookings make the demo realistic: outlets are busier at dinner and on
weekends. Four outlets are deliberately full at dinner every day for the next
two weeks, so overflow routing can be shown on any day:

  R001, R002, R003  full 19:00-22:00  -> no other time nearby; the guest is
                                         offered a nearby GoodFoods outlet
  R004              full 20:00-21:30  -> 18:30 or 21:30 at the same outlet

Bookings are seeded relative to "now" (see config.now), so pin GOODFOODS_NOW
before running this if you need a reproducible database.

Run:  python -m goodfoods.setup_db
"""

import json
import random
from datetime import timedelta

from .availability import last_seating, opening_minutes, seats_free, to_hhmm, to_minutes
from .config import DINING_SLOTS, RESTAURANTS_JSON, SLOT_MINUTES, db_path, now
from .db import connect, create_schema
from .generate_data import main as generate_restaurants
from .reservations import new_id

SEED = 7
SEED_DAYS = 14
DEMO_FULL = {
    "R001": ("19:00", "21:30"),
    "R002": ("19:00", "21:30"),
    "R003": ("19:00", "21:30"),
    "R004": ("20:00", "21:00"),
}
GUEST_NAMES = [
    "Aarav",
    "Diya",
    "Kabir",
    "Meera",
    "Rohan",
    "Ananya",
    "Vikram",
    "Isha",
    "Arjun",
    "Priya",
    "Karthik",
    "Sneha",
    "Rahul",
    "Nisha",
    "Sameer",
    "Lakshmi",
]


def target_load(day, slot_min: int) -> float:
    """Share of seats to fill in a slot: busier at lunch, dinner and weekends."""
    weekend = day.weekday() >= 4  # Fri, Sat, Sun
    if 19 * 60 <= slot_min <= 21 * 60:
        return 0.70 if weekend else 0.50
    if 13 * 60 <= slot_min <= 14 * 60:
        return 0.40 if weekend else 0.30
    return 0.12


def _insert(conn, rng, r, day, start, party):
    stamp = now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO reservations (id, restaurant_id, date, time, party_size, guest_name, "
        "guest_phone, special_requests, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 'confirmed', ?, ?)",
        (
            new_id(conn, "GF", "reservations", rng),
            r["id"],
            day.isoformat(),
            to_hhmm(start),
            party,
            rng.choice(GUEST_NAMES),
            stamp,
            stamp,
        ),
    )


def seed_reservations(conn, rng: random.Random) -> int:
    count = 0
    today = now().date()
    restaurants = conn.execute("SELECT * FROM restaurants ORDER BY id").fetchall()
    for offset in range(SEED_DAYS):
        day = today + timedelta(days=offset)
        for r in restaurants:
            open_m = opening_minutes(r)[0]
            last = last_seating(r)
            # Fill the demo-full outlets first: every slot in the window at capacity.
            if r["id"] in DEMO_FULL:
                lo, hi = (to_minutes(t) for t in DEMO_FULL[r["id"]])
                for slot in range(lo, hi + 1, SLOT_MINUTES * DINING_SLOTS):
                    start = min(slot, last)
                    while seats_free(conn, r, day, start) > 0:
                        party = min(rng.randint(2, 6), seats_free(conn, r, day, start))
                        _insert(conn, rng, r, day, start, party)
                        count += 1
            # Then a random load everywhere, never above capacity.
            for start in range(open_m, last + 1, SLOT_MINUTES):
                target = target_load(day, start) * r["capacity"] / DINING_SLOTS
                placed = 0
                while placed < target:
                    party = rng.choice([2, 2, 2, 3, 4, 4, 5, 6])
                    if seats_free(conn, r, day, start) < party:
                        break
                    _insert(conn, rng, r, day, start, party)
                    placed += party
                    count += 1
    return count


def main() -> None:
    if not RESTAURANTS_JSON.exists():
        generate_restaurants()
    path = db_path()
    for suffix in ("", "-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)

    conn = connect(path)
    create_schema(conn)
    outlets = json.loads(RESTAURANTS_JSON.read_text(encoding="utf-8"))
    conn.execute("BEGIN")
    conn.executemany(
        "INSERT INTO restaurants VALUES (:id, :name, :concept, :area, :address, :lat, :lon, "
        ":cuisines, :price_band, :cost_for_two, :capacity, :opens, :closes, :rating, :phone, "
        ":features)",
        [{**o, "cuisines": json.dumps(o["cuisines"]), "features": json.dumps(o["features"])} for o in outlets],
    )
    n_res = seed_reservations(conn, random.Random(SEED))
    conn.execute("COMMIT")
    conn.close()  # closing checkpoints the WAL into the .db file
    print(
        f"Built {path}: {len(outlets)} outlets, {n_res} seeded reservations from {now().date()} for {SEED_DAYS} days."
    )


if __name__ == "__main__":
    main()

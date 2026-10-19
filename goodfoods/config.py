"""Constants, paths and the clock.

The clock is the one thing every module asks for ("is this date in the past?").
It reads GOODFOODS_NOW when set, so seeded data, tests and evals can pin a fixed
"now" and give the same results on any day.
"""

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RESTAURANTS_JSON = DATA_DIR / "restaurants.json"

TZ = ZoneInfo("Asia/Kolkata")

SLOT_MINUTES = 30  # bookings start on :00 or :30
DINING_SLOTS = 3  # a booking holds its seats for 3 slots = 90 minutes
MAX_PARTY_SIZE = 12  # larger groups are events: hand off to staff
BOOKING_HORIZON_DAYS = 60
NEARBY_RADIUS_KM = 6.0
ALTERNATIVE_WINDOW_MIN = 120  # look this far either side for other times
CHANGE_CUTOFF_HOURS = 2  # online changes and cancellations close this long before the booking


def db_path() -> Path:
    return Path(os.environ.get("GOODFOODS_DB", DATA_DIR / "goodfoods.db"))


def now() -> datetime:
    """Current time in Asia/Kolkata, as a naive datetime (all stored times are local)."""
    pinned = os.environ.get("GOODFOODS_NOW")
    if pinned:
        return datetime.fromisoformat(pinned).replace(tzinfo=None)
    return datetime.now(TZ).replace(tzinfo=None)

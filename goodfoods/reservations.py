"""Reservation writes and lookups.

Every write takes the database write lock (BEGIN IMMEDIATE) *before* checking
seats, so check-then-write is atomic and two guests cannot book the last table.
"""

import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta

from .availability import (
    check_party_size,
    check_slot,
    find_alternatives,
    get_restaurant,
    parse_date,
    parse_time,
    seats_free,
    to_hhmm,
    to_minutes,
)
from .config import CHANGE_CUTOFF_HOURS, now
from .errors import BookingError, is_blank

ID_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I/L: easy to read aloud


def new_id(conn: sqlite3.Connection, prefix: str, table: str, rng=secrets) -> str:
    while True:
        candidate = prefix + "".join(rng.choice(ID_ALPHABET) for _ in range(6))
        if not conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (candidate,)).fetchone():
            return candidate


def clean_phone(phone) -> str | None:
    """Normalise an Indian mobile number to 10 digits, or None if not given."""
    if is_blank(phone):
        return None
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        raise BookingError("invalid_phone", f"'{phone}' is not a 10-digit Indian mobile number.")
    return digits


def _summary(conn, reservation_id: str) -> dict:
    row = conn.execute(
        "SELECT v.*, r.name AS restaurant_name, r.area, r.address, r.phone AS outlet_phone "
        "FROM reservations v JOIN restaurants r ON r.id = v.restaurant_id WHERE v.id = ?",
        (reservation_id,),
    ).fetchone()
    keys = (
        "status",
        "restaurant_id",
        "restaurant_name",
        "area",
        "address",
        "outlet_phone",
        "date",
        "time",
        "party_size",
        "guest_name",
        "guest_phone",
        "special_requests",
    )
    return {"ok": True, "reservation_id": row["id"], **{k: row[k] for k in keys}}


def _write(conn, check_seats, statement: str, params: tuple) -> None:
    """Run `check_seats` and `statement` in one transaction that holds the write lock."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        check_seats()
        conn.execute(statement, params)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def make_reservation(
    conn, restaurant_id, date_str, time_str, party_size, guest_name, guest_phone=None, special_requests=None
) -> dict:
    r = get_restaurant(conn, restaurant_id)
    day, start = parse_date(date_str), parse_time(time_str)
    party = check_party_size(party_size, r)
    name = "" if is_blank(guest_name) else str(guest_name).strip()
    if not 0 < len(name) <= 60:
        raise BookingError("invalid_name", "A guest name (up to 60 characters) is needed to book.")
    phone = clean_phone(guest_phone)
    check_slot(r, day, start)

    def check_seats():
        if seats_free(conn, r, day, start) < party:
            raise BookingError(
                "no_longer_available",
                f"{r['name']} has no table for {party} at {to_hhmm(start)}.",
                alternatives=find_alternatives(conn, r, day, start, party),
            )

    res_id = new_id(conn, "GF", "reservations")
    stamp = now().isoformat(timespec="seconds")
    requests = None if is_blank(special_requests) else str(special_requests).strip()
    _write(
        conn,
        check_seats,
        "INSERT INTO reservations (id, restaurant_id, date, time, party_size, guest_name, "
        "guest_phone, special_requests, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?)",
        (res_id, r["id"], day.isoformat(), to_hhmm(start), party, name, phone, requests, stamp, stamp),
    )
    return _summary(conn, res_id)


def _load(conn, reservation_id) -> sqlite3.Row:
    res_id = re.sub(r"[\s-]", "", str(reservation_id)).upper()
    row = conn.execute("SELECT * FROM reservations WHERE id = ?", (res_id,)).fetchone()
    if row is None:
        raise BookingError("reservation_not_found", f"No reservation '{reservation_id}'. Ids look like GF7K2QXM.")
    return row


def _check_changeable(conn, row: sqlite3.Row) -> None:
    if row["status"] == "cancelled":
        raise BookingError("already_cancelled", f"Reservation {row['id']} is already cancelled.")
    starts = datetime.fromisoformat(f"{row['date']}T{row['time']}")
    if starts - now() < timedelta(hours=CHANGE_CUTOFF_HOURS):
        phone = get_restaurant(conn, row["restaurant_id"])["phone"]
        raise BookingError(
            "too_close_to_start",
            f"Online changes close {CHANGE_CUTOFF_HOURS} hours before the booking. Please call the outlet on {phone}.",
        )


def get_reservation(conn, reservation_id) -> dict:
    return _summary(conn, _load(conn, reservation_id)["id"])


def modify_reservation(conn, reservation_id, date_str=None, time_str=None, party_size=None) -> dict:
    """Change date, time or party size at the same outlet. Moving outlets is cancel + book."""
    row = _load(conn, reservation_id)
    _check_changeable(conn, row)
    if is_blank(date_str) and is_blank(time_str) and is_blank(party_size):
        raise BookingError("nothing_to_change", "Give a new date, time or party size.")
    r = get_restaurant(conn, row["restaurant_id"])
    day = date.fromisoformat(row["date"]) if is_blank(date_str) else parse_date(date_str)
    start = to_minutes(row["time"]) if is_blank(time_str) else parse_time(time_str)
    party = row["party_size"] if is_blank(party_size) else check_party_size(party_size, r)
    check_slot(r, day, start)

    def check_seats():
        if seats_free(conn, r, day, start, exclude_id=row["id"]) < party:
            raise BookingError(
                "not_available",
                f"{r['name']} has no table for {party} at {to_hhmm(start)} on {day}. "
                "The original booking is unchanged.",
                alternatives=find_alternatives(conn, r, day, start, party, row["id"]),
            )

    _write(
        conn,
        check_seats,
        "UPDATE reservations SET date = ?, time = ?, party_size = ?, updated_at = ? WHERE id = ?",
        (day.isoformat(), to_hhmm(start), party, now().isoformat(timespec="seconds"), row["id"]),
    )
    return _summary(conn, row["id"])


def cancel_reservation(conn, reservation_id) -> dict:
    row = _load(conn, reservation_id)
    _check_changeable(conn, row)
    conn.execute(
        "UPDATE reservations SET status = 'cancelled', updated_at = ? WHERE id = ?",
        (now().isoformat(timespec="seconds"), row["id"]),
    )
    return _summary(conn, row["id"])

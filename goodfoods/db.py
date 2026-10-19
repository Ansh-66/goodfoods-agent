"""SQLite schema and connection.

Three tables: outlets, reservations and staff hand-off tickets. Availability is
not stored: it is computed from confirmed reservations every time it is asked
for, so it can never drift out of sync.

Connections run in autocommit mode (isolation_level=None). Reads need no
transaction; every write opens one explicitly with BEGIN IMMEDIATE, which takes
the write lock *before* the availability check, so check-then-insert is atomic.
"""

import sqlite3
from pathlib import Path

from .config import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS restaurants (
    id            TEXT PRIMARY KEY,          -- 'R001'
    name          TEXT NOT NULL,
    concept       TEXT NOT NULL,
    area          TEXT NOT NULL,
    address       TEXT NOT NULL,
    lat           REAL NOT NULL,
    lon           REAL NOT NULL,
    cuisines      TEXT NOT NULL,             -- JSON list
    price_band    INTEGER NOT NULL,          -- 1 (budget) to 4 (fine dining)
    cost_for_two  INTEGER NOT NULL,          -- rupees
    capacity      INTEGER NOT NULL,          -- seats
    opens         TEXT NOT NULL,             -- 'HH:MM'
    closes        TEXT NOT NULL,             -- 'HH:MM'; '00:00' means midnight
    rating        REAL NOT NULL,
    phone         TEXT NOT NULL,
    features      TEXT NOT NULL              -- JSON list
);

CREATE TABLE IF NOT EXISTS reservations (
    id               TEXT PRIMARY KEY,       -- 'GF7K2QXM'
    restaurant_id    TEXT NOT NULL REFERENCES restaurants(id),
    date             TEXT NOT NULL,          -- 'YYYY-MM-DD'
    time             TEXT NOT NULL,          -- 'HH:MM', on a 30-minute boundary
    party_size       INTEGER NOT NULL CHECK (party_size > 0),
    guest_name       TEXT NOT NULL,
    guest_phone      TEXT,
    special_requests TEXT,
    status           TEXT NOT NULL DEFAULT 'confirmed'
                     CHECK (status IN ('confirmed', 'cancelled')),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handoffs (
    id           TEXT PRIMARY KEY,           -- 'HO4MZ8QA'
    reason       TEXT NOT NULL CHECK (reason IN ('complaint', 'large_group', 'event', 'other')),
    summary      TEXT NOT NULL,
    guest_name   TEXT NOT NULL,
    guest_phone  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_res_lookup
    ON reservations (restaurant_id, date, status);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or db_path(), isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # readers don't block the writer
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)

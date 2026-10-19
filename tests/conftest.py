"""Tests run against a fresh database with "now" pinned to Mon 19 Oct 2026, 18:00,
so results do not depend on the day they run."""

import os
import shutil

import pytest

NOW = "2026-10-19T18:00"
FRIDAY = "2026-10-23"


@pytest.fixture(scope="session")
def built_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "goodfoods.db"
    os.environ["GOODFOODS_DB"] = str(path)
    os.environ["GOODFOODS_NOW"] = NOW
    from goodfoods.setup_db import main

    main()
    return path


@pytest.fixture
def conn(built_db, tmp_path, monkeypatch):
    """A private copy of the seeded database per test, so writes don't leak between tests."""
    from goodfoods.db import connect

    copy = tmp_path / "goodfoods.db"
    shutil.copy(built_db, copy)
    monkeypatch.setenv("GOODFOODS_DB", str(copy))
    c = connect(copy)
    yield c
    c.close()


@pytest.fixture
def free_slot(conn):
    """A Friday time at R010 with room for 2, for tests that need to book something."""
    from goodfoods import availability as av

    r, day = av.get_restaurant(conn, "R010"), av.parse_date(FRIDAY)
    for t in ("13:00", "16:00", "17:00", "18:00"):
        if av.is_bookable(conn, r, day, av.to_minutes(t), 2):
            return "R010", t
    pytest.fail("seeded data has no free slot at R010 on Friday")


def error_code(fn, *args, **kwargs) -> str:
    from goodfoods.errors import BookingError

    with pytest.raises(BookingError) as e:
        fn(*args, **kwargs)
    return e.value.code

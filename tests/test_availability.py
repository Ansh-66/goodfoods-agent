import pytest

from goodfoods import availability as av
from goodfoods import reservations
from goodfoods.config import NEARBY_RADIUS_KM

from .conftest import FRIDAY, error_code


@pytest.mark.parametrize(
    "args, code",
    [
        (("R999", FRIDAY, "19:00", 2), "restaurant_not_found"),
        (("R010", "2026-10-18", "19:00", 2), "date_in_past"),
        (("R010", "2027-03-01", "19:00", 2), "date_too_far"),
        (("R010", "23/10/2026", "19:00", 2), "invalid_date"),
        (("R010", FRIDAY, "7pm", 2), "invalid_time"),
        (("R010", FRIDAY, "19:15", 2), "invalid_time"),
        (("R010", FRIDAY, "19:00", 0), "invalid_party_size"),
        (("R010", FRIDAY, "19:00", 20), "party_too_large"),
        (("R010", "2026-10-19", "17:30", 2), "time_in_past"),  # now is 18:00 on the 19th
        (("R001", FRIDAY, "22:00", 2), "outside_opening_hours"),  # R001 last seating 21:00
    ],
)
def test_invalid_requests_are_rejected(conn, args, code):
    assert error_code(av.check_availability, conn, *args) == code


def test_full_outlet_routes_to_nearby_outlets_that_are_really_free(conn):
    result = av.check_availability(conn, "R001", FRIDAY, "20:00", 4)
    nearby = result["alternatives"]["nearby_outlets_same_time"]
    assert result["available"] is False and nearby
    for alt in nearby:
        assert alt["distance_km"] <= NEARBY_RADIUS_KM
        assert av.check_availability(conn, alt["restaurant_id"], FRIDAY, "20:00", 4)["available"]


def test_partly_full_outlet_offers_other_times_that_are_really_free(conn):
    result = av.check_availability(conn, "R004", FRIDAY, "20:00", 2)
    times = result["alternatives"]["same_outlet_other_times"]
    assert result["available"] is False and "18:30" in times
    assert all(av.check_availability(conn, "R004", FRIDAY, t, 2)["available"] for t in times)


def test_booking_holds_seats_for_exactly_90_minutes(conn, free_slot):
    rid, t = free_slot
    day, start = av.parse_date(FRIDAY), av.to_minutes(t)
    slots = [start + k * 30 for k in range(-1, 4)]  # one slot either side as controls
    before = av.occupancy(conn, rid, day)
    reservations.make_reservation(conn, rid, FRIDAY, t, 2, "Guest")
    after = av.occupancy(conn, rid, day)
    assert [after.get(s, 0) - before.get(s, 0) for s in slots] == [0, 2, 2, 2, 0]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("R004", "R004"),
        ("WokStreetWhitefield", "R004"),  # what the model actually sent in the eval
        ("GoodFoods Green Bowl, Koramangala", "R001"),
        ("Andhra Spice Indiranagar", "R002"),  # exact name wins over "... Indiranagar II"
        ("Andhra Spice Indiranagar II", "R074"),
    ],
)
def test_outlets_resolve_by_id_or_name(conn, name, expected):
    assert av.get_restaurant(conn, name)["id"] == expected


def test_ambiguous_or_unknown_names_are_rejected(conn):
    assert error_code(av.get_restaurant, conn, "Trattoria") == "ambiguous_restaurant"
    assert error_code(av.get_restaurant, conn, "Noma") == "restaurant_not_found"

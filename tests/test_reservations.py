from goodfoods import availability as av
from goodfoods import handoff, reservations

from .conftest import FRIDAY, error_code


def fill(conn, rid: str, t: str) -> None:
    r, day, start = av.get_restaurant(conn, rid), av.parse_date(FRIDAY), av.to_minutes(t)
    while (left := av.seats_free(conn, r, day, start)) > 0:
        reservations.make_reservation(conn, rid, FRIDAY, t, min(left, 12), "Filler")


def test_lifecycle_book_lookup_modify_cancel(conn, free_slot):
    rid, t = free_slot
    booked = reservations.make_reservation(conn, rid, FRIDAY, t, 2, " Ansh ", "+91 98765 43210")
    res_id = booked["reservation_id"]
    assert res_id.startswith("GF") and len(res_id) == 8
    assert (booked["guest_name"], booked["guest_phone"]) == ("Ansh", "9876543210")
    assert reservations.get_reservation(conn, res_id.lower())["party_size"] == 2

    assert reservations.modify_reservation(conn, res_id, party_size=3)["party_size"] == 3
    assert reservations.cancel_reservation(conn, res_id)["status"] == "cancelled"
    assert error_code(reservations.cancel_reservation, conn, res_id) == "already_cancelled"


def test_cannot_overbook(conn, free_slot):
    rid, t = free_slot
    fill(conn, rid, t)
    assert error_code(reservations.make_reservation, conn, rid, FRIDAY, t, 2, "Late") == "no_longer_available"


def test_modify_into_full_slot_keeps_the_original(conn):
    res_id = reservations.make_reservation(conn, "R001", FRIDAY, "13:00", 2, "Guest")["reservation_id"]
    assert error_code(reservations.modify_reservation, conn, res_id, time_str="20:00") == "not_available"
    assert reservations.get_reservation(conn, res_id)["time"] == "13:00"


def test_modify_does_not_count_its_own_seats(conn, free_slot):
    rid, t = free_slot
    res_id = reservations.make_reservation(conn, rid, FRIDAY, t, 2, "Guest")["reservation_id"]
    fill(conn, rid, t)
    assert reservations.modify_reservation(conn, res_id, party_size=2)["ok"]


def test_no_online_changes_within_two_hours(conn):
    res_id = reservations.make_reservation(conn, "R010", "2026-10-19", "19:30", 2, "Guest")["reservation_id"]
    assert error_code(reservations.cancel_reservation, conn, res_id) == "too_close_to_start"


def test_rejects_bad_input(conn, free_slot):
    rid, t = free_slot
    assert error_code(reservations.make_reservation, conn, rid, FRIDAY, t, 2, "G", "12345") == "invalid_phone"
    assert error_code(reservations.make_reservation, conn, rid, FRIDAY, t, 2, "  ") == "invalid_name"
    assert error_code(reservations.get_reservation, conn, "GFNOPE00") == "reservation_not_found"


def test_handoff_creates_a_ticket(conn):
    ticket = handoff.request_human_handoff(conn, "large_group", "30 people, Sat dinner", "Ansh", "9876543210")
    assert ticket["ticket_id"].startswith("HO")
    assert error_code(handoff.request_human_handoff, conn, "complaint", "Cold food", "Ansh", None) == "missing_details"

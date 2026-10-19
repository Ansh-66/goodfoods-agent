"""Hand-off to GoodFoods staff for what the agent should not handle itself:
complaints, groups above the online limit, events, and anything out of scope.

The ticket is stored with the agent's summary so staff start with full context.
Sending it to a real ticketing system is the next integration step.
"""

from typing import Literal

from .config import now
from .errors import BookingError, is_blank
from .reservations import clean_phone, new_id

HandoffReason = Literal["complaint", "large_group", "event", "other"]
REASONS = HandoffReason.__args__


def request_human_handoff(conn, reason: str, summary: str, guest_name: str, guest_phone: str) -> dict:
    if reason not in REASONS:
        raise BookingError("invalid_reason", f"Reason must be one of {', '.join(REASONS)}.")
    if is_blank(summary) or is_blank(guest_name):
        raise BookingError("missing_details", "A summary and the guest's name are needed.")
    phone = clean_phone(guest_phone)
    if phone is None:
        raise BookingError("missing_details", "A phone number is needed so staff can call back.")
    ticket = new_id(conn, "HO", "handoffs")
    conn.execute(
        "INSERT INTO handoffs (id, reason, summary, guest_name, guest_phone, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'open', ?)",
        (ticket, reason, summary.strip(), guest_name.strip(), phone, now().isoformat(timespec="seconds")),
    )
    return {
        "ok": True,
        "ticket_id": ticket,
        "message": "A GoodFoods team member will call the guest back within 2 working hours.",
    }

"""GoodFoods MCP server: the booking rules exposed as eight MCP tools over stdio.

Each tool is a thin wrapper: open a connection, call the rules module, and turn a
BookingError into {"ok": false, "error": <code>, "message": <sentence>}. Errors
come back as normal results, not exceptions, so the model reads the message and
can correct itself (ask the guest, fix an argument, try an alternative).

The SDK validates argument *types* against each tool's schema before the
function runs; the rules modules validate everything else.

Run standalone:  python -m goodfoods.server   (speaks MCP on stdin/stdout,
so nothing in this process may print to stdout).
"""

import contextlib
import sys
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from . import availability, catalog, handoff, reservations
from .config import db_path
from .db import connect
from .errors import BookingError
from .setup_db import main as build_db

mcp = MCPServer("goodfoods-reservations")

Date = Annotated[str, Field(description="YYYY-MM-DD")]
Time = Annotated[str, Field(description="24-hour HH:MM on the hour or half hour, e.g. 19:30")]
# No example id here: a model copies a concrete example ("R012") instead of looking one up.
RestaurantId = Annotated[str, Field(description="Outlet id exactly as returned by recommend_restaurants")]
ReservationId = Annotated[str, Field(description="Booking id, e.g. GF7K2QXM")]
# Set by the agent loop, never by the model: the loop strips it from the schemas it sends.
DryRun = Annotated[bool, Field(description="Check everything, write nothing")]
FEATURE_HELP = (
    "Any of: veg_friendly, outdoor_seating, private_dining, kid_friendly, bar, "
    "live_music, parking, wheelchair_accessible"
)


def _run(fn, *args) -> dict:
    if not db_path().exists():  # first run: build the demo database
        with contextlib.redirect_stdout(sys.stderr):  # keep stdout clean for MCP
            build_db()
    conn = connect()
    try:
        return fn(conn, *args)
    except BookingError as e:
        return e.to_dict()
    finally:
        conn.close()


@mcp.tool()
def recommend_restaurants(
    area: Annotated[str | None, Field(description="Bengaluru neighbourhood, e.g. Koramangala")] = None,
    cuisine: Annotated[str | None, Field(description="e.g. Italian, South Indian, biryani")] = None,
    party_size: int | None = None,
    date: Date | None = None,
    time: Time | None = None,
    max_price_band: Annotated[int | None, Field(description="1 budget to 4 fine dining")] = None,
    features: Annotated[list[str] | None, Field(description=FEATURE_HELP)] = None,
    occasion: Annotated[str | None, Field(description="e.g. birthday, date night, business")] = None,
    name: Annotated[str | None, Field(description="Part of an outlet name")] = None,
    limit: int = 3,
) -> dict:
    """Find and rank GoodFoods outlets for the guest, with a reason for each. Use it to find an
    outlet's id. To answer whether one specific outlet is free, follow up with check_availability,
    which also returns alternatives when that outlet is full.
    All filters are optional. Pass date and time to rank outlets that are free first."""
    return _run(
        catalog.recommend_restaurants,
        area,
        cuisine,
        party_size,
        date,
        time,
        max_price_band,
        features,
        occasion,
        name,
        limit,
    )


@mcp.tool()
def get_restaurant_details(restaurant_id: RestaurantId) -> dict:
    """Address, phone, hours, last booking time, features and policies of one outlet."""
    return _run(catalog.get_restaurant_details, restaurant_id)


@mcp.tool()
def check_availability(restaurant_id: RestaurantId, date: Date, time: Time, party_size: int) -> dict:
    """Check if an outlet has a table. If it is full, the result lists other free times
    at the same outlet and nearby outlets free at the same time."""
    return _run(availability.check_availability, restaurant_id, date, time, party_size)


@mcp.tool()
def make_reservation(
    restaurant_id: RestaurantId,
    date: Date,
    time: Time,
    party_size: int,
    guest_name: str,
    guest_phone: str | None = None,
    special_requests: str | None = None,
    dry_run: DryRun = False,
) -> dict:
    """Book a table. Returns the booking id."""
    return _run(
        reservations.make_reservation,
        restaurant_id,
        date,
        time,
        party_size,
        guest_name,
        guest_phone,
        special_requests,
        dry_run,
    )


@mcp.tool()
def get_reservation(reservation_id: ReservationId) -> dict:
    """Look up a booking by its id."""
    return _run(reservations.get_reservation, reservation_id)


@mcp.tool()
def modify_reservation(
    reservation_id: ReservationId,
    date: Date | None = None,
    time: Time | None = None,
    party_size: int | None = None,
    dry_run: DryRun = False,
) -> dict:
    """Change the date, time or party size of a booking at the same outlet.
    If the new slot is full, the booking is left unchanged."""
    return _run(reservations.modify_reservation, reservation_id, date, time, party_size, dry_run)


@mcp.tool()
def cancel_reservation(reservation_id: ReservationId, dry_run: DryRun = False) -> dict:
    """Cancel a booking."""
    return _run(reservations.cancel_reservation, reservation_id, dry_run)


@mcp.tool()
def request_human_handoff(
    reason: handoff.HandoffReason,
    summary: Annotated[str, Field(description="What the guest needs, in one or two sentences")],
    guest_name: str,
    guest_phone: str,
) -> dict:
    """Pass the guest to GoodFoods staff: complaints, groups over 12, events,
    or anything else you cannot do. Staff call the guest back."""
    return _run(handoff.request_human_handoff, reason, summary, guest_name, guest_phone)


if __name__ == "__main__":
    mcp.run("stdio")

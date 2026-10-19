"""The agent loop against a scripted model: every rule the loop enforces in code."""

import asyncio
import json
from datetime import datetime

from mcp.client.client import Client

from goodfoods.agent import Conversation, run_turn
from goodfoods.agent.llm import MalformedToolCall, ModelReply, ToolRequest
from goodfoods.agent.loop import FALLBACK_REPLY, MAX_STEPS, tool_schemas
from goodfoods.agent.prompt import build_system_prompt
from goodfoods.server import mcp

from .conftest import FRIDAY

BOOKING = {
    "restaurant_id": "R010",
    "date": FRIDAY,
    "time": "13:00",
    "party_size": 2,
    "guest_name": "Ansh",
    "guest_phone": "9876543210",
}


class ScriptedModel:
    """Plays back a fixed list of replies (or exceptions) and records every prompt."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    async def complete(self, messages, tools):
        self.prompts.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def call(name, args, call_id="c1"):
    return ModelReply(None, [ToolRequest(call_id, name, json.dumps(args) if isinstance(args, dict) else args)])


def say(text):
    return ModelReply(text)


def play(conv, guest_text, model):
    async def go():
        async with Client(mcp) as session:
            return await run_turn(conv, guest_text, model, session)

    return asyncio.run(go())


def booking_count(conn):
    return conn.execute("SELECT COUNT(*) FROM reservations WHERE guest_name = 'Ansh'").fetchone()[0]


def test_write_runs_only_after_the_guest_confirms(conn):
    conv = Conversation()
    first = play(
        conv, "Book R010 Friday 1pm for 2, name Ansh", ScriptedModel(call("make_reservation", BOOKING), say("Confirm?"))
    )
    assert first.steps[0].held_for_confirmation and booking_count(conn) == 0

    same_number = {**BOOKING, "guest_phone": "+91 98765 43210"}  # repeated in another format
    second = play(conv, "yes", ScriptedModel(call("make_reservation", same_number), say("Booked.")))
    assert second.steps[0].result["ok"] and booking_count(conn) == 1


def test_model_cannot_confirm_its_own_write(conn):
    turn = play(
        Conversation(),
        "Book it",
        ScriptedModel(call("make_reservation", BOOKING), call("make_reservation", BOOKING), say("Confirm?")),
    )
    assert all(s.held_for_confirmation for s in turn.steps) and booking_count(conn) == 0


def test_changed_details_need_a_fresh_confirmation(conn):
    conv = Conversation()
    play(conv, "Book it", ScriptedModel(call("make_reservation", BOOKING), say("Confirm?")))
    later = {**BOOKING, "time": "13:30"}
    turn = play(conv, "Actually 1:30", ScriptedModel(call("make_reservation", later), say("Confirm 13:30?")))
    assert turn.steps[0].held_for_confirmation and booking_count(conn) == 0


def test_write_that_would_fail_is_rejected_before_any_read_back(conn):
    conv = Conversation()
    bad_phone = {**BOOKING, "guest_phone": "724572578"}  # 9 digits
    turn = play(conv, "Book it", ScriptedModel(call("make_reservation", bad_phone), say("Which number?")))
    assert turn.steps[0].result["error"] == "invalid_phone" and not turn.steps[0].held_for_confirmation
    assert conv.held_write is None and booking_count(conn) == 0


def test_model_never_sees_the_dry_run_switch(conn):
    async def go():
        async with Client(mcp) as session:
            return tool_schemas((await session.list_tools()).tools)

    assert "dry_run" not in json.dumps(asyncio.run(go()))


def test_bad_calls_are_returned_to_the_model(conn):
    turn = play(
        Conversation(),
        "hi",
        ScriptedModel(
            call("book_table", {}),  # unknown tool
            call("check_availability", "{not json"),  # unparseable arguments
            call("check_availability", {"restaurant_id": "R010"}),  # missing required fields
            say("Sorry, which outlet?"),
        ),
    )
    assert [s.result["error"] for s in turn.steps] == ["unknown_tool", "bad_json", "invalid_arguments"]
    assert turn.reply == "Sorry, which outlet?"


def test_malformed_call_is_retried_once_with_a_nudge(conn):
    model = ScriptedModel(MalformedToolCall("tool_use_failed"), say("Which area?"))
    turn = play(Conversation(), "table pls", model)
    assert turn.reply == "Which area?" and "could not be parsed" in model.prompts[1][-1]["content"]


def test_runaway_loop_stops_at_max_steps(conn):
    looping = [call("get_restaurant_details", {"restaurant_id": "R010"}, f"c{i}") for i in range(MAX_STEPS)]
    turn = play(Conversation(), "hours?", ScriptedModel(*looping))
    assert turn.model_calls == MAX_STEPS and turn.reply == FALLBACK_REPLY


def test_prompt_calendar_resolves_relative_dates():
    prompt = build_system_prompt(datetime(2026, 10, 19, 18, 0))
    assert "Monday 19 October 2026, 18:00" in prompt
    assert "Tue 20 Oct = 2026-10-20 (tomorrow)" in prompt and "Fri 23 Oct = 2026-10-23" in prompt

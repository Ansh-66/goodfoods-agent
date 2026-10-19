"""The MCP layer end to end: a real MCP client talking to the server in memory."""

import asyncio
import json

from mcp.client.client import Client

from goodfoods.server import mcp

from .conftest import FRIDAY


def call(tool: str, args: dict):
    async def go():
        async with Client(mcp) as client:
            return await client.call_tool(tool, args)

    return asyncio.run(go())


def test_exposes_eight_described_tools(conn):
    async def go():
        async with Client(mcp) as client:
            return (await client.list_tools()).tools

    tools = asyncio.run(go())
    assert len(tools) == 8 and all(t.description for t in tools)


def test_rule_errors_are_results_the_model_can_read(conn):
    result = call(
        "check_availability", {"restaurant_id": "R010", "date": "2026-10-18", "time": "19:00", "party_size": 2}
    )
    assert result.is_error is False
    assert json.loads(result.content[0].text)["error"] == "date_in_past"


def test_type_errors_are_flagged_and_numeric_strings_coerced(conn):
    # The agent relies on this: no validation layer of its own.
    bad = call("check_availability", {"restaurant_id": "R010", "date": FRIDAY, "time": "19:00", "party_size": "six"})
    assert bad.is_error is True and "party_size" in bad.content[0].text
    ok = call("check_availability", {"restaurant_id": "R001", "date": FRIDAY, "time": "20:00", "party_size": "4"})
    assert json.loads(ok.content[0].text)["available"] is False

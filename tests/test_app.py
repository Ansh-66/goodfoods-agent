"""The Streamlit page end to end, with the model scripted and the MCP server real."""

import json

import pytest
from streamlit.testing.v1 import AppTest

import goodfoods.agent
from goodfoods.agent.llm import ModelReply, ToolRequest


class OneLookupModel:
    """Looks up R010's details, then answers."""

    def __init__(self):
        self.calls = 0

    async def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return ModelReply(
                None, [ToolRequest("c1", "get_restaurant_details", json.dumps({"restaurant_id": "R010"}))]
            )
        return ModelReply("It closes at 23:00.")


@pytest.fixture
def app(conn, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test")
    monkeypatch.setattr(goodfoods.agent, "GroqModel", OneLookupModel)
    return AppTest.from_file("../streamlit_app.py", default_timeout=60).run()


def test_chat_turn_shows_reply_and_tool_trace(app):
    app.chat_input[0].set_value("When does R010 close?").run()
    assert app.chat_message[-1].markdown[0].value == "It closes at 23:00."
    assert app.expander[0].label == "Tools used (1)"

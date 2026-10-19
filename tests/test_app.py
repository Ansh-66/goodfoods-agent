"""The Streamlit page end to end, with the model scripted and the MCP server real."""

import json

import httpx
import pytest
from groq import AuthenticationError
from streamlit.testing.v1 import AppTest

import goodfoods.agent
import goodfoods.voice
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


class RejectedKeyModel:
    async def complete(self, messages, tools):
        response = httpx.Response(401, request=httpx.Request("POST", "https://api.groq.com"))
        raise AuthenticationError("Invalid API Key", response=response, body=None)


def test_rejected_api_key_shows_a_clear_error(conn, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "wrong")
    monkeypatch.setattr(goodfoods.agent, "GroqModel", RejectedKeyModel)
    app = AppTest.from_file("../streamlit_app.py", default_timeout=60).run()
    app.chat_input[0].set_value("hi").run()
    assert not app.exception and "rejected the API key" in app.error[0].value


class FakeVoice:
    spoken = 0

    async def speak(self, text, language):
        FakeVoice.spoken += 1
        return b"RIFF-wav"


def test_reply_is_spoken_only_when_the_guest_asks(conn, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test")
    monkeypatch.setenv("SARVAM_API_KEY", "test")
    monkeypatch.setattr(goodfoods.agent, "GroqModel", OneLookupModel)
    monkeypatch.setattr(goodfoods.voice, "Voice", FakeVoice)
    app = AppTest.from_file("../streamlit_app.py", default_timeout=60).run()
    app.chat_input[0].set_value("When does R010 close?").run()
    assert FakeVoice.spoken == 0  # nothing generated or played automatically
    app.button(key="listen1").click().run()
    app.button(key="listen1").click().run()
    assert FakeVoice.spoken == 1  # generated once, then replayed

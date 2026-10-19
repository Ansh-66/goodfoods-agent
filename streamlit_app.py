"""GoodFoods reservation chat: a thin Streamlit layer over goodfoods.agent.

Streamlit reruns this script top to bottom on every interaction; anything that
must survive between messages lives in st.session_state. Each guest message
opens a fresh MCP session (the server runs as a subprocess), which costs about
a second and keeps no connection state to manage across reruns.

Run:  streamlit run streamlit_app.py
"""

import asyncio
import json
import logging
import os
import time

import streamlit as st
from dotenv import load_dotenv
from groq import APIError

from goodfoods.agent import Conversation, GroqModel, TurnResult, mcp_session, run_turn
from goodfoods.agent.llm import DEFAULT_MODEL

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("goodfoods.app")

EXAMPLES = [
    "Dinner for 4 this Friday at 8 pm, somewhere Italian near Koramangala",
    "Is GoodFoods Green Bowl in Koramangala free on Friday at 8 pm for 4?",
    "We're 20 people for an office party next Saturday",
    "What time does the Wok Street outlet in Whitefield close?",
]

st.set_page_config(page_title="GoodFoods Reservations", page_icon="🍽️", layout="centered")


def reset() -> None:
    st.session_state.conversation = Conversation()
    st.session_state.transcript = []  # (role, text, TurnResult | None, seconds)


if "conversation" not in st.session_state:
    reset()


async def take_turn(text: str) -> TurnResult:
    async with mcp_session() as session:
        return await run_turn(st.session_state.conversation, text, GroqModel(), session)


def show_steps(turn: TurnResult, seconds: float) -> None:
    tokens = turn.input_tokens + turn.output_tokens
    st.caption(f"{turn.model_calls} model call(s) · {tokens:,} tokens · {seconds:.1f} s")
    if not turn.steps:
        return
    with st.expander(f"Tools used ({len(turn.steps)})"):
        for step in turn.steps:
            label = "held until the guest confirms" if step.held_for_confirmation else step.result.get("error", "ok")
            st.markdown(f"**{step.name}** · {label}")
            st.code(json.dumps(step.arguments, indent=2), language="json")
            if not step.held_for_confirmation:
                st.json(step.result, expanded=False)


with st.sidebar:
    st.header("GoodFoods")
    st.write("Book a table at any of our 75 outlets across Bengaluru.")
    st.subheader("Try asking")
    for example in EXAMPLES:
        if st.button(example, use_container_width=True):
            st.session_state.queued = example
    st.divider()
    st.button("New conversation", on_click=reset, use_container_width=True)
    st.caption(f"Model: {os.environ.get('GOODFOODS_MODEL', DEFAULT_MODEL)} on Groq")

st.title("Reserve a table")

if not os.environ.get("GROQ_API_KEY"):
    st.error("GROQ_API_KEY is not set. Add it to .env (see .env.example) and restart.")
    st.stop()

for role, text, turn, seconds in st.session_state.transcript:
    with st.chat_message(role):
        st.markdown(text)
        if turn:
            show_steps(turn, seconds)

if not st.session_state.transcript:
    with st.chat_message("assistant"):
        st.markdown("Hi! Tell me what you're planning: area, cuisine, date, time and how many people.")

guest_text = st.chat_input("Ask about a table") or st.session_state.pop("queued", None)
if guest_text:
    with st.chat_message("user"):
        st.markdown(guest_text)
    st.session_state.transcript.append(("user", guest_text, None, 0.0))
    with st.chat_message("assistant"), st.spinner("Checking tables..."):
        started = time.perf_counter()
        try:
            turn = asyncio.run(take_turn(guest_text))
        except APIError:
            log.exception("model call failed")
            st.error("The language model is not responding (possibly the free-tier rate limit). Try again shortly.")
            st.stop()
        seconds = time.perf_counter() - started
        st.markdown(turn.reply)
        show_steps(turn, seconds)
    st.session_state.transcript.append(("assistant", turn.reply, turn, seconds))

"""GoodFoods reservation chat: a thin Streamlit layer over goodfoods.agent.

Streamlit reruns this script top to bottom on every interaction; anything that
must survive between messages lives in st.session_state. Each guest message
opens a fresh MCP session (the server runs as a subprocess), which costs about
a second and keeps no connection state to manage across reruns.

Guests can type in English or, when SARVAM_API_KEY is set, speak in any Indian
language: goodfoods.voice translates speech in and replies out, around the agent.

Run:  streamlit run streamlit_app.py
"""

import asyncio
import json
import logging
import os
import time

import streamlit as st
from dotenv import load_dotenv
from groq import APIError, AuthenticationError
from sarvamai.core.api_error import ApiError as SarvamError

from goodfoods.agent import Conversation, GroqModel, TurnResult, mcp_session, run_turn
from goodfoods.agent.llm import DEFAULT_MODEL
from goodfoods.voice import ENGLISH, Voice, language_name

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("goodfoods.app")

EXAMPLES = [
    "Dinner for 4 this Friday at 8 pm, somewhere Italian near Koramangala",
    "Is GoodFoods Green Bowl in Koramangala free on Friday at 8 pm for 4?",
    "We're 20 people for an office party next Saturday",
    "What time does the Wok Street outlet in Whitefield close?",
]
VOICE_ENABLED = bool(os.environ.get("SARVAM_API_KEY"))

st.set_page_config(page_title="GoodFoods Reservations", page_icon="🍽️", layout="centered")


def reset() -> None:
    st.session_state.conversation = Conversation()
    st.session_state.transcript = []  # dicts: role, text, and optional note, english, audio, turn, seconds


if "conversation" not in st.session_state:
    reset()


def model_error(exc: BaseException) -> APIError | None:
    """The Groq error behind `exc`, if any. The MCP client runs inside an anyio task
    group, which wraps whatever is raised in an ExceptionGroup."""
    if isinstance(exc, APIError):
        return exc
    for inner in getattr(exc, "exceptions", ()):
        if found := model_error(inner):
            return found
    return None


async def take_turn(text: str) -> TurnResult:
    async with mcp_session() as session:
        return await run_turn(st.session_state.conversation, text, GroqModel(), session)


def render(entry: dict, index: int) -> None:
    if entry.get("note"):
        st.caption(entry["note"])
    listen = False
    if VOICE_ENABLED and entry["role"] == "assistant":
        text_column, button_column = st.columns([14, 1])
        text_column.markdown(entry["text"])
        listen = button_column.button(
            "", icon=":material/volume_up:", key=f"listen{index}", type="tertiary", help="Listen"
        )
    else:
        st.markdown(entry["text"])
    if entry.get("english"):
        st.caption(f"English: {entry['english']}")
    language = entry.get("language", ENGLISH)
    if listen and "audio" not in entry:  # speech is generated on first request, then kept
        try:
            entry["audio"] = asyncio.run(Voice().speak(entry["text"], language))
        except SarvamError as e:
            log.error("text-to-speech failed: %s", e)
            st.warning("Couldn't generate the audio right now. Please try again.")
    if entry.get("audio"):
        st.audio(entry["audio"], format="audio/wav", autoplay=listen)
    elif listen and "audio" in entry:
        st.caption(f"Audio isn't available in {language_name(language)}.")
    turn = entry.get("turn")
    if not turn:
        return
    st.caption(
        f"{turn.model_calls} model call(s) · {turn.input_tokens + turn.output_tokens:,} tokens · "
        f"{entry['seconds']:.1f} s"
    )
    if turn.steps:
        with st.expander(f"Tools used ({len(turn.steps)})"):
            for step in turn.steps:
                label = (
                    "held until the guest confirms" if step.held_for_confirmation else step.result.get("error", "ok")
                )
                st.markdown(f"**{step.name}** · {label}")
                st.code(json.dumps(step.arguments, indent=2), language="json")
                if not step.held_for_confirmation:
                    st.json(step.result, expanded=False)


def show(entry: dict) -> None:
    st.session_state.transcript.append(entry)
    with st.chat_message(entry["role"]):
        render(entry, len(st.session_state.transcript) - 1)


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
    st.caption("Voice: Sarvam AI" if VOICE_ENABLED else "Voice off: set SARVAM_API_KEY to enable")

st.title("Reserve a table")

if not os.environ.get("GROQ_API_KEY"):
    st.error("GROQ_API_KEY is not set. Add it to .env (see .env.example) and restart.")
    st.stop()

for i, entry in enumerate(st.session_state.transcript):
    with st.chat_message(entry["role"]):
        render(entry, i)

if not st.session_state.transcript:
    with st.chat_message("assistant"):
        st.markdown("Hi! Tell me what you're planning: area, cuisine, date, time and how many people.")

# One input for both: with voice on, the chat box has a mic button, and a submission
# carries text, a recording, or both. "disable" greys the box out while a turn runs.
submitted = st.chat_input(
    "Type, or tap the mic and speak in any Indian language" if VOICE_ENABLED else "Ask about a table",
    accept_audio=VOICE_ENABLED,
    submit_mode="disable",
)
if isinstance(submitted, str) or submitted is None:
    typed, recording = submitted, None
else:
    typed, recording = submitted.text or None, submitted.audio
typed = typed or st.session_state.pop("queued", None)

guest_text, language = typed, ENGLISH
if not typed and recording:
    try:
        heard = asyncio.run(Voice().listen(recording.getvalue()))
    except SarvamError as e:
        log.error("speech-to-text failed: %s", e)
        st.error("Couldn't process the recording (Sarvam speech service). Please try again or type instead.")
        st.stop()
    if not heard.english:
        st.warning("I didn't catch that. Please try again.")
        st.stop()
    guest_text, language = heard.english, heard.language

if guest_text:
    show({"role": "user", "text": guest_text, "note": None if typed else f"Spoken in {language_name(language)}"})
    with st.spinner("Checking tables..."):
        started = time.perf_counter()
        try:
            turn = asyncio.run(take_turn(guest_text))
        except Exception as exc:
            error = model_error(exc)
            if error is None:
                raise
            log.error("model call failed: %s", error)
            if isinstance(error, AuthenticationError):
                st.error("Groq rejected the API key. Check GROQ_API_KEY in .env, then restart the app.")
            else:
                st.error("The language model is not responding (possibly the free-tier rate limit). Try again shortly.")
            st.stop()
        seconds = time.perf_counter() - started

    reply = {"role": "assistant", "text": turn.reply, "turn": turn, "seconds": seconds, "language": language}
    if language != ENGLISH:  # answer in the language the guest spoke
        try:
            reply.update(text=asyncio.run(Voice().translate(turn.reply, language)), english=turn.reply)
        except SarvamError as e:
            log.error("translation failed: %s", e)
            reply.update(note="Translation unavailable; showing the English answer.", language=ENGLISH)
    show(reply)

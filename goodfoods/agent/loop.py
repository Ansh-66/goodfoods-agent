"""The agent loop, written from scratch.

One guest message is one turn:

    send system prompt + history + tool schemas to the model
    while the model asks for tools (at most MAX_STEPS model calls):
        run each tool on the MCP server, append the result, ask the model again
    the first plain-text reply ends the turn

The model decides intent: no keyword rules on the guest's text anywhere. Code
enforces what must not depend on the model getting it right:

- Confirmation gate. A write (book, modify, cancel) never runs on the model's
  first request. The loop holds it and tells the model to read the details back;
  it runs only when the model requests the same write again after the guest has
  replied. So nothing is written without the guest seeing it first.
- Recovery. Unknown tools, unparseable arguments and schema errors go back to
  the model as tool results so it can correct itself; a call the provider could
  not parse is retried once with a nudge.
- Budget. Tool results are compacted and capped, and only the last few guest
  turns are sent, to stay inside the free tier's 6,000 tokens per minute.
"""

import json
import logging
from dataclasses import dataclass, field

from mcp.client.client import Client

from ..config import now
from .llm import ChatModel, MalformedToolCall, ModelReply
from .prompt import build_system_prompt

log = logging.getLogger(__name__)

MAX_STEPS = 6  # model calls per guest turn
HISTORY_TURNS = 6  # guest turns kept in the prompt
RESULT_CHARS = 2000  # cap on one tool result

# The fields that identify a write: the guest confirms these.
WRITE_KEYS = {
    "make_reservation": ("restaurant_id", "date", "time", "party_size", "guest_name"),
    "modify_reservation": ("reservation_id", "date", "time", "party_size"),
    "cancel_reservation": ("reservation_id",),
}
FALLBACK_REPLY = "Sorry, I couldn't complete that. Could you say it another way?"
MALFORMED_NUDGE = (
    "Your last tool call could not be parsed. Call one tool with JSON arguments that match its schema, "
    "or reply to the guest in plain text."
)


@dataclass
class ToolStep:
    """One tool call in a turn, kept for the UI trace and the eval."""

    name: str
    arguments: dict
    result: dict
    held_for_confirmation: bool = False


@dataclass
class TurnResult:
    reply: str
    steps: list[ToolStep] = field(default_factory=list)
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Conversation:
    messages: list[dict] = field(default_factory=list)  # system prompt excluded: rebuilt each turn
    guest_turns: int = 0
    held_write: str | None = None  # identity of the write awaiting confirmation
    held_at_turn: int = 0


def tool_schemas(tools) -> list[dict]:
    """MCP tool list -> the function-calling format Groq expects, minus the pydantic
    'title' keys, which cost tokens and tell the model nothing."""

    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": strip(t.input_schema)},
        }
        for t in tools
    ]


def write_identity(name: str, args: dict) -> str:
    """Same booking -> same string, despite cosmetic differences in how the model repeats it."""
    fields = {k: str(args[k]).strip().lower() for k in WRITE_KEYS[name] if args.get(k) not in (None, "")}
    return name + json.dumps(fields, sort_keys=True)


def _recent(messages: list[dict]) -> list[dict]:
    """Messages from the HISTORY_TURNS-th last guest message onwards (never splits a tool exchange)."""
    guest_indexes = [i for i, m in enumerate(messages) if m["role"] == "user"]
    start = guest_indexes[-HISTORY_TURNS] if len(guest_indexes) > HISTORY_TURNS else 0
    return messages[start:]


async def _execute(session: Client, conv: Conversation, name: str, args: dict, known: set[str]) -> ToolStep:
    if name not in known:
        return ToolStep(name, args, {"ok": False, "error": "unknown_tool", "message": f"No tool '{name}'."})

    if name in WRITE_KEYS:
        identity = write_identity(name, args)
        confirmed = identity == conv.held_write and conv.guest_turns > conv.held_at_turn
        if not confirmed:
            conv.held_write, conv.held_at_turn = identity, conv.guest_turns
            held = {
                "ok": False,
                "error": "confirmation_required",
                "message": "Nothing has been changed yet. Read these details back to the guest and ask them "
                f"to confirm. If they agree, call {name} again with the same arguments.",
                "details": args,
            }
            return ToolStep(name, args, held, held_for_confirmation=True)
        conv.held_write = None

    result = await session.call_tool(name, args)
    text = result.content[0].text if result.content else "{}"
    if result.is_error:  # the SDK rejected the arguments against the schema
        return ToolStep(name, args, {"ok": False, "error": "invalid_arguments", "message": text[:500]})
    return ToolStep(name, args, json.loads(text))


async def run_turn(conv: Conversation, guest_text: str, model: ChatModel, session: Client) -> TurnResult:
    conv.guest_turns += 1
    conv.messages.append({"role": "user", "content": guest_text})
    tools = tool_schemas((await session.list_tools()).tools)
    known = {t["function"]["name"] for t in tools}
    turn = TurnResult(reply=FALLBACK_REPLY)
    nudge: list[dict] = []

    while turn.model_calls < MAX_STEPS:
        prompt = [{"role": "system", "content": build_system_prompt(now())}, *_recent(conv.messages), *nudge]
        turn.model_calls += 1
        try:
            reply: ModelReply = await model.complete(prompt, tools)
        except MalformedToolCall:
            log.warning("malformed tool call (attempt %d)", len(nudge) + 1)
            if nudge:  # already nudged once this turn
                break
            nudge = [{"role": "system", "content": MALFORMED_NUDGE}]
            continue
        nudge = []
        turn.input_tokens += reply.input_tokens
        turn.output_tokens += reply.output_tokens

        if not reply.tool_requests:
            turn.reply = (reply.content or "").strip() or FALLBACK_REPLY
            conv.messages.append({"role": "assistant", "content": turn.reply})
            return turn

        conv.messages.append(
            {
                "role": "assistant",
                "content": reply.content or "",
                "tool_calls": [
                    {"id": r.id, "type": "function", "function": {"name": r.name, "arguments": r.arguments}}
                    for r in reply.tool_requests
                ],
            }
        )
        for request in reply.tool_requests:
            try:
                args = json.loads(request.arguments or "{}")
                if not isinstance(args, dict):
                    raise ValueError("arguments must be a JSON object")
            except ValueError as e:
                step = ToolStep(request.name, {}, {"ok": False, "error": "bad_json", "message": str(e)})
            else:
                step = await _execute(session, conv, request.name, args, known)
            log.info("tool %s %s -> %s", step.name, step.arguments, step.result.get("error", "ok"))
            turn.steps.append(step)
            content = json.dumps(step.result, separators=(",", ":"))[:RESULT_CHARS]
            conv.messages.append({"role": "tool", "tool_call_id": request.id, "content": content})

    conv.messages.append({"role": "assistant", "content": turn.reply})
    return turn

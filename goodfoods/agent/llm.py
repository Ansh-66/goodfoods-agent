"""The model behind the agent: gpt-oss-20b on Groq, behind a one-method interface.

Groq retired llama-3.1-8b-instant (the brief's suggested size) on 16 Aug 2026 and
names gpt-oss-20b as its replacement: 21B parameters in total, about 3.6B active
per token (mixture of experts), so per-token compute stays in small-model range.

The loop only depends on `ChatModel.complete`, so tests and the eval can swap in
a scripted model, and changing provider means changing this file only.
"""

import os
from dataclasses import dataclass, field
from typing import Protocol

from groq import AsyncGroq, BadRequestError

DEFAULT_MODEL = "openai/gpt-oss-20b"
REASONING_EFFORT = "low"  # gpt-oss reasons before answering; low keeps tokens and latency down


@dataclass
class ToolRequest:
    id: str
    name: str
    arguments: str  # raw JSON text, exactly as the model produced it


@dataclass
class ModelReply:
    content: str | None
    tool_requests: list[ToolRequest] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class MalformedToolCall(Exception):
    """The model produced a tool call the provider could not parse."""


class ChatModel(Protocol):
    async def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply: ...


class GroqModel:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or os.environ.get("GOODFOODS_MODEL", DEFAULT_MODEL)
        # The SDK retries 429s itself and honours Groq's retry-after header,
        # which matters on the free tier's 8,000 tokens-per-minute limit.
        self.client = AsyncGroq(api_key=api_key or os.environ.get("GROQ_API_KEY"), max_retries=5)

    async def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply:
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0,
                max_tokens=1024,  # reasoning tokens count towards this
                **({"reasoning_effort": REASONING_EFFORT} if "gpt-oss" in self.model else {}),
            )
        except BadRequestError as e:
            if "tool_use_failed" in str(e):
                raise MalformedToolCall(str(e)) from e
            raise
        message = response.choices[0].message
        return ModelReply(
            content=message.content,
            tool_requests=[ToolRequest(c.id, c.function.name, c.function.arguments) for c in message.tool_calls or []],
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
        )

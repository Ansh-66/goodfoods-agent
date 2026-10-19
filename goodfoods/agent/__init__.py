"""The reservation agent: an MCP client loop around a small tool-calling model."""

import os
import sys
from contextlib import asynccontextmanager

from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters, get_default_environment

from ..config import ROOT
from .llm import ChatModel, GroqModel
from .loop import Conversation, ToolStep, TurnResult, run_turn

__all__ = ["ChatModel", "Conversation", "GroqModel", "ToolStep", "TurnResult", "mcp_session", "run_turn"]


@asynccontextmanager
async def mcp_session():
    """Start the MCP server as a subprocess and connect to it over stdio.

    The server gets only the default safe environment plus GOODFOODS_* settings:
    it has no reason to see the Groq key.
    """
    env = {**get_default_environment(), **{k: v for k, v in os.environ.items() if k.startswith("GOODFOODS_")}}
    params = StdioServerParameters(command=sys.executable, args=["-m", "goodfoods.server"], env=env, cwd=str(ROOT))
    async with Client(params) as client:
        yield client

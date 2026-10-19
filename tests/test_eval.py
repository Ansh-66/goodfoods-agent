"""The eval harness itself, driven by a scripted model, so a reported score can be trusted."""

import asyncio
import importlib.util
import json
from pathlib import Path

from goodfoods.agent.llm import ModelReply, ToolRequest

spec = importlib.util.spec_from_file_location("run_eval", Path(__file__).parents[1] / "evals" / "run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_eval)


class Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)

    async def complete(self, messages, tools):
        return self.replies.pop(0)


def lookup(args):
    return ModelReply(None, [ToolRequest("c1", "get_restaurant_details", json.dumps(args))])


def test_passing_case_scores_full_marks(conn):
    model = Scripted(lookup({"restaurant_id": "R004"}), ModelReply("It closes at 23:00."))
    summary = asyncio.run(run_eval.main(["--case", "outlet-info"], model=model))
    assert summary["case_success"] == 1 and summary["args_correct"] == summary["arg_checks"] == 1


def test_wrong_tool_and_wrong_argument_are_both_caught(conn):
    model = Scripted(lookup({"restaurant_id": "R010"}), ModelReply("It closes at 23:00."))
    summary = asyncio.run(run_eval.main(["--case", "outlet-info"], model=model))
    assert summary["tool_selection"] == 1 and summary["args_correct"] == 0

    model = Scripted(ModelReply("Here is a poem about cricket..."))
    summary = asyncio.run(run_eval.main(["--case", "lookup"], model=model))
    assert summary["tool_selection"] == 0 and summary["case_success"] == 0

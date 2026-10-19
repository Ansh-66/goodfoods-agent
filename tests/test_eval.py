"""The eval harness itself, driven by a scripted model, so a reported score can be trusted."""

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
from groq import RateLimitError

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


def test_refusing_an_in_scope_question_fails_the_case(conn):
    refusal = Scripted(ModelReply("I can only help with GoodFoods reservations."))
    assert asyncio.run(run_eval.main(["--case", "menu-not-in-data"], model=refusal))["case_success"] == 0
    honest = Scripted(ModelReply("I don't have the menu, but it serves Italian food for about 1,800 for two."))
    assert asyncio.run(run_eval.main(["--case", "menu-not-in-data"], model=honest))["case_success"] == 1


class QuotaRunsOut:
    """Answers in plain text, then hits the rate limit after `budget` calls (once only)."""

    def __init__(self, budget):
        self.budget = budget

    async def complete(self, messages, tools):
        if self.budget == 0:
            self.budget = None
            response = httpx.Response(429, request=httpx.Request("POST", "https://api.groq.com"))
            raise RateLimitError("tokens per day", response=response, body=None)
        if self.budget:
            self.budget -= 1
        return ModelReply("Sorry, which outlet?")


def test_run_stopped_by_the_rate_limit_resumes_where_it_left_off(conn, tmp_path):
    model = QuotaRunsOut(budget=3)
    assert asyncio.run(run_eval.main([], model=model, out_dir=tmp_path)) is None
    saved = json.loads((tmp_path / "progress.json").read_text())["results"]
    assert 0 < len(saved) < len(run_eval.CASES["cases"])

    summary = asyncio.run(run_eval.main([], model=model, out_dir=tmp_path))
    assert summary["cases"] == len(run_eval.CASES["cases"])
    assert (tmp_path / "results.md").exists() and not (tmp_path / "progress.json").exists()

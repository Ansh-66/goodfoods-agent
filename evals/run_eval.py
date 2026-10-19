"""Run the scripted guest journeys in evals/cases.json against the live agent.

Each case gets a fresh copy of a database seeded with "now" pinned (see
cases.json), so expected dates and outlets never move. The agent talks to the
real MCP server, in memory instead of over stdio, which is the same protocol
without a subprocess per turn.

Metrics:
- Tool selection: a turn is correct when every required tool was called and no
  tool outside required + allowed was called.
- Argument accuracy: for each tool with an argument spec, the first call to it
  in that turn carries every listed argument (strings compared case-insensitively
  as substrings, phone numbers by digits, numbers exactly).
- Case success: every turn's selection is correct and the final checks on the
  database and the last reply pass.

Resuming: a full run saves each finished case to evals/progress.json. If it
stops (for example on the free tier's daily token limit), running it again
skips the finished cases. Progress is only reused when the git commit, the
model and cases.json are all unchanged, so one report never mixes results
from two versions of the agent.

Run:  python evals/run_eval.py               (all cases, resumable; writes evals/results.md and .json)
      python evals/run_eval.py --fresh       (ignore saved progress)
      python evals/run_eval.py --case cancel  (one case, nothing saved)
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from groq import RateLimitError
from mcp.client.client import Client

HERE = Path(__file__).resolve().parent
CASES_TEXT = (HERE / "cases.json").read_text(encoding="utf-8")
CASES = json.loads(CASES_TEXT)
os.environ["GOODFOODS_NOW"] = CASES["now"]  # before goodfoods reads the clock

from goodfoods import reservations  # noqa: E402
from goodfoods.agent import ChatModel, Conversation, GroqModel, run_turn  # noqa: E402
from goodfoods.db import connect  # noqa: E402
from goodfoods.server import mcp  # noqa: E402
from goodfoods.setup_db import main as build_db  # noqa: E402


def arg_matches(expected, actual) -> bool:
    if actual is None:
        return False
    if isinstance(expected, int):
        try:
            return int(actual) == expected
        except (TypeError, ValueError):
            return False
    return str(expected).lower() in str(actual).lower()


def check_args(spec: dict, actual: dict) -> list[str]:
    """Names of the arguments in `spec` that `actual` gets wrong."""
    wrong = []
    for key, expected in spec.items():
        value = actual.get(key)
        if key.endswith("phone"):
            ok = value is not None and re.sub(r"\D", "", str(value)).endswith(str(expected))
        else:
            ok = arg_matches(expected, value)
        if not ok:
            wrong.append(f"{key}={value!r} (expected {expected!r})")
    return wrong


def score_turn(spec: dict, steps) -> dict:
    called = [s.name for s in steps]
    required, allowed = set(spec["required"]), set(spec["required"]) | set(spec.get("allowed", []))
    missing, unexpected = sorted(required - set(called)), sorted(set(called) - allowed)
    arg_results = {}
    for tool, arg_spec in spec.get("args", {}).items():
        first = next((s for s in steps if s.name == tool), None)
        arg_results[tool] = ["not called"] if first is None else check_args(arg_spec, first.arguments)
    return {
        "guest": spec["guest"],
        "called": called,
        "selection_ok": not missing and not unexpected,
        "missing": missing,
        "unexpected": unexpected,
        "args": {tool: not wrong for tool, wrong in arg_results.items()},
        "arg_errors": {tool: wrong for tool, wrong in arg_results.items() if wrong},
    }


def check_final(
    spec: dict, conn, seeded: int, last_seeded_row: int, reservation_id: str | None, reply: str
) -> list[str]:
    failures = []
    count = conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]
    handoffs = conn.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0]
    if spec.get("no_writes") and (count != seeded or handoffs):
        failures.append("expected no writes")
    if "handoffs" in spec and handoffs != spec["handoffs"]:
        failures.append(f"handoffs={handoffs}, expected {spec['handoffs']}")
    if "reservations_by" in spec:
        want = spec["reservations_by"]
        n = conn.execute(  # only bookings made during the case: seeded guests can share the name
            "SELECT COUNT(*) FROM reservations WHERE lower(guest_name) = lower(?) AND status = 'confirmed' "
            "AND rowid > ?",
            (want["guest_name"], last_seeded_row),
        ).fetchone()[0]
        if n != want["count"]:
            failures.append(f"{n} bookings for {want['guest_name']}, expected {want['count']}")
    if "reservation" in spec:
        row = reservations.get_reservation(conn, reservation_id)
        failures += [f"{k}={row[k]!r}, expected {v!r}" for k, v in spec["reservation"].items() if row[k] != v]
    if "reply_contains_any" in spec and not any(s.lower() in reply.lower() for s in spec["reply_contains_any"]):
        failures.append(f"last reply mentions none of {spec['reply_contains_any']}")
    if found := [s for s in spec.get("reply_excludes", []) if s.lower() in reply.lower()]:
        failures.append(f"last reply contains {found}")
    return failures


async def run_case(case: dict, model: ChatModel, seeded_db: Path, workdir: Path) -> dict:
    db = workdir / f"{case['id']}.db"
    shutil.copy(seeded_db, db)
    os.environ["GOODFOODS_DB"] = str(db)
    # closing() releases the file even when a rate-limit error ends the case early;
    # Windows cannot delete the temp folder while a connection holds it open.
    with closing(connect(db)) as conn:
        reservation_id = None
        if "setup" in case:
            s = case["setup"]
            reservation_id = reservations.make_reservation(
                conn, s["restaurant_id"], s["date"], s["time"], s["party_size"], s["guest_name"]
            )["reservation_id"]
        seeded = conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]
        last_seeded_row = conn.execute("SELECT MAX(rowid) FROM reservations").fetchone()[0]

        conv, turns, totals, reply = Conversation(), [], {"model_calls": 0, "tokens": 0, "seconds": 0.0}, ""
        async with Client(mcp) as session:
            for spec in case["turns"]:
                spec = json.loads(json.dumps(spec).replace("{reservation_id}", reservation_id or ""))
                started = time.perf_counter()
                turn = await run_turn(conv, spec["guest"], model, session)
                totals["seconds"] += time.perf_counter() - started
                totals["model_calls"] += turn.model_calls
                totals["tokens"] += turn.input_tokens + turn.output_tokens
                reply = turn.reply
                turns.append({**score_turn(spec, turn.steps), "reply": reply})

        failures = check_final(case.get("final", {}), conn, seeded, last_seeded_row, reservation_id, reply)
    return {
        "id": case["id"],
        "journey": case["journey"],
        "turns": turns,
        "final_failures": failures,
        "success": all(t["selection_ok"] for t in turns) and not failures,
        **totals,
    }


def summarise(results: list[dict]) -> dict:
    turns = [t for r in results for t in r["turns"]]
    arg_checks = [ok for t in turns for ok in t["args"].values()]
    return {
        "cases": len(results),
        "case_success": sum(r["success"] for r in results),
        "turns": len(turns),
        "tool_selection": sum(t["selection_ok"] for t in turns),
        "arg_checks": len(arg_checks),
        "args_correct": sum(arg_checks),
        "model_calls": sum(r["model_calls"] for r in results),
        "tokens": sum(r["tokens"] for r in results),
    }


def run_identity(model_name: str) -> dict:
    """What a set of results was produced by. Saved progress is reused only if this matches."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=HERE, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    return {"commit": commit, "model": model_name, "cases_sha": hashlib.sha256(CASES_TEXT.encode()).hexdigest()[:12]}


def rate_limited(exc: BaseException) -> bool:
    """True if `exc` is, or wraps, a Groq rate-limit error (the MCP client nests errors in ExceptionGroups)."""
    return isinstance(exc, RateLimitError) or any(rate_limited(e) for e in getattr(exc, "exceptions", ()))


def write_report(results: list[dict], summary: dict, identity: dict, out_dir: Path) -> None:
    pct = lambda a, b: f"{a}/{b} ({100 * a / b:.0f}%)"  # noqa: E731
    lines = [
        "# Evaluation results",
        "",
        f"Model: `{identity['model']}` on Groq, temperature 0. Commit `{identity['commit']}`. "
        f"Clock pinned to {CASES['now']}. Generated by `python evals/run_eval.py`; "
        "definitions in that file's docstring.",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Case success | {pct(summary['case_success'], summary['cases'])} |",
        f"| Tool selection (per turn) | {pct(summary['tool_selection'], summary['turns'])} |",
        f"| Argument accuracy (per checked call) | {pct(summary['args_correct'], summary['arg_checks'])} |",
        f"| Model calls / tokens | {summary['model_calls']} / {summary['tokens']:,} |",
        "",
        "| Case | Journey | Result | Model calls | Tokens |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['id']} | {r['journey']} | {'pass' if r['success'] else 'FAIL'} | "
            f"{r['model_calls']} | {r['tokens']:,} |"
        )
    failed = [r for r in results if not r["success"] or any(t["arg_errors"] for t in r["turns"])]
    if failed:
        lines += ["", "## Failures", ""]
    for r in failed:
        lines.append(f"**{r['id']}**")
        for t in r["turns"]:
            problems = [f"missing {', '.join(t['missing'])}"] if t["missing"] else []
            problems += [f"unexpected {', '.join(t['unexpected'])}"] if t["unexpected"] else []
            problems += [f"{tool}: {'; '.join(errs)}" for tool, errs in t["arg_errors"].items()]
            if problems:
                lines.append(f'- "{t["guest"]}" called {t["called"] or "nothing"}: {" | ".join(problems)}')
        lines += [f"- final: {f}" for f in r["final_failures"]]
        lines.append("")
    (out_dir / "results.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    report = {"identity": identity, "summary": summary, "cases": results}
    (out_dir / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def load_progress(path: Path, identity: dict, fresh: bool) -> list[dict]:
    if fresh or not path.exists():
        return []
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved["identity"] != identity:
        print(f"Saved progress is from {saved['identity']}, not {identity}: starting fresh.")
        return []
    print(f"Resuming: {len(saved['results'])} case(s) already done.")
    return saved["results"]


async def main(argv: list[str] | None = None, model: ChatModel | None = None, out_dir: Path = HERE) -> dict | None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", help="run one case by id (nothing saved)")
    parser.add_argument("--fresh", action="store_true", help="ignore saved progress")
    args = parser.parse_args(argv)
    load_dotenv()
    model = model or GroqModel()
    identity = run_identity(getattr(model, "model", type(model).__name__))
    cases = [c for c in CASES["cases"] if args.case in (None, c["id"])]
    if not cases:
        sys.exit(f"No case '{args.case}'.")

    progress_file = out_dir / "progress.json"
    full_run = args.case is None
    results = load_progress(progress_file, identity, args.fresh) if full_run else []
    done = {r["id"] for r in results}

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        os.environ["GOODFOODS_DB"] = str(workdir / "seeded.db")
        build_db()
        for case in cases:
            if case["id"] in done:
                continue
            try:
                result = await run_case(case, model, workdir / "seeded.db", workdir)
            except Exception as exc:
                if not rate_limited(exc):
                    raise
                print(
                    f"Stopped at '{case['id']}': Groq rate limit. {len(results)}/{len(cases)} cases saved; "
                    "run the same command again once the quota resets (daily limits reset at 00:00 UTC)."
                )
                return None
            print(f"{'pass' if result['success'] else 'FAIL'}  {case['id']}", flush=True)
            results.append(result)
            if full_run:
                saved = {
                    "identity": identity,
                    "updated": datetime.now().isoformat(timespec="seconds"),
                    "results": results,
                }
                progress_file.write_text(json.dumps(saved, indent=2), encoding="utf-8")

    summary = summarise(results)
    print(json.dumps(summary, indent=2))
    if full_run:
        write_report(results, summary, identity, out_dir)
        progress_file.unlink(missing_ok=True)
    return summary


if __name__ == "__main__":
    asyncio.run(main())

"""The Requirements probe's requests and its mechanical grader (#3630 steps 3 and 4).

Nine requests a person might open a task with, from "hi" to an exact change, each with the
behaviour a good Requirements answer shows, fixed in advance. They come from the 2026-11-15
comparison of Opus 5.5, GPT-6.1 Sol, GLM-5.3 and MiMo, rewritten against the small project bundled
in ``fixtures/requirements_probe`` so any account can run them offline: the original R4 and R6 named
a pull request on GitHub, and R8 and R9 named this repository's code.

``grade`` reads one Requirements report (the ``requirements_gather`` answer) and what the probe
measured about it, and returns which checks passed. No model judges another: every check reads a
schema field, a count or a time. A check that needs the person's words is written as a pattern
over the report's own text, and its name says what it looks for.

The quick pass (``QUICK``) is the three requests that separated the models most on 2026-11-15; a
model runs the full nine only after it passes them (#3630 step 3).

Pure: no model call, no file access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# About 90 seconds per reply for Requirements (#3630 step 4): the person is waiting at the chat.
LATENCY_LIMIT_SECONDS = 90
BUILD_JOBS = frozenset({"build", "bugfix"})
ANY_JOB = frozenset({"build", "bugfix", "review", "design", "discuss", "none"})


@dataclass(frozen=True)
class Request:
    id: str
    text: str
    # The jobs a good answer may name.
    jobs: frozenset
    # True: it must ask the person something. False: it must not. None: either is fine.
    asks: bool | None
    # The most tool calls a good answer spends (None: no limit). "hi" needs no repository look.
    max_tool_calls: int | None = None
    # A build or bug fix with no question must hand off behaviours and acceptance tests.
    needs_handoff: bool = False
    why: str = ""


REQUESTS = (
    Request("R1", "hi", frozenset({"none"}), True, 0, why="a greeting: ask what they want, read nothing"),
    Request("R2", "make it better", frozenset({"none", "build"}), True, 3, why="ask what 'it' and 'better' mean"),
    Request(
        "R3",
        "the report command feels slow, can you look at it?",
        frozenset({"none", "bugfix", "build", "discuss"}),
        True,
        why="ask for a concrete symptom; a short look at the code is fine",
    ),
    Request(
        "R4",
        "fix the failing test",
        BUILD_JOBS,
        False,
        needs_handoff=True,
        why="the failing test is in the project: find it, do not ask which one",
    ),
    Request(
        "R5",
        "why does the cache expire entries after an hour?",
        frozenset({"discuss"}),
        False,
        why="a question about the code: read it and answer",
    ),
    Request("R6", "review the change to totals.py", frozenset({"review"}), False, why="a review, nothing to ask"),
    Request(
        "R7",
        "make the report faster but do not change report.py",
        ANY_JOB,
        True,
        why="the report lives in report.py: point out the conflict and ask what may change",
    ),
    Request(
        "R8",
        "add a --json option to the report command that prints the totals as one JSON object",
        frozenset({"build"}),
        False,
        needs_handoff=True,
        why="an exact change: hand off behaviours and tests, no question",
    ),
    Request(
        "R9",
        "average([]) in totals.py raises ZeroDivisionError; it should return 0.0",
        BUILD_JOBS,
        False,
        needs_handoff=True,
        why="an exact bug fix: hand off, no question",
    ),
)
BY_ID = {request.id: request for request in REQUESTS}
QUICK = ("R1", "R2", "R8")

# A closing confirmation that stops a clear request until the person answers it (seen on GPT-6.1
# Sol and Opus at high, 2026-11-15). Matched in the reply and the open questions.
CONFIRMATION = re.compile(
    r"\b(is (that|this) (correct|right)|does (that|this) (sound|look) (right|good|correct)|"
    r"(can|could) you confirm|please confirm|shall i (proceed|go ahead)|should i (proceed|go ahead))\b",
    re.IGNORECASE,
)
_SPACE = re.compile(r"\s+")

CHECKS = (
    "job",  # the job is one the request allows
    "asks",  # asked when it should, and not when it should not
    "tool_calls",  # read no more than the request needs
    "handoff",  # a clear build names behaviours and acceptance tests
    "clarity",  # not marked clear while it asks the person what outcome they want
    "reply_matches_question",  # the reply asks the same question the open questions list
    "no_blocking_confirmation",  # a clear request is not stopped by "Is that correct?"
    "latency",  # answered within LATENCY_LIMIT_SECONDS
)
# The checks a request passes or fails on; latency ranks models instead (#3630 step 4).
QUALITY_CHECKS = tuple(check for check in CHECKS if check != "latency")


def grade(request, report, *, seconds=None, tool_calls=None, tokens=None, cost_usd=None):
    """``{"request", "checks": {name: bool}, "passed": bool, "failed": [names], measurements}``.

    ``report`` is the decoded Requirements answer; anything that is not a dict fails every check
    but latency. ``passed`` means every quality check passed.
    """
    if isinstance(request, str):
        request = BY_ID[request]
    report = report if isinstance(report, dict) else {}
    questions = _questions(report)
    asked = bool(questions)
    clarity = report.get("clarity")
    reply = _text(report.get("assistant_reply"))
    checks = {
        "job": report.get("job") in request.jobs,
        "asks": request.asks is None or asked == request.asks,
        "tool_calls": request.max_tool_calls is None
        or (type(tool_calls) is int and tool_calls <= request.max_tool_calls),
        "handoff": not request.needs_handoff
        or asked
        or (bool(report.get("required_behaviors")) and bool(report.get("acceptance_tests"))),
        "clarity": not (asked and clarity == "clear" and _asks_outcome(report)),
        "reply_matches_question": (not asked or _reply_asks(reply, questions)),
        "no_blocking_confirmation": not (
            request.asks is False and any(CONFIRMATION.search(text) for text in [reply, *questions])
        ),
        "latency": isinstance(seconds, (int, float)) and seconds <= LATENCY_LIMIT_SECONDS,
    }
    if not report:
        # No answer to read is no answer at all, not an answer that happens to ask nothing.
        checks = {name: (checks[name] if name == "latency" else False) for name in CHECKS}
    failed = [name for name in QUALITY_CHECKS if not checks[name]]
    return {
        "request": request.id,
        "checks": checks,
        "passed": not failed,
        "failed": failed,
        "asked": asked,
        "seconds": seconds,
        "tool_calls": tool_calls,
        "tokens": tokens,
        "cost_usd": cost_usd,
    }


def summary(grades):
    """What a set of graded requests adds up to: passes, total, mean seconds and total cost."""
    grades = list(grades)
    seconds = [g["seconds"] for g in grades if isinstance(g.get("seconds"), (int, float))]
    costs = [g["cost_usd"] for g in grades if isinstance(g.get("cost_usd"), (int, float))]
    return {
        "passed": sum(1 for g in grades if g.get("passed")),
        "total": len(grades),
        "requests": sorted(g["request"] for g in grades),
        "mean_seconds": round(sum(seconds) / len(seconds), 1) if seconds else None,
        "cost_usd": round(sum(costs), 4) if len(costs) == len(grades) and grades else None,
    }


def passes_quick(grades):
    """True when every quick-pass request was graded and passed."""
    passed = {g["request"] for g in grades if g.get("passed")}
    return all(request in passed for request in QUICK)


def _text(value):
    return _SPACE.sub(" ", value).strip() if isinstance(value, str) else ""


def _questions(report):
    questions = report.get("open_questions")
    if not isinstance(questions, list):
        return []
    return [_text(q.get("question")) for q in questions if isinstance(q, dict) and _text(q.get("question"))]


def _asks_outcome(report):
    """An open question about what the person wants, by the report's own category."""
    return any(
        isinstance(q, dict) and q.get("category") == "requested_outcome" for q in report.get("open_questions") or ()
    )


def _reply_asks(reply, questions):
    """The reply carries one of the open questions word for word (spacing and case aside)."""
    folded = reply.casefold()
    return any(question.casefold() in folded for question in questions)

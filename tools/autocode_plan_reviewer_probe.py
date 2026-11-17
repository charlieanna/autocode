"""The Plan Reviewer probe's planted flaws and its mechanical grader (#3630, the Plan Reviewer stage).

The probe measures a Plan Reviewer on plans with a known answer. For each request of the Planner
probe (``autocode_planner_probe``) a Planner the person names writes a plan, which a run brings to
approval; the candidate then reviews that plan as it stands (sound), and the same plan with one
flaw planted in it. A good reviewer approves the first and blocks the second. The method is the
2026-11-16 comparison of GLM-5.3, MiMo v2.6 Pro, GPT-6.1 Sol and Opus 5.5 as Plan Reviewer, where
every reviewer caught every flaw and they differed in false blocks, time and cost.

The flaws are planted by structure, never by rewriting the Planner's words, so they fit whatever
plan the Planner writes: a file the request forbids added to every milestone, the files a fix
belongs in replaced by the test file, a criterion's test replaced by reading the code, a sentence
of approach that contradicts the request. A plan's ``sources`` and quotes are never touched: a
plan whose quotes changed is refused when it is loaded.

``grade`` reads the two verdicts the run shows (``view.plan_review``, ``autocode_plan_review_view``):

- both reviews were accepted, with no report repair;
- the sound plan was approved;
- the flawed plan was blocked;
- each review finished within ``TIME_LIMIT_SECONDS``.

Whether a blocking concern names the planted flaw is recorded (``names_flaw``, a loose pattern
over the concern's words) but does not decide a pass: on 2026-11-16 every block on a flawed plan
named its flaw when read, and a pattern over a model's wording is a guess.

Pure: no model call, no file access.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

try:
    from . import autocode_planner_probe as planner_probe
    from . import autocode_requirements_probe as requirements_probe
except ImportError:
    import autocode_planner_probe as planner_probe
    import autocode_requirements_probe as requirements_probe

# One review, Requirements and the Planner already done. The slowest reviewer on 2026-11-16 took
# about four minutes; a review past this keeps a person waiting at every plan.
TIME_LIMIT_SECONDS = 600
BLOCKED = ("changes_requested", "unresolved")
# What a run records about where a plan came from; a file holding them is refused when loaded, and
# the run records them again for the plan it loads.
RUNNER_RECORDS = ("brief_acceptance", "risk_acceptance", "source_trace")
UNTESTED = "The Validator reads the changed code and confirms it does this; no test is run."


@dataclass(frozen=True)
class Flaw:
    request: str
    kind: str
    # Sentences added to the plan's technical approach, or replacing it when ``replace_approach``.
    approach: tuple = ()
    replace_approach: bool = False
    # The files every milestone (and the first task) then changes, replacing its own; None keeps them.
    paths: tuple | None = None
    # Files added to every milestone's (and the first task's) files.
    add_paths: tuple = ()
    # The first criterion is verified by reading the code instead of a test.
    untested: bool = False
    # A loose pattern a blocking concern about this flaw matches (recorded, not graded).
    names: str = ""
    why: str = ""


FLAWS = (
    Flaw(
        "P1",
        "weakens_test",
        approach=(
            "Leave totals.py unchanged.",
            "Change the failing test test_total_with_tax_adds_the_tax to expect the value total_with_tax returns now.",
        ),
        replace_approach=True,
        paths=("test_totals.py",),
        names=r"test|expect|weak|tax|totals\.py",
        why="the fix changes the test to match the bug",
    ),
    Flaw(
        "P2",
        "untested_criterion",
        untested=True,
        names=r"verif|test|inspect|read",
        why="a criterion no test checks",
    ),
    Flaw(
        "P3",
        "wrong_result",
        approach=("Make average([]) return None rather than 0.0.",),
        names=r"None|0\.0",
        why="the approach returns what the request rules out",
    ),
    Flaw(
        "P4",
        "shortens_lifetime",
        approach=(
            "Lower cache.TTL_SECONDS from 3600 to 5 so a changed orders file is picked up within five seconds.",
            "Leave the cache lookup and report.py unchanged.",
        ),
        replace_approach=True,
        paths=("cache.py",),
        names=r"TTL|expir|second|stale|chang",
        why="a shorter lifetime still serves a stale result",
    ),
    Flaw(
        "P5",
        "edits_forbidden_file",
        approach=("Add the empty-list regression test to the existing tests in test_totals.py.",),
        add_paths=("test_totals.py",),
        names=r"test_totals\.py",
        why="the request forbids editing test_totals.py",
    ),
    Flaw(
        "P6",
        "partial_coverage",
        approach=("Only the total line gets the currency symbol; the average and with-tax lines stay plain numbers.",),
        names=r"average|with.tax|every|all",
        why="the request asks for every printed amount",
    ),
    Flaw(
        "P7",
        "keeps_crash",
        approach=(
            "A malformed line still raises ValueError and stops the report; only comments and blank lines are skipped.",
        ),
        names=r"malformed|crash|raise|line number",
        why="the request asks for malformed lines to be reported, not to crash",
    ),
)
BY_REQUEST = {flaw.request: flaw for flaw in FLAWS}
REQUESTS = tuple(planner_probe.BY_ID[flaw.request] for flaw in FLAWS)
BY_ID = {request.id: request for request in REQUESTS}
# The two requests that separated the reviewers on 2026-11-16: a design one sound plan could be
# falsely blocked on, and a constraint the flawed plan breaks.
QUICK = ("P4", "P5")
CHECKS = ("reviews_accepted", "approves_sound", "blocks_flawed", "time")


def plant(contract, flaw) -> dict:
    """``contract`` (a plan file's ``contract``) with ``flaw`` planted and the runner's records left out."""
    flaw = BY_REQUEST[flaw] if isinstance(flaw, str) else flaw
    planted = sound(contract)
    approach = [line for line in planted.get("technical_approach") or () if isinstance(line, str)]
    planted["technical_approach"] = list(flaw.approach) if flaw.replace_approach else approach + list(flaw.approach)
    holders = [row for row in planted.get("milestones") or () if isinstance(row, dict)]
    if isinstance(planted.get("initial_task"), dict):
        holders.append(planted["initial_task"])
    for holder in holders:
        paths = list(flaw.paths) if flaw.paths is not None else list(holder.get("affected_paths") or ())
        holder["affected_paths"] = paths + [path for path in flaw.add_paths if path not in paths]
    if flaw.untested:
        criteria = [row for row in planted.get("acceptance_criteria") or () if isinstance(row, dict)]
        if not criteria:
            raise ValueError("the plan has no criterion to leave untested")
        criteria[0]["verification_method"] = UNTESTED
    return planted


def sound(contract) -> dict:
    """``contract`` as it stands, without the runner's records, ready to load again."""
    if not isinstance(contract, dict):
        raise ValueError("a plan file's contract must be an object")
    return {key: copy.deepcopy(value) for key, value in contract.items() if key not in RUNNER_RECORDS}


def grade(request, sound_review, flawed_review, *, seconds=(), cost_usd=None):
    """``{"request", "checks", "passed", "failed", ...}`` for one request's two reviews.

    ``sound_review`` and ``flawed_review`` are the run's ``plan_review`` after each, or None when
    the review gave no verdict of its own (it stopped, or needed a repair). ``seconds`` holds each
    review's wall time.
    """
    if isinstance(request, str):
        request = BY_ID[request]
    flaw = BY_REQUEST[request.id]
    fresh = [review for review in (sound_review, flawed_review) if _fresh(review)]
    times = [value for value in seconds if isinstance(value, (int, float)) and not isinstance(value, bool)]
    checks = {
        "reviews_accepted": len(fresh) == 2,
        "approves_sound": _fresh(sound_review) and sound_review["verdict"] == "approved",
        "blocks_flawed": _fresh(flawed_review) and flawed_review["verdict"] in BLOCKED,
        "time": len(times) == 2 and max(times) <= TIME_LIMIT_SECONDS,
    }
    failed = [name for name in CHECKS if not checks[name]]
    return {
        "request": request.id,
        "flaw": flaw.kind,
        "checks": checks,
        "passed": not failed,
        "failed": failed,
        "sound_verdict": (sound_review or {}).get("verdict"),
        "flawed_verdict": (flawed_review or {}).get("verdict"),
        "false_block": [] if checks["approves_sound"] else list((sound_review or {}).get("blocking") or ()),
        "names_flaw": names_flaw(flaw, flawed_review),
        "seconds": round(sum(times) / len(times), 1) if times else None,
        "cost_usd": cost_usd,
    }


def names_flaw(flaw, review) -> bool:
    """Whether a blocking concern of ``review`` matches the flaw's loose pattern."""
    if not _fresh(review) or not flaw.names:
        return False
    pattern = re.compile(flaw.names, re.IGNORECASE)
    return any(
        row.get("blocking") and pattern.search(f"{row.get('concern') or ''} {row.get('requested_change') or ''}")
        for row in review.get("concerns") or ()
        if isinstance(row, dict)
    )


def summary(grades):
    """The same summary the other probes give, so one ranking reads them all."""
    return requirements_probe.summary(grades)


def passes_quick(grades):
    passed = {g["request"] for g in grades if g.get("passed")}
    return all(request in passed for request in QUICK)


def _fresh(review) -> bool:
    return isinstance(review, dict) and not review.get("from_earlier_plan") and isinstance(review.get("verdict"), str)

"""The Planner probe's requests and its mechanical grader (#3630, the Planner stage).

Seven requests on the bundled project (``fixtures/requirements_probe``), each with the files a good
plan must and must not change, fixed in advance. They come from the 2026-11-16 comparison of
GPT-6.1 Sol, Opus 5.5, GLM-5.3 and MiMo v2.6 Pro as Planner. P1 runs on the project as bundled,
whose tax bug makes one test fail. Every other request runs on a copy with that bug fixed
(``FIXED_TAX``): there, a plan that keeps "the existing tests pass" is true, and a Planner that
stops to ask about a failure the request never mentioned is not being tested on the request.

``grade`` reads the plan the run shows at approval (``view.displayed_plan``) and what the probe
measured about the run: the Plan Reviewer's rounds and the Planner's report repairs. No model judges
another, and every check reads a field, a count or a time:

- the run reached plan approval, within ``TIME_LIMIT_SECONDS``;
- the Planner's first report was accepted, with no repair or rejected attempt;
- the Plan Reviewer approved within ``MAX_REVIEW_ROUNDS``;
- the plan changes every file the request needs and none it forbids (the deliverables and the
  milestones' ``affected_paths``);
- every criterion is verified by a test: a named ``test:`` or ``guard:``, or a command that runs the
  project's tests, since a project whose runner reports only an exit code is proven by a command
  guard (AGENTS.md); a criterion that a file stays unchanged needs none;
- a small fix gets a small plan (``SMALL_PLAN_MILESTONES``).

Whether the plan's approach is right (P1 adds the tax, P4 notices a changed file instead of
shortening the cache's lifetime) needs reading; the Plan Reviewer's verdict stands in for it here.

Pure: no model call, no file access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

try:
    from . import autocode_requirements_probe as requirements_probe
except ImportError:
    import autocode_requirements_probe as requirements_probe

# A Planner run includes Requirements and the review rounds; the slowest model measured on
# 2026-11-16 took up to 35 minutes, so a run past this is a failure, not a slow pass.
TIME_LIMIT_SECONDS = 1800
MAX_REVIEW_ROUNDS = 2
SMALL_PLAN_MILESTONES = 2
# The bundled project's tax bug, and its fix for every request but P1.
FIXED_TAX = ("return total(amounts) * TAX_RATE", "return total(amounts) * (1 + TAX_RATE)")


@dataclass(frozen=True)
class Request:
    id: str
    text: str
    must_change: frozenset = field(default_factory=frozenset)
    # One of these must change (P4: the cache or the report that reads it).
    one_of: frozenset = field(default_factory=frozenset)
    must_not_change: frozenset = field(default_factory=frozenset)
    small: bool = False
    # True: the request runs on the project as bundled, with the tax bug.
    broken_tax: bool = False
    why: str = ""


REQUESTS = (
    Request(
        "P1",
        "fix the failing test",
        frozenset({"totals.py"}),
        small=True,
        broken_tax=True,
        why="the bug is in total_with_tax, not in the test",
    ),
    Request(
        "P2",
        "add a --json option to the report command that prints the totals as one JSON object",
        frozenset({"report.py"}),
        why="an exact feature in one command",
    ),
    Request(
        "P3",
        "average([]) in totals.py raises ZeroDivisionError; it should return 0.0",
        frozenset({"totals.py"}),
        small=True,
        why="an exact bug fix",
    ),
    Request(
        "P4",
        "make the report command pick up changes to the orders file instead of serving a cached result "
        "for up to an hour",
        one_of=frozenset({"cache.py", "report.py"}),
        why="a design choice: notice the change, not a shorter lifetime",
    ),
    Request(
        "P5",
        "make average([]) return 0.0, and do not edit test_totals.py",
        frozenset({"totals.py"}),
        must_not_change=frozenset({"test_totals.py"}),
        small=True,
        why="a constraint the plan must keep: the new test goes in another file",
    ),
    Request(
        "P6",
        "add a --currency option that formats every printed amount, with a test for each command",
        frozenset({"report.py"}),
        why="several outputs to cover, not just the total",
    ),
    Request(
        "P7",
        "orders files may contain comments (#) and blank lines; malformed lines should be reported with "
        "their line number, not crash",
        frozenset({"report.py"}),
        why="edge cases the criteria must name",
    ),
)
BY_ID = {request.id: request for request in REQUESTS}
# The two requests that separated the settings on 2026-11-16 (a feature and a constraint).
QUICK = ("P2", "P5")

CHECKS = (
    "reached_approval",
    "first_report_accepted",
    "reviewer_approved",
    "changes_needed_files",
    "keeps_forbidden_files",
    "every_criterion_tested",
    "small_plan",
    "time",
)
_PY_FILE = re.compile(r"[\w./-]*\w\.py\b")
_TESTED = re.compile(r"\s*(?:test|guard)\b", re.IGNORECASE)
_TEST_COMMAND = re.compile(r"\b(?:unittest|pytest)\b")
_UNCHANGED = re.compile(r"unchanged|byte-for-byte|same contents|not (?:be )?(?:edited|modified|changed)", re.IGNORECASE)
_SECTION = re.compile(r"^## (.+)$", re.MULTILINE)


def grade(request, plan, *, reached_approval, review_rounds=None, report_repairs=None, seconds=None, cost_usd=None):
    """``{"request", "checks": {name: bool}, "passed", "failed": [names], measurements}``.

    ``plan`` is the run's ``displayed_plan``, or None when it showed none. ``passed`` means every
    check passed, time included: a Planner run that does not finish is no plan for the person.
    """
    if isinstance(request, str):
        request = BY_ID[request]
    plan = plan if isinstance(plan, dict) else {}
    changed = changed_files(plan)
    criteria = [row for row in plan.get("acceptance_criteria") or () if isinstance(row, dict)]
    milestones = [row for row in plan.get("milestones") or () if isinstance(row, dict)]
    checks = {
        "reached_approval": bool(reached_approval and plan),
        "first_report_accepted": report_repairs == 0,
        "reviewer_approved": bool(reached_approval)
        and type(review_rounds) is int
        and 1 <= review_rounds <= MAX_REVIEW_ROUNDS,
        "changes_needed_files": request.must_change <= changed
        and (not request.one_of or bool(request.one_of & changed)),
        "keeps_forbidden_files": not (request.must_not_change & changed),
        "every_criterion_tested": bool(criteria) and not untested(criteria),
        "small_plan": not request.small or len(milestones) <= SMALL_PLAN_MILESTONES,
        "time": isinstance(seconds, (int, float)) and seconds <= TIME_LIMIT_SECONDS,
    }
    if not plan:
        checks = {name: False for name in CHECKS}
    failed = [name for name in CHECKS if not checks[name]]
    return {
        "request": request.id,
        "checks": checks,
        "passed": not failed,
        "failed": failed,
        "changed_files": sorted(changed),
        "untested": untested(criteria),
        "review_rounds": review_rounds,
        "report_repairs": report_repairs,
        "seconds": seconds,
        "cost_usd": cost_usd,
    }


def changed_files(plan) -> set:
    """The file names the plan changes: its deliverables and its milestones' ``affected_paths``."""
    names = set(_PY_FILE.findall(_section(plan.get("review_markdown"), "Deliverables")))
    for milestone in plan.get("milestones") or ():
        if isinstance(milestone, dict):
            names.update(path for path in milestone.get("affected_paths") or () if isinstance(path, str))
    return {PurePosixPath(name).name for name in names if name}


def untested(criteria) -> list:
    """The ids of criteria no test verifies, leaving out a file that must stay unchanged."""
    result = []
    for row in criteria:
        method = row.get("verification_method") if isinstance(row.get("verification_method"), str) else ""
        if _TESTED.match(method) or _TEST_COMMAND.search(method):
            continue
        if _UNCHANGED.search(str(row.get("criterion") or "")):
            continue
        result.append(row.get("id"))
    return result


def summary(grades):
    """The same summary the Requirements probe gives, so one ranking reads both."""
    return requirements_probe.summary(grades)


def passes_quick(grades):
    passed = {g["request"] for g in grades if g.get("passed")}
    return all(request in passed for request in QUICK)


def _section(markdown, name):
    if not isinstance(markdown, str):
        return ""
    headings = list(_SECTION.finditer(markdown))
    for index, heading in enumerate(headings):
        if heading.group(1).strip().lower() == name.lower():
            end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
            return markdown[heading.end() : end]
    return ""

"""Tests described in plain English, and the runner's link from each one to the test that proves it.

Two places write them. A bug fix's Investigator writes ``test_cases`` (id,
given, when, then) in its diagnosis (autocode_bug_job). A small feature's
Planner writes acceptance criteria as concrete examples and marks the ones a
test proves with a verification_method of ``test: test_<id>_...``
(``contract_cases``). Either way a person approves English, the Builder writes
one test per case named after its id, and the runner's proof
(autocode_regression) checks by name, with no model, that each case has a
test that passes with the change and did not before (``match_cases``).

Pure functions over saved state. Imports nothing from the runner.
"""
from __future__ import annotations

import re

MARK = "test:"
# Planner-written cases only in a one-milestone plan: a later milestone's tests cannot
# pass at an earlier milestone's checkpoint, so larger plans keep ordinary criteria.
MAX_MILESTONES = 1


def contract_cases(state: dict) -> list[dict]:
    """The approved plan's criteria marked ``test:``, as cases (id, text), in a one-milestone plan."""
    body = (state.get("goal_contract") or {}).get("body") or {}
    if len(body.get("milestones") or []) > MAX_MILESTONES:
        return []
    return [{"id": row["id"], "text": row.get("criterion", "")} for row in body.get("acceptance_criteria") or []
            if isinstance(row, dict) and row.get("id")
            and str(row.get("verification_method", "")).strip().lower().startswith(MARK)]


def case_text(case: dict) -> str:
    if "text" in case:
        return f"{case['id']}: {case['text']}"
    return f"{case['id']}: Given {case['given']}; when {case['when']}; then {case['then']}"


def case_test_name(case_id: str) -> str:
    return f"test_{case_id.lower()}_<what it checks>"


def _words(name: str) -> list[str]:
    """Lowercase words of an identifier: test_t1_x, TestT1X and test-t1-x all give test, t1, x."""
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [word for word in re.split(r"[^a-z0-9]+", name.lower()) if word]


def _test_function(test_id: str) -> str:
    """The test's own name inside a runner's id (module.Class.test_x, path::Class::test_x[param], ...)."""
    names = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", re.sub(r"\[.*\]$", "", test_id))
    tests = [name for name in names if name.lower().startswith("test")]
    return tests[-1] if tests else (names[-1] if names else "")


def match_cases(cases: list[dict], test_ids: list[str]) -> dict[str, list[str]]:
    """For each case, the tests whose name carries its id as whole words (T1 -> test_t1_...)."""
    matched = {}
    for case in cases:
        want = _words(case["id"])
        matched[case["id"]] = [test for test in test_ids
                               if any(_words(_test_function(test))[i:i + len(want)] == want
                                      for i in range(len(_words(_test_function(test)))))]
    return matched


BUILDER_NOTE = """
TESTS NAMED IN THE PLAN: every acceptance criterion whose verification_method starts with "test:" is a
concrete example you must write as its own test, named with that criterion's id (C2 -> test_c2_<what it
checks>) and asserting exactly the criterion's example. Before completion the runner runs these tests itself:
each must pass with your change and must not have passed without it. Criteria without "test:" are checked
by the Validator as usual.
"""


def builder_note(state: dict) -> str:
    return BUILDER_NOTE if contract_cases(state) else ""

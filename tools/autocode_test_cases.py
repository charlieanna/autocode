"""Tests described in plain English, and the runner's link from each one to the test that proves it.

Two places write them. A bug fix's Investigator writes ``test_cases`` (id,
given, when, then) in its diagnosis (autocode_bug_job). A small feature's
Planner writes acceptance criteria as concrete examples and marks the ones a
test proves with a verification_method of ``test: test_<id>_...``, or of
``guard: test_<id>_...`` for behavior that already works and must keep working
(``contract_cases``; a guard is a preserve case, as in a bug diagnosis). Either way a person approves English, the Builder writes
one test per case named after its id, and the runner's proof
(autocode_regression) checks by name, with no model, that each case has a
test that passes with the change and did not before (``match_cases``).

In a plan with several milestones, a case is due once its milestone is: the
proof at a milestone checkpoint covers the current milestone (every member of a
parallel batch) and the milestones already accepted, never a later one
(``in_scope``). Criteria that belong to no milestone are due once every
milestone is, that is at final completion.

Pure functions over saved state. Imports nothing from the runner; milestone
progress is read from ``milestone_progress`` as autocode_milestones saves it.
"""
from __future__ import annotations

import re

try:
    from . import autocode_progressive_state as progressive_state
except ImportError:
    import autocode_progressive_state as progressive_state

MARK = "test:"
# Behavior that already works and must keep working: its test passes before and after the change
# (a "preserve" case, autocode_regression.check_cases). Live review-then-fix plans (2026-09-29) had
# only "test:" for "a timeout before execution is still retried", which the reviewed change never
# broke, so the proof could not pass it: one run asked the user, one was sent back to rework.
GUARD_MARK = "guard:"


def proof(method) -> str:
    """A verification method with a test:/guard: mark reduced to the test it names.

    Whether a named test must fail first (test:) or pass before and after (guard:) is how the
    runner proves a criterion, not what the user agreed to. A planning revision may switch it
    without a user answer (autocode_goals.revision_guard); the proof still refuses a mislabeled
    one. A live review-then-fix plan (2026-09-29) could not move "a timeout before execution is
    still retried" from test: to guard:, as its Plan Reviewer asked, without asking the user.
    """
    text = str(method or "").strip()
    for mark in (MARK, GUARD_MARK):
        if text.lower().startswith(mark):
            return "tested: " + text[len(mark):].strip()
    return text


def design_only(state: dict) -> bool:
    """A job recognized as design (autocode_workflows) delivers documents, never code or tests. A live run
    asked to "deliver only a design ... no application code" planned every criterion as a test and added
    tests/ (architecture-two-services, 2026-09-29); its criteria are checked by the Validator instead."""
    return (state.get("workflow") or {}).get("kind") == "design"


def contract_cases(state: dict) -> list[dict]:
    """The approved plan's criteria marked ``test:`` or ``guard:`` that are due now, as cases (id, text, and
    kind "preserve" for a guard). None in a design-only job: nothing there is proven by a test the Builder writes."""
    if design_only(state):
        return []
    body = (state.get("goal_contract") or {}).get("body") or {}
    due = in_scope(state)
    cases = []
    for row in body.get("acceptance_criteria") or []:
        method = str(row.get("verification_method", "")).strip() if isinstance(row, dict) else ""
        lowered = method.lower()
        if row.get("id") and (due is None or row["id"] in due) and lowered.startswith((MARK, GUARD_MARK)):
            mark = GUARD_MARK if lowered.startswith(GUARD_MARK) else MARK
            test_name = method[len(mark):].strip()
            cases.append({"id": row["id"], "text": row.get("criterion", ""),
                          **({"test_name": test_name} if re.fullmatch(r"test_[A-Za-z0-9_]+", test_name) else {}),
                          **({"kind": "preserve"} if mark == GUARD_MARK else {})})
    return cases


def in_scope(state: dict) -> set[str] | None:
    """Criterion ids due at this point of a multi-milestone plan, or None when all are due.

    Due: the criteria of the current task's milestone (or batch members) and of milestones
    already accepted under this contract. All are due in a one-milestone plan, when the
    current task names no milestone, and once every milestone is current or accepted.
    """
    contract = state.get("goal_contract") or {}
    progressive = progressive_state.context(state)
    if progressive:
        # Contribution checks are due, but do not prove the broader product case.
        # Findings stay in the product ledger; this only selects contract tests.
        criteria = (contract.get("body") or {}).get("acceptance_criteria") or []
        outstanding = set(progressive.get("outstanding_criteria") or [])
        fully_due = {criterion for check in progressive.get("required_checks") or []
                     if check.get("relation") == "fully_verify" for criterion in check.get("criterion_ids") or []}
        return {row["id"] for row in criteria if row.get("id") not in outstanding or row.get("id") in fully_due}
    milestones = (contract.get("body") or {}).get("milestones") or []
    task = state.get("current_task") or {}
    current = set(task.get("milestone_ids") or []) or ({task["milestone_id"]} if task.get("milestone_id") else set())
    if len(milestones) <= 1 or not current:
        return None
    accepted = {mid for row in (state.get("milestone_progress") or {}).values()
                if row.get("accepted") and row.get("contract_hash") == contract.get("hash")
                for mid in row.get("milestone_ids") or [row.get("id")]}
    reached = current | accepted
    if {milestone.get("id") for milestone in milestones} <= reached:
        return None
    return {criterion for milestone in milestones if milestone.get("id") in reached
            for criterion in milestone.get("acceptance_criteria") or []}


def case_text(case: dict) -> str:
    if "text" in case:
        return f"{case['id']}: {case['text']}"
    return f"{case['id']}: Given {case['given']}; when {case['when']}; then {case['then']}"


def case_test_name(case_id: str, test_name: str | None = None) -> str:
    return test_name or f"test_{case_id.lower()}_<what it checks>"


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
    """Match an approved exact test name, or a diagnosis case's id-based test name."""
    matched = {}
    for case in cases:
        if case.get("test_name"):
            matched[case["id"]] = [test for test in test_ids
                                   if _test_function(test) == case["test_name"]]
        else:
            # The documented lowercase spelling keeps M1A as m1a. Retain the
            # CamelCase spelling too, without accepting prefixes of either form.
            wants = (_words(case["id"]), _words(case["id"].lower()))
            matched[case["id"]] = [test for test in test_ids
                                   if any(_words(_test_function(test))[i:i + len(want)] == want
                                          for want in wants
                                          for i in range(len(_words(_test_function(test)))))]
    return matched


def run_probes(rows: list[dict], run_probe, *, what: str = "claim", key: str = "claim") -> list[dict]:
    """Run every row's ``probe`` (``run_probe(command)``, a scratch run); each must exit 0.

    A probed row needs its ``example`` in plain English. Returns the receipts of the rows shown;
    raises ValueError naming the rows whose probe did not exit 0. ``what`` names the rows in
    messages (claim, concern, conflict) and ``key`` is the field that identifies a row.
    """
    shown, failed = [], []
    for row in rows:
        probe = str(row.get("probe") or "").strip()
        if not probe:
            continue
        if not str(row.get("example") or "").strip():
            raise ValueError(f"A probed {what} needs its example in plain English: {row.get(key)!r}")
        run = run_probe(probe)
        receipt = {key: row.get(key), "probe": probe, "exit_code": run.get("exit_code"),
                   "tail": (run.get("tail") or run.get("error") or "")[-600:]}
        (shown if run.get("exit_code") == 0 and not run.get("error") else failed).append(receipt)
    if failed:
        raise ValueError(f"These {what}s' probes did not exit 0 on the code as it is, so they are not shown: "
                         + "; ".join(f"{row[key]!r} ({row['probe']}: exit {row['exit_code']}) {row['tail'][-200:]}"
                                     for row in failed))
    return shown


NAMED_PROOF_NOTE = """
NAMED TEST PROOF: the runner can attribute cases with Python unittest/pytest, Go tests, or Node's built-in
node:test. In Node projects register each named case with node:test, for example
`const {test} = require('node:test'); test('test_c2_example', async () => { /* existing assertions */ });`,
and run `node --test tests/example.cjs`. Keep fixture helpers and assertions; await every async check.
Custom scripts printing PASS labels, or npm/Jest/Vitest/Mocha summaries, do not supply named proof.
A node:test case must assert the behavior itself, never spawn another test runner (npm/pnpm/yarn test,
npx vitest, jest, mocha or node --test through child_process): its pass would be that runner's exit code,
which is 0 even when a -t filter matches no test, so the runner refuses such a file as named proof.
Running the product's own CLI from a node:test case is fine.
Keep the existing project suite and protected tests intact. Add supported named tests within the approved
test paths; plan any needed test paths before approval. Do not replace test:/guard: criteria with prose to
avoid proof. If no supported runner fits the project, raise the compatibility blocker before approval.
"""

BUILDER_NOTE = """
TESTS NAMED IN THE PLAN: every acceptance criterion of your milestone whose verification_method starts with
"test:" is a concrete example you must write as its own test, named with that criterion's id (C2 ->
test_c2_<what it checks>) and asserting exactly the criterion's example. Before the Validator runs, the runner
runs these tests itself, with those of milestones already accepted: each must pass with the change and must
not have passed before the run began. A criterion whose verification_method starts with "guard:" is behavior
that already works and must keep working: write its test the same way (C4 -> test_c4_...); it must pass both
before and after the change, so put it where it imports only code that exists before the change. Criteria without "test:" or "guard:" are checked by the Validator as usual.
Keep existing test names and assertions intact. Add a new case test when needed; do not rename or remove an
existing test to make its name match a planned case id. The regression proof rejects removed test names.
""" + NAMED_PROOF_NOTE


# Issue #299: a seam the fix adds cannot compile on the unfixed code, and a log line the fix adds proves nothing.
BUGFIX_TEST_NOTE = """
BUG FIX TESTS: each regression test must build and run on the unfixed code. A test of behavior the fix
restores must fail there because of the bug; a guard: (preserve) test must pass there and after the fix.
Do not make a test import or reference anything the fix adds (a new function, package variable, hook or
injectable seam): on the unfixed code such a test only fails to compile or import, which is not a
reproduction, and adding the seam with the fix does not change that. Drive the real failure path through
public APIs that exist before the fix (for example a real file, directory or input that makes the failing
operation fail) and assert the behavior itself: the returned error, the result, the saved state. A log line
or message alone does not prove the behavior.
"""


def bugfix(state: dict) -> bool:
    """The approved contract is a bug fix, whose tests must run and fail on the unfixed code (autocode_regression)."""
    return ((state.get("goal_contract") or {}).get("body") or {}).get("task_kind") == "bugfix"


def builder_note(state: dict) -> str:
    fix_note = BUGFIX_TEST_NOTE if bugfix(state) else ""
    if contract_cases(state):
        return BUILDER_NOTE + fix_note
    return (NAMED_PROOF_NOTE if (state.get("investigation") or {}).get("test_cases") else "") + fix_note

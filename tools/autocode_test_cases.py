"""Tests described in plain English, and the runner's link from each one to the test that proves it.

Two places write them. A bug fix's Investigator writes ``test_cases`` (id,
given, when, then) in its diagnosis (autocode_bug_job). A small feature's
Planner writes acceptance criteria as concrete examples and marks the ones a
test proves with a verification_method of ``test: test_<id>_...``, or of
``guard: test_<id>_...`` for behavior that already works and must keep working
(``contract_cases``; a guard is a preserve case, as in a bug diagnosis), or of the
Go test name the user asked for (autocode_native_test_names). Either way a person approves English, the Builder writes
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
from typing import Any

# A verification method may name the test and then say how to run it ("test: TestFoo (go test ...)").
# Go subtests keep hyphens and dots (Run_-_creates_..., Package.Case_Test). Stop before a parenthetical command.
_NAMED_TEST = re.compile(r"(test_[A-Za-z0-9_]+|Test[A-Za-z0-9_]+(?:/[A-Za-z0-9_.-]+)*)")


def _named_test(rest: str) -> str:
    match = _NAMED_TEST.match(str(rest or "").strip())
    return match.group(1) if match else ""


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
    found = mark(text)
    return "tested: " + text[len(found) :].strip() if found else text


def mark(method) -> str | None:
    """MARK or GUARD_MARK when a verification method names a test the runner proves, else None."""
    lowered = str(method or "").strip().lower()
    return GUARD_MARK if lowered.startswith(GUARD_MARK) else MARK if lowered.startswith(MARK) else None


def design_only(state: dict) -> bool:
    """A job recognized as design (autocode_workflows) delivers documents, never code or tests. A live run
    asked to "deliver only a design ... no application code" planned every criterion as a test and added
    tests/ (architecture-two-services, 2026-09-29); its criteria are checked by the Validator instead."""
    return (state.get("workflow") or {}).get("kind") == "design"


def declared_test_name(text: str) -> str | None:
    """Keep a declared identifier when an annotation follows a clear separator.

    The annotation explains the test; it must not silently replace its identity
    with the criterion ID. Placeholders and free-form methods keep the existing
    ID-based convention. Native Go names are valid explicit identifiers too.
    """
    match = re.fullmatch(
        r"(test_[A-Za-z0-9_]+|Test[A-Z0-9_][A-Za-z0-9_]*(?:/[A-Za-z0-9_.-]+)*)"
        r"(?:\s+(?:[—–-]\s+.+|\(.+\)))?",
        text,
        re.DOTALL,
    )
    return match[1] if match else None


def contract_cases(state: dict, *, all_due: bool = False) -> list[dict]:
    """The approved plan's criteria marked ``test:`` or ``guard:`` that are due now (every one with ``all_due``,
    as at final completion), as ``plan_cases``. None in a design-only job: nothing there is proven by a test the
    Builder writes."""
    if design_only(state):
        return []
    due = None if all_due else in_scope(state)
    return [
        case for case in plan_cases((state.get("goal_contract") or {}).get("body")) if due is None or case["id"] in due
    ]


def plan_cases(body) -> list[dict]:
    """A plan body's criteria marked ``test:`` or ``guard:``, as cases: id, text, the test it names when that
    is a test name, and kind "preserve" for a guard."""
    cases = []
    for row in (body.get("acceptance_criteria") if isinstance(body, dict) else None) or []:
        found = mark(row.get("verification_method")) if isinstance(row, dict) else None
        if found and row.get("id"):
            test_name = declared_test_name(str(row["verification_method"]).strip()[len(found) :].strip())
            cases.append(
                {
                    "id": row["id"],
                    "text": row.get("criterion", ""),
                    **({"test_name": test_name} if test_name else {}),
                    **({"kind": "preserve"} if found == GUARD_MARK else {}),
                }
            )
    return cases


def diagnosis_cases(state: dict) -> list[dict]:
    """A reproduced bug's English test cases (autocode_bug_job), or [] (bugs planned without an
    investigation, older runs)."""
    found = state.get("investigation") or {}
    return list(found.get("test_cases") or []) if found.get("outcome") == "reproduced" else []


def proof_cases(state: dict, *, all_due: bool = False) -> list[dict]:
    """The cases the runner's regression proof (autocode_regression) requires a test for: a reproduced bug's
    diagnosis, else the plan's ``contract_cases``. The plan approval summary (autocode_approval_view) states
    the same cases with ``all_due``. Explicit approved test names can scope diagnosis cases to a checkpoint;
    incomplete mappings preserve the full requirement."""
    cases = diagnosis_cases(state)
    if not cases:
        return contract_cases(state, all_due=all_due)
    due = None if all_due else in_scope(state)
    if due is None or not due:
        return cases
    # Only the approved, explicit test names establish a case's milestone.
    # Legacy, incomplete or ambiguous mappings keep the whole proof required.
    bindings = {case["id"]: set() for case in cases}
    for criterion in contract_cases(state, all_due=True):
        name = criterion.get("test_name")
        if not name:
            continue
        matched = match_cases(cases, [name], framework="go")
        owners = [case for case in cases if matched[case["id"]]]
        if len(owners) != 1:
            if owners:
                return cases
            continue
        case = owners[0]
        if (case.get("kind") == "preserve") != (criterion.get("kind") == "preserve"):
            return cases
        bindings[case["id"]].add(criterion["id"])
    if any(not owners for owners in bindings.values()):
        return cases
    return [case for case in cases if bindings[case["id"]] & due]


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
        fully_due = {
            criterion
            for check in progressive.get("required_checks") or []
            if check.get("relation") == "fully_verify"
            for criterion in check.get("criterion_ids") or []
        }
        return {row["id"] for row in criteria if row.get("id") not in outstanding or row.get("id") in fully_due}
    milestones = (contract.get("body") or {}).get("milestones") or []
    task = state.get("current_task") or {}
    current = set(task.get("milestone_ids") or []) or ({task["milestone_id"]} if task.get("milestone_id") else set())
    if len(milestones) <= 1 or not current:
        return None
    accepted = {
        mid
        for row in (state.get("milestone_progress") or {}).values()
        if row.get("accepted") and row.get("contract_hash") == contract.get("hash")
        for mid in row.get("milestone_ids") or [row.get("id")]
    }
    reached = current | accepted
    if {milestone.get("id") for milestone in milestones} <= reached:
        return None
    return {
        criterion
        for milestone in milestones
        if milestone.get("id") in reached
        for criterion in milestone.get("acceptance_criteria") or []
    }


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


def _go_words(name: str) -> list[str]:
    """Go aliases retain whole words and numeric groups across case separators.

    To30 and to_30 are the same alias; To300 and version21 versus version2_1
    remain different. Other runners keep their exact identifier comparison.
    """
    return [part for word in _words(name) for part in re.findall(r"[a-z]+|[0-9]+", word)]


def _test_function(test_id: str) -> str:
    """The test's own name inside a runner's id (module.Class.test_x, path::Class::test_x[param], ...)."""
    names = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", re.sub(r"\[.*\]$", "", test_id))
    tests = [name for name in names if name.lower().startswith("test")]
    return tests[-1] if tests else (names[-1] if names else "")


def function_name(test_id: str) -> str:
    """The test's own function name inside a runner's id."""
    return _test_function(test_id)


def named_in(test_id: str, text: str) -> bool:
    """Whether ``text`` (a file's source) names the test's own function."""
    name = _test_function(test_id)
    return bool(name) and re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text) is not None


def match_cases(cases: list[dict], test_ids: list[str], *, framework=None) -> dict[str, list[str]]:
    """Match an approved exact test name, or a diagnosis case's id-based test name."""
    matched = {}
    for case in cases:
        if case.get("test_name"):
            matched[case["id"]] = [
                test
                for test in test_ids
                if (
                    _test_function(test) == case["test_name"]
                    or test.rsplit("::", 1)[-1] == case["test_name"]
                    or (
                        framework == "go"
                        and case["test_name"].startswith("test_")
                        and re.match(r"^Test[A-Z0-9_]", _test_function(test))
                        and _go_words(_test_function(test)) == _go_words(case["test_name"])
                    )
                )
            ]
        else:
            # The documented lowercase spelling keeps M1A as m1a. Retain the
            # CamelCase spelling too, without accepting prefixes of either form.
            wants = (_words(case["id"]), _words(case["id"].lower()))
            matched[case["id"]] = [
                test
                for test in test_ids
                if any(
                    _words(_test_function(test))[i : i + len(want)] == want
                    for want in wants
                    for i in range(len(_words(_test_function(test))))
                )
            ]
    return matched


def run_probes(rows: list[dict], run_probe, *, what: str = "claim", key: str = "claim") -> list[dict]:
    """Run every row's ``probe`` (``run_probe(command)``, a scratch run); each must exit 0.

    A probed row needs its ``example`` in plain English. Returns the receipts of the rows shown;
    raises ValueError naming the rows whose probe did not exit 0. ``what`` names the rows in
    messages (claim, concern, conflict) and ``key`` is the field that identifies a row.
    """
    shown: list[Any] = []
    failed: list[Any] = []
    for row in rows:
        probe = str(row.get("probe") or "").strip()
        if not probe:
            continue
        if not str(row.get("example") or "").strip():
            raise ValueError(f"A probed {what} needs its example in plain English: {row.get(key)!r}")
        run = run_probe(probe)
        receipt = {
            key: row.get(key),
            "probe": probe,
            "exit_code": run.get("exit_code"),
            "tail": (run.get("tail") or run.get("error") or "")[-600:],
        }
        (shown if run.get("exit_code") == 0 and not run.get("error") else failed).append(receipt)
    if failed:
        raise ValueError(
            f"These {what}s' probes did not exit 0 on the code as it is, so they are not shown: "
            + "; ".join(
                f"{row[key]!r} ({row['probe']}: exit {row['exit_code']}) {row['tail'][-200:]}" for row in failed
            )
        )
    return shown


NAMED_PROOF_NOTE = """
NAMED TEST PROOF: the runner attributes cases with Python unittest/pytest, Go tests, Node's built-in
node:test, and native Vitest 4. The identifier immediately after test:/guard: names the required test;
an explanation after it does not define an alias. Preserve an explicitly requested supported native name.
For example, Go TestCacheExpiry uses "test: TestCacheExpiry", and TestCacheRetainsFresh uses
"guard: TestCacheRetainsFresh". Preserve case and the complete native subtest path. Do not substitute
"test_ac1_cache_expiry" and claim in prose that it resolves to TestCacheExpiry: those names do
not match. During plan review, check the declared proof identifiers against the required native test
names; block a mismatch instead of accepting an explanatory alias. The criterion ID remains unchanged. In a Vitest project keep cases in Vitest and run
`npx --no-install vitest run <test files>` or an npm test script that is a single `vitest run` command.
The runner owns the reporter and checks actual named outcomes; missing, skipped and ambiguous cases
never pass. Do not create node:test wrappers just to relabel existing Vitest cases.
For node:test register each case with `test('test_c2_example', async () => { /* assertions */ });`
and run `node --test tests/example.cjs`. Keep fixture helpers and assertions; await every async check.
Custom scripts printing PASS labels, or ordinary npm/Jest/Mocha summaries, do not supply named proof.
A node:test case must assert the behavior itself, never spawn another test runner (npm/pnpm/yarn test,
npx vitest, jest, mocha or node --test through child_process): its pass would be that runner's exit code,
which is 0 even when a -t filter matches no test, so the runner refuses such a file as named proof.
Running the product's own CLI from a node:test case is fine.
Keep the existing project suite and protected tests intact. Add supported named tests within the approved
test paths; plan any needed test paths before approval. Do not replace test:/guard: criteria with prose to
avoid proof. If no supported runner fits the project, raise the compatibility blocker before approval.
"""

BUILDER_NOTE = (
    """
TESTS NAMED IN THE PLAN: every acceptance criterion of your milestone whose verification_method starts with
"test:" is a concrete example you must write as its own test, using the supported test identifier declared
immediately after the marker and asserting exactly the criterion's example. Preserve an explicitly requested
native name exactly (test: TestFixedReturnsTwo -> func TestFixedReturnsTwo). In Go, a criterion-ID identifier
such as test_ac1_x maps to native TestAc1X by the runner's documented whole-word and numeric-group alias
rules; do not define a lowercase Go test function.
If no name is declared, use that criterion's id (C2 -> test_c2_<what it checks>). Before the Validator runs, the runner
runs these tests itself, with those of milestones already accepted: each must pass with the change and must
not have passed before the run began. A criterion whose verification_method starts with "guard:" is behavior
that already works and must keep working: preserve its declared test name too (otherwise C4 -> test_c4_...);
it must pass both
before and after the change, so put it where it imports only code that exists before the change. Criteria without "test:" or "guard:" are checked by the Validator as usual.
Keep existing test names and assertions intact. Add a new case test when needed; do not rename or remove an
existing test to make its name match a planned case id. The regression proof rejects removed test names.
"""
    + NAMED_PROOF_NOTE
)


# A live Arena bug fix (Boltons #474, 2026-10-05) used the plan's AC ids for its
# tests, while the proof still required the Investigator's T ids. Give the
# Builder the same cases the runner proves, including each case's before-state.
DIAGNOSIS_BUILDER_NOTE = """
TESTS NAMED IN THE DIAGNOSIS: write one separate test for each Investigator case below, asserting its
exact given, when and then. Use each diagnosis case's own id in the test name, even when the plan uses
different acceptance criterion ids or describes verification in prose. The runner proves these diagnosis
cases before the Validator runs; tests named only after the plan's criteria cannot satisfy them.
Keep existing test names and assertions intact. Add new case tests; do not rename or remove existing tests.
"""


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
    diagnosis = diagnosis_cases(state)
    if diagnosis:
        rows = []
        for case in diagnosis:
            before = (
                "preserve: must pass on the original code and with the fix"
                if case.get("kind") == "preserve"
                else "restore: must fail on the original code because of the bug and pass with the fix"
            )
            rows.append(f"- {case_text(case)}; test name: {case_test_name(case['id'])}; {before}.")
        return DIAGNOSIS_BUILDER_NOTE + "\n".join(rows) + "\n" + NAMED_PROOF_NOTE + fix_note
    if contract_cases(state):
        return BUILDER_NOTE + fix_note
    return fix_note

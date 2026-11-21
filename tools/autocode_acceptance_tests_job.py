"""The Tester writes the approved acceptance tests before the Builder starts (spec-first runs, #1200).

autocode_acceptance_tests routes a build here when its due ``test:``/``guard:`` cases have no tests yet.
The Tester, on the Validator's model with a session of its own, writes one test per case, named as the
case says, and changes only test files and validated Go loading metadata. The runner then:

- rejects non-test changes except additive already-selected Go prerequisites (the stray-write rule),
- runs the reported test files on the code as it is (a scratch copy) and requires, per case, that a
  ``test:`` case's test does not pass there (a failure, or a file that cannot load because the product
  lacks what it imports, counts when the file defines the named test) and that a ``guard:`` case's
  test passes; anything else rejects the report, so the Tester's retry gets the runner's evidence,
- archives the files and records the receipt, and returns the run to the build it was routed from.

A passing ``test:`` case on a retained candidate is checked on the original reference before any
report repair or plan correction. A fresh original failure keeps its approved meaning; a fresh
original pass goes back to plan review once, relabelled ``guard:`` in a runner-built draft (ORIGIN).
The mirror holds for a ``guard:`` case: when the runner's rewrites are spent and its test still runs and fails
on an assertion, today and on the original reference, the plan, not the test, is what disagrees with the code,
so it goes back to plan review once relabelled ``test:`` in the same kind of draft.
Missing original evidence holds verification. The final proof is always still required.
That holds for a bug fix too: its plan can add ``test:`` cases of
its own beside the Investigator's reproduction (autocode_acceptance_tests._cases), and one of those that
already works is the same disagreement as in a build (#1868). A second disagreement on that case, a case no
test can show (``cannot_write``) and a bug's reproduction that already passes pause the run
(PAUSED_ACCEPTANCE_TESTS): only the user (``--feedback``) can say which is right.

A fault in the tests themselves (a guard that does not pass on today's code, often because its file cannot
load there, or a test the runner's command did not run) is not a question for the user: the runner sends the
tests back to the Tester with its evidence (``runner_feedback``), up to MAX_REWRITES times per plan and batch.
When those are spent, or the same cases come back skipped after the Tester was told they skip, the attempt is
rejected, so the Investigator takes it before any person is asked (#3261).

Pure: prompt, schema and the transition; the unit passes in the function that runs the tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_evidence_repair as evidence_repair
    from . import autocode_acceptance_test_repair as test_repair
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_acceptance_evidence_repair as evidence_repair
    import autocode_acceptance_test_repair as test_repair
    import autocode_prompts as prompts

import copy
import json
import re
import shutil
from pathlib import Path

try:
    from . import autocode_acceptance_provenance as provenance
    from . import autocode_acceptance_reference as reference
    from . import autocode_acceptance_tests as acceptance
    from . import autocode_acceptance_verification_retry as verification_retry
    from . import autocode_check_outcome as check_outcome
    from . import autocode_go_test_prerequisites as go_prerequisites
    from . import autocode_handoff_dedup as handoff
    from . import autocode_project_kinds as project_kinds
    from . import autocode_run_status as run_status
    from . import autocode_stray_writes as stray_writes
    from . import autocode_test_cases as test_cases
    from . import autocode_unreached_skip as unreached_skip
    from . import autocode_util as util
    from . import autocode_wrapped_runner as wrapped_runner
    from .autocode_report_repair_queue import ExecutionEvidenceRequired
    from .autocode_verify import is_test_path, run_command
except ImportError:
    import autocode_acceptance_provenance as provenance
    import autocode_acceptance_reference as reference
    import autocode_acceptance_tests as acceptance
    import autocode_acceptance_verification_retry as verification_retry
    import autocode_check_outcome as check_outcome
    import autocode_go_test_prerequisites as go_prerequisites
    import autocode_handoff_dedup as handoff
    import autocode_project_kinds as project_kinds
    import autocode_run_status as run_status
    import autocode_stray_writes as stray_writes
    import autocode_test_cases as test_cases
    import autocode_unreached_skip as unreached_skip
    import autocode_util as util
    import autocode_wrapped_runner as wrapped_runner
    from autocode_report_repair_queue import ExecutionEvidenceRequired
    from autocode_verify import is_test_path, run_command

STAGE = acceptance.STAGE
PAUSE = "PAUSED_ACCEPTANCE_TESTS"
# The runner-built draft that relabels cases the runner saw already pass (autocode_goal_lifecycle.UNAUTHORED_DRAFTS).
ORIGIN = "acceptance_tests_correction"
S = {"type": "string"}


def _rows(properties: dict) -> dict:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": list(properties),
            "properties": properties,
        },
    }


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "tests", "already_passing", "cannot_write", "tests_run"],
    "properties": {
        "summary": S,
        # One row per due case: the test that proves it and the file it lives in.
        "tests": _rows({"case_id": S, "test_name": S, "file": S}),
        # A test: case whose behavior the code already has, with what showed it.
        "already_passing": _rows({"case_id": S, "evidence": S}),
        # A case no automated test can show, and why.
        "cannot_write": _rows({"case_id": S, "reason": S}),
        "tests_run": {"type": "array", "items": S},
    },
}
PROMPT = prompts.get("acceptance-tests.md")
REPAIR_RULES = prompts.get("fragments/acceptance-tests/repair-rules.md")
# Fresh Tester attempts the runner grants itself, per plan and batch of cases, when it ran the Tester's tests
# and saw a fault in the tests themselves. Only once they are spent does the run stop.
MAX_REWRITES = 2


class TestsNeedRewriting(ExecutionEvidenceRequired):
    """The runner ran the Tester's tests on today's code and the fault is in the tests: a guard that does not
    pass there, or a test the command did not run. The Tester can fix that, so the run gives it a fresh
    attempt with this evidence instead of asking the person (live run, 2026-11-11: a test module that imported
    the new function at its top could not load on today's code, so the guard in it never ran, and the run
    asked the person how to fix the tests).

    ``skipped`` names the cases whose tests the command collected and then skipped (#3261). ``failing_guards``
    (case id -> {"name", "tests"}) is set only when every fault is a guard whose test ran and failed on its own
    assertion: once the rewrites are spent, those may be cases the plan labelled wrongly (_guards_to_tests)."""

    def __init__(self, message: str, *, skipped=(), failing_guards=None, original_reference=None):
        super().__init__(message)
        self.skipped = sorted(skipped)
        self.failing_guards = dict(failing_guards or {})
        self.original_reference = original_reference


class ReferenceEvidenceRequired(ExecutionEvidenceRequired):
    """The original-reference execution is unresolved, not a new person decision."""


class RunnerDidNotStart(ExecutionEvidenceRequired):
    """The acceptance test command could not start (autocode_check_outcome RUNNER_NOT_STARTED, #3877): no test
    ran, so the written tests were not judged. The finished writer and its tests are kept, and a resume re-runs
    the checks without another writer or planner call. ``run`` is the failed command's receipt."""

    def __init__(self, message: str, run: dict):
        super().__init__(message)
        self.run = run


def _case(case: dict, inherited=False) -> dict:
    return {
        "id": case["id"],
        "criterion": test_cases.case_text(case),
        "test_name": case.get("test_name") or test_cases.case_test_name(case["id"]),
        "kind": (
            "test: already delivered with authenticated fail-first provenance; retain its existing test, which may pass today; "
            "fresh original-reference fail-first proof remains required"
            if inherited
            else "guard: must pass on today's code"
            if case.get("kind") == "preserve"
            else "test: must fail today"
        ),
    }


def packet(state: RunState, inventory: dict | None = None, engine: str | None = None) -> dict:
    inherited = provenance.inherited_cases(state, workspace=state.get("workspace"))
    return {
        "stage": STAGE,
        "task": state["task"],
        "workspace": state.get("workspace"),
        "execution_engine": engine,
        "cases": [_case(case, case["id"] in inherited) for case in acceptance.job_cases(state)],
        "goal_contract": state.get("goal_contract"),
        "current_task": state.get("current_task"),
        "saved_answers": state.get("answers") or {},
        "workspace_inventory": inventory or {},
        # Why the runner rejected this plan's earlier attempts at these cases, oldest first.
        "runner_feedback": [row["feedback"] for row in _rewrites(state)],
        "evidence_repair": evidence_repair.packet(state),
        "test_repair": test_repair.packet(state, receipts_map=acceptance.receipts(state)),
    }


def _rewrites(state: RunState) -> list[dict]:
    """The fresh attempts the runner granted itself for the current plan and batch of cases."""
    record = state.get(acceptance.KEY) or {}
    contract = (state.get("goal_contract") or {}).get("hash")
    cases = sorted(record.get("pending_case_ids") or [])
    return [
        row
        for row in record.get("rewrites") or []
        if row.get("contract_hash") == contract and sorted(row.get("case_ids") or []) == cases
    ]


def _rewrite(state: RunState, error: TestsNeedRewriting, record: dict) -> bool:
    """Send the tests back to the Tester with the runner's evidence; False once MAX_REWRITES are spent.

    Also False when the same cases come back skipped after the Tester was told they skip (#3261): an
    identical round proves nothing more on this host, so the Investigator takes it instead."""
    earlier = _rewrites(state)
    skipped = error.skipped
    if len(earlier) >= MAX_REWRITES or (skipped and earlier and skipped == earlier[-1].get("skipped")):
        return False
    saved = state.setdefault(acceptance.KEY, {"receipts": {}})
    saved.setdefault("rewrites", []).append(
        {
            "contract_hash": (state.get("goal_contract") or {}).get("hash"),
            "case_ids": sorted(saved.get("pending_case_ids") or []),
            "feedback": str(error),
            "output": record.get("output"),
            "skipped": skipped,
            **({"original_reference": error.original_reference} if error.original_reference else {}),
        }
    )
    run_status.running(state, "EXECUTING", next_stage=STAGE)
    return True


def prompt(
    state: RunState, inventory: dict | None = None, soft_budget_tokens: int = 10000, engine: str | None = None
) -> tuple[str, dict]:
    text = (
        PROMPT
        + "\nCURRENT HANDOFF DATA\n"
        + json.dumps(handoff.deduplicate(packet(state, inventory, engine)), indent=2)
    )
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def repair_context(state: RunState) -> dict:
    """A report-only repair sees the due cases, so it can move one to already_passing (jobs.repair_context)."""
    return {"cases": packet(state)["cases"]}


def owns(state: RunState) -> bool:
    return False  # the run continues to the Builder; it never ends here


def render(state: RunState) -> str:
    return ""


def _check_rows(value: dict, cases: list[dict], *, shared_names=()) -> None:
    due = {case["id"] for case in cases}
    named = [row["case_id"] for key in ("tests", "cannot_write") for row in value[key]]
    unknown = sorted((set(named) | {row["case_id"] for row in value["already_passing"]}) - due)
    if unknown:
        raise ValueError(f"Report only the due cases {sorted(due)}; unknown: {unknown}")
    if len(named) != len(set(named)) or set(named) != due:
        raise ValueError(f"Account for every due case exactly once, in tests or cannot_write: {sorted(due)}")
    untested = sorted({row["case_id"] for row in value["already_passing"]} - {row["case_id"] for row in value["tests"]})
    if untested:
        raise ValueError(
            f"A case reported in already_passing still needs its test in tests, so the runner can see it pass: {untested}"
        )
    split = [
        name for name in shared_names if len({row["file"] for row in value["tests"] if row["test_name"] == name}) > 1
    ]
    if split:
        raise ValueError(f"Shared case bindings must report the same function and file, not copies: {sorted(split)}")


DISAGREE = "The Tester found the approved plan and the code disagree, before any building: "
CANNOT_TEST = acceptance.CANNOT_TEST_PREFIX
DISAGREE_END = ". --feedback revises the plan."


def _pause(state: RunState, rows: list[str]) -> None:
    run_status.pause(state, PAUSE, DISAGREE + "; ".join(rows) + DISAGREE_END, next_stage=STAGE)


def _pause_cannot_write(state: RunState, rows: list[str]) -> None:
    run_status.pause(state, PAUSE, CANNOT_TEST + "; ".join(rows) + DISAGREE_END, next_stage=STAGE)


def finding_kind(reason) -> str:
    """Whether ``reason`` is a plan-vs-code disagreement (``disagree``) or host limitation (``cannot_write``)."""
    head = str(reason or "").split("\nInvestigator ", 1)[0]
    if head.startswith(CANNOT_TEST) and head.endswith(DISAGREE_END):
        return "cannot_write"
    if head.startswith(DISAGREE) and head.endswith(DISAGREE_END):
        return "disagree"
    return ""


def finding(reason) -> str:
    """What the Tester found at its pre-build stop, from the reason _pause/_pause_cannot_write saved; "" otherwise.

    The chat states it, so the person answering the stop knows which case stopped and why (live run,
    2026-11-11: the chat said only that the Tester stopped and to inspect a reason it never showed).
    Strips any trailing Investigator annotation before extracting the finding (#3350)."""
    head = str(reason or "").split("\nInvestigator ", 1)[0]
    for prefix in (CANNOT_TEST, DISAGREE):
        if head.startswith(prefix) and head.endswith(DISAGREE_END):
            return head[len(prefix) : -len(DISAGREE_END)]
    return ""


def investigator_note(reason) -> str:
    """Any Investigator diagnosis appended to ``reason`` when ``cannot_write`` held after investigation."""
    parts = str(reason or "").split("\nInvestigator ", 1)
    if len(parts) < 2 or ":" not in parts[1]:
        return ""
    return parts[1].split(":", 1)[1].strip()


def reproduction_ids(state: RunState) -> set[str]:
    """The ids of the Investigator's reproduction cases: a reproduced bug's ``restore`` cases, not its guards.

    They live in the diagnosis, not the contract, so the runner cannot relabel one; a plan's own cases
    never share their ids (autocode_acceptance_tests._cases)."""
    return {case["id"] for case in test_cases.diagnosis_cases(state) if case.get("kind", "restore") != "preserve"}


def corrected_body(state: RunState, names: dict[str, str], *, to: str = test_cases.GUARD_MARK) -> dict:
    """The approved plan with each case in ``names`` (id -> test name) relabelled to ``to``, from the other mark."""
    source = test_cases.MARK if to == test_cases.GUARD_MARK else test_cases.GUARD_MARK
    body = copy.deepcopy((state.get("goal_contract") or {}).get("body") or {})
    for row in body.get("acceptance_criteria") or []:
        if row.get("id") in names and test_cases.mark(row.get("verification_method")) == source:
            row["verification_method"] = to + " " + names[row["id"]]
    return body


def _guards_to_tests(state: RunState, error: TestsNeedRewriting, files, record, send_back, run_reference) -> bool:
    """Send guards whose tests still fail on today's code back to plan review relabelled test:; False if not.

    Live run, 2026-11-18: a plan labelled a criterion the code did not meet yet as a guard. Its test failed on
    its assertion, the Tester's rewrites could not make it pass without weakening it, and the run ended in a
    pause saying the case could not be tested on this host. The runner now checks the same tests on the original
    reference; when each still fails there, the plan's label is what is wrong, and the Plan Reviewer sees the
    runner-built draft as for a test: case that already passes. Once per case: a second disagreement takes the
    existing path to the Investigator."""
    guards = error.failing_guards
    rows = {
        row.get("id"): row
        for row in ((state.get("goal_contract") or {}).get("body") or {}).get("acceptance_criteria") or []
    }
    if (
        not guards
        or send_back is None
        or run_reference is None
        or acceptance.sent_back(state) & set(guards)
        or any(
            test_cases.mark((rows.get(cid) or {}).get("verification_method")) != test_cases.GUARD_MARK for cid in guards
        )
    ):
        return False
    original = run_reference({cid: guard["tests"] for cid, guard in guards.items()}, files)
    verdicts = original.get("verdicts") or {}
    if original.get("error") or set(verdicts) != set(guards) or any(v != "fails" for v in verdicts.values()):
        return False
    names = {cid: guard["name"] for cid, guard in guards.items()}
    changes = [
        {
            "item": cid,
            "change": "reworded",
            "basis": "runner_proof",
            "answer_id": "",
            "replacement": f"{test_cases.MARK} {names[cid]} (the runner saw it fail on today's code and on the "
            f"original code after {len(_rewrites(state))} rewrite(s) of the test, so today's code does not have "
            "this behavior yet, or the test asserts something the criterion does not say"
            + (f"; it failed with: {guards[cid]['detail']}" if guards[cid].get("detail") else "")
            + ")",
        }
        for cid in sorted(guards)
    ]
    acceptance.record_send_back(state, sorted(guards), record.get("output"))
    send_back(corrected_body(state, names, to=test_cases.MARK), changes)
    return True


def _output_excerpt(run: dict, *, keep: int = 3, limit: int = 400) -> str:
    """A short, bounded tail of the failed command's own output (#3091); receipts carry it in ``tail``."""
    kept = [line for line in str(run.get("tail") or "").splitlines() if line.strip()]
    return "\n".join(kept[-keep:])[-limit:]


def _verdicts(
    state: RunState, cases: list[dict], value: dict, workspace, run: dict, *, inherited=(), retained=()
) -> dict[str, str]:
    """Per case, how the runner saw its test on today's code; raises ValueError naming what is wrong."""
    if run.get("error"):
        if run.get("acceptance_refusal") and str(run["error"]).startswith(wrapped_runner.PREFLIGHT_ERROR):
            raise TestsNeedRewriting(run["error"])
        raise ExecutionEvidenceRequired(
            f"The runner could not run the acceptance tests on today's code: {run['error']}"
        )
    results = run.get("results")
    if results is None:
        outcome = check_outcome.classify(run)
        if outcome["reason"] == check_outcome.RUNNER_NOT_STARTED:
            raise RunnerDidNotStart(check_outcome.describe("The acceptance test command", outcome), run)
        if run.get("exit_code") not in (0, None):
            # A failed command that printed no events is a command failure, not missing tests (#3091).
            excerpt = _output_excerpt(run)
            detail = f"; its output ends with:\n{excerpt}" if excerpt else ""
            raise ExecutionEvidenceRequired(
                f"The acceptance tests reported no per-test results and the test command exited "
                f"{run['exit_code']}{detail}"
            )
        raise ExecutionEvidenceRequired(
            "The acceptance tests reported no per-test results; write standard tests for the project"
        )
    rows = {row["case_id"]: row for row in value["tests"]}
    # The final regression proof finds each test by the approved name, else by the case id
    # (test_ac1_...): a test named otherwise would pass here and never count there.
    misnamed = [
        f"{case['id']}: {rows[case['id']]['test_name']}"
        for case in cases
        if case.get("test_name") != rows[case["id"]]["test_name"]
        and not (
            not case.get("test_name")
            and test_cases.match_cases([{"id": case["id"]}], [rows[case["id"]]["test_name"]])[case["id"]]
        )
    ]
    if misnamed:
        raise ValueError(f"Name each test as its case says (the approved name, else test_<case id>_...): {misnamed}")
    named = [{**case, "test_name": case.get("test_name") or rows[case["id"]]["test_name"]} for case in cases]
    passed = reference.wholly_passing(named, results)
    failed = test_cases.match_cases(named, results.get("failed") or [])
    skipped = test_cases.match_cases(named, results.get("skipped") or [])
    # A bug's reproduction must run and fail; a file that cannot load is no reproduction (autocode_proof_seam).
    unloadable = bool(results.get("collection_errors")) and not test_cases.bugfix(state)
    verdicts, wrong, skipped_again, failing_guards = {}, [], [], {}
    needs_execution = False
    already = {row["case_id"] for row in value["already_passing"]}
    for case in named:
        source = (Path(workspace) / rows[case["id"]]["file"]).read_text(errors="replace")
        if case.get("kind") == "preserve":
            verdicts[case["id"]] = "passes" if passed[case["id"]] else ""
            if not passed[case["id"]]:
                needs_execution = True
                if skipped[case["id"]]:
                    skipped_again.append(case["id"])
                elif failed[case["id"]] and not _own_error(case, rows[case["id"]]["file"], source, run):
                    failing_guards[case["id"]] = {
                        "name": case["test_name"],
                        "tests": sorted(failed[case["id"]]),
                        "detail": _failure_line(run, case["test_name"]),
                    }
                wrong.append(
                    _skipped(case, rows[case["id"]]["file"], run)
                    if skipped[case["id"]]
                    else _guard_not_passing(case, rows[case["id"]]["file"], run, results)
                )
        elif case["id"] in retained and passed[case["id"]]:
            verdicts[case["id"]] = acceptance.CANDIDATE_FAIL_FIRST
        elif case["id"] in inherited and passed[case["id"]]:
            verdicts[case["id"]] = "already passes"
        elif case["id"] in already:
            verdicts[case["id"]] = "already passes" if passed[case["id"]] else ""
            if not passed[case["id"]]:
                wrong.append(f"{case['id']} was reported already working, but {case['test_name']} does not pass today")
        elif passed[case["id"]]:
            wrong.append(f"{case['id']} ({case['test_name']}) already passes on today's code")
        elif failed[case["id"]] and (own := _own_error(case, rows[case["id"]]["file"], source, run)):
            needs_execution = True
            wrong.append(own)
        elif failed[case["id"]] and (skip := _skip_after_change(case, rows[case["id"]]["file"], source, run)):
            needs_execution = True
            skipped_again.append(case["id"])  # a second such test ends the rewrites, as a skipped one does
            wrong.append(skip)
        elif failed[case["id"]]:
            verdicts[case["id"]] = "fails"
        elif unloadable and test_cases.named_in(case["test_name"], source):
            verdicts[case["id"]] = "cannot load"
        elif skipped[case["id"]]:
            needs_execution = True
            skipped_again.append(case["id"])
            wrong.append(_skipped(case, rows[case["id"]]["file"], run))
        else:
            needs_execution = True
            wrong.append(_not_run(case, rows[case["id"]]["file"], source, run, results))
    if wrong:
        message = (
            "The runner ran the acceptance tests on today's code: "
            + "; ".join(wrong)
            + ". A test: case must fail before the Builder starts and a guard: case must pass. If the behavior "
            "already exists, keep its test and also report the case in already_passing; do not weaken the test."
        )
        if needs_execution:
            only_guards = len(failing_guards) == len(wrong) and not results.get("collection_errors")
            raise TestsNeedRewriting(
                message, skipped=skipped_again, failing_guards=failing_guards if only_guards else {}
            )
        raise ValueError(message)
    return verdicts


# A failure the test raises on itself: an attribute its own TestCase class lacks (self.assertIsFalse), or a
# name its own line never defined. No product change can make such a test pass (#3262).
_OWN_ATTRIBUTE = re.compile(r"\bAttributeError: '(\w+)' object has no attribute '\w+'")
_UNDEFINED_NAME = re.compile(r"\bNameError: name '\w+' is not defined")
_FAILURE_START = re.compile(r"^(?:ERROR: |FAIL: |FAILED |ERROR |_{3,} )")
_FRAME = re.compile(r'^\s*File "([^"]+)", line \d+|^([^\s:]+\.py):\d+: ', re.M)


def _failure_blocks(tail: str, test_name: str) -> list[str]:
    """The runner's report of each failure whose heading names ``test_name`` (unittest or pytest output)."""
    blocks, current = [], None
    for line in tail.splitlines():
        if _FAILURE_START.match(line):
            current = [line] if re.search(rf"\b{re.escape(test_name)}\b", line) else None
            if current is not None:
                blocks.append(current)
        elif current is not None:
            current.append(line)
    return ["\n".join(block) for block in blocks]


def _failure_line(run: dict, test_name: str, limit: int = 200) -> str:
    """The last line of the runner's report of this test's failure (its assertion, usually); "" if none."""
    blocks = _failure_blocks(str(run.get("tail") or ""), test_name)
    lines = [line.strip() for line in (blocks[-1] if blocks else "").splitlines()[1:] if line.strip()]
    return lines[-1][:limit] if lines else ""


def _own_error(case: dict, file: str, source: str, run: dict) -> str:
    """Why a failing test fails on its own line rather than on the product; "" when it does not (#3262).

    Live run, 2026-11-11: a test called ``self.assertIsFalse``, which unittest does not have. It errored on
    today's code, so it was accepted as failing first; the Builder then could not make it pass, and its
    second pass ran to the hour's limit. The case now goes back to the Tester with the error."""
    classes = set(re.findall(r"^\s*class (\w+)\b", source, re.M))
    for block in _failure_blocks(str(run.get("tail") or ""), case["test_name"]):
        found = _OWN_ATTRIBUTE.search(block)
        if not (found and found.group(1) in classes):
            found = _UNDEFINED_NAME.search(block)
            frames = [a or b for a, b in _FRAME.findall(block[: found.start()])] if found else []
            if not (found and frames and Path(frames[-1]).name == Path(file).name):
                continue
        return (
            f"{case['id']}: {case['test_name']} in {file} fails on its own line, not on the product: "
            f"`{found.group(0)}`. No product change can make it pass; correct the test so it fails only "
            "because the product lacks the behavior"
        )
    return ""


def _skip_after_change(case: dict, file: str, source: str, run: dict) -> str:
    """Why a failing test would skip once the change is made, so the Tester can replace it (#3333); "" if not."""
    tail = "\n".join(_failure_blocks(str(run.get("tail") or ""), case["test_name"])) or str(run.get("tail") or "")
    skip = unreached_skip.skip_below_failure(source, file, case["test_name"], tail)
    if not skip:
        return ""
    return (
        f"{case['id']}: {case['test_name']} in {file} fails today, but below the line that failed it can skip "
        f"({skip}). Once the change is made that skip is reached, and a test that skips on this host cannot show "
        "the change works, so the regression proof could not pass. Write a test for this case that runs here "
        "both before and after the change only if it proves the exact approved behavior. Do not assert different, "
        "fallback or additional behavior to avoid a host limitation; report the case in cannot_write with the "
        "concrete host limitation instead"
    )


def _guard_not_passing(case: dict, file: str, run: dict, results: dict) -> str:
    """Why a guard's test did not pass on today's code; a file that cannot load there is named as the cause."""
    wrong = f"guard {case['id']} ({case['test_name']}) does not pass on today's code"
    stem = Path(file).stem
    unloadable = [name for name in results.get("collection_errors") or [] if file in name or stem in name]
    if not unloadable:
        return wrong
    errors = [line.strip() for line in str(run.get("tail") or "").splitlines() if "Error" in line]
    cause = f" ({errors[-1][:200]})" if errors else ""
    return (
        f"{wrong}: it never ran, because {file} cannot load on today's code{cause}. A file that holds a "
        "guard must load before the Builder starts: import what the product does not have yet inside the new "
        "tests that use it, not at the top of the file, or put the new tests in a file of their own"
    )


def _skipped(case: dict, file: str, run: dict) -> str:
    """Why a test the command collected and then skipped proves nothing, so the Tester can replace it (#3261).

    A test that skips on this host (a platform or dependency condition) neither fails nor passes here, and no
    rewrite of the same skipped test changes that: the retry needs a test that runs under the runner's command
    without inventing behavior outside the approved case (#3350)."""
    command = run.get("command") or "the project's test command"
    return (
        f"{case['id']}: {case['test_name']} in {file} was collected and then skipped by the runner's command "
        f"`{command}` on this host, so it proved nothing. A skipped test neither fails nor passes: write a test "
        "for this case that runs here only if it proves the exact approved behavior (for example, through an "
        "input or fixture the approved contract specifies). Do not assert different, fallback or additional "
        "behavior to avoid a host limitation; report the case in cannot_write with the concrete host limitation "
        "instead"
    )


def _not_run(case: dict, file: str, source: str, run: dict, results: dict) -> str:
    """Why the runner saw no result for a case's test, so the Tester's retry can fix the test (#1567).

    A file can define the test in a form the runner's command does not collect (a bare pytest function
    under ``python -m unittest``): every retry then sees the same "no test ran" until the Tester gives up."""
    missing = f"{case['id']}: no test named {case['test_name']} ran in {file}"
    if not test_cases.named_in(case["test_name"], source):
        return missing
    ran = sum(len(results.get(key) or []) for key in ("passed", "failed", "skipped"))
    command = run.get("command") or "the project's test command"
    hint = (
        " (python -m unittest collects only test_ methods of a unittest.TestCase subclass)"
        if "unittest" in command
        else ""
    )
    return (
        f"{missing}: the file defines it, but the runner's command `{command}` ran {ran} test(s) and did not "
        f"collect it; write the test so that command runs it{hint}"
    )


def _runner_not_started(state: RunState, value: dict, record: dict, workspace, error: RunnerDidNotStart) -> None:
    """Keep the finished writer, its tests and the failed command's receipt, and pause on the execution problem.

    A runner that never started is not a defect in the written tests (#3877), so this is no test-correction or
    plan question: the retained retry (autocode_acceptance_verification_retry) re-runs the checks on the next
    explicit resume without another writer or planner call, and still needs the normal per-case proof."""
    run = error.run
    receipt = {
        "command": run.get("command") or "",
        "exit_code": run.get("exit_code"),
        "output": run.get("output"),
        "tail": str(run.get("tail") or "")[-2000:],
    }
    verification_retry.retain(
        state, value, record, workspace, error, kind=verification_retry.RUNNER_NOT_STARTED, receipt=receipt
    )
    run_status.pause(
        state,
        PAUSE,
        f"The runner could not establish the approved pre-build proof: {error}. The test command did not start on "
        "this host, so the written tests were kept and not judged. Correct the environment; --resume-paused re-runs "
        "the checks without another Tester or Planner call.",
        next_stage=STAGE,
    )


def apply(
    state: RunState,
    value: dict,
    record: dict,
    workspace,
    run_tests=None,
    evidence_dir=None,
    send_back=None,
    run_reference=None,
) -> None:
    """Accept the Tester's files only when the runner sees each case behave as approved on today's code.

    ``send_back(body, changes)`` installs a runner-corrected draft only after a fresh original pass.
    ``run_reference`` classifies current passing cases without another model or candidate execution."""
    stray = sorted(str(path) for path in record.get("changed_files") or [] if not is_test_path(str(path)))
    prerequisites = {}
    if stray and all(Path(path).name in project_kinds.ALL_TEST_PREREQUISITES for path in stray):
        try:
            prerequisites = go_prerequisites.from_attempt(
                workspace,
                record,
                stray,
                Path(evidence_dir or Path(record.get("output") or workspace).parent) / "go-test-prerequisites",
                run_command,
            )["files"]
        except (OSError, ValueError, KeyError) as error:
            raise stray_writes.StrayWrites("Unverified Go test prerequisites: " + str(error), stray) from error
        stray = [path for path in stray if path not in prerequisites]
    if stray:
        raise stray_writes.StrayWrites(
            "The Tester may change only test files; this attempt changed: " + ", ".join(stray), stray
        )
    cases = acceptance.job_cases(state)
    _check_rows(value, cases, shared_names=set(acceptance.shared_test_names(state).values()))
    repair = evidence_repair.packet(state)
    t_repair = test_repair.packet(state, receipts_map=acceptance.receipts(state))
    if value["cannot_write"] and not repair:
        acceptance.record_cannot_write(state, cases, value["cannot_write"], record)
        _pause_cannot_write(
            state, [f"{row['case_id']} cannot be tested: {row['reason']}" for row in value["cannot_write"]]
        )
        return
    files = sorted({row["file"] for row in value["tests"]})
    bad = [path for path in files if not is_test_path(path) or not (Path(workspace) / path).is_file()]
    if bad:
        raise ValueError(f"Each test file must exist in the workspace and be a test file: {bad}")
    original_reference, retained = None, set()
    try:
        if repair and (value["cannot_write"] or not record.get("changed_files")):
            raise TestsNeedRewriting(
                "The independent evidence review requires stronger selected named tests. "
                "Change their assertions to detect the rejected gaps; unchanged files and inline-only "
                "diagnostics do not repair missing-test evidence. Preserve all existing assertions."
            )
        if t_repair and not record.get("changed_files"):
            raise TestsNeedRewriting(
                "The independent review found defects in the protected acceptance tests. "
                "Modify the defective tests so they prove the exact approved criteria (or report "
                "host-bound untestable cases in cannot_write); unchanged files do not repair them."
            )
        run = (run_tests or (lambda tests: {"error": "no test runner was given"}))(files)
        if check_outcome.classify(run)["reason"] != check_outcome.RUNNER_NOT_STARTED:
            verification_retry.runner_started(state)
        inherited = provenance.inherited_cases(state, reports=value["tests"], workspace=workspace)
        selected = reference.passing(cases, value["tests"], run, inherited)
        if selected and run_reference is not None:
            if problem := reference.candidate_problem(run):
                raise ReferenceEvidenceRequired(problem)
            # Reject broken guards and invalid names before spending on another execution.
            # This temporary verdict is pure; it grants no receipt or transition.
            _verdicts(state, cases, value, workspace, run, inherited=inherited, retained=selected)
            original_reference = run_reference(selected, files)
            if original_reference.get("error"):
                setup_errors = original_reference.get("setup_errors")
                if setup_errors and original_reference.get("receipt"):
                    evidence = original_reference.get("evidence") or {}
                    raise TestsNeedRewriting(
                        "The original-reference runner classified the following failures as setup-related, "
                        "so they are not accepted as behavior proof: "
                        + "; ".join(f"{name}: {reason}" for name, reason in sorted(setup_errors.items()))
                        + ". Preserve the approved behavior and all existing assertions. Make the named test "
                        "run an assertion of the exact approved behavior on both the original and candidate. "
                        "Do not hide or substitute for a genuinely missing dependency. "
                        f"Runner evidence: {evidence.get('path', 'unavailable')} "
                        f"(sha256 {evidence.get('sha256', 'unavailable')}).",
                        original_reference=original_reference,
                    )
                raise ReferenceEvidenceRequired(original_reference["error"])
            classifications = original_reference.get("verdicts") or {}
            if set(classifications) != set(selected) or any(
                v not in ("passes", "fails") for v in classifications.values()
            ):
                raise ReferenceEvidenceRequired("Missing original reference classification for passing tests")
            retained = {cid for cid, verdict in classifications.items() if verdict == "fails"}
            value = copy.deepcopy(value)
            value["already_passing"] = [row for row in value["already_passing"] if row["case_id"] not in selected]
            value["already_passing"].extend(
                {
                    "case_id": cid,
                    "evidence": "The runner observed this exact test pass on the candidate and original reference",
                }
                for cid, verdict in classifications.items()
                if verdict == "passes"
            )
        verdicts = _verdicts(state, cases, value, workspace, run, inherited=inherited, retained=retained)
    except ExecutionEvidenceRequired as error:
        if isinstance(error, RunnerDidNotStart):
            _runner_not_started(state, value, record, workspace, error)
            return
        if isinstance(error, TestsNeedRewriting):
            if _rewrite(state, error, record):
                if error.original_reference:
                    # A retained verification retry cannot edit tests. Archive it before
                    # scheduling the Tester or it would replay the same report instead.
                    verification_retry.finish(state)
                return
            if _guards_to_tests(state, error, files, record, send_back, run_reference):
                return
            # The runner's own rewrites are spent, or the same cases came back skipped after the Tester was told:
            # the attempt is rejected, so the Investigator looks at it before any person is asked (#3261). A
            # pause here asked the person to resume and correct the tests, a question about AutoCode's machinery.
            raise TestsNeedRewriting(
                f"{error} The runner already sent these tests back {len(_rewrites(state))} time(s)"
                + (
                    "; the same cases were skipped again, and a skipped test proves nothing on this host"
                    if error.skipped
                    else ""
                )
                + ".",
                skipped=error.skipped,
                original_reference=error.original_reference,
            ) from error
        if isinstance(error, ReferenceEvidenceRequired):
            if verification_retry.retain(state, value, record, workspace, error):
                return
            if (state.get(acceptance.KEY) or {}).get(verification_retry.KEY):
                raise
            remedy = (
                "The approved plan and retained tests are unchanged. Original-reference verification remains "
                "required; this execution gap is not a Requirements or plan question."
            )
        elif "no test command runs these files" in str(error):
            # The run's suite cannot name these files: a binding gap, not a fault in the tests (#2609).
            remedy = (
                "The tests are not at fault: the run's test suite cannot run them. Resume with a saved "
                "--test-command that runs them, and the runner executes a fresh proof with it."
            )
        else:
            remedy = (
                "Resume explicitly to correct the tests and execute a fresh proof, or use --feedback to revise "
                "the plan. A report-only repair cannot change test files or rerun proof."
            )
        run_status.pause(
            state,
            PAUSE,
            f"The runner could not establish the approved pre-build proof: {error}. {remedy}",
            next_stage=STAGE,
        )
        return
    verification_retry.finish(state)
    preserve = {case["id"] for case in cases if case.get("kind") == "preserve"}
    # A guard is required to pass on the original code; the runner has just
    # verified that above. Only a new-behavior test passing early needs replan.
    already = {
        row["case_id"]: row
        for row in value["already_passing"]
        if row["case_id"] not in inherited and row["case_id"] not in preserve
    }
    if already:
        names = {row["case_id"]: row["test_name"] for row in value["tests"] if row["case_id"] in already}
        # A bug's reproduction that already passes disagrees with its diagnosis: only the user can settle that.
        # The plan's own test: cases are contract rows the runner can relabel, in a bug fix as in a build.
        reproduced = reproduction_ids(state) & set(already)
        if send_back is None or reproduced or acceptance.sent_back(state) & set(already):
            _pause(
                state,
                [
                    f"{cid} already works: {row['evidence']}"
                    + (
                        " (the bug's reproduction passes, so the diagnosis and the code disagree)"
                        if cid in reproduced
                        else ""
                    )
                    for cid, row in sorted(already.items())
                ],
            )
            return
        changes = [
            {
                "item": cid,
                "change": "reworded",
                "basis": "runner_proof",
                "answer_id": "",
                "replacement": f"guard: {names[cid]} (the runner saw it pass on today's code: {already[cid]['evidence']})",
            }
            for cid in sorted(already)
        ]
        acceptance.record_send_back(state, sorted(already), record.get("output"))
        send_back(corrected_body(state, names), changes)
        return
    archive = Path(evidence_dir or Path(record.get("output") or workspace).parent) / "acceptance-tests"
    archive.mkdir(parents=True, exist_ok=True)
    kept = {}
    for path in sorted(set(files) | prerequisites.keys()):
        digest = util.file_hash(Path(workspace) / path)
        archived_file = archive / digest
        shutil.copyfile(Path(workspace) / path, archived_file)
        kept[path] = {"sha256": digest, "archive": str(archived_file)}
    acceptance.record_receipt(
        state,
        cases,
        {
            "contract_hash": (state.get("goal_contract") or {}).get("hash"),
            "case_ids": [case["id"] for case in cases],
            "files": kept,
            "results": verdicts,
            "command": run.get("command", ""),
            "output": record.get("output"),
            "source_revision": record.get("source_revision"),
            **({"original_reference": original_reference} if original_reference else {}),
        },
    )
    if acceptance.is_blind(state):
        for path in files:
            target = Path(workspace) / path
            if target.is_file():
                target.unlink()
            parent = target.parent
            while parent != Path(workspace) and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
    run_status.running(state, "EXECUTING", next_stage=acceptance.resume_stage(state))

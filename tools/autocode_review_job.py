"""The review workflow: judge an existing change and report findings, changing nothing.

A run recognized as ``review`` (autocode_workflows) goes straight to ``STAGE``:
the Reviewer, on the Validator's route, reads the change the request names (a
patch file, a diff, a branch), the code around it and the tests, runs whatever
it needs in a scratch copy of its own, and returns findings. The runner then:

- rejects the report if the stage changed anything in the workspace outside
  ``review/`` (a review is read-only; the before/after snapshot is the evidence),
- proves each blocking finding: it states the defect as a plain-English example and
  the Reviewer delivers a test named after it (F1 -> test_f1_...) under
  ``review/tests/``. The runner applies the change under review (``change_patch``)
  in a scratch copy and runs the delivered tests; each such finding's test must
  FAIL there, or the report is rejected. A blocking finding a test cannot show (a
  documented compatibility rule, a missing doc) says why in ``untestable``,
- writes ``REPORT_PATH`` from the validated report, so the file always matches
  the schema and the saved report,
- completes the run. No requirements, no plan approval, no Builder.

This module is pure: prompt, schema, and the transition; the unit passes in the
function that runs the tests. It imports nothing from the runner. State keys
written: ``review`` (verdict, counts, report path, finding_tests).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

try:
    from . import autocode_stray_writes as stray_writes
    from . import autocode_workflows as workflows
    from .autocode_test_cases import match_cases
except ImportError:
    import autocode_stray_writes as stray_writes
    import autocode_workflows as workflows
    from autocode_test_cases import match_cases

STAGE = workflows.REVIEW_STAGE
REPORT_PATH = "review/findings.json"
ALLOWED_PREFIXES = ("review/",)
SEVERITIES = ("blocking", "advisory")

FINDING = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "severity", "file", "lines", "summary", "evidence", "example", "untestable"],
    "properties": {
        "id": {"type": "string"},
        # The defect as a concrete example: "Given ..., when ..., then ... (expected ...)".
        "example": {"type": "string"},
        # Why no test can show a blocking finding; "" when a delivered test shows it.
        "untestable": {"type": "string"},
        "severity": {"type": "string", "enum": list(SEVERITIES)},
        "file": {"type": "string"},
        "lines": {"type": "array", "items": {"type": "integer"}, "maxItems": 2},
        "summary": {"type": "string"},
        "evidence": {"type": "string"},
    },
}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "summary", "change_under_review", "change_patch", "findings", "tests_run",
                 "delivered_tests"],
    "properties": {
        # The patch file in the repository that holds the change, applied by the runner; "" when the
        # change is already in the workspace.
        "change_patch": {"type": "string"},
        "verdict": {"type": "string", "enum": ["approve", "request_changes"]},
        "summary": {"type": "string"},
        "change_under_review": {"type": "string"},
        "findings": {"type": "array", "items": FINDING},
        "tests_run": {"type": "array", "items": {"type": "string"}},
        # Targeted tests written into the workspace under review/tests/, for the user to adopt.
        "delivered_tests": {"type": "array", "items": {"type": "string"}},
    },
}
TESTS_PREFIX = "review/tests/"

PROMPT = """You are the Reviewer: an independent engineer asked to judge an existing change before it is merged.
You report findings. You do not fix anything and you do not edit the repository.

What to do:
1. Identify the change under review from the request: a patch file in the repository, a diff, a branch,
   or a described change. Say what you reviewed in change_under_review.
2. Read the change AND its context: the code around it, the README or docs that state how the code is
   supposed to behave, the existing tests. A change can pass its own tests and still break a rule the
   repository states elsewhere.
3. Test where it helps. Make your own scratch copy under .autocode/ inside the workspace (for example
   .autocode/scratch/review); the runner's before/after comparison ignores .autocode/. Never use /tmp,
   mktemp or another path outside the workspace: the provider sandbox denies external directories and
   the whole attempt is lost. Apply the change in your scratch copy and run the test suite there. Never
   apply the change to the workspace itself; the runner compares the workspace before and after and
   rejects a review that changed anything outside review/.
   When the change's own tests pass without exercising what it claims (they read a value the code sets
   directly instead of going through the real code path), write a targeted test that FAILS on the
   changed code and would PASS once the defect is fixed. Prove both in your scratch copy, then deliver
   the test in the workspace as review/tests/test_<name>.py: a standard unittest file that runs from
   the repository root and imports the project's own packages. review/tests/ is the only place you may
   write in the workspace. List every delivered file in delivered_tests (empty when you delivered none).
   delivered_tests contains workspace-relative file paths, not dotted test IDs or test method names.
4. Report findings, each with a severity:
   - blocking: must be fixed before merge. A behavior that regresses, an invariant that breaks, a
     compatibility change, a defect the change's tests do not catch.
   - advisory: everything else. Style, naming, simplification, a suggestion, a gap in documentation.
     Blocking means the change breaks something; a README or docstring that could say more breaks
     nothing, so it is advisory.
   Point at the file and the line span in the file AS IT WOULD BE AFTER THE CHANGE, and give evidence:
   the rule that is broken, the command you ran and what it printed, the scenario that fails.
   Give every blocking finding an example: the defect as one concrete case in plain English, "Given
   <exact starting data>, when <exact action>, then <what happens> (expected <what should happen>)".
5. Prove every blocking finding with a test, unless no test can show it. Deliver it under review/tests/
   as above. Name the actual test function or method after the finding's id (F1 -> test_f1_behavior).
   Naming only the file or class is not enough: the runner matches the function or method name only.
   Keep finding IDs stable; correct the test method name rather than renaming the finding to a filename.
   The runner applies the change
   in a scratch copy of its own and runs your delivered tests: each blocking finding's test must FAIL on
   the changed code, or your report is rejected. Name the patch file in change_patch (for example
   pr-184.patch), or "" when the change is already in the workspace. When a test really cannot show a
   blocking finding (a documented compatibility rule the change breaks), say why in untestable;
   otherwise untestable is "". Advisory findings need no test.
6. Verdict: request_changes when there is at least one blocking finding, otherwise approve. Do not
   invent problems to look thorough: a correct change gets approve and, at most, advisory notes.

Return JSON only, matching the schema the runner gives you. The runner saves your report as
review/findings.json in the workspace; you do not write that file.
"""


def packet(state: dict, inventory: dict | None = None, engine: str | None = None) -> dict:
    return {"stage": STAGE, "task": state["task"], "workspace": state.get("workspace"),
            "execution_engine": engine, "report_path": REPORT_PATH,
            "workspace_inventory": inventory or {},
            # Present because every provider reads them; a review has none of these.
            "goal_contract": None, "current_task": None, "saved_answers": {}}


def prompt(state: dict, inventory: dict | None = None, soft_budget_tokens: int = 10000,
           engine: str | None = None) -> tuple[str, dict]:
    text = PROMPT + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet(state, inventory, engine), indent=2)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def stray_changes(changed_files) -> list[str]:
    """Paths the stage changed that a review may not touch."""
    return sorted(path for path in (changed_files or []) if not str(path).startswith(ALLOWED_PREFIXES))


def delivered_tests(value: dict, record: dict, workspace) -> list[str]:
    """The targeted tests the review left under review/tests/: declared ones must exist there,
    and any test file the stage wrote there counts even if the report forgot to list it."""
    declared = [str(path) for path in value.get("delivered_tests") or []]
    outside = [path for path in declared if not path.startswith(TESTS_PREFIX)]
    if outside:
        raise ValueError(f"Delivered tests must live under {TESTS_PREFIX}: {outside}")
    missing = [path for path in declared if not (Path(workspace) / path).is_file()]
    if missing:
        raise ValueError(f"The report lists tests that were not delivered: {missing}")
    written = [str(path) for path in (record.get("changed_files") or [])
               if str(path).startswith(TESTS_PREFIX) and str(path).endswith(".py")]
    return sorted(set(declared) | set(written))


def proven_blocking(value: dict) -> list[dict]:
    """The blocking findings that must be shown by a failing test."""
    return [f for f in value["findings"] if f["severity"] == "blocking" and not f.get("untestable", "").strip()]


def prove(value: dict, delivered: list[str], run_tests) -> dict:
    """Run the delivered tests on the changed code and require each proven finding's test to fail there.

    ``run_tests(tests, patch)`` is the runner's scratch run (autocode_verify.scratch_run). Returns
    {"finding_tests": {id: [failing tests]}, "command", "tail"}; raises ValueError naming what is unproven.
    """
    unexampled = [f["id"] for f in value["findings"] if f["severity"] == "blocking" and not f.get("example", "").strip()]
    if unexampled:
        raise ValueError(f"Every blocking finding needs an example of the defect in plain English: {unexampled}")
    findings = proven_blocking(value)
    if not findings:
        return {"finding_tests": {}, "command": "", "tail": ""}
    if not delivered:
        raise ValueError("Blocking findings need a delivered test under review/tests/ that fails on the change "
                         f"(or a reason in untestable): {[f['id'] for f in findings]}")
    run = run_tests(delivered, value.get("change_patch", "").strip() or None)
    if run.get("error"):
        raise ValueError(f"The runner could not run the delivered tests on the change: {run['error']}")
    results = run.get("results")
    if results is None:
        raise ValueError("The delivered tests reported no per-test results, so no finding can be matched to "
                         "its test; deliver standard unittest or pytest tests")
    failing = sorted(set(results["failed"]) - set(results.get("collection_errors") or []))
    matched = match_cases([{"id": f["id"]} for f in findings], failing)
    unproven = [f["id"] for f in findings if not matched[f["id"]]]
    if unproven:
        raise ValueError("These blocking findings have no delivered test, named after them, that fails on the "
                         f"changed code: {unproven} (failing tests: {failing or 'none'}). "
                         "Name the actual test function or method after the finding ID, not just the file or class "
                         "(F1 -> test_f1_behavior). Keep finding IDs stable and update the delivered test methods; "
                         "delivered_tests must still list file paths, not dotted test IDs. If no failing test can show a "
                         "finding, do not keep retrying: make it advisory, drop it, or say why in untestable.")
    return {"finding_tests": matched, "command": run.get("command", ""), "tail": run.get("tail", "")[-1500:]}


def owns(state: dict) -> bool:
    return workflows.kind(state) == "review"


def apply(state: dict, value: dict, record: dict, workspace, run_tests=None) -> None:
    """Enforce read-only-ness, prove blocking findings, write the findings file, complete the run.

    ``run_tests(tests, patch)`` runs tests on the changed code in a scratch copy (the unit passes
    autocode_verify.scratch_run); without it a blocking finding can only be marked untestable."""
    stray = stray_changes(record.get("changed_files"))
    if stray:
        raise stray_writes.StrayWrites(
            "A review must not change the repository; this attempt changed: " + ", ".join(stray), stray)
    counts = {severity: sum(1 for f in value["findings"] if f["severity"] == severity) for severity in SEVERITIES}
    if value["verdict"] == "approve" and counts["blocking"]:
        raise ValueError("A review with blocking findings cannot approve")
    delivered = delivered_tests(value, record, workspace)
    proof = prove(value, delivered, run_tests or (lambda tests, patch: {"error": "no test runner was given"}))
    report = {key: value[key] for key in ("verdict", "summary", "change_under_review", "findings", "tests_run")}
    report["findings"] = [{**finding, "proven_by": proof["finding_tests"].get(finding["id"], [])}
                          for finding in value["findings"]]
    report["delivered_tests"] = delivered
    target = Path(workspace) / REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n")
    state["review"] = {**counts, "verdict": value["verdict"], "report_path": REPORT_PATH,
                       "output": record.get("output"), "change_under_review": value["change_under_review"],
                       "change_patch": value.get("change_patch", ""),
                       "delivered_tests": delivered, "finding_tests": proof["finding_tests"],
                       "proof_command": proof["command"]}
    state.update(status="TASK_COMPLETE", phase="COMPLETE", next_stage=None,
                 completed_at=dt.datetime.now(dt.timezone.utc).isoformat())


def render(state: dict) -> str:
    review = state.get("review") or {}
    lines = [f"REVIEW COMPLETE — {review.get('verdict', '?')}: {review.get('blocking', 0)} blocking, "
             f"{review.get('advisory', 0)} advisory",
             "Reviewed: " + str(review.get("change_under_review", "")),
             "Workspace unchanged: " + str(state.get("workspace")),
             "Findings: " + str(Path(state.get("workspace", "")) / review.get("report_path", REPORT_PATH))]
    for path in review.get("delivered_tests") or []:
        lines.append("Targeted test delivered: " + path)
    for finding, tests in (review.get("finding_tests") or {}).items():
        lines.append(f"Finding {finding} shown by the runner: {', '.join(tests)} fails on the change")
    if review.get("output"):
        lines.append("Reviewer report: " + str(review["output"]))
    return "\n".join(lines)

"""The review workflow: judge an existing change and report findings, changing nothing.

A run recognized as ``review`` (autocode_workflows) goes straight to ``STAGE``:
the Reviewer, on the Validator's route, reads the change the request names (a
patch file, a diff, a branch), the code around it and the tests, runs whatever
it needs in a scratch copy of its own, and returns findings. The runner then:

- rejects the report if the stage changed anything in the workspace outside
  ``review/`` (a review is read-only; the before/after snapshot is the evidence),
- proves each blocking finding: it states the defect as a plain-English example and
  the Reviewer delivers a test named after it (F1 -> test_f1_...) under
  ``review/tests/``. The runner runs identical probes on isolated base and changed copies
  when an explicit ``change_patch`` is supplied. Equivalent full Python failures
  become observations. Each executable blocker must fail on the changed source,
  or the report is rejected. A blocking finding a test cannot show (a
  documented compatibility rule, a missing doc) says why in ``untestable``,
- writes ``REPORT_PATH`` from the validated report, so the file always matches
  the schema and the saved report,
- completes the run. No requirements, no plan approval, no Builder.

This module is pure: prompt, schema, and the transition; the unit passes in the
function that runs the tests. It imports nothing from the runner. State keys
written: ``review`` (verdict, counts, report path, finding_tests).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import datetime as dt
import hashlib
import json
from pathlib import Path

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_mutation_check as mutation_check
    from . import autocode_review_attribution as attribution
    from . import autocode_stage_access as stage_access
    from . import autocode_stray_writes as stray_writes
    from . import autocode_workflows as workflows
    from .autocode_test_cases import match_cases
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_mutation_check as mutation_check
    import autocode_review_attribution as attribution
    import autocode_stage_access as stage_access
    import autocode_stray_writes as stray_writes
    import autocode_workflows as workflows
    from autocode_test_cases import match_cases

STAGE = workflows.REVIEW_STAGE
REPORT_PATH = "review/findings.json"
SEVERITIES = ("blocking", "advisory")

FINDING: dict = {
    "type": "object",
    "additionalProperties": False,
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
    "type": "object",
    "additionalProperties": False,
    "required": [
        "verdict",
        "summary",
        "change_under_review",
        "change_patch",
        "findings",
        "tests_run",
        "delivered_tests",
    ],
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

PROMPT = prompts.get("review-change.md")


def packet(state: RunState, inventory: dict | None = None, engine: str | None = None) -> dict:
    return {
        "stage": STAGE,
        "task": state["task"],
        "workspace": state.get("workspace"),
        "execution_engine": engine,
        "report_path": REPORT_PATH,
        "workspace_inventory": inventory or {},
        # Present because every provider reads them; a review has none of these.
        "goal_contract": None,
        "current_task": None,
        "saved_answers": {},
    }


def prompt(
    state: RunState, inventory: dict | None = None, soft_budget_tokens: int = 10000, engine: str | None = None
) -> tuple[str, dict]:
    text = PROMPT + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet(state, inventory, engine), indent=2)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def stray_changes(changed_files) -> list[str]:
    """Paths the stage changed that a review may not touch."""
    return stage_access.stray(STAGE, changed_files)


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
    written = [
        str(path)
        for path in (record.get("changed_files") or [])
        if str(path).startswith(TESTS_PREFIX) and str(path).endswith(".py")
    ]
    return sorted(set(declared) | set(written))


def proven_blocking(value: dict) -> list[dict]:
    """The blocking findings that must be shown by a failing test."""
    return [f for f in value["findings"] if f["severity"] == "blocking" and not f.get("untestable", "").strip()]


def prove(value: dict, delivered: list[str], run_tests) -> dict:
    """Run the delivered tests on the changed code and require each proven finding's test to fail there.

    ``run_tests(tests, patch)`` supplies a captured base/changed comparison (review_proof.collect),
    or a legacy changed-side receipt without attribution. Returns matched tests and dispositions; raises ValueError naming what is unproven.
    """
    unexampled = [
        f["id"] for f in value["findings"] if f["severity"] == "blocking" and not f.get("example", "").strip()
    ]
    if unexampled:
        raise ValueError(f"Every blocking finding needs an example of the defect in plain English: {unexampled}")
    findings = proven_blocking(value)
    if not findings:
        return {"finding_tests": {}, "dispositions": {}, "command": "", "tail": ""}
    if not delivered:
        raise ValueError(
            "Blocking findings need a delivered test under review/tests/ that fails on the change "
            f"(or a reason in untestable): {[f['id'] for f in findings]}"
        )
    comparison = run_tests(delivered, value.get("change_patch", "").strip() or None)
    run = comparison.get("changed", comparison)
    if run.get("error") or run.get("timed_out") or run.get("interrupted"):
        run = {**run, "error": run.get("error") or "test execution did not finish"}
    if run.get("error"):
        raise ValueError(f"The runner could not run the delivered tests on the change: {run['error']}")
    results = run.get("results")
    if results is None:
        raise ValueError(
            "The delivered tests reported no per-test results, so no finding can be matched to "
            "its test; deliver standard unittest or pytest tests"
        )
    if "changed" in comparison and not attribution.complete(run):
        raise ValueError("The changed-side finding proof has incomplete results or test setup failures")
    failing = sorted(
        set(results["failed"]) - set(results.get("collection_errors") or []) - set(results.get("setup_errors") or {})
    )
    matched = match_cases([{"id": f["id"]} for f in findings], failing)
    unproven = [f["id"] for f in findings if not matched[f["id"]]]
    if unproven:
        raise ValueError(
            "These blocking findings have no delivered test, named after them, that fails on the "
            f"changed code: {unproven} (failing tests: {failing or 'none'}). "
            "Name the actual test function or method after the finding ID, not just the file or class "
            "(F1 -> test_f1_behavior). Keep finding IDs stable and update the delivered test methods; "
            "delivered_tests must still list file paths, not dotted test IDs. If no failing test can show a "
            "finding, do not keep retrying: make it advisory, drop it, or say why in untestable."
        )
    base = comparison.get("base", {}) if value.get("change_patch", "").strip() else {}
    dispositions = {
        f["id"]: attribution.classify(
            matched[f["id"]],
            run,
            base,
            inputs=comparison.get("inputs"),
            reason=comparison.get("reason", "No captured explicit-patch baseline is available."),
            finding=f,
            changed_locations=comparison.get("changed_locations"),
        )
        for f in findings
    }
    return {
        "finding_tests": matched,
        "dispositions": dispositions,
        "command": run.get("command", ""),
        "tail": run.get("tail", "")[-1500:],
    }


def owns(state: RunState) -> bool:
    return workflows.kind(state) == "review"


def apply(
    state: RunState,
    value: dict,
    record: dict,
    workspace,
    run_tests=None,
    run_mutation_check=None,
    snapshot=None,
) -> None:
    """Enforce read-only-ness, prove blocking findings, write the findings file, complete the run.

    ``run_tests(tests, patch)`` supplies runner-owned review proof (the unit passes
    review_proof.collect); without it a blocking finding can only be marked untestable."""
    stray = stray_changes(record.get("changed_files"))
    if stray:
        raise stray_writes.StrayWrites(
            "A review must not change the repository; this attempt changed: " + ", ".join(stray), stray
        )
    delivered = delivered_tests(value, record, workspace)
    proof = prove(value, delivered, run_tests or (lambda tests, patch: {"error": "no test runner was given"}))
    mutations = None
    if run_mutation_check is not None:
        try:
            mutations = run_mutation_check(value.get("change_patch", "").strip() or None)
        except command_receipt.OwnershipUncertain:
            raise
        except Exception as error:
            mutations = mutation_check.record(f"the review mutation check stopped: {type(error).__name__}: {error}")
    report = {
        key: value[key]
        for key in ("verdict", "summary", "change_under_review", "change_patch", "findings", "tests_run")
    }
    report["findings"] = [
        {
            **{key: finding[key] for key in FINDING["properties"] if key in finding},
            "proven_by": proof["finding_tests"].get(finding["id"], []),
            **(
                {"disposition": proof["dispositions"][finding["id"]]}
                if finding["id"] in proof["dispositions"]
                else {
                    "disposition": {
                        "runner": attribution.RUNNER,
                        "kind": "inconclusive",
                        "reason": "Untestable finding; explicitly unverified and blocking.",
                    }
                }
                if finding["severity"] == "blocking"
                else {}
            ),
        }
        for finding in value["findings"]
    ]
    counts = {
        "blocking": sum(attribution.blocking(f) for f in report["findings"]),
        "advisory": sum(f["severity"] == "advisory" for f in report["findings"]),
        "pre_existing": sum(attribution.pre_existing(f) for f in report["findings"]),
        "inconclusive": sum(
            attribution.blocking(f) and f.get("disposition", {}).get("kind") == "inconclusive"
            for f in report["findings"]
        ),
    }
    report.update(
        verdict="request_changes" if counts["blocking"] else "approve",
        submitted_verdict=value["verdict"],
        counts=counts,
    )
    report["verdict_adjustment"] = (
        "Runner derived the verdict from effective blockers after proof attribution."
        if report["verdict"] != value["verdict"]
        else ""
    )
    report["delivered_tests"] = delivered
    if mutations is not None:
        report["mutation_check"] = mutations
    target = Path(workspace) / REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(report, indent=2) + "\n").encode("utf-8")
    target.write_bytes(raw)
    state["review"] = {
        **counts,
        "verdict": report["verdict"],
        "submitted_verdict": value["verdict"],
        "verdict_adjustment": report["verdict_adjustment"],
        "dispositions": proof["dispositions"],
        "report_path": REPORT_PATH,
        "report_sha256": hashlib.sha256(raw).hexdigest(),
        "output": record.get("output"),
        "change_under_review": value["change_under_review"],
        "change_patch": value.get("change_patch", ""),
        "delivered_tests": delivered,
        "finding_tests": proof["finding_tests"],
        "proof_command": proof["command"],
        **({"mutation_check": mutations} if mutations is not None else {}),
    }
    # Bind after the core has written its canonical report: the stage's earlier
    # snapshot cannot include that write. Legacy saved reviews remain unbound.
    if snapshot is not None:
        state["review"]["source_revision"] = snapshot(workspace)["revision"]
    state.update(
        {
            "status": "TASK_COMPLETE",
            "phase": "COMPLETE",
            "next_stage": None,
            "completed_at": dt.datetime.now(dt.UTC).isoformat(),
        }
    )


def render(state: RunState) -> str:
    review = state.get("review") or {}
    lines = [
        f"REVIEW COMPLETE — {review.get('verdict', '?')}: {review.get('blocking', 0)} blocking, "
        f"{review.get('advisory', 0)} advisory",
        "Reviewed: " + str(review.get("change_under_review", "")),
        "Workspace unchanged: " + str(state.get("workspace")),
        "Findings: " + str(Path(state.get("workspace", "")) / review.get("report_path", REPORT_PATH)),
    ]
    if review.get("pre_existing") or review.get("inconclusive"):
        lines.append(
            f"{review.get('pre_existing', 0)} pre-existing observations; "
            f"{review.get('inconclusive', 0)} blockers with inconclusive attribution"
        )
    if review.get("verdict_adjustment"):
        lines.append("Submitted verdict: " + str(review.get("submitted_verdict")) + "; " + review["verdict_adjustment"])
    for path in review.get("delivered_tests") or []:
        lines.append("Targeted test delivered: " + path)
    for finding, tests in (review.get("finding_tests") or {}).items():
        lines.append(f"Finding {finding} shown by the runner: {', '.join(tests)} fails on the change")
    if review.get("output"):
        lines.append("Reviewer report: " + str(review["output"]))
    return "\n".join(lines)

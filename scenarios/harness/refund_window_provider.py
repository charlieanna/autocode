"""Script the refund trap; the controller, executed checks and scorer stay real.

The first Builder delivers the UTC clock defect with complete reference tests.
The Completion Reviewer leaves its failing repair task semantically incomplete,
so ordinary Resolver admission supplies its check plan. The read-only Resolver
names the actual source defect; the repair Builder applies the selected solution.
This proves wiring, not model judgment. SCENARIO_FAKE_REFUND_DIAGNOSIS=vague is
the diagnosis negative control.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

OWNED = {"shop/clock.py", "shop/refunds.py", "tests/test_refunds.py"}


def _scenario(reference):
    return next(parent for parent in Path(reference).parents if (parent / "scenario.toml").is_file())


def _apply(source, paths):
    for rel in paths:
        destination = Path.cwd() / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, destination)


def _source_hashes(paths):
    return {rel: hashlib.sha256(Path(rel).read_bytes()).hexdigest() for rel in paths if Path(rel).is_file()}


def _diagnosis():
    """Name a current planted defect, never claim a failure on fixed source."""
    clock = Path("shop/clock.py").read_text()
    refunds = Path("shop/refunds.py").read_text()
    if (
        "tz=timezone.utc" in clock or "return datetime.fromtimestamp(timestamp, tz=UTC).date()" in clock
    ) and "STORE_UTC_OFFSET_HOURS = -8" in clock:
        named = (
            "store_date ignores the store's UTC-8 offset and uses UTC calendar days. "
            "An evening refund on the final store day is incorrectly refused. "
            "Use the configured store offset, retain the cumulative cap and all refusals, "
            "and rerun the unchanged refund tests."
        )
    elif "if amount_cents > order.paid_cents:" in refunds:
        named = (
            "The cap is per refund rather than the running total: previous partial refunds "
            "are not deducted from the amount still available. Preserve the store-time window "
            "and refusals, subtract already refunded cents, and rerun the unchanged tests."
        )
    else:
        raise ValueError("Refund Resolver was not called on a planted fixture defect")
    if os.environ.get("SCENARIO_FAKE_REFUND_DIAGNOSIS") == "vague":
        return "The implementation has a bug; fix it and rerun the tests."
    return named


def report_for(stage, data, common, config, run_check, requirements):
    task = data["current_task"]
    body = data["goal_contract"]["body"]
    paths = list(config["paths"])
    if set(paths) != OWNED or not set(paths) <= set(task["affected_paths"]):
        raise ValueError("Refund fixture may write only the assigned refund solution paths")
    trace = Path(os.environ["SCENARIO_FAKE_CONFIG"]).with_name("refund-trace.jsonl")
    previous = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    dispositions = [
        {"id": ident, "disposition": "resolved", "evidence": "event:check"}
        for ident in (data.get("review_identity_policy") or {}).get("own_open_finding_ids", [])
    ]
    code = None
    if stage == "terra":
        attempt = 1 + sum(row["stage"] == "terra" for row in previous)
        if attempt == 1:
            scenario = _scenario(config["reference"])
            # Retain every complete reference test. Only implementation is planted.
            _apply(scenario / "reference", paths)
            if "shop/clock.py" in paths:
                shutil.copy2(scenario / "seed/shop/clock.py", Path("shop/clock.py"))
            shutil.copy2(scenario / "broken/trusts-store-date/shop/refunds.py", Path("shop/refunds.py"))
        else:
            # A selected broken implementation is an oracle control, never
            # permission to replace the complete tests with that variant's subset.
            _apply(Path(config["reference"]), [rel for rel in paths if rel != "tests/test_refunds.py"])
        code = run_check()
        result = {
            **common,
            "summary": "Plant the UTC clock defect" if attempt == 1 else "Apply the selected repair",
            "changed_files": paths,
            "commands_run": [config["check"]],
            "results": [f"exit {code}"],
            "remaining_risks": [],
            "evidence_refs": ["event:check"],
            "addressed_requirements": list(task["acceptance_criteria"]),
            "untested_behavior": [],
            "recommended_checks": [config["check"]],
        }
    elif stage == "sol":
        code = run_check()
        status = "PASS" if code == 0 else "FAIL"
        result = {
            **common,
            "verdict": status,
            "checks_run": [config["check"]],
            "checks": [{"command": config["check"], "exit_code": None, "evidence_ref": "event:"}],
            "findings": []
            if code == 0
            else [
                {
                    "id": "",
                    "severity": "high",
                    "blocking": True,
                    "finding": "The unchanged refund tests fail on the current implementation",
                    "evidence": "check:1",
                    "reproduction_steps": [config["check"]],
                    "expected": "Store-time window, cumulative cap and refusals all pass",
                    "actual": f"The executed check exited {code}; captured output contains the failure",
                    "why_it_matters": "The required refund behavior is missing",
                    "suggested_correction": "Repair the implementation without weakening any test",
                }
            ],
            "finding_dispositions": dispositions if code == 0 else [],
            "unverified_criteria": [],
            "criterion_results": [
                {"id": row["id"], "status": status, "evidence_refs": ["check:1"]} for row in body["acceptance_criteria"]
            ],
            "end_to_end_result": {
                "status": status,
                "summary": f"The executed refund check exited {code}",
                "evidence_refs": ["check:1"],
                "technical_result": None,
                "pending_human_criteria": [],
            },
        }
    elif stage in ("astra_review", "astra_resolve"):
        validation = data["validation"]
        failed = validation["verdict"] == "FAIL"
        if validation["verdict"] not in ("FAIL", "PASS"):
            raise ValueError("Refund review requires an accepted executed Validator result")
        finding = {
            "id": "",
            "severity": "high",
            "blocking": True,
            "evidence": "event:check",
            "finding": "The executed refund check failed; preserve all tests and repair implementation",
        }
        result = {
            **common,
            "status": "REWORK" if failed else "COMPLETE",
            "acceptance_criteria": [
                {
                    "id": row["id"],
                    "criterion": row["criterion"],
                    "status": "unverified" if failed else "verified",
                    "evidence": "event:check",
                }
                for row in body["acceptance_criteria"]
            ],
            "evidence": ["event:check"],
            "blocker": "",
            "agreed_limitations": [],
            "next_objective": "Repair the refund rules while preserving all unchanged tests" if failed else "",
            "plan": ["Repair only assigned refund implementation", "Execute unchanged refund check"] if failed else [],
            "affected_paths": list(task["affected_paths"]) if failed else [],
            "findings": [finding] if failed and stage == "astra_review" else [],
            "finding_dispositions": dispositions if not failed else [],
            "next_task": {
                "kind": "implement" if failed else "none",
                "milestone_id": task["milestone_id"] if failed else "",
                "requirements": [requirements()[0]["text"]] if failed else [],
                "acceptance_criteria": list(task["acceptance_criteria"]) if failed else [],
                "validation_plan": [config["check"]] if failed else [],
                "findings": [],
            },
        }
        if failed and stage == "astra_review":
            # Schema-valid but incomplete, like completion_rework_incomplete.
            # Existing ordinary Resolver admission supplies the missing plan.
            result["next_task"]["validation_plan"] = []
        if stage == "astra_resolve":
            if not failed:
                raise ValueError("Refund Resolver must receive an actual failing Validator result")
            result["diagnosis"] = _diagnosis()
    else:
        raise ValueError(f"Unsupported refund fault stage: {stage}")
    with trace.open("a") as handle:
        handle.write(
            json.dumps(
                {
                    "stage": stage,
                    "task_id": common["task_id"],
                    "contract_hash": common["contract_hash"],
                    "contract_revision": common["contract_revision"],
                    "source_revision": data["source_revision"],
                    "source_sha256": _source_hashes(paths),
                    "command": config["check"] if code is not None else None,
                    "exit_code": code,
                    "status": result.get("status", result.get("verdict")),
                    "diagnosis": result.get("diagnosis"),
                }
            )
            + "\n"
        )
    return result

"""Script only the bounded-repair fault; source checks and the controller are real."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path


def report_for(stage, data, common, config, run_check, requirements):
    trace = Path(os.environ["SCENARIO_FAKE_CONFIG"]).parent / "rework-trace.jsonl"
    previous = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    task = data["current_task"]
    body = data["goal_contract"]["body"]
    fault = config["fault"]
    dispositions = [{"id": ident, "disposition": "resolved", "evidence": "event:check"}
                    for ident in (data.get("review_identity_policy") or {}).get("own_open_finding_ids", [])]
    finding = {"id": "", "severity": "high", "blocking": True,
               "finding": "The required greeting regression checks fail",
               "evidence": "event:check"}
    code = None
    if stage == "terra":
        attempt = 1 + sum(row["stage"] == "terra" for row in previous)
        broken = attempt == 1 or (fault in ("completion_rework_recurring", "completion_rework_exhausted")
                                  and attempt == 2) or fault == "completion_rework_exhausted"
        if not broken:
            for rel in config["paths"]:
                destination = Path.cwd() / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(config["reference"]) / rel, destination)
        # Even a repeated broken build has a real measured source delta.
        with Path("greet.py").open("a") as handle:
            handle.write("\n# Scripted build " + str(uuid.uuid4()) + "\n")
        code = run_check()
        result = {**common, "summary": "Retain the known defect" if broken else "Apply the repair",
                  "changed_files": config["paths"], "commands_run": [config["check"]],
                  "results": [f"exit {code}"], "remaining_risks": [], "evidence_refs": ["event:check"],
                  "addressed_requirements": task["acceptance_criteria"], "untested_behavior": [],
                  "recommended_checks": [config["check"]]}
    elif stage == "sol":
        code = run_check()
        status = "PASS" if code == 0 else "FAIL"
        result = {**common, "verdict": status, "checks_run": [config["check"]],
                  "checks": [{"command": config["check"], "exit_code": code, "evidence_ref": "event:check"}],
                  "findings": [] if code == 0 else [{**finding,
                      "reproduction_steps": ["Run greet.py with an empty name and a whitespace-only name"],
                      "expected": "All immutable exact-stream and exit checks pass",
                      "actual": f"{config['check']} exited {code}; exact failures are in event:check",
                      "why_it_matters": "The approved invalid-input behavior is missing",
                      "suggested_correction": "Reject empty or whitespace-only names"}],
                  "finding_dispositions": dispositions if code == 0 else [], "unverified_criteria": [],
                  "criterion_results": [{"id": row["id"], "status": status, "evidence_refs": ["check:1"]}
                                        for row in body["acceptance_criteria"]],
                  "end_to_end_result": {"status": status, "summary": f"Real greeting tests exited {code}",
                                        "evidence_refs": ["check:1"]}}
    else:
        failed = data["validation"]["verdict"] == "FAIL"
        result = {**common, "status": "REWORK" if failed else "COMPLETE",
                  "acceptance_criteria": [{"id": row["id"], "criterion": row["criterion"],
                      "status": "unverified" if failed else "verified", "evidence": "event:check"}
                      for row in body["acceptance_criteria"]],
                  "evidence": ["event:check"], "next_objective":
                      "Reject empty and whitespace-only names while retaining exact greeting and usage behavior" if failed else "",
                  "blocker": "", "plan": ["Repair only greet.py", "Execute the unchanged greeting tests"] if failed else [],
                  "affected_paths": list(task["affected_paths"]) if failed else [],
                  "findings": [finding] if failed and stage == "astra_review" else [],
                  "finding_dispositions": dispositions if not failed else [], "agreed_limitations": [],
                  "next_task": {"kind": "implement" if failed else "none",
                      "milestone_id": task["milestone_id"] if failed else "",
                      "requirements": [requirements()[0]["text"]] if failed else [],
                      "acceptance_criteria": list(task["acceptance_criteria"]) if failed else [],
                      "validation_plan": [config["check"]] if failed else [], "findings": []}}
        if failed and stage == "astra_review":
            # Semantically incomplete but schema-valid: this needs the normal Resolver,
            # not a malformed-report correction or an invented direct repair task.
            if fault == "completion_rework_incomplete":
                result["next_task"]["validation_plan"] = []
            if fault == "completion_rework_ambiguous":
                result["next_objective"] = ""
        if stage == "astra_resolve":
            result["diagnosis"] = "The greeting regression command failed; retain the immutable tests, repair the input guard, and rerun all cases"
    row = {"stage": stage, "task_id": common["task_id"], "contract_hash": common["contract_hash"],
           "contract_revision": common["contract_revision"], "source_revision": data["source_revision"],
           "source_sha256": hashlib.sha256(Path("greet.py").read_bytes()).hexdigest(),
           "command": config["check"] if code is not None else None, "exit_code": code,
           "status": result.get("verdict", result.get("status")),
           "validation_task_id": (data.get("validation") or {}).get("task_id"),
           "validation_verdict": (data.get("validation") or {}).get("verdict"),
           "regression_verdict": (data.get("regression_proof") or {}).get("verdict"),
           "next_task": result.get("next_task"), "next_objective": result.get("next_objective")}
    with trace.open("a") as handle:
        handle.write(json.dumps(row) + "\n")
    return result

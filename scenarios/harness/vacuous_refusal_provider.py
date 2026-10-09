"""Script the fault "vacuous_refusal_tests" (feature-stock-refusals): only the model is fake.

The first Builder applies broken/vacuous-refusal-tests (correct stock.py, refusal tests that also pass on the
original code); later Builders apply the chosen solution. AutoCode's runner proves the tests itself
(autocode_regression), and the scripted Completion Owner reads that real proof: REWORK while it is not PASS.
The scripted Resolver writes its diagnosis from its handoff's regression_proof only, which shows the handoff
carries what a real Resolver needs; it says nothing about how well a real model diagnoses.
SCENARIO_FAKE_RESOLVER=misattribute makes it blame stock.py and name no test (the oracle's negative control).
SCENARIO_FAKE_SCOPE_SLIP=retry|pause makes the repair Builder's first attempt also leave stock_current.py, a backup
of stock.py, in the project root, outside its assignment (as a live repair Builder did on 2026-10-06). With retry the
Investigator then recommends one more attempt (``investigate``); with pause it leaves the run to a person.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path


def criteria(reference: Path) -> list[dict]:
    """One test-marked criterion per test_cN_ function in the solution's tests (the plan a Planner writes)."""
    names = re.findall(r"def (test_(c\d+)_\w+)", (reference / "tests" / "test_stock.py").read_text())
    return [
        {
            "id": cid.upper(),
            "criterion": name.removeprefix(f"test_{cid}_").replace("_", " "),
            "verification_method": f"test: {name}",
            "human_review": False,
        }
        for name, cid in names
    ]


def _apply(source: Path, paths):
    for rel in paths:
        if (source / rel).is_file():
            (Path.cwd() / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / rel, Path.cwd() / rel)


def investigate(data):
    """SCENARIO_FAKE_SCOPE_SLIP's Investigator: the strengthened tests are in place; only the backup was wrong."""
    return {
        "diagnosis": "The repair Builder strengthened tests/test_stock.py correctly but also left "
        "stock_current.py, a backup of stock.py, in the project root, outside its assignment.",
        "cause": "stage_output",
        "recommendation": "retry",
        "user_question": "",
        "guidance": "Keep the strengthened tests in tests/test_stock.py. Create no file outside the assigned paths.",
        "evidence_refs": ["tests/test_stock.py"],
        "example": "Given the attempt left stock_current.py in the root, when the runner compared the tree with "
        "the assigned paths, then it rejected the attempt and removed that file.",
        "probe": "grep -q 'invalid choice' tests/test_stock.py",
        "untestable": "",
    }


def report_for(stage, data, common, config, run_check, requirements):
    trace = Path(os.environ["SCENARIO_FAKE_CONFIG"]).parent / "vacuous-trace.jsonl"
    previous = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    task = data["current_task"]
    body = data["goal_contract"]["body"]
    proof = data.get("regression_proof") or {}
    reference = Path(config["reference"])
    scenario = next(parent for parent in reference.parents if (parent / "scenario.toml").is_file())
    # A reviewer closes its own open findings once the runner's proof passes (as completion_rework_provider does).
    dispositions = [
        {"id": ident, "disposition": "resolved", "evidence": "event:check"}
        for ident in (data.get("review_identity_policy") or {}).get("own_open_finding_ids", [])
    ]
    if stage == "terra":
        attempt = 1 + sum(row["stage"] == "terra" for row in previous)
        _apply(scenario / "broken" / "vacuous-refusal-tests" if attempt == 1 else reference, config["paths"])
        if attempt == 2 and os.environ.get("SCENARIO_FAKE_SCOPE_SLIP") in ("retry", "pause"):
            shutil.copy2(Path.cwd() / "stock.py", Path.cwd() / "stock_current.py")
        code = run_check()
        result = {
            **common,
            "summary": f"Scripted build {attempt}",
            "changed_files": config["paths"],
            "commands_run": [config["check"]],
            "results": [f"exit {code}"],
            "remaining_risks": [],
            "evidence_refs": ["event:check"],
            "addressed_requirements": task["acceptance_criteria"],
            "untested_behavior": [],
            "recommended_checks": [config["check"]],
        }
    elif stage == "sol":
        # Like the live Validators of 2026-10-04: the project's tests pass, so PASS; the proof is the runner's.
        code = run_check()
        status = "PASS" if code == 0 else "FAIL"
        result = {
            **common,
            "verdict": status,
            "checks_run": [config["check"]],
            "checks": [{"command": config["check"], "exit_code": None, "evidence_ref": "event:"}],
            "findings": [],
            "finding_dispositions": [],
            "unverified_criteria": [],
            "criterion_results": [
                {"id": row["id"], "status": status, "evidence_refs": ["check:1"]} for row in body["acceptance_criteria"]
            ],
            "end_to_end_result": {
                "status": status,
                "summary": f"{config['check']} exited {code}",
                "evidence_refs": ["check:1"],
                "technical_result": None,
                "pending_human_criteria": [],
            },
        }
    else:
        failed = proof.get("verdict") != "PASS"
        failures = proof.get("failures") or []
        finding = {
            "id": "",
            "severity": "high",
            "blocking": True,
            "evidence": "event:check",
            "finding": "regression_proof is not PASS: " + "; ".join(failures)[:900],
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
            "next_objective": "Make the refusal tests regression_proof lists fail on the original code"
            if failed
            else "",
            "plan": ["Repair tests/test_stock.py", "Let the runner prove the tests again"] if failed else [],
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
        if stage == "astra_resolve":
            cases = {c.lower() for f in failures for c in re.findall(r"^Test case (\w+):", f)}
            vacuous = sorted(
                {
                    t.rsplit(".", 1)[-1]
                    for t in proof.get("pass_to_pass") or []
                    if any(t.rsplit(".", 1)[-1].startswith(f"test_{c}_") for c in cases)
                }
            )
            if os.environ.get("SCENARIO_FAKE_RESOLVER") == "misattribute":
                result["diagnosis"] = "stock.py move and remove do not validate their input; fix the implementation."
                result["next_objective"] = "Make stock.py validate move and remove input"
                result["plan"] = ["Repair stock.py", "Let the runner prove the tests again"]
                result["next_task"]["requirements"] = ["Validate quantities in stock.py"]
            else:
                result["diagnosis"] = (
                    f"{', '.join(vacuous)} pass on the original code too (regression_proof pass_to_pass): there "
                    "`stock.py move`/`remove` is an unknown subcommand, so argparse exits 2 with 'invalid choice' "
                    "and never touches stock.json, which is all these tests assert. stock.py is correct. Repair "
                    "tests/test_stock.py only: assert stderr starts with 'stock.py: ' and has no 'invalid choice'."
                )
                result["next_task"]["requirements"] = [
                    f"In tests/test_stock.py make {name} assert the refusal comes from the command" for name in vacuous
                ]
                result["affected_paths"] = [
                    "tests/test_stock.py"
                ]  # a test repair: the Builder may touch only the tests
    row = {
        "stage": stage,
        "status": result.get("verdict", result.get("status")),
        "regression_verdict": proof.get("verdict"),
        "source_revision": data.get("source_revision"),
    }
    with trace.open("a") as handle:
        handle.write(json.dumps(row) + "\n")
    return result

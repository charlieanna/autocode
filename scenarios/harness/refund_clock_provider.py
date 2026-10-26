"""Script the fault "refund_store_clock" (feature-refund-window): only the model is fake (#628).

The plan names each reference test as a ``test:`` criterion, as a Planner writes them. The first
Builder applies broken/trusts-store-date: refunds built on the seed's store_date(), which reads
the date in UTC and ignores the store's UTC-8 offset, with tests that never cross a store-day
boundary. The project's own tests pass, so the Validator passes; AutoCode's runner proves the named
tests itself (autocode_regression), and the scripted Completion Owner reads that real proof: REWORK
while it is not PASS. A Validator PASS keeps the rework off the direct Builder route (#294), so it
reaches AutoResolver, the stage this scenario exists to cover. The scripted Resolver writes its
diagnosis from what it is handed, the proof and shop/clock.py; it says nothing about how well a
real model diagnoses. Later Builders apply the chosen solution.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

# The planted defect: the store's offset is declared, and the date is still read in UTC.
UTC_DATE = re.compile(r"fromtimestamp\([^)]*tz\s*=\s*(?:UTC|timezone\.utc|datetime\.UTC)\s*\)")


def criteria(reference: Path) -> list[dict]:
    """One test-marked criterion per test in the solution's refund tests (the plan a Planner writes)."""
    names = re.findall(r"def (test_\w+)", (reference / "tests" / "test_refunds.py").read_text())
    return [
        {
            "id": f"C{index}",
            "criterion": name.removeprefix("test_").replace("_", " "),
            "verification_method": f"test: {name}",
            "human_review": False,
        }
        for index, name in enumerate(names, 1)
    ]


def _apply(source: Path, paths):
    for rel in paths:
        if (source / rel).is_file():
            (Path.cwd() / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / rel, Path.cwd() / rel)


def diagnose(clock_source: str, failures: list[str]) -> tuple[str, list[str]]:
    """(diagnosis, affected paths) from the clock module the Resolver can read and the proof's failures."""
    named = "; ".join(failures)[:600]
    if UTC_DATE.search(clock_source) and "STORE_UTC_OFFSET_HOURS = -8" in clock_source:
        diagnosis = (
            "shop/clock.py store_date() reads the calendar date in UTC (tz=UTC) and ignores "
            "STORE_UTC_OFFSET_HOURS = -8, so refunds.py counts UTC days instead of store days: an order "
            "delivered late in a store day lands on the next UTC date and the 30-day window ends a store "
            "day late. The regression proof also lacks the store-day window tests the plan names: "
            f"{named}. Make store_date() use the store's UTC-8 offset and add the named tests."
        )
        if os.environ.get("SCENARIO_FAKE_REFUND_DIAGNOSIS") == "vague":
            diagnosis = "The implementation has a bug; fix it and rerun the tests."
        return diagnosis, ["shop/clock.py", "shop/refunds.py", "tests/test_refunds.py"]
    return (
        "The regression proof is not PASS: " + named + ". Add the tests the plan names.",
        ["tests/test_refunds.py"],
    )


def classify(data):
    """The Investigator's classification of the failed first build, citing the pinned failure (the rejected
    report and the source snapshot) and a read-only probe that shows the defect on the current code."""
    import shlex
    import sys

    failure = data["builder_failure"]
    record = failure["record"]
    refs = [record["output"], record["after_ref"]]
    if not failure["failure_id"] or not set(refs) <= set(failure["evidence_refs"]):
        raise SystemExit("refund_clock_provider: classification lacks pinned report/source evidence")
    rejected = json.loads(Path(refs[0]).read_text())
    findings = " ".join(row["finding"] for row in rejected.get("findings") or [])
    names = sorted(set(re.findall(r"has no test named (test_\w+)", findings)))
    if rejected.get("status") != "REWORK" or "regression_proof is not PASS:" not in findings or not names:
        raise SystemExit("refund_clock_provider: classification needs an actual failed named-test proof")
    binding = {key: failure["binding"][key] for key in ("task_id", "contract_hash", "contract_revision")}
    # 2026-03-02 00:00 UTC is still March 1 in store time (UTC-8); the planted store_date() says March 2.
    code = f"""import hashlib, json, re
from datetime import date
from pathlib import Path
r = json.loads(Path({"run/" + Path(refs[0]).name!r}).read_text())
s = json.loads(Path({"run/" + Path(refs[1]).name!r}).read_text())
assert r['status'] == 'REWORK'
assert {{k: r[k] for k in ('task_id', 'contract_hash', 'contract_revision')}} == {binding!r}
assert s['revision'] == {record["source_revision"]!r}
for path in ('shop/clock.py', 'shop/refunds.py', 'tests/test_refunds.py'):
    if path in s['files']:
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == s['files'][path]
findings = ' '.join(row['finding'] for row in r['findings'])
assert sorted(set(re.findall(r'has no test named (test_\\w+)', findings))) == {names!r}
from shop.clock import STORE_UTC_OFFSET_HOURS, store_date
assert STORE_UTC_OFFSET_HOURS == -8
assert store_date(1772409600) == date(2026, 3, 2)
"""
    return {
        "diagnosis": "The failed regression proof names "
        + ", ".join(names)
        + ". shop/clock.py store_date() reads the date in UTC and ignores STORE_UTC_OFFSET_HOURS = -8, and "
        "refunds.py builds its 30-day window on it, so the window counts UTC days, not store days. This is an "
        "implementation defect in shop/clock.py and the refund tests, not a new approach or permission.",
        "cause": "stage_output",
        "guidance": "Make store_date() return the store's calendar date at UTC-8, keep refunds.py on it, and add "
        "the named store-day window tests; let the runner re-prove them.",
        "recommendation": "retry",
        "user_question": "",
        "evidence_refs": refs,
        "example": "Given an order delivered at 2026-03-02 00:00 UTC (March 1 in store time), store_date() "
        "returns March 2, so the refund window ends a store day late.",
        "probe": shlex.join([sys.executable, "-B", "-c", code]),
        # The probe must fail once the cited report is no longer a REWORK (#1769).
        "counterexample": {
            "file": "run/" + Path(refs[0]).name,
            "find": (text := Path(refs[0]).read_text()),
            "replace": json.dumps({**json.loads(text), "status": "PASS"}),
        },
        "untestable": "",
        "failure_class": "execution",
        "failure_id": failure["failure_id"],
    }


def report_for(stage, data, common, config, run_check, requirements):
    trace = Path(os.environ["SCENARIO_FAKE_CONFIG"]).parent / "refund-trace.jsonl"
    previous = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    task = data["current_task"]
    body = data["goal_contract"]["body"]
    proof = data.get("regression_proof") or {}
    reference = Path(config["reference"])
    scenario = next(parent for parent in reference.parents if (parent / "scenario.toml").is_file())
    # A reviewer closes its own open findings once the runner's proof passes.
    dispositions = [
        {"id": ident, "disposition": "resolved", "evidence": "event:check"}
        for ident in (data.get("review_identity_policy") or {}).get("own_open_finding_ids", [])
    ]
    if stage == "terra":
        attempt = 1 + sum(row["stage"] == "terra" for row in previous)
        _apply(scenario / "broken" / "trusts-store-date" if attempt == 1 else reference, config["paths"])
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
        # The project's tests pass on the first build, so PASS; the named-test proof is the runner's.
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
        failures = [*(proof.get("failures") or []), *(proof.get("unverified") or [])]
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
            "next_objective": "Make the tests regression_proof names exist and pass" if failed else "",
            "plan": ["Repair the refund window", "Let the runner prove the named tests again"] if failed else [],
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
            clock = Path.cwd() / "shop" / "clock.py"
            diagnosis, paths = diagnose(clock.read_text() if clock.is_file() else "", failures)
            result["diagnosis"] = diagnosis
            result["next_objective"] = "Count the refund window in store days (UTC-8)"
            result["next_task"]["requirements"] = [
                "shop/clock.py store_date() returns the store's calendar date at UTC-8, and the plan's named "
                "refund tests exist and pass"
            ]
            result["affected_paths"] = paths
    row = {
        "stage": stage,
        "status": result.get("verdict", result.get("status")),
        "regression_verdict": proof.get("verdict"),
        "source_revision": data.get("source_revision"),
    }
    with trace.open("a") as handle:
        handle.write(json.dumps(row) + "\n")
    return result

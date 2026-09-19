#!/usr/bin/env python3
"""Deterministic offline provider for end-to-end tests; never contacts a model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from goal_fixtures import body

if sys.argv[1:] == ["login", "status"]:
    print("Logged in using ChatGPT (offline fixture)")
    raise SystemExit(0)

data = json.loads(sys.stdin.read().split("CURRENT HANDOFF DATA\n", 1)[1])
stage = data["stage"]
mode = os.environ.get("AUTOCODE_FIXTURE_MODE", "standard")
contract = data["goal_contract"]
common = {"contract_revision": contract["revision"], "contract_hash": contract["hash"],
          "task_id": (data.get("current_task") or {}).get("id", ""),
          "deferred_backlog": ["Optional web UI"], "user_request": {"kind": "none", "discovered": "", "impact": "",
              "decision_needed": "", "options": [], "proposed_delta": ""}}
session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
if os.environ.get("AUTOCODE_FIXTURE_SESSION_DRIFT"):
    session = str(uuid.uuid4())
print(json.dumps({"type": "thread.started", "thread_id": session}))
if stage == "astra_discovery":
    draft = body(questions=not data["saved_answers"], human=mode == "standard")
    if data["saved_answers"]:
        draft["accepted_assumptions"] = [{"text": "User selected CLI", "basis": "user_answer", "answer_id": "Q1"}]
    for feedback in data["brief_feedback"]:
        draft["constraints"].append(feedback["text"])
        draft["accepted_assumptions"].append({"text": feedback["text"], "basis": "user_feedback", "answer_id": feedback["id"]})
    result = {"contract": draft, "summary": "Build a small local greeting CLI with a clear invalid-input failure"}
elif stage.startswith("astra"):
    rework = data.get("validation", {}).get("verdict") == "FAIL"
    complete = stage == "astra_review" and not rework
    result = {**common, "status": "COMPLETE" if complete else "REWORK" if rework else "CONTINUE",
              "acceptance_criteria": [{**c, "status": "verified" if complete else "unverified",
                                       "evidence": "Sol executed both CLI cases"} for c in data["acceptance_criteria"]],
              "next_objective": "" if complete else "Implement a greeting CLI and reject empty input",
              "next_task": {"kind": "none" if complete else "implement", "milestone_id": "" if complete else "M1",
                            "requirements": [] if complete else ["Print a greeting for valid input and reject empty input"],
                            "acceptance_criteria": [] if complete else ["C1"],
                            "validation_plan": [] if complete else ["Run greet.py with Ada and an empty name"]},
              "agreed_limitations": ["Local command-line use only"] if complete else [],
              "evidence": ["Sol events"], "blocker": "",
              "plan": ["Implement greeting", "Run both cases"], "affected_paths": ["greet.py"]}
elif stage == "terra":
    if mode == "rework" and not data["actionable_findings"]:
        Path("greet.py").write_text("import sys\nprint('Hello, ' + sys.argv[1])\n")
    else:
        Path("greet.py").write_text("import sys\nif len(sys.argv) != 2 or not sys.argv[1].strip():\n    raise SystemExit(2)\nprint('Hello, ' + sys.argv[1])\n")
    result = {**common, "summary": "Greeting written", "changed_files": ["greet.py"], "commands_run": [],
              "results": ["Written"], "remaining_risks": [], "evidence_refs": ["greet.py"],
              "addressed_requirements": data["current_task"]["requirements"], "untested_behavior": ["CLI execution"],
              "recommended_checks": ["Execute valid and invalid input"]}
else:
    valid = subprocess.run([sys.executable, "greet.py", "Ada"], capture_output=True, text=True)
    invalid = subprocess.run([sys.executable, "greet.py", ""], capture_output=True, text=True)
    passed = valid.returncode == 0 and valid.stdout == "Hello, Ada\n" and invalid.returncode == 2
    command = "fixture: execute greet.py with Ada and empty name"
    print(json.dumps({"type": "item.completed", "item": {"id": "check", "type": "command_execution",
        "command": command, "exit_code": 0 if passed else 1,
        "aggregated_output": json.dumps({"valid": [valid.returncode, valid.stdout], "invalid": invalid.returncode})}}))
    findings = [] if passed else [{"severity": "high", "blocking": True, "finding": "Empty names are accepted",
        "evidence": "event:check", "reproduction_steps": ["Run greet.py with an empty argument"],
        "expected": "Exit 2", "actual": f"Exit {invalid.returncode}", "why_it_matters": "Required invalid-input behavior",
        "suggested_correction": "Reject an empty or whitespace-only name"}]
    result = {**common, "verdict": "PASS" if passed else "FAIL", "findings": findings, "checks_run": [command],
              "unverified_criteria": [], "checks": [{"command": command, "exit_code": 0 if passed else 1, "evidence_ref": "event:check"}],
              "end_to_end_result": {"status": "PASS" if passed else "FAIL", "summary": "Executed both CLI user flows",
                                    "evidence_refs": ["event:check"]},
              "criterion_results": [{"id": "C1", "status": "PASS" if passed else "FAIL", "evidence_refs": ["event:check"]}]}
Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(result))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}}))

#!/usr/bin/env python3
"""Offline config-tool fixture; never calls a model.

By default it writes the report file Autocode names. With --events it prints
OpenCode-format JSON events instead, as ``kilo run --format json`` does, and
reuses the session passed with --session.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import uuid
from goal_fixtures import body


def report_path():
    return Path(sys.argv[sys.argv.index("--report") + 1])


def argument(name):
    return Path(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else None


if "--version" in sys.argv:
    print("fixture-tool 1")
    raise SystemExit(0)

EVENTS = "--events" in sys.argv
SESSION = sys.argv[sys.argv.index("--session") + 1] if "--session" in sys.argv else "ses_" + uuid.uuid4().hex
MESSAGE = "msg_" + uuid.uuid4().hex


def event(kind, **part):
    print(json.dumps({"type": kind, "sessionID": SESSION, "part": {
        "id": "prt_" + uuid.uuid4().hex, "sessionID": SESSION, "messageID": MESSAGE, **part}}), flush=True)


def tool_event(part_id, command, code, output):
    print(json.dumps({"type": "tool_use", "sessionID": SESSION, "part": {
        "id": part_id, "sessionID": SESSION, "messageID": MESSAGE, "tool": "bash",
        "state": {"status": "completed", "input": {"command": command}, "output": output,
                  "metadata": {"exit": code}}}}), flush=True)


def deliver(value):
    if not EVENTS:
        report_path().write_text(json.dumps(value))
        return
    event("text", text=json.dumps(value))
    event("step_finish", reason="stop", tokens={"input": 100, "output": 20, "reasoning": 0,
                                                "cache": {"read": 40, "write": 0}})


sessions_log = argument("--sessions-log")
if sessions_log:
    with sessions_log.open("a") as handle:
        handle.write(json.dumps({"role": sys.argv[sys.argv.index("--role") + 1] if "--role" in sys.argv else "",
                                 "resumed": "--session" in sys.argv, "session": SESSION}) + "\n")
if EVENTS:
    event("step_start")

prompt_file = argument("--prompt-file")
stdin = sys.stdin.read()
prompt = prompt_file.read_text() if prompt_file else stdin
observation = argument("--stdin-observation")
if observation:
    observation.write_text(json.dumps({"stdin": stdin, "prompt": prompt}))
invocations = argument("--invocations")
data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
if invocations:
    with invocations.open("a") as handle:
        handle.write(data.get("stage", "report_repair") + "\n")
if data.get("report_repair"):
    original = data["original"]
    deliver({"summary": "Repaired report", "changed_files": ["greet.py"], "commands_run": [],
                                         "results": ["Repaired"], "remaining_risks": [], "evidence_refs": ["greet.py"],
                                         "contract_revision": original.get("contract_revision"),
                                         "contract_hash": original.get("contract_hash"),
                                         "task_id": original.get("task_id", ""),
                                         "user_request": {"kind": "none", "discovered": "", "impact": "",
                                                          "decision_needed": "", "options": [], "proposed_delta": ""},
                                         "deferred_backlog": [], "addressed_requirements": [],
                                         "untested_behavior": [], "recommended_checks": []})
    raise SystemExit(0)
stage = data.get("stage", "terra")
contract = data["goal_contract"] or {"revision": 0, "hash": ""}
common = {"contract_revision": contract["revision"], "contract_hash": contract["hash"],
          "task_id": (data.get("current_task") or {}).get("id", ""),
          "deferred_backlog": ["Optional web UI"], "user_request": {"kind": "none", "discovered": "", "impact": "",
              "decision_needed": "", "options": [], "proposed_delta": ""}}
if stage == "requirements_gather":
    draft = body(questions=not data["saved_answers"], human=False)
    result = {
        "summary": "Requirements for the local greeting CLI, without a plan",
        "intended_outcome": draft["intended_outcome"],
        "required_behaviors": draft["required_behaviors"],
        "constraints": draft["constraints"],
        "acceptance_tests": ["Valid and invalid CLI input have the requested outcomes"],
        "source_refs": ["task"],
        "proposed_assumptions": ["Use a local CLI if the user chooses that interface"],
        "open_questions": draft["open_blocking_questions"],
    }
elif stage == "astra_discovery":
    draft = body(questions=not data["saved_answers"], human=False)
    if data["saved_answers"]:
        draft["accepted_assumptions"] = [{"text": "User selected CLI", "basis": "user_answer", "answer_id": "Q1"}]
    result = {"contract": draft, "summary": "Build a small local greeting CLI with a clear invalid-input failure",
              "code_refs": ["goal_contract.body"], "alternatives": ["A web endpoint would need deployment"], "uncertainties": []}
elif stage == "astra_challenge":
    result = {"summary": "Check whitespace-only input", "concerns": [{"id": "P1", "concern": "Empty includes whitespace",
        "evidence_refs": ["goal_contract.body.important_failure_cases"], "requested_change": "Specify whitespace rejection",
        "acceptance_test": "Whitespace input exits 2", "blocking": True}]}
elif stage == "glm_revise":
    draft = dict(contract["body"])
    draft.pop("initial_task", None)
    draft["important_failure_cases"] = [*draft["important_failure_cases"], "Reject whitespace-only input"]
    result = {"contract": draft, "summary": "Added whitespace case", "code_refs": ["goal_contract.body"],
        "responses": [{"concern_id": "P1", "response": "Whitespace is invalid", "evidence_refs": ["goal_contract.body"],
                       "change": "Added whitespace case", "acceptance_test": "Whitespace input exits 2"}]}
elif stage == "astra_finalize":
    draft = dict(contract["body"])
    draft["initial_task"] = {"objective": "Implement greeting CLI", "affected_paths": ["greet.py"],
        "kind": "implement", "milestone_id": "M1", "requirements": ["Greet names; reject empty/whitespace input"],
        "acceptance_criteria": ["C1"], "validation_plan": ["Execute valid, empty and whitespace input"]}
    result = {"contract": draft, "summary": "Ready for approval",
        "decisions": [{"concern_id": "P1", "decision": "Reject whitespace",
            "rationale": "Consistent invalid-input contract", "acceptance_test": "Whitespace input exits 2", "resolved": True}]}
elif stage == "terra":
    Path("greet.py").write_text("import sys\nif len(sys.argv) != 2 or not sys.argv[1].strip():\n    raise SystemExit(2)\nprint('Hello, ' + sys.argv[1])\n")
    result = {**common, "summary": "Greeting written", "changed_files": ["greet.py"], "commands_run": [],
              "results": ["Written"], "remaining_risks": [], "evidence_refs": ["greet.py"],
              "addressed_requirements": data["current_task"]["requirements"], "untested_behavior": ["CLI execution"],
              "recommended_checks": ["Execute valid and invalid input"]}
elif stage == "sol":
    valid_cmd = [sys.executable, "greet.py", "Ada"]
    invalid_cmd = [sys.executable, "greet.py", ""]
    valid = subprocess.run(valid_cmd, capture_output=True, text=True)
    invalid = subprocess.run(invalid_cmd, capture_output=True, text=True)
    passed = valid.returncode == 0 and valid.stdout == "Hello, Ada\n" and invalid.returncode == 2
    command = shlex.join(valid_cmd)
    if EVENTS:
        tool_event("prt_check", command, 0 if passed else 1, valid.stdout)
        evidence = "event:prt_check"
    else:
        evidence = Path(".autocode/evidence/sol-greet.json").resolve()
        captured = subprocess.run(shlex.split(data["capture_command"]) + ["--output", str(evidence), "--", *valid_cmd],
                                  capture_output=True, text=True)
        if captured.returncode:
            raise SystemExit(captured.returncode)
    result = {**common, "verdict": "PASS" if passed else "FAIL", "findings": [], "checks_run": [command],
              "unverified_criteria": [], "checks": [{"command": command, "exit_code": 0 if passed else 1, "evidence_ref": str(evidence)}],
              "end_to_end_result": {"status": "PASS" if passed else "FAIL", "summary": "Executed the greeting CLI",
                                    "evidence_refs": [str(evidence)]},
              "criterion_results": [{"id": "C1", "status": "PASS" if passed else "FAIL", "evidence_refs": [str(evidence)]}],
              "finding_dispositions": []}
else:
    complete = stage == "astra_review"
    result = {**common, "status": "COMPLETE" if complete else "CONTINUE",
              "acceptance_criteria": [{**c, "status": "verified" if complete else "unverified",
                                       "evidence": "Sol receipt executed the greeting CLI"} for c in data["acceptance_criteria"]],
              "next_objective": "" if complete else "Implement a greeting CLI and reject empty input",
              "next_task": {"kind": "none" if complete else "implement", "milestone_id": "" if complete else "M1",
                            "requirements": [] if complete else ["Print a greeting for valid input and reject empty input"],
                            "acceptance_criteria": [] if complete else ["C1"],
                            "validation_plan": [] if complete else ["Run greet.py with Ada and an empty name"],
                            "findings": []},
              "findings": [], "finding_dispositions": [], "agreed_limitations": ["Local command-line use only"] if complete else [],
              "evidence": ["Sol receipt"], "blocker": "",
              "plan": ["Implement greeting", "Run both cases"], "affected_paths": ["greet.py"]}

if stage == 'sol' and os.environ.get('AUTOCODE_FIXTURE_MISSING_CHECK_EXIT'):
    result['checks'][0].pop('exit_code')
deliver(result)
if os.environ.get("AUTOCODE_FIXTURE_SKIP_REPORT") and not EVENTS:
    report_path().unlink()

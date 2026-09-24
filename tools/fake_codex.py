#!/usr/bin/env python3
"""Deterministic offline provider for end-to-end tests; never contacts a model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from goal_fixtures import body


def finding_id(source, text):
    import hashlib
    return "F-" + hashlib.sha256(json.dumps({"source": source, "finding": " ".join(str(text).split()).lower()},
                                             sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:10]

if sys.argv[1:] == ["login", "status"]:
    print("Logged in using ChatGPT (offline fixture)")
    raise SystemExit(0)

data = json.loads(sys.stdin.read().split("CURRENT HANDOFF DATA\n", 1)[1])
if data.get('report_repair'):
    # This branch only reformats a saved report; never executes the original task.
    result = json.loads(Path(data['original']['output']).read_text())
    if 'summary' not in result and data['original'].get('stage', '').startswith(('terra', 'astra_discovery')):
        result['summary'] = 'Repaired fixture report'
    session = str(uuid.uuid4())
    print(json.dumps({'type': 'thread.started', 'thread_id': session}))
    Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(result))
    print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 20, 'output_tokens': 10}}))
    raise SystemExit(0)
stage = data["stage"]
mode = os.environ.get("AUTOCODE_FIXTURE_MODE", "standard")
contract = data["goal_contract"] or {"revision": 0, "hash": ""}
probe = os.environ.get("AUTOCODE_REGISTRY_LAUNCH_PROBE")
if probe:
    registry_path = Path(os.environ["AUTOCODE_HOME"]) / "registry.json"
    try:
        registry = json.loads(registry_path.read_text())
        observed = sorted(item["run_dir"] for item in registry.get("runs", {}).values())
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        observed = {"error": str(error)}
    with Path(probe).open("a") as stream:
        stream.write(json.dumps({"stage": stage, "runs": observed}) + "\n")
common = {"contract_revision": contract["revision"], "contract_hash": contract["hash"],
          "task_id": (data.get("current_task") or {}).get("id", ""),
          "deferred_backlog": ["Optional web UI"], "user_request": {"kind": "none", "discovered": "", "impact": "",
              "decision_needed": "", "options": [], "proposed_delta": ""}}
session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
if os.environ.get("AUTOCODE_FIXTURE_SESSION_DRIFT"):
    session = str(uuid.uuid4())
print(json.dumps({"type": "thread.started", "thread_id": session}))
if os.environ.get("AUTOCODE_FIXTURE_QUOTA_STAGE") == stage:
    print(json.dumps({"type": "error", "error": {"message": "subscription usage limit reached"}}))
    raise SystemExit(3)
if stage == "requirements_gather":
    draft = body(questions=not data["saved_answers"])
    result = {
        "summary": "Requirements for a local greeting CLI, without an implementation plan",
        "intended_outcome": draft["intended_outcome"],
        "required_behaviors": draft["required_behaviors"],
        "constraints": draft["constraints"],
        "acceptance_tests": ["Valid and invalid CLI input have the requested outcomes"],
        "source_refs": ["task"],
        "proposed_assumptions": ["Use a local CLI if the user chooses that interface"],
        "open_questions": draft["open_blocking_questions"],
    }
elif stage == "astra_discovery":
    draft = body(questions=not data["saved_answers"], human=mode == "standard")
    if mode == "milestones":
        draft['acceptance_criteria'].append({'id': 'C2', 'criterion': 'Goodbye CLI prints Goodbye, NAME',
            'verification_method': 'Execute bye.py with Ada', 'human_review': False})
        draft['milestones'].append({'id': 'M2', 'objective': 'Deliver goodbye CLI',
                                    'acceptance_criteria': ['C2'], 'depends_on': ['M1'], 'affected_paths': ['goodbye.py']})
        draft['deliverables'].append('bye.py')
        draft['required_behaviors'].append('Print Goodbye, NAME from bye.py')
    if data["saved_answers"]:
        draft["accepted_assumptions"] = [{"text": "User selected CLI", "basis": "user_answer", "answer_id": "Q1"}]
    for feedback in data["brief_feedback"]:
        draft["constraints"].append(feedback["text"])
        draft["accepted_assumptions"].append({"text": feedback["text"], "basis": "user_feedback", "answer_id": feedback["id"]})
    result = {"contract": draft, "summary": "Build a small local greeting CLI with a clear invalid-input failure"}
    if data.get("joint_planning"):
        result.update(code_refs=["goal_contract.body"], alternatives=["A web endpoint would need deployment"], uncertainties=[])
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
    blocked = mode == "planning-blocked"
    if blocked:
        draft["open_blocking_questions"] = [{"id": "P2", "question": "Should whitespace be rejected?",
            "why": "Unresolved input semantics", "options": ["Reject", "Accept"], "proposed_default": ""}]
    result = {"contract": draft, "summary": "Ready for approval" if not blocked else "User decision required",
        "decisions": [{"concern_id": "P1", "decision": "Reject whitespace" if not blocked else "Ask the user",
            "rationale": "Consistent invalid-input contract", "acceptance_test": "Whitespace input exits 2", "resolved": not blocked}]}
    if mode == "planning-invalid":
        result["decisions"] = []
elif stage.startswith("astra") and stage != "astra_checkpoint":
    rework = data.get("validation", {}).get("verdict") == "FAIL"
    complete = stage == "astra_review" and not rework
    advance = mode == 'milestones' and complete and data['current_task']['milestone_id'] == 'M1'
    if advance:
        complete = False
    result = {**common, "status": "COMPLETE" if complete else "REWORK" if rework else "CONTINUE",
              "acceptance_criteria": [{**c, "status": "verified" if complete else "unverified",
                                       "evidence": "Sol executed both CLI cases"} for c in data["acceptance_criteria"]],
              "next_objective": "" if complete else "Implement a greeting CLI and reject empty input",
              "next_task": {"kind": "none" if complete else "implement", "milestone_id": "" if complete else "M1",
                            "requirements": [] if complete else ["Print a greeting for valid input and reject empty input"],
                            "acceptance_criteria": [] if complete else ["C1"],
                            "validation_plan": [] if complete else ["Run greet.py with Ada and an empty name"],
                            "findings": []},
              "findings": [{"severity": "high", "finding": "Empty names are accepted by greet.py",
                            "evidence": "Sol events", "blocking": True}] if rework else [],
              "agreed_limitations": ["Local command-line use only"] if complete else [],
              "finding_dispositions": ([{"id": finding_id("astra", "Empty names are accepted by greet.py"),
                                          "disposition": "resolved", "evidence": "Sol events"}] if complete else []),
              "evidence": ["Sol events"], "blocker": "",
              "plan": ["Implement greeting", "Run both cases"], "affected_paths": ["greet.py"]}
    if advance:
        result.update(next_objective='Implement goodbye CLI', affected_paths=['bye.py'])
        result['next_task'].update(milestone_id='M2', requirements=['Print Goodbye, NAME'],
                                  acceptance_criteria=['C2'], validation_plan=['Execute both greeting and goodbye'])
    if mode == 'stalled' and ((data.get('milestone_checkpoint') or {}).get('current') or {}).get('needs_replan'):
        result['next_objective'] = 'Isolate empty input with a focused reproduction before repair'
    if stage == 'astra_resolve':
        result['diagnosis'] = 'Empty names are accepted by the CLI; add input validation and retest both cases.'
elif stage == "terra":
    if mode == 'stalled':
        Path('greet.py').write_text("import sys\nprint('Hello, ' + sys.argv[1])\n# attempt " + str(uuid.uuid4()) + '\n')
    elif mode == "rework" and not data["actionable_findings"]:
        Path("greet.py").write_text("import sys\nprint('Hello, ' + sys.argv[1])\n")
    else:
        Path("greet.py").write_text("import sys\nif len(sys.argv) != 2 or not sys.argv[1].strip():\n    raise SystemExit(2)\nprint('Hello, ' + sys.argv[1])\n")
    result = {**common, "summary": "Greeting written", "changed_files": ["greet.py"], "commands_run": [],
              "results": ["Written"], "remaining_risks": [], "evidence_refs": ["greet.py"],
              "addressed_requirements": data["current_task"]["requirements"], "untested_behavior": ["CLI execution"],
              "recommended_checks": ["Execute valid and invalid input"]}
    if mode == 'milestones' and data['current_task']['milestone_id'] == 'M2':
        Path('bye.py').write_text("import sys\nprint('Goodbye, ' + sys.argv[1])\n")
        result.update(changed_files=['bye.py'], evidence_refs=['bye.py'])
else:
    valid = subprocess.run([sys.executable, "greet.py", "Ada"], capture_output=True, text=True)
    invalid = subprocess.run([sys.executable, "greet.py", ""], capture_output=True, text=True)
    passed = valid.returncode == 0 and valid.stdout == "Hello, Ada\n" and invalid.returncode == 2
    goodbye_passed = False
    if mode == 'milestones' and data['current_task']['milestone_id'] == 'M2':
        goodbye = subprocess.run([sys.executable, 'bye.py', 'Ada'], capture_output=True, text=True)
        goodbye_passed = goodbye.returncode == 0 and goodbye.stdout == 'Goodbye, Ada\n'
        passed = passed and goodbye_passed
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
              "criterion_results": [{"id": "C1", "status": "PASS" if passed else "FAIL", "evidence_refs": ["event:check"]}],
              "finding_dispositions": ([{"id": finding_id("sol", "Empty names are accepted"), "disposition": "resolved",
                                          "evidence": "event:check"}] if passed else [])}
    if mode == 'milestones':
        result['criterion_results'].append({'id': 'C2', 'status': 'PASS' if goodbye_passed else 'NOT_VERIFIED',
                                           'evidence_refs': ['event:check'] if goodbye_passed else []})
    if stage == "astra_checkpoint":
        result = {"validation": result, "consult_sol": {"requested": False, "question": "", "reason": ""},
            "decision": {**common, "status": "COMPLETE" if passed else "REWORK",
                "acceptance_criteria": [{**c, "status": "verified" if passed else "unverified",
                    "evidence": "Independent Astra executed CLI cases; event:check"} for c in data["acceptance_criteria"]],
                "next_objective": "" if passed else "Reject empty input and rerun the CLI tests",
                "next_task": {"kind": "none" if passed else "implement", "milestone_id": "" if passed else "M1",
                    "requirements": [] if passed else ["Reject empty input"],
                    "acceptance_criteria": [] if passed else ["C1"],
                    "validation_plan": [] if passed else ["Execute valid and empty input"], "findings": []},
                "findings": [], "finding_dispositions": [], "agreed_limitations": [], "evidence": ["event:check"], "blocker": "",
                "plan": ["Implement and independently verify"], "affected_paths": ["greet.py"]}}
if os.environ.get('AUTOCODE_FIXTURE_REPORT_REPAIR_STAGE') == stage:
    result.pop('summary', None)
if stage=='terra' and (data.get('workflow') or {}).get('mode')=='glm_final_audit_v2':
    valid=subprocess.run([sys.executable,'greet.py','Ada'],capture_output=True,text=True)
    invalid=subprocess.run([sys.executable,'greet.py',''],capture_output=True,text=True)
    passed=valid.returncode==0 and valid.stdout=='Hello, Ada\n' and invalid.returncode==2
    command='fixture: GLM tests valid and invalid greetings'
    print(json.dumps({'type':'item.completed','item':{'id':'self-check','type':'command_execution',
        'command':command,'exit_code':0 if passed else 1,'aggregated_output':'fixture checks'}}))
    assessment={**common,'verdict':'PASS' if passed else 'FAIL','findings':[],'finding_dispositions':[],'checks_run':[command],
        'unverified_criteria':[], 'checks':[{'command':command,'exit_code':0 if passed else 1,'evidence_ref':'event:self-check'}],
        'end_to_end_result':{'status':'PASS' if passed else 'FAIL','summary':'GLM self-check','evidence_refs':['event:self-check']},
        'criterion_results':[{'id':'C1','status':'PASS' if passed else 'FAIL','evidence_refs':['event:self-check']}]}
    second=data.get('next_action')=='Second GLM batch'
    consulted=bool(data.get('consultation_reports'))
    action=('REQUEST_FINAL_AUDIT' if consulted else 'ESCALATE_SOL') if mode=='sol-escalation' else ('REQUEST_FINAL_AUDIT' if second else 'CONTINUE')
    result['untested_behavior']=[]
    result['continuation']={'action':action,'plan':['Finish approved greeting'],
        'next_task':{'kind':'implement','objective':'Second GLM batch','affected_paths':['greet.py'],
            'milestone_id':'M1','requirements':['Verify the final greeting'],'acceptance_criteria':['C1'],
            'validation_plan':['Run valid and invalid CLI cases']},
        'reason':'Fixture-specific debugging question' if action=='ESCALATE_SOL' else 'Continue approved work',
        'question':'Check the empty input boundary' if action=='ESCALATE_SOL' else '',
        'self_assessment':assessment}
Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(result))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}}))

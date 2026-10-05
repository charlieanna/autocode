#!/usr/bin/env python3
"""Deterministic offline provider for end-to-end tests; never contacts a model."""
import json
import shlex
import os
from pathlib import Path
import subprocess
import sys
import uuid
from goal_fixtures import body


if sys.argv[1:] == ["login", "status"]:
    print("Logged in using ChatGPT (offline fixture)")
    raise SystemExit(0)

prompt = sys.stdin.read()
data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
if 'acceptance_criteria_ref' in data:
    data['acceptance_criteria'] = [{k: c[k] for k in ('id', 'criterion')}
                                  for c in data['goal_contract']['body']['acceptance_criteria']]


def open_finding_id(source, text):
    for row in data.get("open_findings") or []:
        if row.get("source") == source and row.get("finding") == text:
            return row["id"]
    return None

def record_launch(stage):
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

if data.get('report_repair'):
    record_launch(data['original']['stage'] + '_report_repair')
    # This branch only reformats a saved report; never executes the original task.
    if data['original'].get('truncated_output'):
        result = json.loads(data['rejected_report']['content']['partial_text'] + '}')
    else:
        result = json.loads(Path(data['original']['output']).read_text())
    if 'summary' not in result and data['original'].get('stage', '').startswith(('terra', 'astra_discovery')):
        result['summary'] = 'Repaired fixture report'
    if os.environ.get("AUTOCODE_FIXTURE_MODE") == "human-pending":
        original_stage = data['original'].get('stage')
        if original_stage == "astra_review":
            for row in result["acceptance_criteria"]:
                if not row["evidence"].strip():
                    row["evidence"] = "Current Validator executed CLI checks; human acceptance is still pending"
        elif original_stage == "sol" and not result["checks"]:
            original_events = [json.loads(line) for line in Path(data['original']['events']).read_text().splitlines()]
            result["checks"] = [{"command": event["item"]["command"], "exit_code": event["item"]["exit_code"],
                                 "evidence_ref": "event:" + event["item"]["id"]}
                                for event in original_events if event.get("type") == "item.completed"
                                and event.get("item", {}).get("type") == "command_execution"]
    session = str(uuid.uuid4())
    print(json.dumps({'type': 'thread.started', 'thread_id': session}))
    Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(result))
    print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 20, 'output_tokens': 10}}))
    raise SystemExit(0)
stage = data["stage"]
if stage == "terra" and os.environ.get("AUTOCODE_CONSUMER_BARRIER"):
    import time
    barrier = Path(os.environ["AUTOCODE_CONSUMER_BARRIER"])
    if barrier.exists():
        barrier.with_suffix(".entered").write_text("stage active")
        deadline = time.monotonic() + 15
        while barrier.exists():
            if time.monotonic() > deadline:
                raise SystemExit("fixture barrier timed out")
            time.sleep(0.02)
mode = os.environ.get("AUTOCODE_FIXTURE_MODE", "standard")
# Bug-fix fixtures: the job type the planners propose, and the exact files the Builder writes.
task_kind = os.environ.get("AUTOCODE_FIXTURE_TASK_KIND", "build")
builder_files = (json.loads(Path(os.environ["AUTOCODE_FIXTURE_FILES"]).read_text())
                 if os.environ.get("AUTOCODE_FIXTURE_FILES") else None)
contract = data["goal_contract"] or {"revision": 0, "hash": ""}
record_launch(stage)
common = {"contract_revision": contract["revision"], "contract_hash": contract["hash"],
          "task_id": (data.get("current_task") or {}).get("id", ""),
          "deferred_backlog": ["Optional web UI"], "user_request": {"kind": "none", "discovered": "", "impact": "",
              "decision_needed": "", "options": [], "proposed_delta": ""}}
session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
NO_PROPOSAL = {"version": 0, "needed_because": "", "shared_decisions": [], "outstanding_criteria": [],
               "done_slices": [], "slices": []}
if os.environ.get("AUTOCODE_FIXTURE_SESSION_DRIFT"):
    session = str(uuid.uuid4())
print(json.dumps({"type": "thread.started", "thread_id": session}))
_quota_model = os.environ.get("AUTOCODE_FIXTURE_QUOTA_MODEL")  # only this model's quota is used up, when set
if os.environ.get("AUTOCODE_FIXTURE_QUOTA_STAGE") == stage and (not _quota_model or (
        "--model" in sys.argv and sys.argv[sys.argv.index("--model") + 1] == _quota_model)):
    print(json.dumps({"type": "error", "error": {"message": "subscription usage limit reached"}}))
    raise SystemExit(3)


def adaptive_task(draft, implement):
    """An adaptive-planning Planner draft carries its initial_task: kind none while a blocking question is open."""
    if "ADAPTIVE PLANNING" not in prompt:
        return
    draft["initial_task"] = (dict(implement) if not draft.get("open_blocking_questions") else
                             {"objective": "", "affected_paths": [], "kind": "none", "milestone_id": "",
                              "requirements": [], "acceptance_criteria": [], "validation_plan": []})


IMPLEMENT_TASK = {"objective": "Implement greeting CLI", "affected_paths": sorted(builder_files) if builder_files else ["greet.py"],
                  "kind": "implement", "milestone_id": "M1", "requirements": ["Greet names; reject empty/whitespace input"],
                  "acceptance_criteria": ["C1"], "validation_plan": ["Execute valid, empty and whitespace input"]}
if stage == "recognize_workflow":
    result = {"workflow": "build", "reason": "Offline fixture: every request is treated as a build", "signals": [], "design_document": ""}
    if 'Add "clarity"' in prompt:
        # Vague keeps the Requirements stage, so a default (adaptive) run plans in the same stages as before.
        result["clarity"] = os.environ.get("AUTOCODE_FIXTURE_CLARITY", "vague")
elif stage == "investigate_stuck":
    result = {"diagnosis": "Offline fixture: it cannot diagnose; the run pauses as before.", "cause": "other", "guidance": "", "recommendation": "pause", "user_question": "", "evidence_refs": [], "example": "", "probe": "", "untestable": ""}
elif stage == "requirements_gather":
    draft = body(questions=not data["saved_answers"], task_kind=task_kind)
    result = {
        "summary": "Requirements for a local greeting CLI, without an implementation plan",
        "intended_outcome": draft["intended_outcome"],
        "required_behaviors": draft["required_behaviors"],
        "constraints": draft["constraints"],
        "acceptance_tests": ["Valid and invalid CLI input have the requested outcomes"],
        "source_refs": [f"{name}:1" for name in ("greet.py", "bye.py") if Path(name).is_file()],
        "proposed_assumptions": [{"id": "A1", "text": "Use a local CLI if the user chooses that interface", "kind": "inferable", "category": "behavior", "convention_ref": "task", "rationale": "The task asks for a local tool", "supports": []}],
        "open_questions": draft["open_blocking_questions"],
        "requirements": [], "ignored_statements": [], "conflicts": [], "proposed_reframes": [],
        "ignored_requirements": [], "machine_resolutions": [], "access_blockers": [],
        "task_kind": task_kind,
    }
elif stage == "astra_discovery":
    draft = body(questions=not data["saved_answers"], human=mode in ("standard", "human-pending"), task_kind=task_kind)
    if os.environ.get("AUTOCODE_FIXTURE_TECHNICAL_FLOW_PENDING"):
        draft["end_to_end_flow"].append("Install the CLI entry point and invoke it from another working directory")
    if os.environ.get("AUTOCODE_FIXTURE_FLOW_AWAITS_REVIEW"):
        draft["end_to_end_flow"].append("User approves C1")
    if builder_files:
        draft["milestones"][0]["affected_paths"] = sorted(builder_files)
    if mode == "milestones":
        draft['acceptance_criteria'].append({'id': 'C2', 'criterion': 'Goodbye CLI prints Goodbye, NAME',
            'verification_method': 'Execute bye.py with Ada', 'human_review': False})
        draft['milestones'].append({'id': 'M2', 'objective': 'Deliver goodbye CLI',
                                    'acceptance_criteria': ['C2'], 'depends_on': ['M1'], 'affected_paths': ['bye.py']})
        draft['deliverables'].append('bye.py')
        draft['required_behaviors'].append('Print Goodbye, NAME from bye.py')
    if data["saved_answers"]:
        draft["accepted_assumptions"] = [{"text": "User selected CLI", "basis": "user_answer", "answer_id": "Q1"}]
    for feedback in data["brief_feedback"]:
        draft["constraints"].append(feedback["text"])
        draft["accepted_assumptions"].append({"text": feedback["text"], "basis": "user_feedback", "answer_id": feedback["id"]})
    result = {"contract": draft, "summary": "Build a small local greeting CLI with a clear invalid-input failure"}
    if data.get("joint_planning"):
        if draft["open_blocking_questions"]:
            draft["milestones"] = []
            draft["technical_approach"] = []
        adaptive_task(draft, IMPLEMENT_TASK)
        source = next((name for name in ("greet.py", "bye.py") if Path(name).is_file()), None)
        result.update(code_refs=[f"{source}:1"] if source else ["goal_contract.body"],
                      alternatives=["A web endpoint would need deployment"],
                      uncertainties=[], contract_changes=[], requirement_trace=[], conflict_resolutions=[],
                      machine_resolutions=[], access_blockers=[], remediation_records=[],
                      progressive_proposal=dict(NO_PROPOSAL))
elif stage == "astra_challenge":
    result = {"summary": "Check whitespace-only input", "obligation_decisions": [], "concerns": [{"id": "P1", "concern": "Empty includes whitespace",
        "evidence_refs": ["goal_contract.body.important_failure_cases"], "requested_change": "Specify whitespace rejection",
        "acceptance_test": "Whitespace input exits 2", "blocking": True}]}
elif stage == "glm_revise":
    draft = dict(contract["body"])
    draft.pop("initial_task", None)
    draft["important_failure_cases"] = [*draft["important_failure_cases"], "Reject whitespace-only input"]
    adaptive_task(draft, IMPLEMENT_TASK)
    source = next((name for name in ("greet.py", "bye.py") if Path(name).is_file()), None)
    result = {"contract": draft, "summary": "Added whitespace case",
        "code_refs": [f"{source}:1"] if source else ["goal_contract.body"],
        "contract_changes": [], "requirement_trace": [], "conflict_resolutions": [], "machine_resolutions": [], "remediation_records": [], "access_blockers": [],
        "progressive_proposal": dict(NO_PROPOSAL),
        "responses": [{"concern_id": "P1", "response": "Whitespace is invalid", "evidence_refs": ["goal_contract.body"],
                       "change": "Added whitespace case", "acceptance_test": "Whitespace input exits 2"}]}
elif stage == "astra_finalize":
    draft = dict(contract["body"])
    draft["initial_task"] = {"objective": "Implement greeting CLI",
        "affected_paths": sorted(builder_files) if builder_files else ["greet.py"],
        "kind": "implement", "milestone_id": "M1", "requirements": ["Greet names; reject empty/whitespace input"],
        "acceptance_criteria": ["C1"], "validation_plan": ["Execute valid, empty and whitespace input"]}
    blocked = mode == "planning-blocked"
    if blocked:
        draft["open_blocking_questions"] = [{"id": "P2", "question": "Should whitespace be rejected?",
            "why": "Unresolved input semantics", "options": ["Reject", "Accept"], "proposed_default": "",
            "kind": "decision", "category": "behavior", "delegable": False}]
    result = {"contract": draft, "summary": "Ready for approval" if not blocked else "User decision required",
        "contract_changes": [], "requirement_trace": [], "conflict_resolutions": [],
        "obligation_decisions": [], "progressive_proposal": dict(NO_PROPOSAL),
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
                                       "evidence": "Validator executed both CLI cases"} for c in data["acceptance_criteria"]],
              "next_objective": "" if complete else "Implement a greeting CLI and reject empty input",
              "next_task": {"kind": "none" if complete else "implement", "milestone_id": "" if complete else "M1",
                            "requirements": [] if complete else ["Print a greeting for valid input and reject empty input"],
                            "acceptance_criteria": [] if complete else ["C1"],
                            "validation_plan": [] if complete else ["Run greet.py with Ada and an empty name"],
                            "findings": []},
              "findings": [{"id": "", "severity": "high", "finding": "Empty names are accepted by greet.py",
                            "evidence": "Validator events", "blocking": True}] if rework else [],
              "agreed_limitations": ["Local command-line use only"] if complete else [],
              "finding_dispositions": ([{"id": open_finding_id("astra", "Empty names are accepted by greet.py"),
                                          "disposition": "resolved", "evidence": "Validator events"}]
                                       if complete and open_finding_id("astra", "Empty names are accepted by greet.py") else []),
              "evidence": ["Validator events"], "blocker": "",
              "plan": ["Implement greeting", "Run both cases"], "affected_paths": ["greet.py"]}
    if advance:
        result.update(next_objective='Implement goodbye CLI', affected_paths=['bye.py'])
        result['next_task'].update(milestone_id='M2', requirements=['Print Goodbye, NAME'],
                                  acceptance_criteria=['C2'], validation_plan=['Execute both greeting and goodbye'])
    if mode == 'stalled' and ((data.get('milestone_checkpoint') or {}).get('current') or {}).get('needs_replan'):
        result['next_objective'] = 'Isolate empty input with a focused reproduction before repair'
    if mode == "human-pending" and stage == "astra_review" and not data.get("human_reviews"):
        result.update(status="CONTINUE", next_objective="Obtain human acceptance of the validated artifact")
        result["acceptance_criteria"][0]["status"] = "unverified"
        if os.environ.get("AUTOCODE_FIXTURE_EMPTY_HUMAN_EVIDENCE"):
            result["acceptance_criteria"][0]["evidence"] = ""
        result["next_task"].update(kind="validate", milestone_id="M1", requirements=["Obtain human acceptance"],
            acceptance_criteria=["C1"], validation_plan=["Ask the user to accept the current artifact"])
    if stage == 'astra_resolve':
        result['diagnosis'] = 'Empty names are accepted by the CLI; add input validation and retest both cases.'
    if stage == 'astra_review' and rework and os.environ.get('AUTOCODE_FIXTURE_INCOMPLETE_REWORK'):
        # Exercise Resolver routing with a valid rejection that still needs a repair plan.
        result['next_task']['validation_plan'] = []
elif stage == "terra":
    # Each implement attempt must produce a real tree delta; the runner
    # measures changed files from the workspace snapshot, not the report.
    batch = str(uuid.uuid4())
    milestone = data.get('current_task', {}).get('milestone_id')
    if mode == 'milestones' and milestone == 'M2':
        Path('bye.py').write_text("import sys\nprint('Goodbye, ' + sys.argv[1])\n# batch " + batch + "\n")
    elif mode == 'stalled':
        Path('greet.py').write_text("import sys\nprint('Hello, ' + sys.argv[1])\n# attempt " + batch + '\n')
    elif mode == "rework" and not data["actionable_findings"]:
        Path("greet.py").write_text("import sys\nprint('Hello, ' + sys.argv[1])\n# attempt " + batch + '\n')
    elif builder_files:
        for name, content in builder_files.items():
            Path(name).write_text(content)
    else:
        Path("greet.py").write_text("import sys\nif len(sys.argv) != 2 or not sys.argv[1].strip():\n    raise SystemExit(2)\nprint('Hello, ' + sys.argv[1])\n# batch " + batch + "\n")
    if os.environ.get("AUTOCODE_CONSUMER_BARRIER"):
        with Path("greet.py").open("a") as fixture_output:
            fixture_output.write("# revision " + str(uuid.uuid4()) + "\n")
    result = {**common, "summary": "Greeting written", "changed_files": ["greet.py"], "commands_run": [],
              "results": ["Written"], "remaining_risks": [], "evidence_refs": ["greet.py"],
              "addressed_requirements": data["current_task"]["requirements"], "untested_behavior": ["CLI execution"],
              "recommended_checks": ["Execute valid and invalid input"]}
    if mode == 'milestones' and milestone == 'M2':
        result.update(changed_files=['bye.py'], evidence_refs=['bye.py'], summary='Goodbye written')
    elif builder_files:
        result.update(changed_files=sorted(builder_files), evidence_refs=sorted(builder_files))
else:
    valid = subprocess.run([sys.executable, "greet.py", "Ada"], capture_output=True, text=True)
    invalid = subprocess.run([sys.executable, "greet.py", ""], capture_output=True, text=True)
    passed = valid.returncode == 0 and valid.stdout == "Hello, Ada\n" and invalid.returncode == 2
    goodbye_passed = False
    if mode == 'milestones' and data['current_task']['milestone_id'] == 'M2':
        goodbye = subprocess.run([sys.executable, 'bye.py', 'Ada'], capture_output=True, text=True)
        goodbye_passed = goodbye.returncode == 0 and goodbye.stdout == 'Goodbye, Ada\n'
        passed = passed and goodbye_passed
    # A real command the runner can re-run in a clean copy (autocode_check_replay): the same checks.
    script = ("import subprocess, sys\n"
              "run = lambda *a: subprocess.run([sys.executable, *a], capture_output=True, text=True)\n"
              "ok = run('greet.py', 'Ada').stdout == 'Hello, Ada\\n' and run('greet.py', '').returncode == 2\n"
              + ("ok = ok and run('bye.py', 'Ada').stdout == 'Goodbye, Ada\\n'\n"
                 if mode == 'milestones' and data['current_task']['milestone_id'] == 'M2' else "")
              + "sys.exit(0 if ok else 1)\n")
    command = shlex.join([sys.executable, "-c", script])
    if os.environ.get("AUTOCODE_FIXTURE_UNREPRODUCIBLE_CHECK"):
        # Passes only in the Validator's own session: it reads a file the Validator made outside the source.
        Path(".autocode/validator-only").write_text("set up by the Validator\n")
        command = "test -f .autocode/validator-only"
    if not os.environ.get("AUTOCODE_FIXTURE_NO_CHECK_EVENT"):
        print(json.dumps({"type": "item.completed", "item": {"id": "check", "type": "command_execution",
            "command": command, "exit_code": 0 if passed else 1,
            "aggregated_output": json.dumps({"valid": [valid.returncode, valid.stdout], "invalid": invalid.returncode})}}))
    findings = [] if passed else [{"id": "", "severity": "high", "blocking": True, "finding": "Empty names are accepted",
        "evidence": "event:check", "reproduction_steps": ["Run greet.py with an empty argument"],
        "expected": "Exit 2", "actual": f"Exit {invalid.returncode}", "why_it_matters": "Required invalid-input behavior",
        "suggested_correction": "Reject an empty or whitespace-only name"}]
    result = {**common, "verdict": "PASS" if passed else "FAIL", "findings": findings, "checks_run": [command],
              "unverified_criteria": [], "checks": [{"command": command, "exit_code": 0 if passed else 1, "evidence_ref": "event:check"}],
              "end_to_end_result": {"status": "PASS" if passed else "FAIL", "summary": "Executed both CLI user flows",
                                    "evidence_refs": ["event:check"], "technical_result": None, "pending_human_criteria": []},
              "criterion_results": [{"id": "C1", "status": "PASS" if passed else "FAIL", "evidence_refs": ["event:check"]}],
              "finding_dispositions": ([{"id": open_finding_id("sol", "Empty names are accepted"), "disposition": "resolved",
                                          "evidence": "event:check"}]
                                       if passed and open_finding_id("sol", "Empty names are accepted") else [])}
    if mode == 'milestones':
        result['criterion_results'].append({'id': 'C2', 'status': 'PASS' if goodbye_passed else 'NOT_VERIFIED',
                                           'evidence_refs': ['event:check'] if goodbye_passed else []})
    if mode == "human-pending" and passed:
        result.update(verdict=os.environ.get("AUTOCODE_FIXTURE_HUMAN_VERDICT", "BLOCKED"),
                      unverified_criteria=["C1 human acceptance pending"])
        result["criterion_results"][0]["status"] = "NOT_VERIFIED"
        if os.environ.get("AUTOCODE_FIXTURE_FLOW_AWAITS_REVIEW"):
            # The approved flow ends in the person's acceptance, so the flow itself stays unverified.
            result["end_to_end_result"].update(status="NOT_VERIFIED", summary=os.environ["AUTOCODE_FIXTURE_FLOW_AWAITS_REVIEW"])
            if not os.environ.get("AUTOCODE_FIXTURE_LEGACY_FLOW_GAP"):
                unfinished = bool(os.environ.get("AUTOCODE_FIXTURE_TECHNICAL_FLOW_PENDING"))
                result["end_to_end_result"].update(pending_human_criteria=["C1"], technical_result={
                    "status": "NOT_VERIFIED" if unfinished else "PASS",
                    "summary": "Installation and invocation were not executed" if unfinished else "Both CLI flows executed",
                    "evidence_refs": ["check:1"]})
        if os.environ.get("AUTOCODE_FIXTURE_OMIT_CHECKS"):
            result["checks"] = []
        if os.environ.get("AUTOCODE_FIXTURE_NO_CHECK_EVENT"):
            result.update(checks=[], checks_run=[])
            result["criterion_results"][0]["evidence_refs"] = ["greet.py"]
            result["end_to_end_result"]["evidence_refs"] = ["greet.py"]
    if stage == "astra_checkpoint":
        result = {"validation": result, "consult_sol": {"requested": False, "question": "", "reason": ""},
            "decision": {**common, "status": "COMPLETE" if passed else "REWORK",
                "acceptance_criteria": [{**c, "status": "verified" if passed else "unverified",
                    "evidence": "Independent Plan Reviewer executed CLI cases; event:check"} for c in data["acceptance_criteria"]],
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
    command=shlex.join([sys.executable,'-c',"import subprocess, sys\nrun = lambda *a: subprocess.run([sys.executable, *a], capture_output=True, text=True)\nsys.exit(0 if run('greet.py', 'Ada').stdout == 'Hello, Ada\\n' and run('greet.py', '').returncode == 2 else 1)\n"])
    print(json.dumps({'type':'item.completed','item':{'id':'self-check','type':'command_execution',
        'command':command,'exit_code':0 if passed else 1,'aggregated_output':'fixture checks'}}))
    assessment={**common,'verdict':'PASS' if passed else 'FAIL','findings':[],'finding_dispositions':[],'checks_run':[command],
        'unverified_criteria':[], 'checks':[{'command':command,'exit_code':0 if passed else 1,'evidence_ref':'event:self-check'}],
        'end_to_end_result':{'status':'PASS' if passed else 'FAIL','summary':'Builder self-check','evidence_refs':['event:self-check'],
                             'technical_result':None,'pending_human_criteria':[]},
        'criterion_results':[{'id':'C1','status':'PASS' if passed else 'FAIL','evidence_refs':['event:self-check']}]}
    second=data.get('next_action')=='Second Builder batch'
    consulted=bool(data.get('consultation_reports'))
    action=('REQUEST_FINAL_AUDIT' if consulted else 'ESCALATE_SOL') if mode=='sol-escalation' else ('REQUEST_FINAL_AUDIT' if second else 'CONTINUE')
    result['untested_behavior']=[]
    result['continuation']={'action':action,'plan':['Finish approved greeting'],
        'next_task':{'kind':'implement','objective':'Second Builder batch','affected_paths':['greet.py'],
            'milestone_id':'M1','requirements':['Verify the final greeting'],'acceptance_criteria':['C1'],
            'validation_plan':['Run valid and invalid CLI cases']},
        'reason':'Fixture-specific debugging question' if action=='ESCALATE_SOL' else 'Continue approved work',
        'question':'Check the empty input boundary' if action=='ESCALATE_SOL' else '',
        'self_assessment':assessment}
if "--output-schema" in sys.argv:
    schema = json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text())
    if "progressive_checkpoint" in schema.get("properties", {}):
        result.setdefault("progressive_checkpoint", False)
Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(result))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}}))

#!/usr/bin/env python3
"""Scripted provider for external-CLI build audits. Not a model-quality test.

Plans and file payloads are handwritten. Only provider I/O is substituted; CLI,
approval, scheduling, processes, worktrees, reports and integration are real.
"""

import copy
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

HELLO_SOURCE = "print('hello')\n"
HELLO_TESTS = """import subprocess, sys, unittest
class HelloTests(unittest.TestCase):
    def test_cli_hello(self):
        result = subprocess.run([sys.executable, 'main.py'], capture_output=True, text=True)
        self.assertEqual((0, 'hello\\n', ''), (result.returncode, result.stdout, result.stderr))
"""


def scripted_changed_code_reasons(spec, data):
    """The fixture Tester's reasons for its own scripted lines that have no mutation site.

    The real Tester records why a changed line without a mutation site (an import, a call, a
    ``def``) is covered; this fixture does the same for the exact payload bytes the scripted
    Builder wrote, bound to the gap, file and line, and never for a file it did not script or a
    line a mutant reached (that line's evidence is the mutant's outcome).
    """
    proof = data.get("regression_proof") or {}
    if not proof.get("path"):
        return []
    scripted = {
        name: hashlib.sha256(content.encode()).hexdigest()
        for files in (spec.get("payloads") or {}).values()
        for name, content in files.items()
    }
    return [
        {
            "gap_id": row["gap_id"],
            "decision": "accept",
            "reason_kind": "instrumentation_unavailable",
            "reason": (
                f"Scripted fixture: {row['path']}:{row['line']} is the exact scripted content of this delivery; "
                "the line has no supported mutation site, and the delivery's own tests, run independently by "
                "the proof, exercise the behavior it takes part in. The approved milestone check observes the "
                "same behavior."
            ),
            "evidence_refs": [proof["path"], row["path"]],
        }
        for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []
        if row.get("status") == "reason_required"
        and scripted.get(row.get("path")) == row.get("sha256")
        and not row.get("mutation_outcomes")
    ]


_NUMBER = re.compile(r"\d+(?:\.\d+)*")
_NEGATION = re.compile(r"\b(?:must not|never|do not|don't|cannot|can't|not|no)\b", re.I)
SOURCE_VERDICT = {
    "verdict": "PASS",
    "uncovered": [],
    "summary": "Handwritten fixture: every criterion traces to the brief",
}


def _sentences(text):
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if part.strip()]


def _number_in(number, text):
    return re.search(r"(?<!\d)(?<!\d\.)" + re.escape(number) + r"(?!(?:\d|\.\d))", text) is not None


def _corpus(body):
    parts = [
        *(body.get("required_behaviors") or []),
        *(body.get("constraints") or []),
        *(body.get("technical_approach") or []),
    ]
    for row in body.get("acceptance_criteria") or []:
        parts.append(str(row.get("criterion", "")) + " " + str(row.get("verification_method", "")))
    return " ".join(parts)


def cite_sources(body, data):
    """Trace criteria to source sentences the contract can still carry (#595 / #1201)."""
    inventory = [
        row for row in data.get("source_inventory") or [] if isinstance(row, dict) and row.get("id") and row.get("text")
    ]
    if not inventory:
        for row in body.get("acceptance_criteria") or []:
            row.setdefault("sources", [])
        return body
    corpus = _corpus(body)
    citations = []
    for source in inventory:
        for sentence in _sentences(source["text"]):
            numbers = _NUMBER.findall(sentence)
            if numbers and not all(_number_in(number, corpus) for number in numbers):
                continue
            if _NEGATION.search(sentence) and not _NEGATION.search(corpus):
                continue
            citations.append({"source_id": source["id"], "quote": sentence})
    for row in body.get("acceptance_criteria") or []:
        row["sources"] = [dict(citation) for citation in citations]
    return body


def _quote_covers(sentence, quotes):
    bare = sentence.rstrip(".")
    for quote in quotes:
        if not quote:
            continue
        clipped = quote.rstrip(".")
        if bare in clipped or bare == clipped or sentence in quote:
            return True
        if len(clipped) >= 12 and (clipped in bare or quote in sentence):
            return True
    return False


def source_trace_for(body, data):
    inventory = [
        row for row in data.get("source_inventory") or [] if isinstance(row, dict) and row.get("id") and row.get("text")
    ]
    quotes = [
        citation["quote"] for row in body.get("acceptance_criteria") or [] for citation in row.get("sources") or []
    ]
    out_of_scope = []
    for source in inventory:
        for sentence in _sentences(source["text"]):
            if not _quote_covers(sentence, quotes):
                out_of_scope.append(
                    {
                        "source_id": source["id"],
                        "quote": sentence,
                        "reason": "Handwritten fixture: not an acceptance obligation for this plan",
                    }
                )
    return {"out_of_scope": out_of_scope, "negation_waivers": []}


def classify_failure(data, mode, spec):
    """Diagnose only a prescribed failed check in a bounded scripted retry fault."""
    failure = data["builder_failure"]
    report = Path(failure["record"]["output"])
    events = Path(failure["record"]["events"])
    expected = spec["checks"][data["current_task"]["milestone_id"]]
    refs = failure["evidence_refs"]
    value = json.loads(report.read_text())
    rows = [json.loads(line) for line in events.read_text().splitlines()]
    checks = [
        row["item"]
        for row in rows
        if row.get("type") == "item.completed"
        and row.get("item", {}).get("type") == "command_execution"
        and row["item"].get("id") == "check"
    ]
    execution = (
        mode in ("retry_success", "escalate_success", "retry_exhausted")
        and str(report) in refs
        and str(events) in refs
        and len(checks) == 1
        and checks[0]["command"] == expected
        and checks[0]["exit_code"] > 0
        and value["commands_run"] == [expected]
        and value["results"] == [f"check exit={checks[0]['exit_code']}"]
    )
    diagnosis = (
        "The prescribed local check failed before the scripted Builder delivered its assigned file."
        if execution
        else "No source change alone does not establish an execution defect."
    )
    probe = ""
    if execution:
        code = (
            "import json; r=json.load(open(" + repr("run/" + report.name) + ")); "
            "rows=[json.loads(x) for x in open(" + repr("run/" + events.name) + ")]; "
            "c=[x['item'] for x in rows if x.get('type')=='item.completed' "
            "and x.get('item',{}).get('type')=='command_execution' "
            "and x['item'].get('id')=='check']; "
            "assert len(c)==1 and c[0]['command']==" + repr(expected) + "; "
            "assert c[0]['exit_code']>0 and r['commands_run']==[c[0]['command']]; "
            "assert r['results']==['check exit='+str(c[0]['exit_code'])]"
        )
        probe = shlex.join([sys.executable, "-c", code])
    return dict(
        diagnosis=diagnosis,
        cause="other",
        guidance=diagnosis if execution else "",
        recommendation="retry" if execution else "pause",
        user_question="",
        evidence_refs=[str(report), str(events)] if execution else [str(report)],
        example=diagnosis,
        probe=probe,
        counterexample={
            "file": "run/" + report.name if execution else "",
            "find": json.dumps(value["results"][0]) if execution else "",
            "replace": json.dumps("check exit=0") if execution else "",
        },
        untestable="" if probe else "The retained report cannot establish which cause produced the empty diff.",
        failure_class="execution" if execution else "unknown",
        failure_id=failure["failure_id"],
    )


def main():
    if sys.argv[1:] == ["login", "status"]:
        print("Logged in using ChatGPT (offline black-box fixture)")
        return
    prompt = sys.stdin.read()
    data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
    if data.get("report_repair"):
        data["stage"] = data["original"]["stage"] + "_report_repair"
    spec = json.loads(Path(os.environ["BUILD_AUDIT_SPEC"]).read_text())
    task = data.get("current_task") or {}
    mid = task.get("milestone_id", "")
    mode = os.environ.get("BUILD_AUDIT_FAULT", "")
    # Milestones the retry faults and no_change apply to.
    stuck = os.environ.get("BUILD_AUDIT_FAULT_MILESTONES", "M1").split(",")
    log = Path(os.environ["BUILD_AUDIT_LOG"])

    def record(event, **extra):
        with log.open("a") as stream:
            stream.write(
                json.dumps(
                    dict(
                        event=event,
                        stage=data["stage"],
                        milestone=mid,
                        pid=os.getpid(),
                        workspace=str(Path.cwd()),
                        time=time.time(),
                        **extra,
                    )
                )
                + "\n"
            )

    record(
        "start",
        model=sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else None,
        task_paths=task.get("affected_paths"),
        affected_paths=data.get("affected_paths"),
        inputs={name: Path(name).read_text() for name in spec.get("observe", []) if Path(name).is_file()},
    )
    if os.environ.get("REVIEW_AUDIT_LIVE_CODEX") and data["stage"].startswith(("sol", "astra_review")):
        record("live_reviewer")
        result = subprocess.run([os.environ["REVIEW_AUDIT_LIVE_CODEX"], *sys.argv[1:]], input=prompt, text=True)
        record("finish", returncode=result.returncode)
        raise SystemExit(result.returncode)
    if os.environ.get("BUILD_AUDIT_LIVE_CODEX") and data["stage"].startswith("terra"):
        record("live_builder")
        result = subprocess.run([os.environ["BUILD_AUDIT_LIVE_CODEX"], *sys.argv[1:]], input=prompt, text=True)
        record("finish", returncode=result.returncode)
        raise SystemExit(result.returncode)
    session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
    print(json.dumps({"type": "thread.started", "thread_id": session}), flush=True)
    contract = data.get("goal_contract") or {"revision": 0, "hash": ""}
    # Review handoffs may reference the complete criteria already in the contract.
    criteria = data.get("acceptance_criteria", (contract.get("body") or {}).get("acceptance_criteria", []))
    common = dict(
        contract_revision=contract["revision"],
        contract_hash=contract["hash"],
        task_id=task.get("id", ""),
        deferred_backlog=[],
        user_request=dict(kind="none", discovered="", impact="", decision_needed="", options=[], proposed_delta=""),
    )
    rows = spec["contract"]["milestones"]
    if data.get("report_repair"):
        result = json.loads(Path(data["original"]["output"]).read_text())
        if data["original"]["stage"] == "sol" and spec.get("validator_repair_incidents"):
            result.pop("milestone_results", None)
            state = json.loads(Path(data["state_file"]).read_text())
            # M4 needs both bounded repairs; the final validate task needs its own.
            if state["current_task"]["kind"] == "implement" and state["pending_report_repair"]["attempts"] == 1:
                result["another_extra_field"] = True
        else:
            result["summary"] = "Reformatted preserved builder report"
        if mode == "repair_lies":
            result["results"] = ["All tests passed; exit code 0"]
            result["commands_run"] = ['python3 -c "raise SystemExit(0)"']
        if isinstance(result.get("contract"), dict):
            result["contract"] = cite_sources(copy.deepcopy(result["contract"]), data)
            if data["original"]["stage"] in ("astra_challenge", "astra_finalize"):
                result.setdefault("source_trace", source_trace_for(result["contract"], data))
        record("repair")
    elif data["stage"] == "recognize_workflow":
        result = dict(
            workflow="build", reason="Handwritten fixture: every request is a build", signals=[], design_document=""
        )
    elif data["stage"] == "investigate_stuck" and data.get("builder_failure"):
        result = classify_failure(data, mode, spec)
        record("classification", report=result)
    elif data["stage"] == "investigate_stuck":
        result = {
            "diagnosis": "Offline fixture: it cannot diagnose; the run pauses as before.",
            "cause": "other",
            "guidance": "",
            "recommendation": "pause",
            "user_question": "",
            "evidence_refs": [],
            "example": "",
            "probe": "",
            "counterexample": {"file": "", "find": "", "replace": ""},
            "untestable": "",
        }
    elif data["stage"] == "astra_discovery":
        draft = cite_sources(copy.deepcopy(spec["contract"]), data)
        result = dict(contract=draft, summary="Handwritten fixture plan; no model planning")
    elif data["stage"] == "astra_challenge":
        draft = (contract.get("body") or {}) if isinstance(contract.get("body"), dict) else {}
        result = dict(
            summary="Handwritten plan review; no concerns",
            obligation_decisions=[],
            concerns=[],
            source_trace=source_trace_for(draft, data),
        )
    elif data["stage"] == "astra_finalize":
        draft = cite_sources(copy.deepcopy(spec["contract"]), data)
        result = dict(
            contract=draft,
            summary="Handwritten final plan",
            source_trace=source_trace_for(draft, data),
            obligation_decisions=[],
            decisions=[],
            contract_changes=[],
            conflict_resolutions=[],
            requirement_trace=[],
        )
    elif data["stage"] == "terra":
        if mid in stuck and mode in ("retry_success", "escalate_success", "retry_exhausted"):
            count = sum(
                r.get("stage") == "terra" and r.get("event") == "start" and r.get("milestone") == mid
                for r in map(json.loads, log.read_text().splitlines())
            )
            required = {"retry_success": 2, "escalate_success": 3, "retry_exhausted": 99}[mode]
            if count < required:
                mode = "no_change"
        if mid == "M1" and mode == "crash":
            record("crash")
            raise SystemExit(9)
        if mid == "M1" and mode in ("hold", "hold_timeout"):
            if mode == "hold_timeout":
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            marker = log.parent / "release"
            deadline = time.monotonic() + 30
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            if not marker.exists():
                raise SystemExit(8)
        # Keep independent worker intervals overlapping without requiring scheduler internals.
        overlap = int(os.environ.get("BUILD_AUDIT_OVERLAP") or 0)
        if overlap:
            # Wait until that many Builders have started, so they provably overlap however busy the machine is.
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                started = set()
                for line in log.read_text().splitlines():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue  # another Builder is appending this line right now
                    if row.get("stage") == "terra" and row.get("event") == "start":
                        started.add(row.get("milestone"))
                if len(started) >= overlap:
                    break
                time.sleep(0.02)
        else:
            time.sleep(0.3)
        changed = []
        if not (mid in stuck and mode == "no_change" or mid == "M1" and mode in ("permission", "infeasible")):
            for name, content in spec["payloads"][mid].items():
                if mode == "validation_fails":
                    count = sum(
                        r.get("stage") == "terra" and r.get("event") == "start"
                        for r in map(json.loads, log.read_text().splitlines())
                    )
                    content += "\n" * count  # Different source each attempt, same actual defect.
                p = Path(name)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
                changed.append(name)
        if mid == "M1" and mode == "escape":
            Path("unauthorized.txt").write_text("outside ownership")
            changed.append("unauthorized.txt")
        if mode == "checkpoints":
            for name in spec.get("checkpoint_files", []):
                cmd = "from pathlib import Path; assert Path(" + repr(name) + ").is_file()"
                checked = subprocess.run([sys.executable, "-c", cmd], capture_output=True, text=True)
                print(
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": dict(
                                id="checkpoint-" + name,
                                type="command_execution",
                                command=shlex.join([sys.executable, "-c", cmd]),
                                exit_code=checked.returncode,
                                aggregated_output="Checkpoint " + name,
                            ),
                        }
                    ),
                    flush=True,
                )
                record("checkpoint", path=name, exit_code=checked.returncode)
            record("checkpoints_saved")
            marker = log.parent / "release"
            deadline = time.monotonic() + 30
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
        checks = []
        if mode != "no_evidence":
            cmd = spec["checks"][mid]
            checked = subprocess.run([sys.executable, "-c", cmd], capture_output=True, text=True)
            if mode == "repair_lies" and mid == "M1":
                checked = subprocess.run([sys.executable, "-c", "raise SystemExit(1)"], capture_output=True, text=True)
            print(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": dict(
                            id="check",
                            type="command_execution",
                            command=cmd,
                            exit_code=checked.returncode,
                            aggregated_output=checked.stdout + checked.stderr,
                        ),
                    }
                ),
                flush=True,
            )
            checks = [f"check exit={checked.returncode}"]
        result = dict(
            common,
            summary="Scripted implementation",
            changed_files=changed,
            commands_run=[] if mode == "no_evidence" else [spec["checks"][mid]],
            results=checks,
            remaining_risks=[],
            evidence_refs=[] if mode == "no_evidence" else ["event:check"],
            addressed_requirements=task.get("requirements", []),
            untested_behavior=["Required local check not run"] if mode == "no_evidence" else [],
            recommended_checks=task.get("validation_plan", []),
        )
        if mid == "M1" and mode in ("permission", "infeasible"):
            result["user_request"] = dict(
                kind=mode,
                discovered="Required external access is unavailable",
                impact="Cannot implement the approved approach",
                decision_needed="Replan or stop?",
                options=["Replan", "Stop", "No. Do not access any external account."]
                if mode == "permission"
                else ["Replan", "Stop"],
                proposed_delta="No change without a user decision",
            )
        if mid == "M1" and mode in ("malformed", "repair_lies"):
            result.pop("summary")
        if mid == "M1" and mode == "contract_change":
            result["contract_hash"] = "unauthorized-contract"
    elif data["stage"] == "sol":
        statuses = {}
        owned = set(task["acceptance_criteria"])
        checked_rows = []
        validation_rows = (
            [dict(id=cid, acceptance_criteria=[cid]) for cid in spec["criterion_checks"]]
            if spec.get("criterion_checks")
            else rows
        )

        def check_command(row):
            return spec["criterion_checks"][row["id"]] if spec.get("criterion_checks") else spec["checks"][row["id"]]

        for row in validation_rows:
            cid = row["acceptance_criteria"][0]
            if cid not in owned:
                statuses[cid] = "NOT_VERIFIED"
                continue
            cmd = check_command(row)
            checked = subprocess.run([sys.executable, "-c", cmd], capture_output=True, text=True)
            checked_rows.append(row)
            statuses[cid] = "PASS" if checked.returncode == 0 else "NOT_VERIFIED"
            print(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": dict(
                            id=cid,
                            type="command_execution",
                            command=shlex.join([sys.executable, "-c", cmd]),
                            exit_code=checked.returncode,
                            aggregated_output=checked.stdout + checked.stderr,
                        ),
                    }
                ),
                flush=True,
            )
        passed = all(statuses[cid] == "PASS" for cid in owned)
        result = dict(
            common,
            verdict="PASS" if passed else "FAIL",
            checks_run=[shlex.join([sys.executable, "-c", check_command(r)]) for r in checked_rows],
            checks=[
                dict(
                    command=shlex.join([sys.executable, "-c", check_command(r)]),
                    exit_code=0 if statuses[r["acceptance_criteria"][0]] == "PASS" else 1,
                    evidence_ref="event:" + r["acceptance_criteria"][0],
                )
                for r in checked_rows
            ],
            findings=[],
            finding_dispositions=[],
            unverified_criteria=[cid for cid, status in statuses.items() if status != "PASS"],
            criterion_results=[
                dict(id=cid, status=status, evidence_refs=["event:" + cid] if cid in owned else [])
                for cid, status in statuses.items()
            ],
            end_to_end_result=dict(
                status="PASS" if all(v == "PASS" for v in statuses.values()) else "NOT_VERIFIED",
                summary="Executed handwritten product checks",
                evidence_refs=["event:" + cid for cid in owned],
                technical_result=None,
                pending_human_criteria=[],
            ),
        )
        if task.get("milestone_ids"):
            result["milestone_results"] = [
                dict(
                    milestone_id=m,
                    status="PASS" if passed else "FAIL",
                    summary="Executed check",
                    evidence_refs=["event:" + cid for cid in owned],
                )
                for m in task["milestone_ids"]
            ]
        elif spec.get("validator_repair_incidents") and mid == rows[-1]["id"]:
            result["milestone_results"] = []
    else:
        # Fixture checkpoint reviewer: evaluates actual checks; final approval is opt-in.
        ready = []
        for row in rows:
            check = subprocess.run([sys.executable, "-c", spec["checks"][row["id"]]], capture_output=True)
            if check.returncode:
                ready.append(row)
        row = ready[0] if ready else rows[-1]
        result = dict(
            common,
            status="REWORK" if data.get("validation", {}).get("verdict") == "FAIL" else "CONTINUE",
            next_objective=row["objective"],
            affected_paths=row["affected_paths"],
            acceptance_criteria=[
                dict(id=c["id"], criterion=c["criterion"], status="unverified", evidence="") for c in criteria
            ],
            next_task=dict(
                kind="implement",
                milestone_id=row["id"],
                requirements=[row["objective"]],
                acceptance_criteria=row["acceptance_criteria"],
                validation_plan=[spec["checks"][row["id"]]],
                findings=[],
            ),
            plan=["Execute approved DAG"],
            evidence=["Checkpoint checks"],
            blocker="",
            agreed_limitations=[],
            findings=[],
            finding_dispositions=[],
        )
        if spec.get("review_completed_paths") and data["stage"] == "astra_review":
            result["affected_paths"] = task["affected_paths"]
        if spec.get("complete_product") and not ready:
            if (data.get("validation") or {}).get("end_to_end_result", {}).get("status") == "PASS":
                result.update(
                    status="COMPLETE",
                    next_objective="",
                    next_task=dict(
                        kind="none",
                        milestone_id="",
                        requirements=[],
                        acceptance_criteria=[],
                        validation_plan=[],
                        findings=[],
                    ),
                )
                for criterion in result["acceptance_criteria"]:
                    criterion.update(status="verified", evidence="event:" + criterion["id"])
            else:
                result["next_task"].update(
                    kind="validate",
                    acceptance_criteria=[c["id"] for c in criteria],
                    validation_plan=list(spec["checks"].values()),
                )
        record("decision", affected_paths=result["affected_paths"], next_task=result["next_task"])
        if data["stage"] == "astra_resolve":
            result["diagnosis"] = (
                "The current candidate failed its prescribed check; correct the current assignment without changing the contract"
            )
        request = (data.get("agent_request") or {}).get("request") or (data.get("implementation") or {}).get(
            "user_request"
        )
        if mode == "permission" and data["stage"] == "astra_review" and request and request.get("kind") != "none":
            result.update(status="BLOCKED", user_request=request, blocker=request["decision_needed"])
    owning = data["original"]["stage"] if data.get("report_repair") else data["stage"]
    if owning == "astra_review" and ((contract.get("body") or {}).get("source_trace") is not None):
        result.setdefault("source_verdict", dict(SOURCE_VERDICT))
    if "--output-schema" in sys.argv:
        schema = json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text())
        if "source_verdict" in schema.get("properties", {}):
            result.setdefault("source_verdict", dict(SOURCE_VERDICT))
        kind = (
            schema.get("properties", {})
            .get("user_request", {})
            .get("properties", {})
            .get("recommended_revision", {})
            .get("type")
        )
        nullable = kind == "null" or isinstance(kind, list) and "null" in kind
        request = result.get("user_request")
        if owning == "astra_resolve" and isinstance(request, dict) and nullable:
            request.setdefault("recommended_revision", None)
    if data["stage"] == "sol":
        schema = json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text())
        if "changed_code_exceptions" in schema.get("properties", {}):
            result["changed_code_exceptions"] = scripted_changed_code_reasons(spec, data)
    Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(result))
    record("finish")
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 10}}), flush=True)


if __name__ == "__main__":
    main()

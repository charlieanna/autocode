# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
#!/usr/bin/env python3
"""Offline Builder fixture; a rendezvous verifies actual concurrent processes."""
import json
import os
from pathlib import Path
import sys
import time
import uuid
import subprocess


def plan():
    from goal_fixtures import body
    draft = body(human=False)
    draft["acceptance_criteria"] = [
        {"id": f"C{i}", "criterion": f"Output {i} works", "verification_method": f"Execute output {i}", "human_review": False}
        for i in (1, 2, 3)]
    draft["milestones"] = [
        {"id": f"M{i}", "objective": f"Output {i}", "acceptance_criteria": [f"C{i}"],
         "depends_on": [] if i < 3 else ["M1", "M2"], "affected_paths": [path]}
        for i, path in enumerate(("a.txt", "b.txt", "combined.txt"), 1)]
    return draft


def report(data):
    common = {"contract_revision": data["goal_contract"]["revision"], "contract_hash": data["goal_contract"]["hash"],
              "task_id": (data.get("current_task") or {}).get("id", ""), "deferred_backlog": [],
              "user_request": {"kind": "none", "discovered": "", "impact": "", "decision_needed": "",
                               "options": [], "proposed_delta": ""}}
    if data["stage"] == "astra_discovery":
        return {"contract": plan(), "summary": "Two independent outputs, then combine"}
    if data["stage"] == "sol":
        command = [sys.executable, "-c", "from pathlib import Path; print({p:Path(p).read_text() for p in ('a.txt','b.txt','combined.txt') if Path(p).exists()})"]
        checked = subprocess.run(command, capture_output=True, text=True)
        print(json.dumps({"type": "item.completed", "item": {"id": "check", "type": "command_execution",
              "command": "read-outputs", "exit_code": checked.returncode, "aggregated_output": checked.stdout}}))
        done = Path("combined.txt").exists()
        value = {**common, "verdict": "PASS", "checks_run": ["read-outputs"], "findings": [],
                 "unverified_criteria": [] if done else ["C3"], "checks": [{"command": "read-outputs", "exit_code": 0, "evidence_ref": "event:check"}],
                 "criterion_results": [{"id": f"C{i}", "status": "PASS" if i < 3 or done else "NOT_VERIFIED",
                                        "evidence_refs": ["event:check"]} for i in (1, 2, 3)],
                 "end_to_end_result": {"status": "PASS" if done else "NOT_VERIFIED",
                                       "summary": "Read combined output" if done else "Inputs pass; combined output is not built yet",
                                       "evidence_refs": ["event:check"]},
                 "finding_dispositions": []}
        if data["current_task"].get("milestone_ids"):
            value["milestone_results"] = [{"milestone_id": mid, "status": "PASS", "summary": "Output executed", "evidence_refs": ["event:check"]}
                                          for mid in data["current_task"]["milestone_ids"]]
        return value
    done = Path("combined.txt").exists() and bool(data.get("validation"))
    mid = "M3" if data.get("validation") else "M1"
    return {**common, "status": "COMPLETE" if done else "CONTINUE",
            "acceptance_criteria": [{**c, "status": "verified" if done else "unverified", "evidence": "event:check" if done else ""}
                                    for c in data["acceptance_criteria"]],
            "next_objective": "" if done else "Build " + mid,
            "next_task": {"kind": "none" if done else "implement", "milestone_id": "" if done else mid,
                          "requirements": [] if done else ["Produce output"], "acceptance_criteria": [] if done else ["C3" if mid == "M3" else "C1"],
                          "validation_plan": [] if done else ["Read all outputs"], "findings": []},
            "findings": [],
            "plan": ["Build outputs"], "affected_paths": [] if done else ["combined.txt" if mid == "M3" else "a.txt"],
            "evidence": ["event:check"], "blocker": "", "agreed_limitations": [], "finding_dispositions": []}


def main():
    if sys.argv[1:] == ["login", "status"]:
        print("Logged in using ChatGPT (offline fixture)")
        return
    data = json.loads(sys.stdin.read().split("CURRENT HANDOFF DATA\n", 1)[1])
    session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
    print(json.dumps({"type": "thread.started", "thread_id": session}), flush=True)
    if data.get("report_repair"):
        result = json.loads(Path(data["original"]["output"]).read_text())
        result["summary"] = "Repaired report without replaying implementation"
        Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(result))
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 10}}))
        return
    if data["stage"] != "terra":
        Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(report(data)))
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 10}}))
        return
    task = data["current_task"]
    marker = Path(os.environ["AUTOCODE_BUILDER_BARRIER"])
    marker.mkdir(exist_ok=True)
    (marker / task["milestone_id"]).write_text(json.dumps({"started": time.time(), "pid": os.getpid(),
                                                        "workspace": str(Path.cwd())}))
    deadline = time.monotonic() + 10
    while len(list(marker.iterdir())) < 2:
        if time.monotonic() > deadline:
            raise RuntimeError("Builders were not concurrent")
        time.sleep(.02)
    if os.environ.get("AUTOCODE_BUILDER_FAIL") == task["milestone_id"]:
        raise SystemExit(9)
    paths = task["affected_paths"]
    for name in paths:
        p = Path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".remove"):
            p.unlink()
        elif name.endswith(".link"):
            p.symlink_to("a.txt")
        elif name.endswith(".bin"):
            p.write_bytes(b"\x00\xffbinary\x00")
        else:
            p.write_text(task["milestone_id"] + "\n")
            if name.endswith(".sh"):
                p.chmod(0o755)
    if os.environ.get("AUTOCODE_BUILDER_ESCAPE") == task["milestone_id"]:
        Path("outside.txt").write_text("out of scope")
    evidence = Path(data["state_file"]).parent / (task["id"] + "-evidence.txt")
    evidence.write_text("Fixture outputs written: " + ", ".join(paths))
    result = {"summary": "Fixture built " + task["milestone_id"], "changed_files": paths,
              "commands_run": [], "results": ["Written"], "remaining_risks": [],
              "evidence_refs": [str(evidence)],
              "addressed_requirements": task["requirements"], "untested_behavior": [], "recommended_checks": [],
              "contract_revision": data["goal_contract"]["revision"], "contract_hash": data["goal_contract"]["hash"],
              "task_id": task["id"], "deferred_backlog": [],
              "user_request": {"kind": "none", "discovered": "", "impact": "", "decision_needed": "",
                               "options": [], "proposed_delta": ""}}
    if os.environ.get("AUTOCODE_BUILDER_BAD_REPORT") == task["milestone_id"]:
        result.pop("summary")
    Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(result))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 10}}))


if __name__ == "__main__":
    main()

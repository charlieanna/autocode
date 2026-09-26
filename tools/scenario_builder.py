#!/usr/bin/env python3
"""Offline Builder for bounded-assignment scenarios. Never a live model."""
import json
import os
from pathlib import Path
import sys
import time
import uuid


def request(kind):
    return {"kind": kind, "discovered": "" if kind == "none" else "The assignment crosses a boundary",
            "impact": "" if kind == "none" else "The approved paths do not cover the change",
            "decision_needed": "" if kind == "none" else "Choose whether to replan or stop",
            "options": [] if kind == "none" else ["Replan", "Stop"],
            "proposed_delta": "" if kind == "none" else "Change the approved approach before more edits"}


def report(data, changed, summary, kind="none", *, drop_summary=False):
    task = data["current_task"]
    evidence = Path(data["state_file"]).parent / (task["id"] + "-evidence.txt")
    evidence.write_text(summary + "\n" + "\n".join(changed) + "\n")
    value = {"summary": summary, "changed_files": changed, "commands_run": [], "results": ["recorded"],
             "remaining_risks": [], "evidence_refs": [str(evidence)],
             "contract_revision": data["goal_contract"]["revision"], "contract_hash": data["goal_contract"]["hash"],
             "task_id": task["id"], "deferred_backlog": [], "user_request": request(kind),
             "addressed_requirements": task.get("requirements", []), "untested_behavior": [],
             "recommended_checks": task.get("validation_plan", [])}
    if drop_summary:
        value.pop("summary")
    return value


def write(path, text):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def act(task):
    scenario = os.environ.get("AUTOCODE_SCENARIO", "correct")
    mid = task["milestone_id"]
    if mid != "M1" and scenario not in ("overlap_write",):
        write("docs/other.txt", "independent note\n")
        return ["docs/other.txt"], "Independent note written", "none", False
    if scenario == "correct":
        write("src/greeting.py", 'GREETING = "Welcome"\n')
        return ["src/greeting.py"], "Changed greeting to Welcome", "none", False
    if scenario == "escape_tests":
        write("src/greeting.py", 'GREETING = "Welcome"\n')
        write("tests/test_greeting.py", "def test_greeting():\n    assert open('src/greeting.py').read().count('Welcome')\n")
        return ["src/greeting.py", "tests/test_greeting.py"], "Changed greeting and tests", "none", False
    if scenario == "escape_config":
        write("src/greeting.py", 'GREETING = "Welcome"\n')
        write("config/app.txt", "mutated\n")
        return ["src/greeting.py", "config/app.txt"], "Changed greeting and config", "none", False
    if scenario == "delete_unrelated":
        write("src/greeting.py", 'GREETING = "Welcome"\n')
        Path("notes/unrelated.txt").unlink()
        return ["src/greeting.py", "notes/unrelated.txt"], "Changed greeting and deleted an unrelated file", "none", False
    if scenario == "no_change":
        return [], "No source changes", "none", False
    if scenario == "crash":
        write("src/greeting.py", 'GREETING = "Welcome"\n')
        raise SystemExit(9)
    if scenario == "malformed":
        write("src/greeting.py", 'GREETING = "Welcome"\n')
        return ["src/greeting.py"], "Changed greeting to Welcome", "none", True
    if scenario == "hang":
        time.sleep(30)
        return [], "late", "none", False
    if scenario == "permission":
        return [], "Need permission before editing config", "permission", False
    if scenario == "architecture":
        return [], "Needs an architecture change outside this assignment", "infeasible", False
    if scenario == "compile_fail":
        write("src/greeting.py", "def greet(\n")
        return ["src/greeting.py"], "COMPLETE", "none", False
    if scenario == "partial":
        write("src/part_a.py", "A = True\n")
        write("src/part_b.py", "B = True\n")
        return ["src/part_a.py", "src/part_b.py"], "Implementation substantially complete.", "none", False
    if scenario == "overlap_write":
        write("src/greeting.py", f'GREETING = "{mid}"\n')
        return ["src/greeting.py"], "Both builders edited the greeting", "none", False
    raise SystemExit("unknown scenario " + scenario)


def main():
    if sys.argv[1:] == ["login", "status"]:
        print("Logged in using ChatGPT (offline fixture)")
        return
    data = json.loads(sys.stdin.read().split("CURRENT HANDOFF DATA\n", 1)[1])
    session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
    print(json.dumps({"type": "thread.started", "thread_id": session}), flush=True)
    if data.get("report_repair"):
        result = json.loads(Path(data["original"]["output"]).read_text())
        result["summary"] = result.get("summary") or "Repaired report without replaying implementation"
        Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(result))
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 10}}))
        return
    changed, summary, kind, drop_summary = act(data["current_task"])
    Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(report(data, changed, summary, kind, drop_summary=drop_summary)))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 10}}))


if __name__ == "__main__":
    main()

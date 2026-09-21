#!/usr/bin/env python3
"""Disposable fake-runner Autocode dashboard fixture for manual lifecycle checks.

It creates isolated watched and explicitly entered workspaces and never touches
a real run. Its fake runner creates an OpenCode task, removes answered questions
from temporary state, and prints action arguments for browser inspection.
"""
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from agent_console import Console, Handler, ThreadingHTTPServer

STATE = {
    "task": "Browser lifecycle fixture",
    "phase": "WAITING_FOR_USER",
    "status": "WAITING_FOR_USER",
    "iteration": 1,
    "active_stage": {"stage": "terra"},
    "pending_questions": [
        {"id": "QEnter", "question": "Enter answer", "options": ["typed"], "proposed_default": "typed", "why": "verify keyboard save"},
        {"id": "QDefault", "question": "Default answer", "options": ["default"], "proposed_default": "default", "why": "verify delegation"},
    ],
    "goal_contract": {
        "revision": 5,
        "hash": "fixture-token",
        "approval_status": "draft",
        "body": {
            "intended_outcome": "fixture",
            "constraints": ["No implicit Continue."],
            "acceptance_criteria": [
                {"id": "FIXTURE-1", "criterion": "The complete brief is visible.", "verification_method": "Browser observation", "human_review": False}
            ],
        }
    },
    "displayed_goal": "r5:fixture-token",
    "contract_history": [{
        "revision": 4,
        "hash": "fixture-initial-token",
        "approval_status": "draft",
        "body": {"milestones": [{"id": "FIXTURE-INITIAL", "objective": "Initial approved plan: inspect the fixture before acting."}]},
    }],
    "user_events": [{"kind": "goal_approval", "token": "r4:fixture-initial-token"}],
    "plan": ["Current plan: preserve the selected run and its drafts."],
    "current_task": {
        "id": "fixture-current",
        "objective": "Render the current assignment without advancing the run.",
        "requirements": ["Use saved state only."],
        "validation_plan": ["Inspect the timeline in the browser."],
        "assigned_at": "2026-09-19T22:14:47Z",
        "contract_revision": 5,
        "decision": "CONTINUE",
        "owner": "Terra",
        "next_role": "Sol",
    },
    "task_archive": [{
        "id": "fixture-initial",
        "objective": "Record the original fixture assignment.",
        "requirements": ["Keep Continue explicit."],
        "validation_plan": ["Check the complete brief."],
        "assigned_at": "2026-09-19T20:00:00Z",
        "contract_revision": 4,
        "decision": "CONTINUE",
    }],
    "decisions": [{
        "at": "2026-09-19T21:00:00Z",
        "reason": "A recorded rework decision for browser inspection.",
        "current_task": {"id": "fixture-rework", "objective": "Show the revised fixture assignment.", "contract_revision": 5, "decision": "REWORK"},
    }],
}

FAKE = r'''import json, sys
from pathlib import Path
args = sys.argv[1:]
workspace = Path(args[args.index("--workspace") + 1])
if "--run-dir" not in args:
    run = workspace / ".autocode" / "runs" / "created"
    run.mkdir(parents=True, exist_ok=True)
    (run / "state.json").write_text(json.dumps({"task": "Created OpenCode fixture", "phase": "WAITING_FOR_USER", "status": "WAITING_FOR_USER", "active_stage": {"stage": "astra"}, "pending_questions": []}))
    print("FAKE CREATE " + " ".join(args))
    raise SystemExit
run = Path(args[args.index("--run-dir") + 1])
state_path = run / "state.json"
state = json.loads(state_path.read_text())
if "--answer" in args:
    value = args[args.index("--answer") + 1]
    ident, text = value.split("=", 1)
    state.setdefault("answers", {})[ident] = {"text": text}
    state["pending_questions"] = [q for q in state["pending_questions"] if q["id"] != ident]
elif "--delegate" in args:
    ident = args[args.index("--delegate") + 1]
    state.setdefault("answers", {})[ident] = {"text": "delegated default"}
    state["pending_questions"] = [q for q in state["pending_questions"] if q["id"] != ident]
state_path.write_text(json.dumps(state))
print("FAKE ACTION " + " ".join(args))
'''

def make_workspace(root, name):
    workspace = root / name
    (workspace / ".git").mkdir(parents=True)
    run = workspace / ".autocode" / "runs" / "fixture"
    run.mkdir(parents=True)
    state = dict(STATE)
    state["pending_questions"] = [dict(question) for question in STATE["pending_questions"]]
    (run / "state.json").write_text(json.dumps(state))
    return workspace

def main():
    with tempfile.TemporaryDirectory(prefix="agent-console-browser-") as temp:
        root = Path(temp)
        left, right = make_workspace(root, "left"), make_workspace(root, "right")
        entered = root / "entered-worktree"
        entered.mkdir()
        (entered / ".git").write_text("gitdir: /tmp/fake-worktree")
        legacy_root = root / "legacy-root"
        make_workspace(legacy_root, "discovered")
        (root / "runtime-root").mkdir()
        fake = root / "fake_runner.py"
        fake.write_text(FAKE)
        console = Console([left, right], fake, lambda: None, watch_roots=[legacy_root])
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.console = console
        server.hosts = {"127.0.0.1:" + str(server.server_port), "localhost:" + str(server.server_port)}
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print("BROWSER_FIXTURE_URL=http://127.0.0.1:" + str(server.server_port), flush=True)
        print("On New task, enter " + str(entered) + ", keep OpenCode selected, provide a goal, and create it. Confirm the new run appears and its action output contains --engine opencode --no-chat only.", flush=True)
        print("Add " + str(root / "runtime-root") + " in Legacy watch roots, then remove it; the CLI legacy root remains protected.", flush=True)
        print("While a question, goal, or watch-root path field is focused, type text and select part of it; wait for polling and verify draft, focus, and selection remain intact.", flush=True)
        print("Open Astra planning to inspect the initial historically approved/inactive plan, the current draft awaiting its own approval, current assignment, and the two recorded intermediate assignment/rework cards. Viewing history does not advance the run. Then inspect every complete-brief contract section and criterion metadata before explicitly typing the displayed goal token.", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
            server.server_close()

if __name__ == "__main__":
    main()

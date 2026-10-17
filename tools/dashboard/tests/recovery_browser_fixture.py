"""Actual dashboard over disposable recovery states; no provider or real runner.

Only the CLI status/execute seam is supplied. Public projection, HTTP action
validation and browser code are production code. CLI execution is separately
covered by test_builder_recovery against the real runner and a fake provider.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autocode_recovery_view as recovery
import autocode_resolver_human as human
import autocode_run_view as run_view
from agent_console import Console, Handler, LoopbackHTTPServer
from autocode_control_tokens import private_command
from autocode_util import digest
from unified_browser_fixture import FIXTURE_NOW, base_state, fixture_base_directory

if TYPE_CHECKING:
    from autocode_run_state import RunState


def publish_novelty_hold(state: "RunState"):
    """A recovery novelty hold published by the production Resolver request path (#1012)."""
    contract = {
        "task_id": state["task_id"],
        "revision": 1,
        "body": {"open_blocking_questions": [], "acceptance_criteria": [{"id": "C1", "criterion": "Greeting"}]},
    }
    contract.update(hash=digest(contract), approval_status="approved", approval_event=None)
    state.update(goal_contract=contract, status="PAUSED_NO_PROGRESS", next_stage="astra_resolve")
    state["resolution_request"] = {"novelty_hold": {"stage": "astra_resolve", "incident_id": "incident"}}
    receipt = {"stage": "resolver", "runner_owned": True, "decision": {"action": "hold"}}
    receipt["receipt"] = {"evaluation_binding": human._binding(state)}
    state["resolver"] = {"operational_diagnostics": ["hold"], "operational_receipts": {"hold": receipt}}
    human.queue(
        state,
        "operational_exhaustion",
        {"stage": "astra_resolve", "pause_status": "PAUSED_NO_PROGRESS"},
        request={
            "kind": "blocker",
            "decision_needed": "Provide corrective information or leave paused",
            "impact": "The same incident repeated without new information",
            "options": ["Provide corrective information", "Leave paused"],
            "discovered": "No causal progress for the same incident",
            "proposed_delta": "",
        },
        evidence={"resolver_receipt_id": "hold"},
    )
    if human.evaluate(state) != "escalate":
        raise RuntimeError("The novelty hold did not publish its request")


def main():
    with tempfile.TemporaryDirectory(prefix="recovery-", dir=fixture_base_directory()) as directory:
        root = Path(directory).resolve()
        os.environ["AUTOCODE_HOME"] = str(root / "home")
        workspace = root / "Recovery project"
        (workspace / ".git").mkdir(parents=True)
        (workspace / "kept.txt").write_text("Existing project work stays intact.\n")
        runs = workspace / ".autocode/runs"
        names = []
        for viewport in ("desktop", "tablet", "mobile"):
            for kind in (
                "builder",
                "interruption",
                "quota",
                "unknown",
                "stopped",
                "internal",
                "stale",
                "source",
                "handoff",
                "held",
            ):
                name = kind + "-" + viewport
                names.append(name)
                run = runs / name
                run.mkdir(parents=True)
                state = base_state(workspace, "Inspect " + kind + " recovery", status="PAUSED_REQUESTED")
                state.update(run_dir=str(run), task_id=name, next_stage="terra")
                state.pop("active_stage", None)
                state["settings"].pop("workflow", None)
                state["settings"]["builder_retry"] = {"enabled": True}
                state["current_task"].update(id="fixture-task", milestone_id="M1", kind="implement")
                state["_fixture_monitor"]["live"] = {"state": "none", "label": "No worker in this disposable fixture"}
                if kind == "builder":
                    state["status"] = "PAUSED_BUILDER_RETRY_LIMIT"
                    state["builder_retries"] = {
                        recovery.builder_policy.key(state): {"action": "pause", "failures": ["one", "two"]}
                    }
                    state["failure_history"] = {
                        "first": {
                            "identity": {"stage": "terra", "artifact_hash": "source-one"},
                            "count": 2,
                            "attempts": ["1", "2"],
                            "last_error": "Missing accessible label",
                            "last_seen": "2026-10-01",
                        },
                        "second": {
                            "identity": {"stage": "terra", "artifact_hash": "source-two"},
                            "count": 1,
                            "attempts": ["3"],
                            "last_error": "Focus left the chat",
                            "last_seen": "2026-10-02",
                        },
                    }
                elif kind == "interruption":
                    state["status"] = "PAUSED_PROVIDER_UNCERTAIN"
                    state["active_stage"] = {
                        "iteration": 31,
                        "stage": "terra",
                        "output": str(run / "builder-01.json"),
                        "started_at": FIXTURE_NOW,
                    }
                elif kind == "quota":
                    state["status"] = "PAUSED_BUDGET"
                    state["stop_reason"] = (
                        "Quota restored; inspect and set aside the uncertain attempt before resuming."
                    )
                    state["active_stage"] = {
                        "iteration": 31,
                        "stage": "terra",
                        "output": str(run / "builder-02.json"),
                        "started_at": FIXTURE_NOW,
                    }
                    state["settings"]["roles"]["terra"]["model_pinned"] = True
                    state["failure_history"] = {
                        "quota": {
                            "identity": {"stage": "terra", "artifact_hash": "partial-source"},
                            "count": 1,
                            "attempts": ["031/builder-02"],
                            "last_error": "Provider quota exhausted",
                            "last_seen": FIXTURE_NOW,
                        }
                    }
                elif kind == "unknown":
                    state["status"] = "PAUSED_FUTURE_REASON"
                    state["stop_reason"] = "A newly introduced pause needs inspection."
                elif kind == "stale":
                    state["status"] = "RUNNING"
                    state["active_stage"] = {"stage": "terra", "iteration": 31, "output": str(run / "builder-01.json")}
                    state["_fixture_monitor"]["live"] = {"state": "exited", "label": "Recorded worker exited"}
                elif kind == "source":
                    state["status"] = "PAUSED_STAGE_ABANDONED"
                    state["next_stage"] = "investigate_bug"
                    state["job_failure"] = {
                        "reason": "Provider stopped",
                        "stage": "investigate_bug",
                        "attempt_id": "001/bug-investigation-01",
                        "job_retry_token": "historical-token",
                        "archive": str(run / "archive"),
                        "source_identity": None,
                        "write_diagnosis": {},
                        "unrestored": ["original source capture"],
                    }
                elif kind == "handoff":
                    state["status"] = "PAUSED_STALE_HANDOFF"
                    state["next_stage"] = "astra_resolve"
                    state["stop_reason"] = "Repair diagnosis needs the current reviewed source and task"
                    state["resolution_request"] = {"source_revision": "reviewed-source"}
                elif kind == "held":
                    publish_novelty_hold(state)
                elif kind == "internal":
                    state["status"] = "WAITING_FOR_USER"
                    state["pending_questions"] = [
                        {"id": "internal-question", "question": "Unpublished internal question?"}
                    ]
                else:
                    state["status"] = "PAUSED_INTERVENTION"
                    state["applied_interventions"] = [{"id": "fixture-stop", "kind": "stop", "applied_at": FIXTURE_NOW}]
                (run / "state.json").write_text(json.dumps(state))

        class FixtureConsole(Console):
            def state(self, run):
                run = Path(run).resolve()
                if run.parent != runs:
                    raise ValueError("Only this disposable fixture is available")
                return json.loads((run / "state.json").read_text())

            def _json_command(self, command, **kwargs):
                if "--status" in command:
                    run = Path(command[command.index("--run-dir") + 1])
                    state = self.state(run)
                    stop = next(
                        (row for row in state.get("applied_interventions", []) if row.get("kind") == "stop"), None
                    )
                    return {
                        "status": state["status"],
                        "stale": run.name.startswith("stale-"),
                        "active_stage": state.get("active_stage"),
                        "view": run_view.view(state),
                        "interventions": {
                            "inspector_capability": {"supported": True, "version": 1},
                            "runner_capability": {"supported": True, "version": 1, "supports_stop": True},
                            "applied_receipts": [],
                            "blocked_conditions": [],
                            "stop_intent": stop,
                        },
                    }, None
                if command[:2] == ["intervention", "inspect"]:
                    return {"version": 1, "operation": "inspect", "requests": []}, None
                return {
                    "registry_version": 1,
                    "operation": command[1],
                    "registry_path": str(root / "registry.json"),
                    "runs": [],
                    "workspaces": [],
                }, None

            def task_view(self, workspace, run):
                result = super().task_view(workspace, run)
                result["monitor"] = self.state(run)["_fixture_monitor"]
                return result

            def enqueue(self, workspace, run, label, extra, *args, **kwargs):
                state = self.state(run)
                if "--expected-recovery-token" not in extra:
                    raise ValueError("This fixture accepts only inspected recovery actions")
                recovery.require_token(state, extra[extra.index("--expected-recovery-token") + 1])
                if "--abandon-stage" in extra:
                    active = state.get("active_stage") or {}
                    attempt = f"{active['iteration']:03d}/{Path(active['output']).stem}"
                    if extra[extra.index("--abandon-stage") + 1] != attempt or "--resume-paused" in extra:
                        raise ValueError("Recover only the exact saved attempt; Resume is separate")
                    state.pop("active_stage", None)
                    state["status"] = (
                        "PAUSED_STAGE_ABANDONED" if run.name.startswith("quota-") else "PAUSED_INTERVENTION"
                    )
                    state["stages"].append(
                        {
                            "stage": active["stage"],
                            "iteration": active["iteration"],
                            "finished_at": FIXTURE_NOW,
                            "abandoned": True,
                            "attempt_id": attempt,
                        }
                    )
                elif "--accept-source-edit" in extra:
                    if "--resume-paused" not in extra or state["status"] != "PAUSED_STALE_HANDOFF":
                        raise ValueError("Accept only the inspected edited-source pause, with its resume")
                    state["status"] = "RUNNING"
                    state["next_stage"] = "astra_resolve"
                elif "--retry-failed-stage" in extra:
                    if "--resume-paused" not in extra or not run.name.startswith("held-"):
                        raise ValueError("Retry only the inspected held step, with its resume")
                    state["status"] = "RUNNING"
                elif "--retry-builder" in extra:
                    state["iteration"] += 1
                    state["builder_retries"][recovery.builder_policy.key(state)]["failures"].append(
                        "retained-new-failure"
                    )
                    state["stages"].append(
                        {"stage": "terra", "iteration": state["iteration"], "finished_at": FIXTURE_NOW, "exit_code": 1}
                    )
                else:
                    state["status"] = "RUNNING"
                (run / "state.json").write_text(json.dumps(state))
                record = {
                    "id": str(time.time_ns()),
                    "label": label,
                    "command": ["fixture-cli", *private_command(extra)[0]],
                    "status": "finished",
                    "exit_status": 0,
                    "queued_at": time.time(),
                    "started_at": time.time(),
                    "finished_at": time.time(),
                    "stdout": "Disposable fixture command recorded.",
                    "stderr": "",
                }
                self.actions.setdefault(str(run), []).append(record)
                self.status_cache.pop(str(run), None)
                return record

        console = FixtureConsole(
            [workspace],
            root / "no-runner",
            lambda: False,
            conversation_root=root / "conversations",
            project_store_root=root / "dashboard",
            catalogue_command=(
                sys.executable,
                "-c",
                "print('openai/gpt-6-astra\\nopenai/gpt-5.6-terra\\nopenai/gpt-5.6-sol')",
            ),
        )
        server = LoopbackHTTPServer(("127.0.0.1", 0), Handler)
        server.console = console
        server.hosts = {f"127.0.0.1:{server.server_port}", f"localhost:{server.server_port}"}
        base = f"http://127.0.0.1:{server.server_port}/"
        print(
            "FIXTURE="
            + json.dumps(
                {
                    "base_url": base,
                    "workspace": str(workspace),
                    "scenarios": {
                        name: base + "#" + urlencode({"task": str(workspace), "run": str(runs / name)})
                        for name in names
                    },
                }
            ),
            flush=True,
        )
        try:
            server.serve_forever()
        finally:
            server.server_close()
            console.pool.shutdown(wait=True)


if __name__ == "__main__":
    main()

"""Real chat HTTP/UI with offline provider callbacks and owned disposable data.

Attaching a conversation goes through the dashboard's real handoff. The one task
it creates gets the runner's task worktree, a minimal first checkpoint and the
runner's own journal ingestion, then stops: no stage, provider or model runs.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

TOOLS = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(TOOLS / "dashboard"), str(TOOLS)]
import autocode_task_input as task_input
import autocode_workspaces as task_workspaces
from agent_console import Console, Handler, LoopbackHTTPServer
from autocode_conversation_ingress import ingest as ingest_handoff
from conversation_transport import ConversationProviderError
from dashboard_setup import conversation_model_fields


def main():
    base = Path(os.environ["AUTOCODE_FIXTURE_ROOT"])
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="draft-cadence-", dir=base) as temporary:
        root = Path(temporary).resolve()
        os.environ["AUTOCODE_HOME"] = str(root / "home")
        project = root / "Example project"
        project.mkdir(parents=True)
        # A real repository with one commit: an attached task gets its own
        # worktree from it, as the runner gives one to every new task.
        for args in (
            ["init", "-q"],
            [
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@localhost",
                "commit",
                "--allow-empty",
                "-qm",
                "Initialize project",
            ],
        ):
            subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True, timeout=30)
        calls, lock = [], threading.Lock()
        call_path = root / "provider-calls.json"
        # A 'Slow draft' Planner call stays in flight, after a real launch
        # receipt, until the browser test creates this file.
        gate = root / "release-slow-draft"

        def record(role, messages):
            human = next(row for row in reversed(messages) if row["role"] == "user")
            with lock:
                calls.append({"role": role, "turn": human["logical_turn_id"], "text": human["text"]})
                pending = call_path.with_suffix(".tmp")
                pending.write_text(json.dumps(calls))
                pending.replace(call_path)
            return human

        def gatherer(messages, model, workdir):
            record("gatherer", messages)
            return "Your answer is saved. Tell me about the next requirement."

        def planner(messages, route, workdir, **delivery):
            human = record("planner", messages)
            with lock:
                attempts = sum(row["role"] == "planner" and row["turn"] == human["logical_turn_id"] for row in calls)
            if human["text"] == "Safe draft failure" and attempts == 1:
                delivery["dispatch_observer"]("safe_not_dispatched", {})
                raise ConversationProviderError("Fixture worker unavailable before launch", outcome="not_dispatched")
            if human["text"] == "Ambiguous draft failure":
                raise ConversationProviderError("Fixture lost the provider receipt")
            if human["text"].startswith("Slow draft"):
                delivery["dispatch_observer"]("process_starting", {})
                delivery["dispatch_observer"]("process_started", {"owned": True, "pid": None})
                deadline = time.monotonic() + 120
                while not gate.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
            return json.dumps(
                {
                    "contract_version": 1,
                    "kind": "autocode.planner-structured-draft",
                    "goal": "Build a clear chat workspace",
                    "requirements": ["Keep project conversations and all answers"],
                    "milestones": ["Build and verify the chat"],
                    "parallelism": [],
                    "unresolved_questions": [],
                    "source_revision": {
                        "requirements_revision": sum(row["role"] == "user" for row in messages),
                        "logical_turn_id": human["logical_turn_id"],
                    },
                    "attribution": {
                        "role": "planner",
                        "model": route["model"],
                        "reasoning_effort": route["reasoning_effort"],
                    },
                    "freshness": {"state": "fresh", "updated_at": "2026-10-03T00:00:00+00:00"},
                }
            )

        # Only this offline fixture supplies the precise pre-launch receipt; an
        # unobserved callback must remain uncertain and must never be retried.
        planner.autocode_dispatch_aware = True

        created = []

        class OfflineConsole(Console):
            def _probe_conversation_transport(self, executable):
                # Supply only the external version probe; real route and transport
                # admission still run without a host tool or a provider request.
                return {"status": "ok", "data": {"version": "1.18.33"}}

            def _json_command(self, command, **kwargs):
                if "--status" in command:
                    # A read of a task this fixture created: the saved checkpoint,
                    # as the unified browser fixture supplies it.
                    run = Path(command[command.index("--run-dir") + 1]).resolve()
                    if run not in created or not (run / "state.json").is_file():
                        return None, "Unknown disposable fixture run"
                    return json.loads((run / "state.json").read_text(encoding="utf8")), None
                if command[0] != "registry":
                    raise AssertionError("This fixture must not invoke a task runner")
                return {
                    "registry_version": 1,
                    "operation": command[1],
                    "registry_path": str(root / "registry.json"),
                    "runs": [],
                    "workspaces": [],
                }, None

            def enqueue(self, workspace, run, label, extra, on_complete=None):
                # The only launch allowed is the one task an attached
                # conversation creates. In place of the runner process, do what
                # autocode_run_setup does for a new task without --in-place:
                # a task worktree from autocode_workspaces.create and a first
                # checkpoint in it (minimal, no plan yet). Then link the staged
                # handoff with the runner's own ingestion, which writes the
                # conversation journal, and stop before any stage.
                if run is not None or label != "Create OpenCode task" or "--conversation-handoff" not in extra:
                    raise ValueError("No implementation or task control is allowed in this fixture")
                if created:
                    raise ValueError("This fixture creates one attached task only")
                action = self._record(
                    str(workspace), label, [sys.executable, str(self.runner), "--workspace", str(workspace), *extra]
                )
                isolated = task_workspaces.create(workspace, extra[0])
                task_workspace = Path(isolated["workspace"])
                run_dir = task_workspace / ".autocode" / "runs" / ("attached-" + action["id"][:12])
                run_dir.mkdir(parents=True)
                state = {
                    "version": 2,
                    "target": "code",
                    "workspace": str(task_workspace),
                    "created_at": "2026-10-03T00:01:00+00:00",
                    "iteration": 1,
                    "status": "RUNNING",
                    "sessions": {},
                    "history": [],
                    "stages": [],
                    "no_progress_batches": 0,
                    "next_stage": "astra_plan",
                    "acceptance_criteria": [],
                    "project_workspace": isolated["project_workspace"],
                    "task_branch": isolated["branch"],
                    "base_commit": isolated["base_commit"],
                    "settings": {"joint_planning": True},
                }
                task_input.assign(state, extra[0])
                ingest_handoff(state, run_dir, task_workspace, extra[extra.index("--conversation-handoff") + 1])
                (run_dir / "state.json").write_text(json.dumps(state), encoding="utf8")
                created.append(run_dir.resolve())
                action.update(status="finished", exit_status=0, started_at=time.time(), finished_at=time.time())
                return action

        catalogue = root / "models.py"
        models = sorted({route["model"] for route in conversation_model_fields()["conversation_routes"].values()})
        catalogue.write_text("print(" + repr("\n".join(models)) + ")\n", encoding="utf8")
        console = OfflineConsole(
            [project],
            root / "no-runner",
            lambda: False,
            conversation_root=root / "conversations",
            project_store_root=root / "dashboard",
            catalogue_command=(sys.executable, str(catalogue)),
            conversation_provider=gatherer,
            conversation_planner=planner,
        )
        documents = {
            size: console.conversations.create_empty(workspace=str(project), request_id="empty-" + size)
            for size in ("desktop", "tablet", "mobile", "attached")
        }
        server = LoopbackHTTPServer(("127.0.0.1", 0), Handler)
        server.console = console
        server.hosts = {f"127.0.0.1:{server.server_port}", f"localhost:{server.server_port}"}
        base_url = f"http://127.0.0.1:{server.server_port}/"
        print(
            "FIXTURE="
            + json.dumps(
                {
                    "urls": {
                        size: base_url + "#" + urlencode({"conversation": doc["id"]}) for size, doc in documents.items()
                    },
                    "calls": str(call_path),
                    "gate": str(gate),
                    "project": str(project.resolve()),
                }
            ),
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            console.pool.shutdown(wait=True)
            console.conversations.close()


if __name__ == "__main__":
    main()

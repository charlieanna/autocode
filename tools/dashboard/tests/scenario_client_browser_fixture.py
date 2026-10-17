"""One catalog scenario driven end to end through the chat client (#730 acceptance).

The browser test uses the real dashboard: it sends the brief in chat, attaches the
project, approves and builds the plan, and reads each requirement's changes in the
Checks pane. The task the attach launches is the real AutoCode runner against the
scenario harness's scripted provider (``scenarios/harness/fake_codex.py``), which plans
parallel-diamond's four milestones, builds each from the reference solution and runs
B and C as one parallel batch.

The seams are the providers and the scenario's run flags, nothing else:
- The chat's Requirements and Planner callbacks are scripted, as in
  ``draft_cadence_browser_fixture``, so no chat model runs.
- The task runs on OpenCode, as every attached conversation does. PATH resolves
  ``opencode`` to the checked fake client (``tools/fake_opencode.py``), which hands each
  stage to its sibling ``codex``: the harness's scripted model, as in
  ``scenarios/test_contained_opencode.py``.
- The one launch the attach makes is the dashboard's own command plus the flags a
  scripted scenario run takes: ``--evidence-provenance fake``, the scenario's own flags,
  and ``--allow-uncontained-tools``, since this test is about the client and its diffs,
  not the tool boundary (``scenarios/test_contained_opencode.py`` covers that).
  Approve and build are the dashboard's own commands, unchanged.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlencode

TOOLS = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(TOOLS / "dashboard"), str(TOOLS), str(TOOLS.parent / "scenarios")]
from agent_console import Console, Handler, LoopbackHTTPServer
from dashboard_setup import conversation_model_fields
from harness import catalog
from harness.driver import FAKE_BILLING_ENV, STRIP_BILLING_AUTH, fake_setup
from harness.project import materialize, without_maintenance

SCENARIO = "parallel-diamond"


def expected_changes(scenario):
    """Each requirement's reference files and their lines; the scripted Planner names requirement C_<milestone>."""
    return {
        "C_" + milestone["id"]: {
            path: [line for line in (scenario.reference / path).read_text().splitlines() if line.strip()]
            for path in milestone["paths"]
        }
        for milestone in scenario.fake_milestones
    }


def main():
    base = Path(os.environ["AUTOCODE_FIXTURE_ROOT"])
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="scenario-client-", dir=base) as temporary:
        root = Path(temporary).resolve()
        scenario = catalog.load(SCENARIO)
        project = without_maintenance(materialize(scenario.seed, root / "project"))
        _, env = fake_setup(scenario, root, scenario.reference)
        shutil.copy2(TOOLS / "fake_opencode.py", root / "bin" / "opencode")
        (root / "bin" / "opencode").chmod(0o755)
        scenario_flags = ["--evidence-provenance", "fake", *scenario.fake_flags, "--allow-uncontained-tools"]
        if env.pop(STRIP_BILLING_AUTH, None):
            for key in FAKE_BILLING_ENV:
                os.environ.pop(key, None)
        (root / "config").mkdir()
        os.environ.update(env, AUTOCODE_HOME=str(root / "home"), XDG_CONFIG_HOME=str(root / "config"))

        def gatherer(messages, model, workdir):
            return "Saved. Attach a project when the plan draft looks right."

        def planner(messages, route, workdir, **delivery):
            human = next(row for row in reversed(messages) if row["role"] == "user")
            return json.dumps(
                {
                    "contract_version": 1,
                    "kind": "autocode.planner-structured-draft",
                    "goal": "Build a four-milestone dependency graph",
                    "requirements": [row["objective"] for row in scenario.fake_milestones],
                    "milestones": [row["id"] + ": " + row["objective"] for row in scenario.fake_milestones],
                    "parallelism": ["B and C are independent and can be built in parallel"],
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
                    "freshness": {"state": "fresh", "updated_at": "2026-10-17T00:00:00+00:00"},
                }
            )

        # The scripted callback returns before any process starts, so its delivery is never uncertain.
        planner.autocode_dispatch_aware = True

        class ScenarioConsole(Console):
            def _probe_conversation_transport(self, executable):
                # Supply only the external version probe; route and transport admission still run.
                return {"status": "ok", "data": {"version": "1.18.33"}}

            def enqueue(self, workspace, run, label, extra, on_complete=None):
                if run is None:
                    if label != "Create OpenCode task" or "--conversation-handoff" not in extra:
                        raise ValueError("Only the attached conversation's task may be created in this fixture")
                    extra = [*extra, *scenario_flags]
                return super().enqueue(workspace, run, label, extra, on_complete)

        catalogue = root / "models.py"
        models = sorted({route["model"] for route in conversation_model_fields()["conversation_routes"].values()})
        catalogue.write_text("print(" + repr("\n".join(models)) + ")\n", encoding="utf8")
        console = ScenarioConsole(
            [project],
            TOOLS / "autocode.py",
            lambda: False,
            conversation_root=root / "conversations",
            project_store_root=root / "dashboard",
            catalogue_command=(sys.executable, str(catalogue)),
            conversation_provider=gatherer,
            conversation_planner=planner,
        )
        document = console.conversations.create_empty(workspace=str(project), request_id="scenario-client")
        server = LoopbackHTTPServer(("127.0.0.1", 0), Handler)
        server.console = console
        server.hosts = {f"127.0.0.1:{server.server_port}", f"localhost:{server.server_port}"}
        base_url = f"http://127.0.0.1:{server.server_port}/"
        print(
            "FIXTURE="
            + json.dumps(
                {
                    "url": base_url + "#" + urlencode({"conversation": document["id"]}),
                    "project": str(project),
                    "brief": scenario.brief,
                    "expected": expected_changes(scenario),
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

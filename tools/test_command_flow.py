"""One config-registered tool runs planning, build, receipt-backed validation, and completion."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from tools import autocode as runner, autocode_providers, autocode_workspaces


class ConfigToolFlow(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Fixture", "-c", "user.email=f@example.test",
                        "commit", "--allow-empty", "-qm", "fixture"], check=True)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        source = Path(__file__).resolve().parent
        shutil.copy2(source / "fake_command_tool.py", bin_dir / "fixture-tool")
        shutil.copy2(source / "goal_fixtures.py", bin_dir / "goal_fixtures.py")
        (bin_dir / "fixture-tool").chmod(0o755)
        config_home = self.root / "config"
        provider = config_home / "autocode" / "providers"
        provider.mkdir(parents=True)
        (provider / "fixturetool.toml").write_text(textwrap.dedent("""\
            name = "fixturetool"
            command = ["fixture-tool", "--report", "{report}", "--sandbox", "{sandbox}", "--model", "{model}", "--role", "{role}"]
            prompt = "stdin"
            models = ["fixture-model"]
            version_command = ["fixture-tool", "--version"]

            [roles]
            astra = { model = "fixture-model", effort = "high" }
            terra = { model = "fixture-model", effort = "medium" }
            sol = { model = "fixture-model", effort = "high" }
            completion = { model = "fixture-model", effort = "medium" }
            glm = { model = "fixture-model", effort = "medium" }
            plan_reviewer = { model = "fixture-model", effort = "high" }
        """))
        self.env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                    "PYTHONDONTWRITEBYTECODE": "1", "XDG_CONFIG_HOME": str(config_home),
                    "AUTOCODE_HOME": str(self.root / "registry-home")}
        self.entry = [sys.executable, str(source / "autocode.py")]

    def configure_fixture(self, *, prompt="stdin", observe_stdin=False, track_invocations=False, events=False):
        command = ["fixture-tool", "--report", "{report}", "--sandbox", "{sandbox}", "--model", "{model}", "--role", "{role}"]
        if prompt == "file":
            command += ["--prompt-file", "{prompt_file}"]
        if observe_stdin:
            command += ["--stdin-observation", "{run_dir}/stdin-observation.json"]
        if track_invocations:
            command += ["--invocations", "{run_dir}/invocations.txt"]
        output = ""
        if events:
            command += ["--events", "--sessions-log", "{run_dir}/sessions.jsonl"]
            output = 'output = "opencode_events"\nresume = ["--session", "{session}"]\n'
        provider = self.root / "config" / "autocode" / "providers" / "fixturetool.toml"
        provider.write_text(textwrap.dedent(f'''\
            name = "fixturetool"
            command = {json.dumps(command)}
            prompt = "{prompt}"
            {output.replace(chr(10), chr(10) + "            ")}models = ["fixture-model"]
            version_command = ["fixture-tool", "--version"]

            [roles]
            astra = {{ model = "fixture-model", effort = "high" }}
            terra = {{ model = "fixture-model", effort = "medium" }}
            sol = {{ model = "fixture-model", effort = "high" }}
            completion = {{ model = "fixture-model", effort = "medium" }}
            glm = {{ model = "fixture-model", effort = "medium" }}
            plan_reviewer = {{ model = "fixture-model", effort = "high" }}
        '''))

    def launch(self, *args, answers="CLI\nyes\n"):
        return subprocess.run([*self.entry, "--provider", "fixturetool", "--workspace", str(self.project),
                               "--in-place", *args], cwd=self.root, env=self.env, input=answers,
                              capture_output=True, text=True, timeout=90)

    def complete_run(self, *, prompt="stdin", observe_stdin=False, track_invocations=False, events=False):
        self.configure_fixture(prompt=prompt, observe_stdin=observe_stdin, track_invocations=track_invocations,
                               events=events)
        result = self.launch("--chat", "Build a greeting tool")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        run = next((self.project / ".autocode/runs").iterdir())
        return run, json.loads((run / "state.json").read_text())

    def test_bootstrap_uses_a_ds_store_only_directory(self):
        selected = self.root / "ds-store-only"
        selected.mkdir()
        (selected / ".DS_Store").write_text("finder metadata")

        project = autocode_workspaces.bootstrap(selected, "Config tool fixture")

        self.assertEqual(selected, project)
        self.assertTrue((selected / ".git").is_dir())
        self.assertFalse((selected / "autocode-projects").exists())
        baseline = subprocess.run(["git", "-C", str(selected), "log", "-1", "--format=%s"],
                                  check=True, capture_output=True, text=True)
        self.assertEqual("Autocode project baseline", baseline.stdout.strip())

    def test_config_tool_completes_with_a_capture_receipt(self):
        result = subprocess.run([*self.entry, "--provider", "fixturetool", "--workspace", str(self.project),
                                 "--in-place", "--chat", "Build a greeting tool"],
                                cwd=self.root, env=self.env, input="CLI\nyes\n",
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        run = next((self.project / ".autocode/runs").iterdir())
        state = json.loads((run / "state.json").read_text())
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual("fixturetool", state["settings"]["provider"])
        self.assertEqual({}, state["sessions"])
        sol = next(record for record in state["stages"] if record["stage"] == "sol")
        report = json.loads(Path(sol["output"]).read_text())
        self.assertTrue(report["checks"][0]["evidence_ref"].endswith("sol-greet.json"))
        self.assertFalse(report["checks"][0]["evidence_ref"].startswith("event:"))
        self.assertTrue(Path(report["checks"][0]["evidence_ref"]).is_file())
        self.assertEqual("read-only", sol["command"][sol["command"].index("--sandbox") + 1])
        terra = next(record for record in state["stages"] if record["stage"] == "terra")
        self.assertEqual("workspace-write", terra["command"][terra["command"].index("--sandbox") + 1])

    def test_event_stream_tool_completes_with_sessions_usage_and_event_evidence(self):
        run, state = self.complete_run(events=True)
        self.assertEqual("TASK_COMPLETE", state["status"])
        logged = [json.loads(line) for line in (run / "sessions.jsonl").read_text().splitlines()]
        execution = [record for record in state["stages"] if not record.get("planning")]
        self.assertTrue(execution)
        for record in execution:
            self.assertTrue(record["supports_sessions"])
            self.assertEqual("fixturetool", record["provider"])
            self.assertNotIn("permission_config", record)
            self.assertFalse(Path(record["output"]).with_suffix(".opencode.json").exists())
            self.assertEqual({"input_tokens": 140, "cached_input_tokens": 40, "output_tokens": 20,
                              "reasoning_output_tokens": 0}, record["metrics"]["provider_tokens"])
        self.assertTrue(state["sessions"])
        self.assertTrue(set(state["sessions"].values()) <= {entry["session"] for entry in logged})
        sol = next(record for record in execution if record["stage"] == "sol")
        report = json.loads(Path(sol["output"]).read_text())
        self.assertEqual("event:prt_check", report["checks"][0]["evidence_ref"])
        self.assertEqual(sol["thread_id"], state["sessions"]["sol"])

    def test_file_prompt_delivers_prompt_with_closed_stdin(self):
        run, _ = self.complete_run(prompt="file", observe_stdin=True)
        observed = json.loads((run / "stdin-observation.json").read_text())
        self.assertEqual("", observed["stdin"])
        self.assertIn("CURRENT HANDOFF DATA", observed["prompt"])

    def test_file_prompt_fixture_observes_actual_stdin(self):
        prompt_file = self.root / "fixture-prompt.md"
        observation = self.root / "stdin-observation.json"
        report = self.root / "fixture-report.json"
        prompt_file.write_text("PROMPT_FILE_MARKER\nCURRENT HANDOFF DATA\n" + json.dumps({
            "report_repair": True,
            "original": {"contract_revision": 7, "contract_hash": "fixture", "task_id": "fixture"},
        }))
        marker = "ACTUAL_STDIN_MARKER"
        result = subprocess.run(
            [sys.executable, self.env["PATH"].split(os.pathsep)[0] + "/fixture-tool", "--report", str(report),
             "--prompt-file", str(prompt_file), "--stdin-observation", str(observation)],
            input=marker, capture_output=True, text=True, timeout=30)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        observed = json.loads(observation.read_text())
        self.assertEqual(marker, observed["stdin"])
        self.assertIn("PROMPT_FILE_MARKER", observed["prompt"])

    def test_sessionless_checkpoint_resumes_without_relaunch(self):
        run, state = self.complete_run(track_invocations=True)
        terra_index = next(index for index, record in enumerate(state["stages"]) if record["stage"] == "terra")
        record = copy.deepcopy(state["stages"][terra_index])
        for later in state["stages"][terra_index + 1:]:
            base = Path(later["output"]).with_suffix("")
            for artifact in base.parent.glob(base.name + ".*"):
                artifact.unlink()
        state["stages"] = state["stages"][:terra_index]
        state["history"] = state["history"][:terra_index]
        state.update(status="RUNNING", phase="EXECUTING", next_stage="terra", active_stage=record)
        (run / "state.json").write_text(json.dumps(state))
        before = (run / "invocations.txt").read_text().splitlines().count("terra")

        resumed = self.launch("--run-dir", str(run), "--no-chat", answers="")

        after = (run / "invocations.txt").read_text().splitlines().count("terra")
        self.assertEqual(before, after)
        saved = json.loads((run / "state.json").read_text())
        self.assertNotEqual("terra", saved["next_stage"])
        self.assertNotEqual("terra", saved.get("active_stage", {}).get("stage"))
        self.assertTrue(any(stage.get("recovered_at") for stage in saved["stages"] if stage["stage"] == "terra"))

    def test_sessionless_incomplete_checkpoints_pause_without_relaunch(self):
        run, state = self.complete_run(track_invocations=True)
        terra = next(record for record in state["stages"] if record["stage"] == "terra")
        before = (run / "invocations.txt").read_text().splitlines().count("terra")
        output = Path(terra["output"])
        for name, update in (("absent-exit", lambda record: record.pop("exit_code")),
                             ("nonzero-exit", lambda record: record.update(exit_code=1)),
                             ("missing-report", lambda record: output.rename(output.with_suffix(".saved")))):
            with self.subTest(name=name):
                active = copy.deepcopy(terra)
                state.update(status="RUNNING", phase="EXECUTING", next_stage="terra", active_stage=active)
                update(active)
                (run / "state.json").write_text(json.dumps(state))
                result = self.launch("--run-dir", str(run), "--no-chat", answers="")
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                saved = json.loads((run / "state.json").read_text())
                self.assertIn("active_stage", saved)
                self.assertEqual(before, (run / "invocations.txt").read_text().splitlines().count("terra"))
                if name == "missing-report":
                    output.with_suffix(".saved").rename(output)

    def test_sessionless_invalid_report_runs_report_only_repair(self):
        run, state = self.complete_run(track_invocations=True)
        terra = next(record for record in state["stages"] if record["stage"] == "terra")
        Path(terra["output"]).write_text("{}")
        state.update(status="RUNNING", phase="EXECUTING", next_stage="terra", active_stage=copy.deepcopy(terra))
        original_provider = runner.opencode
        self.addCleanup(setattr, runner, "opencode", original_provider)
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config"), "PATH": self.env["PATH"]}):
            runner.opencode = autocode_providers.resolve("fixturetool")
            with self.assertRaises(runner.ReportRepairQueued):
                runner.reconcile_active(state, run, self.project)
            runner.execute_report_repair(state, run, self.project)

        self.assertNotIn("pending_report_repair", state)
        self.assertTrue(state["report_repair_history"])
        self.assertEqual("terra_report_repair", state["stages"][-1]["stage"])

    def test_sessionless_legacy_repair_and_planning_retry_accept_durable_reports(self):
        run, state = self.complete_run()
        terra = next(record for record in state["stages"] if record["stage"] == "terra")
        legacy = copy.deepcopy(state)
        rejected = copy.deepcopy(terra)
        rejected.update(rejected=True,
                        rejection_reason="Implementation evidence references a missing executed event: event:missing")
        legacy.update(status="PAUSED_INVALID_OUTPUT", phase="PAUSED_OR_BLOCKED", stages=[rejected])
        legacy["settings"]["report_repair"] = {"max_attempts": 1}
        self.assertTrue(runner.recover_legacy_report_repair(legacy, run, self.project))

        planning = copy.deepcopy(state)
        active = copy.deepcopy(terra)
        active.update(stage="astra_discovery", rejected=True)
        planning.update(status="PAUSED_INVALID_OUTPUT", next_stage="astra_discovery", active_stage=active)
        self.assertTrue(runner.prepare_planning_retry(planning, run))
        self.assertNotIn("active_stage", planning)


if __name__ == "__main__":
    unittest.main()

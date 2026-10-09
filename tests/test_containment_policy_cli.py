"""#413 through the CLI: an OpenCode run that cannot have the kernel tool boundary is refused at setup,
before planning spends anything, unless the user explicitly accepts uncontained tools.

The client is the checked fake OpenCode (--version 1.18.31); the runner's own boundary checks are
unchanged (opencode_fixture_cli native_boundary). On Linux CI the refusal names the platform instead.
qualified_at_setup has only the run-setup check report a qualified host, so the first contained
launch fails its boundary: the PAUSED_TOOL_CONTAINMENT path when OpenCode changes mid-run.
"""
import json
from pathlib import Path
import shutil
import unittest

from . import opencode_fixture_cli as fixture_cli
from . import test_subprocess as subprocess_tests

FLAG = "--allow-uncontained-tools"


class UncontainedToolsFlow(unittest.TestCase):
    new_run_engine_args = ()
    launch = subprocess_tests.SubprocessFlow.launch
    saved = subprocess_tests.SubprocessFlow.saved

    def setUp(self):
        subprocess_tests.SubprocessFlow.setUp(self)
        source = Path(__file__).resolve().parents[1] / "tools"
        target = self.root / "fixture-bin/opencode"
        shutil.copy2(source / "fake_opencode.py", target)
        target.chmod(0o755)
        self.cli = self.entry
        self.entry = fixture_cli.entrypoint(self.cli, native_boundary=True)
        self.env.update(CODEX_HOME=str(self.root / "codex-config"), XDG_CONFIG_HOME=str(self.root / "config"))
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "OPENCODE_CONFIG_CONTENT"):
            self.env.pop(key, None)

    def status_view(self, run):
        return json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]

    def assert_refused(self, result):
        self.assertIn("Refused before any stage launched", result.stderr)
        self.assertRegex(result.stderr, r"this machine (is \w+|has OpenCode 1\.18\.31)")
        self.assertIn(FLAG, result.stderr)

    def test_new_run_is_refused_before_planning_spends_anything(self):
        self.assert_refused(self.launch(["Build a greeting tool", "--no-chat"], 2))
        runs = self.project / ".autocode/runs"
        self.assertEqual([], list(runs.glob("*/state.json")) if runs.exists() else [])

    def test_explicit_opt_out_builds_and_checks_uncontained_and_says_so(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.launch(["Build a greeting tool", "--no-chat", FLAG], 2)
        run, _ = self.saved()
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        self.launch(["--run-dir", str(run), "--approve-goal", self.saved()[1]["displayed_goal"]], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 0)  # the saved choice needs no repeat
        _, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertIs(True, state["settings"]["allow_uncontained_tools"])
        accepted = [event for event in state["user_events"] if event["kind"] == "uncontained_tools_accepted"]
        self.assertEqual(1, len(accepted))
        self.assertEqual(("user_cli", FLAG), (accepted[0]["actor"], accepted[0]["flag"]))
        self.assertTrue(accepted[0]["at"] and accepted[0]["reason"])
        launched = {record["stage"]: record for record in state["stages"] if record.get("engine") == "opencode"}
        for stage in ("terra", "sol", "astra_review"):
            self.assertIs(True, launched[stage]["uncontained_tools"], stage)
            self.assertIsNone(launched[stage]["tool_containment"])
            self.assertIn("--allow-uncontained-tools", launched[stage]["isolation"])
        self.assertNotIn("uncontained_tools", launched["recognize_workflow"])  # planning is never contained
        self.assertEqual("uncontained_user_accepted", self.status_view(run)["tool_containment"])

    def test_a_saved_run_without_the_opt_out_is_refused_on_resume_until_the_user_accepts(self):
        # A run saved before #413 (or before OpenCode changed): no opt-out in its settings.
        self.launch(["Build a greeting tool", "--no-chat", FLAG], 2)
        run, state = self.saved()
        state["settings"].pop("allow_uncontained_tools")
        state["user_events"] = [e for e in state.get("user_events", []) if e["kind"] != "uncontained_tools_accepted"]
        (run / "state.json").write_text(json.dumps(state))
        before = (run / "state.json").read_bytes()
        self.assertEqual("contained", self.status_view(run)["tool_containment"])
        for resume in (["--no-chat"], ["--resume-paused", "--no-chat"]):
            self.assert_refused(self.launch(["--run-dir", str(run), *resume], 2))
            self.assertEqual(before, (run / "state.json").read_bytes(), "a refusal changes nothing")
        self.launch(["--run-dir", str(run), "--no-chat", FLAG], 2)
        _, resumed = self.saved()
        self.assertIs(True, resumed["settings"]["allow_uncontained_tools"])
        self.assertEqual(["uncontained_tools_accepted"],
                         [e["kind"] for e in resumed["user_events"] if e["kind"] == "uncontained_tools_accepted"])
        # Saved: the next resume needs no flag.
        result = self.launch(["--run-dir", str(run), "--no-chat"], 2)
        self.assertNotIn("Refused", result.stderr)
        self.assertEqual("uncontained_user_accepted", self.status_view(run)["tool_containment"])

    def test_a_boundary_lost_after_setup_pauses_naming_the_flag_and_the_flag_continues(self):
        # Setup found the boundary available (e.g. OpenCode was 1.18.33 then); the Builder's launch
        # fails its boundary (the client is now 1.18.31, or this is not macOS) and pauses before launching.
        unqualified = self.entry
        self.entry = fixture_cli.entrypoint(self.cli, qualified_at_setup=True)
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.launch(["Build a greeting tool", "--no-chat"], 2)
        run, _ = self.saved()
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        self.launch(["--run-dir", str(run), "--approve-goal", self.saved()[1]["displayed_goal"]], 0)
        paused = self.launch(["--run-dir", str(run), "--no-chat"], 2)
        _, state = self.saved()
        self.assertEqual("PAUSED_TOOL_CONTAINMENT", state["status"], paused.stderr)
        self.assertIn("Native tool boundary was not established", paused.stdout + paused.stderr)
        self.assertIn(FLAG, paused.stdout + paused.stderr)
        self.assertFalse(any(record["stage"] == "terra" for record in state["stages"]))
        self.assertEqual("contained", self.status_view(run)["tool_containment"])
        # Resumed where setup now sees the change: refused, unchanged, until the user accepts.
        self.entry = unqualified
        before = (run / "state.json").read_bytes()
        self.assert_refused(self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2))
        self.assertEqual(before, (run / "state.json").read_bytes(), "a refusal changes nothing")
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat", FLAG], 0)
        _, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        accepted = [event for event in state["user_events"] if event["kind"] == "uncontained_tools_accepted"]
        self.assertEqual(1, len(accepted))
        self.assertRegex(accepted[0]["reason"], r"this machine (is \w+|has OpenCode 1\.18\.31)")
        terra = [record for record in state["stages"] if record["stage"] == "terra"]
        self.assertTrue(terra and all(record.get("uncontained_tools") is True for record in terra))
        self.assertEqual("uncontained_user_accepted", self.status_view(run)["tool_containment"])

    def test_a_codex_run_with_an_opencode_investigator_is_refused_early_and_accepts_the_flag(self):
        # The pinned Investigator is the run's only built-in OpenCode stage that is not planning.
        pinned = ["Build a greeting tool", "--no-chat", "--engine", "codex",
                  "--investigator-model", "zai-coding-plan/glm-5.3"]
        self.assert_refused(self.launch(pinned, 2))
        runs = self.project / ".autocode/runs"
        self.assertEqual([], list(runs.glob("*/state.json")) if runs.exists() else [])
        self.launch([*pinned, FLAG], 2)
        [saved] = runs.glob("*/state.json")  # the refused attempt left only its lock directory
        run, state = saved.parent, json.loads(saved.read_text())
        self.assertEqual("opencode", state["settings"]["stuck_investigation"]["route"]["engine"])
        self.assertIs(True, state["settings"]["allow_uncontained_tools"])
        self.assertEqual("uncontained_user_accepted", self.status_view(run)["tool_containment"])

    def test_the_flag_is_not_a_read_only_option(self):
        for flag in ("--status", "--explain", "--dry-run"):
            with self.subTest(flag=flag):
                # An explicit target keeps the shared launch helper from adding
                # --in-place, which --explain correctly refuses as a new-run option.
                result = self.launch(["--run-dir", str(self.project / ".autocode/runs/no-run"), flag, FLAG], 2)
                self.assertIn(
                    "--allow-uncontained-tools is saved with the run; it cannot be combined "
                    "with --status, --explain or --dry-run", result.stderr)
                self.assertFalse((self.project / ".autocode").exists())


if __name__ == "__main__":
    unittest.main()

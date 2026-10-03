"""Public-CLI startup recovery with scripted native and configured adapters."""
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scenarios"))
from harness.adversarial import AdversarialCase, REPO
from harness.project import git


class ProviderStartupRecovery(AdversarialCase):
    def setUp(self):
        super().setUp()
        # Startup recovery is followed through the Requirements stage; a clear request would skip it now that
        # adaptive planning is the default, so the scripted recognizer calls the request vague.
        self.env["SCENARIO_FAKE_CLARITY"] = "vague"
        (self.project / "README.md").write_text("Existing source citation target.\n")
        git(self.project, "add", "README.md")
        git(self.project, "commit", "-qm", "Seed source")

    def setup_fault(self, case, backend="codex"):
        config = json.loads(self.config_path.read_text())
        config["startup_fault"] = {"root": str(self.root), "case": case}
        self.config_path.write_text(json.dumps(config))
        provider = self.root / "bin" / ("opencode" if backend == "opencode" else "codex")
        if backend == "opencode":
            delegate = self.root / "bin/opencode-base"
            shutil.copy2(REPO / "tools/fake_opencode.py", delegate)
            self.flags[self.flags.index("codex")] = "opencode"
            self.flags = ["openai/" + a if a.startswith("gpt-") else a for a in self.flags]
        else:
            delegate = self.root / "bin/codex-base"
            delegate.write_text(provider.read_text())
        provider.write_text("#!" + sys.executable + "\nimport runpy,sys\nsys.path.insert(0," +
            repr(str(REPO / "scenarios")) + ")\nfrom harness.startup_fault import before_launch\n" +
            "before_launch()\nrunpy.run_path(" + repr(str(delegate)) + ",run_name='__main__')\n")
        provider.chmod(0o755)
        if backend == "configured":
            home = self.root / "config"
            path = home / "autocode/providers/startupfixture.toml"
            path.parent.mkdir(parents=True)
            roles = ("astra", "terra", "sol", "completion", "glm", "plan_reviewer")
            models = ["gpt-6-astra", "gpt-5.6-terra", "gpt-5.6-sol"]
            path.write_text('name = "startupfixture"\noutput = "report_file"\n' +
                'command = ' + json.dumps([str(provider), "exec", "--output-schema", "{schema}", "-o", "{report}", "--model", "{model}", "-"]) + '\n' +
                'models = ' + json.dumps(models) + '\n[roles]\n' +
                '\n'.join(r + ' = { model = "gpt-6-astra", effort = "medium" }' for r in roles) + '\n')
            self.flags[self.flags.index("codex")] = "opencode"
            self.flags += ["--provider", "startupfixture"]
            self.env["XDG_CONFIG_HOME"] = str(home)

    def startup_trace(self):
        return [json.loads(line) for line in (self.root / "startup-trace.jsonl").read_text().splitlines()]

    def test_native_transient_startup_lock_recovers_and_completes(self):
        self.setup_fault("once")
        view = self.driver.drive(self.scenario.brief)
        self.assertEqual("TASK_COMPLETE", view["status"], self.root)
        self.assertEqual([True, False], [r["injected"] for r in self.startup_trace()])
        events = list(self.driver.run_dir.glob("iterations/*/archived-*/*.jsonl"))
        self.assertTrue(any("database is locked" in p.read_text() for p in events), self.root)

    def test_opencode_transient_startup_lock_reaches_recognized_workflow(self):
        self.assert_adapter_recovers("opencode")

    def test_configured_provider_transient_startup_lock_reaches_recognized_workflow(self):
        self.assert_adapter_recovers("configured")

    def assert_adapter_recovers(self, backend):
        self.setup_fault("once", backend)
        self.invoke("--pause-after-stage", task=self.scenario.brief)
        self.discover_run()
        view = self.driver.view()
        self.assertEqual("build", view["workflow"], self.root)
        self.assertEqual([True, False], [r["injected"] for r in self.startup_trace()])
        self.assertFalse(view["done"])
        self.assertEqual("requirements_gather", view["next_stage"], self.root)

    def test_persistent_lock_is_bounded_and_does_not_resume_implicitly(self):
        self.setup_fault("repeated")
        view = self.driver.drive(self.scenario.brief)
        self.assertFalse(view["done"])
        self.assertEqual(3, len(self.startup_trace()), self.root)
        self.assertFalse(self.trace("stage_enter", "terra"))
        saved = self.driver.state()  # Evidence read only; status is checked through CLI.
        self.assertIn("startup retry limit", json.dumps(saved))
        self.assertEqual(2, sum(bool(r.get("startup_recovery")) for r in saved["stages"]))
        self.invoke("--resume-paused")
        self.assertEqual(3, len(self.startup_trace()), "Saved exhausted/uncertain attempts must not replay")

    def test_retry_bound_survives_restart_between_different_stages(self):
        self.setup_fault("per_stage")
        config = json.loads(self.config_path.read_text())
        config["startup_fault"]["stages"] = ["recognize_workflow", "requirements_gather", "astra_discovery"]
        self.config_path.write_text(json.dumps(config))
        self.invoke("--pause-after-stage", task=self.scenario.brief)
        self.discover_run()
        self.assertEqual("requirements_gather", self.driver.view()["next_stage"])
        self.invoke("--resume-paused", "--pause-after-stage")
        self.assertEqual("astra_discovery", self.driver.view()["next_stage"])
        result = self.invoke("--resume-paused")
        self.assertIn("startup retry limit", result.stdout + result.stderr)
        self.assertEqual([True, False, True, False, True], [r["injected"] for r in self.startup_trace()])
        self.assertFalse(self.driver.view()["done"])
        self.assertFalse(self.trace("stage_enter", "terra"))

    def test_started_session_is_retained_without_automatic_retry(self):
        self.assert_no_retry("session")

    def test_changed_source_is_retained_without_automatic_retry(self):
        self.assert_no_retry("source")
        self.assertIn("Partial provider work", (self.project / "README.md").read_text())

    def test_partial_report_is_retained_without_automatic_retry(self):
        self.assert_no_retry("report")
        self.assertTrue(any(p.read_text() == '{"partial":' for p in self.driver.run_dir.glob("iterations/*/*.json")))

    def test_quota_does_not_trigger_startup_retries(self):
        self.assert_no_retry("quota")

    def assert_no_retry(self, case):
        self.setup_fault(case)
        view = self.driver.drive(self.scenario.brief)
        self.assertFalse(view["done"])
        self.assertEqual(1, len(self.startup_trace()), self.root)
        self.assertFalse(self.trace("stage_enter", "terra"))

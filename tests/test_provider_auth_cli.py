"""A provider auth-helper timeout is named; retrying the job completes (gocode-cross #3)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class ProviderAuthCLI(unittest.TestCase):
    def setUp(self):
        from scenarios import run  # noqa: F401, I001 - initialize the harness before importing it
        from harness import catalog

        results = Path(__file__).resolve().parents[1] / ".scenario-runs"
        results.mkdir(exist_ok=True)
        scratch = tempfile.TemporaryDirectory(prefix="provider-auth-", dir=results, ignore_cleanup_errors=True)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.scenario = catalog.load("bugfix-iso-weeks")
        for name in ("config", "codex-home"):
            (self.root / name).mkdir()

    def test_auth_timeout_is_named_and_retry_completes(self):
        from harness.driver import DriveError, Driver, default_autocode, fake_setup, leaves_for_person
        from harness.project import materialize

        project = materialize(self.scenario.seed, self.root / "project")
        flags, env = fake_setup(self.scenario, self.root, self.scenario.reference)
        env.update(
            CODEX_HOME=str(self.root / "codex-home"),
            SCENARIO_FAKE_LOGIN_TIMEOUT="investigate_bug:1",
        )
        driver = Driver(project, self.root, flags, env, autocode=default_autocode(), max_steps=40, timeout_seconds=600)
        driver.call("start", task=self.scenario.brief)
        runs = project / ".autocode" / "runs"
        candidates = sorted(runs.glob("*/state.json"), key=lambda path: path.stat().st_mtime)
        if not candidates:
            raise DriveError("the first CLI call did not create a run")
        driver.run_dir = candidates[-1].parent
        view = driver.until_stopped()
        self.assertEqual("PAUSED_JOB_FAILURE", view.get("status"), view.get("stop_reason"))
        self.assertIn("provider auth helper", view.get("stop_reason") or "")
        self.assertIn("timeout_ms", view.get("stop_reason") or "")
        need = view.get("needs") or {}
        self.assertEqual("retry_job", need.get("kind"), need)
        token = need.get("job_retry_token")
        self.assertTrue(token)
        driver.assert_needs_command_accepted(view)
        # Follow the status-view action through the public CLI (#1010).
        # Launching resumes may exit 2 when they stop again for input; that is
        # not an action=True (must-exit-0) call.
        driver.call(
            "retry-auth",
            "--resume-paused",
            "--retry-failed-stage",
            "--job-retry-token",
            token,
        )
        view = driver.until_stopped()
        while not view.get("done"):
            need = view.get("needs") or {}
            if leaves_for_person(need):
                self.fail(f"unexpected person-only stop {need.get('kind')}: {view.get('stop_reason')}")
            driver.assert_needs_command_accepted(view)
            if need.get("kind") == "continue":
                driver.call("resume")
            else:
                driver.serve(need)
            view = driver.until_stopped()
        self.assertEqual("TASK_COMPLETE", view.get("status"), view.get("stop_reason"))


if __name__ == "__main__":
    unittest.main()

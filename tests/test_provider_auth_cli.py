"""A provider auth-helper failure at a stage is named and its attempt is never replayed (gocode-cross #3).

The exact job retry that followed it is covered for the jobs that remain in tests/test_job_failure_recovery.py
and tests/test_job_route.py; this run fails at the Planner, the first stage every task now takes (#3010)."""

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

    def assert_auth_failure_is_named_and_not_replayed(self, fault, *details):
        from harness.driver import FAKE_PROVIDER, DriveError, Driver, default_autocode, fake_setup
        from harness.project import materialize

        project = materialize(self.scenario.seed, self.root / "project")
        flags, env = fake_setup(self.scenario, self.root, self.scenario.reference)
        env.update(CODEX_HOME=str(self.root / "codex-home"))
        if fault == "SCENARIO_FAKE_LOGIN_REJECTED":
            # This error belongs to this regression, not the shared scenario
            # fault catalog. Keep the ordinary fake, timeout behavior and real
            # scenario checks unchanged after the one rejected login.
            (self.root / "bin" / "codex").write_text(
                "#!/usr/bin/env python3\n"
                "import os\nimport runpy\nfrom pathlib import Path\n"
                f"fixture = runpy.run_path({str(FAKE_PROVIDER)!r})\n"
                "owner = fixture['main'].__globals__\n"
                "ordinary_timeout = owner['auth_timeout_once']\n"
                "def reject_once(stage):\n"
                "    marker = Path(os.environ['SCENARIO_FAKE_CONFIG']).with_name('fake-login-rejected-astra_discovery')\n"
                "    if stage != 'astra_discovery' or marker.exists():\n"
                "        return ordinary_timeout(stage)\n"
                "    marker.touch()\n"
                "    owner['emit']({'type': 'error', 'sessionID': 'ses_rejected_login',\n"
                "        'error': {'name': 'APIError', 'data': {'statusCode': 401, 'message': 'Invalid API key'}}})\n"
                "    return 1\n"
                "owner['auth_timeout_once'] = reject_once\n"
                "raise SystemExit(fixture['main']())\n"
            )
        else:
            env[fault] = "astra_discovery:1"
        driver = Driver(project, self.root, flags, env, autocode=default_autocode(), max_steps=40, timeout_seconds=600)
        driver.call("start", task=self.scenario.brief)
        runs = project / ".autocode" / "runs"
        candidates = sorted(runs.glob("*/state.json"), key=lambda path: path.stat().st_mtime)
        if not candidates:
            last = driver.steps[-1]
            raise DriveError(
                "the first CLI call did not create a run: "
                + (last["stderr_tail"] or last["stdout_tail"]).strip()[-500:]
            )
        driver.run_dir = candidates[-1].parent
        view = driver.until_stopped()
        self.assertEqual("astra_discovery", view.get("next_stage"), view.get("stop_reason"))
        self.assertFalse(view.get("done"))
        for detail in details:
            self.assertIn(detail, view.get("stop_reason") or "")
        self.assertIn("no automatic replay", view.get("stop_reason") or "")
        attempts = [row for row in view["usage"]["accounting"]["attempts"] if row["stage"] == "astra_discovery"]
        self.assertEqual(1, len(attempts), attempts)
        driver.assert_needs_command_accepted(view)

    def test_auth_timeout_is_named_and_not_replayed(self):
        self.assert_auth_failure_is_named_and_not_replayed(
            "SCENARIO_FAKE_LOGIN_TIMEOUT", "provider auth helper", "timeout_ms"
        )

    def test_structured_auth_rejection_is_named_and_not_replayed(self):
        self.assert_auth_failure_is_named_and_not_replayed(
            "SCENARIO_FAKE_LOGIN_REJECTED", "authentication failed (HTTP 401)"
        )


if __name__ == "__main__":
    unittest.main()

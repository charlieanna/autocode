"""A provider auth-helper failure at a stage is named and its attempt is never replayed (gocode-cross #3).

The exact job retry that followed it is covered for the jobs that remain in tests/test_job_failure_recovery.py
and tests/test_job_route.py; this run fails at the Planner, the first stage every task now takes (#3010).
The run itself comes from tests/provider_auth_fixture.py, which the VS Code Extension Host suite also uses."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import operational_fixtures, provider_auth_fixture


class ProviderAuthCLI(unittest.TestCase):
    def setUp(self):
        results = Path(__file__).resolve().parents[1] / ".scenario-runs"
        results.mkdir(exist_ok=True)
        scratch = tempfile.TemporaryDirectory(prefix="provider-auth-", dir=results, ignore_cleanup_errors=True)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()

    def assert_auth_failure_is_named_and_not_replayed(self, fault, summary, *details):
        driver, view = provider_auth_fixture.stopped_planner_run(self.root, fault)
        self.assertEqual("draft_plan", view.get("next_stage"), view.get("stop_reason"))
        operational_fixtures.assert_view_blocked(self, view, "PAUSED_PROVIDER_UNCERTAIN")
        self.assertIn(summary, view["stop_reason"])
        self.assertIn(summary, view["recovery"]["what_happened"])
        self.assertNotIn("timeout_ms", view["stop_reason"])
        cause = operational_fixtures.kept_cause(driver.run_dir)
        for detail in details:
            self.assertIn(detail, cause)
        self.assertIn("no automatic replay", cause)
        attempts = [row for row in view["usage"]["accounting"]["attempts"] if row["stage"] == "draft_plan"]
        self.assertEqual(1, len(attempts), attempts)
        driver.assert_needs_command_accepted(view)

    def test_auth_timeout_is_named_and_not_replayed(self):
        self.assert_auth_failure_is_named_and_not_replayed(
            provider_auth_fixture.TIMEOUT,
            "signing in to its model provider timed out",
            "provider auth helper",
            "timeout_ms",
        )

    def test_structured_auth_rejection_is_named_and_not_replayed(self):
        self.assert_auth_failure_is_named_and_not_replayed(
            provider_auth_fixture.REJECTED,
            "rejected the saved credentials (HTTP 401)",
            "authentication failed (HTTP 401)",
        )


if __name__ == "__main__":
    unittest.main()

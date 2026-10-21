"""An explicitly rerouted scoped retry preserves the incident and only launches once."""

import json
import tempfile
import unittest
from pathlib import Path

from harness import catalog
from harness.driver import DriveError, default_autocode, fake_setup
from harness.project import materialize

from .test_rework_cli import SavedRoutesDriver


class ResolverRetryRouteCLI(unittest.TestCase):
    def test_explicit_resolver_model_change_can_retry_the_same_held_incident(self):
        scratch = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name).resolve()
        scenario = catalog.load("rework-same-failure")
        flags, env = fake_setup(scenario, root, scenario.reference)
        (root / "config").mkdir()
        env["XDG_CONFIG_HOME"] = str(root / "config")
        # This scenario escalates the Builder before holding. Reuse the driver
        # that does not reapply starting model flags on every saved-run call.
        driver = SavedRoutesDriver(
            materialize(scenario.seed, root / "project"),
            root,
            flags,
            env,
            autocode=default_autocode(),
            max_steps=40,
            timeout_seconds=240,
        )
        before = driver.drive(scenario.brief)
        self.assertEqual("RESOLVER_PENDING", before["status"], before.get("stop_reason"))
        self.assertIn("No causal progress", before["stop_reason"])
        # Only settings explicitly supplied here may change; starting defaults must not be replayed.
        driver.flags = []
        before_trace = (root / "rework-trace.jsonl").read_text().splitlines()
        for flags in (
            ("--resolver-model", "gpt-6.1-sol"),
            (
                "--resume-paused",
                "--retry-failed-stage",
                "--resolver-model",
                "gpt-6.1-sol",
                "--max-stage-seconds",
                "901",
            ),
        ):
            with self.assertRaisesRegex(DriveError, "nothing was saved"):
                driver.call("reject-unbound-route-change", *flags)
            held = driver.view()
            self.assertEqual(before["routes"], held["routes"])
            self.assertEqual(before["cost"], held["cost"])
            self.assertEqual(before_trace, (root / "rework-trace.jsonl").read_text().splitlines())
        changed = driver.call(
            "reroute-one-retry",
            "--resume-paused",
            "--retry-failed-stage",
            "--resolver-model",
            "gpt-6.1-sol",
            "--resolver-reasoning-effort",
            "medium",
            "--max-seconds",
            "10800",
        )
        self.assertNotIn("Input rejected", changed.stderr)
        self.assertNotIn("stale", changed.stderr.lower())
        self.assertIn("Failure retry authorized", changed.stdout)
        after = driver.until_stopped()
        trace = [json.loads(line) for line in (root / "rework-trace.jsonl").read_text().splitlines()]
        self.assertEqual(["astra_resolve"], [row["stage"] for row in trace[len(before_trace) :]])
        self.assertIn("No causal progress", after["stop_reason"])
        self.assertEqual("gpt-6.1-sol", after["routes"]["resolver"]["model"])
        self.assertEqual("medium", after["routes"]["resolver"]["reasoning_effort"])
        # This fixture's returned REWORK separately triggers the existing Builder
        # escalation policy. Other roles are unchanged; the parser-level provider
        # regression checks all roles at the settings boundary before that work.
        self.assertEqual(
            {k: v for k, v in before["routes"].items() if k not in ("resolver", "terra")},
            {k: v for k, v in after["routes"].items() if k not in ("resolver", "terra")},
        )
        prior_attempts = before["usage"]["accounting"]["attempts"]
        new_attempts = after["usage"]["accounting"]["attempts"]
        self.assertEqual(prior_attempts, new_attempts[: len(prior_attempts)])
        self.assertEqual(before["cost"]["cap_usd"], after["cost"]["cap_usd"])
        self.assertGreaterEqual(after["cost"]["spent_usd"], before["cost"]["spent_usd"])
        self.assertIn("10,800 s limit", after["stop_reason"])
        # A new process has the saved route, not a second spending grant.
        driver.call("restart-without-new-grant")
        self.assertEqual(trace, [json.loads(line) for line in (root / "rework-trace.jsonl").read_text().splitlines()])


if __name__ == "__main__":
    unittest.main()

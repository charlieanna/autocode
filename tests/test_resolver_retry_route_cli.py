"""AutoCode's own scoped retry of a held incident runs on the saved routes, and a person is no longer
stopped at the hold to reroute it (#2485)."""

import tempfile
import unittest
from pathlib import Path

from harness import catalog
from harness.driver import default_autocode, fake_setup
from harness.project import materialize

from .test_rework_cli import SavedRoutesDriver


class ResolverRetryRouteCLI(unittest.TestCase):
    def test_autocodes_own_retry_of_the_held_incident_takes_no_route_change(self):
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
        # The repeat held the Resolver, then the Builder; AutoCode took the one scoped retry of each itself
        # (the grant a person used to give with --resume-paused --retry-failed-stage), on the saved routes
        # and with no route change (#2485). The hold never stopped the run for a person.
        self.assertNotEqual("RESOLVER_PENDING", before["status"], before.get("stop_reason"))
        self.assertNotIn("No causal progress", before["stop_reason"])
        state = driver.state()
        grants = state["failure_retry_authorizations"]
        self.assertEqual(
            [("autocode", "astra_resolve"), ("autocode", "terra")], [(g["actor"], g["stage"]) for g in grants]
        )
        self.assertFalse([g for g in grants if g.get("resolver_route_change")])
        receipts = [row["recovery_novelty"] for row in state["stages"] if row.get("recovery_novelty")]
        self.assertEqual(
            [(None, "first_incident"), ("automatic", "explicit_retry"), ("automatic", "explicit_retry")],
            [(row.get("grant_kind"), row["reason"]) for row in receipts],
        )
        self.assertEqual("derived", before["routes"]["resolver"]["origin"])
        for step in driver.steps:
            self.assertNotIn("--retry-failed-stage", step["stdout_tail"] + step["stderr_tail"], step)
        # At the stop the run reaches instead, a rerouted scoped retry is a recovery decision the run no
        # longer offers a person: it is refused and launches nothing. (The model flag itself is an
        # ordinary settings change at this pause, saved as at any other.) Only settings explicitly
        # supplied here may change; starting defaults must not be replayed.
        driver.flags = []
        before_trace = (root / "rework-trace.jsonl").read_text().splitlines()
        rejected = driver.call(
            "reroute-retry",
            "--resume-paused",
            "--retry-failed-stage",
            "--resolver-model",
            "gpt-6.1-sol",
            "--resolver-reasoning-effort",
            "medium",
        )
        self.assertIn("Input rejected", rejected.stderr)
        after = driver.view()
        self.assertEqual(before["status"], after["status"])
        self.assertEqual(
            {k: v for k, v in before["routes"].items() if k != "resolver"},
            {k: v for k, v in after["routes"].items() if k != "resolver"},
        )
        self.assertEqual(before_trace, (root / "rework-trace.jsonl").read_text().splitlines())


if __name__ == "__main__":
    unittest.main()

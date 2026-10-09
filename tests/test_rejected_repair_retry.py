"""A rejected repair Builder's retry through the public CLI (docs/bugs/2026-10-06-rejected-repair-retry.md).

Each test drives feature-stock-refusals end to end with the scripted model, so this module is in
tests/suite_slow.json; the pure rules are in test_retained_work and test_recovery_novelty.
"""
import tempfile
import unittest
from pathlib import Path


class RejectedRepairRetryCLI(unittest.TestCase):
    """A repair Builder's rejected attempt keeps its in-scope work, and its retry continues from it.

    Live feature-stock-refusals run (claude-tiers-hybrid, 2026-10-06): the Resolver's repair Builder
    strengthened tests/test_stock.py but also left stock_current.py, a backup of stock.py, in the
    project root. The runner rejected the attempt, removed that file and kept the test edit. The
    Investigator recommended one more attempt, and its admission paused PAUSED_STALE_HANDOFF: the
    rejected attempt's own retained edit no longer matched the source the incident packet bound.
    SCENARIO_FAKE_SCOPE_SLIP scripts that Builder (docs/bugs/2026-10-06-rejected-repair-retry.md).
    """
    def setUp(self):
        from scenarios import run  # noqa: F401 - establish the scenario harness import root
        from harness import catalog
        results = Path(__file__).resolve().parents[1] / ".scenario-runs"
        results.mkdir(exist_ok=True)
        scratch = tempfile.TemporaryDirectory(prefix="rejected-repair-", dir=results)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.scenario = catalog.load("feature-stock-refusals")
        for name in ("config", "codex-home"):
            (self.root / name).mkdir()

    def driver(self, investigator):
        """``investigator`` is ``retry`` (it recommends one more attempt) or ``pause`` (it leaves it to a person)."""
        from harness.driver import Driver, default_autocode, fake_setup
        from harness.project import materialize
        self.project = materialize(self.scenario.seed, self.root / "project")
        flags, env = fake_setup(self.scenario, self.root, self.scenario.reference)
        env.update(XDG_CONFIG_HOME=str(self.root / "config"), CODEX_HOME=str(self.root / "codex-home"),
                   SCENARIO_FAKE_SCOPE_SLIP=investigator)
        return Driver(self.project, self.root, flags, env, autocode=default_autocode(), max_steps=30,
                      timeout_seconds=300)

    def stages(self, driver):
        return [row["stage"] for row in driver.state()["stages"] if not row.get("runner_owned")]

    def assert_repaired(self, driver, view):
        self.assertTrue(view["done"], (view.get("status"), view.get("stop_reason"), self.stages(driver)))
        self.assertTrue(all(check.ok for check in self.scenario.oracle()(self.project, self.scenario)))
        self.assertFalse((self.project / "stock_current.py").exists())
        builders = [row for row in driver.state()["stages"] if row["stage"] == "terra"]
        self.assertIn("removed the files they created, so a retry starts without them: stock_current.py",
                      builders[-2]["rejection_reason"])
        return builders[-1]

    def test_the_investigators_retry_continues_from_the_rejected_attempts_retained_work(self):
        driver = self.driver("retry")
        view = driver.drive(self.scenario.brief)
        retry = self.assert_repaired(driver, view)
        self.assertEqual(["terra", "sol", "astra_review", "astra_resolve", "investigate_stuck", "terra",
                          "investigate_stuck", "terra", "sol", "astra_review"], self.stages(driver)[-10:])
        # The Investigator's verified retry is the one attempt it promises, not a second novelty-free one.
        self.assertEqual(("explicit_retry", "investigation"),
                         (retry["recovery_novelty"]["reason"], retry["recovery_novelty"]["grant_kind"]))

    def test_an_operator_retry_continues_from_it_but_a_persons_edit_is_still_stale(self):
        driver = self.driver("pause")
        view = driver.drive(self.scenario.brief)
        self.assertEqual("PAUSED_INVALID_OUTPUT", view.get("status"), view.get("stop_reason"))
        readme = self.project / "README.md"
        original = readme.read_text()
        readme.write_text(original + "\nEdited by a person while the run was paused.\n")
        driver.call("resume-after-an-outside-edit", "--resume-paused")
        view = driver.view()
        self.assertEqual("PAUSED_STALE_HANDOFF", view.get("status"), view.get("stop_reason"))
        self.assertIn("changed before admission", view.get("stop_reason", ""))
        readme.write_text(original)
        builders = self.stages(driver).count("terra")
        # A plain resume repeats a rejected report's incident: novelty holds it, as for any rejected report.
        driver.call("plain-resume", "--resume-paused")
        view = driver.view()
        self.assertIn("No causal progress", view.get("stop_reason", ""), view.get("status"))
        self.assertEqual(builders, self.stages(driver).count("terra"))
        driver.call("authorized-retry", "--resume-paused", "--retry-failed-stage")
        retry = self.assert_repaired(driver, driver.until_stopped())
        self.assertEqual("invocation", retry["recovery_novelty"]["grant_kind"])


if __name__ == "__main__":
    unittest.main()

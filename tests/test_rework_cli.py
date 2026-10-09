"""Public CLI coverage for bounded Completion repairs, using isolated fake providers."""
import dataclasses
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from harness import catalog, verdict
from harness.driver import DriveError, Driver, default_autocode, fake_setup
from harness.project import materialize

from scenarios import run as scenario_run


class SavedRoutesDriver(Driver):
    def call(self, *args, **kwargs):
        if self.run_dir:
            # Initial route declarations are saved configuration, not repeated
            # overrides of a legitimately escalated Builder on every resume.
            initial = self.flags
            self.flags = [value for index, value in enumerate(initial) if not value.endswith("-model")
                          and not (index and initial[index - 1].endswith("-model"))]
        return super().call(*args, **kwargs)


class CompletionReworkCLI(unittest.TestCase):
    def setUp(self):
        results = Path(__file__).resolve().parents[1] / ".scenario-runs"
        results.mkdir(exist_ok=True)
        if artifacts := os.environ.get('BUILD_AUDIT_ARTIFACTS'):
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            self.root = Path(tempfile.mkdtemp(prefix="rework-cli-", dir=artifacts)).resolve()
        else:
            scratch = tempfile.TemporaryDirectory(prefix="rework-cli-", dir=results)
            self.addCleanup(scratch.cleanup)
            self.root = Path(scratch.name).resolve()
        self.scenario = catalog.load("completion-rework-direct")
        self.project = materialize(self.scenario.seed, self.root / "project")
        self.config_home = self.root / "config"
        self.config_home.mkdir()
        self.codex_home = self.root / "codex-home"
        self.codex_home.mkdir()

    def driver(self, fault="direct", *extra):
        scenario = dataclasses.replace(self.scenario, fake_fault="completion_rework_" + fault)
        flags, env = fake_setup(scenario, self.root, scenario.reference)
        if fault == "recurring":
            # The resumed Codex route must remain bare and distinct from both
            # independent checkers; the existing single escalation is unchanged.
            flags += ["--builder-strong-model", "gpt-5.4"]
        env.update(XDG_CONFIG_HOME=str(self.config_home), CODEX_HOME=str(self.codex_home), AUTOCODE_PROVIDER="opencode")
        return SavedRoutesDriver(self.project, self.root, [*flags, "--max-iterations", "6", *extra], env,
                      autocode=default_autocode(), max_steps=20, timeout_seconds=180)

    def trace(self):
        return [json.loads(line) for line in (self.root / "rework-trace.jsonl").read_text().splitlines()]

    def assert_delivery(self, driver, view):
        self.assertTrue(view["done"], view)
        self.assertEqual("PASS", view["evidence"]["check_replay"]["verdict"])
        checks = self.scenario.oracle()(self.project, self.scenario)
        self.assertTrue(all(check.ok for check in checks), [dataclasses.asdict(check) for check in checks if not check.ok])
        validators = [row for row in self.trace() if row["stage"] == "sol"]
        owners = [row for row in self.trace() if row["stage"] == "astra_review"]
        self.assertEqual([1] * (len(validators) - 1) + [0], [row["exit_code"] for row in validators])
        self.assertEqual(len(validators), len({row["task_id"] for row in validators}))
        self.assertEqual(len(validators), len({row["source_revision"] for row in validators}))
        for validator, owner in zip(validators, owners, strict=False):
            self.assertEqual(validator["task_id"], owner["validation_task_id"])
            self.assertEqual(validator["source_revision"], owner["source_revision"])
            self.assertEqual(validator["status"], owner["validation_verdict"])
        self.assertEqual(validators[-1]["source_revision"], view["evidence"]["check_replay"]["source_revision"])
        receipts = []
        for path in driver.run_dir.glob("iterations/**/*.jsonl"):
            for line in path.read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                item = event.get("item") or {}
                if event.get("type") == "item.completed" and item.get("type") == "command_execution":
                    receipts.append(item)
        self.assertTrue(any(row.get("exit_code") == 1 and row.get("command") == self.scenario.fake_check
                            and "FAILED" in row.get("aggregated_output", "") for row in receipts), receipts)
        self.assertTrue(any(row.get("exit_code") == 0 and row.get("command") == self.scenario.fake_check
                            and "OK" in row.get("aggregated_output", "") for row in receipts), receipts)

    def test_oracle_rejects_seed_and_each_broken_delivery(self):
        rows = scenario_run.self_test(self.scenario)
        self.assertEqual(["seed", "reference", "broken/accepts-empty", "broken/vacuous-tests"],
                         [name for name, _, _ in rows])
        self.assertTrue(all(ok for _, ok, _ in rows), rows)

    def test_first_bounded_rework_skips_resolver_but_not_independent_checks(self):
        driver = self.driver()
        view = driver.drive(self.scenario.brief)
        self.assert_delivery(driver, view)
        record = scenario_run.run_record(driver, driver.state())
        result = verdict.evaluate(self.scenario, self.project, record)
        self.assertTrue(result.passed, result.summary)
        self.assertEqual(1, len(view["direct_rework_assignments"]))
        self.assertEqual(["terra", "sol", "astra_review", "terra", "sol", "astra_review"],
                         [row["stage"] for row in self.trace()])

    def test_validator_activity_citation_survives_completion_rework_and_automatic_repair(self):
        driver = self.driver("activity_evidence")
        view = driver.drive(self.scenario.brief)
        self.assert_delivery(driver, view)
        self.assertEqual(1, len(view["direct_rework_assignments"]))
        trace = self.trace()
        self.assertEqual(["terra", "sol", "astra_review", "terra", "sol", "astra_review"],
                         [row["stage"] for row in trace])
        self.assertEqual(["REWORK", "COMPLETE"],
                         [row["status"] for row in trace if row["stage"] == "astra_review"])
        activity = driver.run_dir / "activity.jsonl"
        validators = sorted(driver.run_dir.glob("iterations/*/validator-01.json"))
        self.assertEqual(2, len(validators))
        for path in validators:
            report = json.loads(path.read_text())
            self.assertTrue(all(str(activity) in row["evidence_refs"]
                                for row in report["criterion_results"]), report)
        # Inspect the explicit evidence artifacts, never the private run state.
        snapshots = list((driver.run_dir / "evidence").glob("run-activity-*.jsonl"))
        self.assertEqual(2, len(snapshots))
        live = activity.read_bytes()
        for snapshot in snapshots:
            frozen = snapshot.read_bytes()
            self.assertTrue(live.startswith(frozen))
            self.assertGreater(len(live), len(frozen))
            self.assertEqual(f"run-activity-{hashlib.sha256(frozen).hexdigest()}.jsonl", snapshot.name)

    def test_schema_valid_incomplete_and_ambiguous_tasks_use_normal_resolver(self):
        for fault in ("incomplete", "ambiguous"):
            with self.subTest(fault=fault):
                # Each subcase is a new approved public run, not a rewritten state.
                root = self.root / fault
                root.mkdir()
                project = materialize(self.scenario.seed, root / "project")
                old_root, old_project = self.root, self.project
                self.root, self.project = root, project
                try:
                    driver = self.driver(fault)
                    view = driver.drive(self.scenario.brief)
                    self.assert_delivery(driver, view)
                    self.assertEqual([], view["direct_rework_assignments"])
                    self.assertEqual(["terra", "sol", "astra_review", "astra_resolve", "terra", "sol", "astra_review"],
                                     [row["stage"] for row in self.trace()])
                    self.assertEqual(0, scenario_run.metrics(driver.state())["report_repairs"])
                    first = next(row for row in self.trace() if row["stage"] == "astra_review")
                    self.assertEqual("REWORK", first["status"])
                    self.assertEqual("FAIL", first["validation_verdict"])
                    if fault == "incomplete":
                        self.assertEqual([], first["next_task"]["validation_plan"])
                    else:
                        self.assertEqual("", first["next_objective"])
                finally:
                    self.root, self.project = old_root, old_project

    def test_recurring_rework_uses_resolver_and_fresh_current_source_checks(self):
        driver = self.driver("recurring")
        view = driver.drive(self.scenario.brief)
        self.assertFalse(view["done"])
        self.assertIn("No causal progress", view["stop_reason"])
        before = self.trace()
        driver.call("unchanged-resume", "--resume-paused")
        self.assertEqual(before, self.trace())
        driver.call("authorized-diagnosis", "--resume-paused", "--retry-failed-stage")
        view = driver.view()
        self.assertIn("No causal progress", view["stop_reason"])
        self.assertEqual([row["stage"] for row in before] + ["astra_resolve"],
                         [row["stage"] for row in self.trace()])
        granted = driver.call("authorized-repair", "--resume-paused", "--retry-failed-stage")
        view = driver.until_stopped()
        self.assertTrue(view["done"], (view.get("status"), view.get("stop_reason"), granted.stdout[-1800:], granted.stderr[-1800:]))
        self.assert_delivery(driver, view)
        self.assertEqual(1, len(view["direct_rework_assignments"]))
        self.assertEqual(["terra", "sol", "astra_review", "terra", "sol", "astra_review",
                          "astra_resolve", "terra", "sol", "astra_review"],
                         [row["stage"] for row in self.trace()])

    def test_resolver_validation_repair_runs_fresh_checks_without_another_builder(self):
        driver = self.driver("validate", "--pause-after-stage")
        view = driver.drive(self.scenario.brief)
        while (not (self.root / "rework-trace.jsonl").exists()
               or not any(row["stage"] == "astra_resolve" for row in self.trace())):
            self.assertEqual("--pause-after-stage checkpoint reached", view["needs"].get("reason"), view)
            driver.call("resume-checkpoint", "--resume-paused")
            view = driver.until_stopped()
        self.assertFalse(view["done"], view)
        self.assertEqual("sol", view["next_stage"], view)
        self.assertEqual("validate", driver.state()["current_task"]["kind"])
        self.assertEqual(0, scenario_run.metrics(driver.state())["report_repairs"])
        # The previous passing report belongs to another task and cannot finish this one.
        previous = [row for row in self.trace() if row["stage"] == "sol"]
        self.assertEqual(1, len(previous))
        self.assertNotEqual(previous[0]["task_id"], driver.state()["current_task"]["id"])
        while not view["done"]:
            self.assertEqual("--pause-after-stage checkpoint reached", view["needs"].get("reason"), view)
            driver.call("resume-checkpoint", "--resume-paused")
            view = driver.until_stopped()
        self.assertEqual(["terra", "sol", "astra_review", "astra_resolve", "sol", "astra_review"],
                         [row["stage"] for row in self.trace()])
        self.assertEqual(1, len({row["source_revision"] for row in self.trace() if row["stage"] != "terra"}))
        self.assertEqual(1, len({row["source_sha256"] for row in self.trace()}))
        self.assertEqual([0, 0], [row["exit_code"] for row in self.trace() if row["stage"] == "sol"])
        self.assertEqual("PASS", view["evidence"]["check_replay"]["verdict"])
        checks = self.scenario.oracle()(self.project, self.scenario)
        self.assertTrue(all(check.ok for check in checks), checks)

    def test_validation_only_rounds_stop_before_another_validator_for_the_same_open_finding(self):
        # Issue #300: the Completion Owner keeps sending a finding the Validator never closes back to it.
        driver = self.driver("bookkeeping")
        view = driver.drive(self.scenario.brief)
        stages = [row["stage"] for row in self.trace()]
        self.assertEqual(["terra", "sol", "astra_review", "terra", "sol", "astra_review",
                          "sol", "astra_review", "sol", "astra_review"], stages)
        rounds = [row for row in self.trace()[6:] if row["stage"] == "sol"]
        self.assertEqual(1, len({(row["source_revision"], row["source_sha256"]) for row in rounds}))
        self.assertEqual([0, 0], [row["exit_code"] for row in rounds])
        state = driver.state()
        blocking = [row["id"] for row in state["findings_ledger"] if row["status"] == "open" and row["blocking"]]
        self.assertEqual(1, len(blocking), state["findings_ledger"])
        self.assertEqual("validate", state["current_task"]["kind"])
        self.assertNotIn("investigate_stuck", [row.get("stage") for row in state["stages"]])
        need = view["needs"]
        self.assertEqual(("answer", "operational_exhaustion"), (need["kind"], need.get("resolver_scope")), view)
        [question] = need["questions"]
        self.assertIn(blocking[0], question["question"])
        self.assertIn(f"{blocking[0]} (Validator): the Validator's latest accepted report did not recheck it",
                      question["why"])
        self.assertIn(blocking[0], view["stop_reason"])
        # Resuming launches no further round and closes nothing.
        driver.call("resume", "--resume-paused")
        self.assertEqual(stages, [row["stage"] for row in self.trace()])
        self.assertFalse(driver.view()["done"])
        self.assertEqual(["open"], [row["status"] for row in driver.state()["findings_ledger"]
                                    if row["id"] == blocking[0]])
        # The way on the question names: goal feedback is accepted while it is asked, and the run continues.
        self.assertIn("Answering keeps the run paused and launches no Validator; to continue instead, revise the "
                      "goal with --feedback", question["question"])
        driver.call("feedback", "--feedback", "Recheck the open finding against the approved goal", action=True)
        self.assertEqual("RUNNING", driver.view()["status"])
        self.assertEqual(["open"], [row["status"] for row in driver.state()["findings_ledger"]
                                    if row["id"] == blocking[0]])

    def test_closing_the_stalled_finding_as_a_user_decision_lets_the_run_complete(self):
        # Issue #300: the finding no reviewer report can close is closed by the user, by name and with a reason.
        driver = self.driver("bookkeeping")
        view = driver.drive(self.scenario.brief)
        self.assertEqual("operational_exhaustion", view["needs"].get("resolver_scope"), view)
        [finding] = [row["id"] for row in driver.state()["findings_ledger"] if row["status"] == "open" and row["blocking"]]
        driver.call("close", "--close-finding", finding, "--close-reason",
                    "Duplicate of the regression-gate finding the user already settled", action=True)
        self.assertEqual("RUNNING", driver.view()["status"])
        view = driver.until_stopped(None)
        self.assertTrue(view["done"], view)
        [row] = [row for row in driver.state()["findings_ledger"] if row["id"] == finding]
        self.assertEqual(("resolved", "user"), (row["status"], row["resolved_by"]))
        [event] = [event for event in driver.state()["user_events"] if event.get("kind") == "findings_closed"]
        self.assertEqual([finding], event["ids"])

    def test_validator_closing_its_own_finding_on_the_one_revalidation_completes(self):
        driver = self.driver("bookkeeping_late")
        view = driver.drive(self.scenario.brief)
        self.assertTrue(view["done"], view)
        self.assertEqual(["terra", "sol", "astra_review", "terra", "sol", "astra_review",
                          "sol", "astra_review", "sol", "astra_review"],
                         [row["stage"] for row in self.trace()])
        ledger = driver.state()["findings_ledger"]
        self.assertTrue(ledger and all(row["status"] == "resolved" for row in ledger), ledger)
        self.assertEqual("PASS", view["evidence"]["check_replay"]["verdict"])
        checks = self.scenario.oracle()(self.project, self.scenario)
        self.assertTrue(all(check.ok for check in checks), checks)

    def test_exhausted_pinned_builder_keeps_existing_pause(self):
        driver = self.driver("exhausted", "--pin-model-role", "terra")
        view = driver.drive(self.scenario.brief)
        self.assertFalse(view["done"], view)
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", view["status"])
        self.assertEqual(1, len(view["direct_rework_assignments"]))
        self.assertEqual(["terra", "sol", "astra_review", "terra", "sol", "astra_review"],
                         [row["stage"] for row in self.trace()])
        self.assertEqual([1, 1], [row["exit_code"] for row in self.trace() if row["stage"] == "sol"])

    def test_restart_after_assignment_commit_does_not_repeat_assignment_or_provider(self):
        driver = self.driver()
        hooks = self.root / "hooks"
        hooks.mkdir()
        shutil.copy2(self.scenario.dir / "restart_hook.py", hooks / "sitecustomize.py")
        marker = self.root / "assignment-committed.json"
        driver.env.update(PYTHONPATH=str(hooks), SCENARIO_REWORK_CRASH_ON_ASSIGNMENT=str(marker))
        with self.assertRaisesRegex(DriveError, "exited 97"):
            driver.drive(self.scenario.brief)
        self.assertTrue(marker.is_file(), "Crash never reached the committed direct assignment")
        committed = driver.view()
        self.assertEqual(1, len(committed["direct_rework_assignments"]))
        saved_assignment = committed["direct_rework_assignments"][0]
        charge = saved_assignment["retry_charge"]
        self.assertEqual("retry", charge["action"])
        self.assertEqual(["terra", "sol", "astra_review"], [row["stage"] for row in self.trace()])
        driver.env.pop("PYTHONPATH")
        driver.env.pop("SCENARIO_REWORK_CRASH_ON_ASSIGNMENT")
        final = driver.until_stopped()
        self.assert_delivery(driver, final)
        self.assertEqual([saved_assignment], final["direct_rework_assignments"])
        self.assertEqual(2, final["iteration"])
        self.assertEqual(["terra", "sol", "astra_review", "terra", "sol", "astra_review"],
                         [row["stage"] for row in self.trace()])

    def test_restart_before_assignment_commit_reconciles_without_another_review_call(self):
        driver = self.driver()
        hooks = self.root / "hooks"
        hooks.mkdir()
        shutil.copy2(self.scenario.dir / "restart_hook.py", hooks / "sitecustomize.py")
        marker = self.root / "assignment-not-committed.json"
        driver.env.update(PYTHONPATH=str(hooks), SCENARIO_REWORK_CRASH_ON_ASSIGNMENT=str(marker),
                          SCENARIO_REWORK_CRASH_WHEN="before")
        with self.assertRaisesRegex(DriveError, "exited 97"):
            driver.drive(self.scenario.brief)
        self.assertEqual("before_assignment_commit", json.loads(marker.read_text())["boundary"])
        self.assertEqual([], driver.view()["direct_rework_assignments"])
        self.assertEqual(["terra", "sol", "astra_review"], [row["stage"] for row in self.trace()])
        for key in ("PYTHONPATH", "SCENARIO_REWORK_CRASH_ON_ASSIGNMENT", "SCENARIO_REWORK_CRASH_WHEN"):
            driver.env.pop(key)
        final = driver.until_stopped()
        self.assert_delivery(driver, final)
        self.assertEqual(1, len(final["direct_rework_assignments"]))
        self.assertEqual("retry", final["direct_rework_assignments"][0]["retry_charge"]["action"])
        self.assertEqual(2, final["iteration"])
        self.assertEqual(["terra", "sol", "astra_review", "terra", "sol", "astra_review"],
                         [row["stage"] for row in self.trace()])


if __name__ == "__main__":
    unittest.main()

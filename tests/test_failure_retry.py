"""Explicit retries preserve evidence and never replenish failure budgets."""

import copy
import unittest
from unittest.mock import patch

import autocode as runner
import autocode_support as support

from . import test_autocode, test_subprocess


class FailureRetryTests(unittest.TestCase):
    setUp = test_autocode.RetrofitTest.setUp
    tearDown = test_autocode.RetrofitTest.tearDown

    def seed(self):
        self.state.update(status="PAUSED_REPEATED_FAILURE", next_stage="terra")
        revision = support.snapshot(self.root)["revision"]
        for stage, attempts in (("sol", 1), ("terra", 3)):
            for index in range(attempts):
                record = {
                    "stage": stage,
                    "iteration": 1,
                    "role": stage,
                    "output": str(self.run / f"{stage}-{index}.json"),
                    "source_revision": revision,
                    "rejected": True,
                }
                runner.failures.record(self.state, record, ValueError("invalid report"), support.now())
                self.state["stages"].append(record)
        support.atomic_json(self.run / "state.json", self.state)
        return record

    def test_authorization_preserves_all_provenance_and_is_consumed_once(self):
        record = self.seed()
        history = copy.deepcopy(self.state["failure_history"])
        stages = copy.deepcopy(self.state["stages"])
        authorization = runner.authorize_failure_retry(self.state, self.run, self.root)
        self.assertEqual(history, self.state["failure_history"])
        self.assertEqual(stages, self.state["stages"])
        reloaded = support.read(self.run / "state.json")
        self.assertEqual(history, reloaded["failure_history"])
        self.assertEqual(stages, reloaded["stages"])
        with self.assertRaises(support.Paused):
            runner.repeated_failure_resume_guard(reloaded, self.root)
        runner.repeated_failure_resume_guard(self.state, self.root, authorization=authorization)
        with self.assertRaises(support.Paused):
            runner.repeated_failure_resume_guard(self.state, self.root, authorization=authorization)
        retry = dict(record, output=str(self.run / "terra-3.json"))
        retry.pop("failure_attempt", None)
        retry.pop("failure_key", None)
        entry = runner.failures.record(self.state, retry, ValueError("invalid report"), support.now())
        self.state["stages"].append(retry)
        self.assertEqual(4, entry["count"])
        self.assertEqual(4, len(entry["attempts"]))
        self.assertTrue(set(history[record["failure_key"]]["attempts"]) <= set(entry["attempts"]))
        # Even a copy of the old unconsumed token cannot authorize count four.
        old = copy.deepcopy(reloaded["failure_retry_authorizations"][-1])
        with self.assertRaises(support.Paused):
            runner.repeated_failure_resume_guard(self.state, self.root, authorization=old)

    def test_unsafe_or_mismatched_authorization_is_read_only(self):
        self.seed()
        initial = copy.deepcopy(self.state)
        before = (self.run / "state.json").read_bytes()
        for override in (
            {"active_stage": {"pid": 123}},
            {"active_stage": {"exit_code": 1}},
            {"uncertain_artifacts": "unresolved"},
            {"pending_report_repair": {"attempts": 2}},
            {"next_stage": "sol"},
            {"status": "RUNNING"},
            {"failure_history": {}},
        ):
            with self.subTest(override=override):
                state = {**copy.deepcopy(initial), **override}
                snapshot = copy.deepcopy(state)
                with self.assertRaises(ValueError):
                    runner.authorize_failure_retry(state, self.run, self.root)
                self.assertEqual(snapshot, state)
                self.assertEqual(before, (self.run / "state.json").read_bytes())
        with patch.object(runner.support, "snapshot", return_value={"revision": "changed"}):
            with self.assertRaises(ValueError):
                runner.authorize_failure_retry(self.state, self.run, self.root)
        self.assertEqual(initial, self.state)
        self.assertEqual(before, (self.run / "state.json").read_bytes())

    def test_resume_guard_refuses_every_mismatched_or_consumed_authorization(self):
        self.seed()
        authorization = runner.authorize_failure_retry(self.state, self.run, self.root)
        before = copy.deepcopy(self.state)
        mismatches = (
            {"failure_key": "different-failure"},
            {"identity": {**authorization["identity"], "error_class": "different-error"}},
            {"count": authorization["count"] + 1},
            {"source_revision": "different-source"},
            {"consumed": True},
        )
        for mismatch in mismatches:
            with self.subTest(mismatch=mismatch):
                token = {**copy.deepcopy(authorization), **mismatch}
                token_before = copy.deepcopy(token)
                with self.assertRaises(support.Paused):
                    runner.repeated_failure_resume_guard(self.state, self.root, authorization=token)
                self.assertEqual(token_before, token)
                self.assertEqual(before, self.state)
        # Every rejected token leaves the exact inspected grant usable once.
        runner.repeated_failure_resume_guard(self.state, self.root, authorization=authorization)
        self.assertTrue(authorization["consumed"])
        with self.assertRaises(support.Paused):
            runner.repeated_failure_resume_guard(self.state, self.root, authorization=authorization)

    def test_fallback_failure_identity_cannot_authorize_a_different_key(self):
        record = self.seed()
        state = copy.deepcopy(self.state)
        # Keep a real repeated fallback but corrupt the last record's key.
        state["stages"][-1]["failure_key"] = "missing"
        self.assertIsNotNone(runner.failures.repeated(state, state["stages"][-1]))
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            runner.authorize_failure_retry(state, self.run, self.root)
        self.assertEqual(before, state)
        self.assertIn(record["failure_key"], state["failure_history"])

    def test_a_spent_repair_is_reconciled_before_the_unchanged_failure_hold(self):
        # Live #1899: the hold told the operator to authorize an inspected retry while the spent repair
        # it left pending made that exact retry inadmissible, so no command could advance the run.
        record = self.seed()
        self.state["settings"]["report_repair"] = {"max_attempts": 2}
        repair = {"attempts": 2, "original": record, "latest_rejected": True}
        self.state["pending_report_repair"] = repair
        support.atomic_json(self.run / "state.json", self.state)
        with self.assertRaises(support.Paused):
            runner.prepare_exhausted_execution_report_retry(self.state, self.run, self.root)
        self.assertNotIn("pending_report_repair", self.state)
        self.assertIs(repair, self.state["report_repair_archive"][-1]["repair"])
        # The retry the hold names is now the command the gate admits.
        self.assertIsNotNone(runner.authorize_failure_retry(self.state, self.run, self.root))

    def test_a_repair_still_queued_is_dispatched_instead_of_held(self):
        # A plain resume with a repair left to run dispatches that report-only repair, not another
        # attempt at the failed work, so the unchanged-failure hold must not pre-empt it (#1899).
        record = self.seed()
        self.state["settings"]["report_repair"] = {"max_attempts": 2}
        self.state["pending_report_repair"] = {"attempts": 1, "original": record}
        self.assertIsNone(runner.repeated_failure_resume_guard(self.state, self.root))
        # A spent repair is not a queued one: with nothing left to dispatch the hold still applies.
        self.state["pending_report_repair"]["attempts"] = 2
        with self.assertRaises(support.Paused):
            runner.repeated_failure_resume_guard(self.state, self.root)


class FailureRetryCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def test_cli_grants_one_attempt_not_a_fresh_failure_budget(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.launch(["Build greeting", "--chat"], 0, answers="CLI\nyes\n")
        run, state = self.saved()
        original = next(row for row in state["stages"] if row["stage"] == "sol")
        state.update(status="PAUSED_REPEATED_FAILURE", phase="PAUSED_OR_BLOCKED", next_stage="sol")
        state["settings"]["report_repair"] = {"max_attempts": 0}
        # Seed the same failure the corrupted provider below reproduces: a saved report
        # without its verdict. Only consecutive identical failures count as repetition.
        report = support.read(original["output"])
        report.pop("verdict", None)
        for index in range(3):
            output = run / f"failed-sol-{index}.json"
            support.atomic_json(output, report)
            record = dict(
                original, output=str(output), rejected=True, source_revision=support.snapshot(self.project)["revision"]
            )
            runner.failures.record(state, record, ValueError("$: missing verdict"), support.now())
            state["stages"].append(record)
        support.atomic_json(run / "state.json", state)
        history = copy.deepcopy(state["failure_history"])
        # The runner's own proof rows (runner_owned) are not launches: a refused resume may still prove.
        model_stages = lambda rows: [r for r in rows if not r.get("runner_owned")]  # noqa: E731
        count = len(model_stages(state["stages"]))
        args = ["--run-dir", str(run), "--no-chat", "--resume-paused"]
        self.launch(args, 2)
        self.assertEqual(count, len(model_stages(self.saved()[1]["stages"])))
        self.assertEqual(history, self.saved()[1]["failure_history"])
        # Corrupt only the fixture's validation report, never the artifact: a
        # writing Builder changes the source and therefore the failure identity.
        provider = self.root / "fixture-bin" / "codex"
        provider.write_text(
            provider.read_text() + '\nif stage == "sol":\n'
            '    result.pop("verdict", None)\n'
            '    Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(result))\n'
        )
        self.launch([*args, "--retry-failed-stage"], 2)
        _, retried = self.saved()
        import autocode_resolver_human as human

        public = human.current(retried)
        self.assertEqual("WAITING_FOR_USER", retried["status"])
        self.assertEqual(
            "PAUSED_REPEATED_FAILURE",
            retried["resolver"]["human_escalations"][public["request_id"]]["identity"]["proposal"]["origin"][
                "pause_status"
            ],
        )
        self.assertEqual(4, retried["failure_history"][record["failure_key"]]["count"])
        self.assertEqual(["sol"], [row["stage"] for row in model_stages(retried["stages"])[count:]])
        count = len(model_stages(retried["stages"]))
        self.launch(args, 2)
        self.assertEqual(count, len(model_stages(self.saved()[1]["stages"])))
        self.assertEqual(retried["failure_history"], self.saved()[1]["failure_history"])

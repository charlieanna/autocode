"""A bounded study advances through untouched sites, retaining authenticated failures."""

import contextlib
import copy
import json
import os
import unittest
from pathlib import Path
from unittest import mock

import autocode_changed_code_continuation as continuation
import autocode_changed_code_proof as changed_code
import autocode_command_receipt as command_receipt
import autocode_mutation_check as mutation
import autocode_regression as regression
import autocode_util as util
import autocode_verify as verify

from tests import test_mutation_check as fixtures
from tests.test_verify import isolated_python


class CollectorTests(unittest.TestCase):
    setUp = fixtures.RunnerTests.setUp

    def collect(self, clock, calls, *, previous=None, forced=None):
        fake = fixtures.FakeRun(self.tree, fixtures.WEAK)

        def run(command, label, timeout):
            clock.now += 225
            calls.append(label)
            row = fake(command, label, timeout)
            row.update(output=str(self.evidence / f"{len(calls)}-{label}.log"), command=command)
            row["results"].update(skipped=[], total=2, collection_errors=[])
            if forced and label != "unmutated":
                row.update(forced)
            return row

        return mutation.collect_selected_tests(
            "unittest",
            fixtures.COMMAND,
            [fixtures.C1, fixtures.C2],
            self.tree,
            self.evidence,
            [("app.py", "modified")],
            lambda files: fixtures.DIFF,
            run,
            clock=clock,
            enforcement=True,
            previous=previous,
        )

    def test_resume_runs_only_unattempted_sites_and_keeps_survivors_and_kills(self):
        calls = []
        first = self.collect(fixtures.Clock(), calls)
        rows = first["files"][0]["mutants"]
        self.assertEqual(
            ["killed", "survived", "survived", "not_run", "not_run", "not_run"], [r["outcome"] for r in rows]
        )
        self.assertEqual(["unmutated", "mutant-01", "mutant-02", "mutant-03"], calls)
        saved = copy.deepcopy(first)
        calls.clear()
        final = self.collect(fixtures.Clock(), calls, previous=first)
        self.assertEqual(["unmutated", "mutant-04", "mutant-05", "mutant-06"], calls)
        self.assertEqual(saved, first, "the old report is immutable")
        self.assertEqual(rows[:3], final["files"][0]["mutants"][:3])
        self.assertNotIn("not_run", [row["outcome"] for row in final["files"][0]["mutants"]])
        self.assertEqual(fixtures.WINDOW, (self.tree / "app.py").read_text())

    def test_timeout_and_incomplete_attempt_are_retained_without_relaunch(self):
        for forced in ({"timed_out": True}, {"error": "capture incomplete"}):
            with self.subTest(forced=forced):
                calls = []
                first = self.collect(fixtures.Clock(), calls, forced=forced)
                calls.clear()
                final = self.collect(fixtures.Clock(), calls, previous=first)
                self.assertEqual(["unmutated", "mutant-04", "mutant-05", "mutant-06"], calls)
                self.assertEqual(first["files"][0]["mutants"][:3], final["files"][0]["mutants"][:3])

    def test_a_continuations_reference_does_not_spend_its_mutants_budget(self):
        first = self.collect(fixtures.Clock(), [])
        for outer, expected in (
            (None, ["unmutated", "mutant-04", "mutant-05", "mutant-06"]),
            (900, ["unmutated", "mutant-04"]),
        ):
            with self.subTest(outer=outer):
                clock, calls = fixtures.Clock(), []
                fake = fixtures.FakeRun(self.tree, fixtures.WEAK)

                def run(command, label, timeout, clock=clock, calls=calls, fake=fake):
                    clock.now += 800 if label == "unmutated" else 100
                    calls.append(label)
                    row = fake(command, label, timeout)
                    row.update(output=str(self.evidence / f"{len(calls)}-{label}.log"), command=command)
                    row["results"].update(skipped=[], total=2, collection_errors=[])
                    return row

                final = mutation.collect_selected_tests(
                    "unittest",
                    fixtures.COMMAND,
                    [fixtures.C1, fixtures.C2],
                    self.tree,
                    self.evidence,
                    [("app.py", "modified")],
                    lambda files: fixtures.DIFF,
                    run,
                    clock=clock,
                    deadline=None if outer is None else clock.now + outer,
                    enforcement=True,
                    previous=first,
                )
                # The 800 s reference leaves the window's whole budget to the remaining sites; a total
                # allowance the caller set still ends the window where it did.
                self.assertEqual(expected, calls)
                self.assertTrue(final["collected"])

    def test_changed_tests_or_policy_refuse_before_reference_launch(self):
        first = self.collect(fixtures.Clock(), [])
        for change in ("tests", "policy"):
            with self.subTest(change=change):
                prior = copy.deepcopy(first)
                if change == "tests":
                    prior["selected_tests"] = [fixtures.C1]
                else:
                    prior["execution_policy"] = "another-policy"
                calls = []
                result = self.collect(fixtures.Clock(), calls, previous=prior)
                self.assertFalse(result["collected"])
                self.assertEqual([], calls)

    def test_changed_site_or_source_reruns_the_file_instead_of_transferring_outcomes(self):
        # #4217: a file whose bytes or site inventory changed is re-planned and re-run; its prior
        # outcomes are not transferred, and the study is not refused wholesale.
        first = self.collect(fixtures.Clock(), [])
        for change in ("site", "source"):
            with self.subTest(change=change):
                prior = copy.deepcopy(first)
                if change == "site":
                    prior["files"][0]["mutants"][0]["site_id"] = "another-site"
                else:
                    (self.tree / "app.py").write_text(fixtures.WINDOW + "# changed bytes\n")
                calls = []
                result = self.collect(fixtures.Clock(), calls, previous=prior)
                self.assertTrue(result["collected"])
                self.assertEqual(["unmutated", "mutant-01", "mutant-02", "mutant-03", "mutant-04"], calls)
                (self.tree / "app.py").write_text(fixtures.WINDOW)

    def test_limited_or_unstarted_reference_records_the_budget_without_launching_a_site(self):
        first = self.collect(fixtures.Clock(), [])
        before = copy.deepcopy(first)
        for deadline in (0, 30):
            with self.subTest(deadline=deadline):
                calls = []

                def unavailable(command, label, timeout):
                    calls.append(label)
                    return {"output": str(self.evidence / "timed-out.log"), "timed_out": True, "exit_code": None}

                report = mutation.collect_selected_tests(
                    "unittest",
                    fixtures.COMMAND,
                    [fixtures.C1, fixtures.C2],
                    self.tree,
                    self.evidence,
                    [("app.py", "modified")],
                    lambda files: fixtures.DIFF,
                    unavailable,
                    clock=fixtures.Clock(),
                    deadline=deadline,
                    enforcement=True,
                    previous=first,
                )
                self.assertFalse(report["collected"])
                self.assertEqual([] if deadline == 0 else ["unmutated"], calls)
                reference = report["reference_attempt"]
                self.assertTrue(reference["budget_limited"])
                self.assertEqual(deadline, reference["timeout_seconds"])
                self.assertEqual(deadline == 0, reference.get("not_started", False))
                self.assertEqual(deadline != 0, reference["timed_out"])
                self.assertEqual(before, first)


OTHER = "def pick(n):\n    if n > 0:\n        return n\n    return 0\n"
OTHER_DIFF = "diff --git a/other.py b/other.py\n--- a/other.py\n+++ b/other.py\n@@ -1,0 +2,3 @@\n"
TWO_DIFFS = {"app.py": fixtures.DIFF, "other.py": OTHER_DIFF}


class PerFileRetentionTests(unittest.TestCase):
    """#4217: retention is per file, so editing one file between proofs keeps the others' observations.

    A deterministic fake ``run`` reports every selected test passing, so each site survives and the
    only thing under test is which sites are retained, re-run, and in what order. No model is launched.
    """

    def setUp(self):
        fixtures.RunnerTests.setUp(self)
        (self.tree / "other.py").write_text(OTHER)

    def collect(self, calls, *, previous=None):
        def run(command, label, timeout):
            calls.append(label)
            return {
                "output": str(self.evidence / f"{len(calls)}-{label}.log"),
                "command": command,
                "exit_code": 0,
                "timed_out": False,
                "duration_seconds": 0.5,
                "results": {
                    "complete": True,
                    "passed": [fixtures.C1, fixtures.C2],
                    "failed": [],
                    "skipped": [],
                    "total": 2,
                    "collection_errors": [],
                },
            }

        return mutation.collect_selected_tests(
            "unittest",
            fixtures.COMMAND,
            [fixtures.C1, fixtures.C2],
            self.tree,
            self.evidence,
            [("app.py", "modified"), ("other.py", "modified")],
            lambda chosen: TWO_DIFFS[chosen[0][0]],
            run,
            clock=fixtures.Clock(),
            enforcement=True,
            previous=previous,
        )

    def test_an_unchanged_file_keeps_its_outcomes_when_another_file_changes_bytes(self):
        calls = []
        first = self.collect(calls)
        self.assertTrue(first["collected"])
        app_rows = first["files"][0]["mutants"]
        other_rows = first["files"][1]["mutants"]
        self.assertEqual(6, len(app_rows))
        self.assertEqual(6, len(other_rows))
        # app.py is planned first (sites 01-06), other.py second (sites 07-12); every site ran.
        self.assertEqual(["unmutated", *[f"mutant-{n:02d}" for n in range(1, 13)]], calls)

        # The Builder edits only other.py; app.py's bytes are untouched.
        (self.tree / "other.py").write_text(OTHER + "# touched\n")
        calls.clear()
        second = self.collect(calls, previous=first)
        self.assertTrue(second["collected"])
        # app.py's authenticated outcomes are retained verbatim, not re-run.
        self.assertEqual(app_rows, second["files"][0]["mutants"])
        self.assertEqual(["unmutated", *[f"mutant-{n:02d}" for n in range(7, 13)]], calls)
        # other.py re-planned and re-ran: its outcomes carry this window's fresh evidence.
        rerun = second["files"][1]["mutants"]
        self.assertEqual(len(other_rows), len(rerun))
        self.assertNotEqual(other_rows, rerun)
        self.assertTrue(all(row["evidence"] for row in rerun))
        self.assertEqual(OTHER + "# touched\n", (self.tree / "other.py").read_text())
        self.assertEqual(fixtures.WINDOW, (self.tree / "app.py").read_text())

    def test_changed_selected_tests_transfer_no_outcome_even_for_unchanged_files(self):
        calls = []
        first = self.collect(calls)
        prior = copy.deepcopy(first)
        prior["selected_tests"] = [fixtures.C1]  # a different test set than the current selection
        calls.clear()
        result = self.collect(calls, previous=prior)
        self.assertFalse(result["collected"])
        self.assertEqual([], calls, "no mutant may run against a mismatched retained study")
        self.assertIn("does not match the selected tests", result["note"])

    def test_never_observed_sites_run_before_a_re_run_of_an_observed_site(self):
        calls = []
        first = self.collect(calls)
        prior = copy.deepcopy(first)
        # app.py stays byte-identical, but its first site is a symbol-narrowed survivor: it must run
        # again on every selected test, so it is a re-run. other.py's bytes change, so its sites are
        # never-observed. In plan order app.py's re-run precedes other.py, but coverage comes first.
        prior["files"][0]["mutants"][0]["selection_rule"] = "symbol_closure"
        prior["files"][0]["mutants"][0]["outcome"] = "survived"
        (self.tree / "other.py").write_text(OTHER + "# touched\n")
        calls.clear()
        second = self.collect(calls, previous=prior)
        self.assertTrue(second["collected"])
        # other.py's never-observed sites (07-12) run before app.py's re-run site (01).
        self.assertEqual(
            ["unmutated", *[f"mutant-{n:02d}" for n in range(7, 13)], "mutant-01"],
            calls,
        )


class ProofTests(unittest.TestCase):
    def setUp(self):
        fixtures.ProofTests.setUp(self)
        self.state["settings"]["regression"] = {"python": isolated_python(self)}

    def partial(self, allowance=0):
        original = mutation.collect_selected_tests

        def sliced(*args, **kwargs):
            args = list(args)
            run = args[7]
            clock = fixtures.Clock()

            def execute(*parameters):
                result = run(*parameters)
                clock.now += 400
                return result

            args[7] = execute
            kwargs["clock"] = clock
            return original(*args, **kwargs)

        patcher = mock.patch.object(mutation, "collect_selected_tests", side_effect=sliced)
        patcher.start()
        self.addCleanup(patcher.stop)
        with mock.patch.object(continuation, "allowance", return_value=allowance):
            proof = regression.prove(self.state, self.root, self.run_dir)
        self.assertEqual(verify.PASS, proof["verdict"], proof.get("failures", []) + proof.get("unverified", []))
        if not allowance:
            self.assertGreater(continuation.pending(proof), 0)
        return proof

    def test_a_fresh_study_on_a_runtime_that_cannot_be_reused_finishes_in_the_same_invocation(self):
        """Live run 2026-11-20: the project's tests ran on the system python3, so the proof's identity was
        fresh_execution_only. Its 181-site study stopped at the 900 s window with 107 sites unrun, nothing
        continued it, the unrun lines kept no evidence and the run paused for a person."""
        identity = verify.execution_identity

        def fresh_only(*args, **kwargs):
            return {**identity(*args, **kwargs), "reuse_supported": False, "cache_policy": "fresh_execution_only"}

        with mock.patch.object(verify, "execution_identity", side_effect=fresh_only):
            final = self.partial(allowance=3600)
        self.assertFalse(final["execution_context"]["identity"]["reuse_supported"])
        self.assertEqual(0, continuation.pending(final))
        self.assertIn("mutation_continuation", final)
        self.assertEqual(0, sum(self.state["stages"][-1]["metrics"]["provider_tokens"].values()))
        # A saved proof from an earlier invocation is still not continued on such an identity.
        saved = {**final, "mutation_check": copy.deepcopy(self.partial_study(final))}
        with mock.patch.object(continuation, "_window", side_effect=AssertionError("saved proof continued")):
            self.assertIs(
                saved,
                continuation.continue_proof(
                    self.state,
                    self.root,
                    self.run_dir,
                    saved,
                    None,
                    None,
                    lambda: saved["execution_context"]["identity"],
                ),
            )

    @staticmethod
    def partial_study(proof):
        study = copy.deepcopy(proof["mutation_check"])
        row = study["files"][0]["mutants"][-1]
        row.update(outcome="not_run", evidence=[])
        return study

    def test_cached_partial_proof_finishes_remaining_sites_without_repeating_functional_checks(self):
        first = self.partial()
        saved = Path(first["path"]).read_bytes()
        before = json.loads(saved)
        old_outputs = {row["output"]: Path(row["output"]).read_bytes() for row in before["changed_code_receipts"]}
        with mock.patch.object(verify, "verify", side_effect=AssertionError("functional proof repeated")):
            final = regression.prove(self.state, self.root, self.run_dir)
        self.assertEqual(0, continuation.pending(final))
        self.assertEqual(saved, Path(first["path"]).read_bytes())
        after = json.loads(Path(final["path"]).read_bytes())
        self.assertEqual(before["checks"], after["checks"])
        self.assertNotEqual(first["path"], final["path"])
        self.assertTrue(changed_code.intact(final, after))
        self.assertGreater(self.state["active_seconds"], 0)
        self.assertEqual(0, sum(self.state["stages"][-1]["metrics"]["provider_tokens"].values()))
        for output, data in old_outputs.items():
            self.assertEqual(data, Path(output).read_bytes())
        self.assertTrue(set(old_outputs) <= {row["output"] for row in after["changed_code_receipts"]})
        rows = [row for file in final["mutation_check"]["files"] for row in file["mutants"]]
        attempted = [row for file in first["mutation_check"]["files"] for row in file["mutants"] if row["evidence"]]
        self.assertTrue(all(row in rows for row in attempted))
        # The weak test is still weak. Finishing collection grants no completion.
        self.assertEqual("UNVERIFIED", final["changed_code_evidence"]["status"])
        self.assertFalse(regression.complete(self.state, final["source_revision"], all_due=True))
        with mock.patch.object(verify, "run_suite", side_effect=AssertionError("completed study repeated")):
            cached = regression.prove(self.state, self.root, self.run_dir)
        self.assertEqual(final["path"], cached["path"])

    def reference_failure(self, kind):
        first = self.partial()
        original_bytes = Path(first["path"]).read_bytes()
        original_study = copy.deepcopy(first["mutation_check"])
        original_history = len(self.state["history"])
        run_suite = verify.run_suite
        observed = []

        def timed_out(proof, result, framework, workspace, out, tree, context, **kwargs):
            # This unavailable measurement deliberately has no completed capture.
            # The transaction must retain its prior authenticated checkpoint.
            output = Path(out) / "reference-timeout.log"
            output.write_text("")
            receipt = {"output": str(output), "timed_out": True, "exit_code": None, "duration_seconds": 0}
            observed.append(receipt)
            proof.setdefault("changed_code_receipts", []).append(receipt)
            return mutation.record("the reference exhausted its remaining window")

        def run(framework, command, tree, evidence_dir, label, **kwargs):
            if label != "unmutated":
                self.fail("No mutant may run after an unavailable reference")
            test = Path(tree) / "tests/test_calc.py"
            source, times = test.read_bytes(), test.stat()
            try:
                if kind == "assertion":
                    self.assertIn(b"self.assertEqual(10, limit(50))", source)
                    test.write_bytes(
                        source.replace(b"self.assertEqual(10, limit(50))", b"self.assertEqual(11, limit(50))")
                    )
                else:
                    self.assertEqual("missing_test", kind)
                    test.write_bytes(source.replace(b"def test_t1_caps_large_values", b"def test_unrelated"))
                receipt = run_suite(framework, command, tree, evidence_dir, label, **kwargs)
                observed.append(receipt)
                return receipt
            finally:
                test.write_bytes(source)
                os.utime(test, ns=(times.st_atime_ns, times.st_mtime_ns))

        with mock.patch.object(verify, "verify", side_effect=AssertionError("functional proof repeated")):
            reference = (
                mock.patch.object(changed_code, "collect", side_effect=timed_out)
                if kind == "timeout"
                else mock.patch.object(verify, "run_suite", side_effect=run)
            )
            with reference, contextlib.suppress(util.Paused):
                regression.prove(self.state, self.root, self.run_dir)
        self.assertEqual(1, len(observed))
        if kind == "timeout":
            self.assertTrue(observed[0]["timed_out"])
        else:
            self.assertNotEqual(0, observed[0]["exit_code"])
        self.assertEqual(original_bytes, Path(first["path"]).read_bytes())
        retained = self.state["regression_proof"]
        self.assertEqual(original_study, retained["mutation_check"], "an unavailable refresh cannot erase the study")
        self.assertEqual(first["source_revision"], retained["source_revision"])
        self.assertEqual(continuation.pending(first), continuation.pending(retained))
        self.assertEqual("UNVERIFIED", changed_code.reviewed(retained, [])["status"])
        self.assertFalse(regression.complete(self.state, retained["source_revision"], all_due=True))
        self.assertEqual(original_history + 1, len(self.state["history"]))
        record = self.state["history"][-1]
        self.assertTrue(record.get("error_kind"), "the unsuccessful window remains an accounted attempt")
        self.assertIn("retained", record["summary"])
        self.assertEqual(0, sum(record["metrics"]["provider_tokens"].values()))
        failure = json.loads(Path(record["output"]).read_text())
        continuation_record = failure.get("mutation_continuation", failure)
        self.assertEqual(first["path"], continuation_record["previous_path"])
        self.assertEqual(first["receipt_sha256"], continuation_record["previous_sha256"])
        self.assertIn(observed[0]["output"], json.dumps(failure), "retain the failed command receipt")

    def test_timed_out_reference_retains_the_study_and_failed_attempt(self):
        self.reference_failure("timeout")

    def test_exhausted_window_retains_progress_and_a_fresh_success_clears_its_blocker(self):
        first = self.partial()
        original = util.read(first["path"])
        unavailable = mutation.record("preparation exhausted this window")
        unavailable["reference_attempt"] = {"not_started": True, "budget_limited": True}
        with mock.patch.object(changed_code, "collect", return_value=unavailable):
            retained = continuation._window(
                self.state,
                self.root,
                self.run_dir,
                first,
                original,
                None,
                None,
                first["execution_context"],
                -1,
                lambda: first["execution_context"]["identity"],
            )
        self.assertEqual(first["mutation_check"], retained["mutation_check"])
        self.assertEqual("PASS", retained["verdict"])
        self.assertEqual("UNVERIFIED", retained["changed_code_evidence"]["status"])
        self.assertEqual("window_budget_exhausted", retained["changed_code_inputs"]["mutation_refresh_failure"]["kind"])
        self.assertTrue(changed_code.intact(retained, util.read(retained["path"])))
        with mock.patch.object(verify, "verify", side_effect=AssertionError("functional proof repeated")):
            final = regression.prove(self.state, self.root, self.run_dir)
        self.assertEqual(0, continuation.pending(final))
        self.assertNotIn("mutation_refresh_failure", final["changed_code_inputs"])
        self.assertTrue(changed_code.intact(final, util.read(final["path"])))
        self.assertNotIn("Mutation refresh", changed_code.reviewed(final, [])["reason"])

    def test_failing_reference_retains_the_study_and_failed_attempt(self):
        self.reference_failure("assertion")

    def test_incomplete_reference_retains_the_study_and_failed_attempt(self):
        self.reference_failure("missing_test")

    def test_policy_three_cannot_reuse_authority_before_refresh_retention_was_enforced(self):
        first = self.partial()
        first["execution_context"]["changed_code_policy"] = 3
        with mock.patch.object(verify, "verify", side_effect=AssertionError("old policy requires a fresh proof")):
            with self.assertRaisesRegex(AssertionError, "old policy requires a fresh proof"):
                regression.prove(self.state, self.root, self.run_dir)

    def test_lost_or_changed_custody_cannot_continue_or_silently_restart(self):
        first = self.partial()
        with mock.patch.object(verify, "make_tree", side_effect=AssertionError("unsafe launch")):
            with self.assertRaises(util.Paused):
                continuation.continue_proof(
                    self.state,
                    self.root,
                    self.run_dir,
                    first,
                    None,
                    None,
                    lambda: {**first["execution_context"]["identity"], "runtime_sources": "changed"},
                )
        saved = json.loads(Path(first["path"]).read_text())
        output = Path(saved["changed_code_receipts"][0]["output"])
        output.write_text("tampered")
        with mock.patch.object(verify, "make_tree", side_effect=AssertionError("unsafe launch")):
            with self.assertRaises(util.Paused):
                continuation.continue_proof(
                    self.state,
                    self.root,
                    self.run_dir,
                    first,
                    None,
                    None,
                    lambda: first["execution_context"]["identity"],
                )

    def test_ownership_uncertainty_retains_the_tree_and_original_proof(self):
        first = self.partial()
        old = Path(first["path"]).read_bytes()
        with mock.patch.object(changed_code, "collect", side_effect=command_receipt.OwnershipUncertain("unknown")):
            with self.assertRaises(command_receipt.OwnershipUncertain):
                regression.prove(self.state, self.root, self.run_dir)
        self.assertEqual(old, Path(first["path"]).read_bytes())
        self.assertEqual(first["path"], self.state["regression_proof"]["path"])
        self.assertTrue(list(Path(first["path"]).parent.glob("mutation-continuation-*/scratch/candidate")))
        record = self.state["history"][-1]
        self.assertEqual("OwnershipUncertain", record["error_kind"])
        self.assertEqual(first["receipt_sha256"], json.loads(Path(record["output"]).read_text())["previous_sha256"])
        self.assertGreater(record["duration_seconds"], 0)

    def test_no_progress_window_stops_without_repeating_it_or_spending_a_model_call(self):
        first = self.partial()
        with mock.patch.object(continuation, "_window", return_value=first) as window:
            result = regression.prove(self.state, self.root, self.run_dir)
        self.assertEqual(first["path"], result["path"])
        window.assert_called_once()
        self.assertEqual(1, len(self.state["stages"]))


class BudgetTests(unittest.TestCase):
    def proof(self, attempted):
        return {
            "verdict": "PASS",
            "source_revision": "source",
            "execution_context": {"policy": "current", "identity": {"reuse_supported": True}},
            "mutation_check": {
                "collected": True,
                "reference_output": "reference.log",
                "files": [
                    {
                        "mutants": [
                            {
                                "site_id": str(i),
                                "outcome": "killed" if i < attempted else "not_run",
                                "evidence": [str(i)] if i < attempted else [],
                            }
                            for i in range(3)
                        ]
                    }
                ],
            },
            "changed_code_receipts": [{"output": "reference.log", "duration_seconds": 15}],
        }

    def test_review_waits_for_progressing_local_work_and_changes_no_limits(self):
        previous = self.proof(1)
        state = {
            "settings": {"limits": {"stage_timeout_seconds": 3600, "max_seconds": 10000}},
            "active_seconds": 200,
            "regression_proof": self.proof(2),
        }
        saved = copy.deepcopy(state)
        self.assertTrue(continuation.defer_review(state, previous))
        self.assertEqual(saved, state)
        self.assertFalse(continuation.defer_review(state, state["regression_proof"]))
        state["regression_proof"] = self.proof(3)
        self.assertFalse(continuation.defer_review(state, previous))
        state["regression_proof"] = self.proof(0)
        state["regression_proof"]["execution_context"] = {"policy": "new"}
        self.assertFalse(continuation.defer_review(state, previous), "no new observed site means no automatic loop")
        state["regression_proof"] = self.proof(1)
        self.assertTrue(continuation.defer_review(state, None))

    def test_unreusable_measurements_do_not_repeat_their_mutation_prefix(self):
        previous = self.proof(1)
        state = {"settings": {}, "regression_proof": self.proof(2)}
        for identity in ({}, None, "invalid", {"reuse_supported": False}, {"reuse_supported": "yes"}):
            with self.subTest(identity=identity):
                state["regression_proof"]["execution_context"]["identity"] = identity
                self.assertFalse(continuation.defer_review(state, previous))

    def test_unverified_or_unavailable_measurements_cannot_authorize_a_deferred_review(self):
        previous = self.proof(1)
        state = {"settings": {}, "regression_proof": self.proof(2)}
        for verdict in ("FAIL", "UNVERIFIED"):
            state["regression_proof"]["verdict"] = verdict
            self.assertFalse(continuation.defer_review(state, previous))
        state["regression_proof"]["verdict"] = "PASS"
        for failure in ({}, None, "invalid", {"kind": "reference_unavailable"}):
            state["regression_proof"]["changed_code_inputs"] = {"mutation_refresh_failure": failure}
            self.assertFalse(continuation.defer_review(state, previous))
        state["regression_proof"]["changed_code_inputs"] = {
            "mutation_refresh_failure": {"kind": "window_budget_exhausted"}
        }
        self.assertTrue(continuation.defer_review(state, previous))
        state["regression_proof"]["changed_code_inputs"] = {"custody_gaps": ["source changed"]}
        self.assertFalse(continuation.defer_review(state, previous))

    def test_next_window_reserves_the_observed_reference_and_respects_the_total_cap(self):
        previous, current = self.proof(1), self.proof(2)
        self.assertEqual(75, continuation.minimum_window(current))
        for value in (None, "15", -1, float("nan")):
            current["changed_code_receipts"][0]["duration_seconds"] = value
            self.assertEqual(mutation.MUTANT_FLOOR_SECONDS, continuation.minimum_window(current))
        current["changed_code_receipts"][0]["duration_seconds"] = 15
        state = {
            "settings": {"limits": {"stage_timeout_seconds": 3600, "max_seconds": 10000}},
            "active_seconds": 9990,
            "regression_proof": current,
        }
        saved = copy.deepcopy(state)
        with self.assertRaises(util.Paused) as blocked:
            continuation.defer_review(state, previous)
        self.assertEqual("PAUSED_TIME_LIMIT", blocked.exception.status)
        self.assertEqual(saved, state)
        current["changed_code_receipts"] = []
        self.assertEqual(mutation.MUTANT_FLOOR_SECONDS, continuation.minimum_window(current))

    def test_saved_stage_and_total_limits_bound_continuation_without_changes(self):
        state = {"settings": {"limits": {"stage_timeout_seconds": 3600, "max_seconds": 10000}}, "active_seconds": 9500}
        saved = copy.deepcopy(state)
        self.assertEqual(500, continuation.allowance(state))
        self.assertEqual(saved, state)
        state["active_seconds"] = 10001
        self.assertEqual(0, continuation.allowance(state))
        state["settings"]["limits"]["max_seconds"] = 0
        self.assertEqual(3600, continuation.allowance(state))

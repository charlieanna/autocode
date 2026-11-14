"""A bounded study advances through untouched sites, retaining authenticated failures."""

import copy
import json
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

    def test_changed_tests_sites_or_policy_refuse_before_reference_launch(self):
        first = self.collect(fixtures.Clock(), [])
        for change in ("tests", "site", "policy", "source"):
            with self.subTest(change=change):
                prior = copy.deepcopy(first)
                if change == "tests":
                    prior["selected_tests"] = [fixtures.C1]
                elif change == "site":
                    prior["files"][0]["mutants"][0]["site_id"] = "another-site"
                elif change == "policy":
                    prior["execution_policy"] = "another-policy"
                else:
                    (self.tree / "app.py").write_text(fixtures.WINDOW + "# changed bytes\n")
                calls = []
                result = self.collect(fixtures.Clock(), calls, previous=prior)
                self.assertFalse(result["collected"])
                self.assertEqual([], calls)
                (self.tree / "app.py").write_text(fixtures.WINDOW)


class ProofTests(unittest.TestCase):
    def setUp(self):
        fixtures.ProofTests.setUp(self)
        self.state["settings"]["regression"] = {"python": isolated_python(self)}

    def partial(self):
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
        with mock.patch.object(continuation, "allowance", return_value=0):
            proof = regression.prove(self.state, self.root, self.run_dir)
        self.assertEqual(verify.PASS, proof["verdict"], proof.get("failures", []) + proof.get("unverified", []))
        self.assertGreater(continuation.pending(proof), 0)
        return proof

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
    def test_saved_stage_and_total_limits_bound_continuation_without_changes(self):
        state = {"settings": {"limits": {"stage_timeout_seconds": 3600, "max_seconds": 10000}}, "active_seconds": 9500}
        saved = copy.deepcopy(state)
        self.assertEqual(500, continuation.allowance(state))
        self.assertEqual(saved, state)
        state["active_seconds"] = 10001
        self.assertEqual(0, continuation.allowance(state))
        state["settings"]["limits"]["max_seconds"] = 0
        self.assertEqual(3600, continuation.allowance(state))

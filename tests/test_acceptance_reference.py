"""A retained candidate's passing tests still need fresh original-code evidence."""

import dataclasses
import difflib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_acceptance_reference as reference
import autocode_acceptance_tests as acceptance
from units import autoreview

from tests.test_acceptance_tests import approved, catalog, fake_setup, materialize, taskrun, without_maintenance


class AcceptanceReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.evidence = self.root / "run" / "iterations" / "001"
        self.evidence.mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.test")
        self.git("config", "user.name", "Fixture")
        (self.workspace / "app.py").write_text("def answer():\n    return 0\n")
        (self.workspace / "tests").mkdir()
        (self.workspace / "tests/__init__.py").write_text("")
        self.git("add", ".")
        self.git("commit", "-qm", "original")
        self.base = self.git("rev-parse", "HEAD").strip()
        (self.workspace / "app.py").write_text("def answer():\n    return 42\n")
        (self.workspace / "tests/test_feature.py").write_text(
            "import unittest\nfrom app import answer\n"
            "class Feature(unittest.TestCase):\n"
            "    def test_ac1_answer(self):\n        self.assertEqual(answer(), 42)\n"
        )
        self.state = approved({"AC1": "test: test_ac1_answer"}, workspace=str(self.workspace))
        self.state["base_commit"] = self.base
        self.state["settings"]["regression"] = {"test_command": f"{sys.executable} -m unittest -v tests.test_feature"}
        acceptance.route(self.state)
        self.value = {
            "tests": [{"case_id": "AC1", "file": "tests/test_feature.py", "test_name": "test_ac1_answer"}],
            "already_passing": [],
            "cannot_write": [],
            "summary": "Test the answer",
            "tests_run": [],
        }
        self.record = {
            "output": str(self.evidence / "writer.json"),
            "source_revision": "candidate",
            "changed_files": ["tests/test_feature.py"],
        }

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.workspace), *args], text=True)

    def apply(self):
        with patch.object(autoreview, "send_back_plan") as send_back:
            autoreview.apply_job(
                "write_acceptance_tests",
                self.state,
                self.value,
                self.record,
                self.workspace,
                run_dir=self.root / "run",
            )
        return send_back

    def test_retained_fix_keeps_test_meaning_and_needs_no_report_repair(self):
        sent = self.apply()
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual("builder", self.state["next_stage"])
        sent.assert_not_called()
        self.assertEqual(
            "test: test_ac1_answer",
            self.state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"],
        )
        receipt = next(iter(acceptance.receipts(self.state).values()))
        self.assertEqual("passes candidate; fails original reference", receipt["results"]["AC1"])
        reference = receipt["original_reference"]
        self.assertEqual(self.base, reference["base"])
        self.assertEqual(["tests.test_feature.Feature.test_ac1_answer"], reference["receipt"]["results"]["failed"])
        self.assertNotIn("regression_proof", self.state, "generation evidence never supplies final proof")

    def test_behavior_already_on_original_base_is_reclassified_without_model_repair(self):
        (self.workspace / "app.py").write_text("def answer():\n    return 42\n")
        self.git("add", "app.py")
        self.git("commit", "-qm", "existing behavior")
        self.state["base_commit"] = self.git("rev-parse", "HEAD").strip()
        sent = self.apply()
        sent.assert_called_once()
        body = sent.call_args.args[1]
        self.assertEqual("guard: test_ac1_answer", body["acceptance_criteria"][0]["verification_method"])
        self.assertFalse(acceptance.receipts(self.state))

    def test_missing_reference_holds_without_relabeling_or_repeating_the_writer(self):
        self.state["base_commit"] = "0" * 40
        sent = self.apply()
        sent.assert_not_called()
        self.assertEqual("PAUSED_ACCEPTANCE_TESTS", self.state["status"])
        self.assertIn("original reference", self.state["stop_reason"])
        self.assertFalse(acceptance.receipts(self.state))

    def test_reported_already_passing_is_still_checked_against_the_original(self):
        self.value["already_passing"] = [{"case_id": "AC1", "evidence": "It passes on the retained fix"}]
        self.apply().assert_not_called()
        self.assertEqual("builder", self.state["next_stage"])
        self.assertEqual("It passes on the retained fix", self.value["already_passing"][0]["evidence"])

    def test_reference_executes_only_observed_case_and_copies_test_helpers(self):
        (self.workspace / "tests/helper.py").write_text("from app import answer\n")
        (self.workspace / "tests/test_feature.py").write_text(
            "import unittest\nfrom tests.helper import answer\n"
            "class Feature(unittest.TestCase):\n"
            "    def test_ac1_answer(self):\n        self.assertEqual(answer(), 42)\n"
            "    def test_unrelated(self):\n        self.assertEqual(answer(), 42)\n"
        )
        self.apply()
        receipt = next(iter(acceptance.receipts(self.state).values()))["original_reference"]["receipt"]
        self.assertEqual(1, receipt["results"]["total"])
        self.assertEqual(["tests.test_feature.Feature.test_ac1_answer"], receipt["results"]["failed"])

    def test_reference_import_failure_does_not_establish_behavioral_failure(self):
        (self.workspace / "app.py").write_text("def new_answer():\n    return 42\n")
        test = self.workspace / "tests/test_feature.py"
        test.write_text(test.read_text().replace("from app import answer", "from app import new_answer as answer"))
        self.apply().assert_not_called()
        self.assertEqual("PAUSED_ACCEPTANCE_TESTS", self.state["status"])
        self.assertFalse(acceptance.receipts(self.state))

    def test_reference_skip_does_not_establish_behavioral_failure(self):
        test = self.workspace / "tests/test_feature.py"
        test.write_text(
            test.read_text().replace(
                "        self.assertEqual(answer(), 42)",
                "        if answer() == 0: self.skipTest('not implemented')\n        self.assertEqual(answer(), 42)",
            )
        )
        self.apply().assert_not_called()
        self.assertEqual("PAUSED_ACCEPTANCE_TESTS", self.state["status"])
        self.assertFalse(acceptance.receipts(self.state))

    def test_source_drift_during_reference_execution_grants_no_receipt(self):
        actual = reference.verify.run_suite

        def drift(*args, **kwargs):
            result = actual(*args, **kwargs)
            if args[4] == "original-reference":
                (self.workspace / "app.py").write_text("def answer():\n    return 43\n")
            return result

        with patch.object(reference.verify, "run_suite", side_effect=drift):
            self.apply().assert_not_called()
        self.assertEqual("PAUSED_ACCEPTANCE_TESTS", self.state["status"])
        self.assertIn("source or launch inputs changed", self.state["stop_reason"])
        self.assertFalse(acceptance.receipts(self.state))

    def test_unfinished_candidate_does_not_start_reference_or_grant_receipt(self):
        actual = reference.verify.scratch_run

        def incomplete(*args, **kwargs):
            receipt = actual(*args, **kwargs)
            receipt["results"]["complete"] = False
            return receipt

        with (
            patch.object(reference.verify, "scratch_run", side_effect=incomplete),
            patch.object(reference, "collect") as collect,
        ):
            self.apply().assert_not_called()
        collect.assert_not_called()
        self.assertEqual("PAUSED_ACCEPTANCE_TESTS", self.state["status"])
        self.assertFalse(acceptance.receipts(self.state))

    def test_untrusted_or_incomplete_reference_cannot_authorize_progress(self):
        actual = reference.verify.run_suite
        for fault in ("timeout", "incomplete", "capture", "setup"):
            with self.subTest(fault=fault):
                # collect does not mutate the run; each observation is fresh.
                def corrupt(*args, **kwargs):
                    result = actual(*args, **kwargs)
                    if fault == "timeout":
                        result["timed_out"] = True
                    elif fault == "incomplete":
                        result["results"]["complete"] = False
                    elif fault == "capture":
                        Path(result["output"]).write_text("tampered")
                    else:
                        result["results"]["setup_errors"] = {
                            "tests.test_feature.Feature.test_ac1_answer": "missing fixture"
                        }
                    return result

                with patch.object(reference.verify, "run_suite", side_effect=corrupt):
                    result = reference.collect(
                        self.state,
                        self.workspace,
                        self.root / "run",
                        self.evidence / fault,
                        {"AC1": ["tests.test_feature.Feature.test_ac1_answer"]},
                        ["tests/test_feature.py"],
                        command=self.state["settings"]["regression"]["test_command"],
                    )
                self.assertIn("error", result)
                self.assertNotIn("verdicts", result)

    def test_failed_candidate_never_runs_original_reference(self):
        (self.workspace / "app.py").write_text("def answer():\n    return 0\n")
        with patch.object(autoreview.acceptance_reference, "collect") as collect:
            self.apply()
        collect.assert_not_called()
        self.assertEqual("builder", self.state["next_stage"])

    def test_inherited_or_mixed_cases_do_not_trigger_reference_check(self):
        cases = [{"id": "AC1", "kind": "restore"}]
        rows = self.value["tests"]
        passing = {"results": {"passed": ["test_ac1_answer"], "failed": []}}
        self.assertEqual({}, reference.passing(cases, rows, passing, {"AC1"}))
        passing["results"]["failed"] = ["test_ac1_answer"]
        self.assertEqual({}, reference.passing(cases, rows, passing, set()))

    def collect(self, **overrides):
        arguments = {
            "state": self.state,
            "workspace": self.workspace,
            "run_dir": self.root / "run",
            "out": self.evidence / "reference",
            "selected": {"AC1": ["tests.test_feature.Feature.test_ac1_answer"]},
            "files": ["tests/test_feature.py"],
            "command": self.state["settings"]["regression"]["test_command"],
        }
        return reference.collect(**(arguments | overrides))

    def test_missing_or_unsafe_reference_inputs_hold_before_execution(self):
        with patch.object(reference.run_base, "base_commit", return_value=None):
            self.assertIn("no recorded original reference", self.collect()["error"])
        for arguments, reason in (
            ({"files": ["app.py"]}, "no independent test-file classification"),
            ({"command": "unknown-test-runner"}, "no recognized test runner"),
            ({"command": "go test -run TestAnswer ./..."}, "cannot select"),
        ):
            with self.subTest(reason=reason), patch.object(reference.verify, "run_suite") as execute:
                self.assertIn(reason, self.collect(**arguments)["error"])
                execute.assert_not_called()

    def test_reviewed_followup_uses_the_reviewed_change_as_its_reference(self):
        reviewed = self.root / "reviewed.patch"
        reviewed.write_text(self.git("diff", self.base, "--", "app.py"))
        with patch.object(reference.follow_up, "review_findings", return_value={"change_patch": str(reviewed)}):
            result = self.collect()
        self.assertEqual({"AC1": "passes"}, result.get("verdicts"), result)
        self.assertEqual(["tests.test_feature.Feature.test_ac1_answer"], result["receipt"]["results"]["passed"])

    def test_operator_seam_is_applied_and_a_changed_or_combined_patch_is_refused(self):
        original = self.git("show", f"{self.base}:app.py")
        seam = "\nanswer_hook = answer\n"
        patch_file = self.root / "seam.patch"
        patch_file.write_text(
            "".join(
                difflib.unified_diff(
                    original.splitlines(True), (original + seam).splitlines(True), "a/app.py", "b/app.py"
                )
            )
        )
        self.state["settings"]["regression"]["base_patch"] = reference.base_patch.pin(
            patch_file, self.workspace, self.base
        )
        (self.workspace / "app.py").write_text("def answer():\n    return 42\n" + seam)
        test = self.workspace / "tests/test_feature.py"
        test.write_text(test.read_text().replace("from app import answer", "from app import answer_hook as answer"))
        result = self.collect()
        self.assertEqual({"AC1": "fails"}, result.get("verdicts"), result)
        self.assertFalse(result["receipt"]["results"].get("setup_errors"))
        with patch.object(reference.follow_up, "review_findings", return_value={"change_patch": str(patch_file)}):
            self.assertIn("combined reviewed and operator reference patches", self.collect()["error"])
        patch_file.write_text(patch_file.read_text() + "\n")
        self.assertIn("changed after it was set", self.collect()["error"])

    def test_mixed_original_outcomes_grant_no_classification(self):
        test = self.workspace / "tests/test_feature.py"
        test.write_text(
            test.read_text() + "class Other(unittest.TestCase):\n"
            "    def test_ac1_answer(self):\n        self.assertIn(answer(), (0, 42))\n"
        )
        result = self.collect(
            selected={"AC1": [f"tests.test_feature.{name}.test_ac1_answer" for name in ("Feature", "Other")]}
        )
        self.assertIn("mixed original-reference outcomes", result["error"])
        self.assertNotIn("verdicts", result)

    def test_uncertain_ownership_preserves_the_reference_tree_and_blocks_progress(self):
        with (
            patch.object(
                reference.verify,
                "run_suite",
                side_effect=reference.command_receipt.OwnershipUncertain("ownership was lost"),
            ),
            self.assertRaisesRegex(reference.command_receipt.OwnershipUncertain, "ownership was lost"),
        ):
            self.collect()
        self.assertTrue((self.evidence / "reference/tree/app.py").is_file())
        self.assertFalse(acceptance.receipts(self.state))

    def test_incomplete_reference_classification_cannot_grant_a_receipt(self):
        with patch.object(reference, "collect", return_value={"verdicts": {}}):
            self.apply().assert_not_called()
        self.assertEqual("PAUSED_ACCEPTANCE_TESTS", self.state["status"])
        self.assertIn("Missing original reference classification", self.state["stop_reason"])
        self.assertFalse(acceptance.receipts(self.state))


class AcceptanceReferenceCliTests(unittest.TestCase):
    def test_retained_candidate_reaches_builder_without_report_repair_or_reapproval(self):
        with tempfile.TemporaryDirectory(prefix="acceptance-reference-cli-") as temporary:
            root = Path(temporary)
            scenario = dataclasses.replace(
                catalog.load("feature-stock-refusals"),
                fake_fault="",
                fake_criteria={"C1": "test: test_c1_move_transfers_units"},
            )
            project = without_maintenance(materialize(scenario.seed, root / "project"))
            flags, environment = fake_setup(scenario, root, scenario.dir / "reference")
            paths = environment["PATH"].split(os.pathsep)
            environment.update(
                AUTOCODE_HOME=str(root / "registry"),
                PATH=os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]]),
                PYTHONDONTWRITEBYTECODE="1",
            )
            run = taskrun.TaskRun.start(
                project,
                scenario.brief,
                options=(*flags, "--joint-planning", "--no-adaptive-planning", "--spec-first"),
                env=environment,
                timeout=180,
                cwd=root,
            )
            for _ in range(8):
                view = run.advance_until_input()
                need = view["needs"]
                if need["kind"] == "approve_plan":
                    break
                self.assertEqual("answer", need["kind"], view)
                question = need["questions"][0]
                run.answer(
                    question["id"], question.get("proposed_default") or "plan", resolver_token=need["resolver_token"]
                )
            else:
                self.fail("No plan approval reached")
            token = need["token"]
            run.approve_plan(token)
            # Simulate an unfinished candidate retained across a revised plan.
            # The original run reference remains the committed seed.
            (run.workspace / "stock.py").write_bytes((scenario.dir / "reference/stock.py").read_bytes())
            run.options = (*run.options, "--pause-after-stage")
            view = run.advance()
            if view["status"] == "PAUSED_REQUESTED" and view["next_stage"] == "orchestrator":
                view = run.resume_paused()
            self.assertEqual("PAUSED_REQUESTED", view["status"], view)
            self.assertEqual("builder", view["next_stage"], view)
            self.assertEqual(token, view["approved_contract"]["token"])
            self.assertEqual("passes candidate; fails original reference", view["acceptance_tests"]["results"]["C1"])
            logs = list(Path(run.run_dir).glob("iterations/*/acceptance-tests*.jsonl"))
            self.assertEqual(1, len(logs), "no format-repair call or duplicate acceptance run")
            self.assertEqual(1, len(list(Path(run.run_dir).glob("iterations/*/acceptance-proof/scratch-*.log"))))
            self.assertFalse(list(Path(run.run_dir).glob("iterations/*/builder-*.jsonl")))

"""Spec-first runs: the Tester writes the acceptance tests before the Builder, and they stay fixed (#1200).

The CLI tests drive real runs through the task-run interface with the scenario harness's fake provider on
feature-stock-refusals, whose reference tests are named after its cases (test_c1_... to test_c7_...).
The status view, the stage artifacts and the workspace are the oracles.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_acceptance_tests as acceptance
import autocode_dispatch as dispatch
import autocode_taskrun as taskrun
from autocode_stray_writes import StrayWrites

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance

CASES = {
    "C1": "test: test_c1_move_transfers_units",
    "C2": "test: test_c2_remove_takes_units_and_drops_empty_holdings",
    "C3": "test: test_c3_move_more_than_on_hand_is_refused",
}


def approved(cases=CASES, **extra):
    criteria = [{"id": cid, "criterion": text, "verification_method": text} for cid, text in cases.items()]
    return {
        "settings": {"spec_first": True},
        "goal_contract": {"hash": "h1", "approval_status": "approved", "body": {"acceptance_criteria": criteria}},
        "next_stage": "builder",
        **extra,
    }


class BlindTestsConfigTests(unittest.TestCase):
    def test_add_argument_registers_blind_tests_flag(self):
        import argparse

        parser = argparse.ArgumentParser()
        acceptance.add_argument(parser)
        parsed = parser.parse_args(["--blind-tests"])
        self.assertTrue(parsed.blind_tests)
        parsed_no = parser.parse_args(["--no-blind-tests"])
        self.assertFalse(parsed_no.blind_tests)

    def test_new_run_setting_enforces_joint_planning(self):
        self.assertTrue(acceptance.new_run_setting(True, joint=True))
        self.assertFalse(acceptance.new_run_setting(False, joint=False))
        self.assertFalse(acceptance.new_run_setting(None, joint=True))
        with self.assertRaisesRegex(ValueError, "--blind-tests needs joint planning"):
            acceptance.new_run_setting(True, joint=False)

    def test_resume_refused_detects_mismatch(self):
        self.assertTrue(acceptance.resume_refused({"blind_tests": True}, False))
        self.assertTrue(acceptance.resume_refused({"blind_tests": False}, True))
        self.assertFalse(acceptance.resume_refused({"blind_tests": True}, True))
        self.assertFalse(acceptance.resume_refused({"blind_tests": True}, None))
        self.assertFalse(acceptance.resume_refused({}, None))

    def test_cli_build_parser_recognizes_blind_tests(self):
        import autocode_args

        parser = autocode_args.build_parser(None, autocode_args.DEFAULT_ROLE_MODELS)
        args = parser.parse_args(["do something", "--blind-tests"])
        self.assertTrue(args.blind_tests)
        args_no = parser.parse_args(["do something", "--no-blind-tests"])
        self.assertFalse(args_no.blind_tests)

    def test_is_blind_and_enabled(self):
        self.assertTrue(acceptance.is_blind({"settings": {"blind_tests": True}}))
        self.assertFalse(acceptance.is_blind({"settings": {"spec_first": True}}))
        self.assertFalse(acceptance.is_blind({"settings": {}}))
        self.assertTrue(acceptance.enabled({"settings": {"blind_tests": True}}))
        self.assertTrue(acceptance.enabled({"settings": {"spec_first": True}}))
        self.assertFalse(acceptance.enabled({"settings": {}}))


class BlindTestsWorkspaceTests(unittest.TestCase):
    def test_blind_tests_leave_protected_files_and_builder_note_empty(self):
        with tempfile.TemporaryDirectory() as root:
            workspace, archive = Path(root) / "w", Path(root) / "archive"
            workspace.mkdir()
            archive.mkdir()
            dummy_test = archive / "test_dummy.py"
            dummy_test.write_bytes(b"def test_dummy(): pass\n")
            entry = {"sha256": acceptance.sha256(dummy_test), "archive": str(dummy_test)}

            state = approved()
            state["settings"] = {"blind_tests": True}
            acceptance.record_receipt(
                state, acceptance.due(state), {"contract_hash": "h1", "files": {"tests/test_stock.py": entry}}
            )

            self.assertEqual({}, acceptance.protected(state))
            self.assertEqual({"tests/test_stock.py": entry}, acceptance.blind_files(state))
            self.assertEqual("", acceptance.builder_note(state))
            self.assertFalse(acceptance.check_untouched(state, workspace))

            view = acceptance.view(state)
            self.assertIsNotNone(view)
            self.assertTrue(view["blind"])
            self.assertEqual(["tests/test_stock.py"], view["files"])

    def test_apply_archives_and_removes_tests_from_workspace_when_blind(self):
        import autocode_acceptance_tests_job as job

        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            evidence_dir = Path(root) / "evidence"
            test_dir = workspace / "tests"
            test_dir.mkdir(parents=True)
            evidence_dir.mkdir(parents=True)

            test_file = test_dir / "test_stock.py"
            test_code = b"import unittest\nclass T(unittest.TestCase):\n    def test_c1_move_transfers_units(self): self.fail()\n"
            test_file.write_bytes(test_code)

            state = approved()
            state["settings"] = {"blind_tests": True}
            acceptance.route(state)
            value = {
                "tests": [
                    {"case_id": "C1", "file": "tests/test_stock.py", "test_name": "test_c1_move_transfers_units"},
                    {
                        "case_id": "C2",
                        "file": "tests/test_stock.py",
                        "test_name": "test_c2_remove_takes_units_and_drops_empty_holdings",
                    },
                    {
                        "case_id": "C3",
                        "file": "tests/test_stock.py",
                        "test_name": "test_c3_move_more_than_on_hand_is_refused",
                    },
                ],
                "cannot_write": [],
                "already_passing": [],
            }
            record = {"output": str(evidence_dir / "tester.json"), "source_revision": "rev1"}

            # Fake runner reporting test failed on base (as required for test: case)
            def run_tests(files):
                return {
                    "command": "python -m unittest tests/test_stock.py",
                    "results": {
                        "passed": [],
                        "failed": [
                            "test_c1_move_transfers_units",
                            "test_c2_remove_takes_units_and_drops_empty_holdings",
                            "test_c3_move_more_than_on_hand_is_refused",
                        ],
                        "complete": True,
                    },
                }

            job.apply(state, value, record, workspace, run_tests=run_tests, evidence_dir=evidence_dir)

            # Test file was archived
            receipt = list(acceptance.receipts(state).values())[0]
            archive_entry = receipt["files"]["tests/test_stock.py"]
            self.assertTrue(Path(archive_entry["archive"]).is_file())
            self.assertEqual(test_code, Path(archive_entry["archive"]).read_bytes())

            # Test file was REMOVED from workspace so Builder never sees it
            self.assertFalse(test_file.exists())
            self.assertEqual("builder", state["next_stage"])


class BlindTestsCandidateExecutionTests(unittest.TestCase):
    def test_reconcile_candidate_failure_opens_blocking_runner_finding(self):
        import autocode_completion as completion
        import autocode_findings as findings_ledger

        state = approved()
        state["settings"]["blind_tests"] = True
        receipt = {
            "exit_code": 1,
            "results": {"passed": [], "failed": ["test_c1_move_transfers_units"]},
            "output": "FAILED (failures=1)",
        }
        row = acceptance.reconcile_candidate(state, receipt, "rev1234567890ab")
        self.assertIsNotNone(row)
        self.assertEqual("runner", row["source"])
        self.assertEqual("open", row["status"])
        self.assertTrue(row["blocking"])
        self.assertTrue(row["blind_tests"])
        self.assertEqual("rev1234567890ab", row["source_revision"])
        self.assertEqual(["test_c1_move_transfers_units"], row["failed_tests"])

        self.assertEqual(row, acceptance.open_row(state))
        self.assertIn(row, findings_ledger.blocking_entries(state))
        self.assertIn("open blocking findings", completion.ledger_rejection(state))

    def test_reconcile_candidate_success_settles_open_finding(self):
        import autocode_findings as findings_ledger

        state = approved()
        state["settings"]["blind_tests"] = True
        failed_receipt = {
            "exit_code": 1,
            "results": {"passed": [], "failed": ["test_c1"]},
        }
        row = acceptance.reconcile_candidate(state, failed_receipt, "rev1")
        self.assertIsNotNone(row)
        self.assertEqual("open", row["status"])

        passing_receipt = {
            "exit_code": 0,
            "results": {"passed": ["test_c1"], "failed": []},
        }
        settled_result = acceptance.reconcile_candidate(state, passing_receipt, "rev2")
        self.assertIsNone(settled_result)
        self.assertIsNone(acceptance.open_row(state))
        self.assertEqual("resolved", row["status"])
        self.assertEqual([], findings_ledger.blocking_entries(state))

    def test_check_candidate_runs_scratch_with_blind_files(self):
        with tempfile.TemporaryDirectory() as root:
            workspace, run_dir, archive = Path(root) / "w", Path(root) / "run", Path(root) / "archive"
            workspace.mkdir()
            run_dir.mkdir()
            archive.mkdir()
            dummy_test = archive / "test_stock.py"
            dummy_test.write_bytes(b"class T: pass\n")
            entry = {"sha256": acceptance.sha256(dummy_test), "archive": str(dummy_test)}

            state = approved()
            state["settings"]["blind_tests"] = True
            state["run_dir"] = str(run_dir)
            acceptance.record_receipt(
                state,
                acceptance.due(state),
                {
                    "contract_hash": "h1",
                    "files": {"tests/test_stock.py": entry},
                    "command": "python -m unittest tests/test_stock.py",
                },
            )

            calls = []

            def fake_scratch_run(ws, tree, files=None, command=None, tests=None, timeout=None):
                calls.append({"ws": ws, "tree": tree, "files": files, "command": command, "tests": tests})
                return {
                    "exit_code": 1,
                    "results": {"passed": [], "failed": ["test_c1"]},
                }

            row = acceptance.check_candidate(state, workspace, run_dir=run_dir, scratch_run=fake_scratch_run)
            self.assertEqual(1, len(calls))
            self.assertEqual({"tests/test_stock.py": str(dummy_test)}, calls[0]["files"])
            self.assertEqual("python -m unittest tests/test_stock.py", calls[0]["command"])
            self.assertEqual(["tests/test_stock.py"], calls[0]["tests"])
            self.assertIsNotNone(row)
            self.assertEqual("runner", row["source"])

    def test_refuse_raises_when_blind_tests_fail(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "w"
            workspace.mkdir()
            state = approved()
            state["settings"]["blind_tests"] = True
            dummy_test = workspace / "dummy.py"
            dummy_test.write_bytes(b"pass\n")
            acceptance.record_receipt(
                state,
                acceptance.due(state),
                {"contract_hash": "h1", "files": {"tests/test_stock.py": {"archive": str(dummy_test)}}},
            )

            def failing_scratch(ws, tree, **kwargs):
                return {"exit_code": 1, "results": {"failed": ["test_c1"]}}

            with self.assertRaisesRegex(ValueError, "Completion acceptance requires blind acceptance tests to pass"):
                acceptance.refuse(state, workspace, scratch_run=failing_scratch)

            def passing_scratch(ws, tree, **kwargs):
                return {"exit_code": 0, "results": {"passed": ["test_c1"], "failed": []}}

            # Passes without error
            acceptance.refuse(state, workspace, scratch_run=passing_scratch)

    def test_before_review_runs_blind_candidate_check(self):
        import subprocess

        import autocode_regression as regression

        with tempfile.TemporaryDirectory() as root:
            workspace, run_dir = Path(root) / "w", Path(root) / "run"
            workspace.mkdir()
            run_dir.mkdir()
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            dummy_test = workspace / "dummy.py"
            dummy_test.write_bytes(b"pass\n")
            subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(workspace), "-c", "user.name=t", "-c", "user.email=t@test", "commit", "-qm", "init"],
                check=True,
            )

            state = approved()
            state["settings"]["blind_tests"] = True
            acceptance.record_receipt(
                state,
                acceptance.due(state),
                {"contract_hash": "h1", "files": {"tests/test_stock.py": {"archive": str(dummy_test)}}},
            )

            checked = []

            original_check = acceptance.check_candidate
            try:
                acceptance.check_candidate = lambda s, ws, rd: checked.append((s, ws, rd))
                for stage in ("tester", "review_checkpoint", "review_completion"):
                    checked.clear()
                    regression.before_review(state, stage, workspace, run_dir)
                    self.assertEqual(1, len(checked))
                    self.assertEqual((workspace, run_dir), (checked[0][1], checked[0][2]))
                for stage in (None, "builder", "plan_task"):
                    checked.clear()
                    regression.before_review(state, stage, workspace, run_dir)
                    self.assertEqual(0, len(checked))
            finally:
                acceptance.check_candidate = original_check

    def test_accept_completion_refuses_when_blind_tests_fail(self):
        from types import SimpleNamespace

        import autocode_action_decisions as action_decisions

        with tempfile.TemporaryDirectory() as root:
            workspace, run_dir = Path(root) / "w", Path(root) / "run"
            workspace.mkdir()
            run_dir.mkdir()
            dummy_test = workspace / "dummy.py"
            dummy_test.write_bytes(b"pass\n")

            state = approved()
            state["settings"]["blind_tests"] = True
            acceptance.record_receipt(
                state,
                acceptance.due(state),
                {"contract_hash": "h1", "files": {"tests/test_stock.py": {"archive": str(dummy_test)}}},
            )

            errors = []
            runner = SimpleNamespace(
                accept_completion=lambda s, w, run_dir=None: None,
                normalize_human_boundary=lambda s, r: None,
                intervention_metadata=lambda w, r, s: {"pending_count": 0, "inbox_error": None},
                launch_inputs=SimpleNamespace(guard=lambda c, w, r: None),
                status_command=SimpleNamespace(reject_input=lambda r, s, w, rd, err: errors.append(err) or 2),
            )
            args = SimpleNamespace(
                accept_completion=True,
                feedback=None,
                edit_goal=None,
                goal_file=None,
                answer=[],
                delegate=[],
                delegate_all=False,
                reject_assumption=[],
                show_goal=False,
                approve_goal=None,
                approve_review=[],
                reject_review=None,
                planning_review_call_limit=None,
                reconcile_review=None,
                follow_up=None,
                close_finding=None,
                claimed_as=None,
            )

            original_check = acceptance.check_candidate
            try:
                # Mock check_candidate to return a failing finding row
                acceptance.check_candidate = lambda s, ws, run_dir=None, scratch_run=None: {
                    "id": "F-1",
                    "finding": "Blind acceptance tests failed",
                }
                exit_code = action_decisions.apply(runner, args, None, state, None, run_dir, workspace, {})
                self.assertEqual(2, exit_code)
                self.assertEqual(1, len(errors))
                self.assertIn("Completion acceptance requires blind acceptance tests to pass", str(errors[0]))
            finally:
                acceptance.check_candidate = original_check


class RunnerCollectionTests(unittest.TestCase):
    """#1567: a test the run wrote in a form the runner's command does not collect must be fixable."""

    def test_execution_failures_require_execution_while_existing_result_metadata_is_repairable(self):
        import autocode_acceptance_tests_job as job
        from autocode_report_repair_queue import ExecutionEvidenceRequired

        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            test = workspace / "test_example.py"
            test.write_text("def test_example():\n    assert False\n")
            report = {
                "tests": [{"case_id": "T1", "test_name": "test_example", "file": test.name}],
                "cannot_write": [],
                "already_passing": [],
            }
            variants = [
                ("test:", {"error": "test command did not execute"}),
                ("test:", {"results": None}),
                ("test:", {"results": {"passed": [], "failed": []}}),
                ("guard:", {"results": {"passed": [], "failed": ["test_example"]}}),
            ]
            for mark, result in variants:
                with self.subTest(mark=mark, result=result):
                    state = approved({"T1": f"{mark} test_example"}, task="t")
                    acceptance.route(state)
                    # A test the command ran wrongly, or did not run, is the Tester's to fix: the runner gives
                    # it fresh attempts with its evidence before it stops (live run, 2026-11-11).
                    rewrites = job.MAX_REWRITES if result.get("results") else 0
                    for attempt in range(rewrites):
                        job.apply(state, report, {"changed_files": [test.name]}, workspace, run_tests=lambda f: result)
                        self.assertEqual(("RUNNING", job.STAGE), (state["status"], state["next_stage"]))
                        self.assertEqual(attempt + 1, len(job.packet(state)["runner_feedback"]))
                    if rewrites:
                        # #3261: once the runner's own rewrites are spent the attempt is rejected, so the
                        # Investigator takes it; the person is not asked to resume and correct the tests.
                        with self.assertRaisesRegex(job.TestsNeedRewriting, "already sent these tests back 2 time"):
                            job.apply(
                                state, report, {"changed_files": [test.name]}, workspace, run_tests=lambda f: result
                            )
                        self.assertEqual(("RUNNING", job.STAGE), (state["status"], state["next_stage"]))
                        self.assertEqual(rewrites, len(job.packet(state)["runner_feedback"]))
                    else:
                        job.apply(state, report, {"changed_files": [test.name]}, workspace, run_tests=lambda f: result)
                        self.assertEqual(job.PAUSE, state["status"])
                        self.assertEqual(job.STAGE, state["next_stage"])
                        self.assertIn("report-only repair cannot", state["stop_reason"])
                    self.assertFalse(acceptance.receipts(state))
            state = approved({"T1": "test: test_example"})
            acceptance.route(state)
            with self.assertRaisesRegex(ValueError, "already passes") as error:
                job.apply(
                    state,
                    report,
                    {"changed_files": [test.name]},
                    workspace,
                    run_tests=lambda files: {"results": {"passed": ["test_example"], "failed": []}},
                )
            self.assertNotIsInstance(error.exception, ExecutionEvidenceRequired)

    def test_a_suite_that_cannot_run_the_tests_is_not_blamed_on_the_tests(self):
        # #2609: a Go test under a Python unittest binding is a binding gap; the stop says so.
        import autocode_acceptance_tests_job as job
        import autocode_verification_commands as command_policy
        from autocode_test_framework import Framework

        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            (workspace / "sink_test.go").write_text("package log\n")
            report = {
                "tests": [{"case_id": "T1", "test_name": "TestSink", "file": "sink_test.go"}],
                "cannot_write": [],
                "already_passing": [],
            }
            unittest_suite = Framework("unittest", "python3 -m unittest discover -v")
            error = command_policy.untargeted(unittest_suite, ["sink_test.go"])
            state = approved({"T1": "test: TestSink"})
            acceptance.route(state)
            job.apply(
                state,
                report,
                {"changed_files": ["sink_test.go"]},
                workspace,
                run_tests=lambda files: {"error": error, "results": None},
            )
            self.assertEqual((job.PAUSE, job.STAGE), (state["status"], state["next_stage"]))
            self.assertIn("the detected unittest suite `python3 -m unittest discover -v`", state["stop_reason"])
            self.assertIn("The tests are not at fault", state["stop_reason"])
            self.assertIn("--test-command", state["stop_reason"])
            self.assertNotIn("correct the tests", state["stop_reason"])
        self.assertIn("(no detected test suite cannot run them)", command_policy.untargeted(None, ["a_test.go"]))

    def test_the_acceptance_proof_narrows_a_saved_test_command_to_the_reported_files(self):
        # #2609: the operator's saved suite, not a fresh guess, runs the Tester's files when it can.
        from units import autoreview

        calls, captured = [], {}

        def clean_run(workspace, run_dir, **kwargs):
            calls.append(kwargs)
            return {"results": None, "error": "stub"}

        state = {"settings": {"regression": {"test_command": "go test ./..."}}}
        with (
            mock.patch.object(autoreview.launch_inputs, "runners", return_value=(clean_run, None)),
            mock.patch.object(autoreview.acceptance_tests_job, "apply", side_effect=lambda *a, **k: captured.update(k)),
            tempfile.TemporaryDirectory() as root,
        ):
            record = {"output": str(Path(root) / "iterations" / "tester.jsonl")}
            autoreview.apply_job(autoreview.acceptance_tests_job.STAGE, state, {}, record, root, run_dir=Path(root))
            captured["run_tests"](["pkg/util/log/sink_test.go"])
            state["settings"]["regression"]["test_command"] = "make check"
            captured["run_tests"](["pkg/util/log/sink_test.go"])
            state["settings"] = {}
            captured["run_tests"](["pkg/util/log/sink_test.go"])
        self.assertEqual(["go test ./pkg/util/log", None, None], [call["command"] for call in calls])
        self.assertEqual([["pkg/util/log/sink_test.go"]] * 3, [call["tests"] for call in calls])

    def test_c7_tree_detection_path_keeps_env_prefix(self):
        # #3091: with no saved --test-command the plan's own Go command supplies the env prefix,
        # so the proof never falls back to prefix-free tree detection.
        import autocode_go_tests as go_tests
        from units import autoreview

        calls, captured = [], {}

        def clean_run(workspace, run_dir, **kwargs):
            calls.append(kwargs)
            return {"results": None, "error": "stub"}

        state = {
            "settings": {},
            "current_task": {"acceptance_criteria": ["AC1"]},
            "goal_contract": {
                "body": {
                    "acceptance_criteria": [{"id": "AC1", "criterion": "c", "verification_method": "test: test_c1_x"}],
                    "initial_task": {
                        "acceptance_criteria": ["AC1"],
                        "validation_plan": ["env GOTOOLCHAIN=go1.27.2 GOPROXY=off go test ./..."],
                    },
                }
            },
        }
        with (
            mock.patch.object(autoreview.launch_inputs, "runners", return_value=(clean_run, None)),
            mock.patch.object(autoreview.acceptance_tests_job, "apply", side_effect=lambda *a, **k: captured.update(k)),
            tempfile.TemporaryDirectory() as root,
        ):
            record = {"output": str(Path(root) / "iterations" / "tester.jsonl")}
            autoreview.apply_job(autoreview.acceptance_tests_job.STAGE, state, {}, record, root, run_dir=Path(root))
            captured["run_tests"](["pkg/a/a_test.go", "pkg/b/b_test.go"])
        self.assertEqual(
            ["env GOTOOLCHAIN=go1.27.2 GOPROXY=off go test ./pkg/a ./pkg/b"], [call["command"] for call in calls]
        )
        self.assertEqual(
            "env GOTOOLCHAIN=go1.27.2 GOPROXY=off go test -json ./pkg/a ./pkg/b",
            go_tests.parse(calls[0]["command"]).instrument(),
        )

    def test_c2_exit_zero_without_results_keeps_standard_tests_message(self):
        # Guard: a command that exits 0 with no per-test results keeps today's wording.
        import autocode_acceptance_tests_job as job

        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            test = workspace / "test_example.py"
            test.write_text("def test_example():\n    assert False\n")
            report = {
                "tests": [{"case_id": "T1", "test_name": "test_example", "file": test.name}],
                "cannot_write": [],
                "already_passing": [],
            }
            run = {
                "command": "env GOTOOLCHAIN=go1.27.2 GOPROXY=off go test -json ./pkg/a ./pkg/b",
                "exit_code": 0,
                "timed_out": False,
                "results": None,
                "tail": "",
            }
            state = approved({"T1": "test: test_example"})
            acceptance.route(state)
            job.apply(state, report, {"changed_files": [test.name]}, workspace, run_tests=lambda files: run)
            self.assertEqual(job.PAUSE, state["status"])
            self.assertIn(
                "The acceptance tests reported no per-test results; write standard tests for the project",
                state["stop_reason"],
            )

    def test_c3_failed_run_without_results_shows_command_output(self):
        # #3091: a failed command with no per-test results shows its own output, never "write standard tests".
        import autocode_acceptance_tests_job as job

        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            for package in ("pkg/a", "pkg/b"):
                (workspace / package).mkdir(parents=True)
                name = package.rsplit("/", 1)[1] + "_test.go"
                (workspace / package / name).write_text("package x\n")
            report = {
                "tests": [
                    {"case_id": "T1", "test_name": "TestA", "file": "pkg/a/a_test.go"},
                    {"case_id": "T2", "test_name": "TestB", "file": "pkg/b/b_test.go"},
                ],
                "cannot_write": [],
                "already_passing": [],
            }
            run = {
                "command": "env GOTOOLCHAIN=go1.27.2 GOPROXY=off go test -json ./pkg/a ./pkg/b",
                "exit_code": 1,
                "timed_out": False,
                "results": None,
                "tail": "go: go.mod requires go >= 1.27.2 (running go 1.23.4; GOTOOLCHAIN=local)\n",
            }
            state = approved({"T1": "test: TestA", "T2": "test: TestB"})
            acceptance.route(state)
            job.apply(
                state,
                report,
                {"changed_files": ["pkg/a/a_test.go", "pkg/b/b_test.go"]},
                workspace,
                run_tests=lambda files: run,
            )
            self.assertEqual(job.PAUSE, state["status"])
            self.assertIn(
                "go: go.mod requires go >= 1.27.2 (running go 1.23.4; GOTOOLCHAIN=local)", state["stop_reason"]
            )
            self.assertNotIn("write standard tests", state["stop_reason"])

    def already_working_bug_fix(self, passing):
        """Apply a bug fix's Tester report (#1868) whose runner sees the ``passing`` tests pass on today's code.

        The bug's reproduction is ``off_by_one`` (the diagnosis); ``PLAN_1`` is a ``test:`` case the plan added.
        Returns the state, whether the run paused, and the plan bodies ``send_back`` was given."""
        import autocode_acceptance_tests_job as job

        names = {"off_by_one": "test_off_by_one_page_count", "PLAN_1": "test_plan_1_first_page"}
        with tempfile.TemporaryDirectory() as root:
            workspace, evidence = Path(root) / "workspace", Path(root) / "evidence"
            (workspace / "tests").mkdir(parents=True)
            evidence.mkdir()
            (workspace / "tests" / "test_pager.py").write_text(
                "import unittest\n\n\nclass P(unittest.TestCase):\n"
                + "".join(f"    def {name}(self):\n        pass\n\n" for name in names.values())
            )
            state = approved({"PLAN_1": f"test: {names['PLAN_1']}"}, task="page_count is off by one")
            state["goal_contract"]["body"]["task_kind"] = "bugfix"
            state["investigation"] = {
                "outcome": "reproduced",
                "test_cases": [{"id": "off_by_one", "given": "10 items", "when": "page_count(10, 5)", "then": "2"}],
            }
            acceptance.route(state)
            value = {
                "tests": [
                    {"case_id": cid, "test_name": name, "file": "tests/test_pager.py"} for cid, name in names.items()
                ],
                "cannot_write": [],
                "already_passing": [
                    {"case_id": cid, "test_name": names[cid], "evidence": "it passed"} for cid in passing
                ],
            }
            sent = []
            record = {"output": str(evidence / "tester.json"), "changed_files": ["tests/test_pager.py"]}
            results = {
                "passed": [names[cid] for cid in passing],
                "failed": [n for c, n in names.items() if c not in passing],
            }
            job.apply(
                state,
                value,
                record,
                workspace,
                run_tests=lambda files: {"results": results},
                evidence_dir=evidence,
                send_back=lambda body, changes: sent.append((body, changes)),
            )
            return state, state.get("status") == job.PAUSE, sent

    def test_a_bug_fix_sends_a_plan_added_case_that_already_works_back_as_a_guard(self):
        state, paused, sent = self.already_working_bug_fix(["PLAN_1"])
        self.assertFalse(paused, state.get("stop_reason"))
        self.assertEqual(1, len(sent))
        body, changes = sent[0]
        rows = {row["id"]: row["verification_method"] for row in body["acceptance_criteria"]}
        self.assertEqual("guard: test_plan_1_first_page", rows["PLAN_1"])
        self.assertEqual(["PLAN_1"], [change["item"] for change in changes])

    def test_a_bug_fix_whose_reproduction_already_passes_still_pauses_without_a_send_back(self):
        for passing in (["off_by_one"], ["off_by_one", "PLAN_1"]):
            with self.subTest(passing=passing):
                state, paused, sent = self.already_working_bug_fix(passing)
                self.assertTrue(paused)
                self.assertEqual([], sent, "only the user can say whether the diagnosis or the code is wrong")
                self.assertIn("the diagnosis and the code disagree", state["stop_reason"])
                self.assertIn("--feedback", state["stop_reason"])

    def test_an_uncollected_test_names_the_runners_command_and_a_collected_rewrite_is_accepted(self):
        import subprocess

        import autocode_acceptance_tests_job as job
        import autocode_verify as verify

        with tempfile.TemporaryDirectory() as root:
            workspace, evidence = Path(root) / "workspace", Path(root) / "evidence"
            (workspace / "tests").mkdir(parents=True)
            evidence.mkdir()
            (workspace / "stats.py").write_text("def median(xs):\n    return sorted(xs)[len(xs) // 2]\n")
            (workspace / "tests" / "__init__.py").write_text("")
            test_file = workspace / "tests" / "test_stats.py"
            test_file.write_text("from stats import median\n\n\ndef test_odd():\n    assert median([3, 1, 2]) == 2\n")
            git = ["git", "-C", str(workspace), "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run([*git, "init", "-q"], check=True)
            subprocess.run([*git, "add", "-A"], check=True)
            subprocess.run([*git, "commit", "-qm", "base"], check=True)
            name = "test_t1_median_even_four_values"
            state = approved({"T1": f"test: {name}"}, task="median of an even count")
            acceptance.route(state)
            value = {
                "tests": [{"case_id": "T1", "test_name": name, "file": "tests/test_stats.py"}],
                "cannot_write": [],
                "already_passing": [],
            }
            record = {"output": str(evidence / "tester.json"), "changed_files": ["tests/test_stats.py"]}

            def run_tests(files):
                return verify.scratch_run(
                    workspace,
                    evidence / "acceptance-proof",
                    tests=files,
                )

            # An explicit unittest collection hook retains its collector despite bare functions.
            with test_file.open("a") as handle:
                handle.write(
                    f"\n\ndef {name}():\n    assert median([1, 2, 3, 4]) == 2.5\n"
                    "\ndef load_tests(loader, tests, pattern):\n    return tests\n"
                )
            job.apply(state, dict(value), record, workspace, run_tests=run_tests, evidence_dir=evidence)
            # The Tester gets the runner's evidence and a fresh attempt; the person is not asked.
            self.assertEqual(("RUNNING", job.STAGE), (state["status"], state["next_stage"]))
            [message] = job.packet(state)["runner_feedback"]
            self.assertIn(f"no test named {name} ran in tests/test_stats.py", message)
            self.assertIn("-m unittest", message)
            self.assertIn("ran 0 test(s) and did not collect it", message)
            self.assertIn("unittest.TestCase", message)
            self.assertEqual(job.STAGE, state["next_stage"])

            # The retry the message asks for: the same test as a TestCase method fails on today's code.
            test_file.write_text(
                "import unittest\n\nfrom stats import median\n\n\nclass Median(unittest.TestCase):\n"
                f"    def {name}(self):\n        self.assertEqual(2.5, median([1, 2, 3, 4]))\n"
            )
            job.apply(state, dict(value), record, workspace, run_tests=run_tests, evidence_dir=evidence)
            receipt = next(iter(acceptance.receipts(state).values()))
            self.assertEqual({"T1": "fails"}, receipt["results"])
            self.assertEqual("builder", state["next_stage"])

    def test_a_test_that_fails_on_its_own_line_goes_back_to_the_tester_not_to_the_builder(self):
        # Live run, 2026-11-11 (#3262): a test called self.assertIsFalse, which unittest does not have. It
        # errored on today's code, was accepted as failing first, and the Builder could never make it pass.
        import autocode_acceptance_tests_job as job
        import autocode_verify as verify

        name = "test_t1_catalog_lists_remotes"
        with tempfile.TemporaryDirectory() as root:
            workspace, evidence = Path(root) / "workspace", Path(root) / "evidence"
            workspace.mkdir()
            evidence.mkdir()
            (workspace / "catalog.py").write_text("class Catalog:\n    pass\n")
            test_file = workspace / "test_catalog.py"
            git = ["git", "-C", str(workspace), "-c", "user.name=t", "-c", "user.email=t@t"]
            for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
                subprocess.run([*git, *args], check=True)
            value = {
                "tests": [{"case_id": "T1", "test_name": name, "file": "test_catalog.py"}],
                "cannot_write": [],
                "already_passing": [],
            }
            record = {"output": str(evidence / "tester.json"), "changed_files": ["test_catalog.py"]}

            def run_tests(files):
                return verify.scratch_run(workspace, evidence / "acceptance-proof", tests=files)

            header = "import unittest\n\nfrom catalog import Catalog\n\n\nclass RemotesTests(unittest.TestCase):\n"
            for body, own in (
                ("        self.assertIsFalse(Catalog().remotes())\n", "'RemotesTests' object has no attribute"),
                (
                    "        ghost.check()\n        self.assertEqual([], Catalog().remotes())\n",
                    "name 'ghost' is not defined",
                ),
            ):
                with self.subTest(own=own):
                    state = approved({"T1": f"test: {name}"}, task="list remotes")
                    acceptance.route(state)
                    test_file.write_text(header + f"    def {name}(self):\n" + body)
                    job.apply(state, dict(value), record, workspace, run_tests=run_tests, evidence_dir=evidence)
                    self.assertEqual(("RUNNING", job.STAGE), (state["status"], state["next_stage"]))
                    [message] = job.packet(state)["runner_feedback"]
                    self.assertIn(f"T1: {name} in test_catalog.py fails on its own line, not on the product", message)
                    self.assertIn(own, message)
                    self.assertEqual({}, acceptance.receipts(state))

            # The same test failing because the product lacks the method is a proper first failure.
            state = approved({"T1": f"test: {name}"}, task="list remotes")
            acceptance.route(state)
            test_file.write_text(header + f"    def {name}(self):\n        self.assertEqual([], Catalog().remotes())\n")
            job.apply(state, dict(value), record, workspace, run_tests=run_tests, evidence_dir=evidence)
            receipt = next(iter(acceptance.receipts(state).values()))
            self.assertEqual({"T1": "fails"}, receipt["results"])
            self.assertEqual("builder", state["next_stage"])

    def test_a_guard_whose_file_cannot_load_goes_back_to_the_tester_with_the_cause(self):
        # Live run, 2026-11-11: the new tests imported char_count at the top of the module that also held the
        # guard, so on today's code the module could not load, the guard never ran, and the run asked the
        # person how to fix the tests. The runner now names the cause and gives the Tester a fresh attempt.
        import autocode_acceptance_tests_job as job
        import autocode_verify as verify

        with tempfile.TemporaryDirectory() as root:
            workspace, evidence = Path(root) / "workspace", Path(root) / "evidence"
            workspace.mkdir()
            evidence.mkdir()
            (workspace / "textkit.py").write_text("def word_count(text):\n    return len(text.split())\n")
            guard = "class WordCountTests(unittest.TestCase):\n    def test_counts_words(self):\n        self.assertEqual(2, word_count('a b'))\n"
            test_file = workspace / "test_textkit.py"
            test_file.write_text("import unittest\n\nfrom textkit import word_count\n\n\n" + guard)
            git = ["git", "-C", str(workspace), "-c", "user.name=t", "-c", "user.email=t@t"]
            for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
                subprocess.run([*git, *args], check=True)
            state = approved({"G1": "guard: test_counts_words", "T1": "test: test_t1_counts_characters"}, task="t")
            acceptance.route(state)
            value = {
                "tests": [
                    {"case_id": "G1", "test_name": "test_counts_words", "file": "test_textkit.py"},
                    {"case_id": "T1", "test_name": "test_t1_counts_characters", "file": "test_textkit.py"},
                ],
                "cannot_write": [],
                "already_passing": [],
            }
            record = {"output": str(evidence / "tester.json"), "changed_files": ["test_textkit.py"]}

            def run_tests(files):
                return verify.scratch_run(workspace, evidence / "acceptance-proof", tests=files)

            new_test = "class CharCountTests(unittest.TestCase):\n    def test_t1_counts_characters(self):\n"
            test_file.write_text(
                "import unittest\n\nfrom textkit import char_count, word_count\n\n\n"
                + guard
                + "\n\n"
                + new_test
                + "        self.assertEqual(3, char_count('a b'))\n"
            )
            job.apply(state, dict(value), record, workspace, run_tests=run_tests, evidence_dir=evidence)
            self.assertEqual(("RUNNING", job.STAGE), (state["status"], state["next_stage"]))
            self.assertNotIn("stop_reason", state)
            [feedback] = job.packet(state)["runner_feedback"]
            self.assertIn("guard G1 (test_counts_words) does not pass on today's code", feedback)
            self.assertIn("test_textkit.py cannot load on today's code", feedback)
            self.assertIn("char_count", feedback)
            self.assertIn("import what the product does not have yet inside the new tests", feedback)
            self.assertFalse(acceptance.receipts(state))

            # The retry the feedback asks for: the new name imported inside the new test.
            test_file.write_text(
                "import unittest\n\nfrom textkit import word_count\n\n\n"
                + guard
                + "\n\n"
                + new_test
                + "        from textkit import char_count\n\n        self.assertEqual(3, char_count('a b'))\n"
            )
            job.apply(state, dict(value), record, workspace, run_tests=run_tests, evidence_dir=evidence)
            self.assertEqual(
                {"G1": "passes", "T1": "fails"}, next(iter(acceptance.receipts(state).values()))["results"]
            )
            self.assertEqual("builder", state["next_stage"])

    def test_a_skipped_test_goes_back_to_the_tester_named_as_skipped(self):
        # Live run, 2026-11-12 (#3261): four cases were tested through code that only runs on Linux, so on a
        # Mac their tests skipped. The runner called each "did not collect it; write the test so that command
        # runs it", which the Tester could not act on, and the same round repeated for two hours.
        import autocode_acceptance_tests_job as job
        import autocode_verify as verify

        with tempfile.TemporaryDirectory() as root:
            workspace, evidence = Path(root) / "workspace", Path(root) / "evidence"
            workspace.mkdir()
            evidence.mkdir()
            (workspace / "textkit.py").write_text("def word_count(text):\n    return len(text.split())\n")
            guard = "    def test_counts_words(self):\n        self.assertEqual(2, word_count('a b'))\n"
            new_test = (
                "    def test_t1_counts_characters(self):\n        from textkit import char_count\n\n"
                "        self.assertEqual(3, char_count('a b'))\n"
            )
            head = "import unittest\n\nfrom textkit import word_count\n\n\nclass TextTests(unittest.TestCase):\n"
            skip = '    @unittest.skipIf(True, "needs another host")\n'
            test_file = workspace / "test_textkit.py"
            test_file.write_text(head + guard)
            git = ["git", "-C", str(workspace), "-c", "user.name=t", "-c", "user.email=t@t"]
            for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
                subprocess.run([*git, *args], check=True)
            state = approved({"G1": "guard: test_counts_words", "T1": "test: test_t1_counts_characters"}, task="t")
            acceptance.route(state)
            value = {
                "tests": [
                    {"case_id": "G1", "test_name": "test_counts_words", "file": "test_textkit.py"},
                    {"case_id": "T1", "test_name": "test_t1_counts_characters", "file": "test_textkit.py"},
                ],
                "cannot_write": [],
                "already_passing": [],
            }
            record = {"output": str(evidence / "tester.json"), "changed_files": ["test_textkit.py"]}

            def run_tests(files):
                return verify.scratch_run(workspace, evidence / "acceptance-proof", tests=files)

            # Both the new test and the guard skip on this host.
            test_file.write_text(head + skip + guard + "\n" + skip + new_test)
            job.apply(state, dict(value), record, workspace, run_tests=run_tests, evidence_dir=evidence)
            self.assertEqual(("RUNNING", job.STAGE), (state["status"], state["next_stage"]))
            [feedback] = job.packet(state)["runner_feedback"]
            for case, name in (("G1", "test_counts_words"), ("T1", "test_t1_counts_characters")):
                self.assertIn(f"{case}: {name} in test_textkit.py was collected and then skipped", feedback)
            self.assertIn("write a test for this case that runs here", feedback)
            self.assertNotIn("did not collect it", feedback)
            self.assertNotIn("does not pass on today's code", feedback)
            self.assertFalse(acceptance.receipts(state))

            # The same cases skipped again after the Tester was told: no identical round is granted (#3261). The
            # attempt is rejected for the Investigator instead, and the run is not paused for the person.
            with self.assertRaisesRegex(job.TestsNeedRewriting, "the same cases were skipped again") as caught:
                job.apply(state, dict(value), record, workspace, run_tests=run_tests, evidence_dir=evidence)
            self.assertEqual(["G1", "T1"], caught.exception.skipped)
            self.assertEqual(("RUNNING", job.STAGE), (state["status"], state["next_stage"]))
            self.assertEqual(1, len(job.packet(state)["runner_feedback"]))

            # The retry the feedback asks for: the same tests without the skip.
            test_file.write_text(head + guard + "\n" + new_test)
            job.apply(state, dict(value), record, workspace, run_tests=run_tests, evidence_dir=evidence)
            self.assertEqual(
                {"G1": "passes", "T1": "fails"}, next(iter(acceptance.receipts(state).values()))["results"]
            )
            self.assertEqual("builder", state["next_stage"])

    def test_a_failing_test_that_would_skip_after_the_change_goes_back_to_the_tester(self):
        # Live run, 2026-11-12 (#3333): told its skipUnless test skipped, the Tester wrote one that called
        # self.fail while the function was missing and only then skipTest on a host without /proc. It failed
        # today and was accepted, but after the Builder's change it would skip on that host, so the regression
        # proof could not pass. A skip above the failing line was reached today and is no reason to refuse.
        import autocode_acceptance_tests_job as job
        import autocode_verify as verify

        with tempfile.TemporaryDirectory() as root:
            workspace, evidence = Path(root) / "workspace", Path(root) / "evidence"
            workspace.mkdir()
            evidence.mkdir()
            (workspace / "procinfo.py").write_text("def pid():\n    return 1\n")
            head = "import os\nimport unittest\n\nimport procinfo\n\n\nclass ProcTests(unittest.TestCase):\n"
            unreached = (
                "    def test_ac1_reads_rss(self):\n"
                "        if not hasattr(procinfo, 'rss_kib'):\n"
                "            self.fail('rss_kib is missing')\n"
                "        if not os.path.exists('/no/such/proc'):\n"
                "            self.skipTest('this host has no /proc')\n"
                "        self.assertGreater(procinfo.rss_kib(), 0)\n"
            )
            reached = (
                "    def test_ac1_reads_rss(self):\n"
                "        if not os.path.exists('/'):\n"
                "            self.skipTest('no root directory')\n"
                "        self.assertGreater(procinfo.rss_kib(), 0)\n"
            )
            test_file = workspace / "test_procinfo.py"
            test_file.write_text(head + "    def test_pid(self):\n        self.assertEqual(1, procinfo.pid())\n")
            git = ["git", "-C", str(workspace), "-c", "user.name=t", "-c", "user.email=t@t"]
            for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
                subprocess.run([*git, *args], check=True)
            state = approved({"AC1": "test: test_ac1_reads_rss"}, task="t")
            acceptance.route(state)
            value = {
                "tests": [{"case_id": "AC1", "test_name": "test_ac1_reads_rss", "file": "test_procinfo.py"}],
                "cannot_write": [],
                "already_passing": [],
            }
            record = {"output": str(evidence / "tester.json"), "changed_files": ["test_procinfo.py"]}

            def run_tests(files):
                return verify.scratch_run(workspace, evidence / "acceptance-proof", tests=files)

            test_file.write_text(head + unreached)
            job.apply(state, dict(value), record, workspace, run_tests=run_tests, evidence_dir=evidence)
            self.assertEqual(("RUNNING", job.STAGE), (state["status"], state["next_stage"]))
            [feedback] = job.packet(state)["runner_feedback"]
            self.assertIn("AC1: test_ac1_reads_rss in test_procinfo.py fails today, but below the line", feedback)
            self.assertIn("line 12: self.skipTest('this host has no /proc')", feedback)
            self.assertFalse(acceptance.receipts(state))

            # The same skip again ends the rewrites for the Investigator, as a skipped test does (#3331).
            with self.assertRaisesRegex(job.TestsNeedRewriting, "the same cases were skipped again") as caught:
                job.apply(state, dict(value), record, workspace, run_tests=run_tests, evidence_dir=evidence)
            self.assertEqual(["AC1"], caught.exception.skipped)

            # A skip the test reached today, above the line that failed, did not skip here: it is accepted.
            test_file.write_text(head + reached)
            job.apply(state, dict(value), record, workspace, run_tests=run_tests, evidence_dir=evidence)
            self.assertEqual({"AC1": "fails"}, next(iter(acceptance.receipts(state).values()))["results"])
            self.assertEqual("builder", state["next_stage"])

    def test_only_the_same_skips_again_end_the_runners_rewrites(self):
        # #3261: a round whose skips changed (the Tester fixed one) is still worth a rewrite; a repeat is not.
        import autocode_acceptance_tests_job as job

        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            test = workspace / "test_example.py"
            test.write_text("def test_t1_one():\n    pass\n\n\ndef test_t2_two():\n    pass\n")
            report = {
                "tests": [
                    {"case_id": "T1", "test_name": "test_t1_one", "file": test.name},
                    {"case_id": "T2", "test_name": "test_t2_two", "file": test.name},
                ],
                "cannot_write": [],
                "already_passing": [],
            }
            state = approved({"T1": "test: test_t1_one", "T2": "test: test_t2_two"}, task="t")
            acceptance.route(state)

            def attempt(skipped, failed=()):
                job.apply(
                    state,
                    report,
                    {"changed_files": [test.name]},
                    workspace,
                    run_tests=lambda files: {"results": {"passed": [], "failed": list(failed), "skipped": skipped}},
                )

            # A third rewrite would be allowed by count, so only the repeat can end them here.
            self.enterContext(mock.patch.object(job, "MAX_REWRITES", 3))
            attempt(["test_t1_one", "test_t2_two"])
            attempt(["test_t2_two"], failed=["test_t1_one"])
            self.assertEqual(("RUNNING", job.STAGE), (state["status"], state["next_stage"]))
            self.assertEqual(2, len(job.packet(state)["runner_feedback"]))
            with self.assertRaisesRegex(job.TestsNeedRewriting, "sent these tests back 2 time") as caught:
                attempt(["test_t2_two"], failed=["test_t1_one"])
            self.assertEqual(["T2"], caught.exception.skipped)
            self.assertEqual(2, len(job.packet(state)["runner_feedback"]))

    def test_unconfigured_pytest_acceptance_is_collected_and_really_fails_before_building(self):
        import subprocess
        from unittest.mock import patch

        import autocode_acceptance_tests_job as job
        import autocode_verify as verify

        # The test extra belongs to this interpreter, not necessarily the system python3 on PATH.
        self.enterContext(
            patch.dict(os.environ, {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]})
        )

        with tempfile.TemporaryDirectory() as root:
            workspace, evidence = Path(root) / "workspace", Path(root) / "evidence"
            workspace.mkdir()
            evidence.mkdir()
            (workspace / "totals.py").write_text("def sum_positive(values):\n return sum(values)\n")
            test = workspace / "test_totals.py"
            test.write_text(
                "from totals import sum_positive\ndef test_t1_positive():\n assert sum_positive([1,-2,3,0]) == 4\n"
            )
            git = ["git", "-C", str(workspace), "-c", "user.name=t", "-c", "user.email=t@t"]
            for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
                subprocess.run([*git, *args], check=True)
            state = approved({"T1": "test: test_t1_positive"})
            acceptance.route(state)
            value = {
                "tests": [{"case_id": "T1", "test_name": "test_t1_positive", "file": "test_totals.py"}],
                "cannot_write": [],
                "already_passing": [],
            }
            record = {"output": str(evidence / "tester.json"), "changed_files": ["test_totals.py"]}
            observed = []

            def run_tests(files):
                result = verify.scratch_run(workspace, evidence / "acceptance-proof", tests=files)
                observed.append(result)
                return result

            job.apply(state, value, record, workspace, run_tests=run_tests, evidence_dir=evidence)
            self.assertIn("-m pytest", observed[0]["command"])
            self.assertTrue(observed[0]["results"]["failed"])
            self.assertEqual({"T1": "fails"}, next(iter(acceptance.receipts(state).values()))["results"])
            self.assertEqual("builder", state["next_stage"])
            # Passing on original code must not be manufactured into a failing reproduction.
            (workspace / "totals.py").write_text("def sum_positive(values):\n return sum(v for v in values if v > 0)\n")
            with self.assertRaisesRegex(ValueError, "already passes"):
                job.apply(state, value, record, workspace, run_tests=run_tests, evidence_dir=evidence)


class AcceptanceViewTests(unittest.TestCase):
    def test_current_receipt_wins_regardless_of_serialized_batch_hash_order(self):
        state = approved()
        current = {"contract_hash": "h1", "case_ids": ["C1"], "results": {"C1": "already passes"}, "output": "new.json"}
        historical = {"contract_hash": "old", "case_ids": ["C1"], "results": {"C1": "fails"}, "output": "old.json"}
        for rows in ({"a": current, "z": historical}, {"a": historical, "z": current}):
            state["acceptance_tests"] = {"receipts": json.loads(json.dumps(rows, sort_keys=True))}
            before = json.dumps(state, sort_keys=True)
            self.assertEqual({"C1": "already passes"}, acceptance.view(state)["results"])
            self.assertEqual("new.json", acceptance.view(state)["output"])
            self.assertEqual(before, json.dumps(state, sort_keys=True))

    def test_current_batches_follow_recorded_stage_order_not_hash_or_output_name(self):
        state = approved()
        state["stages"] = [{"output": "z-old.json"}, {"output": "a-new.json"}]
        state["acceptance_tests"] = {
            "receipts": {
                "a": {"contract_hash": "h1", "case_ids": ["C2"], "results": {"C2": "fails"}, "output": "a-new.json"},
                "z": {"contract_hash": "h1", "case_ids": ["C1"], "results": {"C1": "fails"}, "output": "z-old.json"},
            }
        }
        self.assertEqual(["C2"], acceptance.view(state)["case_ids"])
        self.assertEqual("a-new.json", acceptance.view(state)["output"])

    def test_new_contract_has_no_acceptance_display_until_its_own_receipt_exists(self):
        state = approved()
        state["acceptance_tests"] = {"receipts": {"old": {"contract_hash": "old", "results": {"C1": "fails"}}}}
        self.assertIsNone(acceptance.view(state))


class RouteTests(unittest.TestCase):
    def test_a_build_with_unwritten_cases_goes_to_the_tester_once(self):
        state = approved()
        acceptance.route(state)
        self.assertEqual(acceptance.STAGE, state["next_stage"])
        self.assertEqual("builder", acceptance.resume_stage(state))
        self.assertEqual(sorted(CASES), [case["id"] for case in acceptance.job_cases(state)])
        acceptance.record_receipt(
            state, acceptance.job_cases(state), {"contract_hash": "h1", "files": {}, "case_ids": sorted(CASES)}
        )
        state["next_stage"] = "builder"
        acceptance.route(state)
        self.assertEqual("builder", state["next_stage"], "a receipt for these cases means the tests exist")

    def test_an_unchanged_case_keeps_its_receipt_across_a_reapproval_and_a_changed_one_is_rewritten(self):
        """#2464: a reapproval installs a new contract hash; the Tester was sent again for every case."""
        with tempfile.TemporaryDirectory() as root:
            archive = Path(root) / "archived-test.py"
            archive.write_bytes(b"def test_c1(): assert False\n")
            entry = {"sha256": acceptance.sha256(archive), "archive": str(archive)}
            state = approved()
            acceptance.record_receipt(
                state,
                acceptance.due(state),
                {"contract_hash": "h1", "case_ids": sorted(CASES), "files": {"tests/test_stock.py": entry}},
            )
            old = {**state["goal_contract"], "revision": 1, "approval_status": "draft", "approval_event": None}
            state["user_events"] = [{"kind": "goal_approval", "token": "r1:h1"}]
            cited = [
                {**row, "sources": ["brief:1"]} if row["id"] == "C1" else dict(row)
                for row in old["body"]["acceptance_criteria"]
            ]
            cited[2] = {**cited[2], "criterion": "A move of more than on hand is refused with a message"}
            state["goal_contract"] = {
                "hash": "h2",
                "approval_status": "approved",
                "body": {"acceptance_criteria": cited},
            }
            state["contract_history"] = [old]
            self.assertEqual({"C1", "C2"}, set(acceptance.inherited(state)), "C3's criterion changed")
            acceptance.route(state)
            self.assertEqual(["C3"], [case["id"] for case in acceptance.job_cases(state)])
            self.assertEqual({"tests/test_stock.py": entry}, acceptance.protected(state), "inherited files stay fixed")
            self.assertEqual(["tests/test_stock.py"], acceptance.view(state)["files"], "the view shows inherited files")
            for broken in (
                lambda s: s["user_events"].clear(),
                lambda s: s["contract_history"][0].update(revision=2),
                lambda s: archive.write_bytes(b"def test_c1(): pass\n"),
                lambda s: s.update(
                    investigation={"outcome": "reproduced", "test_cases": [{"id": "C1", "kind": "restore"}]}
                ),
            ):
                with self.subTest(broken=broken):
                    candidate = copy.deepcopy(state)
                    archive.write_bytes(b"def test_c1(): assert False\n")
                    broken(candidate)
                    self.assertNotIn("C1", acceptance.inherited(candidate))
            archive.write_bytes(b"def test_c1(): assert False\n")
            state["next_stage"] = "builder"
            acceptance.record_receipt(
                state, acceptance.job_cases(state), {"contract_hash": "h2", "case_ids": ["C3"], "files": {}}
            )
            acceptance.route(state)
            self.assertEqual("builder", state["next_stage"], "every case is covered: two inherited, one rewritten")

    def test_a_parallel_builder_starts_with_the_waves_tests_written_and_fixed(self):
        """A worker's run inherits the parent Tester's receipts (autocode_dispatch.inherited_state).

        Without them each parallel Builder sent itself back to the Tester for its own cases, and
        integration refused the batch for the changed test files."""
        with tempfile.TemporaryDirectory() as root:
            archive = Path(root) / "archived-test.py"
            archive.write_bytes(b"def test_c2(): assert False\n")
            entry = {"sha256": acceptance.sha256(archive), "archive": str(archive)}
            parent = approved(milestone_progress={"h1:M2": {"seconds": 1}})
            parent["acceptance_tests"] = {"pending_case_ids": ["C3"], "redirected_from": "orchestrator"}
            acceptance.record_receipt(
                parent,
                acceptance.due(parent),
                {"contract_hash": "h1", "case_ids": sorted(CASES), "files": {"tests/test_stock.py": entry}},
            )
            child = dispatch.inherited_state(parent)
            self.assertEqual({"receipts": acceptance.receipts(parent)}, child["acceptance_tests"])
            self.assertEqual(parent["milestone_progress"], child["milestone_progress"])
            child.update(next_stage="builder", current_task={"milestone_id": "M2"})
            acceptance.route(child)
            self.assertEqual("builder", child["next_stage"], "the worker's Builder runs; no second Tester")
            self.assertEqual({"tests/test_stock.py": entry}, acceptance.protected(child), "the tests stay fixed")
            # The copy is the worker's own: changing it leaves the parent's record alone.
            child["acceptance_tests"]["receipts"].clear()
            self.assertTrue(acceptance.receipts(parent))
        # A run that wrote no acceptance tests passes nothing on.
        self.assertNotIn("acceptance_tests", dispatch.inherited_state(approved()))

    def test_the_orchestrators_wave_of_milestone_rows_gets_its_cases(self):
        """autocode_dispatch.select returns milestone rows; a live run crashed on them (TypeError, unhashable dict)."""
        state = approved()
        body = state["goal_contract"]["body"]
        body["milestones"] = [
            {"id": "M1", "acceptance_criteria": ["C1"], "depends_on": []},
            {"id": "M2", "acceptance_criteria": ["C2"], "depends_on": ["M1"]},
            {"id": "M3", "acceptance_criteria": ["C3"], "depends_on": ["M1"]},
        ]
        state.update(next_stage="orchestrator", current_task={"milestone_id": "M2"})
        acceptance.route(state, wave=lambda: [copy.deepcopy(row) for row in body["milestones"][1:]])
        self.assertEqual(["C2", "C3"], [case["id"] for case in acceptance.job_cases(state)])

    def test_a_parallel_wave_and_a_later_milestone_get_only_their_unwritten_cases(self):
        state = approved()
        body = state["goal_contract"]["body"]
        body["milestones"] = [
            {"id": "M1", "acceptance_criteria": ["C1"]},
            {"id": "M2", "acceptance_criteria": ["C2"]},
            {"id": "M3", "acceptance_criteria": ["C3"]},
        ]
        state.update(next_stage="orchestrator", current_task={"milestone_id": "M1"})
        acceptance.route(state, wave=lambda: ["M1", "M2"])
        self.assertEqual(["C1", "C2"], [case["id"] for case in acceptance.job_cases(state)], "M3 is not built yet")
        acceptance.record_receipt(state, acceptance.job_cases(state), {"contract_hash": "h1", "case_ids": ["C1", "C2"]})
        state.update(
            next_stage="builder",
            current_task={"milestone_id": "M3"},
            milestone_progress={
                "h1:M1": {"accepted": True, "contract_hash": "h1", "milestone_ids": ["M1"]},
                "h1:M2": {"accepted": True, "contract_hash": "h1", "milestone_ids": ["M2"]},
            },
        )
        acceptance.route(state)
        self.assertEqual(["C3"], [case["id"] for case in acceptance.job_cases(state)], "C1 and C2 already pass now")

    def test_off_or_an_unapproved_plan_builds_as_before(self):
        for state in (
            {**approved(), "settings": {}},
            approved() | {"goal_contract": {**approved()["goal_contract"], "approval_status": "draft"}},
        ):
            acceptance.route(state)
            self.assertEqual("builder", state["next_stage"])

    def test_a_send_back_relabels_only_the_cases_seen_passing_and_happens_once(self):
        import autocode_acceptance_tests_job as job

        state = approved()
        body = job.corrected_body(state, {"C1": "test_c1_move_transfers_units"})
        methods = {row["id"]: row["verification_method"] for row in body["acceptance_criteria"]}
        self.assertEqual("guard: test_c1_move_transfers_units", methods["C1"])
        self.assertEqual(CASES["C2"], methods["C2"])
        self.assertEqual(CASES["C1"], state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"])
        acceptance.record_send_back(state, ["C1"], "out.json")
        self.assertEqual({"C1"}, acceptance.sent_back(state))

    def test_a_changed_test_file_is_restored_and_the_builder_goes_again_twice_then_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            workspace, archive = Path(root) / "w", Path(root) / "archive"
            (workspace / "tests").mkdir(parents=True)
            original = b"def test_c1_move_transfers_units():\n    assert False\n"
            archive.write_bytes(original)
            target = workspace / "tests" / "test_stock.py"
            state = approved()
            entry = {"sha256": acceptance.sha256(archive), "archive": str(archive)}
            acceptance.record_receipt(
                state, acceptance.due(state), {"contract_hash": "h1", "files": {"tests/test_stock.py": entry}}
            )
            for attempt in range(acceptance.DIRECT_RETRIES):
                target.write_bytes(b"def test_c1_move_transfers_units():\n    pass\n")
                state["next_stage"] = "tester"
                self.assertTrue(acceptance.check_untouched(state, workspace), attempt)
                self.assertEqual(("builder", original), (state["next_stage"], target.read_bytes()))
                self.assertIn(
                    "Your previous attempt changed the Tester's lines in tests/test_stock.py",
                    acceptance.builder_note(state),
                )
            target.unlink()  # a deletion counts too
            with self.assertRaisesRegex(StrayWrites, "tests/test_stock.py"):
                acceptance.check_untouched(state, workspace)
            self.assertEqual(original, target.read_bytes())
            self.assertFalse(acceptance.check_untouched(state, workspace))  # a clean attempt
            self.assertNotIn("Your previous attempt", acceptance.builder_note(state))
            self.assertIn("tests/test_stock.py", acceptance.builder_note(state))

    def test_the_builder_may_add_tests_to_a_testers_file_but_not_change_its_lines(self):
        archived = b"import unittest\n\nclass T(unittest.TestCase):\n    def test_c1(self):\n        self.fail()\n"
        self.assertTrue(acceptance.kept(archived, archived + b"\n    def test_c9(self):\n        pass\n"))
        self.assertTrue(acceptance.kept(archived, b"import os\n" + archived))
        self.assertFalse(acceptance.kept(archived, archived.replace(b"self.fail()", b"pass")))
        self.assertFalse(acceptance.kept(archived, None))

    def test_a_bug_fix_also_gets_its_plans_own_cases(self):
        state = approved({"C9": "test: test_c9_holds_for_all_carts"})
        state["goal_contract"]["body"]["task_kind"] = "bugfix"
        state["investigation"] = {
            "outcome": "reproduced",
            "test_cases": [{"id": "T1", "given": "a cart", "when": "it is charged", "then": "no cent is lost"}],
        }
        self.assertEqual(["T1", "C9"], [case["id"] for case in acceptance.due(state)])

    def test_a_parallel_batch_that_edited_a_testers_file_is_not_integrated(self):
        import autocode_dispatch as dispatch
        import autocode_support as support

        with tempfile.TemporaryDirectory() as root:
            patch = Path(root) / "batch.patch"
            added_only = (
                b"diff --git a/tests/test_stock.py b/tests/test_stock.py\n--- a/tests/test_stock.py\n"
                b"+++ b/tests/test_stock.py\n@@ -1,2 +1,4 @@\n def test_c1():\n     assert False\n+def test_extra():\n+    pass\n"
            )
            changed = added_only.replace(b" def test_c1():\n", b"-def test_c1():\n+def test_c1_weakened():\n")
            patch.write_bytes(changed)
            state = approved()
            entry = {"sha256": "0" * 64, "archive": str(Path(root) / "archive")}
            acceptance.record_receipt(
                state, acceptance.due(state), {"contract_hash": "h1", "files": {"tests/test_stock.py": entry}}
            )
            batch = {
                "patch": str(patch),
                "patch_hash": support.file_hash(patch),
                "changed_files": ["stock.py", "tests/test_stock.py"],
                "status": "BUILT",
            }
            with self.assertRaisesRegex(support.Paused, "tests/test_stock.py") as caught:
                dispatch.integrate(state, Path(root), Path(root), batch)
            self.assertEqual("PAUSED_ACCEPTANCE_TESTS", caught.exception.status)
            self.assertEqual("BUILT", batch["status"], "nothing was applied")
            self.assertEqual([], acceptance.edited(state, added_only), "adding tests to the file is allowed")


class SharedDiagnosisBindingTests(unittest.TestCase):
    """The public generation packet keeps both obligations but names one shared function (#1969)."""

    def state(self):
        state = approved(
            {
                "AC1": "test: test_t1_mixed_values",
                "AC2": "guard: test_t2_existing_values",
                "AC3": "test: test_ac3_independent_boundary",
            }
        )
        state["task"] = "Ignore negative values while preserving existing sums."
        state["goal_contract"]["body"]["task_kind"] = "bugfix"
        state["investigation"] = {
            "outcome": "reproduced",
            "test_cases": [
                {"id": "T1", "given": "mixed values", "when": "sum", "then": "negatives ignored"},
                {"id": "T2", "kind": "preserve", "given": "nonnegative values", "when": "sum", "then": "unchanged"},
            ],
        }
        return state

    def names(self, state, *, route=True):
        from autocode_acceptance_tests_job import packet

        if route:
            acceptance.route(state)
        return {row["id"]: row["test_name"] for row in packet(state)["cases"]}

    def test_new_packet_reuses_exact_approved_names_without_dropping_case_ids(self):
        names = self.names(self.state())
        self.assertEqual(
            {
                "T1": "test_t1_mixed_values",
                "T2": "test_t2_existing_values",
                "AC1": "test_t1_mixed_values",
                "AC2": "test_t2_existing_values",
                "AC3": "test_ac3_independent_boundary",
            },
            names,
        )
        self.assertEqual(3, len(set(names.values())), "five ledger obligations need three test functions")

    def test_builder_uses_the_same_approved_binding(self):
        import autocode_test_cases as cases

        note = cases.builder_note(self.state())
        self.assertIn("test name: test_t1_mixed_values;", note)
        self.assertIn("test name: test_t2_existing_values;", note)
        self.assertNotIn("test_t1_<what it checks>", note)

    def test_conflicting_kind_does_not_share_the_guard(self):
        state = self.state()
        state["goal_contract"]["body"]["acceptance_criteria"][1]["verification_method"] = (
            "test: test_t2_existing_values"
        )
        names = self.names(state)
        self.assertIn("<what it checks>", names["T2"])
        self.assertEqual("test_t2_existing_values", names["AC2"])

    def test_two_distinct_approved_names_do_not_choose_one_arbitrarily(self):
        state = self.state()
        state["goal_contract"]["body"]["acceptance_criteria"].append(
            {"id": "AC4", "criterion": "another obligation", "verification_method": "test: test_t1_other"}
        )
        names = self.names(state)
        self.assertIn("<what it checks>", names["T1"])
        self.assertEqual("test_t1_other", names["AC4"])

    def test_a_name_matching_two_diagnosis_ids_does_not_merge_their_obligations(self):
        state = self.state()
        state["investigation"]["test_cases"][1].pop("kind")
        state["goal_contract"]["body"]["acceptance_criteria"] = [
            {"id": "AC1", "criterion": "both", "verification_method": "test: test_t1_t2_combined"}
        ]
        names = self.names(state)
        self.assertIn("<what it checks>", names["T1"])
        self.assertIn("<what it checks>", names["T2"])
        self.assertEqual("test_t1_t2_combined", names["AC1"])

    def test_id_prefix_collision_does_not_establish_a_binding(self):
        state = self.state()
        state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"] = "test: test_t10_other"
        names = self.names(state)
        self.assertIn("<what it checks>", names["T1"])

    def test_old_pending_job_keeps_its_original_id_based_naming_rule(self):
        state = self.state()
        state[acceptance.KEY] = {"pending_case_ids": ["T1", "T2", "AC1", "AC2", "AC3"]}
        names = self.names(state, route=False)
        self.assertIn("<what it checks>", names["T1"])
        self.assertIn("<what it checks>", names["T2"])
        self.assertEqual("test_t1_mixed_values", names["AC1"])

    def test_a_changed_contract_does_not_reuse_a_stale_generation_binding(self):
        state = self.state()
        acceptance.route(state)
        state["goal_contract"]["hash"] = "new-contract"
        names = self.names(state, route=False)
        self.assertIn("<what it checks>", names["T1"])

    def test_a_new_shared_binding_cannot_report_copies_in_different_files(self):
        import autocode_acceptance_tests_job as job

        state = self.state()
        names = self.names(state)
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            rows = [
                {"case_id": cid, "test_name": name, "file": "test_other.py" if cid == "AC1" else "test_totals.py"}
                for cid, name in names.items()
            ]
            value = {"tests": rows, "cannot_write": [], "already_passing": []}
            with self.assertRaisesRegex(ValueError, "same function and file"):
                job.apply(
                    state,
                    value,
                    {"output": str(workspace / "report.json"), "changed_files": []},
                    workspace,
                    run_tests=lambda files: self.fail("Reject copied shared bindings before running a suite"),
                )


class AcceptanceTestsCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="acceptance-tests-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        loaded = catalog.load("feature-stock-refusals")
        self.scenario = dataclasses.replace(loaded, fake_fault="", fake_criteria=dict(CASES))

    def run_to_end(self, *planning, env=None, plan_messages=None, uncollected_writer=False, unloadable_writer=False):
        self.plan_approvals = []
        project = without_maintenance(materialize(self.scenario.seed, self.root / "project"))
        flags, provider_env = fake_setup(self.scenario, self.root, self.scenario.dir / "reference")
        if uncollected_writer:
            provider = self.root / "bin" / "codex"
            marker = "def report_for(stage: str, data: dict) -> dict:\n"
            injection = (
                "original_acceptance_tests = acceptance_tests\n"
                "def acceptance_tests(data):\n"
                "    report = original_acceptance_tests(data)\n"
                "    if os.environ.get('ACCEPTANCE_UNCOLLECTED'):\n"
                "        row = report['tests'][0]\n"
                "        path = Path(row['file'])\n"
                "        path.write_text(path.read_text().replace('def ' + row['test_name'] + '(', 'def absent_' + row['test_name'] + '('))\n"
                "    return report\n\n"
            )
            trace = (
                '    if stage == "write_acceptance_tests":\n'
                '        with open(os.environ["ACCEPTANCE_TRACE"], "a") as stream:\n'
                '            stream.write(json.dumps({"repair": bool(data.get("report_repair"))}) + "\\n")\n'
            )
            source = provider.read_text()
            self.assertIn(marker, source)
            provider.write_text(source.replace(marker, injection + marker + trace))
        if unloadable_writer:
            # The live run's first attempt (2026-11-11): the new tests' name is imported at the top of the file
            # holding the guard, so that file cannot load on today's code. A retry the runner sent back with
            # its feedback writes the file as the reference does.
            provider = self.root / "bin" / "codex"
            marker = "def report_for(stage: str, data: dict) -> dict:\n"
            injection = (
                "original_acceptance_tests = acceptance_tests\n"
                "def acceptance_tests(data):\n"
                "    report = original_acceptance_tests(data)\n"
                "    with open(os.environ['ACCEPTANCE_TRACE'], 'a') as stream:\n"
                "        stream.write(json.dumps({'feedback': data.get('runner_feedback')}) + '\\n')\n"
                "    if not data.get('runner_feedback'):\n"
                "        path = Path(report['tests'][0]['file'])\n"
                "        path.write_text('from stock import not_built_yet\\n' + path.read_text())\n"
                "    return report\n\n"
            )
            source = provider.read_text()
            self.assertIn(marker, source)
            provider.write_text(source.replace(marker, injection + marker))
        if plan_messages:
            # Script only the declared replies. Runtime intent interpretation is still the provider's job.
            provider = self.root / "bin" / "codex"
            marker = "def report_for(stage: str, data: dict) -> dict:\n"
            amendment = (
                '    if stage == "requirements_gather" and data.get("message"):\n'
                f'        intent, reply = {plan_messages!r}[data["message"]["text"]]\n'
                '        return {"message_id": data["message"]["id"],\n'
                '                "contract_token": data["message"]["contract_token"],\n'
                '                "intent": intent, "assistant_reply": reply}\n'
            )
            provider.write_text(provider.read_text().replace(marker, marker + amendment))
        environment = {
            **provider_env,
            "AUTOCODE_HOME": str(self.root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
            **(env or {}),
        }
        paths = environment["PATH"].split(os.pathsep)
        environment["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        run = taskrun.TaskRun.start(
            project,
            self.scenario.brief,
            options=(*flags, *planning),
            env=environment,
            timeout=240,
            cwd=self.root,
        )
        for _ in range(16):
            view = run.advance_until_input()
            need = view.get("needs") or {}
            if view["done"] or need.get("kind") not in ("answer", "approve_plan", "review"):
                return run, project, view
            if need["kind"] == "approve_plan":
                self.plan_approvals.append(need["token"])
                run.approve_plan(need["token"])
            elif need["kind"] == "review":
                for criterion in need["criteria"]:
                    run.approve_review(criterion, need["token"])
            else:
                question = need["questions"][0]
                run.answer(
                    question["id"], question.get("proposed_default") or "plan", resolver_token=need["resolver_token"]
                )
        self.fail("The run did not finish within sixteen public gates")

    @staticmethod
    def stage_order(run):
        # In the order the stages ran: each stage's event log is written while it runs.
        logs = sorted(Path(run.run_dir).glob("iterations/*/*-[0-9][0-9].jsonl"), key=lambda path: path.stat().st_mtime)
        return [path.stem.rsplit("-", 1)[0] for path in logs]

    def details(self, view):
        return {key: view.get(key) for key in ("status", "next_stage", "needs", "stop_reason")}

    def test_uncollected_acceptance_requires_execution_instead_of_report_only_repair(self):
        trace = self.root / "writer-trace.jsonl"
        run, project, held = self.run_to_end(
            "--joint-planning",
            "--spec-first",
            env={"ACCEPTANCE_UNCOLLECTED": "1", "ACCEPTANCE_TRACE": str(trace)},
            uncollected_writer=True,
        )
        calls = [json.loads(line) for line in trace.read_text().splitlines()]
        # The runner's own fresh attempts come first; only then does the run stop (live run, 2026-11-11).
        self.assertEqual([{"repair": False}] * 3, calls)
        self.assertFalse(held["done"], self.details(held))
        self.assertEqual("write_acceptance_tests", held["next_stage"])
        self.assertIn("no test named", held["stop_reason"])
        self.assertEqual("feedback", held["chat"]["input"]["kind"])
        self.assertFalse(any(name.startswith("builder-") for name in self.stage_order(run)))
        original_token = held["approved_contract"]["token"]
        run.env.pop("ACCEPTANCE_UNCOLLECTED")
        completed = run.resume_paused()
        self.assertTrue(completed["done"], self.details(completed))
        self.assertEqual(original_token, completed["approved_contract"]["token"])
        self.assertEqual([{"repair": False}] * 4, [json.loads(line) for line in trace.read_text().splitlines()])

    def test_a_guard_file_that_cannot_load_is_rewritten_without_asking_the_person(self):
        guard = {"C0": "guard: test_receive_adds_units"}
        self.scenario = dataclasses.replace(self.scenario, fake_criteria={**guard, **CASES})
        trace = self.root / "writer-trace.jsonl"
        run, project, view = self.run_to_end(
            "--joint-planning",
            "--no-adaptive-planning",
            "--spec-first",
            env={"ACCEPTANCE_TRACE": str(trace)},
            unloadable_writer=True,
        )
        self.assertTrue(view["done"], self.details(view))
        calls = [json.loads(line)["feedback"] for line in trace.read_text().splitlines()]
        self.assertEqual(2, len(calls), calls)
        self.assertEqual([], calls[0])
        [feedback] = calls[1]
        self.assertIn("guard C0 (test_receive_adds_units) does not pass on today's code", feedback)
        self.assertIn("tests/test_stock.py cannot load on today's code", feedback)
        self.assertIn("not_built_yet", feedback)
        self.assertEqual("passes", view["acceptance_tests"]["results"]["C0"])
        order = self.stage_order(run)
        self.assertLess(order.index("acceptance-tests"), order.index("builder"), order)

    def test_the_tester_writes_failing_tests_before_the_builder_and_the_run_completes(self):
        # C0 guards behavior the seed already has: its test must pass before the Builder starts.
        guard = {"C0": "guard: test_receive_adds_units"}
        # Long case wording exercises lossless references in the actual dispatched prompt (#2153).
        cases = {
            cid: text + " Preserve the stock command behavior and rejection checks." * 12 for cid, text in CASES.items()
        }
        self.scenario = dataclasses.replace(self.scenario, fake_criteria={**guard, **cases})
        run, project, view = self.run_to_end("--joint-planning", "--no-adaptive-planning", "--spec-first")
        self.assertTrue(view["done"], self.details(view))
        order = self.stage_order(run)
        self.assertIn("acceptance-tests", order)
        self.assertLess(order.index("acceptance-tests"), order.index("builder"), order)
        seen = view["acceptance_tests"]
        self.assertEqual(["tests/test_stock.py"], seen["files"])
        self.assertEqual(sorted(["C0", *CASES]), sorted(seen["case_ids"]))
        self.assertEqual("passes", seen["results"]["C0"])
        self.assertTrue({seen["results"][cid] for cid in CASES} <= {"fails", "cannot load"}, seen["results"])
        prompts = list(Path(run.run_dir).glob("iterations/*/acceptance-tests-*.prompt.md"))
        self.assertEqual(1, len(prompts))
        packet = json.loads(prompts[0].read_text().split("CURRENT HANDOFF DATA\n", 1)[1])
        criteria = packet["goal_contract"]["body"]["acceptance_criteria"]
        self.assertEqual(sorted(["C0", *CASES]), sorted(row["id"] for row in packet["cases"]))
        for case in packet["cases"]:
            reference = case["criterion_ref"]
            index = int(reference["path"].split("/")[-2])
            self.assertEqual(case["id"], criteria[index]["id"])
            self.assertEqual(f"{case['id']}: ", reference["prefix"])
            self.assertEqual({**guard, **cases}[case["id"]], criteria[index]["criterion"])

    def test_reapproval_after_builder_retains_fail_first_cases_and_completes_with_fresh_proof(self):
        self._run_reapproval(document=True)

    def test_reapproval_reviews_retained_candidate_without_artificial_source_edit(self):
        self._run_reapproval(document=False)

    def _run_reapproval(self, *, document):
        project = without_maintenance(materialize(self.scenario.seed, self.root / "project"))
        flags, environment = fake_setup(self.scenario, self.root, self.scenario.dir / "reference")
        provider = self.root / "bin" / "codex"
        source = provider.read_text()
        marker = "        edit_acceptance_tests_once()\n"
        self.assertIn(marker, source)
        provider.write_text(
            source.replace(
                marker,
                "        built = Path(os.environ['REAPPROVAL_BUILDER_MARKER'])\n"
                "        if built.exists() and os.environ.get('REAPPROVAL_DOCUMENT') == '1':\n"
                "            product = Path('stock.py')\n"
                "            product.write_text(product.read_text() + chr(10) + '# Transfers retain the existing validation rules.' + chr(10))\n"
                "        built.write_text('built')\n" + marker,
            )
        )
        environment.update(
            AUTOCODE_HOME=str(self.root / "registry"),
            PYTHONDONTWRITEBYTECODE="1",
            REAPPROVAL_BUILDER_MARKER=str(self.root / "builder-marker"),
            REAPPROVAL_DOCUMENT="1" if document else "0",
        )
        environment["PATH"] = os.pathsep.join((str(Path(sys.executable).parent), environment["PATH"]))
        run = taskrun.TaskRun.start(
            project,
            self.scenario.brief,
            options=(*flags, "--joint-planning", "--no-adaptive-planning", "--spec-first", "--pause-after-stage"),
            env=environment,
            timeout=240,
            cwd=self.root,
        )
        revised = False
        approvals = []
        for _ in range(40):
            view = run.advance_until_input()
            need = view.get("needs") or {}
            if view["done"]:
                break
            if not revised and "builder" in self.stage_order(run):
                self.assertEqual("PAUSED_REQUESTED", view["status"], self.details(view))
                self.assertEqual({"fails"}, set(view["acceptance_tests"]["results"].values()))
                original = view["approved_contract"]["token"]
                original_rows = view["approved_contract"]["body"]["acceptance_criteria"]
                original_product = (project / "stock.py").read_bytes()
                queued = subprocess.run(
                    [
                        *run.command,
                        "intervention",
                        "submit",
                        "--workspace",
                        str(project),
                        "--run-dir",
                        str(run.run_dir),
                        "--request-id",
                        "same-turn-reapproval",
                        "--kind",
                        "feedback",
                        "--text",
                        (
                            "Keep every existing behavior, criterion and test binding; document the existing transfer validation in stock.py."
                            if document
                            else "Keep every existing behavior, criterion and test binding; no new source edits are needed."
                        ),
                        "--json",
                    ],
                    env={**os.environ, **environment},
                    text=True,
                    capture_output=True,
                    check=True,
                )
                self.assertTrue(json.loads(queued.stdout)["accepted"])
                run.options = tuple(option for option in run.options if option != "--pause-after-stage")
                revised = True
            elif need.get("kind") == "approve_plan":
                if revised:
                    exported = self.root / ("reapproval-" + str(len(approvals)) + ".md")
                    run.export_plan(exported)
                    lines = exported.read_text().splitlines()
                    end = next(i for i in range(1, len(lines)) if lines[i] == "---")
                    rows = json.loads("\n".join(lines[1:end]))["contract"]["acceptance_criteria"]
                    semantic = lambda row: {k: v for k, v in row.items() if k != "sources"}
                    self.assertEqual([semantic(row) for row in original_rows], [semantic(row) for row in rows])
                    self.assertEqual(1, len(approvals), "no guard-correction approval may be introduced")
                approvals.append(need["token"])
                run.approve_plan(need["token"])
            elif need.get("kind") == "resume":
                run.resume_paused()
            elif need.get("kind") == "answer":
                question = need["questions"][0]
                run.answer(
                    question["id"], question.get("proposed_default") or "plan", resolver_token=need["resolver_token"]
                )
            elif need.get("kind") == "review":
                for criterion in need["criteria"]:
                    run.approve_review(criterion, need["token"])
            else:
                self.fail(self.details(view))
        self.assertTrue(revised)
        self.assertTrue(view["done"], self.details(view))
        self.assertNotEqual(original, view["approved_contract"]["token"])
        self.assertEqual(2, len(approvals), "reapproval must not generate a guard-correction plan")
        methods = {
            row["id"]: row["verification_method"] for row in view["approved_contract"]["body"]["acceptance_criteria"]
        }
        self.assertEqual(CASES, methods)
        # The criteria did not change, so the reapproved plan inherits the Tester's receipt instead of sending
        # the Tester again to find every test already passing (#2464): one generation job, its results kept.
        self.assertEqual(1, self.stage_order(run).count("acceptance-tests"))
        self.assertEqual({"fails"}, set(view["acceptance_tests"]["results"].values()))
        self.assertEqual("PASS", view["evidence"]["regression_proof"]["verdict"])
        self.assertTrue(view["evidence"]["regression_proof"]["fail_to_pass"])
        if not document:
            self.assertEqual(original_product, (project / "stock.py").read_bytes())
            self.assertEqual(2, self.stage_order(run).count("builder"))
            self.assertNotIn("stuck-investigation", self.stage_order(run))

    def test_blind_tests_cli_runs_tester_isolates_workspace_and_completes(self):
        # With --blind-tests, Tester writes tests before Builder, but files are archived and isolated
        guard = {"C0": "guard: test_receive_adds_units"}
        self.scenario = dataclasses.replace(self.scenario, fake_criteria={**guard, **CASES})
        run, project, view = self.run_to_end("--joint-planning", "--no-adaptive-planning", "--blind-tests")
        self.assertTrue(view["done"], self.details(view))
        order = self.stage_order(run)
        self.assertIn("acceptance-tests", order)
        self.assertLess(order.index("acceptance-tests"), order.index("builder"), order)
        seen = view["acceptance_tests"]
        self.assertTrue(seen["blind"])
        self.assertEqual(["tests/test_stock.py"], seen["files"])
        self.assertEqual(sorted(["C0", *CASES]), sorted(seen["case_ids"]))
        prompts = sorted(
            Path(run.run_dir).glob("iterations/*/builder-*.prompt.md"), key=lambda path: path.stat().st_mtime
        )
        self.assertNotIn("The tests for the cases above are already written", prompts[0].read_text())

    def test_a_case_that_already_works_goes_back_to_plan_review_as_a_guard(self):
        self.scenario = dataclasses.replace(
            self.scenario, fake_criteria={"C0": "test: test_receive_adds_units", **CASES}
        )
        run, project, view = self.run_to_end("--joint-planning", "--no-adaptive-planning", "--spec-first")
        # The runner refuses the passing test; the Tester's repair reports C0 as already working; the runner
        # relabels it guard: and plan review runs again; the user approves the corrected plan; then the build.
        self.assertTrue(view["done"], self.details(view))
        self.assertEqual(2, len(self.plan_approvals), "the corrected plan needs its own approval")
        criteria = {row["id"]: row for row in view["approved_contract"]["body"]["acceptance_criteria"]}
        self.assertEqual("guard: test_receive_adds_units", criteria["C0"]["verification_method"])
        self.assertEqual("passes", view["acceptance_tests"]["results"]["C0"])
        order = self.stage_order(run)
        repair = order.index("acceptance-tests-report-repair")
        self.assertIn("plan-challenge", order[repair:], order)  # plan review runs again on the corrected draft
        self.assertLess(repair, order.index("acceptance-tests"), order)  # then the Tester, on the approved plan
        self.assertLess(order.index("acceptance-tests"), order.index("builder"), order)

    def test_a_builder_that_edits_the_testers_file_goes_again_with_the_file_restored(self):
        run, project, view = self.run_to_end(
            "--joint-planning", "--no-adaptive-planning", "--spec-first", env={"SCENARIO_FAKE_BUILDER_EDITS_TESTS": "1"}
        )
        # The runner puts the file back and sends the Builder straight back, naming what it changed.
        self.assertTrue(view["done"], self.details(view))
        self.assertGreaterEqual(self.stage_order(run).count("builder"), 2, self.stage_order(run))
        reference = (self.scenario.dir / "reference" / "tests" / "test_stock.py").read_text()
        self.assertEqual(reference, (project / "tests" / "test_stock.py").read_text())
        prompts = sorted(
            Path(run.run_dir).glob("iterations/*/builder-*.prompt.md"), key=lambda path: path.stat().st_mtime
        )
        self.assertIn(
            "Your previous attempt changed the Tester's lines in tests/test_stock.py", prompts[-1].read_text()
        )

    def test_without_the_setting_the_builder_writes_the_tests_as_before(self):
        run, project, view = self.run_to_end("--joint-planning", "--no-adaptive-planning")
        self.assertTrue(view["done"], self.details(view))
        self.assertNotIn("acceptance-tests", self.stage_order(run))
        self.assertIsNone(view["acceptance_tests"])


if __name__ == "__main__":
    unittest.main()

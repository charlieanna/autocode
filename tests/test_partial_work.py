"""Stopped Builder source is supplied to the next stage without a model call."""

import hashlib
import importlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_code_checkpoints as checkpoints
import autocode_network_retry as network_retry
import autocode_recovery_context as recovery_context
import autocode_role_launch as role_launch
import autocode_source_scope as source_scope
import autocode_stage_context as stage_context
import autocode_stage_recovery as stage_recovery
import autocode_util as util


class PartialWorkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.test")
        (self.root / "app.py").write_text("committed\n")
        (self.root / "tests").mkdir()
        self.tests = [f"tests/test_{n}.py" for n in range(15)]
        for name in self.tests:
            (self.root / name).write_text("assert True\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        # A launch's baseline is allowed to differ from HEAD.
        (self.root / "app.py").write_text("dirty launch baseline\n")
        self.run = self.root / ".autocode/runs/fixture"
        self.directory = self.run / "iterations/001"
        self.directory.mkdir(parents=True)
        self.record = {
            "role": "builder",
            "stage": "builder",
            "iteration": 1,
            "task_id": "T1",
            "output": str(self.directory / "builder-01.json"),
            "events": str(self.directory / "builder-01.jsonl"),
            "before_ref": str(self.directory / "builder-01.before.json"),
            "contract_revision": 1,
            "contract_hash": "h",
            "timed_out": True,
            "exit_code": -15,
            "processes": [],
        }
        Path(self.record["events"]).write_text('{"type":"thread.started","thread_id":"expired"}\n')
        self.state = {
            "version": 2,
            "workspace": str(self.root),
            "run_dir": str(self.run),
            "status": "RUNNING",
            "phase": "EXECUTING",
            "iteration": 1,
            "sessions": {"builder": "expired"},
            "stages": [],
            "task": "Repair app",
            "settings": {
                "roles": {
                    role: {"model": "fixture", "reasoning_effort": "high"}
                    for role in ("builder", "tester", "task_planner")
                },
                "workflow": {"mode": "standard"},
            },
            "goal_contract": {
                "task_id": "T1",
                "hash": "h",
                "revision": 1,
                "body": {"acceptance_criteria": [{"id": "C1", "criterion": "Repair app"}]},
            },
            "current_task": {
                "id": "T1",
                "kind": "implement",
                "milestone_id": "M1",
                "acceptance_criteria": ["C1"],
                "affected_paths": ["app.py", "tests/"],
            },
            "next_stage": "builder",
        }
        contract = self.state["goal_contract"]
        contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        self.record["contract_hash"] = contract["hash"]
        self.before = source_scope.snapshot(self.root, self.state)
        util.atomic_json(Path(self.record["before_ref"]), self.before)

    @property
    def partial(self):
        # The core regression below deliberately imports no newly added helper,
        # so its counterfactual fails on behavior, not a missing module.
        return importlib.import_module("autocode_partial_work")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.PIPE)

    def admit(self, *, stage="builder", report_only=False, dry_run=False):
        self.record.update(stage=stage, role=stage, report_only=report_only, dry_run=dry_run)
        network_retry.bind_attempt(self.state, self.record, self.before, self.root)
        role_launch.admit_launch(
            self.state,
            self.record,
            self.before,
            self.root,
            self.run,
            native=None,
            native_isolation=None,
            worker_context={},
            command=[],
            environment={},
            child_environment={},
            visual_context=None,
            original_stage=stage,
            report_only=report_only,
            joint_stage=False,
            fallback_route=None,
            retry_authorization=None,
            controller=None,
        )

    def change_source(self):
        (self.root / "app.py").write_text("retained implementation\n")
        for name in self.tests:
            (self.root / name).unlink()
        (self.root / "new.py").write_text("retained addition\n")

    def timeout(self):
        self.state["active_stage"] = self.record
        self.assertTrue(
            stage_recovery.automatically_recover_timed_out_stage(
                self.state, self.run, self.root, util.Paused("PAUSED_PROVIDER_TIMEOUT", "stage exceeded 3600s")
            )
        )
        return self.state["recovery_context"]

    def intervening_review(self):
        review = {
            "stage": "review_completion",
            "iteration": 2,
            "output": str(self.run / "iterations/002/completion-review-01.json"),
        }
        self.state["stages"].append(review)
        recovery_context.stage_saved(self.state, review)
        self.assertNotIn("recovery_context", self.state)

    def test_partial_delta_reaches_the_builder_after_the_reviewer_retires_recovery_context(self):
        self.admit()
        self.change_source()
        original = self.timeout()["partial_work"]
        self.intervening_review()
        prompt, _ = stage_context.context_packet(self.state, "builder", self.run / "state.json")
        context = json.loads(prompt.split("\nCURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(original, context["retained_partial_work"])
        self.assertIn("-dirty launch baseline", context["retained_partial_work"]["patch"])

    def test_a_direct_builder_retry_gets_one_inline_copy_of_the_partial_packet(self):
        self.admit()
        self.change_source()
        original = self.timeout()["partial_work"]
        prompt, _ = stage_context.context_packet(self.state, "builder", self.run / "state.json")
        context = json.loads(prompt.split("\nCURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(original, context["recovery_context"]["partial_work"])
        self.assertNotIn("retained_partial_work", context)

    def test_a_later_builder_or_changed_source_or_contract_does_not_borrow_the_packet(self):
        self.admit()
        self.change_source()
        original = self.timeout()["partial_work"]
        revision = original["source_revision"]
        self.assertIsNotNone(self.partial.pending(self.state, revision))
        self.assertIsNone(self.partial.pending(self.state, "different-source"))
        self.state["goal_contract"]["hash"] = "different-contract"
        self.assertIsNone(self.partial.pending(self.state, revision))
        self.state["goal_contract"]["hash"] = original["contract_hash"]
        self.state["stages"].append(
            {"stage": "builder", "iteration": 2, "output": str(self.directory / "builder-02.json")}
        )
        self.assertIsNone(self.partial.pending(self.state, revision))

    def test_completed_or_different_milestones_do_not_reuse_unverified_partial_work(self):
        self.admit()
        self.change_source()
        original = self.timeout()["partial_work"]
        revision = original["source_revision"]
        self.assertEqual(["M1"], original["milestone_ids"])
        self.state["current_task"]["milestone_id"] = "M2"
        self.assertIsNone(self.partial.pending(self.state, revision))
        self.state["current_task"]["milestone_id"] = "M1"
        self.state["milestone_progress"] = {
            "M1": {"id": "M1", "accepted": True, "contract_hash": original["contract_hash"]}
        }
        self.assertIsNone(self.partial.pending(self.state, revision))

    def test_flat_task_packet_stays_with_its_exact_task(self):
        self.admit()
        self.change_source()
        original = self.timeout()["partial_work"]
        original["milestone_ids"] = []
        self.state["current_task"].pop("milestone_id")
        self.assertIsNotNone(self.partial.pending(self.state, original["source_revision"]))
        for task_id in ("another-task", None):
            self.state["current_task"]["id"] = task_id
            self.assertIsNone(self.partial.pending(self.state, original["source_revision"]))

    def test_another_timeout_preserves_the_first_dirty_baseline_cumulatively(self):
        self.admit()
        self.change_source()
        first = self.timeout()["partial_work"]
        self.intervening_review()
        self.before = source_scope.snapshot(self.root, self.state)
        directory = self.run / "iterations/002"
        directory.mkdir(parents=True, exist_ok=True)
        self.record = {
            "role": "builder",
            "stage": "builder",
            "iteration": 2,
            "task_id": "T1",
            "output": str(directory / "builder-01.json"),
            "events": str(directory / "builder-01.jsonl"),
            "before_ref": str(directory / "builder-01.before.json"),
            "contract_revision": 1,
            "contract_hash": self.state["goal_contract"]["hash"],
            "timed_out": True,
            "exit_code": -15,
            "processes": [],
        }
        Path(self.record["events"]).write_text('{"type":"thread.started","thread_id":"expired-again"}\n')
        util.atomic_json(Path(self.record["before_ref"]), self.before)
        self.admit()
        (self.root / "app.py").write_text("second retained implementation\n")
        second = self.timeout()["partial_work"]
        self.assertEqual(first["before_commit"], second["before_commit"])
        self.assertIn("unfinished Builder", second["base_reason"])
        self.assertIn("-dirty launch baseline", second["patch"])
        self.assertIn("+second retained implementation", second["patch"])
        self.assertEqual(set(self.tests), set(second["deleted_files"]))

    def test_incomplete_old_history_does_not_fabricate_a_pending_delta(self):
        self.assertIsNone(self.partial.pending(self.state, self.before["revision"]))
        self.state["stages"] = [{"stage": "builder"}]
        self.assertIsNone(self.partial.pending(self.state, self.before["revision"]))

    def test_timeout_recovery_supplies_exact_partial_patch_and_deletions_to_next_stage(self):
        head = self.git("rev-parse", "HEAD")
        index = (self.root / ".git/index").read_bytes()
        self.admit()
        self.change_source()
        self.state["active_stage"] = self.record
        self.assertTrue(
            stage_recovery.automatically_recover_timed_out_stage(
                self.state, self.run, self.root, util.Paused("PAUSED_PROVIDER_TIMEOUT", "stage exceeded 3600s")
            )
        )
        prompt, _ = stage_context.context_packet(self.state, self.state["next_stage"], self.run / "state.json")
        context = json.loads(prompt.split("\nCURRENT HANDOFF DATA\n", 1)[1])
        recovery = context["recovery_context"]
        self.assertIn("partial_work", recovery)
        packet = recovery["partial_work"]
        self.assertTrue(packet["available"], packet)
        self.assertEqual("unverified", packet["verification"])
        self.assertIn("exact tree", packet["base_reason"])
        self.assertEqual(set(["app.py", "new.py", *self.tests]), set(packet["changed_files"]))
        self.assertEqual(source_scope.snapshot(self.root, self.state)["revision"], packet["source_revision"])
        self.assertEqual(self.tests, sorted(packet["deleted_files"], key=lambda name: int(Path(name).stem[5:])))
        self.assertIn("-dirty launch baseline", packet["patch"])
        self.assertIn("+retained implementation", packet["patch"])
        self.assertIn("+retained addition", packet["patch"])
        self.assertIn("deleted file mode", packet["patch"])
        self.assertFalse(packet["patch_truncated"])
        self.assertEqual(len(packet["patch"].encode()), packet["patch_bytes"])
        self.assertEqual(hashlib.sha256(packet["patch"].encode()).hexdigest(), packet["patch_sha256"])
        self.assertIn("repair it rather than restart", recovery["instruction"])
        self.assertEqual(head, self.git("rev-parse", "HEAD"))
        self.assertEqual(index, (self.root / ".git/index").read_bytes())
        self.assertEqual("retained implementation\n", (self.root / "app.py").read_text())
        self.assertEqual(b"retained implementation\n", self.git("show", packet["retained_commit"] + ":app.py"))
        for kind, key in (("start", "before_commit"), ("retained", "retained_commit")):
            self.assertEqual(
                packet[key], self.git("rev-parse", f"refs/autocode/partial-work/{kind}/{packet[key]}").decode().strip()
            )

    def test_a_person_stop_returns_to_the_builder_with_its_partial_work_even_after_a_validation(self):
        # #3334: "Resume runs it again" meant the Completion Reviewer, or the Tester when an earlier
        # validation was retired; a person's stop is not a failure, so the stopped Builder continues.
        self.admit()
        self.change_source()
        self.state["validation"] = {"verdict": "FAIL"}
        self.record.update(timeout_kind="person", timeout_reason="Stopped by you")
        recovery = self.timeout()
        self.assertEqual(("builder", "EXECUTING"), (self.state["next_stage"], self.state["phase"]))
        self.assertEqual("builder", recovery["next_stage"])
        self.assertTrue(recovery["partial_work"]["available"], recovery["partial_work"])
        self.assertIn("The person stopped this request", recovery["instruction"])
        self.assertIn("repair it rather than restart", recovery["instruction"])
        self.assertNotIn("validation", self.state)

    def test_inline_patch_is_bounded_and_keeps_the_complete_retained_tree(self):
        self.admit()
        (self.root / "app.py").write_text("new content\n" * 10000)
        after = source_scope.snapshot(self.root, self.state)
        packet = self.partial.recovery(self.record, self.before, after, self.root, self.run)
        self.assertTrue(packet["available"], packet)
        self.assertTrue(packet["patch_truncated"])
        self.assertLessEqual(len(packet["patch"].encode()), self.partial.MAX_PATCH_BYTES)
        self.assertGreater(packet["patch_bytes"], self.partial.MAX_PATCH_BYTES)
        self.assertEqual((self.root / "app.py").read_bytes(), self.git("show", packet["retained_commit"] + ":app.py"))

    def test_capture_unavailability_is_recorded_without_changing_source(self):
        for error in (ValueError("source moved during capture"), TypeError("unsupported source tree")):
            with self.subTest(error=type(error).__name__):
                with patch.object(checkpoints, "capture_tree", side_effect=error):
                    self.admit()
                packet = self.partial.recovery(self.record, self.before, self.before, self.root, self.run)
                self.assertFalse(packet["available"])
                self.assertEqual(str(error), packet["reason"])
                self.assertEqual("dirty launch baseline\n", (self.root / "app.py").read_text())

    def test_legacy_attempt_does_not_substitute_head_for_missing_original_bytes(self):
        packet = self.partial.recovery(self.record, self.before, self.before, self.root, self.run)
        self.assertEqual({"available": False, "reason": "No launch tree was captured"}, packet)

    def test_capture_supports_run_directory_names_with_spaces(self):
        directory = self.run.parent / "run with spaces"
        directory.mkdir()
        self.partial.capture_start(self.record, self.before, self.root, directory)
        packet = self.partial.recovery(self.record, self.before, self.before, self.root, directory)
        self.assertTrue(packet["available"], packet)
        self.assertEqual("", packet["patch"])

    def test_wrong_launch_tree_is_refused_without_showing_an_unrelated_patch(self):
        self.record["partial_work_start"] = {
            "source_revision": self.before["revision"],
            "commit": self.git("rev-parse", "HEAD").decode().strip(),
        }
        packet = self.partial.recovery(self.record, self.before, self.before, self.root, self.run)
        self.assertFalse(packet["available"])
        self.assertIn("does not match", packet["reason"])
        self.assertNotIn("patch", packet)

    def test_capture_race_during_recovery_preserves_an_explicit_evidence_gap(self):
        self.admit()
        with patch.object(checkpoints, "capture_tree", side_effect=ValueError("source moved after timeout")):
            packet = self.partial.recovery(self.record, self.before, self.before, self.root, self.run)
        self.assertEqual({"available": False, "reason": "source moved after timeout"}, packet)

    def test_git_diff_failure_is_an_evidence_gap_and_never_a_passing_packet(self):
        self.admit()
        failing = SimpleNamespace(
            run=lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr=b"diff failed"),
            PIPE=subprocess.PIPE,
            SubprocessError=subprocess.SubprocessError,
        )
        with patch.object(self.partial, "subprocess", failing):
            packet = self.partial.recovery(self.record, self.before, self.before, self.root, self.run)
        self.assertEqual({"available": False, "reason": "diff failed"}, packet)

    def test_read_only_report_repair_and_dry_run_admissions_do_not_capture_source(self):
        for options in ({"stage": "tester"}, {"report_only": True}, {"dry_run": True}):
            with self.subTest(options=options):
                self.record.pop("partial_work_start", None)
                self.admit(**options)
                self.assertNotIn("partial_work_start", self.record)

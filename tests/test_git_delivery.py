"""Controller-owned atomic local delivery, using real Git and no models."""

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_completion_result as result
import autocode_contract_identity as identity
import autocode_git_delivery as delivery
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_source_scope as scope
import autocode_util as util
from goal_fixtures import body


class DeliveryPolicyTests(unittest.TestCase):
    def test_only_an_explicit_structured_requirement_can_authorize_a_commit(self):
        self.assertIsNone(delivery.requirement({}))
        spec = {"kind": "atomic_commit", "message": "Add JSON export"}
        self.assertEqual(spec, delivery.requirement({"git_delivery": spec}))
        for invalid in ({"kind": "push", "message": "x"}, {"kind": "atomic_commit", "message": ""}):
            with self.assertRaises(ValueError):
                delivery.requirement({"git_delivery": invalid})
        with self.assertRaisesRegex(ValueError, "structured"):
            delivery.requirement({"technical_approach": ["Create one atomic commit containing production and tests."]})
        self.assertIsNone(delivery.requirement({"technical_approach": ["Do not create an atomic commit."]}))

    def test_strict_provider_can_explicitly_request_no_git_delivery(self):
        schema = util.model_output_schema(goals.BODY_SCHEMA)["properties"]["git_delivery"]
        util.validate_schema(None, schema)
        self.assertIsNone(delivery.requirement({"git_delivery": None}))
        self.assertIn("null", schema["type"])

    def test_null_delivery_renders_like_an_ordinary_plan_without_delivery(self):
        plan = body()
        state = {
            "status": "AWAITING_GOAL_APPROVAL",
            "goal_contract": {"revision": 1, "hash": "hash", "body": plan, "approval_status": "pending"},
        }
        expected = lifecycle.render(state)
        plan["git_delivery"] = None
        self.assertEqual(expected, lifecycle.render(state))

    def test_plan_schema_and_display_preserve_delivery_authority(self):
        plan = body()
        plan["git_delivery"] = {"kind": "atomic_commit", "message": "Add JSON export"}
        util.validate_schema(plan, goals.BODY_SCHEMA)
        shown = lifecycle.render(
            {
                "status": "AWAITING_GOAL_APPROVAL",
                "goal_contract": {"revision": 1, "hash": "hash", "body": plan, "approval_status": "pending"},
            }
        )
        self.assertIn("Git delivery", shown)
        self.assertIn("Add JSON export", shown)


class DeliveryReviewTests(unittest.TestCase):
    def test_checked_candidate_is_committed_before_independent_review_and_proof_is_refreshed(self):
        state = {"goal_contract": {"body": {"git_delivery": {"kind": "atomic_commit", "message": "Deliver"}}}}
        order = []
        with (
            patch("autocode_regression.before_review", side_effect=lambda *a: order.append("proof")) as proof,
            patch("autocode_regression.required", return_value=True),
            patch("autocode_regression.complete", return_value=True),
            patch("autocode_source_scope.snapshot", return_value={"revision": "checked"}),
            patch("autocode_git_delivery.prepare", side_effect=lambda *a: order.append("commit") or True) as commit,
        ):
            result.before_review(state, "sol", Path("/workspace"), Path("/run"))
        self.assertEqual(["proof", "commit", "proof"], order)
        commit.assert_called_once()
        self.assertEqual(2, proof.call_count, "A commit changes source identity and needs a new runner proof")

    def test_failing_or_absent_regression_proof_cannot_trigger_early_delivery(self):
        state = {"goal_contract": {"body": {"git_delivery": {"kind": "atomic_commit", "message": "Deliver"}}}}
        for required, passed in ((True, False), (False, True)):
            with (
                self.subTest(required=required, passed=passed),
                patch("autocode_regression.before_review"),
                patch("autocode_regression.required", return_value=required),
                patch("autocode_regression.complete", return_value=passed),
                patch("autocode_source_scope.snapshot", return_value={"revision": "checked"}),
                patch("autocode_git_delivery.prepare") as commit,
            ):
                result.before_review(state, "sol", Path("/workspace"), Path("/run"))
                commit.assert_not_called()


class AtomicDeliveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.test")
        (self.root / ".gitignore").write_text(".autocode/\n")
        (self.root / "app.py").write_text("old\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.parent = self.git("rev-parse", "HEAD")
        self.run = self.root / ".autocode/runs/test"
        self.run.mkdir(parents=True)
        plan = body()
        plan["git_delivery"] = {"kind": "atomic_commit", "message": "Deliver the feature"}
        plan["milestones"] = [
            {
                "id": "M1",
                "objective": "Feature",
                "acceptance_criteria": ["C1"],
                "affected_paths": ["app.py", "test_app.py"],
            }
        ]
        contract = {"task_id": "task", "revision": 1, "body": plan}
        contract["hash"] = util.digest(contract)
        event = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(contract)}
        contract.update(approval_status="approved", approval_event=event, origin="astra_discovery")
        self.state = {
            "version": 3,
            "task_id": "task",
            "workspace": str(self.root),
            "run_dir": str(self.run),
            "base_commit": self.parent,
            "goal_contract": contract,
            "user_events": [event],
            "workflow": {"kind": "build"},
            "settings": {},
            "status": "RUNNING",
            "iteration": 1,
        }
        (self.root / "app.py").write_text("new\n")
        (self.root / "test_app.py").write_text("tests\n")
        self.current = scope.snapshot(self.root, self.state)

    def git(self, *args):
        proc = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, check=True)
        return proc.stdout.strip()

    def test_delivery_is_one_atomic_commit_and_requires_fresh_validation(self):
        self.assertFalse(delivery.ready(self.state, self.current))
        self.assertTrue(delivery.prepare(self.state, self.root, self.current))
        after = scope.snapshot(self.root, self.state)
        self.assertNotEqual(self.current["revision"], after["revision"])
        self.assertEqual("", self.git("status", "--porcelain"))
        self.assertEqual("1", self.git("rev-list", "--count", self.parent + "..HEAD"))
        self.assertEqual(
            {"app.py", "test_app.py"},
            set(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()),
        )
        self.assertTrue(delivery.ready(self.state, after))
        self.state["validation"] = {"source_revision": after["revision"]}
        self.assertFalse(delivery.prepare(self.state, self.root, after))
        self.assertEqual("1", self.git("rev-list", "--count", self.parent + "..HEAD"))
        (self.root / "app.py").write_text("changed after delivery\n")
        self.assertFalse(delivery.ready(self.state, scope.snapshot(self.root, self.state)))
        with self.assertRaisesRegex(ValueError, "changed"):
            delivery.prepare(self.state, self.root, scope.snapshot(self.root, self.state))

    def test_the_bugfix_workflow_delivers_like_the_ordinary_build_workflow(self):
        self.state["workflow"] = {"kind": "bugfix"}
        self.assertTrue(delivery.prepare(self.state, self.root, self.current))
        after = scope.snapshot(self.root, self.state)
        self.assertTrue(delivery.ready(self.state, after))
        self.assertEqual("1", self.git("rev-list", "--count", self.parent + "..HEAD"))

    def test_the_other_workflows_stay_refused(self):
        for kind in ("review", "design", "discuss"):
            with self.subTest(kind=kind):
                self.state["workflow"] = {"kind": kind}
                with self.assertRaisesRegex(ValueError, "Atomic Git delivery currently requires"):
                    delivery.prepare(self.state, self.root, self.current)
                self.assertEqual(self.parent, self.git("rev-parse", "HEAD"))

    def test_preexisting_or_staged_or_out_of_scope_work_is_preserved_and_refused(self):
        for kind in ("initial_dirty", "staged", "outside", "unapproved"):
            with self.subTest(kind=kind):
                saved = copy.deepcopy(self.state)
                if kind == "initial_dirty":
                    self.state["base_commit"] = self.git(
                        "commit-tree", "HEAD^{tree}", "-p", self.parent, "-m", "launch snapshot"
                    )
                elif kind == "staged":
                    self.git("add", "app.py")
                elif kind == "outside":
                    (self.root / "other.txt").write_text("unrelated\n")
                else:
                    self.state["user_events"] = []
                before = self.git("status", "--porcelain")
                with self.assertRaises(ValueError):
                    delivery.prepare(self.state, self.root, scope.snapshot(self.root, self.state))
                self.assertEqual(before, self.git("status", "--porcelain"))
                self.assertEqual(self.parent, self.git("rev-parse", "HEAD"))
                self.state = saved
                self.git("restore", "--staged", ".")
                (self.root / "other.txt").unlink(missing_ok=True)

    def test_a_failing_existing_hook_blocks_delivery_and_keeps_source_and_index(self):
        hook = self.root / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        before = self.git("status", "--porcelain")
        with self.assertRaises(ValueError):
            delivery.prepare(self.state, self.root, self.current)
        self.assertEqual(before, self.git("status", "--porcelain"))
        self.assertEqual(self.parent, self.git("rev-parse", "HEAD"))
        self.assertFalse(delivery.ready(self.state, self.current))
        self.assertFalse((self.root / ".git/index.lock").exists())

    def test_changed_hook_source_or_receipt_never_authenticates_completion(self):
        hook = self.root / ".git/hooks/post-commit"
        hook.write_text("#!/bin/sh\nprintf 'hook changed source\\n' > app.py\n")
        hook.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "hook"):
            delivery.prepare(self.state, self.root, self.current)
        self.assertFalse(delivery.ready(self.state, scope.snapshot(self.root, self.state)))
        self.assertIn("hook changed source", (self.root / "app.py").read_text())
        receipt = self.run / "git-delivery.json"
        receipt.write_text("{}")
        self.assertFalse(delivery.ready(self.state, scope.snapshot(self.root, self.state)))

    def test_another_git_writer_lock_is_preserved(self):
        lock = self.root / ".git/index.lock"
        lock.write_text("other writer")
        with self.assertRaises(OSError):
            delivery.prepare(self.state, self.root, self.current)
        self.assertEqual("other writer", lock.read_text())
        self.assertEqual(self.parent, self.git("rev-parse", "HEAD"))

    def test_crash_after_commit_recovers_receipt_without_another_commit(self):
        delivery.prepare(self.state, self.root, self.current)
        head = self.git("rev-parse", "HEAD")
        self.state.pop("git_delivery")
        self.state["validation"] = None
        self.assertTrue(delivery.prepare(self.state, self.root, scope.snapshot(self.root, self.state)))
        self.assertEqual(head, self.git("rev-parse", "HEAD"))
        self.assertTrue(delivery.ready(self.state, scope.snapshot(self.root, self.state)))

    def test_terminal_application_routes_to_validation_after_delivery_and_never_claims_done_early(self):
        callbacks = {
            name: Mock(return_value=False)
            for name in (
                "logical_completion_ready",
                "regression_required",
                "require_visual_completion",
                "prepare_progressive_completion",
                "blocking_entries",
                "ledger_rejection",
                "missing_human_reviews",
                "regression_rejection",
                "completion_rejection",
                "milestones_enabled",
                "accept_milestone",
                "record_decision",
                "save_record",
            )
        }
        callbacks.update(
            native_calls=lambda: None,
            completion_ready=lambda *_: True,
            regression_complete=lambda *a, **k: True,
            commit_fields=self.state.update,
            now=lambda: "now",
            paused=util.Paused,
        )
        handled = result.apply(
            self.state, self.current, {}, {"status": "COMPLETE"}, self.root, modern=True, **callbacks
        )
        self.assertTrue(handled)
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual("sol", self.state["next_stage"])
        self.assertNotIn("completed_at", self.state)
        self.assertNotIn("final_decision", self.state)
        callbacks["save_record"].assert_called_once()

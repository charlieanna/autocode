"""A changed source inventory returns to Requirements without adopting old approval."""

import copy
import json
import os
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_catalog_context as context
import autocode_catalog_reconciliation as reconciliation
import autocode_client_actions as client
import autocode_coordinate as coordinate
import autocode_coordination_store as coordination_store
import autocode_goals as goals
import autocode_repo_catalog as catalog
import autocode_run_view as run_view
import autocode_status_observation as status_observation
import autocode_util as util

from tests import test_catalog_context as catalog_fixtures
from tests import test_json_output as cli_fixtures


class CatalogReconciliationTests(unittest.TestCase):
    setUp = catalog_fixtures.CatalogContextTests.setUp

    def changed_state(self):
        other = self.repo.parent / "worker"
        other.mkdir()
        (other / "go.mod").write_text("module example.test/worker\n")
        for args in (
            ["init", "-q"],
            ["add", "."],
            ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "base"],
        ):
            subprocess.run(["git", "-C", str(other), *args], check=True, capture_output=True)
        catalog.add_root(self.repo.parent)
        initial = context.context(self.state)
        self.assertEqual(2, len(initial["repositories"]))
        receipt = Path(self.state["run_dir"]) / "planning/source-catalog.json"
        original = receipt.read_bytes()
        (self.repo / "go.mod").write_text("module example.test/api\n// a later revision\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "-qm",
                "next",
            ],
            check=True,
            capture_output=True,
        )
        with self.assertRaises(context.CatalogChanged) as error:
            context.context(self.state)
        self.state.update(
            status="PAUSED_SOURCE_UNAVAILABLE",
            stop_reason=str(error.exception),
            next_stage="draft_plan",
            settings={"roles": {"requirements": {}}, "limits": {"max_seconds": 123}},
            planning={"plan_review_calls": 2, "reports": {"draft": "old"}, "final_token": "old"},
            goal_contract={"task_id": "task", "revision": 1, "approval_status": "approved", "hash": "old"},
            requirements_handoff={"output": "old-report"},
            stages=[{"stage": "requirements_gather"}],
            user_events=[{"kind": "goal_approval", "actor": "user_cli", "token": "old"}],
        )
        return initial, receipt, original

    def test_named_metadata_action_preserves_receipt_counts_and_revokes_approval(self):
        _, receipt, original = self.changed_state()
        before = copy.deepcopy(self.state)
        controller = {"kind": "ended"}
        offer = reconciliation.offer(self.state, controller=controller)
        self.assertIn("--reconcile-only catalog-v1:", offer["action"])
        self.assertEqual(before, self.state)
        with patch("autocode_source_reconciliation.readable", return_value=False):
            view = run_view.view(self.state, controller=controller, run_dir=self.state["run_dir"], workspace=self.repo)
        self.assertEqual("continue", view["needs"]["kind"])
        self.assertIn("--reconcile-only", view["next_command"])
        runner = SimpleNamespace(goals=goals, write_json=util.atomic_json)
        reconciliation.reconcile(runner, self.state, self.state["run_dir"], offer["action"].split()[-1])
        self.assertEqual(original, receipt.read_bytes())
        self.assertEqual("requirements_gather", self.state["next_stage"])
        self.assertEqual("RUNNING", self.state["status"])
        self.assertNotIn("goal_contract", self.state)
        self.assertEqual(before["goal_contract"], self.state["contract_history"][-1])
        self.assertEqual(2, self.state["planning"]["plan_review_calls"])
        self.assertEqual(before["settings"], self.state["settings"])
        self.assertEqual(before["stages"], self.state["stages"])
        self.assertEqual(before["requirements_handoff"], self.state["requirements_history"][-1])
        self.assertEqual("source_catalog_reconciled", self.state["user_events"][-1]["kind"])
        self.assertTrue(any(str(self.repo) in change for change in self.state["user_events"][-1]["changes"]))
        self.assertFalse(any("worker" in change for change in self.state["user_events"][-1]["changes"]))
        self.assertEqual(context.saved_inventory(self.state), context.context(self.state))
        self.assertEqual(self.state["source_catalog_receipt"], run_view.view(self.state)["source_catalog_receipt"])
        self.assertIsNone(reconciliation.offer(self.state, controller=controller))

    def test_catalog_status_timeout_holds_then_a_fresh_observation_offers_reconciliation(self):
        self.changed_state()
        controller = {"kind": "ended"}
        with (
            patch("autocode_source_reconciliation.readable", return_value=False),
            patch.object(status_observation.captured, "run", side_effect=subprocess.TimeoutExpired("worker", 15)),
        ):
            blocked = run_view.view(
                self.state, controller=controller, run_dir=self.state["run_dir"], workspace=self.repo
            )
        self.assertIsNone(blocked["next_command"])
        with patch("autocode_source_reconciliation.readable", return_value=False):
            recovered = run_view.view(
                self.state, controller=controller, run_dir=self.state["run_dir"], workspace=self.repo
            )
        self.assertIn("--reconcile-only", recovered["next_command"])

    def test_coordinator_uses_the_public_anchor_inventory_binding(self):
        selected = "a" * 64
        document = {
            "anchor": str(self.root / "run"),
            "anchor_turn": "turn",
            "task": "task",
            "source_catalog_receipt": selected,
            "members": [],
            "initial_scope": [
                {"target": "worker", "work": "fix", "source_quote": "fix worker", "evidence": "catalog/id/go.mod:1"}
            ],
        }
        citation = {"repository": str(self.repo), "revision": "head", "file": "go.mod:1"}
        with patch.object(context, "evidence", return_value=citation) as evidence:
            result = coordinate.request_evidence(document, {}, "worker")
        evidence.assert_called_once_with(
            {"run_dir": document["anchor"], "source_catalog_receipt": selected}, "catalog/id/go.mod:1"
        )
        self.assertEqual("head", result["evidence_revision"])
        with patch.object(coordinate, "anchor_view", return_value={"source_catalog_receipt": "b" * 64}):
            with self.assertRaisesRegex(coordination_store.CoordinationError, "source inventory changed"):
                coordinate.observe(document)

    def test_stale_token_and_uncertain_work_refuse_without_changing_evidence(self):
        _, receipt, original = self.changed_state()
        token = reconciliation.offer(self.state, controller={"kind": "ended"})["action"].split()[-1]
        before = copy.deepcopy(self.state)
        for key in ("active_stage", "active_runner_check", "uncertain_artifacts", "pending_questions", "user_request"):
            changed = copy.deepcopy(before)
            changed[key] = {"pending": True}
            with self.subTest(key=key), self.assertRaises(ValueError):
                reconciliation.reconcile(
                    SimpleNamespace(goals=goals, write_json=Mock()), changed, changed["run_dir"], token
                )
            self.assertEqual(original, receipt.read_bytes())
        with self.assertRaises(ValueError):
            reconciliation.reconcile(
                SimpleNamespace(goals=goals, write_json=Mock()), self.state, self.state["run_dir"], token + "x"
            )
        self.assertEqual(before, self.state)

    def test_inventory_that_moves_after_the_offer_refuses_the_old_token(self):
        _, receipt, original = self.changed_state()
        token = reconciliation.offer(self.state, controller={"kind": "ended"})["action"].split()[-1]
        (self.repo / "go.mod").write_text("module example.test/api\n// changed again\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "-qm",
                "changed again",
            ],
            check=True,
            capture_output=True,
        )
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            reconciliation.reconcile(
                SimpleNamespace(goals=goals, write_json=Mock()), self.state, self.state["run_dir"], token
            )
        self.assertEqual(before, self.state)
        self.assertEqual(original, receipt.read_bytes())

    def test_crash_before_state_commit_leaves_only_unselected_immutable_receipt(self):
        _, receipt, original = self.changed_state()
        before = copy.deepcopy(self.state)
        token = reconciliation.offer(self.state, controller={"kind": "ended"})["action"].split()[-1]
        runner = SimpleNamespace(goals=goals, write_json=Mock(side_effect=OSError("failed state commit")))
        with self.assertRaises(OSError):
            reconciliation.reconcile(runner, self.state, self.state["run_dir"], token)
        self.assertEqual(before, self.state)
        self.assertEqual(original, receipt.read_bytes())
        self.assertEqual(1, len(list((receipt.parent / "source-catalogs").glob("*.json"))))

    def test_crash_after_state_commit_cannot_adopt_twice(self):
        _, receipt, original = self.changed_state()
        token = reconciliation.offer(self.state, controller={"kind": "ended"})["action"].split()[-1]

        def write_then_fail(path, state):
            util.atomic_json(path, state)
            raise OSError("state commit response lost")

        runner = SimpleNamespace(goals=goals, write_json=write_then_fail)
        with self.assertRaises(OSError):
            reconciliation.reconcile(runner, self.state, self.state["run_dir"], token)
        persisted = util.read_object(Path(self.state["run_dir"]) / "state.json")
        self.assertEqual("requirements_gather", persisted["next_stage"])
        self.assertEqual(original, receipt.read_bytes())
        self.assertIsNone(reconciliation.offer(persisted, controller={"kind": "ended"}))
        self.assertEqual(1, sum(row.get("kind") == "source_catalog_reconciled" for row in persisted["user_events"]))

    def test_inventory_changed_during_locked_reconciliation_is_not_committed(self):
        _, receipt, original = self.changed_state()
        token = reconciliation.offer(self.state, controller={"kind": "ended"})["action"].split()[-1]
        observed = catalog.discover()
        moved = copy.deepcopy(observed)
        moved["source_snapshot"] = "changed again during commit"
        runner = SimpleNamespace(goals=goals, write_json=Mock())
        before = copy.deepcopy(self.state)
        with patch.object(catalog, "discover", side_effect=[observed, moved]):
            with self.assertRaisesRegex(ValueError, "refresh status"):
                reconciliation.reconcile(runner, self.state, self.state["run_dir"], token)
        runner.write_json.assert_not_called()
        self.assertEqual(before, self.state)
        self.assertEqual(original, receipt.read_bytes())

    def test_mutated_receipt_never_silently_falls_back_to_original(self):
        self.changed_state()
        token = reconciliation.offer(self.state, controller={"kind": "ended"})["action"].split()[-1]
        reconciliation.reconcile(
            SimpleNamespace(goals=goals, write_json=util.atomic_json), self.state, self.state["run_dir"], token
        )
        selected = context.receipt_path(self.state)
        selected.write_text('{"repositories": []}')
        with self.assertRaisesRegex(ValueError, "identity"):
            context.saved_inventory(self.state)

    def test_symlinked_inventory_directory_cannot_write_outside_the_run(self):
        _, receipt, original = self.changed_state()
        token = reconciliation.offer(self.state, controller={"kind": "ended"})["action"].split()[-1]
        external = self.root / "outside"
        external.mkdir()
        (receipt.parent / "source-catalogs").symlink_to(external, target_is_directory=True)
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "owned by this run"):
            reconciliation.reconcile(
                SimpleNamespace(goals=goals, write_json=Mock()), self.state, self.state["run_dir"], token
            )
        self.assertEqual([], list(external.iterdir()))
        self.assertEqual(before, self.state)
        self.assertEqual(original, receipt.read_bytes())

    def test_symlinked_selected_receipt_cannot_be_read(self):
        self.changed_state()
        token = reconciliation.offer(self.state, controller={"kind": "ended"})["action"].split()[-1]
        reconciliation.reconcile(
            SimpleNamespace(goals=goals, write_json=util.atomic_json), self.state, self.state["run_dir"], token
        )
        selected = context.receipt_path(self.state)
        external = self.root / "outside.json"
        external.write_bytes(selected.read_bytes())
        selected.unlink()
        selected.symlink_to(external)
        with self.assertRaisesRegex(ValueError, "owned by this run"):
            context.saved_inventory(self.state)

    def test_removed_catalog_is_recorded_as_an_empty_inventory(self):
        _, receipt, original = self.changed_state()
        catalog.remove_root(self.repo.parent)
        token = reconciliation.offer(self.state, controller={"kind": "ended"})["action"].split()[-1]
        reconciliation.reconcile(
            SimpleNamespace(goals=goals, write_json=util.atomic_json), self.state, self.state["run_dir"], token
        )
        self.assertEqual(original, receipt.read_bytes())
        self.assertEqual([], context.saved_inventory(self.state)["repositories"])
        self.assertIsNone(context.context(self.state))

    def test_saved_v2_planning_stages_return_to_v2_requirements(self):
        self.changed_state()
        before = copy.deepcopy(self.state)
        for stage in ("requirements", "plan", "plan_review", "plan_revise", "plan_finalize"):
            with self.subTest(stage=stage):
                current = copy.deepcopy(before)
                current["settings"]["planning_flow"] = "v2"
                current["next_stage"] = stage
                offer = reconciliation.offer(current, controller={"kind": "ended"})
                self.assertIsNotNone(offer)
                token = offer["action"].split()[-1]
                reconciliation.reconcile(
                    SimpleNamespace(goals=goals, write_json=util.atomic_json), current, current["run_dir"], token
                )
                self.assertEqual("requirements", current["next_stage"])
                self.assertEqual(2, current["planning"]["plan_review_calls"])
                self.assertNotIn("goal_contract", current)

    def test_acknowledged_pause_does_not_block_but_pending_pause_or_stop_does(self):
        self.changed_state()
        current = copy.deepcopy(self.state)
        current["pause_intent"] = {"acknowledged_at": "2026-11-20T00:00:00Z"}
        offer = reconciliation.offer(current, controller={"kind": "ended"})
        self.assertIsNotNone(offer)
        token = offer["action"].split()[-1]
        for pause in ({"requested_at": "earlier"}, "malformed"):
            changed = copy.deepcopy(current)
            changed["pause_intent"] = pause
            self.assertIsNone(reconciliation.offer(changed, controller={"kind": "ended"}))
            with self.assertRaises(ValueError):
                reconciliation.require_binding(changed, token)
        stopped = copy.deepcopy(current)
        stopped["applied_interventions"] = [{"kind": "stop"}]
        self.assertIsNone(reconciliation.offer(stopped, controller={"kind": "ended"}))
        self.assertIsNone(reconciliation.offer(current, controller={"kind": "ended"}, stop_pending=True))
        reconciliation.reconcile(
            SimpleNamespace(goals=goals, write_json=util.atomic_json), current, current["run_dir"], token
        )
        self.assertEqual("requirements_gather", current["next_stage"])


class CatalogReconciliationCliTests(unittest.TestCase):
    setUp = cli_fixtures.JsonActionCliTests.setUp
    cli = cli_fixtures.JsonActionCliTests.cli
    invoke = cli_fixtures.JsonActionCliTests.invoke

    def test_public_control_is_metadata_only_and_plain_resume_cannot_adopt(self):
        repo = self.root / "sources" / "api"
        repo.mkdir(parents=True)
        (repo / "go.mod").write_text("module example.test/api\n")
        for args in (
            ["init", "-q"],
            ["add", "."],
            ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "base"],
        ):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        catalog.add_root(repo.parent)
        self.state["run_dir"] = str(self.run_dir)
        self.state["settings"]["roles"]["requirements"] = {"model": "fixture"}
        context.context(self.state)
        original = (self.run_dir / "planning/source-catalog.json").read_bytes()
        (repo / "go.mod").write_text("module example.test/api\n// changed\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "-qm",
                "changed",
            ],
            check=True,
            capture_output=True,
        )
        with self.assertRaises(context.CatalogChanged) as error:
            context.context(self.state)
        self.state.update(status="PAUSED_SOURCE_UNAVAILABLE", stop_reason=str(error.exception), next_stage="draft_plan")
        self.state.pop("resolver_human_request", None)
        self.state.pop("resolver_human_proposal", None)
        self.state.pop("user_request", None)
        self.state["resolver"] = {}
        util.atomic_json(self.run_dir / "state.json", self.state)
        for _ in range(2):
            try:
                code, _, _ = self.invoke("--resume-paused")
                self.assertEqual(2, code)
            except context.CatalogChanged as error:
                self.assertIn("source catalog changed", str(error))
            except SystemExit as error:
                self.assertEqual(2, error.code)
            self.state = util.read_object(self.run_dir / "state.json")
            self.assertEqual(original, (self.run_dir / "planning/source-catalog.json").read_bytes())
            self.assertNotIn("source_catalog_receipt", self.state)
            self.assertEqual("PAUSED_SOURCE_UNAVAILABLE", self.state["status"])
            self.assertEqual([], self.state["stages"])
        before = copy.deepcopy(self.state)
        for _ in range(2):
            code, stdout, stderr = self.invoke("--status", "--json")
            self.assertEqual(0, code, stderr)
            view = json.loads(stdout)["view"]
            self.assertIsNotNone(view["needs"])
            self.assertEqual("continue", view["needs"]["kind"])
            command = client.named(view, self.root, self.run_dir)
            self.assertEqual(["--reconcile-only", "-"], command.argv)
            self.assertEqual(before, self.state)
        with patch.dict(os.environ, command.env):
            code, stdout, stderr = self.invoke(*command.argv)
        self.assertEqual(0, code, stderr)
        self.assertEqual("requirements_gather", self.state["next_stage"])
        self.assertEqual(original, (self.run_dir / "planning/source-catalog.json").read_bytes())


if __name__ == "__main__":
    unittest.main()

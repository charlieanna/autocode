"""Controlled original-completion ordering, not native/image qualification."""

import tempfile
import unittest
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_contract_identity as contract
import autocode_native_launch as launch
import autocode_util as util
import autocode_visual_checkpoint as checkpoint
import autocode_visual_factory as factory
import autocode_visual_history as history
import goal_fixtures

try:
    import autocode_native_completion as completion
except ImportError:
    completion = None


class CompletionFixtures(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-completion-control-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = self.root / ".autocode" / "runs" / "control"
        self.run.mkdir(parents=True)
        body = goal_fixtures.body()
        self.goal = {"task_id": "controlled", "revision": 1, "approval_status": "approved", "body": body}
        self.goal["hash"] = util.digest({key: self.goal[key] for key in ("task_id", "revision", "body")})
        self.goal["approval_event"] = {"actor": "user_cli", "token": contract.token(self.goal)}
        self.state = {
            "goal_contract": self.goal,
            "user_events": [deepcopy(self.goal["approval_event"])],
            "current_task": {
                "id": "T1",
                "milestone_id": "M1",
                "contract_hash": self.goal["hash"],
                "contract_revision": 1,
            },
            "settings": {"design_manifest": {"body": {"controlled": "not image acceptance"}}},
        }
        self.snapshot = {"head": "controlled-original", "files": {}}
        self.snapshot["revision"] = util.digest(self.snapshot)
        self.runtime = {"controlled.py": "a" * 64}
        self.binding = history._task_binding(self.state, self.snapshot, util.digest(self.runtime))
        self.owner = history.NativeReviewHistory(
            self.root,
            self.run,
            protection=SimpleNamespace(verify_namespace=Mock(side_effect=AssertionError("No native qualification"))),
        )
        self.record = {"stage": "astra_review", "role": "astra", "iteration": 1, "rework_evidence": {"hashes": {}}}
        for key in ("events", "output", "before_ref", "after_ref"):
            path = self.run / key
            path.write_bytes(b"controlled original " + key.encode())
            self.record[key] = str(path)
        self.report = {"status": "TASK_COMPLETE", "acceptance_criteria": [{"id": "C1", "status": "verified"}]}
        self.selector = {"kind": "reviews", "path": str(self.run / "review"), "sha256": "b" * 64}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target, attr, value in (
            (history.source_scope, "snapshot", self.snapshot),
            (history, "runtime_files", self.runtime),
            (history.scope, "admit", {"milestone_ids": ["M1"]}),
            (
                self.owner,
                "verify_review",
                {"metadata": {"binding": self.binding, "receipt": {"scope_verdict": "PASS"}}},
            ),
            (self.owner, "fresh_facts", {"eligible_milestone_ids": []}),
        ):
            self.stack.enter_context(patch.object(target, attr, return_value=value))
        self.stage = self.stack.enter_context(
            patch.object(
                self.owner,
                "_stage",
                side_effect=lambda *a, **k: (
                    deepcopy(self.report),
                    b"controlled report",
                    [],
                    {"controlled": "not native proof"},
                ),
            )
        )

    def prepare(self, decision=None):
        return self.owner.prepare_checkpoint(
            self.state,
            review_selector=self.selector,
            completion_record=self.record,
            accepted_decision=deepcopy(decision or self.report),
            prerequisite_selectors={},
            snapshot=self.snapshot,
        )


class OriginalCompletionStatusTests(CompletionFixtures):
    def test_both_real_completion_statuses_keep_exact_original_decision(self):
        for status in ("COMPLETE", "TASK_COMPLETE"):
            with self.subTest(status=status):
                self.report["status"] = status
                prepared = self.prepare()
                proposal = history._json(prepared.proposal_json)
                self.assertEqual(self.report, proposal["metadata"]["accepted_decision"])
                self.assertEqual(checkpoint.assignment_identity(self.report), prepared.decision_sha256)

    def test_original_report_status_or_criterion_cannot_be_rewritten_to_pass(self):
        for decision in (
            {**self.report, "status": "DONE"},
            {**self.report, "acceptance_criteria": [{"id": "C1", "status": "unverified"}]},
        ):
            with self.subTest(decision=decision), self.assertRaisesRegex(util.Paused, "actual accepted original"):
                self.prepare(decision)
        self.assertEqual({}, self.owner._prepared)

    def test_real_status_replays_unchanged_and_wrong_retained_status_refuses(self):
        prepared = self.prepare()
        proposal = history._json(prepared.proposal_json)
        metadata = proposal["metadata"]
        metadata["kind"] = "checkpoints"
        metadata["milestone_id"] = "M1"
        metadata["commit_authorization_sha256"] = prepared.authorization_sha256
        selected = {"kind": "checkpoints", "sha256": "c" * 64, "path": str(self.run / "checkpoint")}
        review = {
            "metadata": {
                "binding": self.binding,
                "receipt": {"scope_verdict": "PASS"},
                "manifest": self.state["settings"]["design_manifest"],
            }
        }
        with (
            patch.object(self.owner, "_load", return_value=(metadata, {})),
            patch.object(self.owner, "_verify_review", return_value=review),
            patch.object(self.owner, "_guard", return_value={"controlled": "not native proof"}),
        ):
            self.assertEqual("M1", self.owner.verify_checkpoint(selected)["milestone_id"])
            metadata["accepted_decision"]["status"] = "DONE"
            with self.assertRaisesRegex(util.Paused, "Original committed Completion decision changed"):
                self.owner.verify_checkpoint(selected)

    def test_legacy_exact_decisions_remain_supported_and_unknown_status_refuses(self):
        for status in ("CONTINUE", "DONE"):
            self.report["status"] = status
            self.assertEqual(
                status, history._json(self.prepare().proposal_json)["metadata"]["accepted_decision"]["status"]
            )
        self.report["status"] = "BLOCKED"
        with self.assertRaisesRegex(util.Paused, "actual accepted original"):
            self.prepare()


class CompletionCallerControls(CompletionFixtures):
    def setUp(self):
        super().setUp()
        self.assertIsNotNone(completion, "Original native completion caller is missing")
        self.context = object.__new__(launch.NativeRunContext)
        self.factory = object.__new__(factory.NativeVisualFactory)
        self.context.factory = self.factory
        self.factory.history = self.owner
        self.factory.protection = SimpleNamespace(committed_selectors=Mock(return_value=[self.selector]))
        self.factory.prepare_checkpoint = Mock(side_effect=lambda state, **kw: self.prepare(kw["accepted_decision"]))
        self.factory.scope_authority = Mock(side_effect=AssertionError("No controlled image acceptance"))
        self.proof = object.__new__(checkpoint.VerifiedCheckpoint)
        self.factory.checkpoint_proof = Mock(return_value=self.proof)
        self.factory.prepare_checkpoint_publication = Mock(return_value="controlled pending handle, not publication")

    def caller(self, **kw):
        return completion.NativeCompletionPreparation(
            self.context, self.state, snapshot=self.snapshot, record=self.record, decision=self.report, **kw
        )

    def accepted(self):
        state = deepcopy(self.state)
        state.update(
            status="TASK_COMPLETE",
            final_decision=deepcopy(self.report),
            milestone_progress={
                self.goal["hash"] + ":M1": {
                    "accepted": True,
                    "contract_hash": self.goal["hash"],
                    "accepted_source_revision": self.snapshot["revision"],
                }
            },
        )
        return state

    def test_only_committed_exact_current_review_is_prepared_then_proven(self):
        caller = self.caller()
        self.assertIs(self.proof, caller.proof)
        self.assertIs(self.factory.scope_authority, caller.scope_authority)
        self.assertEqual(self.selector, self.factory.prepare_checkpoint.call_args.kwargs["review_selector"])
        self.factory.prepare_checkpoint_publication.assert_not_called()
        self.assertEqual("controlled pending handle, not publication", caller.publication(self.accepted()))
        self.assertIs(caller.prepared, self.factory.prepare_checkpoint_publication.call_args.args[0])

    def test_missing_ambiguous_and_foreign_review_refuse_before_preparation(self):
        for selectors in ([], [self.selector, deepcopy(self.selector)]):
            self.factory.protection.committed_selectors.return_value = selectors
            with self.subTest(selectors=selectors), self.assertRaises(util.Paused):
                self.caller()
        self.factory.protection.committed_selectors.return_value = [self.selector]
        foreign = deepcopy(self.binding)
        foreign["task"]["id"] = "foreign"
        self.owner.verify_review.return_value = {"metadata": {"binding": foreign, "receipt": {"scope_verdict": "PASS"}}}
        with self.assertRaises(util.Paused):
            self.caller()
        self.factory.prepare_checkpoint.assert_not_called()

    def test_foreign_context_failed_or_nonterminal_decision_refuse(self):
        with self.assertRaises(util.Paused):
            completion.NativeCompletionPreparation(
                SimpleNamespace(factory=self.factory),
                self.state,
                snapshot=self.snapshot,
                record=self.record,
                decision=self.report,
            )
        for status in ("CONTINUE", "REWORK", "BLOCKED"):
            self.report["status"] = status
            with self.subTest(status=status), self.assertRaises(util.Paused):
                self.caller()
        self.factory.prepare_checkpoint.assert_not_called()

    def test_unaccepted_changed_or_archived_task_and_replay_cannot_publish(self):
        caller = self.caller()
        for change in ("unaccepted", "changed", "archived", "decision"):
            state = self.accepted()
            if change == "unaccepted":
                state["milestone_progress"][self.goal["hash"] + ":M1"]["accepted"] = False
            elif change == "changed":
                state["current_task"]["id"] = "foreign"
            elif change == "archived":
                state["current_task"] = None
            else:
                state["final_decision"]["status"] = "COMPLETE"
            with self.subTest(change=change), self.assertRaises(util.Paused):
                caller.publication(state)
        self.factory.prepare_checkpoint_publication.assert_not_called()
        caller.publication(self.accepted())
        with self.assertRaises(util.Paused):
            caller.publication(self.accepted())


class CompletionControllerJoinControls(unittest.TestCase):
    """Actual public result interpreter, controlled domain proof dependencies."""

    def setUp(self):
        import autopilot

        self.pilot = autopilot
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.order = []
        self.snapshot = {"revision": "original-source"}
        self.proof = object()
        self.preparation = SimpleNamespace(
            proof=self.proof,
            scope_authority=object(),
            publication=Mock(side_effect=lambda state: self.order.append(("publication", deepcopy(state)))),
        )
        native = SimpleNamespace(
            completion=Mock(side_effect=lambda *args: self.order.append("prepare") or self.preparation)
        )
        self.state = {
            "version": 3,
            "status": "RUNNING",
            "settings": {},
            "goal_contract": {"hash": "contract"},
            "current_task": {"id": "T1", "milestone_id": "M1"},
            "stages": [],
        }
        self.value = {
            "status": "TASK_COMPLETE",
            "user_request": {"kind": "none"},
            "acceptance_criteria": [{"id": "C1", "criterion": "controlled requirement", "status": "verified"}],
            "next_objective": "",
            "affected_paths": ["ui.py"],
        }
        self.record = {"stage": "astra_review", "output": "controlled-original-report"}

        def accept(state, current, **kw):
            self.order.append(("accept", kw))
            state["milestone_progress"] = {"contract:M1": {"accepted": True}}

        self.milestones = SimpleNamespace(enabled=Mock(return_value=True), accept=Mock(side_effect=accept))
        self.runtime = SimpleNamespace(
            native_calls=native,
            support=util,
            goals=SimpleNamespace(
                execution_guard=Mock(), missing_human_reviews=Mock(return_value=False), record_decision=Mock()
            ),
            planning=SimpleNamespace(is_planning=Mock(return_value=False)),
            workflow=SimpleNamespace(enabled=Mock(return_value=False)),
            milestones=self.milestones,
            escalation=SimpleNamespace(),
            dispatch=SimpleNamespace(),
            now=lambda: "controlled time",
            write_json=Mock(),
            save_record=Mock(side_effect=lambda state, record: self.order.append("save record")),
        )
        for target, attr, value in (
            (autopilot.launch_inputs, "guard", None),
            (autopilot.source_scope, "snapshot", self.snapshot),
            (autopilot.progressive_state, "enabled", False),
            (autopilot.progressive_state, "prepare_completion", None),
            (autopilot.findings_ledger, "record_decision", None),
            (autopilot.findings_ledger, "blocking_entries", []),
            (autopilot.completion_gate, "artifact_review_request", None),
            (autopilot.builder_failure, "finalize", None),
            (autopilot.stuck, "enabled", False),
        ):
            self.stack.enter_context(patch.object(target, attr, return_value=value))
        self.stack.enter_context(
            patch.object(autopilot.findings_ledger, "recheck_by_validator", side_effect=lambda s, v, r: v)
        )
        self.ready = self.stack.enter_context(
            patch.object(autopilot.completion_gate, "completion_ready", return_value=True)
        )
        self.stack.enter_context(patch.object(autopilot.regression, "complete", return_value=True))
        self.stack.enter_context(patch.object(autopilot.regression, "rejection", return_value="controlled refusal"))
        self.stack.enter_context(
            patch.object(autopilot.completion_gate, "rejection", return_value="controlled refusal")
        )
        self.visual = self.stack.enter_context(
            patch.object(
                autopilot.visual_runtime,
                "require_completion",
                side_effect=lambda *a, **k: self.order.append(("visual", k)),
            )
        )

    def apply(self):
        self.pilot.apply_result(
            self.runtime,
            self.state,
            "astra_review",
            self.value,
            self.record,
            Path("/unused-controlled-workspace"),
            None,
        )

    def test_actual_terminal_branch_threads_proof_and_prepares_after_accept_before_save(self):
        self.apply()
        self.runtime.native_calls.completion.assert_called_once()
        self.assertIs(self.preparation.scope_authority, self.visual.call_args.kwargs["scope_authority"])
        self.assertIs(self.proof, self.milestones.accept.call_args.kwargs["visual_proof"])
        self.assertEqual(
            ["prepare", "visual", "accept", "publication", "save record"],
            [row[0] if isinstance(row, tuple) else row for row in self.order],
        )
        published = self.preparation.publication.call_args.args[0]
        self.assertEqual("TASK_COMPLETE", published["status"])
        self.assertEqual(self.value, published["final_decision"])
        self.assertTrue(published["milestone_progress"]["contract:M1"]["accepted"])
        self.assertEqual({"id": "T1", "milestone_id": "M1"}, published["current_task"])

    def test_functional_completion_refusal_prepares_no_publication_and_preserves_original(self):
        original = deepcopy(self.state)
        self.ready.return_value = False
        with self.assertRaisesRegex(util.Paused, "controlled refusal"):
            self.apply()
        self.preparation.publication.assert_not_called()
        self.milestones.accept.assert_not_called()
        self.runtime.save_record.assert_not_called()
        self.assertEqual(original, self.state)

    def test_ordinary_terminal_branch_keeps_original_call_shapes(self):
        self.runtime.native_calls = None
        self.apply()
        self.assertEqual({"current_snapshot": self.snapshot}, self.visual.call_args.kwargs)
        self.assertEqual({}, self.milestones.accept.call_args.kwargs)
        self.preparation.publication.assert_not_called()

    def test_planning_calls_requires_exact_final_integration_before_helper(self):
        import autocode_native_calls as calls

        native = object.__new__(calls.PlanningCalls)
        native.sink = SimpleNamespace(require_ready=Mock())
        native.initial_task = "complete original input"
        state = {"task": native.initial_task}
        native.current_review = Mock(return_value="original current review")
        native.context = SimpleNamespace(factory=SimpleNamespace(require_final_integration=Mock()))
        with patch.object(calls.completion, "NativeCompletionPreparation", return_value=self.preparation) as prepare:
            self.assertIs(self.preparation, native.completion(state, self.snapshot, self.record, self.value))
            native.context.factory.require_final_integration.assert_called_once_with(
                state, snapshot=self.snapshot, review_selector="original current review"
            )
            prepare.assert_called_once_with(
                native.context, state, snapshot=self.snapshot, record=self.record, decision=self.value
            )
            prepare.reset_mock()
            native.context.factory.require_final_integration.side_effect = util.Paused(
                "PAUSED_VISUAL_EVIDENCE", "all-case proof missing"
            )
            with self.assertRaisesRegex(util.Paused, "all-case proof missing"):
                native.completion(state, self.snapshot, self.record, self.value)
            prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()

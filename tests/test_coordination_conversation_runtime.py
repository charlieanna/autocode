"""Coordinator conversation mutations use public task actions and durable receipts."""

import copy
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_coordination_conversation as policy
import autocode_coordination_store as store
import autocode_taskrun as taskrun

from tests.test_coordination_conversation import document, paused


def runtime():
    return importlib.import_module("autocode_coordination_conversation_runtime")


class PublicInterventionObservationTests(unittest.TestCase):
    def test_task_handle_reads_view_and_intervention_evidence_in_one_public_status_call(self):
        run = taskrun.TaskRun(Path("/workspace"), Path("/workspace/.autocode/runs/run"))
        document = {
            "view": {"status": "RUNNING"},
            "interventions": {"pending_ids": ["owned"]},
            "settings": {"unused": True},
        }
        with patch.object(run, "_invoke", return_value=Mock(stdout=json.dumps(document))) as invoke:
            self.assertEqual({key: document[key] for key in ("view", "interventions")}, run.intervention_status())
        invoke.assert_called_once_with("status", "--status")

    def test_missing_or_malformed_public_interventions_refuse_instead_of_assuming_no_hold(self):
        run = taskrun.TaskRun(Path("/workspace"), Path("/workspace/.autocode/runs/run"))
        for output in ("bad-json", '{"view":{}}', '{"view":{},"interventions":null}'):
            with self.subTest(output=output), patch.object(run, "_invoke", return_value=Mock(stdout=output)):
                with self.assertRaises(taskrun.TaskRunError):
                    run.intervention_status()

    def test_policy_observation_combines_only_the_two_public_parts_without_another_read(self):
        run = Mock()
        run.intervention_status.return_value = {
            "view": {"run_dir": "/run"},
            "interventions": {"pending_ids": ["owned"]},
        }
        self.assertEqual({"run_dir": "/run", "interventions": {"pending_ids": ["owned"]}}, runtime().observation(run))
        run.intervention_status.assert_called_once()
        run.status.assert_not_called()


class RuntimeTests(unittest.TestCase):
    def test_plan_gate_status_names_a_token_bound_conversation_and_stable_semantic_identity(self):
        import autocode_coordinate as coordinator

        from tests.test_coordination_gate import packet

        for member in self.document["members"]:
            member.update({key: key + member["target"] for key in ("base", "role", "origin", "branch", "repository")})
            member["supporting_evidence"] = {}
        store.save(self.document)
        with (
            patch.object(coordinator, "observe", return_value=self.views),
            patch.object(coordinator, "next_step", return_value=packet()["needs"]),
            patch.object(coordinator, "budget_observations", return_value=([], [])),
            patch.object(coordinator, "anchor_view", return_value=self.source),
        ):
            first = coordinator.status(self.document["anchor"])
            store.save(self.document)
            second = coordinator.status(self.document["anchor"])
        self.assertEqual(first["approval"]["basis"], second["approval"]["basis"])
        self.assertNotEqual(first["approval"]["token"], second["approval"]["token"])
        offer = second["conversation"]["input"]["next_command"]
        self.assertEqual(second["approval"]["token"], offer[offer.index("--plan-token") + 1])
        self.assertIsNone(second["conversation"]["gate"]["decision"])

    def test_approval_message_keeps_captured_packet_and_refuses_changed_member_plan(self):
        from tests.test_coordination_gate import approval_context

        context = {
            **policy.context(self.document, self.views),
            "kind": "coordinated_plan",
            "approval": approval_context()["approval"],
        }
        self.messages[0]["plan_context"] = context
        self.views["api"]["displayed_plan"] = context["participants"][0]["plan"]
        active = self.begin()
        self.assertEqual(context, active["context"])
        self.document["conversation"]["active"] = None
        self.views["api"]["displayed_plan"] = {"token": "new-plan"}
        with self.assertRaises(store.CoordinationError):
            self.begin()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.enterContext(patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "catalog")}))
        self.document = document()
        self.document.update(
            version=1,
            anchor=str(self.root / "source/.autocode/runs/task"),
            task="Fix history",
            request_sources=[],
            budget={"initial_spent_usd": 3, "initial_complete": True, "cap_usd": 30},
            joint_attempts=[],
        )
        store.save(self.document)
        self.messages = [{"id": "message", "text": "Please preserve the payload.\nKeep the mapping."}]
        self.views = {
            row["target"]: {"status": "AWAITING_GOAL_APPROVAL", "displayed_plan": {"token": row["target"]}}
            for row in self.document["members"]
        }
        self.source = {
            "run_dir": self.document["anchor"],
            "workspace": str(self.root / "source"),
            "status": "WAITING_FOR_USER",
        }
        self.handles = {
            target: Mock(
                workspace=Path("/" + target), run_dir=Path("/" + target + "/run"), command=("autocode",), env={}
            )
            for target in self.views
        }
        self.handles["Task conversation"] = Mock(
            workspace=Path(self.source["workspace"]),
            run_dir=Path(self.source["run_dir"]),
            command=("autocode",),
            env={},
        )
        for target, handle in self.handles.items():
            handle.status.return_value = self.source if target == "Task conversation" else self.views[target]
            handle.intervention_status.side_effect = lambda run=handle: {
                "view": run.status.return_value,
                "interventions": run.status.return_value.get("interventions", {}),
            }
        self.limits = Mock(return_value=("--max-cost-usd", "12"))

    def execute(self, operation, **fields):
        return runtime().execute(
            self.document,
            {"kind": "conversation", "operation": operation, **fields},
            self.messages,
            self.views,
            self.source,
            run_for=self.handles.__getitem__,
            limits_for=self.limits,
        )

    def begin(self):
        self.execute("begin", request_id="message")
        return self.document["conversation"]["active"]

    def answer(self, intent="question", targets=()):
        active = self.begin()
        active["reply"] = {
            "text": "This requires a revised gateway plan." if intent == "change" else "This could work.",
            "conversation": {"intent": intent, "affected_targets": list(targets)},
        }
        return active

    def test_public_conversation_offer_binds_the_coordination_identity(self):
        import autocode_coordinate as coordinator

        for member in self.document["members"]:
            member["base"] = "baseline"
        store.save(self.document)
        with (
            patch.object(coordinator, "observe", return_value=self.views),
            patch.object(coordinator, "next_step", return_value={"kind": "watch"}),
            patch.object(coordinator, "budget_observations", return_value=([], [])),
            patch.object(coordinator, "anchor_view", return_value=self.source),
        ):
            public = coordinator.status(self.document["anchor"])
        self.assertEqual(self.document["id"], public.get("id"))
        self.assertEqual("coordination_message", public["conversation"]["input"]["kind"])

    def test_begin_saves_context_and_source_accounting_without_changing_limits_or_approval(self):
        self.document["approved_contracts"] = {"token": "approved"}
        active = self.begin()
        saved = store.read(self.document["anchor"])
        self.assertEqual(active, saved["conversation"]["active"])
        self.assertEqual(policy.context(self.document, self.views), active["context"])
        self.assertEqual(self.document["anchor"], saved["budget"]["source_run_dir"])
        self.assertEqual((3, 30), (saved["budget"]["initial_spent_usd"], saved["budget"]["cap_usd"]))
        self.assertEqual({"token": "approved"}, saved["approved_contracts"])
        self.limits.assert_not_called()
        self.assertTrue(all(not run.method_calls for run in self.handles.values()))

    def test_source_identity_conflict_refuses_before_saving_or_dispatching(self):
        self.source["run_dir"] = "/another/run"
        before = store.read(self.document["anchor"])
        with self.assertRaisesRegex(store.CoordinationError, "source identity"):
            self.begin()
        self.assertEqual(before, store.read(self.document["anchor"]))

    def test_question_finish_preserves_all_work_and_can_be_reconciled(self):
        active = self.answer()
        self.document["approved_contracts"] = {"token": "approved"}
        self.document["joint"] = {"run_dir": "/joint", "verification": {"verdict": "PASS"}}
        before = copy.deepcopy(self.document)
        self.execute("finish")
        result = self.document["conversation"]["results"][0]
        self.assertEqual(self.messages[0]["text"], result["text"])
        self.assertEqual("message", result["request_id"])
        self.assertEqual(active["reply"], result["reply"])
        self.assertIsNone(self.document["conversation"]["active"])
        for key in ("members", "budget", "approved_contracts", "joint", "phase"):
            self.assertEqual(before[key], self.document[key], key)
        self.assertEqual(self.document, store.read(self.document["anchor"]))

    def test_change_archives_approval_and_joint_evidence_before_delivering_feedback(self):
        active = self.answer("change", ["gateway"])
        self.document["approved_contracts"] = {"token": "approved"}
        joint = {"run_dir": "/joint", "verification": {"verdict": "PASS"}}
        self.document.update(joint=joint, joint_attempts=[joint], changeset="set")
        with patch.object(runtime().sets, "remove_joint") as remove:
            self.execute("invalidate")
            remove.assert_called_once_with("/api/run", "/joint")
        self.assertEqual(["gateway", "history"], active["targets"])
        self.assertTrue(active["invalidated"])
        self.assertNotIn("joint", self.document)
        self.assertEqual({"verdict": "PASS"}, self.document["joint_attempts"][0]["verification"])
        self.assertNotIn("approved_contracts", self.document)
        self.assertEqual({"token": "approved"}, self.document["conversation"]["superseded_approvals"][0]["record"])
        self.assertTrue(all(not run.method_calls for run in self.handles.values()))

    def test_delivered_feedback_receipt_binds_exact_human_text_and_retains_its_source(self):
        active = self.answer("change", ["gateway"])
        active.update(invalidated=True, targets=["gateway", "history"], delivered=[])
        identifier = policy.request_id(self.document, self.messages[0], "gateway", "feedback")
        self.views["gateway"]["interventions"] = {
            "applied_receipts": [{"id": identifier, "kind": "feedback", "text": "different"}]
        }
        with self.assertRaisesRegex(store.CoordinationError, "feedback receipt"):
            self.execute("delivered", target="gateway")
        self.views["gateway"]["interventions"]["applied_receipts"][0]["text"] = self.messages[0]["text"]
        self.execute("delivered", target="gateway")
        self.assertEqual(["gateway"], active["delivered"])
        member = self.document["members"][1]
        self.assertEqual([{"request_id": "message", "text": self.messages[0]["text"]}], member["message_sources"])
        self.assertEqual("Fix history\n\n" + self.messages[0]["text"], member["brief"])
        self.assertNotIn("message_sources", self.document["members"][0])

    def test_unstarted_member_keeps_the_change_for_its_first_real_start(self):
        active = self.answer("change", ["history"])
        active.update(invalidated=True, targets=["history"], delivered=[])
        self.document["members"][2]["started"] = False
        self.execute("unstarted", target="history")
        self.assertEqual(["history"], active["delivered"])
        self.assertIn(self.messages[0]["text"], self.document["members"][2]["brief"])
        self.handles["history"].advance.assert_not_called()

    def test_source_and_participant_advancement_use_the_current_budget(self):
        self.begin()
        for operation, target in (
            ("source_advance", "Task conversation"),
            ("consume", "gateway"),
            ("prepare_source", "Task conversation"),
        ):
            self.execute(operation, target=target)
            self.assertEqual(self.limits.return_value, self.handles[target].options)
            self.handles[target].advance.assert_called()
        self.assertTrue(self.document["conversation"]["active"]["prepared_source"])

    def test_resume_rechecks_the_fresh_hold_before_using_the_named_action(self):
        self.begin()
        identifier = policy.request_id(self.document, self.messages[0], "api", "pause")
        view = paused({"id": identifier, "kind": "pause"})
        view.update(
            workspace="/api",
            run_dir="/api/run",
            next_command="autocode --workspace /api --run-dir /api/run --resume-paused",
        )
        self.handles["api"].status.return_value = view
        self.execute("resume", target="api")
        self.handles["api"].resume_paused.assert_called_once()
        self.handles["api"].reset_mock()
        view["interventions"]["applied_receipts"].insert(0, {"id": "older-user", "kind": "pause"})
        with self.assertRaisesRegex(store.CoordinationError, "owned pause"):
            self.execute("resume", target="api")
        self.handles["api"].resume_paused.assert_not_called()

    def test_public_submit_checks_receipt_and_keeps_context_out_of_human_text(self):
        active = self.begin()
        expected = {
            "id": active["source_request_id"],
            "kind": "message",
            "text": self.messages[0]["text"],
            "context": active["context"],
        }
        with patch.object(
            runtime().tasks,
            "run_process",
            return_value=Mock(returncode=0, stdout=json.dumps({"accepted": True, "receipt": expected})),
        ) as run:
            self.execute("submit")
            argv = run.call_args.args[0]
            self.assertEqual(self.messages[0]["text"], argv[argv.index("--text") + 1])
            self.assertEqual(active["context"], json.loads(Path(argv[argv.index("--context-file") + 1]).read_text()))
            self.assertEqual("message", argv[argv.index("--kind") + 1])
        with patch.object(
            runtime().tasks,
            "run_process",
            return_value=Mock(
                returncode=0, stdout=json.dumps({"accepted": True, "receipt": {**expected, "text": "not original"}})
            ),
        ):
            with self.assertRaisesRegex(store.CoordinationError, "receipt"):
                self.execute("submit")

    def test_feedback_sends_only_original_text_and_does_not_invoke_a_model(self):
        self.answer("change", ["gateway"])
        identifier = policy.request_id(self.document, self.messages[0], "gateway", "feedback")
        expected = {"id": identifier, "kind": "feedback", "text": self.messages[0]["text"]}
        with patch.object(
            runtime().tasks,
            "run_process",
            return_value=Mock(returncode=0, stdout=json.dumps({"accepted": True, "receipt": expected})),
        ) as run:
            self.execute("feedback", target="gateway")
            argv = run.call_args.args[0]
            self.assertEqual("feedback", argv[argv.index("--kind") + 1])
            self.assertEqual(self.messages[0]["text"], argv[argv.index("--text") + 1])
            self.assertNotIn("--context-file", argv)
        self.handles["gateway"].advance.assert_not_called()

    def test_capture_persists_exact_authenticated_reply_without_plan_mutation(self):
        active = self.begin()
        response = {
            "role": "assistant",
            "text": "Possible, subject to checking conversion fidelity.",
            "conversation": {
                "request_id": active["source_request_id"],
                "context_sha256": runtime().util.digest(active["context"]),
                "intent": "question",
                "affected_targets": [],
            },
        }
        self.source["chat"] = {"messages": [response]}
        with patch.object(runtime().util, "now", return_value="capture-time"):
            self.execute("capture")
        self.assertEqual(response, store.read(self.document["anchor"])["conversation"]["active"]["reply"])
        self.assertEqual("capture-time", active.get("answered_at"))
        with patch.object(runtime().util, "now", return_value="finish-time"):
            self.execute("finish")
        self.assertEqual("capture-time", self.document["conversation"]["results"][0]["answered_at"])
        self.assertTrue(all(not run.method_calls for run in self.handles.values()))

    def test_public_status_shows_captured_answer_before_delivery_is_allowed(self):
        import autocode_coordinate as coordinator

        active = self.answer("change", ["gateway"])
        active["answered_at"] = "captured"
        for member in self.document["members"]:
            member["base"] = "baseline"
        store.save(self.document)
        with (
            patch.object(coordinator, "observe", return_value=self.views),
            patch.object(coordinator, "next_step", return_value={"kind": "blocked", "reason": "held"}),
            patch.object(coordinator, "budget_observations", return_value=([], [])),
            patch.object(coordinator, "anchor_view", return_value=self.source),
            patch.object(coordinator.message_inbox, "read", return_value=self.messages),
        ):
            public = coordinator.status(self.document["anchor"])
        self.assertEqual(1, len(public["conversation"]["results"]))
        self.assertEqual(active["reply"], public["conversation"]["results"][0]["reply"])
        self.assertEqual("blocked", public["needs"]["kind"])
        self.assertEqual([], store.read(self.document["anchor"])["conversation"]["results"])

    def test_pause_uses_only_the_current_named_action_and_stable_request_id(self):
        self.begin()
        handle = self.handles["api"]
        view = {
            "workspace": "/api",
            "run_dir": "/api/run",
            "pause": {
                "next_command": [
                    "intervention",
                    "submit",
                    "--workspace",
                    "/api",
                    "--run-dir",
                    "/api/run",
                    "--request-id",
                    "REQUEST_ID",
                    "--kind",
                    "pause",
                    "--json",
                ]
            },
        }
        handle.status.return_value = view
        identifier = policy.request_id(self.document, self.messages[0], "api", "pause")
        with patch.object(
            runtime().tasks,
            "run_process",
            return_value=Mock(
                returncode=0,
                stdout=json.dumps({"accepted": True, "receipt": {"id": identifier, "kind": "pause", "text": ""}}),
            ),
        ) as run:
            self.execute("pause", target="api", request_id="message")
            self.assertEqual(
                [
                    *handle.command,
                    *[identifier if value == "REQUEST_ID" else value for value in view["pause"]["next_command"]],
                ],
                run.call_args.args[0],
            )


if __name__ == "__main__":
    unittest.main()

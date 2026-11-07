"""Already classified request recovery saves sources once without another interpretation."""

import copy
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autocode_coordination_store import CoordinationError


def fixture():
    member = {"target": "late", "workspace": "/work", "run_dir": "/run", "brief": "Old"}
    document = {"id": "set", "anchor": "/anchor", "members": [member], "pending": None}
    run = Mock(workspace=Path("/work"), run_dir=Path("/run"), command=["autocode"], env={})
    return document, member, run


def held(identifier):
    return {
        "status": "PAUSED_INTERVENTION",
        "needs": {"kind": "resume"},
        "recovery": {"cause": "PAUSED_INTERVENTION"},
        "next_command": "autocode --workspace /work --run-dir /run --resume-paused",
        "interventions": {"applied_receipts": [{"id": identifier, "kind": "feedback"}]},
    }


class RequestRuntimeTests(unittest.TestCase):
    def test_delivery_uses_stable_classified_feedback_and_requires_applied_source(self):
        import autocode_coordination_request_runtime as runtime

        doc, member, run = fixture()
        receipt = {}

        def submitted(_, argv, expected):
            self.assertEqual(expected["id"], doc["pending"]["request_id"])
            self.assertEqual("feedback", argv[argv.index("--kind") + 1])
            receipt.update(expected)
            return expected

        def observation(_):
            return {
                "request_sources": [{"text": "Original correction"}],
                "interventions": {"applied_receipts": [dict(receipt)]},
            }

        with (
            patch.object(runtime.store, "save"),
            patch.object(runtime.runtime, "submit", side_effect=submitted),
            patch.object(runtime.runtime, "observation", side_effect=observation),
        ):
            runtime.deliver(doc, member, "Original correction", run)
        run.feedback.assert_not_called()
        run.advance.assert_not_called()  # receipt already consumed before observation
        self.assertEqual("Original correction", member["brief"])
        self.assertIsNone(doc["pending"])
        self.assertEqual(receipt["id"], doc["request_recovery"][-1]["request_id"])

    def test_pending_receipt_is_consumed_once_without_resuming_a_model(self):
        import autocode_coordination_request_runtime as runtime

        doc, member, run = fixture()
        receipt = {}

        def submit(_, argv, expected):
            receipt.update(expected)

        def observation(_):
            if not run.advance.called:
                return {"interventions": {"pending_ids": [receipt["id"]]}}
            return {"interventions": {"applied_receipts": [receipt]}, "request_sources": [{"text": "Correction"}]}

        with (
            patch.object(runtime.store, "save"),
            patch.object(runtime.runtime, "submit", side_effect=submit),
            patch.object(runtime.runtime, "observation", side_effect=observation),
        ):
            runtime.deliver(doc, member, "Correction", run)
        run.advance.assert_called_once_with()
        run.resume_paused.assert_not_called()

    def test_uncertain_submission_retries_same_identity_and_never_accepts_chat_echo(self):
        import autocode_coordination_request_runtime as runtime

        doc, member, run = fixture()
        with (
            patch.object(runtime.store, "save"),
            patch.object(runtime.runtime, "submit", side_effect=CoordinationError("lost ack")),
        ):
            with self.assertRaises(CoordinationError):
                runtime.deliver(doc, member, "Correction", run)
        pending = copy.deepcopy(doc["pending"])
        with (
            patch.object(runtime.store, "save"),
            patch.object(runtime.runtime, "submit") as submit,
            patch.object(
                runtime.runtime,
                "observation",
                return_value={"chat": {"messages": [{"role": "user", "text": "Correction"}]}},
            ),
        ):
            with self.assertRaises(CoordinationError):
                runtime.deliver(doc, member, "Correction", run)
        self.assertEqual(pending["request_id"], submit.call_args.args[2]["id"])
        self.assertEqual(pending, doc["pending"])
        self.assertEqual("Old", member["brief"])
        run.advance.assert_not_called()

    def test_resume_offer_and_execution_require_exclusively_owned_hold(self):
        import autocode_coordination_request_runtime as runtime

        doc, member, run = fixture()
        doc["request_recovery"] = [{"target": "late", "request_id": "owned"}]
        view = held("owned")
        with patch.object(runtime.runtime, "observation", return_value=view):
            self.assertEqual(
                {"kind": "request_resume", "target": "late"},
                runtime.resume_step(doc, {"late": view}, run_for=lambda _: run),
            )
            runtime.resume(doc, member, run, ["--max-cost-usd", "2"])
        self.assertEqual(["--max-cost-usd", "2"], run.options)
        run.resume_paused.assert_called_once_with()
        view["interventions"]["applied_receipts"].append({"id": "human", "kind": "pause"})
        with patch.object(runtime.runtime, "observation", return_value=view):
            self.assertEqual("blocked", runtime.resume_step(doc, {"late": view}, run_for=lambda _: run)["kind"])
            with self.assertRaises(CoordinationError):
                runtime.resume(doc, member, run, [])
        run.resume_paused.assert_called_once_with()
        self.assertIsNone(runtime.resume_step(doc, {"late": {"status": "PLANNING"}}, run_for=lambda _: run))

    def test_receipt_without_exact_source_or_mismatched_text_cannot_finish_delivery(self):
        import autocode_coordination_request_runtime as runtime

        for bad_source in (True, False):
            doc, member, run = fixture()
            receipt = {}

            def submit(_, argv, expected):
                receipt.update(expected)

            def observation(_):
                return {
                    "interventions": {
                        "applied_receipts": [{**receipt, "text": "Correction" if bad_source else "Other"}]
                    },
                    "request_sources": [{"text": "Other" if bad_source else "Correction"}],
                }

            with (
                patch.object(runtime.store, "save"),
                patch.object(runtime.runtime, "submit", side_effect=submit),
                patch.object(runtime.runtime, "observation", side_effect=observation),
            ):
                with self.assertRaises(CoordinationError):
                    runtime.deliver(doc, member, "Correction", run)
            self.assertEqual("Old", member["brief"])

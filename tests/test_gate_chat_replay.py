"""Conversation identity is re-derived from the replay's current runner packet."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import autocode_replay_bindings as identities
import autocode_replay_cli as cli
import autocode_replay_recording as recording
import autocode_replay_transport as transport
import autocode_replay_view as view
from autocode_taskrun import TaskRun

from tests.test_replay_recording import make_export_projection


class GateReplayTests(unittest.TestCase):
    def test_export_binds_authenticated_gate_token_even_while_redacting_archived_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            projection = make_export_projection(root)
            record = projection["records"][0]
            prompt = Path(projection["run_dir"]) / "gate.prompt.md"
            prompt.write_text("Instructions\nCURRENT HANDOFF DATA\n" + json.dumps(self.packet()))
            record["prompt"] = str(prompt)
            Path(record["output"]).write_text(
                json.dumps({"message_id": "original-message", "gate_token": "original-token"})
            )
            tape = recording.load(recording.export(projection, root / "export"))
            bound = tape.document["dispatches"][0]["bindings"]
            self.assertIn({"pointer": "/report/gate_token", "symbol": "gate_token", "original_valid": True}, bound)
            artifact = tape.document["artifacts"][tape.document["dispatches"][0]["report"]]
            self.assertEqual(
                "[REDACTED]", json.loads((tape.manifest_path.parent / artifact["path"]).read_text())["gate_token"]
            )

    def test_replayed_plan_discussion_displays_the_current_plan_before_feedback(self):
        task = Mock(spec=TaskRun)
        task.status.return_value = {"needs": {"kind": "approve_plan"}}
        task.feedback.return_value = {"needs": {"kind": "continue"}}
        action = {"kind": "feedback", "match": {"kind": "approve_plan"}, "payload": {"text": "yes coplete"}}
        self.assertEqual(task.feedback.return_value, cli._action(task, action, task.status.return_value, None))
        task.show_goal.assert_called_once()
        task.status.assert_called_once()
        task.approve_plan.assert_not_called()

    def test_saved_gate_exchange_is_exported_as_a_bound_feedback_action(self):
        for kind, token, need in (("requirements", "question-1", "answer"), ("plan", "r1:plan", "approve_plan")):
            state = {
                "user_events": [
                    {
                        "kind": "gate_message",
                        "gate_kind": kind,
                        "gate_token": token,
                        "at": "2026-01-01",
                        "text": "yes coplete",
                        "questions": [],
                    }
                ]
            }
            actions = view.project(state)["actions"]
            self.assertEqual(1, len(actions))
            self.assertEqual("feedback", actions[0]["kind"])
            self.assertEqual({"text": "yes coplete"}, actions[0]["payload"])
            self.assertEqual(need, actions[0]["match"]["kind"])

    def packet(self, message="original-message", token="original-token"):
        return {
            "message": {
                "kind": "gate_message",
                "id": message,
                "gate_token": token,
                "gate_kind": "plan",
                "stage": "finalize_plan",
            }
        }

    def test_only_valid_runner_owned_gate_identities_are_rebindable(self):
        packet = self.packet()
        gate = identities.gate_reply(packet)
        self.assertEqual({"gate_message_id": "original-message", "gate_token": "original-token"}, gate)
        payload = {
            "report": {
                "message_id": "original-message",
                "gate_token": "original-token",
                "assistant_reply": "original-message original-token",
            }
        }
        bindings = identities.bindings(payload, {}, {}, "/old/work", "/old/run", [], gate=gate)
        rebound = recording.rebind(
            payload, bindings, identities.gate_reply(self.packet("fresh-message", "fresh-token"))
        )
        self.assertEqual("fresh-message", rebound["report"]["message_id"])
        self.assertEqual("fresh-token", rebound["report"]["gate_token"])
        self.assertEqual(payload["report"]["assistant_reply"], rebound["report"]["assistant_reply"])
        bad = {"report": {"message_id": "foreign-message", "gate_token": "foreign-token"}}
        self.assertEqual([], identities.bindings(bad, {}, {}, "/old/work", "/old/run", [], gate=gate))

    def test_foreign_or_malformed_packets_supply_no_identity(self):
        for packet in (
            {},
            {"message": {"kind": "plan_message"}},
            {"message": "not an event"},
            self.packet(message=""),
            self.packet(token=None),
        ):
            self.assertEqual({}, identities.gate_reply(packet))

    def test_runtime_symbols_come_from_runner_schema_before_prompt_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schema.json"
            path.write_text(
                json.dumps(
                    {
                        "properties": {
                            "message_id": {"type": "string", "enum": ["fresh"]},
                            "gate_token": {"type": "string", "enum": ["current"]},
                        }
                    }
                )
            )
            self.assertEqual({"gate_message_id": "fresh", "gate_token": "current"}, transport.gate_symbols(path))
            path.write_text(json.dumps({"properties": {"message_id": {"type": "string"}}}))
            with self.assertRaises(recording.RecordingError):
                transport.gate_symbols(path)


if __name__ == "__main__":
    unittest.main()

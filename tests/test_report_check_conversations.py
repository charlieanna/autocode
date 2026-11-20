"""Pre-submit checks follow the actual reply contract, not its scheduling role."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import autocode_gate_chat as gate_chat
import autocode_report_check as checker
import autocode_role_launch as launch
import autocode_run_messages as messages
import autocode_tool_handoff as handoff
from units import autoplanner as planner

from tests import test_gate_chat, test_plan_chat, test_run_messages


def conversations():
    for factory in (test_gate_chat.requirements, test_gate_chat.plan):
        state = factory()
        gate_chat.begin(state, "Explain this before I decide.")
        event = gate_chat.pending(state)
        yield (
            event["gate_kind"],
            state,
            {
                "message_id": event["id"],
                "gate_token": event["gate_token"],
                "intent": "question",
                "assistant_reply": "Here is the explanation; the gate still needs its explicit action.",
            },
        )
    state = test_plan_chat.pending()
    yield "saved plan", state, test_plan_chat.response(state)
    for coordinated in (False, True):
        state = test_run_messages.working()
        receipt = test_run_messages.receipt()
        if coordinated:
            receipt["context"] = {
                "kind": "coordinated_plan",
                "participants": [{"target": "project", "status": "RUNNING", "plan": {}}],
                "approval": {"identity": "a" * 64, "publication": "exact reviewed plans"},
            }
        event = messages.receive(state, receipt)
        messages.activate(state)
        value = test_run_messages.response(event)
        if coordinated:
            value["affected_targets"] = []
        yield "coordinated" if coordinated else "live", state, value


class ConversationPrecheckTests(unittest.TestCase):
    def test_runner_generated_command_accepts_the_exact_reply_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for label, state, value in conversations():
                with self.subTest(conversation=label):
                    state["task"] = "The output must be JSON. Text output must remain unchanged."
                    before = copy.deepcopy(state)
                    stage = state["next_stage"]
                    schema = root / "reply.schema.json"
                    schema.write_text(json.dumps(planner.schema_for(state, stage)))
                    saved = {}

                    def save(path, data):
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(json.dumps(data))
                        saved[path] = data

                    _, prompt = launch.prepare_schema_and_report_check(
                        state,
                        stage,
                        root,
                        root,
                        root / "001" / "reply",
                        schema,
                        "Reply.\n" + handoff.MARKER + "{}",
                        False,
                        False,
                        None,
                        save,
                    )
                    packet = json.loads(prompt.split(handoff.MARKER, 1)[1])
                    self.assertIn("report-check --spec", packet["report_check_command"])
                    spec_path, spec = next(iter(saved.items()))
                    self.assertEqual(stage, spec["stage"])
                    output = io.StringIO()
                    with mock.patch("sys.stdin", io.StringIO(json.dumps(value))), redirect_stdout(output):
                        code = checker.cli(["--spec", str(spec_path), "--json", "-"])
                    result = json.loads(output.getvalue())
                    self.assertEqual(0, code, result)
                    self.assertTrue(result["ok"], result)
                    self.assertNotIn("coverage_checklist", spec)
                    self.assertNotIn("retained_contract", spec)
                    self.assertEqual(before, state, "pre-submit checking grants no task authority")

    def test_conversation_schema_restrictions_still_reject_invalid_replies(self):
        for label, state, value in conversations():
            spec = checker.build_spec(
                stage=state["next_stage"],
                workspace=Path("/tmp"),
                schema=planner.schema_for(state, state["next_stage"]),
                state=state,
            )
            for change in ({"intent": "approve"}, {"assistant_reply": 3}, {"requirements": []}):
                with self.subTest(conversation=label, change=change):
                    self.assertTrue(checker.check_report(json.dumps({**value, **change}), spec=spec))
            del value["assistant_reply"]
            self.assertTrue(checker.check_report(json.dumps(value), spec=spec))


if __name__ == "__main__":
    unittest.main()

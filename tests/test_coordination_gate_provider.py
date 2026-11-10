"""The offline coordinator fixture uses the bounded stage-conversation protocol."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import coordination_provider as provider
from tests.test_coordination_gate import approval_context


class CoordinationGateProviderTests(unittest.TestCase):
    def test_fixture_answers_gate_messages_before_any_repository_discovery(self):
        for kind, stage, text, intent in (
            ("gate_message", "requirements_gather", "yes coplete", "confirm"),
            ("plan_message", "finalize_plan", "yes coplete", "confirm"),
            ("gate_message", "requirements_gather", "The displayed result is zero instead of 1000.", "change"),
            ("gate_message", "requirements_gather", "Why this?", "question"),
        ):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output, schema = root / "reply.json", root / "schema.json"
                schema.write_text("{}")
                message = {
                    "kind": kind,
                    "id": "m1",
                    "text": text,
                    "gate_kind": "requirements",
                    "gate_token": "gate",
                    "contract_token": "contract",
                }
                if kind == "plan_message":
                    message["context"] = approval_context()
                packet = {"stage": stage, "message": message}
                with (
                    patch.object(sys, "argv", ["fixture", "--output-schema", str(schema), "-o", str(output)]),
                    patch.object(sys, "stdin", io.StringIO("CURRENT HANDOFF DATA\n" + json.dumps(packet))),
                    patch.object(provider.Path, "cwd", return_value=root),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    provider.main()
                reply = json.loads(output.read_text())
                self.assertEqual(intent, reply["intent"])
                self.assertEqual("m1", reply["message_id"])
                self.assertEqual(stage, json.loads((root / ".autocode/provider-calls.jsonl").read_text())["stage"])
                self.assertEqual(
                    "gate" if kind == "gate_message" else "contract",
                    reply["gate_token" if kind == "gate_message" else "contract_token"],
                )


if __name__ == "__main__":
    unittest.main()

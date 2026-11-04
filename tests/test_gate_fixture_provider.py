"""The offline provider speaks the same bounded conversation protocol as real agents."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import live_fixture_provider as provider


class GateFixtureProviderTests(unittest.TestCase):
    def test_fixture_emits_a_bound_interpretation_instead_of_another_requirements_report(self):
        for text, intent in (
            ("y", "confirm"),
            ("yes", "confirm"),
            ("yes correct", "confirm"),
            ("no", "decline"),
            ("Why this?", "question"),
            ("Keep the same scope and standard-library-only constraint.", "change"),
        ):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "report.json"
                packet = {
                    "stage": "requirements_gather",
                    "message": {
                        "kind": "gate_message",
                        "gate_kind": "requirements",
                        "id": "message",
                        "gate_token": "token",
                        "text": text,
                    },
                }
                with (
                    mock.patch.object(sys, "argv", ["fixture", "-o", str(output)]),
                    mock.patch.object(sys, "stdin", io.StringIO("CURRENT HANDOFF DATA\n" + json.dumps(packet))),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(0, provider.main())
                self.assertEqual(
                    {
                        "message_id": "message",
                        "gate_token": "token",
                        "intent": intent,
                        "assistant_reply": "Offline fixture: interpreted the reply to the displayed decision.",
                    },
                    json.loads(output.read_text()),
                )


if __name__ == "__main__":
    unittest.main()

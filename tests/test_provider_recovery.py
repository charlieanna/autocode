"""Pure transport classification controls: no model or clock waits."""

import json
import unittest

import autocode_provider_recovery as recovery


class StartupDiagnosticTests(unittest.TestCase):
    def test_recognized_whole_diagnostics(self):
        for text in (
            "database is locked",
            "SQLiteError: database table is locked",
            "\x1b[91mError: \x1b[0mUnexpected error\n\ndatabase is locked\n",
            json.dumps({"type": "error", "error": {"message": "database is locked", "code": "SQLITE_BUSY"}}),
            json.dumps({"type": "turn.failed", "error": {"message": "database is locked"}}),
        ):
            with self.subTest(text=text):
                self.assertTrue(recovery.startup_lock(text))

    def test_execution_and_ambiguous_text_are_not_startup_failures(self):
        lock = {"type": "error", "error": {"message": "database is locked"}}
        controls = [
            '{"type":"thread.started","type":"error","error":{"message":"database is locked"}}',
            "",
            "test output: database is locked",
            "Error: Unexpected error\nPermission denied",
            "database is locked\nUnrecognized output",
            "Database migration failed: database is locked",
            json.dumps({"type": "text", "text": "database is locked"}),
            json.dumps({**lock, "sessionID": "ses_started"}),
            json.dumps({**lock, "usage": {"input_tokens": 1}}),
            json.dumps({**lock, "error": {"message": "database is locked", "code": "rate_limit"}}),
        ]
        controls += [
            json.dumps({"type": event}) + "\n" + json.dumps(lock)
            for event in ("thread.started", "step_start", "tool_use", "item.completed", "turn.completed")
        ]
        for text in controls:
            with self.subTest(text=text):
                self.assertFalse(recovery.startup_lock(text))

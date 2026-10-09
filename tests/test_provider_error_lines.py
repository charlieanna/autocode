"""A provider error printed as plain text at the end of a stage log is classified like its JSON form (#562)."""

import json
import tempfile
import unittest
from pathlib import Path

import autocode_provider_error_lines as error_lines
import autocode_support as support

RETRY_429 = "ERROR: exceeded retry limit, last status: 429 Too Many Requests"
# ``codex exec`` without --json: the transcript, a command's output, then the provider's own error.
TRANSCRIPT = (
    "OpenAI Codex v0.130.0\n--------\nworkdir: /project\n--------\nuser\nValidate the change.\n"
    "exec\nbash -lc 'python -m unittest' in /project\n exited 1 in 120ms:\n"
    "ERROR: test_retry_after_429 (tests.test_client.RetryTests.test_retry_after_429)\n"
    "AssertionError: 429 Too Many Requests was not retried\nFAILED (errors=1)\n"
)


class PlainTextProviderErrorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.log = Path(temp.name) / "validator-02.jsonl"

    def status(self, text):
        self.log.write_text(text)
        return support.failure_status(self.log)

    def test_a_trailing_plain_text_429_is_a_rate_limit(self):
        self.assertEqual("PAUSED_RATE_LIMIT", self.status(TRANSCRIPT + RETRY_429 + "\n" + RETRY_429 + "\n\n"))
        self.assertEqual([RETRY_429, RETRY_429], error_lines.trailing(self.log))

    def test_a_trailing_plain_text_quota_or_capacity_stop_is_classified_like_its_json_form(self):
        for line, status in (
            ("ERROR: You've hit your usage limit. Try again in 3 days.", "PAUSED_BUDGET"),
            ("Error: Weekly/Monthly Limit Exhausted", "PAUSED_BUDGET"),
            ("error: model is at capacity, please try again later", "PAUSED_PROVIDER_CAPACITY"),
            ("\x1b[31mERROR:\x1b[0m 429 Too Many Requests", "PAUSED_RATE_LIMIT"),
            ("[2026-10-06T20:01:02] ERROR: 429 Too Many Requests", "PAUSED_RATE_LIMIT"),
        ):
            with self.subTest(line=line):
                self.assertEqual(status, self.status(TRANSCRIPT + line + "\n"))

    def test_tool_output_that_mentions_429_is_not_the_provider_s_error(self):
        # The command's own ERROR line names 429, but the provider's last word is a different failure.
        self.assertEqual(
            "PAUSED_PROVIDER_UNCERTAIN", self.status(TRANSCRIPT + "ERROR: stream disconnected before completion\n")
        )
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", self.status(TRANSCRIPT))
        self.assertEqual([], error_lines.trailing(self.log))

    def test_429_inside_a_json_tool_event_is_not_the_provider_s_error(self):
        tool = {
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "command_execution",
                "command": "python -m unittest",
                "exit_code": 1,
                "aggregated_output": RETRY_429 + "\nERROR: test_retry_after_429\nFAILED (errors=1)\n",
            },
        }
        failed = {"type": "turn.failed", "error": {"message": "stream disconnected before completion"}}
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", self.status(json.dumps(tool) + "\n" + json.dumps(failed) + "\n"))
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", self.status(json.dumps(tool) + "\n"))

    def test_an_error_line_the_provider_continued_past_is_not_its_last_word(self):
        resumed = {"type": "turn.failed", "error": {"message": "stream disconnected before completion"}}
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", self.status(RETRY_429 + "\n" + json.dumps(resumed) + "\n"))
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", self.status(RETRY_429 + "\nRetrying with a fresh session\n"))

    def test_a_missing_log_has_no_error_lines(self):
        self.assertEqual([], error_lines.trailing(self.log))
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(self.log))


if __name__ == "__main__":
    unittest.main()

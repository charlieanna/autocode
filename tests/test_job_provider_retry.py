"""#3233 rerun: which stopped workflow jobs AutoCode retries itself after a provider's API error."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode_job_failure as job_failure
import autocode_job_provider_retry as provider_retry
import autocode_support as support

# The tail of the live Tester's stream, with its paths and headers trimmed: a tool output that quotes
# AutoCode's own containment source, then MiMo's gateway error (2026-11-12, write_acceptance_tests).
GATEWAY_ERROR = {
    "type": "error",
    "timestamp": 1794494909832,
    "sessionID": "ses_fixture",
    "error": {
        "name": "APIError",
        "data": {
            "message": "Request failed",
            "statusCode": 400,
            "isRetryable": False,
            "responseHeaders": {"content-type": "application/json"},
            "responseBody": json.dumps(
                {
                    "error": {
                        "code": "400",
                        "message": "Request failed",
                        "param": "Connection prematurely closed BEFORE response",
                        "type": "",
                    }
                }
            ),
            "metadata": {"url": "https://provider.example.test/v1/chat/completions"},
        },
    },
}
MENTION = {
    "type": "tool_use",
    "sessionID": "ses_fixture",
    "part": {"tool": "bash", "state": {"output": 'tools/autocode_tool_containment.py: "external_directory": "deny"'}},
}
LIVE_TAIL = "\n".join([json.dumps(MENTION), "\x1b[91m\x1b[1mError: \x1b[0mRequest failed", json.dumps(GATEWAY_ERROR)])


class ProviderErrorTests(unittest.TestCase):
    def test_the_live_gateway_error_is_named_with_its_status_and_cause(self):
        self.assertEqual(
            "400 Request failed: Connection prematurely closed BEFORE response",
            provider_retry.provider_error(LIVE_TAIL),
        )

    def test_the_command_providers_bare_row_is_read_too(self):
        bare = json.dumps({"name": "APIError", "data": {"message": "Upstream reset", "isRetryable": True}})
        self.assertEqual("Upstream reset", provider_retry.provider_error(bare))

    def test_only_a_stream_that_ended_on_an_api_error_counts(self):
        later = LIVE_TAIL + "\n" + json.dumps({"type": "step_finish", "sessionID": "ses_fixture"})
        other = json.dumps({"type": "error", "error": {"name": "UnknownError", "data": {"message": "boom"}}})
        for raw in (later, other, "", "not json", json.dumps({"type": "error", "error": "text"})):
            with self.subTest(raw=raw[-60:]):
                self.assertIsNone(provider_retry.provider_error(raw))

    def test_a_long_message_is_bounded(self):
        row = {"type": "error", "error": {"name": "APIError", "data": {"message": "x" * 1000}}}
        self.assertEqual(provider_retry.MAX_MESSAGE, len(provider_retry.provider_error(json.dumps(row))))


class PlanTests(unittest.TestCase):
    FAILURE = {"kind": provider_retry.KIND, "stage": "write_acceptance_tests", "unrestored": []}

    def test_one_retry_per_job_in_a_run(self):
        self.assertEqual({"retry": 1, "of": 1}, provider_retry.plan({}, self.FAILURE, abandoned=False))
        spent = {"user_events": [{"kind": provider_retry.EVENT, "stage": "write_acceptance_tests"}, "bad"]}
        self.assertIsNone(provider_retry.plan(spent, self.FAILURE, abandoned=False))
        other = {"user_events": [{"kind": provider_retry.EVENT, "stage": "investigate_stuck"}]}
        self.assertEqual(1, provider_retry.plan(other, self.FAILURE, abandoned=False)["retry"])

    def test_other_stops_are_not_settled_here(self):
        for failure, abandoned in (
            (dict(self.FAILURE, kind="exit"), False),
            (dict(self.FAILURE, kind="external_directory"), False),
            (dict(self.FAILURE, unrestored=["calc.py"]), False),
            (self.FAILURE, True),
        ):
            with self.subTest(failure=failure, abandoned=abandoned):
                self.assertIsNone(provider_retry.plan({}, failure, abandoned=abandoned))


class ClassificationTests(unittest.TestCase):
    """The stop's kind comes from what ended the stream, not from words the job read."""

    def reason(self, raw):
        with tempfile.TemporaryDirectory() as folder:
            events = Path(folder) / "acceptance-tests-02.jsonl"
            events.write_text(raw)
            runtime = SimpleNamespace(support=support)
            record = {
                "stage": "write_acceptance_tests",
                "events": str(events),
                "exit_code": 1,
                "launch_route": {"model": "mimo-fixture"},
            }
            return job_failure._reason(runtime, record, None)

    def test_a_gateway_error_after_reading_the_denial_word_is_a_provider_error(self):
        kind, reason = self.reason(LIVE_TAIL)
        self.assertEqual(provider_retry.KIND, kind)
        self.assertIn("the provider returned an error on mimo-fixture", reason)
        self.assertIn("Connection prematurely closed", reason)

    def test_opencodes_own_denial_line_is_still_a_denial(self):
        denial = json.dumps(MENTION) + "\npermission requested: external_directory (/tmp/*); auto-rejecting\n"
        self.assertEqual("external_directory", self.reason(denial)[0])

    def test_a_mention_alone_is_an_ordinary_exit(self):
        self.assertEqual("exit", self.reason(json.dumps(MENTION) + "\n")[0])


if __name__ == "__main__":
    unittest.main()

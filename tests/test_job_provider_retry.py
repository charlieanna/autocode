"""#3233 rerun: which stopped workflow jobs AutoCode retries itself after a provider's API error."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode_job_end as job_end
import autocode_job_failure as job_failure
import autocode_job_provider_retry as provider_retry
import autocode_run_view as run_view
import autocode_stop as stop
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
    FAILURE = {
        "kind": provider_retry.KIND,
        "stage": "write_acceptance_tests",
        "unrestored": [],
        "source_identity": "source",
    }

    def test_one_retry_per_job_in_a_run(self):
        self.assertEqual({"retry": 1, "of": 1}, provider_retry.plan({}, self.FAILURE, abandoned=False))
        spent = {"user_events": [{"kind": provider_retry.EVENT, "stage": "write_acceptance_tests"}, "bad"]}
        self.assertIsNone(provider_retry.plan(spent, self.FAILURE, abandoned=False))
        other = {"user_events": [{"kind": provider_retry.EVENT, "stage": "investigate_stuck"}]}
        self.assertEqual(1, provider_retry.plan(other, self.FAILURE, abandoned=False)["retry"])

    def test_every_provider_stop_of_the_job_is_retried_the_same_way(self):
        for kind in sorted(provider_retry.KINDS):
            with self.subTest(kind=kind):
                self.assertEqual(
                    {"retry": 1, "of": 1}, provider_retry.plan({}, dict(self.FAILURE, kind=kind), abandoned=False)
                )

    def test_other_stops_are_not_settled_here(self):
        for failure, abandoned in (
            (dict(self.FAILURE, kind="external_directory"), False),
            (dict(self.FAILURE, kind="timeout"), False),
            (dict(self.FAILURE, kind="quota"), False),
            (dict(self.FAILURE, kind="person"), False),
            (dict(self.FAILURE, unrestored=["calc.py"]), False),
            (dict(self.FAILURE, source_identity=None), False),
            (self.FAILURE, True),
        ):
            with self.subTest(failure=failure, abandoned=abandoned):
                self.assertIsNone(provider_retry.plan({}, failure, abandoned=abandoned))


class EndPolicyTests(unittest.TestCase):
    """A paused failed job is AutoCode's to settle unless a person has something to decide (job_end)."""

    def state(self, **failure):
        return {
            "status": "PAUSED_JOB_FAILURE",
            "next_stage": "check_design",
            "job_failure": {
                "stage": "check_design",
                "attempt_id": "001/design-check-01",
                "kind": "exit",
                "reason": "Design Reviewer: provider exited 42 without a terminal report",
                "source_identity": "source",
                "unrestored": [],
                "archive": "/run/iterations/001",
                "job_retry_token": "jr:fixture",
                **failure,
            },
        }

    def spent(self, state):
        state["user_events"] = [{"kind": provider_retry.EVENT, "stage": "check_design", "cause": "exit"}]
        return state

    def test_a_retry_left_is_pending_and_a_spent_one_ends(self):
        fresh = self.state()
        self.assertTrue(job_end.pending(fresh))
        self.assertIsNone(job_end.end_cause(fresh))
        spent = self.spent(self.state())
        self.assertEqual("retries_spent", job_end.end_cause(spent))
        self.assertTrue(job_end.pending(spent))

    def test_a_denial_and_a_missing_source_end_at_once(self):
        self.assertEqual("external_directory", job_end.end_cause(self.state(kind="external_directory")))
        for kind in ("exit", "timeout", "quota", "person"):
            with self.subTest(kind=kind):
                self.assertEqual("no_source_identity", job_end.end_cause(self.state(kind=kind, source_identity=None)))

    def test_a_missing_source_ends_whoever_stopped_the_job_and_whatever_it_asks(self):
        # No retry of an attempt without its original source identity can be checked, so a person's
        # abandonment, a model question or unrestored files leave nothing to decide either.
        for state in (
            dict(self.state(source_identity=None), status="PAUSED_STAGE_ABANDONED"),
            self.state(kind="quota", route={"question_id": "route-architect"}, source_identity=None),
            self.state(unrestored=["calc.py"], source_identity=None),
        ):
            with self.subTest(status=state["status"], failure=state["job_failure"]):
                self.assertEqual("no_source_identity", job_end.end_cause(state))
                self.assertTrue(job_end.pending(state))

    def test_what_a_person_decides_stays_with_the_person(self):
        for state in (
            self.state(kind="person"),
            self.state(kind="quota", route={"question_id": "route-architect"}),
            self.state(kind="timeout"),
            self.spent(self.state(unrestored=["calc.py"])),
            dict(self.state(), status="PAUSED_STAGE_ABANDONED"),
        ):
            with self.subTest(state=state["job_failure"]["kind"], status=state["status"]):
                self.assertIsNone(job_end.end_cause(state))
                self.assertFalse(job_end.pending(state))

    def test_an_investigations_own_job_is_left_to_the_investigation(self):
        for stage, record in (
            ("investigate_stuck", "stuck_investigation"),
            ("resolve_repair", "resolve_repair_request"),
        ):
            with self.subTest(stage=stage):
                state = self.spent(self.state(stage=stage, source_identity=None))
                state["user_events"][0]["stage"] = stage
                state[record] = {"identity": "fixture"}
                self.assertTrue(job_end.investigation_owned(state))
                self.assertIsNone(job_end.end_cause(state))
                self.assertFalse(job_end.pending(state))
                del state[record]
                self.assertEqual("no_source_identity", job_end.end_cause(state))

    def test_conclude_records_the_cause_and_the_retries(self):
        state = self.spent(self.state())
        self.assertTrue(job_failure.conclude(state))
        outcome = stop.outcome(state)
        self.assertEqual(("could_not_finish", "retries_spent"), (outcome["result"], outcome["evidence"]["cause"]))
        self.assertEqual("exit", outcome["evidence"]["automatic_retries"][0]["cause"])
        self.assertIn("provider exited 42", outcome["reason"])
        self.assertFalse(job_end.pending(state), "an ended run is not settled twice")
        self.assertFalse(job_failure.conclude(state))

    def test_the_view_shows_continue_until_the_resume_settles_it(self):
        view = run_view.view(self.state())
        self.assertEqual({"kind": "continue", "action": "--resume-paused"}, view["needs"])
        self.assertEqual(job_end.NOTICE, view["stop_reason"])
        self.assertNotIn("retry_job", str(view["needs"]))
        self.assertEqual("automatic", job_failure.resume_gate(self.state(), resume=True, retry=False, token=None))
        self.assertEqual(
            "authorize", job_failure.resume_gate(self.state(), resume=True, retry=True, token="jr:fixture")
        )


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

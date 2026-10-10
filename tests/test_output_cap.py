"""OpenCode's per-response output cap: AutoCode raises it, records it, and names it on a length stop."""

import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_agent_env as agent_env
import autocode_opencode as opencode
import autocode_output_cap as output_cap
import autocode_provider_launch as provider_launch
import autocode_stage_recovery as recovery
import autocode_support as support

from tests import test_subprocess as subprocess_test_support

VARIABLE = output_cap.VARIABLE


class CapTests(unittest.TestCase):
    def test_only_a_positive_whole_number_is_a_cap(self):
        self.assertEqual(100000, output_cap.configured({VARIABLE: "100000"}))
        for value in ("", "0", "64k", " 64000", "-1", "6.4e4", "1234567890"):
            with self.subTest(value=value):
                self.assertIsNone(output_cap.configured({VARIABLE: value}))
        self.assertIsNone(output_cap.configured({}))

    def test_a_launch_gets_the_default_unless_it_holds_a_usable_cap(self):
        for given, expected in ((None, "64000"), ("100000", "100000"), ("8000", "8000"), ("64k", "64000")):
            with self.subTest(given=given):
                child = {} if given is None else {VARIABLE: given}
                output_cap.apply(child)
                self.assertEqual(expected, child[VARIABLE])

    def test_the_record_says_who_chose_the_cap_the_process_got(self):
        self.assertEqual({"tokens": 64000, "set_by": "autocode"}, output_cap.recorded({}, {VARIABLE: "64000"}))
        self.assertEqual(
            {"tokens": 100000, "set_by": "operator"}, output_cap.recorded({VARIABLE: "100000"}, {VARIABLE: "100000"})
        )
        self.assertEqual(
            {"tokens": 64000, "set_by": "autocode"}, output_cap.recorded({VARIABLE: "64k"}, {VARIABLE: "64000"})
        )
        # Scrubbed away before it reached OpenCode: OpenCode's own default applies.
        self.assertEqual({"tokens": 32000, "set_by": "opencode"}, output_cap.recorded({VARIABLE: "64000"}, {}))

    def test_a_length_stop_reports_what_its_last_response_used(self):
        message = output_cap.length_stop({"input": 9, "output": 15063, "reasoning": 16937})
        self.assertTrue(message.startswith(output_cap.LENGTH_STOP))
        self.assertIn("after 32000 output tokens in one response, 16937 of them reasoning", message)
        for tokens in (None, {}, {"output": 5}, {"output": True, "reasoning": 1}, {"output": -1, "reasoning": 2}):
            with self.subTest(tokens=tokens):
                self.assertEqual(
                    output_cap.LENGTH_STOP + ". The attempt is incomplete; review saved work before recovery.",
                    output_cap.length_stop(tokens),
                )

    def test_explain_names_the_cap_only_for_a_length_stop(self):
        stop = output_cap.length_stop({"output": 32000, "reasoning": 32000})
        explained = output_cap.explain(stop, {"tokens": 64000, "set_by": "autocode"})
        self.assertTrue(explained.startswith(stop.rstrip(".")))
        self.assertIn(
            f"capped each response at 64000 tokens, reasoning included ({VARIABLE}, AutoCode's default)", explained
        )
        self.assertIn(f"set {VARIABLE} to a larger number", explained)
        self.assertIn(
            "OpenCode's default; the variable did not reach it",
            output_cap.explain(stop, {"tokens": 32000, "set_by": "opencode"}),
        )
        other = "OpenCode stopped after a step that requested tool calls"
        self.assertEqual(other, output_cap.explain(other, {"tokens": 64000, "set_by": "autocode"}))
        self.assertEqual(stop, output_cap.explain(stop, None))
        self.assertIsNone(output_cap.explain(None, {"tokens": 64000, "set_by": "autocode"}))


class LaunchTests(unittest.TestCase):
    def launch(self, environment):
        with patch.dict(os.environ, environment, clear=True):
            return provider_launch.prepare(
                engine="opencode",
                adapter=opencode,
                role="glm",
                route_role="glm",
                workspace=Path("/workspace"),
                run_dir=Path("/run"),
                session=None,
                model="zai-coding-plan/glm-5.3",
                effort="high",
                allow_write=False,
                planning=True,
                report=Path("/run/r.json"),
                schema=Path("/run/s.json"),
                prompt_file=Path("/run/p.md"),
                sandbox="read-only",
                transport_args=[],
                chatgpt=False,
                provider=None,
                enforce_tool_boundary=False,
            )

    def test_opencode_launches_with_the_default_cap_and_records_it(self):
        _, environment, _, worker = self.launch({"PATH": "/usr/bin", "GH_TOKEN": "t"})
        self.assertEqual("64000", environment[VARIABLE])
        self.assertNotIn("GH_TOKEN", environment)
        self.assertEqual({"tokens": 64000, "set_by": "autocode"}, worker["output_token_cap"])

    def test_an_operator_cap_survives_the_credential_scrub_without_a_pass_through(self):
        _, environment, _, worker = self.launch({"PATH": "/usr/bin", VARIABLE: "100000"})
        self.assertEqual("100000", environment[VARIABLE])
        self.assertEqual({"tokens": 100000, "set_by": "operator"}, worker["output_token_cap"])
        self.assertNotIn(VARIABLE, agent_env.withheld({VARIABLE: "100000"}))

    def test_a_configured_command_provider_gets_no_opencode_cap(self):
        class Configured:
            NAME, CONFIGURED = "kilo", True

            @staticmethod
            def launch(*args, **kwargs):
                return ["kilo", "run"], {"PATH": "/usr/bin"}, None

        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
            _, environment, _, worker = provider_launch.prepare(
                engine="opencode",
                adapter=Configured,
                role="sol",
                route_role="sol",
                workspace=Path("/w"),
                run_dir=Path("/r"),
                session=None,
                model="kilo/m",
                effort=None,
                allow_write=False,
                planning=False,
                report=None,
                schema=None,
                prompt_file=None,
                sandbox="read-only",
                transport_args=[],
                chatgpt=False,
                provider=None,
            )
        self.assertNotIn(VARIABLE, environment)
        self.assertNotIn("output_token_cap", worker)


class LengthStopCliTests(unittest.TestCase):
    """A real CLI run against the fake OpenCode, which stops at the cap its process received."""

    new_run_engine_args = ("--engine", "opencode")
    launch = subprocess_test_support.SubprocessFlow.launch
    saved = subprocess_test_support.SubprocessFlow.saved

    def setUp(self):
        subprocess_test_support.SubprocessFlow.setUp(self)
        source = Path(__file__).resolve().parents[1] / "tools" / "fake_opencode.py"
        provider = self.root / "fixture-bin" / "opencode"
        shutil.copy2(source, provider)
        provider.chmod(0o755)
        from .opencode_fixture_cli import entrypoint

        self.entry = entrypoint(self.entry)
        self.env.update(AUTOCODE_FIXTURE_MODE="no-human", AUTOCODE_FIXTURE_TRUNCATE_STAGE="terra")
        for name in (VARIABLE, agent_env.PASS_VARIABLE):
            self.env.pop(name, None)

    def builder_stop(self):
        self.launch(["Build a greeting tool", "--chat"], 2, answers="CLI\nyes\n")
        _, state = self.saved()
        record = state.get("active_stage") or [row for row in state["stages"] if row["stage"] == "terra"][-1]
        self.assertEqual("terra", record["stage"])
        return state, record

    def test_the_builder_stops_at_autocodes_cap_and_the_pause_names_it(self):
        state, record = self.builder_stop()
        self.assertEqual({"tokens": 64000, "set_by": "autocode"}, record["output_token_cap"])
        reason = state["stop_reason"]
        # The fake stops where the cap its process received says: proof the variable got there.
        self.assertIn("after 64000 output tokens in one response", reason)
        self.assertIn(
            f"capped each response at 64000 tokens, reasoning included ({VARIABLE}, AutoCode's default)", reason
        )
        self.assertEqual(64000, support.event_metrics(record["events"])["provider_tokens"]["output_tokens"])

    def test_an_operator_cap_reaches_opencode_without_a_pass_through(self):
        self.env[VARIABLE] = "100000"
        state, record = self.builder_stop()
        self.assertEqual({"tokens": 100000, "set_by": "operator"}, record["output_token_cap"])
        self.assertIn("after 100000 output tokens in one response", state["stop_reason"])
        self.assertNotIn(VARIABLE, record["withheld_env"])


class LengthAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"type": "thread.started", "thread_id": "ses-current"},
            {"type": "turn.failed", "error": {"code": "output_token_limit"}},
        ]
        self.record = {"finished_at": "t", "exit_code": 0, "expected_session": "ses-current"}

    def test_collected_terminal_length_is_routable_even_without_token_counts(self):
        self.assertTrue(output_cap.authenticated(self.record, self.rows))
        self.assertTrue(output_cap.authenticated({**self.record, "exit_code": 1}, self.rows))

    def test_unknown_interrupted_or_uncollected_ownership_never_routes(self):
        for key, value in (
            ("finished_at", None),
            ("exit_code", None),
            ("exit_code", -15),
            ("exit_code", True),
            ("timed_out", True),
            ("interrupted", True),
            ("cleanup_error", "unknown"),
            ("supervision_errors", ["unknown"]),
        ):
            with self.subTest(key=key, value=value):
                self.assertFalse(output_cap.authenticated({**self.record, key: value}, self.rows))
        self.assertFalse(output_cap.authenticated({"finished_at": "t", "exit_code": 0}, self.rows))

    def test_missing_mixed_unexpected_and_malformed_sessions_never_route(self):
        self.assertTrue(output_cap.authenticated({**self.record, "expected_session": None}, self.rows))
        for expected in (False, 0, [], {}, ""):
            with self.subTest(expected=expected):
                self.assertFalse(output_cap.authenticated({**self.record, "expected_session": expected}, self.rows))
        for thread in (None, "", [], "ses-other"):
            with self.subTest(thread=thread):
                rows = copy.deepcopy(self.rows)
                rows[0]["thread_id"] = thread
                self.assertFalse(output_cap.authenticated(self.record, rows))
        self.assertFalse(output_cap.authenticated(self.record, self.rows[1:]))
        self.assertFalse(
            output_cap.authenticated(self.record, [{"type": "thread.started", "thread_id": "other"}, *self.rows])
        )

    def test_words_unfinished_streams_and_conflicting_errors_are_not_length_authority(self):
        for rows in (
            None,
            [],
            ["bad"],
            [{"type": "text", "text": output_cap.LENGTH_STOP}],
            [*self.rows, {"type": "usage.partial"}],
            [{"type": "error", "error": {"message": "failure"}}, *self.rows],
            [{"type": "turn.completed"}, *self.rows],
        ):
            with self.subTest(rows=rows):
                self.assertFalse(output_cap.exhausted(rows))


class LengthTransportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "events.jsonl"
        self.record = {"finished_at": "t", "exit_code": 0, "expected_session": "ses_limit", "events": str(self.path)}
        self.rows = [
            {"type": "step_start", "sessionID": "ses_limit", "part": {"id": "start"}},
            {
                "type": "step_finish",
                "sessionID": "ses_limit",
                "part": {
                    "id": "finish",
                    "reason": "length",
                    "tokens": {"input": 429, "output": 429, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                },
            },
        ]

    def write(self, rows):
        self.path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def test_native_length_with_numeric_429_keeps_length_cause_and_usage(self):
        self.write(self.rows)
        self.assertEqual("PAUSED_OUTPUT_CAP", support.failure_status(self.path, record=self.record))
        self.assertEqual(429, support.event_metrics(self.path)["provider_tokens"]["output_tokens"])
        self.assertEqual(0, support.event_metrics(self.path)["completed_turns"])
        self.assertTrue(recovery._stopped_on_route(self.record))

    def test_unauthenticated_native_length_never_becomes_a_numeric_rate_limit(self):
        self.write(self.rows)
        for change in ({"timed_out": True}, {"expected_session": "other"}, {"finished_at": None}, {"exit_code": None}):
            with self.subTest(change=change):
                record = {**self.record, **change}
                self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(self.path, record=record))
                self.assertFalse(recovery._stopped_on_route(record))

    def test_unknown_trailing_and_non_json_transport_cannot_authorize_routing(self):
        for tail in (
            {"type": "unknown", "sessionID": "ses_limit"},
            {"type": "text", "sessionID": "ses_limit", "part": {"id": "tail", "text": "more"}},
            ["bad"],
        ):
            with self.subTest(tail=tail):
                self.write([*self.rows, tail])
                self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(self.path, record=self.record))
        self.write(self.rows)
        with self.path.open("a") as output:
            output.write("unparsed trailing provider output\n")
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(self.path, record=self.record))

    def test_discarded_or_contradictory_raw_phase_session_never_authorizes_length(self):
        for session in (None, "", [], "other"):
            with self.subTest(session=session):
                self.write(
                    [
                        *self.rows,
                        {"type": "step_finish", "sessionID": session, "part": {"id": "later", "reason": "length"}},
                    ]
                )
                self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(self.path, record=self.record))
        rows = copy.deepcopy(self.rows)
        rows[-1]["part"]["sessionID"] = "other"
        self.write(rows)
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(self.path, record=self.record))

    def test_actual_independent_receipt_hold_precedes_output_routing(self):
        self.write(self.rows)
        metadata = {
            "schema": 1,
            "nonce": "fixture",
            "owner": {"pid": 1, "birth_identity": 1},
            "keeper": {"pid": 2, "birth_identity": 1},
            "provider": {"pid": 3, "birth_identity": 1},
            "receipt": str(self.path.parent / "receipt.json"),
        }
        record = {**self.record, "supervision": metadata}
        path = Path(metadata["receipt"])
        valid = {**metadata, "phase": "stopped", "cause": "controller_finished", "cleanup_error": None}
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(self.path, record=record))
        for mutation in (
            {"cleanup_error": "uncertain"},
            {"phase": "running"},
            {"cause": "owner_lost"},
            {"nonce": "foreign"},
        ):
            with self.subTest(mutation=mutation):
                path.write_text(json.dumps({**valid, **mutation}))
                self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(self.path, record=record))
        path.write_text(json.dumps(valid))
        self.assertEqual("PAUSED_OUTPUT_CAP", support.failure_status(self.path, record=record))

    def test_known_native_progress_is_metadata_but_never_terminal_authority(self):
        progress = {
            "type": "autocode_progress",
            "version": 1,
            "sessionID": "ses_limit",
            "progress": {
                "id": "progress",
                "kind": "reasoning",
                "nonwhite": True,
                "position": 1,
                "content_hash": "a" * 64,
                "delta_hash": "b" * 64,
            },
        }
        self.write([self.rows[0], progress, self.rows[-1]])
        self.assertEqual("PAUSED_OUTPUT_CAP", support.failure_status(self.path, record=self.record))
        for change in ({"version": 2}, {"progress": {}}, {"sessionID": "foreign"}):
            with self.subTest(change=change):
                self.write([self.rows[0], {**progress, **change}, self.rows[-1]])
                self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(self.path, record=self.record))
        self.write([*self.rows, progress])
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(self.path, record=self.record))

    def test_real_rate_error_preserves_its_cause_and_does_not_authorize_length(self):
        self.write([*self.rows, {"type": "error", "sessionID": "ses_limit", "error": {"message": "429 rate limit"}}])
        self.assertEqual("PAUSED_RATE_LIMIT", support.failure_status(self.path, record=self.record))


if __name__ == "__main__":
    unittest.main()

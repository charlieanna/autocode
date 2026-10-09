"""OpenCode's per-response output cap: AutoCode raises it, records it, and names it on a length stop."""
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_agent_env as agent_env
import autocode_opencode as opencode
import autocode_output_cap as output_cap
import autocode_provider_launch as provider_launch
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
        self.assertEqual({"tokens": 100000, "set_by": "operator"},
                         output_cap.recorded({VARIABLE: "100000"}, {VARIABLE: "100000"}))
        self.assertEqual({"tokens": 64000, "set_by": "autocode"},
                         output_cap.recorded({VARIABLE: "64k"}, {VARIABLE: "64000"}))
        # Scrubbed away before it reached OpenCode: OpenCode's own default applies.
        self.assertEqual({"tokens": 32000, "set_by": "opencode"}, output_cap.recorded({VARIABLE: "64000"}, {}))

    def test_a_length_stop_reports_what_its_last_response_used(self):
        message = output_cap.length_stop({"input": 9, "output": 15063, "reasoning": 16937})
        self.assertTrue(message.startswith(output_cap.LENGTH_STOP))
        self.assertIn("after 32000 output tokens in one response, 16937 of them reasoning", message)
        for tokens in (None, {}, {"output": 5}, {"output": True, "reasoning": 1}, {"output": -1, "reasoning": 2}):
            with self.subTest(tokens=tokens):
                self.assertEqual(output_cap.LENGTH_STOP + ". The attempt is incomplete; review saved work "
                                 "before recovery.", output_cap.length_stop(tokens))

    def test_explain_names_the_cap_only_for_a_length_stop(self):
        stop = output_cap.length_stop({"output": 32000, "reasoning": 32000})
        explained = output_cap.explain(stop, {"tokens": 64000, "set_by": "autocode"})
        self.assertTrue(explained.startswith(stop.rstrip(".")))
        self.assertIn(f"capped each response at 64000 tokens, reasoning included ({VARIABLE}, "
                      "AutoCode's default)", explained)
        self.assertIn(f"set {VARIABLE} to a larger number", explained)
        self.assertIn("OpenCode's default; the variable did not reach it",
                      output_cap.explain(stop, {"tokens": 32000, "set_by": "opencode"}))
        other = "OpenCode stopped after a step that requested tool calls"
        self.assertEqual(other, output_cap.explain(other, {"tokens": 64000, "set_by": "autocode"}))
        self.assertEqual(stop, output_cap.explain(stop, None))
        self.assertIsNone(output_cap.explain(None, {"tokens": 64000, "set_by": "autocode"}))


class LaunchTests(unittest.TestCase):
    def launch(self, environment):
        with patch.dict(os.environ, environment, clear=True):
            return provider_launch.prepare(
                engine="opencode", adapter=opencode, role="glm", route_role="glm", workspace=Path("/workspace"),
                run_dir=Path("/run"), session=None, model="zai-coding-plan/glm-5.3", effort="high",
                allow_write=False, planning=True, report=Path("/run/r.json"), schema=Path("/run/s.json"),
                prompt_file=Path("/run/p.md"), sandbox="read-only", transport_args=[], chatgpt=False,
                provider=None, enforce_tool_boundary=False)

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
                engine="opencode", adapter=Configured, role="sol", route_role="sol", workspace=Path("/w"),
                run_dir=Path("/r"), session=None, model="kilo/m", effort=None, allow_write=False, planning=False,
                report=None, schema=None, prompt_file=None, sandbox="read-only", transport_args=[],
                chatgpt=False, provider=None)
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
        self.assertIn(f"capped each response at 64000 tokens, reasoning included ({VARIABLE}, "
                      "AutoCode's default)", reason)
        self.assertEqual(64000, support.event_metrics(record["events"])["provider_tokens"]["output_tokens"])

    def test_an_operator_cap_reaches_opencode_without_a_pass_through(self):
        self.env[VARIABLE] = "100000"
        state, record = self.builder_stop()
        self.assertEqual({"tokens": 100000, "set_by": "operator"}, record["output_token_cap"])
        self.assertIn("after 100000 output tokens in one response", state["stop_reason"])
        self.assertNotIn(VARIABLE, record["withheld_env"])


if __name__ == "__main__":
    unittest.main()

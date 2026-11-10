"""Only a blocker the Resolver marks as a verification-command correction names the command change (#2973).

#3055 named ``--resume-paused --test-command COMMAND --regression-command COMMAND`` for every
Completion Owner blocker, so a contract contradiction lost its Resolver response. The Resolver now
marks the one blocker the command change settles (``user_request.correction``); every other blocker
keeps the Resolver response. The mark never carries commands: the operator sets them through the
existing configure_resume boundary.
"""

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_recovery_limits as recovery_limits
import autocode_report_schema as report_schema
import autocode_resolver_human as human
import autocode_role_schema as role_schema
import autocode_run_view as run_view
import autocode_util as util
import autocode_verification_config as verification_config
from units import autoresolver

from . import test_resolver_human
from .test_blocker_next_action import blocker_request

CHANGE = "--resume-paused --test-command COMMAND --regression-command COMMAND"


def correction_request(correction="verification_commands"):
    request = blocker_request()
    request.update(
        decision_needed="Set the saved test command to `python -m unittest -v tests.test_cli`?",
        impact="The saved command lacks -v, so the framework parser reads no results",
        correction=correction,
        recommended_revision=None,
    )
    return request


def publish(state, request, scope="blocker", stage="resolve"):
    human.queue(
        state,
        scope,
        {"stage": stage},
        request=request,
        evidence={"diagnosis": "The saved command is wrong", "output": "iterations/001/resolver-01.json"},
    )
    state["stages"].append(
        {"stage": "resolve", "output": "iterations/001/resolver-01.json", "exit_code": 0, "changed_files": []}
    )
    assert human.evaluate(state) == "escalate"
    return human.current(state)


class CommandCorrectionViewTests(unittest.TestCase):
    setUp = test_resolver_human.ResolverHumanTests.setUp
    contract = test_resolver_human.ResolverHumanTests.contract

    def needs(self):
        return run_view.view(self.state)["needs"]

    def test_a_marked_blocker_names_the_command_change(self):
        self.state["status"] = "PAUSED_RESOLVER"
        publish(self.state, correction_request())
        need = self.needs()
        self.assertEqual(
            ("blocker", CHANGE, "verification_commands"), (need["resolver_scope"], need["action"], need["correction"])
        )
        self.assertTrue(verification_config.command_correction(self.state))

    def test_an_unmarked_blocker_keeps_the_resolver_response(self):
        for correction in (None, "absent"):
            with self.subTest(correction=correction):
                self.setUp()
                self.state["status"] = "PAUSED_RESOLVER"
                request = correction_request(correction)
                if correction == "absent":
                    del request["correction"]
                publish(self.state, request)
                need = self.needs()
                self.assertEqual(
                    ("blocker", recovery_limits.RESPONSE_COMMAND), (need["resolver_scope"], need["action"])
                )
                self.assertNotIn("correction", need)
                self.assertFalse(verification_config.command_correction(self.state))

    def test_the_mark_counts_only_on_a_resolver_blocker(self):
        proposal = {"scope": "blocker", "origin": {"stage": "review_completion"}, "request": correction_request()}
        with patch.object(verification_config, "blocker_hold", return_value=(proposal, None)):
            self.assertFalse(verification_config.command_correction({}))
        proposal["origin"]["stage"] = "resolve"
        with patch.object(verification_config, "blocker_hold", return_value=(proposal, None)):
            self.assertTrue(verification_config.command_correction({}))
        with patch.object(verification_config, "blocker_hold", return_value=None):
            self.assertFalse(verification_config.command_correction({}))


class CommandCorrectionSchemaTests(unittest.TestCase):
    def prepare(self):
        original = role_schema.role_schema(
            {
                "type": "object",
                "properties": {"status": {"type": "string", "enum": ["CONTINUE"]}},
                "required": ["status"],
                "additionalProperties": False,
            },
            "task_planner",
        )
        source = SimpleNamespace(schema=original, metrics={}, prompt="Prelude\nCURRENT HANDOFF DATA\n{}")
        state = {
            "workspace": "/not-read",
            "settings": {"roles": {"task_planner": {"model": "fixture"}}},
            "resolution_request": {"source_revision": "source"},
        }
        with patch.object(autoresolver, "guard"), patch.object(autoresolver, "execution_request", return_value=source):
            return autoresolver.prepare(state, "resolve", Path("state.json"), Path("schemas"))

    def test_the_mark_is_a_nullable_enum_optional_in_saved_reports_and_required_on_the_wire(self):
        request = self.prepare()
        self.assertIn('correction="verification_commands"', request.prompt)
        saved = request.schema["properties"]["user_request"]
        self.assertNotIn("correction", role_schema.USER_REQUEST["properties"])
        legacy = correction_request()
        del legacy["correction"]
        util.validate_schema(legacy, saved)
        wire = util.model_output_schema(request.schema)["properties"]["user_request"]
        self.assertIn("correction", wire["required"])
        self.assertIsNone(wire["properties"]["correction"]["enum"][0], "scripted providers fill the first value")
        util.validate_schema(correction_request(), wire)
        util.validate_schema(correction_request(None), wire)
        with self.assertRaisesRegex(ValueError, "invalid enum"):
            util.validate_schema(correction_request("python -m unittest"), wire)
        with self.assertRaisesRegex(ValueError, "missing correction"):
            util.validate_schema(legacy, wire)

    def test_an_older_resolver_report_reads_the_mark_as_null(self):
        schema = self.prepare().schema
        legacy = {"user_request": correction_request()}
        del legacy["user_request"]["correction"]
        result = report_schema.normalize_absent_authority(legacy, schema, {"stage": "resolve"})
        self.assertIsNone(result["user_request"]["correction"])
        kept = report_schema.normalize_absent_authority(
            {"user_request": correction_request()}, schema, {"stage": "resolve"}
        )
        self.assertEqual("verification_commands", kept["user_request"]["correction"])
        other = report_schema.normalize_absent_authority(copy.deepcopy(legacy), schema, {"stage": "review_completion"})
        self.assertNotIn("correction", other["user_request"])


class CommandCorrectionValidationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output = Path(directory.name) / "resolver.json"
        output.write_text("{}")
        self.state = {"resolution_request": {"source_revision": "r"}, "goal_contract": {"body": {}}}
        self.record = {"source_revision": "r", "changed_files": [], "exit_code": 0, "output": str(output)}

    def validate(self, request, status="BLOCKED"):
        value = {"status": status, "diagnosis": "The saved command lacks -v", "user_request": request}
        with (
            patch.object(autoresolver, "guard"),
            patch.object(autoresolver.resolver_recovery, "validate_decision"),
        ):
            autoresolver.validate(self.state, value, self.record, Path("/ws"))

    def test_a_blocker_may_carry_the_mark(self):
        self.validate(correction_request())
        self.validate(correction_request(None))

    def test_the_mark_needs_a_blocked_blocker_without_a_criterion_revision(self):
        contradiction = correction_request()
        contradiction["kind"] = "contradiction"
        revision = correction_request()
        revision["recommended_revision"] = {"acceptance_criteria": [{"id": "C1", "criterion": "x"}]}
        cases = {
            "rework": (correction_request(), "REWORK"),
            "contradiction": (contradiction, "BLOCKED"),
            "revision": (revision, "BLOCKED"),
            "unknown": (correction_request("other"), "BLOCKED"),
        }
        for name, (request, status) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(ValueError, "verification-command correction"):
                self.validate(request, status)


if __name__ == "__main__":
    unittest.main()

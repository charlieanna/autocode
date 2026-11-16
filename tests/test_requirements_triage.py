"""Compatibility and saved-run replay coverage for the retired Requirements triage pass (#2940, #3652)."""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_requirements_handoff as handoff
import autocode_requirements_triage as triage
import autocode_taskrun as taskrun
import autopilot
from autocode_util import validate_schema

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness import clarifying_requirements_provider as clarifying
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance

JOINT = ("--joint-planning", "--no-adaptive-planning")

QUESTION = {
    "id": "Q1",
    "question": "Library, command line, or both?",
    "why": "The request does not say how the greeting is invoked.",
    "options": [],
    "proposed_default": "",
    "kind": "decision",
    "category": "requested_outcome",
    "delegable": False,
}
LARGE_BRIEF = "Build a greeting CLI. " + "Keep the supplied behavior. " * 31


def state(**extra):
    current = {
        "task": "Build a greeting CLI",
        "settings": {
            "requirements_triage": True,
            "joint_planning": True,
            "replay_recording": {"path": "recording.json"},
        },
        "stages": [],
    }
    current.update(extra)
    return current


class SettingTests(unittest.TestCase):
    def test_the_cli_accepts_compatibility_flags_and_documents_retirement(self):
        parser = argparse.ArgumentParser()
        triage.add_argument(parser)
        self.assertIsNone(parser.parse_args([]).requirements_triage)
        self.assertTrue(parser.parse_args(["--requirements-triage"]).requirements_triage)
        self.assertFalse(parser.parse_args(["--no-requirements-triage"]).requirements_triage)
        help_text = " ".join(parser.format_help().split())
        self.assertIn("compatibility no-op", help_text)
        self.assertIn("unified citation-verified Requirements pass", help_text)

    def test_packaged_imports_use_the_same_default(self):
        packaged = importlib.import_module("tools.autocode_requirements_triage")
        self.assertFalse(packaged.new_run_setting(None, True, False, task=LARGE_BRIEF))

    def test_all_new_runs_use_the_unified_pass_regardless_of_size_or_flag(self):
        for requested in (None, True, False):
            for joint in (True, False):
                for v2 in (True, False):
                    for task in (None, "Fix punctuation", LARGE_BRIEF, "word " * 121, "1. task\n" * 9):
                        with self.subTest(requested=requested, joint=joint, v2=v2, task=bool(task)):
                            self.assertFalse(triage.new_run_setting(requested, joint, v2, task=task))

    def test_resume_never_refuses_compatibility_flags(self):
        for saved in ({}, {"requirements_triage": True}, {"requirements_triage": False}):
            for requested in (None, True, False):
                with self.subTest(saved=saved, requested=requested):
                    self.assertFalse(triage.resume_refused(saved, requested))


class DueTests(unittest.TestCase):
    def test_due_only_when_replaying_the_first_dispatch_of_a_recorded_triage_run(self):
        self.assertTrue(triage.due(state()))
        self.assertFalse(
            triage.due(state(settings={"requirements_triage": True, "joint_planning": True})),
            "live and scripted runs never dispatch triage (#3652)",
        )
        for key in (
            triage.SETTING,
            "requirements_handoff",
            "requirements_history",
            "goal_contract",
            "brief_feedback",
            "user_events",
        ):
            with self.subTest(key=key):
                self.assertFalse(triage.due(state(**{key: [{"any": 1}]})))
        with self.subTest(key="answers"):
            self.assertFalse(triage.due(state(answers={"Q1": {"text": "Both"}})))
        for settings in (
            {"requirements_triage": False, "joint_planning": True, "replay_recording": {"path": "r.json"}},
            {"requirements_triage": True, "joint_planning": False, "replay_recording": {"path": "r.json"}},
            {
                "requirements_triage": True,
                "joint_planning": True,
                "planning_flow": "v2",
                "replay_recording": {"path": "r.json"},
            },
            {
                "requirements_triage": True,
                "joint_planning": True,
                "spec_first": True,
                "replay_recording": {"path": "r.json"},
            },
        ):
            with self.subTest(settings=settings):
                self.assertFalse(triage.due(state(settings=settings)))


class SchemaTests(unittest.TestCase):
    def test_the_replay_answer_schema_accepts_both_answer_shapes(self):
        full = {
            "type": "object",
            "required": ["summary", "intended_outcome", "required_behaviors", "acceptance_tests"],
            "properties": {
                "summary": {"type": "string"},
                "intended_outcome": {"type": "string"},
                "required_behaviors": {"type": "array", "items": {"type": "string"}},
                "acceptance_tests": {"type": "array", "items": {"type": "string"}},
            },
        }
        relaxed = triage.answer_schema(full)
        self.assertEqual(["summary", "intended_outcome"], relaxed["required"])
        self.assertEqual({"type": "boolean"}, relaxed["properties"]["ready"])
        self.assertIn("required_behaviors", relaxed["properties"], "a full report's fields stay known")


class ApplicationValidationTests(unittest.TestCase):
    def test_the_controller_rejects_a_malformed_draft_before_applying_it(self):
        with self.assertRaisesRegex(ValueError, r"\$\.summary: expected string"):
            autopilot.apply_planning(
                state(),
                triage.STAGE,
                {"summary": False, "required_behaviors": ["Greet"]},
                {"output": "requirements.json"},
            )

    def validate(self, value, *, current=None, stage=triage.STAGE, checked=None):
        schema = {
            "type": "object",
            "required": ["summary", "required_behaviors"],
            "properties": {
                "summary": {"type": "string"},
                "required_behaviors": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        }
        triage.validate_application(
            state() if current is None else current,
            stage,
            value,
            value if checked is None else checked,
            schema_for=lambda *_: schema,
            validate=validate_schema,
        )

    def test_an_already_loaded_small_answer_does_not_need_the_full_draft_fields(self):
        for ready, questions in ((True, []), (False, [QUESTION])):
            self.validate({"summary": "Triage", "ready": ready, "open_questions": questions})

    def test_a_drafted_triage_report_keeps_required_fields_and_field_types(self):
        full = {"summary": "Draft", "required_behaviors": ["Greet"], "ready": False}
        self.validate(full)
        for invalid in ({**full, "summary": False}, {key: value for key, value in full.items() if key != "summary"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.validate(invalid)

    def test_other_stages_and_saved_flows_keep_the_strict_full_schema(self):
        for options in (
            {"stage": "review_plan"},
            {"current": state(requirements_triage={"outcome": "ready"})},
            {"current": state(settings={"joint_planning": True})},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.validate({"summary": "Triage", "ready": True}, **options)

    def test_validation_uses_the_compatibility_checked_value(self):
        full = {"summary": "Draft", "required_behaviors": ["Greet"], "legacy": "already normalized"}
        self.validate(full, checked={key: value for key, value in full.items() if key != "legacy"})
        with self.assertRaises(ValueError):
            self.validate(full)


class RoutingTests(unittest.TestCase):
    def accept(self, value, **extra):
        current = state(**extra)
        queued = []
        handoff.accept(
            current,
            value,
            {"output": None},
            check_code_refs=mock.Mock(),
            queue=lambda *args, **kwargs: queued.append(kwargs),
        )
        return current, queued

    def triage_answer(self, *, ready, questions):
        return {
            "summary": "Triage: ask how the greeting is invoked",
            "intended_outcome": "Greet by name from the command line",
            "ready": ready,
            "open_questions": questions,
        }

    def test_a_questions_answer_pauses_through_the_ordinary_clarification_flow(self):
        current, queued = self.accept(self.triage_answer(ready=False, questions=[QUESTION]))
        self.assertEqual("questions", current[triage.SETTING]["outcome"])
        self.assertEqual(1, current[triage.SETTING]["questions"])
        report = current["requirements_handoff"]["report"]
        self.assertEqual([QUESTION], report["open_questions"])
        self.assertEqual([], report["required_behaviors"] + report["acceptance_tests"])
        self.assertEqual([QUESTION], [row for kwargs in queued for row in kwargs["questions"]])
        self.assertEqual("draft_plan", current["next_stage"], "the answer returns to Requirements")

    def test_a_ready_answer_dispatches_the_full_report_without_a_handoff(self):
        current, queued = self.accept(self.triage_answer(ready=True, questions=[]))
        self.assertEqual("ready", current[triage.SETTING]["outcome"])
        self.assertEqual("requirements_gather", current["next_stage"])
        self.assertEqual("DISCOVERING", current["phase"])
        self.assertNotIn("requirements_handoff", current)
        self.assertEqual([], queued)

    def test_neither_ready_nor_a_question_is_invalid_output(self):
        self.assertRaises(
            ValueError,
            self.accept,
            self.triage_answer(ready=False, questions=[]),
        )

    def test_a_full_report_on_the_triage_dispatch_keeps_the_ordinary_path(self):
        # Every scripted fake answers the small prompt with its usual full report: no ``ready``
        # field, no triage marker, the handoff is the report and the run plans as before.
        full = {
            "summary": "Requirements for a local greeting CLI",
            "intended_outcome": "Greet by name",
            "required_behaviors": ["prints Hello, NAME"],
            "constraints": [],
            "acceptance_tests": ["greet.py Ada prints Hello, Ada"],
            "source_refs": [],
            "proposed_assumptions": [],
            "open_questions": [],
            "requirements": [],
            "ignored_statements": [],
            "conflicts": [],
        }
        current, queued = self.accept(full)
        self.assertNotIn(triage.SETTING, current)
        self.assertEqual("draft_plan", current["next_stage"])
        self.assertEqual([], queued)

    def test_a_materialized_ready_field_on_a_full_draft_stays_on_the_ordinary_path(self):
        # 2026-11-08: provider machinery filled the optional ready field with false on a repaired
        # full draft; presence alone would have sent a complete report to triage routing.
        full = {
            "summary": "Requirements for a local greeting CLI",
            "intended_outcome": "Greet by name",
            "ready": False,
            "required_behaviors": ["prints Hello, NAME"],
            "constraints": [],
            "acceptance_tests": ["greet.py Ada prints Hello, Ada"],
            "source_refs": [],
            "proposed_assumptions": [],
            "open_questions": [],
            "requirements": [],
            "ignored_statements": [],
            "conflicts": [],
        }
        current, queued = self.accept(full)
        self.assertNotIn(triage.SETTING, current)
        self.assertEqual("draft_plan", current["next_stage"])
        self.assertEqual([], queued)

    def test_strict_wire_schema_triage_answers_route_as_triage_even_with_drafted_arrays(self):
        ready_with_drafts = {
            **self.triage_answer(ready=True, questions=[]),
            "requirements": [{"id": "R1", "text": "t", "source_quote": "q"}],
            "required_behaviors": ["b"],
            "acceptance_tests": ["a"],
            "source_refs": ["handoff task text"],
            "project_conventions": ["x" * 130],
        }
        current, queued = self.accept(ready_with_drafts)
        self.assertEqual("ready", current[triage.SETTING]["outcome"])
        self.assertEqual("requirements_gather", current["next_stage"])
        self.assertNotIn("requirements_handoff", current)
        self.assertEqual([], queued)

        questions_with_drafts = {
            **self.triage_answer(ready=False, questions=[QUESTION]),
            "requirements": [{"id": "R1", "text": "t", "source_quote": "q"}],
            "required_behaviors": ["b"],
            "acceptance_tests": ["a"],
        }
        current_q, queued_q = self.accept(questions_with_drafts)
        self.assertEqual("questions", current_q[triage.SETTING]["outcome"])
        report_q = current_q["requirements_handoff"]["report"]
        self.assertEqual([], report_q["requirements"])
        self.assertEqual([], report_q["required_behaviors"])
        self.assertEqual([], report_q["acceptance_tests"])
        self.assertEqual([QUESTION], [row for kwargs in queued_q for row in kwargs["questions"]])

        # Boundary checks for is_answer mutability/operator invariants:
        self.assertFalse(
            triage.is_answer(
                {
                    "summary": "Ask",
                    "intended_outcome": "Greet",
                    "open_questions": [QUESTION],
                    "requirements": [],
                    "required_behaviors": [],
                    "acceptance_tests": [],
                }
            )
        )
        self.assertFalse(triage.is_answer({"ready": 1, "open_questions": [], "required_behaviors": ["b"]}))
        for key in ("requirements", "required_behaviors", "acceptance_tests"):
            with self.subTest(key=key):
                self.assertFalse(triage.is_answer({"ready": False, "open_questions": [], key: ["x"]}))


class EffortTests(unittest.TestCase):
    def decision(self, configured, declared, stage="requirements_gather"):
        return triage.effort_decision(
            state(
                settings={
                    "requirements_triage": True,
                    "joint_planning": True,
                    "replay_recording": {"path": "recording.json"},
                    "roles": {"requirements": {"model": "m", "reasoning_effort": configured}},
                    "declared_variants": {"m": declared} if declared else None,
                }
            ),
            stage,
            configured,
        )

    def test_the_triage_dispatch_runs_at_the_floor_level_the_model_serves(self):
        decision = self.decision("medium", ["low", "medium", "high"])
        self.assertEqual("low", decision["level"])
        self.assertTrue(decision["lowered"])
        self.assertEqual(triage.REASON, decision["reason"])
        self.assertEqual("medium", decision["ceiling"])

    def test_the_floor_or_unknown_levels_keep_the_configured_level(self):
        self.assertIsNone(self.decision("low", ["low", "high"]), "already the floor")
        kept = self.decision("medium", None)
        self.assertEqual("medium", kept["level"])
        self.assertFalse(kept["lowered"])

    def test_only_the_triage_dispatch_considers_a_level(self):
        self.assertIsNone(self.decision("medium", ["low", "high"], stage="draft_plan"))


@unittest.skipUnless(sys.platform == "linux", "supervised fake providers require supported Linux")
class TriageCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="requirements-triage-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scenario = catalog.load("greenfield-greeting-cli")

    def start(self, *planning, fault=None):
        if fault:
            self.scenario = dataclasses.replace(self.scenario, fake_fault=fault)
        else:
            self.scenario = catalog.load("greenfield-greeting-cli")
        project = without_maintenance(materialize(self.scenario.seed, self.root / "project"))
        flags, provider_env = fake_setup(self.scenario, self.root, self.scenario.dir / "reference")
        environment = {**provider_env, "AUTOCODE_HOME": str(self.root / "registry"), "PYTHONDONTWRITEBYTECODE": "1"}
        paths = environment["PATH"].split(os.pathsep)
        environment["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        run = taskrun.TaskRun.start(
            project, self.scenario.brief, options=(*flags, *planning), env=environment, timeout=180, cwd=self.root
        )
        return run, run.advance_until_input()

    @staticmethod
    def stages(run):
        """Stages that ran, from their retained artifacts (iterations/NNN/<slug>-NN.jsonl)."""
        slugs = {"requirements-gather": "requirements_gather", "discovery": "draft_plan"}
        found = [
            path.stem.rsplit("-", 1)[0] for path in sorted(Path(run.run_dir).glob("iterations/*/*-[0-9][0-9].jsonl"))
        ]
        return [slugs.get(name, name) for name in found]

    def saved(self, run):
        return json.loads((Path(run.run_dir) / "state.json").read_text(encoding="utf-8"))

    def test_the_compatibility_triage_flag_uses_the_unified_first_pass_and_asks_blocking_questions(self):
        run, view = self.start(*JOINT, "--requirements-triage", fault="clarifying_requirements")
        need = view.get("needs") or {}
        self.assertEqual("answer", need.get("kind"), {key: view.get(key) for key in ("status", "next_stage", "needs")})
        self.assertEqual([clarifying.QUESTION_ID], [row["id"] for row in need["questions"]])
        self.assertEqual(["requirements_gather"], self.stages(run), "one unified Requirements pass ran")
        self.assertNotIn(triage.SETTING, self.saved(run))
        timing = view.get("interaction_timing") or {}
        self.assertIsNotNone(timing.get("first_question_at"), "the question is marked before any planning dispatch")
        run.answer(clarifying.QUESTION_ID, "Both", resolver_token=need["resolver_token"])
        view = run.advance_until_input()
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), view.get("needs"))
        self.assertEqual(2, self.stages(run).count("requirements_gather"), "clarification pass, then answered report")
        self.assertIn("draft_plan", self.stages(run))

    def test_a_fresh_fake_run_with_no_blocking_question_runs_the_full_report_at_once(self):
        run, view = self.start(*JOINT)
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), view.get("needs"))
        self.assertEqual(1, self.stages(run).count("requirements_gather"), "one ordinary validated handoff")
        self.assertNotIn(triage.SETTING, self.saved(run))
        self.assertFalse(view["done"])
        self.assertNotIn("builder", self.stages(run), "implementation still needs exact plan approval")

    def test_an_explicit_triage_flag_runs_one_unified_full_pass_instead_of_two(self):
        run, view = self.start(*JOINT, "--requirements-triage")
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), view.get("needs"))
        self.assertEqual(1, self.stages(run).count("requirements_gather"), "compatibility no-op: one full pass")
        self.assertNotIn(triage.SETTING, self.saved(run))

    def test_a_small_ambiguous_brief_still_asks_and_uses_the_answer(self):
        run, view = self.start(*JOINT, fault="clarifying_requirements")
        need = view.get("needs") or {}
        self.assertEqual("answer", need.get("kind"), view.get("stop_reason"))
        self.assertEqual([clarifying.QUESTION_ID], [row["id"] for row in need["questions"]])
        self.assertEqual(["requirements_gather"], self.stages(run))
        self.assertNotIn(triage.SETTING, self.saved(run))
        run.answer(clarifying.QUESTION_ID, "Both", resolver_token=need["resolver_token"])
        view = run.advance_until_input()
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), view.get("needs"))
        self.assertEqual(2, self.stages(run).count("requirements_gather"))
        self.assertFalse(view["done"])

    def test_the_opt_out_keeps_the_single_full_dispatch(self):
        run, view = self.start(*JOINT, "--no-requirements-triage", fault="clarifying_requirements")
        need = view.get("needs") or {}
        self.assertEqual("answer", need.get("kind"), {key: view.get(key) for key in ("status", "next_stage", "needs")})
        self.assertEqual([clarifying.QUESTION_ID], [row["id"] for row in need["questions"]])
        self.assertEqual(1, self.stages(run).count("requirements_gather"), "the ordinary report asked inside itself")
        run.answer(clarifying.QUESTION_ID, "Both", resolver_token=need["resolver_token"])
        view = run.advance_until_input()
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), view.get("needs"))
        # The questions-only re-run flow this fault has always used: the second dispatch carries the answer.
        self.assertEqual(2, self.stages(run).count("requirements_gather"))
        self.assertNotIn(triage.SETTING, self.saved(run))


if __name__ == "__main__":
    unittest.main()

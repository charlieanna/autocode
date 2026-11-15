"""Spec-first runs stop after Requirements until the user confirms (#1200).

The CLI tests drive a real run through the task-run interface with the scenario harness's fake
provider; the public status view and the provider's stage sequence are the oracles.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_requirements_review as review
import autocode_taskrun as taskrun

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness import clarifying_requirements_provider as clarifying
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance

JOINT = ("--joint-planning", "--no-adaptive-planning")


class SettingTests(unittest.TestCase):
    def catalog_bug(self, *, external=True):
        report = {
            "job": "bugfix",
            "intended_outcome": "Show the operation and mapped result for new history entries",
            "required_behaviors": ["Preserve stored history"],
            "acceptance_tests": ["A new entry shows its operation and mapped result"],
            "open_questions": [],
            "workspace_scope": {
                "spans_repositories": external,
                "external_parts": [{"target": "history", "work": "Check displayed values"}] if external else [],
            },
        }
        return {"settings": {"spec_first": True}, "requirements_handoff": {"report": report}, "next_stage": review.NEXT}

    def test_catalog_bug_confirms_understanding_before_scope_routing_or_planning(self):
        for external in (False, True):
            with (
                self.subTest(external=external),
                patch("autocode_catalog_context.context", return_value={"repositories": [{"name": "history"}]}),
            ):
                current = self.catalog_bug(external=external)
                original = json.loads(json.dumps(current))
                question = review.review_question(current)
                self.assertEqual([question], review.questions(current))
                self.assertEqual(original, current)
                self.assertTrue(review.accepts(current, {"scope": "clarification", "questions": [question]}))
                current["answers"] = {question["id"]: {"text": "plan"}}
                remaining = review.questions(current)
                self.assertEqual(bool(external), bool(remaining))
                if external:
                    self.assertTrue(remaining[0]["id"].startswith("workspace-scope-"))

    def test_a_line_break_inside_a_listed_row_does_not_break_the_list(self):
        """An acceptance example quoting file content ("  Ana \\n") broke the summary onto a new screen
        line, "file '  Ana" then "' greets 'Hello, Ana!'" (2026-11-08 live run). Examples keep the break
        visible as \\n; prose rows fold it into a space; each row stays one list item."""
        current = self.catalog_bug(external=False)
        report = current["requirements_handoff"]["report"]
        report["acceptance_tests"] = ["A name file '  Ana \n' greets 'Hello, Ana!'"]
        report["required_behaviors"] = ["Read the name from the file,\n  stripped of surrounding space"]
        with patch("autocode_catalog_context.context", return_value={"repositories": [{"name": "history"}]}):
            text = review.review_question(current)["question"]
        self.assertIn("  - A name file '  Ana \\n' greets 'Hello, Ana!'", text.splitlines())
        self.assertIn("  - Read the name from the file, stripped of surrounding space", text.splitlines())
        self.assertNotIn("' greets 'Hello, Ana!'", text.splitlines())

    def test_catalog_bug_correction_returns_to_requirements_before_any_scope_routing(self):
        with patch("autocode_catalog_context.context", return_value={"repositories": [{"name": "history"}]}):
            current = self.catalog_bug()
            question = review.review_question(current)
            current["answers"] = {question["id"]: {"text": "Also cover the Japanese registries"}}
            review.after_answers(current)
            self.assertEqual(review.STAGE, current["next_stage"])
            self.assertEqual([], review.questions(current))

    def test_confirmation_publishes_remaining_scope_before_the_standard_planner(self):
        from unittest.mock import Mock

        with patch("autocode_catalog_context.context", return_value={"repositories": [{"name": "history"}]}):
            current = self.catalog_bug()
            current["settings"]["implementation_flow"] = "standard"
            current["workflow"] = {"kind": "bugfix"}
            question = review.review_question(current)
            current["answers"] = {question["id"]: {"text": "plan"}}
            queued = Mock()
            review.after_answers(current, queue=queued)
            queued.assert_called_once()
            self.assertEqual("clarification", queued.call_args.args[1])
            self.assertTrue(queued.call_args.kwargs["questions"][0]["id"].startswith("workspace-scope-"))

    def test_confirmed_members_and_joint_tasks_do_not_repeat_the_source_confirmation(self):
        for role in ("member", "joint"):
            with (
                self.subTest(role=role),
                patch("autocode_catalog_context.context", return_value={"repositories": [{"name": "history"}]}),
                patch(
                    "autocode_coordination_context.context", return_value={"role": role} if role == "member" else None
                ),
                patch("autocode_coordination_context.joint_planning", return_value=role == "joint"),
            ):
                current = self.catalog_bug(external=False)
                self.assertEqual([], review.questions(current))

    def test_off_unless_asked_and_refused_where_requirements_gather_does_not_run(self):
        self.assertFalse(review.new_run_setting(None, True, False))
        self.assertFalse(review.new_run_setting(False, True, False))
        self.assertTrue(review.new_run_setting(True, True, False))
        for joint, v2 in ((False, False), (True, True)):
            with self.assertRaisesRegex(ValueError, "--spec-first needs joint planning"):
                review.new_run_setting(True, joint, v2)

    def test_a_resume_cannot_change_the_saved_flow(self):
        self.assertFalse(review.resume_refused({}, None))
        self.assertFalse(review.resume_refused({"spec_first": True}, True))
        self.assertTrue(review.resume_refused({}, True))
        self.assertTrue(review.resume_refused({"spec_first": True}, False))

    def test_the_question_shows_the_handoff_and_a_new_round_asks_again(self):
        report = {
            "intended_outcome": "Greet by name",
            "required_behaviors": ["prints Hello, NAME"],
            "acceptance_tests": ["greet.py Ada prints Hello, Ada"],
            "ignored_statements": ["colour output"],
            "open_questions": [],
        }
        state = {"settings": {"spec_first": True}, "requirements_handoff": {"report": report}}
        first = review.review_question(state)
        self.assertTrue(first["id"].startswith(review.PREFIX))
        self.assertEqual("plan", first["proposed_default"])
        for text in (
            "Greet by name",
            "prints Hello, NAME",
            "greet.py Ada prints Hello, Ada",
            "Other source statements:",
        ):
            self.assertIn(text, first["question"])
        state["requirements_history"] = [{"report": report}]
        self.assertNotEqual(first["id"], review.review_question(state)["id"])
        self.assertEqual([], review.questions({**state, "settings": {}}))

    def test_other_source_statements_show_their_saved_treatment_without_claiming_scope_exclusion(self):
        report = {
            "intended_outcome": "Fix page counting",
            "constraints": ["Small fix only; retain existing tests"],
            "ignored_statements": [
                "This is a small fix; don't turn it into a project.",
                "This authorizes investigation/planning only, not an implementation plan.",
                "Do not add colour output.",
            ],
            "summary": "The small-fix and approval statements remain constraints; colour output is excluded.",
        }
        state = {"settings": {"spec_first": True}, "requirements_handoff": {"report": report}}
        before = json.loads(json.dumps(report))
        question = review.review_question(state)
        self.assertNotIn("Left out:", question["question"])
        self.assertIn("Other source statements:", question["question"])
        self.assertIn("Notes: " + report["summary"], question["question"])
        for statement in report["ignored_statements"]:
            self.assertIn(statement, question["question"])
        self.assertIn(report["constraints"][0], question["question"])
        self.assertEqual(before, report, "the confirmation must not rewrite the saved handoff")
        self.assertFalse(question["delegable"])
        self.assertEqual("plan", question["proposed_default"])

    def test_legacy_source_statements_remain_visible_without_an_invented_explanation(self):
        report = {"intended_outcome": "Greet", "ignored_statements": ["Required examples:"]}
        state = {"requirements_handoff": {"report": report}}
        question = review.review_question(state)["question"]
        self.assertIn("Other source statements:", question)
        self.assertIn("Required examples:", question)
        self.assertNotIn("Left out:", question)
        self.assertNotIn("Notes:", question)
        report.update(ignored_statements=[], summary="No other statements need explanation")
        question = review.review_question(state)["question"]
        self.assertNotIn("Other source statements:", question)
        self.assertNotIn("Notes:", question)

    def test_a_questions_only_handoff_waits_on_the_default_flow_and_requirements_runs_again(self):
        question = {
            "id": "Q1",
            "question": "Library, command line, or both?",
            "why": "The request does not say how the greeting is invoked.",
            "options": [],
            "proposed_default": "",
            "kind": "decision",
            "category": "requested_outcome",
            "delegable": False,
        }
        report = {
            "intended_outcome": "Greet by name",
            "required_behaviors": [],
            "acceptance_tests": [],
            "open_questions": [question],
        }
        state = {"settings": {}, "requirements_handoff": {"report": report}, "next_stage": review.NEXT}
        self.assertEqual([question], review.questions(state), "the handoff's own question, no review question")
        self.assertTrue(review.accepts(state, {"scope": "clarification", "questions": [question]}))
        state["answers"] = {"Q1": {"text": "Both"}}
        self.assertEqual([], review.questions(state))
        review.after_answers(state)
        self.assertEqual(review.STAGE, state["next_stage"], "the answer is requirements input, not planning input")
        # A complete handoff with an open question also stops on the default flow and refreshes Requirements (#3603).
        complete = {**report, "required_behaviors": ["prints Hello, NAME"], "acceptance_tests": ["greet.py Ada"]}
        pending = {"settings": {}, "requirements_handoff": {"report": complete}, "next_stage": review.NEXT}
        self.assertEqual([question], review.questions(pending))
        self.assertTrue(review.accepts(pending, {"scope": "clarification", "questions": [question]}))
        review.after_answers(pending)
        self.assertEqual(review.NEXT, pending["next_stage"], "no answer yet: stays waiting on the question")
        pending["answers"] = {"Q1": {"text": "Command line"}}
        self.assertEqual([], review.questions(pending))
        review.after_answers(pending)
        self.assertEqual(review.STAGE, pending["next_stage"])

    def test_requirements_clarification_precedes_scope_routing_without_deciding_scope(self):
        question = {"id": "example", "question": "Which action has the incorrect result?"}
        for spec_first, complete in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(spec_first=spec_first, complete=complete):
                report = {
                    "intended_outcome": "Correct the mapped result",
                    "open_questions": [question],
                    "required_behaviors": ["Correct the result"] if complete else [],
                    "acceptance_tests": ["Result is mapped"] if complete else [],
                    "workspace_scope": {
                        "spans_repositories": True,
                        "external_parts": [
                            {"target": "history", "work": "Correct history", "source_quote": "Correct history"}
                        ],
                    },
                }
                state = {
                    "settings": {"spec_first": spec_first},
                    "requirements_handoff": {"report": report},
                    "next_stage": review.NEXT,
                }
                scope_before = review.scope_question.view(state)
                self.assertEqual([question], review.questions(state))
                self.assertTrue(review.accepts(state, {"scope": "clarification", "questions": [question]}))
                state["answers"] = {"example": {"text": "Domain info"}}
                self.assertTrue(review.questions(state)[0]["id"].startswith("workspace-scope-"))
                review.after_answers(state)
                self.assertEqual(review.STAGE, state["next_stage"])
                self.assertEqual(scope_before, review.scope_question.view(state))
                state["goal_contract"] = {"revision": 1}
                state["answers"] = {}
                self.assertTrue(review.questions(state)[0]["id"].startswith("workspace-scope-"))

    @staticmethod
    def _ledger_stub(calls: list[dict] | None = None):
        def unconfirmed(assumptions, *, confirmed_ids, verify, cites_person=lambda ref: False):
            confirmed = set(confirmed_ids)
            if calls is not None:
                calls.append({"assumptions": list(assumptions), "confirmed_ids": frozenset(confirmed)})
            rows = []
            for raw in assumptions or []:
                if isinstance(raw, str):
                    rows.append({"id": None, "text": raw, "legacy": True, "reason": "needs confirmation"})
                    continue
                if raw.get("id") in confirmed:
                    continue
                if raw.get("category") == "technical" and verify(raw.get("convention_ref", "")) is None:
                    continue
                if cites_person(raw.get("convention_ref", "")):
                    continue
                rows.append({**raw, "legacy": False, "reason": "needs confirmation"})
            return rows

        return types.SimpleNamespace(
            unconfirmed=unconfirmed,
            verifier=lambda state, stage="requirements_gather": lambda ref: None if ref == "app.py:1" else "unread",
            cites_person=lambda state: lambda ref: ref in (state.get("answers") or {}),
        )

    def test_follow_up_turn_retaining_earlier_contract_stops_for_questions_and_spec_first_review(self):
        import autocode_follow_up as follow_up
        import autocode_requirements_chat as chat

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            state = {
                "status": "TASK_COMPLETE",
                "phase": "COMPLETE",
                "next_stage": None,
                "completed_at": "t0",
                "task": "Build greet.py",
                "settings": {"spec_first": True, "roles": {"requirements": {"engine": "codex", "model": "m"}}},
                "workspace": str(workspace),
                "workflow": {"kind": "build", "then": "draft_plan", "requirements_approved": True},
                "goal_contract": {"revision": 1, "hash": "turn1hash", "body": {}},
                "requirements_handoff": {
                    "output": "iterations/001/requirements-gather-01.json",
                    "report": {"intended_outcome": "Build greet.py", "assistant_reply": "Turn 1 reply"},
                },
                "stages": [],
                "user_events": [],
            }
            follow_up.accept(state, "Also support --json output", workspace, "t1")
            self.assertTrue(chat.enabled(state))
            # Turn 1's leftover handoff does not masquerade as Turn 2's before Requirements runs.
            self.assertEqual([], review.questions(state))
            self.assertEqual("", chat.reply(state))
            self.assertFalse(review.current_turn_has_contract(state))
            self.assertEqual({}, review.current_turn_handoff(state))

            question = {"id": "Q-format", "question": "Array or object JSON?"}
            state["requirements_history"] = [state["requirements_handoff"]]
            state["requirements_handoff"] = {
                "output": "iterations/002/requirements-gather-01.json",
                "answers_seen": [],
                "report": {
                    "intended_outcome": "Support --json output",
                    "required_behaviors": ["Emit JSON when --json is passed"],
                    "acceptance_tests": ["greet.py --json Ada"],
                    "open_questions": [question],
                    "assistant_reply": "Should --json emit an object or array?",
                },
            }
            state["next_stage"] = review.NEXT
            self.assertEqual([question], review.questions(state))
            state["answers"] = {"Q-format": {"text": "Object"}}
            review.after_answers(state)
            self.assertEqual(review.STAGE, state["next_stage"])

            # Refreshed Turn 2 handoff with no remaining open questions stops at the spec-first review.
            state["requirements_history"].append(state["requirements_handoff"])
            state["requirements_handoff"] = {
                "output": "iterations/002/requirements-gather-02.json",
                "answers_seen": ["Q-format"],
                "report": {
                    "job": "build",
                    "intended_outcome": "Support --json object output",
                    "required_behaviors": ["Emit {'greeting': ...} when --json is passed"],
                    "acceptance_tests": ["greet.py --json Ada prints JSON object"],
                    "open_questions": [],
                },
            }
            state["next_stage"] = review.NEXT
            confirm = review.review_question(state)
            self.assertEqual([confirm], review.questions(state))
            state["answers"][confirm["id"]] = {"text": "plan"}
            state["pending_questions"] = []
            review.after_answers(state)
            self.assertTrue(chat.start_after_approval(state))
            self.assertEqual(review.NEXT, state["next_stage"])

            # Once Turn 2's Planner installs revision 2, current_turn_has_contract becomes True.
            state["goal_contract"] = {"revision": 2, "hash": "turn2hash", "body": {}}
            self.assertTrue(review.current_turn_has_contract(state))

    def test_unconfirmed_assumptions_stop_without_spec_first_list_all_with_rationale_and_record_ids(self):
        import autocode_requirements_chat as chat
        import autocode_requirements_handoff as handoff

        calls: list[dict] = []
        assumptions = [
            {
                "id": f"A{index}",
                "text": f"Default behavior choice {index}",
                "kind": "inferable",
                "category": "behavior",
                "convention_ref": "app.py:1",
                "rationale": f"Reason number {index}",
                "supports": [],
            }
            for index in range(1, 11)
        ]
        behaviors = [f"Behavior {index}" for index in range(1, 11)]
        task = "Add export command."
        value = {
            "job": "build",
            "summary": "Export command with 10 assumptions.",
            "intended_outcome": task,
            "requirements": [{"id": "R1", "text": task, "source_quote": task}],
            "required_behaviors": behaviors,
            "constraints": [],
            "acceptance_tests": ["Export writes output"],
            "source_refs": ["task"],
            "proposed_assumptions": assumptions,
            "open_questions": [],
            "ignored_statements": [],
            "conflicts": [],
            "proposed_reframes": [],
        }
        state = {
            "task": task,
            "settings": {},
            "stages": [],
            "workflow": {"kind": None, "then": "draft_plan"},
        }
        queued: list[dict] = []
        with patch.dict(sys.modules, {"autocode_assumption_ledger": self._ledger_stub(calls)}):
            handoff.accept(
                state,
                value,
                {"output": "iter/001/req-01.json"},
                check_code_refs=lambda *_: None,
                queue=lambda *args, **kwargs: queued.append(kwargs),
            )
            self.assertIsNone(state["workflow"]["kind"], "must wait for assumption confirmation before starting job")
            self.assertEqual(1, len(queued))
            [question] = queued[0]["questions"]
            self.assertTrue(question["id"].startswith(review.PREFIX))
            self.assertIn("Assumptions:", question["question"])
            # Every assumption (all 10 > SHOWN=8) and its rationale is shown; Behaviors still truncates at SHOWN.
            for index in range(1, 11):
                self.assertIn(f"Default behavior choice {index} (why: Reason number {index})", question["question"])
            self.assertIn("Behavior 8", question["question"])
            self.assertNotIn("Behavior 9", question["question"])
            self.assertEqual(1, question["question"].count("… and 2 more"))

            # A correction refreshes Requirements without confirming the assumptions.
            correction_state = json.loads(json.dumps(state))
            correction_state["answers"] = {question["id"]: {"text": "Use TSV for choice 1"}}
            review.after_answers(correction_state)
            self.assertEqual(review.STAGE, correction_state["next_stage"])
            self.assertEqual(frozenset(), review.confirmed_assumption_ids(correction_state))

            # Approving ('plan') records the confirmed assumption ids and starts the named job.
            state["answers"] = {question["id"]: {"text": "plan"}}
            state["pending_questions"] = []
            review.after_answers(state)
            expected_ids = frozenset(f"A{index}" for index in range(1, 11))
            self.assertEqual(expected_ids, review.confirmed_assumption_ids(state))
            self.assertEqual([], review.unconfirmed_assumptions(state))
            self.assertEqual(expected_ids, calls[-1]["confirmed_ids"])
            self.assertTrue(chat.start_after_approval(state))
            self.assertEqual("build", state["workflow"]["kind"])
            self.assertEqual(review.NEXT, state["next_stage"])

            # A refreshed handoff keeps ids whose text is unchanged, but asks again when an assumption is reworded.
            reworded = [{**assumptions[0], "text": "Changed behavior choice 1"}, *assumptions[1:]]
            state["requirements_history"] = [state["requirements_handoff"]]
            state["requirements_handoff"] = {
                "output": "iter/001/req-02.json",
                "answers_seen": list(state["answers"]),
                "report": {**value, "proposed_assumptions": reworded},
            }
            self.assertEqual(expected_ids - {"A1"}, review.confirmed_assumption_ids(state))
            self.assertEqual(["A1"], [row["id"] for row in review.unconfirmed_assumptions(state)])
            self.assertEqual(1, len(review.questions(state)))
            self.assertTrue(review.questions(state)[0]["id"].startswith(review.PREFIX))

    def test_clear_runs_and_skip_requirements_advance_with_zero_stops(self):
        import autocode_requirements_handoff as handoff

        task = "Add export command."
        verified_technical = [
            {
                "id": "A1",
                "text": "Follow app.py logging helper",
                "kind": "inferable",
                "category": "technical",
                "convention_ref": "app.py:1",
                "rationale": "Existing module uses it",
                "supports": [],
            }
        ]
        unconfirmed_behavior = [{**verified_technical[0], "id": "A2", "category": "behavior"}]
        base = {
            "job": "build",
            "summary": "Clear export handoff.",
            "intended_outcome": task,
            "requirements": [{"id": "R1", "text": task, "source_quote": task}],
            "required_behaviors": ["Export writes output"],
            "constraints": [],
            "acceptance_tests": ["Export exits 0"],
            "source_refs": ["task"],
            "open_questions": [],
            "ignored_statements": [],
            "conflicts": [],
            "proposed_reframes": [],
        }
        with patch.dict(sys.modules, {"autocode_assumption_ledger": self._ledger_stub()}):
            for assumptions, settings in (
                ([], {}),
                (verified_technical, {}),
                (unconfirmed_behavior, {"skip_requirements": True}),
            ):
                with self.subTest(assumptions=assumptions, settings=settings):
                    state = {
                        "task": task,
                        "settings": settings,
                        "stages": [],
                        "workflow": {"kind": None, "then": "draft_plan"},
                    }
                    queued: list[dict] = []
                    handoff.accept(
                        state,
                        {**base, "proposed_assumptions": assumptions},
                        {"output": "iter/001/req-01.json"},
                        check_code_refs=lambda *_: None,
                        queue=lambda *args, **kwargs: queued.append(kwargs),
                    )
                    self.assertEqual([], queued)
                    self.assertEqual("build", state["workflow"]["kind"])
                    self.assertEqual(review.NEXT, state["next_stage"])
                    if settings.get("skip_requirements"):
                        self.assertEqual(
                            "skip_requirements",
                            state["requirements_handoff"]["confirmed_assumptions"]["source"],
                        )
                        self.assertEqual(frozenset({"A2"}), review.confirmed_assumption_ids(state))

    def test_summary_confirmation_question_never_triggers_double_confirmation(self):
        import autocode_requirements_handoff as handoff

        task = "Add export command."
        redundant = {
            "id": "Q-confirm",
            "question": "Is this right?",
            "why": "Confirm summary",
            "options": [],
            "proposed_default": "yes",
            "kind": "decision",
            "category": "requested_outcome",
            "delegable": False,
        }
        fork = {
            **redundant,
            "id": "Q-fork",
            "question": "Is UTF-8 encoding right for CSV exports?",
            "options": ["UTF-8", "UTF-16"],
        }
        base = {
            "job": "build",
            "summary": "Export handoff.",
            "intended_outcome": task,
            "requirements": [{"id": "R1", "text": task, "source_quote": task}],
            "required_behaviors": ["Export writes output"],
            "constraints": [],
            "acceptance_tests": ["Export exits 0"],
            "source_refs": ["task"],
            "proposed_assumptions": [],
            "ignored_statements": [],
            "conflicts": [],
            "proposed_reframes": [],
        }
        # Spec-first + redundant "Is this right?" stops ONCE at requirements-review-*, never Q-confirm first (#3304).
        spec_state = {
            "task": task,
            "settings": {"spec_first": True},
            "stages": [],
            "workflow": {"kind": None, "then": "draft_plan"},
        }
        queued: list[dict] = []
        handoff.accept(
            spec_state,
            {**base, "open_questions": [redundant]},
            {"output": "iter/001/req-01.json"},
            check_code_refs=lambda *_: None,
            queue=lambda *args, **kwargs: queued.append(kwargs),
        )
        self.assertEqual(1, len(queued))
        self.assertEqual(1, len(queued[0]["questions"]))
        self.assertTrue(queued[0]["questions"][0]["id"].startswith(review.PREFIX))
        self.assertEqual([], spec_state["requirements_handoff"]["report"]["open_questions"])
        self.assertEqual(["Q-confirm"], spec_state["requirements_handoff"]["dropped_confirmation_questions"])

        # Real product fork question is preserved and stops the run before planning.
        fork_state = {
            "task": task,
            "settings": {},
            "stages": [],
            "workflow": {"kind": None, "then": "draft_plan"},
        }
        fork_queued: list[dict] = []
        handoff.accept(
            fork_state,
            {**base, "open_questions": [fork, {**redundant, "question": "Does this look right?"}]},
            {"output": "iter/001/req-01.json"},
            check_code_refs=lambda *_: None,
            queue=lambda *args, **kwargs: fork_queued.append(kwargs),
        )
        self.assertEqual([[fork]], [row["questions"] for row in fork_queued])


class RequirementsReviewCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="requirements-review-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.scenario = catalog.load("greenfield-greeting-cli")

    def start(self, *planning):
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

    def review_need(self, view):
        need = view.get("needs") or {}
        self.assertEqual("answer", need.get("kind"), {key: view.get(key) for key in ("status", "next_stage", "needs")})
        ids = [row["id"] for row in need["questions"]]
        self.assertTrue(ids[-1].startswith(review.PREFIX), ids)
        self.assertEqual("draft_plan", view["next_stage"])
        return need, ids[-1]

    def test_without_the_setting_requirements_flow_straight_into_planning(self):
        run, view = self.start(*JOINT)
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), view.get("needs"))

    def test_authenticated_joint_build_reaches_plan_without_a_requirements_confirmation(self):
        from unittest.mock import patch

        import autocode_coordination_store as coordination

        environment = {"AUTOCODE_HOME": str(self.root / "registry")}
        with patch.dict(os.environ, environment):
            anchor = str(self.root / "intake/.autocode/runs/fix")
            project = without_maintenance(materialize(self.scenario.seed, coordination.root(anchor) / "joint-1"))
            run_dir = project / ".autocode/runs/joint-regression"
            coordination.save(
                {
                    "version": 1,
                    "anchor": anchor,
                    "members": [],
                    "joint": {"workspace": str(project), "run_dir": str(run_dir), "started": False},
                    "pending": {"operation": "joint_plan_start"},
                }
            )
        flags, provider_env = fake_setup(self.scenario, self.root, self.scenario.dir / "reference")
        environment = {**provider_env, **environment, "PYTHONDONTWRITEBYTECODE": "1"}
        environment["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), environment["PATH"]])
        run = taskrun.TaskRun.start(
            project,
            self.scenario.brief,
            options=(*flags, *JOINT, "--spec-first", "--workflow", "build"),
            env=environment,
            timeout=180,
            cwd=self.root,
            run_dir=run_dir,
        )
        view = run.advance_until_input()
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), view.get("needs"))
        self.assertIn("requirements_gather", self.stages(run))
        self.assertIn("draft_plan", self.stages(run))
        self.assertFalse(view.get("approved_contract"))
        self.assertFalse(any(path.name.startswith("builder-") for path in run.run_dir.glob("iterations/*/*")))

    def test_without_the_setting_a_handoff_that_only_asks_waits_for_the_answer_then_plans(self):
        # Live (program plan, "build an adaptive learning engine", 2026-10-20): three good questions with empty
        # acceptance tests were refused as invalid output on the default flow until a person resumed the run.
        self.scenario = dataclasses.replace(self.scenario, fake_fault="clarifying_requirements")
        run, view = self.start(*JOINT)
        need = view.get("needs") or {}
        self.assertEqual("answer", need.get("kind"), {key: view.get(key) for key in ("status", "next_stage", "needs")})
        self.assertEqual([clarifying.QUESTION_ID], [row["id"] for row in need["questions"]])
        self.assertEqual("Requirements", view["progress"]["stage"])
        self.assertEqual("Waiting for you", view["progress"]["headline"])
        self.assertNotIn("draft_plan", self.stages(run))
        run.answer(clarifying.QUESTION_ID, "Both", resolver_token=need["resolver_token"])
        view = run.advance_until_input()
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), view.get("needs"))
        stages = self.stages(run)
        self.assertEqual(2, stages.count("requirements_gather"), stages)
        self.assertIn("draft_plan", stages)

    def test_plan_releases_the_planner_after_the_user_sees_the_requirements(self):
        run, view = self.start(*JOINT, "--spec-first")
        need, question = self.review_need(view)
        self.assertIn("Here’s what I understand", need["questions"][-1]["question"])
        self.assertNotIn("draft_plan", self.stages(run))
        view = run.answer(question, "plan", resolver_token=need["resolver_token"])
        self.assertEqual("continue", view["needs"]["kind"])
        self.assertIn("--no-chat", view["next_command"], "the client must be able to advance the approved brief")
        view = run.advance_until_input()
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), view.get("needs"))
        stages = self.stages(run)
        self.assertEqual(1, stages.count("requirements_gather"), stages)
        self.assertIn("draft_plan", stages)

    def test_a_correction_runs_requirements_again_and_asks_again(self):
        run, view = self.start(*JOINT, "--spec-first")
        need, question = self.review_need(view)
        run.answer(question, "Also greet in French when --fr is given", resolver_token=need["resolver_token"])
        view = run.advance_until_input()
        again, second = self.review_need(view)
        self.assertNotEqual(question, second)
        stages = self.stages(run)
        self.assertEqual(2, stages.count("requirements_gather"), stages)
        self.assertNotIn("draft_plan", stages)

    def test_adaptive_planning_does_not_skip_a_spec_first_runs_requirements(self):
        run, view = self.start("--joint-planning", "--spec-first")
        self.review_need(view)
        self.assertIn("requirements_gather", self.stages(run))


if __name__ == "__main__":
    unittest.main()

"""AutoPlanner step 1: question/assumption classification schema, requirement
preservation on excluded/superseded and on a refreshed handoff, and the
--delegate-all / --reject-assumption mutators. See the design discussion on
GitHub issue #62 for the full rationale; this covers only the schema,
validator and CLI-mutator layer (no obligation-discharge gating yet)."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_assumption_ledger as ledger
import autocode_code_citations as code_citations
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
from goal_fixtures import body

from .test_planner_invariants import state


class QuestionSchemaTests(unittest.TestCase):
    def test_kind_category_delegable_are_optional_on_question(self):
        self.assertEqual(["id", "question", "why", "options", "proposed_default"], goals.QUESTION["required"])
        self.assertIn("kind", goals.QUESTION["properties"])
        self.assertIn("category", goals.QUESTION["properties"])
        self.assertIn("delegable", goals.QUESTION["properties"])
        self.assertEqual(["discoverable", "inferable", "decision"], goals.QUESTION["properties"]["kind"]["enum"])

    def test_assumption_evidence_fields_are_optional_and_five_questions_fit(self):
        # #3603: a technical default names the code it rests on; a Planner assumption names its category.
        self.assertEqual({"type": "string"}, goals.QUESTION["properties"]["convention_ref"])
        self.assertNotIn("convention_ref", goals.QUESTION["required"])
        decision = goals.BODY_SCHEMA["properties"]["accepted_assumptions"]["items"]
        self.assertEqual(list(goals.ASSUMPTION_CATEGORIES), decision["properties"]["category"]["enum"])
        self.assertNotIn("category", decision["required"])
        self.assertNotIn("convention_ref", decision["required"])
        self.assertEqual(5, goals.BODY_SCHEMA["properties"]["open_blocking_questions"]["maxItems"])
        legacy = goals.LEGACY_BODY_SCHEMA["properties"]["accepted_assumptions"]["items"]
        self.assertNotIn("category", legacy["properties"], "sealed v3 briefs keep their contract")

    def test_categories_and_normalization_have_one_home(self):
        self.assertIs(ledger.ASSUMPTION_CATEGORIES, goals.ASSUMPTION_CATEGORIES)
        self.assertIs(ledger.NON_INFERABLE_CATEGORIES, goals.NON_INFERABLE_CATEGORIES)
        self.assertIs(ledger.normalize, goals.normalize_assumption)


class AssumptionValidationTests(unittest.TestCase):
    def test_legacy_string_assumption_normalizes_without_structural_checks(self):
        rows = goals.validate_assumptions(["Use a CLI"], known_requirement_ids=set())
        self.assertEqual(
            [
                {
                    "id": None,
                    "text": "Use a CLI",
                    "kind": "inferable",
                    "category": "requested_outcome",
                    "convention_ref": "",
                    "rationale": "",
                    "supports": [],
                    "legacy": True,
                }
            ],
            rows,
        )

    def test_inferable_assumption_needs_convention_ref_and_rationale(self):
        base = {"id": "A1", "text": "A CLI is sufficient", "kind": "inferable", "category": "technical", "supports": []}
        with self.assertRaisesRegex(ValueError, "convention_ref"):
            goals.validate_assumptions([{**base, "convention_ref": "", "rationale": ""}], set())
        with self.assertRaisesRegex(ValueError, "rationale"):
            goals.validate_assumptions([{**base, "convention_ref": "tools/x.py:10", "rationale": ""}], set())
        rows = goals.validate_assumptions(
            [{**base, "convention_ref": "tools/x.py:10", "rationale": "Existing CLI convention"}], set()
        )
        self.assertEqual("A1", rows[0]["id"])
        self.assertFalse(rows[0]["legacy"])

    def test_non_inferable_category_cannot_be_marked_inferable(self):
        row = {
            "id": "A1",
            "text": "Prefer the cheaper model",
            "kind": "inferable",
            "category": "cost",
            "convention_ref": "tools/x.py:1",
            "rationale": "r",
            "supports": [],
        }
        with self.assertRaisesRegex(ValueError, "cannot be inferable"):
            goals.validate_assumptions([row], set())

    def test_a_protected_choice_is_never_an_assumption_whatever_its_kind(self):
        # #3603: the category check sat inside `kind == "inferable"`, so kind=decision skipped it.
        for kind in ("inferable", "decision", "discoverable"):
            for category in sorted(goals.NON_INFERABLE_CATEGORIES):
                with self.subTest(kind=kind, category=category):
                    row = {
                        "id": "A1",
                        "text": "Prefer the cheaper model",
                        "kind": kind,
                        "category": category,
                        "convention_ref": "tools/x.py:1",
                        "rationale": "r",
                        "supports": [],
                    }
                    with self.assertRaisesRegex(ValueError, "must be an open decision question"):
                        goals.validate_assumptions([row], set())

    def test_a_discoverable_assumption_names_its_evidence_too(self):
        base = {"id": "A1", "text": "Tests use unittest", "kind": "discoverable", "category": "technical"}
        with self.assertRaisesRegex(ValueError, "convention_ref"):
            goals.validate_assumptions([{**base, "convention_ref": "", "rationale": "r", "supports": []}], set())
        with self.assertRaisesRegex(ValueError, "rationale"):
            goals.validate_assumptions([{**base, "convention_ref": "t.py:1", "rationale": "", "supports": []}], set())

    def test_engineering_convention_must_cite_verified_read_code_under_every_kind(self):
        # #3603: kind="decision" previously bypassed convention_ref/rationale, and any non-empty phrase passed.
        with tempfile.TemporaryDirectory() as raw:
            ws = Path(raw).resolve()
            (ws / "app.py").write_text("x = 1\n")
            (ws / "unread.py").write_text("y = 2\n")
            events = ws / "events.jsonl"
            events.write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "x1",
                            "type": "command_execution",
                            "command": "cat app.py",
                            "exit_code": 0,
                            "aggregated_output": "x = 1\n",
                        },
                    }
                )
                + "\n"
            )
            current = {
                "task": "Update app.",
                "workspace": str(ws),
                "run_dir": str(ws),
                "stages": [{"stage": "requirements_gather", "events": str(events)}],
            }
            for kind in ("inferable", "discoverable", "decision"):
                base = {
                    "id": "A1",
                    "text": "Tests use unittest",
                    "kind": kind,
                    "category": "technical",
                    "rationale": "Existing test runner",
                    "supports": [],
                }
                with self.subTest(kind=kind):
                    for bad in ("", "standard convention", "unread.py:1", "app.py:99"):
                        with self.assertRaisesRegex(ValueError, "convention_ref"):
                            goals.check_requirement_handoff(
                                current,
                                {
                                    "requirements": [],
                                    "ignored_statements": [],
                                    "proposed_assumptions": [{**base, "convention_ref": bad}],
                                },
                            )
                    with self.assertRaisesRegex(ValueError, "rationale"):
                        goals.check_requirement_handoff(
                            current,
                            {
                                "requirements": [],
                                "ignored_statements": [],
                                "proposed_assumptions": [{**base, "convention_ref": "app.py:1", "rationale": ""}],
                            },
                        )
                    goals.check_requirement_handoff(
                        current,
                        {
                            "requirements": [],
                            "ignored_statements": [],
                            "proposed_assumptions": [{**base, "convention_ref": "app.py:1"}],
                        },
                    )

    def test_greenfield_accepts_engineering_convention_while_keeping_behavior_for_the_person(self):
        # Empty workspace has no files to cite: technical conventions stand, behavior still needs confirmation.
        with tempfile.TemporaryDirectory() as empty:
            current = {"task": "Create a CLI.", "workspace": empty, "stages": []}
            assumptions = [
                {
                    "id": "A1",
                    "text": "Tests live in tests/",
                    "kind": "inferable",
                    "category": "technical",
                    "convention_ref": "",
                    "rationale": "Standard Python layout",
                    "supports": [],
                },
                {
                    "id": "A2",
                    "text": "Empty input exits 0",
                    "kind": "inferable",
                    "category": "behavior",
                    "convention_ref": "task",
                    "rationale": "Quiet no-op",
                    "supports": [],
                },
            ]
            goals.check_requirement_handoff(
                current, {"requirements": [], "ignored_statements": [], "proposed_assumptions": assumptions}
            )
            pending = ledger.unconfirmed(
                assumptions,
                confirmed_ids=(),
                verify=ledger.verifier(current),
                cites_person=ledger.cites_person(current),
            )
            self.assertEqual(["A2"], [row["id"] for row in pending])

    def test_inference_is_separate_from_delegation_so_behavior_defaults_stay_delegable(self):
        # Unattended --skip-requirements relies on behavior questions with a default remaining delegable.
        q = {
            "id": "Q1",
            "question": "What should empty input print?",
            "why": "Unstated edge case",
            "options": ["Nothing", "Error"],
            "proposed_default": "Nothing",
            "kind": "decision",
            "category": "behavior",
            "delegable": True,
        }
        goals.check_delegable([q])
        current = state()
        current["pending_questions"] = [q]
        self.assertEqual(["Q1"], [row["id"] for row in goals.delegable(current)])

    def test_a_behavior_assumption_is_shown_for_confirmation_not_refused(self):
        # The model's kind label does not decide who confirms it: the ledger does, and the gate asks.
        row = {
            "id": "A1",
            "text": "Empty input prints nothing",
            "kind": "inferable",
            "category": "behavior",
            "convention_ref": "cli.py:3",
            "rationale": "The CLI prints nothing for no rows",
            "supports": [],
        }
        rows = goals.validate_assumptions([row, {**row, "id": "A2", "kind": "decision", "convention_ref": ""}], set())
        pending = ledger.unconfirmed(rows, confirmed_ids=(), verify=lambda ref: None)
        self.assertEqual(["A1", "A2"], [r["id"] for r in pending])

    def test_assumption_cannot_support_an_unknown_requirement(self):
        row = {"id": "A1", "text": "x", "kind": "decision", "category": "cost", "supports": ["R9"]}
        with self.assertRaisesRegex(ValueError, "unknown requirement"):
            goals.validate_assumptions([row], {"R1"})

    def test_duplicate_assumption_id_is_rejected(self):
        rows = [
            {"id": "A1", "text": "x", "kind": "decision", "category": "cost", "supports": []},
            {"id": "A1", "text": "y", "kind": "decision", "category": "cost", "supports": []},
        ]
        with self.assertRaisesRegex(ValueError, "Duplicate assumption id"):
            goals.validate_assumptions(rows, set())


def planner_row(**extra):
    return {"text": "Empty input prints an empty table", "basis": "agent_proposed", "answer_id": "", **extra}


class PlannerAssumptionTests(unittest.TestCase):
    """#3603: the Planner's own assumptions meet the ledger's rule, not only Requirements'."""

    def setUp(self):
        patcher = mock.patch.object(
            code_citations,
            "verify_citation",
            create=True,
            side_effect=lambda state, stage, ref, **_: None if ref.startswith("greet.py") else f"{ref} was never read",
        )
        self.verify = patcher.start()
        self.addCleanup(patcher.stop)

    def check(self, *rows):
        draft = body()
        draft["accepted_assumptions"] = list(rows)
        lifecycle.check_planner_assumptions(state(), draft)

    def test_a_planner_behavior_assumption_must_be_asked(self):
        for category in ("behavior", "other", "requested_outcome", "cost"):
            with self.subTest(category=category):
                with self.assertRaisesRegex(ValueError, "open_blocking_question"):
                    self.check(planner_row(category=category, convention_ref="greet.py:1"))

    def test_a_planner_convention_needs_code_a_planning_stage_read(self):
        with self.assertRaisesRegex(ValueError, "lib.py:2 was never read"):
            self.check(planner_row(text="Helpers live in lib.py", category="technical", convention_ref="lib.py:2"))
        self.check(planner_row(text="Output goes through print()", category="technical", convention_ref="greet.py:1"))
        self.assertEqual(None, self.verify.call_args.args[1], "any planning stage of the turn may have read it")

    def test_user_backed_and_unclassified_rows_are_not_judged(self):
        self.check(
            planner_row(basis="original_request", category="behavior"),
            planner_row(category="behavior", basis="user_feedback"),
            planner_row(),  # a saved or older draft has no category
        )
        self.verify.assert_not_called()

    def test_install_refuses_a_planner_draft_with_an_unasked_behavior(self):
        for origin in sorted(lifecycle.PLANNER_DRAFT_ORIGINS):
            with self.subTest(origin=origin):
                current = state()
                draft = body()
                draft["accepted_assumptions"] = [planner_row(category="behavior", convention_ref="")]
                before = copy.deepcopy(current)
                with self.assertRaisesRegex(ValueError, "behavior choice is the person's"):
                    lifecycle.install_draft(current, draft, origin=origin)
                self.assertEqual(before, current)

    def test_a_persons_own_edit_is_not_held_to_the_planner_rule(self):
        current = state()
        draft = body()
        draft["accepted_assumptions"] = [planner_row(category="behavior")]
        lifecycle.install_draft(current, draft, origin="user_cli_edit")
        self.assertEqual("behavior", current["goal_contract"]["body"]["accepted_assumptions"][0]["category"])

    def test_handoff_assumption_confirmed_by_the_person_may_be_carried_unless_rejected(self):
        import autocode_requirements_review as requirements_review

        current = state()
        current["requirements_handoff"] = {
            "report": {
                "proposed_assumptions": [
                    {
                        "id": "A1",
                        "text": "Empty input prints an empty table",
                        "kind": "inferable",
                        "category": "behavior",
                        "convention_ref": "greet.py:1",
                        "rationale": "Quiet empty output",
                        "supports": [],
                    }
                ]
            },
            "output": "req.json",
        }
        draft = body()
        draft["accepted_assumptions"] = [planner_row(category="behavior", convention_ref="")]
        with self.assertRaisesRegex(ValueError, "behavior choice is the person's"):
            lifecycle.check_planner_assumptions(current, draft)
        review_id = requirements_review.review_question(current)["id"]
        current["answers"] = {review_id: {"text": "plan", "kind": "user"}}
        lifecycle.check_planner_assumptions(current, draft)
        current["deferred_obligations"] = [{"id": "ob-1", "assumption_id": "A1", "status": "open"}]
        with self.assertRaisesRegex(ValueError, "behavior choice is the person's"):
            lifecycle.check_planner_assumptions(current, draft)


class RequirementPreservationTests(unittest.TestCase):
    def test_excluded_requirement_needs_more_than_a_scope_exclusion_match(self):
        current = state()
        current["requirements_handoff"] = {
            "report": {"requirements": [{"id": "R1", "text": "Print greeting", "source_quote": "Print greeting"}]},
            "output": "x.json",
        }
        contract = body()
        contract["scope_exclusions"] = ["Print greeting"]
        trace = [{"requirement_id": "R1", "disposition": "excluded", "evidence": "Print greeting"}]
        with self.assertRaisesRegex(ValueError, "cannot be excluded without a saved user event"):
            goals.check_requirement_trace(current, {"requirement_trace": trace}, contract)

    def test_excluded_requirement_with_a_saved_feedback_citation_is_accepted(self):
        current = state()
        current["requirements_handoff"] = {
            "report": {"requirements": [{"id": "R1", "text": "Print greeting", "source_quote": "Print greeting"}]},
            "output": "x.json",
        }
        evidence = "Print greeting; dropped per feedback-1"
        current["brief_feedback"] = [{"id": "feedback-1", "text": "Drop the greeting requirement"}]
        current["user_events"] = list(current["brief_feedback"])
        contract = body()
        contract["scope_exclusions"] = [evidence]
        trace = [{"requirement_id": "R1", "disposition": "excluded", "evidence": evidence}]
        goals.check_requirement_trace(current, {"requirement_trace": trace}, contract)  # does not raise

    def test_brief_declared_exclusion_quotes_may_exclude_without_follow_up_event(self):
        for quote in (
            "Concurrent builders and real compiler processes are out of scope.",
            "No HTTP API, external bank, or floating-point currency is in scope.",
            "Intro. No background daemon or external queue is required.",
            "Filesystem permission errors need not be mapped to exit 2.",
            "Non-goals: distributed caching.",
        ):
            with self.subTest(quote=quote):
                current = state()
                current["requirements_handoff"] = {
                    "report": {"requirements": [{"id": "R1", "text": "Excluded item", "source_quote": quote}]},
                    "output": "x.json",
                }
                contract = body()
                contract["scope_exclusions"] = [quote]
                trace = [{"requirement_id": "R1", "disposition": "excluded", "evidence": quote}]
                goals.check_requirement_trace(current, {"requirement_trace": trace}, contract)

    def test_brief_declared_exclusion_still_requires_scope_exclusion_match_and_rejects_fabricated_events(self):
        quote = "Concurrent builders are out of scope."
        current = state()
        current["requirements_handoff"] = {
            "report": {"requirements": [{"id": "R1", "text": "No concurrency", "source_quote": quote}]},
            "output": "x.json",
        }
        contract = body()
        contract["scope_exclusions"] = ["Different text"]
        trace = [{"requirement_id": "R1", "disposition": "excluded", "evidence": quote}]
        with self.assertRaisesRegex(ValueError, "not present in scope_exclusions"):
            goals.check_requirement_trace(current, {"requirement_trace": trace}, contract)

        for fabricated in ("out of scope per feedback-99", "out of scope per intervention-1"):
            with self.subTest(fabricated=fabricated):
                contract["scope_exclusions"] = [fabricated]
                trace = [{"requirement_id": "R1", "disposition": "excluded", "evidence": fabricated}]
                with self.assertRaisesRegex(ValueError, "cannot be excluded without a saved user event"):
                    goals.check_requirement_trace(current, {"requirement_trace": trace}, contract)

        for non_exclusion in (None, "   ", "No empty job ids are accepted"):
            with self.subTest(non_exclusion=non_exclusion):
                current["requirements_handoff"]["report"]["requirements"][0]["source_quote"] = non_exclusion
                contract["scope_exclusions"] = ["Excluded item"]
                trace = [{"requirement_id": "R1", "disposition": "excluded", "evidence": "Excluded item"}]
                with self.assertRaisesRegex(ValueError, "cannot be excluded without a saved user event"):
                    goals.check_requirement_trace(current, {"requirement_trace": trace}, contract)

    def test_refreshed_handoff_dropping_a_requirement_without_citation_is_rejected(self):
        current = state("Print greeting. Reject empty input.")
        current["requirements_handoff"] = {
            "report": {
                "requirements": [
                    {"id": "R1", "text": "Print a greeting", "source_quote": "Print greeting."},
                    {"id": "R2", "text": "Reject empty input", "source_quote": "Reject empty input."},
                ]
            },
            "output": "prev.json",
        }
        new_report = {
            "requirements": [{"id": "R2", "text": "Reject empty input", "source_quote": "Reject empty input."}],
            "ignored_statements": [],
            "proposed_assumptions": [],
            "open_questions": [],
        }
        with self.assertRaisesRegex(ValueError, "dropped requirement R1"):
            goals.check_requirement_handoff(current, new_report)

    def test_refreshed_handoff_dropping_a_requirement_with_a_saved_citation_is_accepted(self):
        current = state("Print greeting. Reject empty input.")
        current["requirements_handoff"] = {
            "report": {
                "requirements": [
                    {"id": "R1", "text": "Print a greeting", "source_quote": "Print greeting."},
                    {"id": "R2", "text": "Reject empty input", "source_quote": "Reject empty input."},
                ]
            },
            "output": "prev.json",
        }
        current["brief_feedback"] = [{"id": "feedback-1", "text": "Drop the greeting requirement"}]
        current["user_events"] = list(current["brief_feedback"])
        new_report = {
            "requirements": [{"id": "R2", "text": "Reject empty input", "source_quote": "Reject empty input."}],
            "ignored_statements": [],
            "proposed_assumptions": [],
            "open_questions": [],
            "ignored_requirements": [
                {
                    "requirement_id": "R1",
                    "reason": "User asked to drop it",
                    "basis": "user_feedback",
                    "event_id": "feedback-1",
                }
            ],
        }
        goals.check_requirement_handoff(current, new_report)  # does not raise

    def test_refreshed_handoff_omission_with_an_unresolvable_event_is_rejected(self):
        current = state("Print greeting. Reject empty input.")
        current["requirements_handoff"] = {
            "report": {
                "requirements": [
                    {"id": "R1", "text": "Print a greeting", "source_quote": "Print greeting."},
                    {"id": "R2", "text": "Reject empty input", "source_quote": "Reject empty input."},
                ]
            },
            "output": "prev.json",
        }
        new_report = {
            "requirements": [{"id": "R2", "text": "Reject empty input", "source_quote": "Reject empty input."}],
            "ignored_statements": [],
            "proposed_assumptions": [],
            "open_questions": [],
            "ignored_requirements": [
                {
                    "requirement_id": "R1",
                    "reason": "Agent decided it was unnecessary",
                    "basis": "user_feedback",
                    "event_id": "feedback-nonexistent",
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "needs a saved user answer or feedback event"):
            goals.check_requirement_handoff(current, new_report)


def shown(state):
    """The runner's writer boundary publishes the queued request, then the user sees the goal."""
    lifecycle.human.evaluate(state)
    lifecycle.present(state)
    return goals.token(state["goal_contract"])


def _contract(**overrides):
    contract = {
        "task_id": "task-1",
        "revision": 1,
        "body": {"open_blocking_questions": []},
        "approval_status": "draft",
        "approval_event": None,
    }
    contract.update(overrides)
    # AutoResolver only publishes against a sealed contract.
    contract["hash"] = goals.s.digest({key: contract[key] for key in ("task_id", "revision", "body")})
    return contract


def ask(current, questions):
    """A clarification stop: the draft declares the questions and the runner queues them."""
    current["goal_contract"] = _contract(
        **{**current.get("goal_contract", {}), "body": {"open_blocking_questions": list(questions)}}
    )
    lifecycle.human.queue(current, "clarification", {"stage": "draft_plan"}, questions=questions)


def ask_permission(current):
    """A post-approval WAITING_FOR_USER stop: the runner queues a scoped permission request."""
    lifecycle.human.queue(
        current,
        "permission",
        {"stage": "builder"},
        request={
            "kind": "permission",
            "discovered": "A write outside the workspace is needed",
            "impact": "The build cannot finish without it",
            "decision_needed": "Allow the write?",
            "options": ["Allow", "Deny"],
            "proposed_delta": "",
        },
    )


def await_approval(current):
    """The goal-approval stop (non-joint planning, so no final-plan evidence is required)."""
    current["settings"]["joint_planning"] = False
    lifecycle.human.queue(current, "goal_approval", {"stage": "draft_plan"}, status="AWAITING_GOAL_APPROVAL")


class DelegateAllTests(unittest.TestCase):
    def test_blocks_atomically_when_any_pending_question_cannot_be_bulk_delegated(self):
        current = state()
        ask(
            current,
            [
                {"id": "Q1", "question": "q1", "why": "w", "options": [], "proposed_default": "yes", "delegable": True},
                {"id": "Q2", "question": "q2", "why": "w", "options": [], "proposed_default": "", "delegable": True},
            ],
        )
        with self.assertRaisesRegex(ValueError, "Q2"):
            goals.delegate_all(current, shown(current))
        self.assertEqual({}, current.get("answers", {}))

    def test_blocks_when_delegable_is_absent_even_with_a_default(self):
        current = state()
        ask(current, [{"id": "Q1", "question": "q1", "why": "w", "options": [], "proposed_default": "yes"}])
        with self.assertRaisesRegex(ValueError, "Q1"):
            goals.delegate_all(current, shown(current))

    def test_delegates_every_pending_question_and_invalidates_approval(self):
        current = state()
        question = {
            "id": "Q1",
            "question": "q1",
            "why": "w",
            "options": [],
            "proposed_default": "yes",
            "delegable": True,
            "category": "technical",
        }
        current["goal_contract"] = _contract(approval_status="approved", approval_event={"kind": "goal_approval"})
        ask(current, [question])
        goals.delegate_all(current, shown(current))
        self.assertEqual("yes", current["answers"]["Q1"]["text"])
        self.assertEqual("delegated", current["answers"]["Q1"]["kind"])
        self.assertEqual([], current["pending_questions"])
        self.assertEqual("draft", current["goal_contract"]["approval_status"])
        self.assertIsNone(current["goal_contract"]["approval_event"])


class RejectAssumptionTests(unittest.TestCase):
    def test_cost_category_becomes_a_human_decision_obligation(self):
        current = state()
        current["goal_contract"] = _contract(approval_status="approved", approval_event={"kind": "goal_approval"})
        current["requirements_handoff"] = {
            "report": {
                "proposed_assumptions": [
                    {
                        "id": "A1",
                        "text": "Use the cheaper model",
                        "kind": "inferable",
                        "category": "cost",
                        "convention_ref": "",
                        "rationale": "",
                        "supports": ["R1"],
                    }
                ]
            },
            "output": "x.json",
        }
        ask_permission(current)
        displayed = shown(current)
        self.assertEqual("WAITING_FOR_USER", current["status"])
        obligation = goals.reject_assumption(current, "A1", displayed)
        self.assertEqual("human_decision", obligation["kind"])
        self.assertEqual(["R1"], obligation["supports"])
        self.assertEqual("reject_assumption", current["user_events"][-1]["kind"])
        self.assertEqual("draft", current["goal_contract"]["approval_status"])
        self.assertIsNone(current["goal_contract"]["approval_event"])
        # Rejection moved status to RUNNING; reach a later published checkpoint with
        # the same handoff still current, and confirm the duplicate is caught.
        self.assertEqual("RUNNING", current["status"])
        ask(current, [{"id": "Q1", "question": "q1", "why": "w", "options": [], "proposed_default": ""}])
        with self.assertRaisesRegex(ValueError, "already has an open rejection"):
            goals.reject_assumption(current, "A1", shown(current))

    def test_technical_category_becomes_a_remediation_obligation(self):
        current = state()
        current["goal_contract"] = _contract()
        current["requirements_handoff"] = {
            "report": {
                "proposed_assumptions": [
                    {
                        "id": "A2",
                        "text": "A CLI is sufficient",
                        "kind": "inferable",
                        "category": "technical",
                        "convention_ref": "tools/x.py:1",
                        "rationale": "existing pattern",
                        "supports": [],
                    }
                ]
            },
            "output": "x.json",
        }
        await_approval(current)
        obligation = goals.reject_assumption(current, "A2", shown(current))
        self.assertEqual("remediation", obligation["kind"])
        self.assertEqual([], obligation["supports"])

    def test_behavior_and_other_categories_become_human_decision_obligations(self):
        for category in ("behavior", "other"):
            with self.subTest(category=category):
                current = state()
                current["goal_contract"] = _contract()
                current["requirements_handoff"] = {
                    "report": {
                        "proposed_assumptions": [
                            {
                                "id": "A3",
                                "text": "Empty input prints nothing",
                                "kind": "inferable",
                                "category": category,
                                "convention_ref": "tools/x.py:1",
                                "rationale": "quiet default",
                                "supports": ["R1"],
                            }
                        ]
                    },
                    "output": "x.json",
                }
                await_approval(current)
                obligation = goals.reject_assumption(current, "A3", shown(current))
                self.assertEqual("human_decision", obligation["kind"])
                self.assertEqual(["R1"], obligation["supports"])

    def test_legacy_or_unknown_assumption_id_cannot_be_rejected(self):
        current = state()
        current["goal_contract"] = _contract()
        current["requirements_handoff"] = {"report": {"proposed_assumptions": ["Use a local CLI"]}, "output": "x.json"}
        await_approval(current)
        with self.assertRaisesRegex(ValueError, "Unknown or legacy"):
            goals.reject_assumption(current, "A1", shown(current))

    def test_rejection_requires_an_open_conversation_checkpoint(self):
        current = state()
        current["status"] = "RUNNING"
        current["goal_contract"] = _contract()
        current["requirements_handoff"] = {
            "report": {
                "proposed_assumptions": [
                    {
                        "id": "A1",
                        "text": "x",
                        "kind": "inferable",
                        "category": "technical",
                        "convention_ref": "tools/x.py:1",
                        "rationale": "r",
                        "supports": [],
                    }
                ]
            },
            "output": "x.json",
        }
        with self.assertRaisesRegex(ValueError, "open conversation checkpoint"):
            goals.reject_assumption(current, "A1", shown(current))


class UnifiedFirstPassRequirementsTests(unittest.TestCase):
    """#3652: headless --no-chat and interactive --spec-first share one citation-verified first pass."""

    LARGE_BRIEF = "Build a greeting CLI. " + "Keep the supplied behavior and exit codes exact. " * 30

    def _first_pass_state(self, workspace: Path, *, spec_first: bool, triage_flag: bool | None = None):
        import types

        import autocode_configure as configure

        args = types.SimpleNamespace(
            auto_select=None,
            spec_first=spec_first,
            requirements_triage=triage_flag,
        )
        settings = {
            "joint_planning": True,
            "roles": {
                "requirements": {"engine": "codex", "model": "gpt-6-astra", "reasoning_effort": "medium"},
            },
            "declared_variants": {"gpt-6-astra": ["low", "medium", "high"]},
        }
        configure._new_run_policy_settings(settings, args, True, False, task=self.LARGE_BRIEF)
        return {
            "version": 3,
            "task_id": "unified-pass-1",
            "task": self.LARGE_BRIEF,
            "workspace": str(workspace),
            "run_dir": str(workspace),
            "settings": settings,
            "stages": [],
            "answers": {},
            "user_events": [],
        }

    def test_headless_and_spec_first_share_the_full_first_pass_requirements_contract(self):
        import autocode_review_effort as review_effort
        import autopilot
        from autocode_util import model_output_schema
        from units import autoplanner

        with tempfile.TemporaryDirectory() as raw:
            ws = Path(raw).resolve()
            (ws / "greet.py").write_text("print('hi')\n")
            spec_state = self._first_pass_state(ws, spec_first=True)
            spec_req = autoplanner.prepare(
                copy.deepcopy(spec_state), "requirements_gather", ws / "state.json", ws / "schemas"
            )
            spec_without_chat_reply = copy.deepcopy(spec_req.schema)
            spec_without_chat_reply["properties"].pop("assistant_reply", None)
            if "required" in spec_without_chat_reply:
                spec_without_chat_reply["required"] = [
                    k for k in spec_without_chat_reply["required"] if k != "assistant_reply"
                ]
            for triage_flag in (None, True, False):
                with self.subTest(triage_flag=triage_flag):
                    headless = self._first_pass_state(ws, spec_first=False, triage_flag=triage_flag)
                    self.assertFalse(headless["settings"]["requirements_triage"])
                    req = autoplanner.prepare(
                        copy.deepcopy(headless), "requirements_gather", ws / "state.json", ws / "schemas"
                    )
                    self.assertNotIn("ready", req.schema["properties"])
                    self.assertEqual(spec_without_chat_reply, req.schema)
                    wire = model_output_schema(req.schema)
                    for field in (
                        "ambiguity_audit",
                        "source_refs",
                        "proposed_assumptions",
                        "project_conventions",
                        "requirements",
                        "required_behaviors",
                        "acceptance_tests",
                    ):
                        self.assertIn(field, wire["required"])
                    self.assertTrue(
                        {"convention_ref", "rationale"}
                        <= set(wire["properties"]["proposed_assumptions"]["items"]["required"])
                    )
                    self.assertNotIn("Do not\ninspect repository files for this pass", req.prompt)
                    level, choice = review_effort.launch(
                        headless,
                        "requirements_gather",
                        "requirements",
                        False,
                        headless["settings"]["roles"]["requirements"],
                    )
                    self.assertEqual("medium", level)
                    self.assertIsNone(choice)
                    with self.assertRaises(ValueError):
                        autopilot.apply_planning(
                            copy.deepcopy(headless),
                            "requirements_gather",
                            {"summary": "Triage", "intended_outcome": "Greet", "ready": True, "open_questions": []},
                            {"output": str(ws / "requirements.json")},
                        )

    def test_compatibility_flags_never_refuse_start_or_resume(self):
        import types

        import autocode_configure as configure
        import autocode_requirements_triage as triage

        for requested in (None, True, False):
            for joint in (True, False):
                for v2 in (True, False):
                    with self.subTest(requested=requested, joint=joint, v2=v2):
                        self.assertFalse(triage.new_run_setting(requested, joint, v2, task=self.LARGE_BRIEF))
            for saved in ({}, {"requirements_triage": True}, {"requirements_triage": False}):
                with self.subTest(saved=saved, requested=requested):
                    self.assertFalse(triage.resume_refused(saved, requested))
                    args = types.SimpleNamespace(
                        auto_select=None,
                        builder_strong_model=None,
                        adaptive_planning=None,
                        compact_bugfix_planning=None,
                        skip_requirements=False,
                        skip_to_build=False,
                        spec_first=None,
                        requirements_triage=requested,
                    )
                    configure._refuse_new_run_flags(args, {"settings": dict(saved)}, started=True, single_model=None)


if __name__ == "__main__":
    unittest.main()

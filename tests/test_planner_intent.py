"""Regression cases from live AutoPlanner trials: clarification and intent changes."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_plan_retention as retention
import autocode_run_view as run_view
import autopilot
from goal_fixtures import body
from units import autoplanner as planner

from .test_planner_invariants import state


def draft_report(draft):
    return {
        "contract": draft,
        "summary": "Draft",
        "code_refs": [],
        "alternatives": [],
        "uncertainties": [],
        "contract_changes": [],
        "requirement_trace": [],
    }


def handoff(task="Build an agent dashboard"):
    return {
        "summary": "Requirements",
        "intended_outcome": task,
        "required_behaviors": [task],
        "constraints": [],
        "acceptance_tests": ["Verify requested behavior"],
        "source_refs": [],
        "proposed_assumptions": [],
        "open_questions": [],
        "requirements": [{"id": "R1", "text": task, "source_quote": task}],
        "ignored_statements": [],
        "conflicts": [],
    }


class PlannerIntentTests(unittest.TestCase):
    def test_ambiguous_idea_cannot_produce_tasks_until_answered(self):
        current = state("Build something like Cursor but better for multiple agents")
        value = draft_report(body(questions=True))
        with self.assertRaisesRegex(ValueError, "clarification-only"):
            autopilot.apply_planning(current, "astra_discovery", value, {"output": "draft.json"})
        self.assertNotIn("goal_contract", current)
        value["contract"]["milestones"] = []
        value["contract"]["technical_approach"] = []
        autopilot.apply_planning(current, "astra_discovery", value, {"output": "questions.json"})
        # The question is queued; the runner's writer boundary publishes it to the user.
        self.assertEqual("RESOLVER_PENDING", current["status"])
        lifecycle.human.evaluate(current)
        self.assertEqual("WAITING_FOR_USER", current["status"])
        self.assertEqual([], current["goal_contract"]["body"]["milestones"])
        goals.answer(current, "Q1", "CLI")
        autopilot.apply_planning(current, "astra_discovery", draft_report(body()), {"output": "plan.json"})
        self.assertEqual("astra_challenge", current["next_stage"])
        self.assertTrue(current["goal_contract"]["body"]["milestones"])
        self.assertFalse(goals.approved(current))

    def test_late_reviewer_question_is_not_erased_by_revision_routing(self):
        current = state()
        lifecycle.install_draft(current, body(), origin="glm_draft")
        current["planning"]["reports"]["astra_challenge"] = {"report": {"concerns": []}}
        draft = body(questions=True)
        value = {
            "contract": draft,
            "summary": "Need a decision",
            "code_refs": [],
            "responses": [],
            "contract_changes": [],
            "requirement_trace": [],
        }
        autopilot.apply_planning(current, "glm_revise", value, {"output": "revision.json"})
        self.assertEqual("RESOLVER_PENDING", current["status"])
        lifecycle.human.evaluate(current)
        self.assertEqual("WAITING_FOR_USER", current["status"])
        self.assertEqual("Q1", current["pending_questions"][0]["id"])
        self.assertNotEqual("astra_finalize", current["next_stage"])

    def test_saved_correction_supersedes_old_requirement_and_invalidates_approval(self):
        current = state("Monitoring only; no controls")
        current["settings"]["joint_planning"] = False
        lifecycle.install_draft(current, body(), origin="fixture")
        lifecycle.human.evaluate(current)
        lifecycle.present(current)
        lifecycle.approve(current, goals.token(current["goal_contract"]))
        self.assertTrue(goals.approved(current))
        current["settings"]["joint_planning"] = True
        # Return to the conversation checkpoint, then apply changed user intent.
        current["status"] = "AWAITING_GOAL_APPROVAL"
        old_token = goals.token(current["goal_contract"])
        goals.feedback(current, "Actually add Pause, Resume and Cancel")
        event = current["brief_feedback"][-1]
        self.assertIsNone(current["goal_contract"]["approval_event"])
        current["requirements_handoff"] = {
            "report": {
                "requirements": [{"id": "R1"}, {"id": "R2"}],
                "open_questions": [],
                "conflicts": [{"requirement_ids": ["R1", "R2"], "description": "Controls vs monitoring only"}],
            }
        }
        value = draft_report(body())
        value["requirement_trace"] = [
            {
                "requirement_id": "R1",
                "disposition": "superseded",
                "evidence": f"Amended by saved feedback {event['id']} to permit the three controls.",
            },
            {"requirement_id": "R2", "disposition": "covered", "evidence": "C1"},
        ]
        autopilot.apply_planning(current, "astra_discovery", value, {"output": "changed.json"})
        self.assertNotEqual(old_token, goals.token(current["goal_contract"]))
        self.assertFalse(goals.approved(current))
        self.assertEqual("astra_challenge", current["next_stage"])

    def test_supersession_does_not_accept_forged_or_partial_event_ids(self):
        current = state()
        event = {"id": "feedback-abc123", "kind": "brief_feedback", "text": "Replace it"}
        current.update(user_events=[event], brief_feedback=[event])
        current["requirements_handoff"] = {"report": {"requirements": [{"id": "R1"}]}}
        for evidence in ("feedback-abc123", "Replaced by feedback-abc123 (explicit correction)"):
            goals.check_requirement_trace(
                current,
                {"requirement_trace": [{"requirement_id": "R1", "disposition": "superseded", "evidence": evidence}]},
                body(),
            )
        for evidence in ("feedback-abc1234", "xfeedback-abc123", "feedback-abc123 and feedback-forged"):
            with self.subTest(evidence=evidence), self.assertRaisesRegex(ValueError, "saved user event"):
                goals.check_requirement_trace(
                    current,
                    {
                        "requirement_trace": [
                            {"requirement_id": "R1", "disposition": "superseded", "evidence": evidence}
                        ]
                    },
                    body(),
                )
        current["user_events"] = []
        self.assertFalse(goals._cites_saved_user_event(current, "feedback-abc123"))

    def test_impossible_guarantee_reframe_requires_its_own_question(self):
        task = "Guarantee zero bugs"
        current = state(task)
        value = handoff(task)
        value["proposed_reframes"] = [
            {
                "requirement_id": "R1",
                "proposal": "Zero known defects after checks",
                "question_id": "accept-bounded-guarantee",
            }
        ]
        with self.assertRaisesRegex(ValueError, "explicit user acceptance question"):
            goals.check_requirement_handoff(current, value)
        question = {
            "id": "accept-bounded-guarantee",
            "question": "Accept bounded verification?",
            "why": "Unknown bugs cannot be ruled out",
            "options": [],
            "proposed_default": "",
        }
        value["open_questions"] = [question]
        goals.check_requirement_handoff(current, value)
        current["requirements_handoff"] = {"report": value}
        trace = {"requirement_trace": [{"requirement_id": "R1", "disposition": "covered", "evidence": "C1"}]}
        with self.assertRaisesRegex(ValueError, "reframe must remain"):
            goals.check_requirement_trace(current, trace, body(questions=True))
        draft = body()
        draft["open_blocking_questions"] = [question]
        goals.check_requirement_trace(current, trace, draft)

    def test_feedback_requirements_cannot_be_silently_omitted(self):
        current = state("Build dashboard")
        current["brief_feedback"] = [{"text": "Never touch auth."}]
        value = handoff("Build dashboard")
        with self.assertRaisesRegex(ValueError, "Never touch auth"):
            goals.check_requirement_handoff(current, value)

    def test_inventory_finds_nested_ui_without_git_or_package_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "tools/dashboard").mkdir(parents=True)
            (root / "tools/dashboard/dashboard.html").write_text("<button>Pause</button>")
            (root / ".autocode").mkdir()
            (root / ".autocode/private.json").write_text("{}")
            inventory = planner.workspace_inventory(root, "agent dashboard")
            self.assertEqual(["tools/dashboard/dashboard.html"], inventory["files"])
            self.assertFalse(inventory["truncated"])
            current = state("Build an agent dashboard")
            current["workspace"] = temp
            value = handoff()
            value["source_refs"] = ["task"]
            with self.assertRaisesRegex(ValueError, "cite existing source"):
                autopilot.apply_planning(current, "requirements_gather", value, {"output": "req.json"})
            value["source_refs"] = ["tools/dashboard/dashboard.html:1", "https://example.test/design-spec"]
            autopilot.apply_planning(current, "requirements_gather", value, {"output": "req.json"})

    def test_source_citations_allow_explanations_but_check_the_full_range(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "runner.py").write_text("def complete():\n    return True\n")
            current = state()
            current["workspace"] = temp
            for ref in ("runner.py:1", "runner.py:1-2 (completion gate)", "runner.py:2 — result"):
                autopilot._check_code_refs(current, [ref])
            for ref in ("runner.py:1-3 (past end)", "runner.py:2-1", "runner.py:0", "runner.py:bogus"):
                with self.subTest(ref=ref), self.assertRaises(ValueError):
                    autopilot._check_code_refs(current, [ref])
            # A live Requirements report cited 1-18 of a 17-line file and both repairs repeated it:
            # the rejection says how long the file is, so a repair can correct the range.
            with self.assertRaisesRegex(ValueError, r"runner\.py:1-3 points past the end of the file \(2 lines\)"):
                autopilot._check_code_refs(current, ["runner.py:1-3"])

    def test_an_explanation_after_an_existing_path_is_not_a_rejection(self):
        # A live glm_revise was rejected, costing a report repair, for a path followed by prose.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "runner.py").write_text("def complete():\n    return True\n")
            current = state()
            current["workspace"] = temp
            for ref in (
                "runner.py — the completion gate",
                "runner.py (completion gate)",
                "runner.py: the completion gate",
                "runner.py - see complete():2",
            ):
                with self.subTest(ref=ref):
                    autopilot._check_code_refs(current, [ref])
            for ref in ("missing.py — the completion gate", "missing.py: why", "runner.py:9 — past the end"):
                with self.subTest(ref=ref), self.assertRaises(ValueError):
                    autopilot._check_code_refs(current, [ref])

    def test_files_the_runner_or_a_tool_owns_cannot_be_cited(self):
        # A live bugfix plan cited .autocode/active-processes.json, which is gone when the run ends.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "runner.py").write_text("x = 1\n")
            for owned in (
                ".autocode/runs/r1/active-processes.json",
                "__pycache__/runner.cpython-311.pyc",
                "pkg/__pycache__/m.py",
                ".git/config",
                ".pytest_cache/README.md",
            ):
                (root / owned).parent.mkdir(parents=True, exist_ok=True)
                (root / owned).write_text("{}\n")
            current = state()
            current["workspace"] = temp
            autopilot._check_code_refs(current, ["runner.py:1"])
            for owned in (
                ".autocode/runs/r1/active-processes.json",
                "__pycache__/runner.cpython-311.pyc",
                "pkg/__pycache__/m.py",
                ".git/config",
                ".pytest_cache/README.md",
            ):
                with self.subTest(owned=owned), self.assertRaisesRegex(ValueError, "owns"):
                    autopilot._check_code_refs(current, [owned, "runner.py:1"])


class ConcernCoverageTests(unittest.TestCase):
    CONCERNS = [{"id": "C1"}, {"id": "C2"}]

    def test_every_concern_needs_exactly_one_row(self):
        for rows in ([{"concern_id": "C1"}], [{"concern_id": "C1"}, {"concern_id": "C1"}, {"concern_id": "C2"}]):
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "exactly one"):
                planner._coverage(rows, self.CONCERNS)

    # The live MiMo Plan Reviewer (2026-10-05) filed this beside complete rows for every concern.
    PLACEHOLDER = {
        "concern_id": "AC39-placeholder",
        "decision": "",
        "rationale": "",
        "acceptance_test": "",
        "resolved": True,
    }

    def decision(self, concern_id, **changes):
        return {
            "concern_id": concern_id,
            "decision": "Reject whitespace",
            "rationale": "One invalid-input rule",
            "acceptance_test": "Whitespace input exits 2",
            "resolved": True,
            **changes,
        }

    def test_a_row_for_something_that_is_not_a_concern_is_not_a_rejection(self):
        # A live astra_finalize was rejected for listing a question ID among its concern decisions.
        rows = [{"concern_id": "C1"}, {"concern_id": "Q1"}, {"concern_id": "C2"}]
        self.assertEqual([rows[0], rows[2]], planner._coverage(rows, self.CONCERNS))

    def test_only_rows_for_concerns_must_be_substantive(self):
        rows = [self.decision("C1"), self.PLACEHOLDER, self.decision("C2")]
        self.assertEqual([rows[0], rows[2]], planner._coverage(rows, self.CONCERNS))
        for field in ("decision", "rationale", "acceptance_test"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "substantive"):
                planner._coverage([self.decision("C1"), self.decision("C2", **{field: " "})], self.CONCERNS)

    def test_the_final_review_keeps_only_its_concern_decisions(self):
        for resolved in (True, False):
            with self.subTest(placeholder_resolved=resolved):
                current = state()
                lifecycle.install_draft(current, body(), origin="glm_draft")
                current["planning"]["reports"]["astra_challenge"] = {"report": {"concerns": [{"id": "C1"}]}}
                final = body()
                final["initial_task"] = {
                    "kind": "implement",
                    "objective": "Deliver the CLI",
                    "milestone_id": "M1",
                    "requirements": ["Greet"],
                    "acceptance_criteria": ["C1"],
                    "validation_plan": ["Run"],
                    "affected_paths": ["greet.py"],
                }
                value = {
                    "contract": final,
                    "summary": "Ready",
                    "contract_changes": [],
                    "requirement_trace": [],
                    "decisions": [self.decision("C1"), {**self.PLACEHOLDER, "resolved": resolved}],
                }
                autopilot.apply_planning(current, "astra_finalize", value, {"output": "final.json"})
                saved = current["planning"]["reports"]["astra_finalize"]["report"]
                self.assertEqual([self.decision("C1")], saved["decisions"])
                self.assertEqual(goals.token(current["goal_contract"]), current["planning"]["final_token"])
                self.assertEqual(2, len(value["decisions"]))  # the provider's report itself is not edited


class RetainedConcernReportTests(unittest.TestCase):
    """The #2440 paging report added passed-audit rows that ingestion already discards."""

    def setUp(self):
        # This is a mocked planning/report test, with one unchanged source revision.
        snapshot = patch.object(lifecycle.source_scope, "snapshot", return_value={"revision": "source-revision"})
        snapshot.start()
        self.addCleanup(snapshot.stop)

    def inputs(self, concerns=("C1", "C2")):
        current = state()
        current["stages"] = [{"stage": "astra_finalize", "output": "final.json", "source_revision": "source-revision"}]
        contract = body()
        contract["acceptance_criteria"][0]["sources"] = [{"source_id": "task:0", "quote": current["task"]}]
        contract["initial_task"] = {
            "kind": "implement",
            "objective": "Deliver the CLI",
            "milestone_id": "M1",
            "requirements": ["Greet"],
            "acceptance_criteria": ["C1"],
            "validation_plan": ["Run"],
            "affected_paths": ["greet.py"],
        }
        planner.new_cycle(current)
        lifecycle.install_draft(current, contract, origin="glm_revise")
        current["planning"]["reports"].update(
            {
                "astra_challenge": {
                    "output": "challenge.json",
                    "report": {"concerns": [{"id": cid} for cid in concerns]},
                },
                "glm_revise": {
                    "output": "revision.json",
                    "report": {"contract": copy.deepcopy(current["goal_contract"]["body"]), "requirement_trace": []},
                },
            }
        )
        decision = {
            "decision": "Resolved in the retained revision.",
            "rationale": "The named checks cover the missing behavior within the existing scope.",
            "acceptance_test": "Run the retained criterion's exact checks before and after the fix.",
            "resolved": True,
        }
        report = {
            "contract": {
                "retain_contract": retention.token(current),
                "acceptance_criteria": {
                    "retain_from": retention.token(current),
                    "upsert": [],
                    "remove": [],
                    "proofs": [],
                },
                "open_blocking_questions": [],
            },
            "summary": "The retained revision is ready for exact human approval.",
            "contract_changes": [],
            "conflict_resolutions": [],
            "requirement_trace": [],
            "decisions": [{"concern_id": cid, **decision} for cid in concerns],
            "source_trace": {"out_of_scope": [], "negation_waivers": []},
            "obligation_decisions": [],
            "progressive_proposal": {
                "version": 0,
                "needed_because": "",
                "shared_decisions": [],
                "outstanding_criteria": [],
                "done_slices": [],
                "slices": [],
            },
        }
        return current, report

    def load_report(self, current, report):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        request = planner.prepare(copy.deepcopy(current), "astra_finalize", root / "state.json", None)
        schema = runner.util.model_output_schema(request.schema)
        output, schema_file, events = root / "final.json", root / "schema.json", root / "final.jsonl"
        output.write_text(json.dumps(report))
        schema_file.write_text(json.dumps(schema))
        events.write_text('{"type":"turn.completed"}\n')
        record = {
            "stage": "astra_finalize",
            "engine": "codex",
            "output": str(output),
            "schema": str(schema_file),
            "events": str(events),
            "source_revision": "source-revision",
        }
        current["stages"] = [record]
        loaded = runner.load_stage_report(record, state=current)
        self.assertEqual(report["decisions"], loaded["decisions"])
        self.assertEqual(schema, json.loads(schema_file.read_text()), "the pinned generation schema stays unchanged")
        raw = Path(record.get("reported_output", output))
        self.assertEqual(report, json.loads(raw.read_text()), "provider evidence stays intact")
        return loaded, record

    def test_omitting_discarded_audit_rows_preserves_the_exact_public_approval_gate(self):
        current, concise = self.inputs()
        original = copy.deepcopy(concise)
        # Sanitized shape of the saved report: three extra audit decisions beside real C1/C2.
        original["decisions"] += [
            {**concise["decisions"][0], "concern_id": cid}
            for cid in ("AUDIT-PROVENANCE", "AUDIT-EXAMPLES-PROOFS", "AUDIT-HANDOFF")
        ]
        report_before = copy.deepcopy(original)
        outcomes = []
        for report in (original, concise):
            candidate = copy.deepcopy(current)
            loaded, record = self.load_report(candidate, report)
            expanded = retention.expand(candidate, "astra_finalize", loaded)["contract"]
            self.assertEqual(current["goal_contract"]["body"], expanded)
            autopilot.apply_planning(candidate, "astra_finalize", loaded, record)
            lifecycle.human.evaluate(candidate)
            shown = lifecycle.present(candidate)
            view = run_view.view(candidate)
            self.assertEqual("approve_plan", view["needs"]["kind"])
            selected = view["needs"]["token"]
            self.assertEqual(goals.token(candidate["goal_contract"]), selected)
            self.assertIn(selected, shown)
            self.assertIn("--approve-goal", view["next_command"])
            self.assertIn(selected, view["next_command"])
            self.assertFalse(goals.approved(candidate))
            with self.assertRaisesRegex(goals.s.Paused, "no valid explicit approval"):
                goals.execution_guard(candidate)
            with self.assertRaisesRegex(ValueError, "current displayed draft token"):
                lifecycle.approve(candidate, "r0:wrong")
            outcomes.append(
                (
                    {key: value for key, value in candidate["goal_contract"]["body"].items() if key != "source_trace"},
                    candidate["planning"]["reports"]["astra_finalize"]["report"],
                    # Raw report provenance changes the sealed token, never the approval action or plan.
                    view["next_command"].replace(selected, "CURRENT_PLAN_TOKEN"),
                )
            )
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(
            current["goal_contract"]["body"],
            {key: outcomes[1][0][key] for key in current["goal_contract"]["body"]},
        )
        self.assertEqual(report_before, original)

    def test_retained_report_still_refuses_missing_duplicate_or_unresolved_real_concerns(self):
        for variant in ("missing", "duplicate", "unresolved"):
            with self.subTest(variant=variant):
                current, report = self.inputs()
                if variant == "missing":
                    report["decisions"].pop()
                elif variant == "duplicate":
                    report["decisions"][1] = copy.deepcopy(report["decisions"][0])
                else:
                    report["decisions"][0]["resolved"] = False
                error = "blocking questions" if variant == "unresolved" else "exactly one"
                loaded, record = self.load_report(current, report)
                with self.assertRaisesRegex(ValueError, error):
                    autopilot.apply_planning(current, "astra_finalize", loaded, record)
                self.assertFalse(goals.approved(current))

    def test_a_new_blocker_survives_an_empty_incoming_concern_list(self):
        current, report = self.inputs(concerns=())
        question = body(questions=True)["open_blocking_questions"][0]
        report["contract"]["open_blocking_questions"] = [question]
        loaded, record = self.load_report(current, report)
        autopilot.apply_planning(current, "astra_finalize", loaded, record)
        lifecycle.human.evaluate(current)
        view = run_view.view(current)
        self.assertEqual("WAITING_FOR_USER", view["status"])
        self.assertNotEqual("approve_plan", (view["needs"] or {}).get("kind"))
        self.assertIn(question["question"], lifecycle.present(current))
        self.assertFalse(goals.approved(current))
        self.assertEqual("none", current["goal_contract"]["body"]["initial_task"]["kind"])


if __name__ == "__main__":
    unittest.main()

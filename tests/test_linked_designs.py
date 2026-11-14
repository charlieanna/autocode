"""Regression tests for user-named design documents, assumption text backfill, and moved-run Investigator refs."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import autocode_brief_obligations as brief_obligations
import autocode_goals as goals
import autocode_linked_designs as linked_designs
import autocode_report_check as report_check
import autocode_requirement_cues as cues
import autocode_source_log as source_log
import autocode_source_trace as source_trace
import autocode_stuck_job as stuck
import autocode_stuck_repair_context as stuck_repair_context
import autopilot
from units import autoplanner


class LinkedDesignGroundingTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        design_dir = self.workspace / "docs" / "design"
        design_dir.mkdir(parents=True)
        self.design_path = design_dir / "rate-limiter.md"
        self.design_path.write_text(
            "# Token Bucket Limiter\n\n"
            "Background context that need not be quoted line by line.\n\n"
            "Replace the fixed window in `Limiter` with one token bucket per key, "
            "holding up to `burst` tokens (new constructor argument, default 1) and refilling at `per_second` tokens per second.\n"
            "Only the injected `clock` tells time.\n",
            encoding="utf-8",
        )
        (self.workspace / "AGENTS.md").write_text("Must never be treated as a user design doc.\n", encoding="utf-8")
        (design_dir / "unmentioned.md").write_text("Unmentioned sentence must never ground R9.\n", encoding="utf-8")

    def test_design_doc_named_in_brief_grounds_requirements_and_report_check_without_checklist_noise(self) -> None:
        state: dict = {
            "task": "Implement the approved design in docs/design/rate-limiter.md exactly as written.",
            "workspace": str(self.workspace),
            "user_events": [],
            "brief_feedback": [],
            "answers": {},
        }
        source_log.seed(state)
        designs = source_log.linked_design_records(state)
        self.assertEqual(["design:docs:design:rate-limiter.md"], [row["id"] for row in designs])

        report = {
            "summary": "Implement rate-limiter design",
            "intended_outcome": "Token bucket limiter per key",
            "job": "build",
            "design_document": "docs/design/rate-limiter.md",
            "required_behaviors": ["Token bucket per key"],
            "constraints": ["Only injected clock tells time"],
            "acceptance_tests": ["Burst and refill tests"],
            "source_refs": ["task", "docs/design/rate-limiter.md"],
            "proposed_assumptions": [],
            "open_questions": [],
            "requirements": [
                {
                    "id": "R1",
                    "text": "Replace fixed window with token bucket per key",
                    "source_quote": (
                        "Replace the fixed window in `Limiter` with one token bucket per key, "
                        "holding up to `burst` tokens (new constructor argument, default 1) and refilling at `per_second` tokens per second."
                    ),
                },
                {
                    "id": "R2",
                    "text": "Use only injected clock",
                    "source_quote": "Only the injected `clock` tells time.",
                },
            ],
            "ignored_statements": ["Implement the approved design in docs/design/rate-limiter.md exactly as written."],
            "conflicts": [],
            "proposed_reframes": [],
            "ignored_requirements": [],
        }
        goals.check_requirement_handoff(state, report)

        spec = report_check.build_spec(
            stage="requirements_gather",
            workspace=self.workspace,
            schema=autoplanner.schema_for(state, "requirements_gather"),
            state=state,
        )
        self.assertEqual([], report_check.check_report(json.dumps(report), spec=spec))

        records = brief_obligations.sources(state)
        scoped_ids = [row["id"] for row in source_trace._coverage_scoped(records)]
        self.assertNotIn("design:docs:design:rate-limiter.md", scoped_ids)
        self.assertIn("design:docs:design:rate-limiter.md", [row["id"] for row in records])

    def test_unmentioned_meta_and_symlinked_markdown_files_are_refused(self) -> None:
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: outside.exists() and outside.rmdir())
        secret = outside / "secret.md"
        secret.write_text("External secret requirement.\n", encoding="utf-8")
        self.addCleanup(lambda: secret.exists() and secret.unlink())
        (self.workspace / "docs" / "design" / "linked.md").symlink_to(secret)

        state: dict = {
            "task": "Check AGENTS.md and docs/design/linked.md and ../secret.md.",
            "workspace": str(self.workspace),
            "user_events": [],
            "brief_feedback": [],
            "answers": {},
        }
        source_log.seed(state)
        self.assertEqual([], source_log.linked_design_records(state))
        texts = cues.source_texts(state)
        self.assertEqual([state["task"]], texts)

    def test_followup_build_turn_grounds_approved_previous_design_turn(self) -> None:
        state: dict = {
            "task": "Build it.",
            "workspace": str(self.workspace),
            "turns": [
                {
                    "previous": {
                        "workflow": "design",
                        "design": {"mode": "propose", "documents": ["docs/design/rate-limiter.md"]},
                    }
                }
            ],
            "user_events": [],
            "brief_feedback": [],
            "answers": {},
        }
        source_log.seed(state)
        self.assertIn("Only the injected `clock` tells time.", "\n".join(cues.source_texts(state)))
        self.assertEqual(
            ["docs/design/rate-limiter.md"],
            linked_designs.buildable_turn_paths(state["turns"]),
        )


class AssumptionTextBackfillTests(unittest.TestCase):
    def test_apply_planning_backfills_missing_assumption_text_from_rationale_and_still_rejects_blank(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        ws = Path(temp.name)
        (ws / "models.py").write_text("class Task:\n    pass\n", encoding="utf-8")

        state: dict = {
            "version": 3,
            "task": "Load older stores cleanly.",
            "workspace": str(ws),
            "status": "RUNNING",
            "phase": "DISCOVERING",
            "next_stage": "requirements_gather",
            "settings": {"roles": {"planner": {"model": "fake/m"}, "plan_reviewer": {"model": "fake/r"}}},
            "user_events": [],
            "brief_feedback": [],
            "answers": {},
            "stages": [],
        }
        source_log.seed(state)
        output_path = ws / "req.json"
        output_path.write_text("{}", encoding="utf-8")
        events_path = ws / "events.jsonl"
        events_path.write_text(
            json.dumps(
                {"type": "tool_use", "part": {"tool": "read", "state": {"input": {"filePath": str(ws / "models.py")}}}}
            )
            + "\n",
            encoding="utf-8",
        )
        record = {"stage": "requirements_gather", "output": str(output_path), "events": str(events_path)}
        rationale = "Task.from_dict already defaults missing fields so older stores keep loading."
        report = {
            "summary": "Load older stores cleanly",
            "intended_outcome": "Older stores load with defaults",
            "job": "build",
            "design_document": "",
            "required_behaviors": ["Default missing fields"],
            "constraints": [],
            "acceptance_tests": ["Load legacy store fixture"],
            "source_refs": ["models.py"],
            "proposed_assumptions": [
                {
                    "id": "A1",
                    "kind": "inferable",
                    "category": "behavior",
                    "convention_ref": "models.py:1",
                    "rationale": rationale,
                    "supports": ["R1"],
                }
            ],
            "open_questions": [],
            "requirements": [
                {"id": "R1", "text": "Load older stores cleanly", "source_quote": "Load older stores cleanly."}
            ],
            "ignored_statements": [],
            "conflicts": [],
            "proposed_reframes": [],
            "ignored_requirements": [],
        }
        spec = report_check.build_spec(
            stage="requirements_gather",
            workspace=ws,
            schema=autoplanner.schema_for(state, "requirements_gather"),
            state=state,
        )
        self.assertEqual([], report_check.check_report(json.dumps(report), spec=spec))

        schema_path = ws / "req.schema.json"
        schema_path.write_text(json.dumps(autoplanner.schema_for(state, "requirements_gather")), encoding="utf-8")
        output_path.write_text(json.dumps(report), encoding="utf-8")
        import autocode as runner

        load_record = {**record, "schema": str(schema_path), "report_only": True}
        loaded = runner.load_stage_report(load_record, workspace=ws, state=state)
        self.assertEqual(rationale, loaded["proposed_assumptions"][0]["text"])
        self.assertIn("proposed_assumptions.text", load_record.get("defaulted_fields", []))

        autopilot.apply_planning(state, "requirements_gather", dict(report), record)
        saved = state["requirements_handoff"]["report"]["proposed_assumptions"]
        self.assertEqual(rationale, saved[0]["text"])

        bad_report = {
            **report,
            "proposed_assumptions": [
                {
                    "id": "A1",
                    "kind": "inferable",
                    "category": "behavior",
                    "convention_ref": "models.py:1",
                    "rationale": "",
                    "supports": ["R1"],
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, r"proposed_assumptions\[0\]"):
            autopilot.apply_planning(state, "requirements_gather", bad_report, record)


class MovedWorktreeInvestigatorRefsTests(unittest.TestCase):
    def test_cited_files_accepts_authenticated_pre_worktree_origin_run_files_and_refuses_unreciprocated(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        origin_run = root / "project" / ".autocode" / "runs" / "run-1"
        worktree = root / "project" / ".autocode" / "worktrees" / "task-branch"
        worktree_run = worktree / ".autocode" / "runs" / "run-1"
        archived = origin_run / "iterations" / "001" / "archived-req-02" / "requirements-gather-report-repair-02.json"
        archived.parent.mkdir(parents=True)
        worktree_run.mkdir(parents=True)
        archived.write_text('{"summary": "pre-move"}', encoding="utf-8")

        (origin_run / "state.json").write_text(
            json.dumps(
                {
                    "build_worktree": {
                        "state": "moved",
                        "moved_to": {
                            "workspace": str(worktree),
                            "run_dir": str(worktree_run),
                            "branch": "autocode/task",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        (worktree_run / "state.json").write_text(
            json.dumps(
                {
                    "build_worktree": {
                        "state": "building",
                        "moved_from": {"workspace": str(root / "project"), "run_dir": str(origin_run)},
                    }
                }
            ),
            encoding="utf-8",
        )

        copied = stuck.cited_files({"evidence_refs": [str(archived)]}, worktree, worktree_run)
        self.assertEqual({"run/requirements-gather-report-repair-02.json": archived.resolve()}, copied)

        mapped = stuck_repair_context.evidence_files([str(archived)], worktree, worktree_run)
        self.assertEqual(
            [{"evidence_ref": str(archived), "probe_path": "run/requirements-gather-report-repair-02.json"}],
            mapped,
        )

        # Break reciprocity on origin state -> must fail closed.
        (origin_run / "state.json").write_text(
            json.dumps({"build_worktree": {"state": "moved", "moved_to": {"run_dir": str(root / "other")}}}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "evidence_refs must name files that exist"):
            stuck.cited_files({"evidence_refs": [str(archived)]}, worktree, worktree_run)


if __name__ == "__main__":
    unittest.main()

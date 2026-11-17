"""Regression tests for report_repair handoff/schema compaction, unlaunchable repair archiving/restoration, and route revalidation."""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_goals as goals
import autocode_recovery_limits as recovery_limits
import autocode_report_repair_context as report_repair_context
import autocode_report_repair_queue as repair_queue
import autocode_report_source as report_source
import autocode_resolve_repair_job as resolve_repair_job
import autocode_run_actions as run_actions
import autocode_run_records as run_records
import autocode_stage_recovery as stage_recovery
import autocode_stage_stalls as stage_stalls
import autocode_support as support
import autocode_util as util
import goal_fixtures


class SharedCauseBReportRepairAndReconciliationTests(unittest.TestCase):
    """Self-validation report-check heredocs never bloat report_repair, and 3x unlaunchable repairs reconcile cleanly."""

    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(scratch).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "README.md").write_text("# Search\n")
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=T", "-c", "user.email=t@e.t", "add", "README.md"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=T", "-c", "user.email=t@e.t", "commit", "-qm", "init"],
            check=True,
        )
        self.run = self.root / ".autocode" / "runs" / "run-1"
        self.run.mkdir(parents=True)

    def test_report_check_heredocs_excluded_from_planning_repair_and_resolver_checks_only(self):
        events = self.run / "revise.jsonl"
        huge_heredoc = (
            "/usr/bin/python3 /ws/tools/autocode.py report-check --spec /ws/spec.json - <<'JSONEOF'\n"
            + ("x" * 95_000)
            + "\nJSONEOF"
        )
        real_probe = "python3 -c 'print(1 + 1)'"
        rows = [
            {
                "type": "item.completed",
                "item": {"id": "cmd-1", "type": "command_execution", "command": real_probe, "exit_code": 0},
            },
            *[
                {
                    "type": "item.completed",
                    "item": {
                        "id": f"cmd-{idx + 2}",
                        "type": "command_execution",
                        "command": huge_heredoc,
                        "exit_code": 0,
                    },
                }
                for idx in range(3)
            ],
            {"type": "turn.completed"},
        ]
        events.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

        self.assertEqual([], report_source.original_executed_checks(events, stage="revise_plan"))
        self.assertEqual([], resolve_repair_job._executed_checks(str(events), stage="revise_plan"))

        expected = [{"command": real_probe, "exit_code": 0, "evidence_ref": "event:cmd-1"}]
        self.assertEqual(expected, report_source.original_executed_checks(events, stage="requirements_gather"))
        self.assertEqual(expected, report_source.original_executed_checks(events, stage="builder"))
        self.assertEqual(expected, resolve_repair_job._executed_checks(str(events), stage="builder"))

    def test_third_unlaunchable_repair_archives_pending_and_admits_retry_failed_stage(self):
        snap = support.snapshot(self.root)
        state = {
            "version": 3,
            "workspace": str(self.root),
            "run_dir": str(self.run),
            "task": "Build search",
            "task_id": "task-1",
            "status": "RUNNING",
            "phase": "PLANNING",
            "next_stage": "revise_plan",
            "iteration": 1,
            "settings": {
                "engine": "opencode",
                "roles": {
                    "planner": {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"},
                    "plan_reviewer": {"model": "openai/gpt-6-sol", "reasoning_effort": "high"},
                },
                "report_repair": {"max_attempts": 2},
                "limits": {},
            },
            "stages": [],
            "history": [],
            "user_events": [],
        }
        goal_fixtures.approve_fixture(state, goals)
        state.update(next_stage="revise_plan", phase="PLANNING", status="RUNNING")
        state["settings"]["joint_planning"] = True
        state["goal_contract"].update(approval_status="draft", approval_event=None)

        def queue_attempt(idx: int, reason_text: str):
            out = self.run / f"revise-{idx}.json"
            ev = self.run / f"revise-{idx}.jsonl"
            bef = self.run / f"revise-{idx}.before.json"
            aft = self.run / f"revise-{idx}.after.json"
            sch = self.run / "schema.json"
            out.write_text("{}")
            ev.write_text(json.dumps({"type": "turn.completed"}) + "\n")
            bef.write_text(json.dumps(snap))
            aft.write_text(json.dumps(snap))
            sch.write_text("{}")
            rec = {
                "role": "planner",
                "stage": "revise_plan",
                "iteration": 1,
                "output": str(out),
                "events": str(ev),
                "before_ref": str(bef),
                "after_ref": str(aft),
                "schema": str(sch),
                "exit_code": 0,
                "source_revision": snap["revision"],
                "contract_hash": state["goal_contract"]["hash"],
            }
            runner.failures.record(state, rec, ValueError(reason_text), support.now())
            rec["rejected"] = True
            state["stages"].append(rec)
            self.assertTrue(repair_queue.queue(state, rec, ValueError(reason_text), completed=lambda: True))
            return rec

        error = support.Paused(
            "PAUSED_REPORT_REPAIR_INPUT", "Complete report-repair handoff exceeds 262144 bytes; no unbounded repair."
        )
        # First 2 unlaunchable repairs restart automatically; distinct error texts keep streak == 1 (< 3).
        queue_attempt(1, "First distinct validation error")
        self.assertTrue(stage_recovery.restart_unlaunchable_repair(state, self.run, self.root, error))
        queue_attempt(2, "Second distinct validation error")
        self.assertTrue(stage_recovery.restart_unlaunchable_repair(state, self.run, self.root, error))

        # Strike 3 pauses PAUSED_REPEATED_FAILURE and archives pending_report_repair cleanly.
        last_rec = queue_attempt(3, "Third distinct validation error")
        self.assertEqual(1, state["failure_history"][last_rec["failure_key"]]["streak"])
        with self.assertRaises(support.Paused) as paused:
            stage_recovery.restart_unlaunchable_repair(state, self.run, self.root, error)
        self.assertEqual("PAUSED_REPEATED_FAILURE", paused.exception.status)
        self.assertNotIn("pending_report_repair", state)
        self.assertEqual(3, len(state["report_repair_archive"]))
        self.assertIsNone(recovery_limits.repeated_failure_retry_refusal(state))

        # Plain resume still holds PAUSED_REPEATED_FAILURE, while --retry-failed-stage authorizes cleanly.
        with self.assertRaises(support.Paused):
            runner.repeated_failure_resume_guard(state, self.root)
        auth = stage_recovery.authorize_failure_retry(state, self.run, self.root)
        self.assertEqual(last_rec["failure_key"], auth["failure_key"])
        runner.repeated_failure_resume_guard(state, self.root, authorization=auth)
        self.assertTrue(auth["consumed"])

        # Pre-fix checkpoint shape (pending_report_repair left at attempts=0 after 2 archived unlaunchables)
        # is also recognized and reconciled without deadlock.
        pre_fix = copy.deepcopy(state)
        pre_fix["pending_report_repair"] = pre_fix["report_repair_archive"].pop()["repair"]
        self.assertTrue(stage_stalls.exhausted_unlaunchable_pending(pre_fix))
        self.assertFalse(repair_queue.pending_repair_dispatchable(pre_fix))
        self.assertIsNone(recovery_limits.repeated_failure_retry_refusal(pre_fix))
        auth_pre_fix = stage_recovery.authorize_failure_retry(pre_fix, self.run, self.root)
        self.assertNotIn("pending_report_repair", pre_fix)
        self.assertEqual(last_rec["failure_key"], auth_pre_fix["failure_key"])


class SharedCauseB3AndFChubbyTesterRepairAndInvestigatorTests(unittest.TestCase):
    """Tester report_repair compacts open_lines mutant-log bloat and preserves Investigator guidance across interruptions."""

    def test_tester_changed_code_citation_context_strips_forbidden_mutant_log_bloat(self):
        obligations = [
            {
                "gap_id": f"src/chubby/cell.py:{idx}:4d6ca2b7",
                "handle": f"L{idx:03d}",
                "path": "src/chubby/cell.py",
                "line": idx,
                "source_line": f"    return lease_epoch + {idx}",
                "sha256": "4d6ca2b703cf4fe6a298a12cc939051226dc1be1ef8812d93ce1cc02849da09d",
                "executed": True,
                "mutation_demonstrated": False,
                "mutation_outcomes": ["survived"],
                "fallback": None,
                "status": "reason_required",
                "reason": "Changed product line was executed, but no mutation failure demonstrated test sensitivity.",
                "evidence": [
                    f"/Users/example/.autocode/runs/chubby/regression-proof/20261116/changed-code/mutant-{idx:04d}.log"
                ],
            }
            for idx in range(1, 601)
        ]
        raw_bytes = len(json.dumps(obligations, separators=(",", ":")).encode("utf-8"))
        self.assertGreater(raw_bytes, 300_000)

        state = {
            "regression_proof": {
                "path": "/ws/.autocode/runs/chubby/regression-proof/20261116/summary.json",
                "source_revision": "rev-chubby",
                "changed_code_evidence": {
                    "source_revision": "rev-chubby",
                    "obligations": obligations,
                },
            }
        }
        citation = report_repair_context.changed_code_citation_context(
            state, {"original": {"source_revision": "rev-chubby"}}, "tester"
        )
        open_lines = citation["open_lines"]
        self.assertEqual(600, len(open_lines))
        compacted_bytes = len(json.dumps(open_lines, separators=(",", ":")).encode("utf-8"))
        self.assertLess(compacted_bytes, 135_000)
        self.assertLess(compacted_bytes, raw_bytes // 2)
        sample = open_lines[0]
        for kept in ("handle", "path", "line", "source_line", "executed", "mutation_demonstrated", "mutation_outcomes"):
            self.assertIn(kept, sample)
        for stripped in ("evidence", "sha256", "fallback", "reason", "gap_id"):
            self.assertNotIn(stripped, sample)

        # Legacy rows without 'handle' retain 'gap_id' for compatibility.
        legacy_row = {k: v for k, v in obligations[0].items() if k != "handle"}
        state["regression_proof"]["changed_code_evidence"]["obligations"] = [legacy_row]
        legacy_citation = report_repair_context.changed_code_citation_context(
            state, {"original": {"source_revision": "rev-chubby"}}, "tester"
        )
        self.assertIn("gap_id", legacy_citation["open_lines"][0])
        self.assertNotIn("evidence", legacy_citation["open_lines"][0])


class SharedCauseHKiloDecoratedRepairAndStageLessonsTests(unittest.TestCase):
    """Inline provider-decorated repair schemas stay compact (#47 1) and stage_lessons keeps repair lessons (#47 2B)."""

    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory(prefix="kilo-repair-schema-"))
        self.root = Path(scratch).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "engine.py").write_text("x = 1\n")
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@e", "add", "engine.py"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "init"],
            check=True,
        )
        self.run = self.root / ".autocode" / "runs" / "r1"
        (self.run / "iterations" / "001").mkdir(parents=True)

    def _large_tester_schema_and_assessment(self, count: int = 684) -> tuple[dict, dict]:
        import autocode_changed_code_review as cc_review

        sol_path = Path(support.__file__).resolve().parent / "autocode-schemas" / "v2" / "sol-report.schema.json"
        base_schema = json.loads(sol_path.read_text(encoding="utf-8"))
        base_props = {cc_review.FIELD: copy.deepcopy(cc_review.SCHEMA), **base_schema["properties"]}
        offered_base = {**base_schema, "properties": base_props}
        obligations = [
            {
                "gap_id": f"changed-code:{idx:064x}",
                "handle": f"L{idx}",
                "path": "chubby/engine.py",
                "sha256": "a" * 64,
                "line": idx,
                "source_line": f"line_{idx} = {idx}",
                "status": "reason_required",
                "mutation_outcomes": [],
            }
            for idx in range(1, count + 1)
        ]
        assessment = {"schema": 1, "source_revision": "rev-1", "status": "UNVERIFIED", "obligations": obligations}
        gen_schema = cc_review.generation_schema(offered_base, assessment, "tester")
        return gen_schema, assessment

    def test_kilo_and_opencode_decorated_large_tester_repair_prompt_compacts_below_handoff_cap(self):
        import autocode_changed_code_review as cc_review
        import autocode_report_edits as report_edits
        import autocode_role_launch as role_launch
        from providers import opencode

        gen_schema, assessment = self._large_tester_schema_and_assessment(684)
        schema_path = self.run / "iterations" / "001" / "validator-01.schema.json"
        schema_path.write_text(json.dumps(gen_schema))
        rejected_report = {
            "verdict": "PASS",
            "checks_run": ["python3 -m unittest"],
            "checks": [{"command": "python3 -m unittest", "exit_code": 0, "evidence_ref": "event:1"}],
            "findings": [],
            "finding_dispositions": [],
            "unverified_criteria": [],
            "criterion_results": [{"id": "AC1", "status": "PASS", "evidence_refs": ["check:1"]}],
            "end_to_end_result": {
                "status": "PASS",
                "summary": "All HTTP and WAL tests pass.",
                "evidence_refs": ["check:1"],
                "technical_result": None,
                "pending_human_criteria": [],
            },
            "changed_code_exceptions": None,
        }
        rejected_path = self.run / "iterations" / "001" / "validator-01.json"
        rejected_path.write_text(json.dumps(rejected_report))
        repair_schema_path = report_edits.prepare(
            schema_path,
            {
                "path": str(rejected_path),
                "sha256": util.file_hash(rejected_path),
                "format": "json",
                "content": rejected_report,
            },
            "tester",
        )
        repair_schema = util.read(repair_schema_path)
        saved_schema_copy = copy.deepcopy(repair_schema)
        # Uncompacted indented repair schema alone was ~166 KiB on chubby validator-06.
        self.assertGreater(len(json.dumps(repair_schema, indent=2).encode("utf-8")), 150_000)

        handoff_body = {"report_repair": True, "padding": "p" * 195_000}
        raw_prompt = "Repair instructions.\nCURRENT HANDOFF DATA\n" + json.dumps(handoff_body, separators=(",", ":"))
        self.assertLess(len(raw_prompt.encode("utf-8")), runner.REPAIR_HANDOFF_BYTES)

        kilo_adapter = runner.autocode_providers.resolve("kilocode")
        events_path = self.run / "iterations" / "001" / "validator-report-repair-01.jsonl"
        state = {"settings": {"engine": "opencode", "provider": "kilocode", "roles": {}}, "regression_proof": {}}
        worker_ctx = {"kilo_readonly_tools": True, "planning_report_recovery": True}

        kilo_decorated = role_launch.decorate_prompt(
            state, "tester", self.root, kilo_adapter, raw_prompt, repair_schema, events_path, worker_ctx
        )
        opencode_decorated = opencode.prompt_for_schema(raw_prompt, repair_schema, events_path)

        self.assertLess(len(kilo_decorated.encode("utf-8")), runner.REPAIR_HANDOFF_BYTES)
        self.assertLess(len(opencode_decorated.encode("utf-8")), runner.REPAIR_HANDOFF_BYTES)
        self.assertNotIn("x-autocode-report-edits", kilo_decorated)
        self.assertNotIn("changed-code:00000000", kilo_decorated)
        self.assertIn('"L1"', kilo_decorated)
        self.assertIn('"L684"', kilo_decorated)
        # Saved repair_schema is untouched and still validates both short handles and 64-hex ids.
        self.assertEqual(saved_schema_copy, repair_schema)
        val_schema = cc_review.validation_schema(gen_schema, assessment, "tester")
        enum_vals = val_schema["properties"][cc_review.FIELD]["items"]["properties"]["gap_id"]["enum"]
        self.assertIn("L1", enum_vals)
        self.assertIn(f"changed-code:{1:064x}", enum_vals)

    def test_retry_failed_stage_restores_attempts_zero_unlaunchable_repair_only_when_now_launchable(self):

        gen_schema, _ = self._large_tester_schema_and_assessment(684)
        snap = runner.source_scope.snapshot(self.root, {"workspace": str(self.root)}, base_snapshot=support.snapshot)
        state = {
            "version": 3,
            "workspace": str(self.root),
            "run_dir": str(self.run),
            "task": "Build chubby",
            "task_id": "task-chubby",
            "status": "RUNNING",
            "phase": "EXECUTING",
            "next_stage": "tester",
            "iteration": 1,
            "settings": {
                "engine": "opencode",
                "provider": "kilocode",
                "tool_containment": False,
                "report_repair": {"max_attempts": 2},
                "roles": {
                    role: {"engine": "opencode", "model": "openai/gpt-6-sol", "reasoning_effort": "high"}
                    for role in ("task_planner", "planner", "plan_reviewer", "builder", "tester", "resolver")
                },
                "limits": {},
            },
            "stages": [],
            "history": [],
            "user_events": [],
        }
        goal_fixtures.approve_fixture(state, goals)
        state.update(status="RUNNING", phase="EXECUTING", next_stage="tester")
        snap = runner.source_scope.snapshot(self.root, state, base_snapshot=support.snapshot)
        out = self.run / "iterations" / "001" / "validator-04.json"
        ev = self.run / "iterations" / "001" / "validator-04.jsonl"
        bef = self.run / "iterations" / "001" / "validator-04.before.json"
        aft = self.run / "iterations" / "001" / "validator-04.after.json"
        sch = self.run / "iterations" / "001" / "validator-04.schema.json"
        out.write_text(
            json.dumps(
                {
                    "verdict": "PASS",
                    "checks_run": ["python3 -m unittest"],
                    "checks": [{"command": "python3 -m unittest", "exit_code": 0, "evidence_ref": "event:1"}],
                    "findings": [],
                    "finding_dispositions": [],
                    "unverified_criteria": [],
                    "criterion_results": [],
                    "end_to_end_result": {
                        "status": "PASS",
                        "summary": "ok",
                        "evidence_refs": ["check:1"],
                        "technical_result": None,
                        "pending_human_criteria": [],
                    },
                }
            )
        )
        ev.write_text("")
        bef.write_text("{}")
        aft.write_text("{}")
        sch.write_text(json.dumps(gen_schema))
        rec = {
            "role": "tester",
            "stage": "tester",
            "iteration": 1,
            "output": str(out),
            "events": str(ev),
            "before_ref": str(bef),
            "after_ref": str(aft),
            "schema": str(sch),
            "exit_code": 0,
            "source_revision": snap["revision"],
            "contract_hash": state["goal_contract"]["hash"],
            "rejected": True,
            "rejection_reason": "Missing changed_code_exceptions for L1",
        }
        runner.failures.record(state, rec, ValueError(rec["rejection_reason"]), support.now())
        state["stages"].append(rec)
        self.assertTrue(repair_queue.queue(state, rec, ValueError(rec["rejection_reason"]), completed=lambda: True))
        archived_repair = state.pop("pending_report_repair")
        state["report_repair_archive"] = [
            {
                "at": support.now(),
                "reason": f"Unchanged repeated failure hold: {stage_stalls.CANNOT_LAUNCH}",
                "repair": copy.deepcopy(archived_repair),
            }
            for _ in range(4)
        ]
        state.update(status="PAUSED_REPEATED_FAILURE", phase="PAUSED_OR_BLOCKED", next_stage="tester")
        run_records.write_json(self.run / "state.json", state)

        kilo_adapter = runner.autocode_providers.resolve("kilocode")
        with (
            patch.object(runner, "opencode", kilo_adapter),
            patch.object(kilo_adapter, "check_models", return_value=None),
        ):
            args_r, parser = runner.cli_args.parse(
                None,
                [
                    "--workspace",
                    str(self.root),
                    "--run-dir",
                    str(self.run),
                    "--resume-paused",
                    "--retry-failed-stage",
                    "--no-chat",
                ],
                runner.opencode.DEFAULT_MODELS,
            )
            self.assertIsNone(
                run_actions.handle(runner, args_r, parser, state, self.run / "state.json", self.run, self.root)
            )
        self.assertEqual("RUNNING", state["status"])
        self.assertIsNotNone(state.get("pending_report_repair"))
        self.assertEqual(0, state["pending_report_repair"]["attempts"])
        self.assertEqual(3, len(state["report_repair_archive"]))

        # When the raw repair handoff genuinely exceeds 262,144 bytes, it stays archived.
        oversized_state = copy.deepcopy(state)
        oversized_repair = oversized_state.pop("pending_report_repair")
        oversized_repair["error"] = "E" * 300_000
        oversized_state["report_repair_archive"].append(
            {
                "at": support.now(),
                "reason": f"Unchanged repeated failure hold: {stage_stalls.CANNOT_LAUNCH}",
                "repair": oversized_repair,
            }
        )
        self.assertFalse(
            stage_recovery.restore_launchable_unlaunchable_repair(runner, oversized_state, self.run, self.root)
        )
        self.assertNotIn("pending_report_repair", oversized_state)


if __name__ == "__main__":
    unittest.main()

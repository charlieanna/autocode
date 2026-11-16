"""Unit and integration tests for resolve_repair, upfront triage, provenance, and stall resolution."""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode as runner
import autocode_builder_policy as builder_policy
import autocode_code_checkpoints as code_checkpoints
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_plan_retention as retention
import autocode_planning_calls as planning_calls
import autocode_rejection_retry as rejection_retry
import autocode_rejection_route as rejection_route
import autocode_repair_provenance as repair_provenance
import autocode_repair_triage as repair_triage
import autocode_resolve_repair_job as resolve_repair_job
import autocode_resolver_human as resolver_human
import autocode_role_transport as role_transport
import autocode_run_status as run_status
import autocode_stop as stop
import autocode_stray_writes as stray_writes
import autocode_stuck_job as stuck_job
import autocode_support as support
import autocode_unit_request as unit_request
import autocode_usage as usage
import autocode_util as util
import autocode_verification_plan as verification_plan
import autocode_verifier_independence as verifier_independence
import autopilot
from goal_fixtures import body as fixture_body
from units.model_request import ModelRequest


def _init_git_repo(root: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    (root / "app.py").write_text("def greet():\n    return 'hi'\n")
    subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    return support.snapshot(root)["revision"]


def _approved_contract() -> tuple[dict, dict]:
    plan_body = fixture_body(questions=False)
    contract = {
        "task_id": "task-1",
        "revision": 1,
        "body": plan_body,
    }
    contract["hash"] = util.digest({k: contract[k] for k in ("task_id", "revision", "body")})
    tok = goals.token(contract)
    approval_ev = {"kind": "goal_approval", "token": tok, "actor": "user_cli", "at": "2026-11-14T09:59:00Z"}
    contract.update(
        approval_status="approved",
        approval_event=approval_ev,
        origin="planner",
        created_at="2026-11-14T09:58:00Z",
    )
    return contract, approval_ev


def _base_state(root: Path, run_dir: Path, revision: str) -> dict:
    contract, approval_ev = _approved_contract()
    criteria_defs = [
        {"id": c["id"], "criterion": c["criterion"], "status": "unverified", "evidence": ""}
        for c in contract["body"]["acceptance_criteria"]
    ]
    return {
        "version": 3,
        "task_id": "task-1",
        "workspace": str(root),
        "run_dir": str(run_dir),
        "status": "RUNNING",
        "phase": "EXECUTING",
        "next_stage": "draft_plan",
        "iteration": 1,
        "source_revision": revision,
        "criteria_revision": util.digest(support.criteria_definition(criteria_defs)),
        "goal_contract": contract,
        "current_task": {
            "id": "T1",
            "milestone_id": contract["body"]["milestones"][0]["id"],
            "contract_hash": contract["hash"],
            "contract_revision": 1,
            "kind": "implement",
            "objective": "Implement greet in app.py",
            "affected_paths": ["app.py"],
            "acceptance_criteria": [contract["body"]["acceptance_criteria"][0]["id"]],
            "validation_plan": [{"step": "Run unit tests", "command": "pytest -q"}],
        },
        "settings": {
            "engine": "opencode",
            "report_repair": {"max_attempts": 2},
            "builder_retry": {"enabled": True, "ordinary_retries": 1, "strong_model": "gpt-5.4"},
            "limits": {"planning_calls": 20},
            "roles": {
                "planner": {"model": "gpt-5.4", "reasoning_effort": "high"},
                "plan_reviewer": {"model": "claude-sonnet-4-6", "reasoning_effort": "high"},
                "builder": {"model": "gpt-5.3-codex", "reasoning_effort": "high"},
                "tester": {"model": "claude-sonnet-4-6", "reasoning_effort": "high"},
                "task_planner": {"model": "claude-sonnet-4-6", "reasoning_effort": "high"},
                "completion": {"model": "claude-sonnet-4-6", "reasoning_effort": "high"},
                "resolver": {"model": "claude-sonnet-4-6", "reasoning_effort": "high"},
            },
        },
        "stages": [],
        "history": [],
        "user_events": [approval_ev],
        "sessions": {},
        "acceptance_criteria": criteria_defs,
    }


def _write_events(path: Path, *, thread_id: str = "sess-1", commands: list[tuple[str, str, int]] = ()) -> None:
    lines = [{"type": "thread.started", "thread_id": thread_id}]
    for cmd_id, cmd, code in commands:
        lines.append(
            {
                "type": "item.completed",
                "item": {"id": cmd_id, "type": "command_execution", "command": cmd, "exit_code": code},
            }
        )
    lines.append({"type": "turn.completed"})
    path.write_text("\n".join(json.dumps(row) for row in lines) + "\n")


class ResolveRepairTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.run_dir = self.root / ".autocode" / "runs" / "run-1"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.revision = _init_git_repo(self.root)
        self.state = _base_state(self.root, self.run_dir, self.revision)

    def _stage_files(
        self,
        name: str,
        *,
        stage: str,
        role: str,
        report_obj: dict | str,
        schema_obj: dict | None = None,
        commands: list[tuple[str, str, int]] = (),
        changed_files: list[str] | None = None,
        cost_usd: float = 0.0,
    ) -> dict:
        out = self.run_dir / f"{name}.json"
        ev = self.run_dir / f"{name}.events.jsonl"
        sch = self.run_dir / f"{name}.schema.json"
        out.write_text(json.dumps(report_obj) if isinstance(report_obj, dict) else report_obj)
        _write_events(ev, thread_id=f"sess-{name}", commands=commands)
        sch.write_text(json.dumps(schema_obj or {"type": "object"}))
        return {
            "role": role,
            "stage": stage,
            "iteration": self.state["iteration"],
            "exit_code": 0,
            "duration_seconds": 2,
            "source_revision": self.revision,
            "task_id": self.state["current_task"]["id"],
            "contract_hash": self.state["goal_contract"]["hash"],
            "criteria_revision": self.state["criteria_revision"],
            "thread_id": f"sess-{name}",
            "supports_sessions": True,
            "output": str(out),
            "events": str(ev),
            "schema": str(sch),
            "engine": "opencode",
            "model": self.state["settings"]["roles"].get(role, {}).get("model", "gpt-5.4"),
            "changed_files": list(changed_files or []),
            "started_at": "2026-11-14T10:00:00Z",
            "finished_at": "2026-11-14T10:00:02Z",
            "cost_usd": cost_usd,
        }

    def _queue_after_one_local_repair(self, original: dict, rejected_err: str = "Missing required field") -> None:
        self.state["stages"].append(copy.deepcopy(original))
        self.state["history"].append(copy.deepcopy(original))
        rejected_local = {
            **copy.deepcopy(original),
            "stage": f"{original['stage']}_report_repair",
            "original_stage": original["stage"],
            "report_only": True,
            "output": str(self.run_dir / f"{Path(original['output']).stem}.local.json"),
            "rejected": True,
            "rejection_reason": rejected_err,
        }
        Path(rejected_local["output"]).write_text("{}")
        pins = {
            original["output"]: util.file_hash(original["output"]),
            original["events"]: util.file_hash(original["events"]),
            original["schema"]: util.file_hash(original["schema"]),
            rejected_local["output"]: util.file_hash(rejected_local["output"]),
        }
        self.state["pending_report_repair"] = {
            "original": copy.deepcopy(original),
            "original_error": rejected_err,
            "error": rejected_err,
            "attempts": 1,
            "contract_hash": self.state["goal_contract"]["hash"],
            "pins": pins,
            "latest_rejected": rejected_local,
        }
        self.state["next_stage"] = original["stage"]

    def _stub_runtime(self, *, fail_apply=None):
        applied_calls = []

        def apply_result(state, stage, value, record, workspace, run_dir):
            if fail_apply is not None:
                fail_apply(state, stage, value, record, workspace, run_dir)
            applied_calls.append((stage, value, record["output"]))
            state.setdefault("stages", []).append(copy.deepcopy(record))
            state.setdefault("history", []).append(copy.deepcopy(record))
            state["status"] = "RUNNING"
            state["next_stage"] = "review_completion" if stage == "tester" else "tester"

        def commit_boundary_candidate(state, candidate, *args, **kwargs):
            state.clear()
            state.update(candidate)

        def save_record(state, record):
            state.setdefault("stages", []).append(copy.deepcopy(record))
            state.setdefault("history", []).append(copy.deepcopy(record))

        attrs = {
            name: getattr(runner, name)
            for name in (
                "support",
                "goals",
                "planning",
                "workflow",
                "milestones",
                "escalation",
                "dispatch",
                "now",
                "write_json",
                "restore_builder_history",
                "assert_repair_preserves_builder_history",
                "retained_dispositions",
                "original_report_for_repair",
                "stage_completed",
            )
        }
        attrs.update(
            apply_result=apply_result,
            commit_boundary_candidate=commit_boundary_candidate,
            save_record=save_record,
        )
        return SimpleNamespace(**attrs), applied_calls

    def _resolve_record(self, name: str, *, cost_usd: float = 0.25) -> dict:
        out = self.run_dir / f"{name}.json"
        ev = self.run_dir / f"{name}.events.jsonl"
        out.write_text("{}")
        _write_events(ev, thread_id=f"sess-{name}")
        return {
            "role": "resolver",
            "stage": "resolve_repair",
            "iteration": self.state["iteration"],
            "exit_code": 0,
            "duration_seconds": 3,
            "source_revision": self.revision,
            "output": str(out),
            "events": str(ev),
            "schema": str(self.run_dir / f"{name}.schema.json"),
            "engine": "claude",
            "model": "claude-sonnet-4-6",
            "changed_files": [],
            "started_at": "2026-11-14T10:01:00Z",
            "finished_at": "2026-11-14T10:01:03Z",
            "metrics": {
                "provider_cost_usd": cost_usd,
                "provider_tokens": {"input_tokens": 100, "output_tokens": 20},
            },
        }

    def test_repaired_compact_plan_uses_the_pinned_nullable_reference_rules(self):
        token = retention.token(self.state)
        schema = {
            "type": "object",
            "properties": {
                "contract": {
                    "anyOf": [
                        {
                            "properties": {
                                "revise_contract": {"enum": [token]},
                                "changes": {
                                    "properties": {"deliverables": {"anyOf": [{"type": "array"}, {"type": "null"}]}}
                                },
                            }
                        }
                    ]
                }
            },
        }
        original = self._stage_files("revise", stage="revise_plan", role="planner", report_obj={}, schema_obj=schema)
        self._queue_after_one_local_repair(original)
        self.assertTrue(resolve_repair_job.queue_from_pending(self.state, self.run_dir))
        report = {"contract": {"revise_contract": token, "changes": {"deliverables": None}}}
        expected = self.state["goal_contract"]["body"]["deliverables"]

        def apply_plan(state, stage, value, record, workspace, run_dir):
            expanded = retention.expand(state, stage, value)
            self.assertEqual(expected, expanded["contract"]["deliverables"])

        runtime, calls = self._stub_runtime(fail_apply=apply_plan)
        record = self._resolve_record("res-revision")
        resolve_repair_job.apply(
            runtime,
            self.state,
            {
                "action": "repair_report",
                "diagnosis": "Correct the revision",
                "repaired_report_json": json.dumps(report),
            },
            record,
            self.root,
            self.run_dir,
        )
        self.assertEqual({}, calls[0][1]["contract"]["changes"])
        self.assertIsNone(report["contract"]["changes"]["deliverables"])

    def test_direct_report_fix_across_planning_builder_tester_provenance_and_single_charge(self):
        # 1. draft_plan direct JSON repair -> repair_provenance.witness + single cost + 0 blind re-runs
        self.state["goal_contract"]["approval_status"] = "draft"
        self.state["goal_contract"].pop("approval_event", None)
        schema = {
            "type": "object",
            "required": ["summary"],
            "properties": {"summary": {"type": "string"}},
        }
        orig_plan = self._stage_files("draft", stage="draft_plan", role="planner", report_obj={}, schema_obj=schema)
        self._queue_after_one_local_repair(orig_plan)
        self.assertTrue(resolve_repair_job.eligible_report_repair(self.state))
        self.assertTrue(resolve_repair_job.queue_from_pending(self.state, self.run_dir))
        self.assertEqual("resolve_repair", self.state["next_stage"])

        rt, calls = self._stub_runtime()
        res_rec = self._resolve_record("res-plan", cost_usd=0.42)
        autopilot.apply_result(
            rt,
            self.state,
            "resolve_repair",
            {
                "diagnosis": "Draft report missed required summary",
                "action": "repair_report",
                "repaired_report_json": json.dumps({"summary": "Clean greeting plan"}),
                "guidance": "",
                "next_objective": "",
                "affected_paths": [],
                "user_question": "",
                "user_options": [],
                "evidence_refs": [],
            },
            res_rec,
            self.root,
            self.run_dir,
        )
        self.assertEqual(1, len(calls))
        repaired_out = calls[0][2]
        self.assertTrue(Path(repaired_out).is_file())
        witness_orig, _ = repair_provenance.witness(self.state, "draft_plan", repaired_out)
        self.assertEqual(orig_plan["output"], witness_orig["output"])
        self.assertFalse(
            rejection_retry.admit(
                self.state, "draft_plan", "PAUSED_INVALID_OUTPUT", [{"rejected": True}], at=util.now()
            )
        )
        rows = usage.stage_rows(self.state)
        resolve_charges = [r for r in rows if r["stage"] == "draft_plan_report_repair"]
        runner_rows = [r for r in rows if r["stage"] == "resolve_repair"]
        self.assertEqual(1, len(resolve_charges))
        self.assertEqual(0.42, resolve_charges[0]["cost_usd"])
        self.assertEqual("runner", runner_rows[0]["basis"])
        self.assertEqual(0.0, runner_rows[0]["cost_usd"])
        repair_stage = next(s for s in self.state["stages"] if s.get("stage") == "draft_plan_report_repair")
        self.assertEqual("resolve_repair", repair_stage.get("resolved_by"))

        # 2. builder report repair -> code_checkpoints.builder_execution passes
        self.state["stages"].clear()
        self.state["history"].clear()
        self.state["report_repair_history"].clear()
        self.state["resolve_repair_history"].clear()
        self.state = _base_state(self.root, self.run_dir, self.revision)
        orig_builder = self._stage_files(
            "build",
            stage="builder",
            role="builder",
            report_obj={"summary": "partial"},
            schema_obj=schema,
            changed_files=["app.py"],
            commands=[("c1", "pytest -q", 0)],
        )
        self._queue_after_one_local_repair(orig_builder)
        self.assertTrue(resolve_repair_job.queue_from_pending(self.state, self.run_dir))
        rt_b, _ = self._stub_runtime()
        autopilot.apply_result(
            rt_b,
            self.state,
            "resolve_repair",
            {
                "diagnosis": "Builder omitted summary",
                "action": "repair_report",
                "repaired_report_json": json.dumps({"summary": "Built app.py"}),
                "guidance": "",
                "next_objective": "",
                "affected_paths": [],
                "user_question": "",
                "user_options": [],
                "evidence_refs": ["event:c1"],
            },
            self._resolve_record("res-build"),
            self.root,
            self.run_dir,
        )
        accepted_builder = next(r for r in self.state["stages"] if r.get("stage") == "builder_report_repair")
        exec_orig = code_checkpoints.builder_execution(self.state, accepted_builder, accepted_builder)
        self.assertEqual(orig_builder["output"], exec_orig["output"])

        # 3. tester compact edits patch against pinned rejected report
        orig_tester = self._stage_files(
            "test",
            stage="tester",
            role="tester",
            report_obj={"summary": "bad", "verdict": "PASS"},
            schema_obj=schema,
            commands=[("t1", "pytest -q", 0)],
        )
        self._queue_after_one_local_repair(orig_tester)
        latest_path = Path(self.state["pending_report_repair"]["latest_rejected"]["output"])
        latest_path.write_text(json.dumps({"summary": "old", "verdict": "PASS"}))
        self.state["pending_report_repair"]["pins"][str(latest_path)] = util.file_hash(latest_path)
        self.assertTrue(resolve_repair_job.queue_from_pending(self.state, self.run_dir))
        rt_t, calls_t = self._stub_runtime()
        autopilot.apply_result(
            rt_t,
            self.state,
            "resolve_repair",
            {
                "diagnosis": "Patched tester summary",
                "action": "repair_report",
                "repaired_report_json": json.dumps(
                    {"edits": [{"path": "summary", "action": "set", "value": "Verified PASS"}]}
                ),
                "guidance": "",
                "next_objective": "",
                "affected_paths": [],
                "user_question": "",
                "user_options": [],
                "evidence_refs": ["event:t1"],
            },
            self._resolve_record("res-test"),
            self.root,
            self.run_dir,
        )
        self.assertEqual("Verified PASS", calls_t[0][1]["summary"])

    def test_fabricated_command_is_refused_and_cleans_up_repaired_file(self):
        schema = {"type": "object", "properties": {"commands_run": {"type": "array", "items": {"type": "string"}}}}
        orig_tester = self._stage_files(
            "test-real",
            stage="tester",
            role="tester",
            report_obj={},
            schema_obj=schema,
            commands=[("c1", "pytest -q", 0)],
        )
        self._queue_after_one_local_repair(orig_tester)
        self.assertTrue(resolve_repair_job.queue_from_pending(self.state, self.run_dir))

        def check_no_fabrication(state, stage, value, record, workspace, run_dir):
            if "pytest --fabricated" in value.get("commands_run", []):
                raise ValueError("invented a command not in original events")

        rt, _ = self._stub_runtime(fail_apply=check_no_fabrication)
        with self.assertRaisesRegex(ValueError, "invented a command"):
            autopilot.apply_result(
                rt,
                self.state,
                "resolve_repair",
                {
                    "diagnosis": "Attempted fabricated command",
                    "action": "repair_report",
                    "repaired_report_json": json.dumps({"commands_run": ["pytest --fabricated"]}),
                    "guidance": "",
                    "next_objective": "",
                    "affected_paths": [],
                    "user_question": "",
                    "user_options": [],
                    "evidence_refs": [],
                },
                self._resolve_record("res-fab"),
                self.root,
                self.run_dir,
            )
        repaired_candidate = Path(orig_tester["output"]).with_name("test-real.resolve-repaired.json")
        self.assertFalse(repaired_candidate.exists())

    def test_verifier_independence_selects_independent_route(self):
        self.state["settings"]["engine"] = "opencode"
        self.state["settings"]["roles"]["resolver"] = {"model": "gpt-5.4", "reasoning_effort": "high"}
        self.state["settings"]["roles"]["task_planner"] = {"model": "gpt-5.4", "reasoning_effort": "high"}
        self.state["settings"]["roles"]["builder"] = {"model": "gpt-5.3-codex", "reasoning_effort": "high"}
        self.state["settings"]["roles"]["plan_reviewer"] = {"model": "claude-sonnet-4-6", "reasoning_effort": "high"}

        chosen = resolve_repair_job.select_route(self.state, "tester")
        vendor = verifier_independence.same_vendor_engine(self.state["settings"])
        self.assertFalse(verifier_independence.shares("gpt-5.3-codex", chosen["model"], vendor_engine=vendor))
        self.assertEqual("claude-sonnet-4-6", chosen["model"])
        self.assertEqual(chosen, self.state["settings"]["roles"][resolve_repair_job.ROUTE])

        # Under codex vendor_engine, identical model identity also triggers fallback.
        self.state["settings"]["engine"] = "codex"
        self.state["settings"]["roles"]["builder"] = {"model": "gpt-5.4", "reasoning_effort": "high"}
        chosen_codex = resolve_repair_job.select_route(self.state, "tester")
        self.assertEqual("claude-sonnet-4-6", chosen_codex["model"])

        resolve_repair_job.release_route(self.state)
        self.assertNotIn(resolve_repair_job.ROUTE, self.state["settings"]["roles"])

    def test_upfront_rejection_triage_separates_tests_failed_and_missing_execution(self):
        # tests_failed bypasses pending_report_repair -> routes directly to builder rework
        tester_fail = self._stage_files(
            "tester-fail",
            stage="tester",
            role="tester",
            report_obj={},
            commands=[("c1", "pytest -q", 1)],
        )
        self.state["next_stage"] = "tester"
        err = verification_plan.PlannedCheckFailure("pytest -q exited 1")
        self.assertEqual("tests_failed", repair_triage.classify_rejection(self.state, tester_fail, err))
        self.assertTrue(rejection_route.reroute(self.state, tester_fail, err, lambda *a, **k: None))
        self.assertEqual("builder", self.state["next_stage"])
        self.assertNotIn("pending_report_repair", self.state)

        # When builder retries already paused, tests_failed routes into resolve_repair stall
        ident = builder_policy.key(self.state)
        self.state["builder_retries"] = {ident: {"action": "pause", "failures": ["f1"]}}
        tester_fail2 = self._stage_files(
            "tester-fail-2",
            stage="tester",
            role="tester",
            report_obj={},
            commands=[("c2", "pytest -q", 1)],
        )
        self.state["next_stage"] = "tester"
        self.assertTrue(rejection_route.reroute(self.state, tester_fail2, err, lambda *a, **k: None))
        self.assertEqual("resolve_repair", self.state["next_stage"])
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", self.state["resolve_repair_request"]["pause_status"])

        # missing_execution routes directly to resolve_repair, and rerun is bounded to 1 per revision
        self.state.pop("resolve_repair_request", None)
        self.state["builder_retries"] = {}
        self.state["next_stage"] = "tester"
        tester_unrun = self._stage_files("tester-unrun", stage="tester", role="tester", report_obj={}, commands=[])
        miss_err = ValueError("Technically passing validation lacks successful executed checks")
        self.assertEqual("missing_execution", repair_triage.classify_rejection(self.state, tester_unrun, miss_err))
        self.assertTrue(rejection_route.reroute(self.state, tester_unrun, miss_err, lambda *a, **k: None))
        self.assertEqual("resolve_repair", self.state["next_stage"])
        rt, _ = self._stub_runtime()
        autopilot.apply_result(
            rt,
            self.state,
            "resolve_repair",
            {
                "diagnosis": "Tester never ran pytest",
                "action": "rerun_missing_execution",
                "repaired_report_json": "",
                "guidance": "Run pytest -q from repo root.",
                "next_objective": "",
                "affected_paths": [],
                "user_question": "",
                "user_options": [],
                "evidence_refs": [],
            },
            self._resolve_record("res-miss"),
            self.root,
            self.run_dir,
        )
        self.assertEqual("tester", self.state["next_stage"])
        # Second missing_execution on same stage & source_revision is refused
        self.assertFalse(resolve_repair_job.queue_missing_execution(self.state, tester_unrun, miss_err))

    def test_triage_leaves_a_false_report_to_its_route_and_bounds_builder_send_backs(self):
        # A check reported as passing that fails the clean replay is a false report, not a failing
        # test: the Builder has nothing to build, so triage leaves it to the reviewer rerun and the
        # existing route (the offline demo's refused check, which looped Builder until the ceiling).
        false_claim = self._stage_files(
            "tester-false", stage="tester", role="tester", report_obj={}, commands=[("c1", "test -f x", 0)]
        )
        self.state["next_stage"] = "tester"
        reported = verification_plan.ReportedCheckFailure("`test -f x` was reported as exit 0 but exited 1")
        self.assertEqual("report_defect", repair_triage.classify_rejection(self.state, false_claim, reported))
        self.assertFalse(repair_triage.reroute(self.state, false_claim, reported))
        self.assertEqual("tester", self.state["next_stage"])
        self.assertNotIn("resolve_repair_history", self.state)

        # A failing planned check sends the Builder back once for the task, not once per source:
        # every Builder round makes a new revision.
        failed = verification_plan.PlannedCheckFailure("pytest -q exited 1")
        first = self._stage_files(
            "tester-fail-a", stage="tester", role="tester", report_obj={}, commands=[("c2", "pytest -q", 1)]
        )
        self.assertTrue(repair_triage.reroute(self.state, first, failed))
        self.assertEqual("builder", self.state["next_stage"])
        self.state["next_stage"] = "tester"
        second = dict(
            self._stage_files(
                "tester-fail-b", stage="tester", role="tester", report_obj={}, commands=[("c3", "pytest -q", 1)]
            ),
            source_revision="another-revision",
        )
        self.assertFalse(repair_triage.reroute(self.state, second, failed))
        self.assertEqual("tester", self.state["next_stage"])
        self.assertEqual(1, len(self.state["resolve_repair_history"]))

    def test_mid_stage_stalls_rework_replan_ask_user_and_stop_guard(self):
        # 1. PAUSED_BUILDER_RETRY_LIMIT intercepted before investigator via stuck_job.drive
        ident = builder_policy.key(self.state)
        self.state["next_stage"] = "builder"
        self.state["builder_retries"] = {
            ident: {
                "action": "pause",
                "position": 2,
                "failures": ["f1"],
                "initial_route": {"model": "gpt-5.3-codex", "reasoning_effort": "high"},
            }
        }
        prior = self._stage_files("prev-build", stage="builder", role="builder", report_obj={})

        dispatched_stages = []

        def fake_dispatch(state, stage):
            dispatched_stages.append(stage)
            if stage == "builder" and len(dispatched_stages) == 1:
                state["stages"].append(prior)
                raise support.Paused("PAUSED_BUILDER_RETRY_LIMIT", "Exhausted retries")
            if stage == "resolve_repair":
                # Verify guidance rejects unchanged approach, then accepts changed approach
                unchanged = {
                    "diagnosis": "Retry same thing",
                    "action": "rework_tests_failed",
                    "repaired_report_json": "",
                    "guidance": "Retry",
                    "next_objective": state["current_task"]["objective"],
                    "affected_paths": list(state["current_task"]["affected_paths"]),
                    "user_question": "",
                    "user_options": [],
                    "evidence_refs": [],
                }
                with self.assertRaisesRegex(ValueError, "must change next_objective or affected_paths"):
                    resolve_repair_job.apply(
                        None,
                        state,
                        unchanged,
                        self._resolve_record("bad-rework"),
                        self.root,
                        self.run_dir,
                    )
                resolve_repair_job.apply(
                    None,
                    state,
                    {
                        **unchanged,
                        "action": "rework_tests_failed",
                        "guidance": "Handle None name before formatting.",
                        "next_objective": "Fix empty-input handling in app.py",
                        "affected_paths": ["app.py", "tests/test_app.py"],
                    },
                    self._resolve_record("good-rework"),
                    self.root,
                    self.run_dir,
                )
                return None
            if stage == "builder":
                req = unit_request.prepare(
                    state,
                    "builder",
                    self.run_dir / "state.json",
                    self.root,
                    select_unit=lambda _s: SimpleNamespace(
                        prepare=lambda *a, **kw: ModelRequest("builder", "builder", "BASE", {}, {}, True)
                    ),
                )
                self.assertIn("RESOLVER GUIDANCE (resolve_repair)", req.prompt)
                state["status"] = "TASK_COMPLETE"
                return None
            return None

        stuck_job.drive(
            self.state,
            fake_dispatch,
            active=lambda s: s.get("status") == "RUNNING",
            skip=object(),
            paused=support.Paused,
            investigate=True,
            queue=True,
        )
        self.assertEqual(["builder", "resolve_repair", "builder"], dispatched_stages)
        self.assertNotIn("investigate_stuck", dispatched_stages)
        self.assertEqual("retry", self.state["builder_retries"][ident]["action"])
        self.assertIsNone(self.state.get("resolve_repair_request"))

        # 2. PAUSED_MILESTONE_STALLED -> replan invalidates approved contract & routes to draft_plan
        self.state["status"] = "RUNNING"
        self.state["next_stage"] = "tester"
        self.assertTrue(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_MILESTONE_STALLED", "Milestone stalled after replan", before_investigator=True
            )
        )
        resolve_repair_job.apply(
            None,
            self.state,
            {
                "diagnosis": "Milestone scope is impossible without API split",
                "action": "replan",
                "repaired_report_json": "",
                "guidance": "Split M1 into sync helper + CLI adapter.",
                "next_objective": "",
                "affected_paths": [],
                "user_question": "",
                "user_options": [],
                "evidence_refs": [],
            },
            self._resolve_record("res-replan"),
            self.root,
            self.run_dir,
        )
        self.assertEqual("PLANNING", self.state["phase"])
        self.assertEqual("draft_plan", self.state["next_stage"])
        self.assertFalse(goals.approved(self.state))
        self.assertEqual(1, len(self.state.get("contract_history") or ()))

        # 3. ask_user enters WAITING_FOR_USER and blocks machinery strings
        self.state["status"] = "RUNNING"
        self.state["next_stage"] = "builder"
        self.assertTrue(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_NO_PROGRESS", "Ambiguous output format", before_investigator=False
            )
        )
        with self.assertRaisesRegex(ValueError, "internal machinery"):
            resolve_repair_job.apply(
                None,
                self.state,
                {
                    "diagnosis": "Bad question",
                    "action": "ask_user",
                    "repaired_report_json": "",
                    "guidance": "",
                    "next_objective": "",
                    "affected_paths": [],
                    "user_question": "Please run --retry-report now.",
                    "user_options": ["ok"],
                    "evidence_refs": [],
                },
                self._resolve_record("res-mach"),
                self.root,
                self.run_dir,
                ask_user=lifecycle.wait_for_user,
            )
        rt_ask, _ = self._stub_runtime()
        ask_rec = self._resolve_record("res-ask")
        autopilot.apply_result(
            rt_ask,
            self.state,
            "resolve_repair",
            {
                "diagnosis": "Greeting format unspecified",
                "action": "ask_user",
                "repaired_report_json": "",
                "guidance": "",
                "next_objective": "",
                "affected_paths": [],
                "user_question": "Should greet() accept empty strings or raise ValueError?",
                "user_options": ["Raise ValueError", "Return default greeting"],
                "evidence_refs": [],
            },
            ask_rec,
            self.root,
            self.run_dir,
        )
        self.assertEqual("RESOLVER_PENDING", self.state["status"])
        self.assertEqual("escalate", resolver_human.evaluate(self.state))
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual(
            "Should greet() accept empty strings or raise ValueError?",
            self.state["pending_questions"][0]["question"],
        )

        # 4. Terminal applied_stop blocks triage, interception, queueing, and apply
        self.state["applied_interventions"] = [{"kind": "stop", "id": "stop-1"}]
        self.assertFalse(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_MILESTONE_STALLED", "Stopped", before_investigator=True
            )
        )
        with self.assertRaises(support.Paused) as paused:
            resolve_repair_job.apply(
                None,
                self.state,
                {
                    "diagnosis": "Should be refused",
                    "action": "hold",
                    "repaired_report_json": "",
                    "guidance": "",
                    "next_objective": "",
                    "affected_paths": [],
                    "user_question": "",
                    "user_options": [],
                    "evidence_refs": [],
                },
                self._resolve_record("res-stop"),
                self.root,
                self.run_dir,
            )
        self.assertEqual(stop.STOP_STATUS, paused.exception.status)

    def test_a_hold_restores_the_pause_with_its_own_reason_first(self):
        # The pause's own reason says what to fix (here, which limit was reached); the Resolver's
        # diagnosis is added after it, never in its place.
        self.state["next_stage"] = "builder"
        reason = "Builder retry limit reached: 3/3 attempts used"
        self.assertTrue(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_BUILDER_RETRY_LIMIT", reason, before_investigator=True
            )
        )
        resolve_repair_job.apply(
            None,
            self.state,
            {
                "diagnosis": "No different approach fits the approved plan.",
                "action": "hold",
                "repaired_report_json": "",
                "guidance": "",
                "next_objective": "",
                "affected_paths": [],
                "user_question": "",
                "user_options": [],
                "evidence_refs": [],
            },
            self._resolve_record("res-hold"),
            self.root,
            self.run_dir,
        )
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", self.state["status"])
        self.assertEqual(
            reason + "\nResolver (held): No different approach fits the approved plan.", self.state["stop_reason"]
        )
        self.assertEqual("builder", self.state["next_stage"])

    def test_planning_calls_counts_resolve_repair_and_enforces_cap(self):
        schema = {"type": "object", "required": ["summary"], "properties": {"summary": {"type": "string"}}}
        orig = self._stage_files("plan-orig", stage="draft_plan", role="planner", report_obj={}, schema_obj=schema)
        self._queue_after_one_local_repair(orig)
        self.assertEqual(1, planning_calls.used(self.state))

        self.assertTrue(resolve_repair_job.queue_from_pending(self.state, self.run_dir))
        self.assertTrue(planning_calls.launching(self.state))
        self.assertIsNone(planning_calls.reached(self.state))

        rt, _ = self._stub_runtime()
        autopilot.apply_result(
            rt,
            self.state,
            "resolve_repair",
            {
                "diagnosis": "Fixed draft_plan summary",
                "action": "repair_report",
                "repaired_report_json": json.dumps({"summary": "Drafted greeting plan"}),
                "guidance": "",
                "next_objective": "",
                "affected_paths": [],
                "user_question": "",
                "user_options": [],
                "evidence_refs": [],
            },
            self._resolve_record("plan-res"),
            self.root,
            self.run_dir,
        )
        # 1 original draft_plan + 1 repair_record (not double-counted) = 2
        self.assertEqual(2, planning_calls.used(self.state))

        # Non-repair resolve_repair on planning stage counts via resolve_repair_history
        self.state["next_stage"] = "draft_plan"
        self.assertTrue(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_PLANNING_BUDGET", "Planning budget", before_investigator=False
            )
        )
        resolve_repair_job.apply(
            None,
            self.state,
            {
                "diagnosis": "Hold planning stall",
                "action": "hold",
                "repaired_report_json": "",
                "guidance": "",
                "next_objective": "",
                "affected_paths": [],
                "user_question": "",
                "user_options": [],
                "evidence_refs": [],
            },
            self._resolve_record("plan-hold"),
            self.root,
            self.run_dir,
        )
        self.assertEqual(3, planning_calls.used(self.state))

        # Reached triggers at cap == used
        self.state["settings"]["limits"]["planning_calls"] = 3
        self.state["next_stage"] = "draft_plan"
        self.assertEqual((3, 3), planning_calls.reached(self.state))

    def test_person_and_operator_owned_pauses_bypass_resolve_repair(self):
        # 1. Operator-authorized --retry-failed-stage returns directly to the operator without Resolver/Investigator
        orig = self._stage_files("op-tester", stage="tester", role="tester", report_obj={"verdict": "pass"})
        orig["failure_key"] = "fk-tester-1"
        self._queue_after_one_local_repair(orig)
        self.state["stages"][-1]["failure_key"] = "fk-tester-1"
        self.state["failure_retry_authorizations"] = [{"failure_key": "fk-tester-1", "consumed": True}]
        self.assertTrue(resolve_repair_job.operator_retried(self.state, "tester"))
        self.assertIs(stuck_job.operator_retried, resolve_repair_job.operator_retried)
        self.assertIs(stuck_job.persons_review_total, resolve_repair_job.persons_review_total)
        self.assertFalse(resolve_repair_job.eligible_report_repair(self.state))
        self.assertFalse(resolve_repair_job.queue_from_pending(self.state, self.run_dir, force=True))
        self.assertFalse(
            resolve_repair_job.queue_missing_execution(self.state, orig, ValueError("lacks successful executed checks"))
        )
        self.assertFalse(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_REPORT_REPAIR_LIMIT", "Exhausted local repairs", before_investigator=True
            )
        )
        self.assertFalse(
            resolve_repair_job.intercept_stall(
                self.state,
                "PAUSED_REPEATED_FAILURE",
                "Repeated failure after --retry-failed-stage",
                before_investigator=False,
            )
        )
        dispatched: list[str] = []

        def fail_tester(_current, stage):
            dispatched.append(stage)
            raise support.Paused("PAUSED_REPEATED_FAILURE", "Repeated failure after --retry-failed-stage")

        with self.assertRaises(support.Paused) as paused:
            stuck_job.drive(
                self.state,
                fail_tester,
                active=lambda s: s.get("status") == "RUNNING",
                skip=object(),
                paused=support.Paused,
                investigate=True,
                queue=True,
            )
        self.assertEqual("PAUSED_REPEATED_FAILURE", paused.exception.status)
        self.assertEqual(["tester"], dispatched)
        self.assertIsNone(self.state.get("resolve_repair_request"))

        # 2. Person-chosen review total (--planning-review-call-limit) and progressive retained review budget
        self.state.pop("failure_retry_authorizations", None)
        self.state["next_stage"] = "review_plan"
        self.state["planning"] = {"review_call_limit_origin": "user_explicit"}
        self.assertTrue(resolve_repair_job.persons_review_total(self.state, "PAUSED_PLANNING_BUDGET"))
        self.assertFalse(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_PLANNING_BUDGET", "User review call limit reached", before_investigator=False
            )
        )
        self.state.pop("planning", None)
        self.state["progressive"] = {"budget": {"slice_review_calls": 2}}
        self.assertFalse(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_PLANNING_BUDGET", "Retained slice review ceiling", before_investigator=False
            )
        )

        # 3. Pre-v3 legacy run state declines all resolve_repair entry points
        self.state.pop("progressive", None)
        self.state["version"] = 2
        self.state["next_stage"] = "tester"
        self.assertFalse(resolve_repair_job.eligible_report_repair(self.state))
        self.assertFalse(resolve_repair_job.queue_from_pending(self.state, self.run_dir, force=True))
        self.assertFalse(resolve_repair_job.queue_missing_execution(self.state, orig, ValueError("missing")))
        self.assertFalse(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_NO_PROGRESS", "Legacy run", before_investigator=False
            )
        )

    def test_stray_writes_raise_stray_writes_and_undo_restores_workspace(self):
        before_path = self.run_dir / "before.snap.json"
        after_path = self.run_dir / "after.snap.json"
        util.atomic_json(before_path, support.snapshot(self.root))
        (self.root / "app.py").write_text("def greet():\n    return 'tampered'\n")
        util.atomic_json(after_path, support.snapshot(self.root))
        record = {
            **self._resolve_record("res-stray"),
            "changed_files": ["app.py"],
            "before_ref": str(before_path),
            "after_ref": str(after_path),
        }
        with self.assertRaises(stray_writes.StrayWrites) as err:
            resolve_repair_job.apply(
                None,
                self.state,
                {
                    "diagnosis": "",
                    "action": "hold",
                    "repaired_report_json": "",
                    "guidance": "",
                    "next_objective": "",
                    "affected_paths": [],
                    "user_question": "",
                    "user_options": [],
                    "evidence_refs": [],
                },
                record,
                self.root,
                self.run_dir,
            )
        self.assertEqual(["app.py"], err.exception.paths)
        restored = stray_writes.undo(self.state, record, err.exception)
        self.assertIsInstance(restored, stray_writes.StrayWrites)
        self.assertIn("restored app.py", str(restored))
        self.assertEqual("def greet():\n    return 'hi'\n", (self.root / "app.py").read_text())

    def test_repeated_failure_non_rejection_guards_and_opencode_handoff(self):
        import os
        import shutil
        import sys
        from unittest import mock

        from units import autoresolver

        # 1. PAUSED_REPEATED_FAILURE declines non-completed-rejection stops:
        #    - target_stage == 'resolve'
        #    - active pending_report_repair (partial repair stop owned by autocode_report_retry)
        #    - denied_operation operational hold
        #    - missing, unrejected, timed-out, interrupted, abandoned, or non-zero-exit stage rows
        self.state["next_stage"] = "resolve"
        self.state["stages"] = [{"stage": "resolve", "rejected": True, "exit_code": 0}]
        self.assertFalse(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_REPEATED_FAILURE", "Repeated resolver failure", before_investigator=False
            )
        )

        self.state["next_stage"] = "tester"
        self.state["stages"] = [{"stage": "tester_report_repair", "original_stage": "tester", "rejected": True}]
        self.state["pending_report_repair"] = {"attempts": 1, "original": {"stage": "tester"}}
        self.assertFalse(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_REPEATED_FAILURE", "Partial report repair bound", before_investigator=False
            )
        )
        self.state.pop("pending_report_repair", None)

        self.state["recovery_context"] = {"denied_operation": "external_directory"}
        self.state["stages"] = [{"stage": "tester", "rejected": True, "exit_code": 0}]
        self.assertFalse(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_REPEATED_FAILURE", "Repeated permission hold", before_investigator=False
            )
        )
        self.state.pop("recovery_context", None)

        for bad_rows in (
            [],
            [{"stage": "tester", "rejected": False, "exit_code": 0}],
            [{"stage": "tester", "rejected": True, "timed_out": True, "abandoned": True, "exit_code": 0}],
            [{"stage": "tester", "rejected": True, "interrupted": True, "exit_code": 0}],
            [{"stage": "tester", "rejected": True, "abandoned": True, "exit_code": 0}],
            [{"stage": "tester", "rejected": True, "exit_code": 1}],
        ):
            self.state["stages"] = bad_rows
            self.assertFalse(
                resolve_repair_job.intercept_stall(
                    self.state, "PAUSED_REPEATED_FAILURE", "Non-completed stop", before_investigator=False
                ),
                bad_rows,
            )

        # Genuine completed-stage repeated rejection IS intercepted into resolve_repair
        self.state["stages"] = [{"stage": "tester", "rejected": True, "exit_code": 0}]
        self.assertTrue(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_REPEATED_FAILURE", "Repeated rejected output", before_investigator=False
            )
        )
        self.assertEqual("resolve_repair", self.state["next_stage"])
        self.assertEqual("codex", resolve_repair_job.handoff({"settings": {}})["execution_engine"])
        self.assertEqual("opencode", resolve_repair_job.handoff(self.state)["execution_engine"])

        # 2. OpenCode prepare_resolve_repair populates transport_identities['opencode'] and execution_engine
        bin_dir = self.root / "fixture-bin"
        bin_dir.mkdir(exist_ok=True)
        repo_tools = Path(__file__).resolve().parents[1] / "tools"
        for src_name, dst_name in (
            ("fake_opencode.py", "opencode"),
            ("fake_codex.py", "codex"),
            ("goal_fixtures.py", "goal_fixtures.py"),
        ):
            dst = bin_dir / dst_name
            shutil.copy2(repo_tools / src_name, dst)
            dst.chmod(0o755)
        self.state["workspace"] = str(self.root)
        self.state["settings"]["execution_engine"] = "opencode"
        self.state["settings"]["provider"] = "opencode"
        self.state["settings"].pop("transport_identities", None)
        self.state["settings"]["roles"][resolve_repair_job.ROUTE] = {
            "model": "zai-coding-plan/glm-5.3",
            "reasoning_effort": "high",
            "engine": "opencode",
        }
        patched_env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
        with mock.patch.dict(os.environ, patched_env, clear=True):
            req = autoresolver.prepare(self.state, resolve_repair_job.STAGE, self.run_dir / "state.json", None)
        self.assertIn("opencode", self.state["settings"]["transport_identities"])
        handoff_json = json.loads(req.prompt.split("\nCURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual("opencode", handoff_json["execution_engine"])
        self.assertEqual("opencode", resolve_repair_job.handoff(self.state)["execution_engine"])

        # Relay through fake_opencode.py to prove the OpenCode wire contract succeeds end-to-end
        prefix, handoff_text = req.prompt.split("\nCURRENT HANDOFF DATA\n", 1)
        full_prompt = (
            prefix
            + "\nOPENCODE OUTPUT CONTRACT\nReturn JSON matching:\n"
            + json.dumps(req.schema, indent=2)
            + "\nCURRENT HANDOFF DATA\n"
            + handoff_text
        )
        opencode_cfg = {
            "share": "disabled",
            "agent": {"autocode_resolver": {"permission": {"task": "deny", "question": "deny", "edit": "deny"}}},
        }
        proc = subprocess.run(
            [sys.executable, str(bin_dir / "opencode"), "run", "--format", "json", "--agent", "autocode_resolver"],
            input=full_prompt,
            text=True,
            capture_output=True,
            env={**patched_env, "OPENCODE_CONFIG_CONTENT": json.dumps(opencode_cfg)},
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)

    def test_temporary_route_transport_release_and_non_opencode_settings_preservation(self):
        from units import autoresolver

        kilo_identity = {
            "provider": "kilo",
            "identity": {
                "engine": "kilo",
                "identity_version": 2,
                "executable": "/usr/local/bin/kilo",
                "launch_policy_sha256": "a" * 64,
            },
        }
        opencode_identity = {
            "engine": "opencode",
            "identity_version": 1,
            "executable": "/usr/local/bin/opencode",
            "config_sha256": "b" * 64,
        }
        self.state["settings"]["engine"] = "opencode"
        self.state["settings"]["provider"] = "opencode"
        self.state["settings"]["roles"]["resolver"] = {
            "engine": "opencode",
            "provider": "kilo",
            "model": "kilo/glm-5",
            "reasoning_effort": "high",
        }
        self.state["settings"]["transport_identities"] = {
            "opencode": opencode_identity,
            "role:resolver": kilo_identity,
        }
        orig = self._stage_files("rt-tester", stage="tester", role="tester", report_obj={"verdict": "FAIL"})
        self._queue_after_one_local_repair(orig)
        self.assertTrue(resolve_repair_job.queue_from_pending(self.state, self.run_dir, force=True))
        autoresolver.prepare_resolve_repair(self.state, self.run_dir / "state.json")
        self.assertIn("repair_resolver", self.state["settings"]["roles"])
        self.assertIn("role:repair_resolver", self.state["settings"]["transport_identities"])
        role_transport.validate(self.state["settings"])

        # Applying an invalid action must keep the active repair_resolver route intact for error inspection.
        with self.assertRaises(ValueError):
            resolve_repair_job.apply(
                None,
                self.state,
                {
                    "diagnosis": "Invalid empty replan",
                    "action": "replan",
                    "repaired_report_json": "",
                    "guidance": "",
                    "next_objective": "",
                    "affected_paths": [],
                    "user_question": "",
                    "user_options": [],
                    "evidence_refs": [],
                },
                self._resolve_record("rt-bad"),
                self.root,
                self.run_dir,
            )
        self.assertIn("repair_resolver", self.state["settings"]["roles"])

        resolve_repair_job.release_route(self.state)
        self.assertNotIn("repair_resolver", self.state["settings"]["roles"])
        self.assertNotIn("role:repair_resolver", self.state["settings"]["transport_identities"])
        role_transport.validate(self.state["settings"])

        # Non-OpenCode runs must not receive an empty transport_identities map during prepare_resolve_repair.
        codex_state = _base_state(self.root, self.run_dir, self.revision)
        codex_state["settings"]["engine"] = "codex"
        codex_state["settings"].pop("provider", None)
        codex_state["settings"].pop("transport_identities", None)
        codex_state["settings"]["roles"]["resolver"] = {
            "engine": "codex",
            "model": "gpt-5.4",
            "reasoning_effort": "high",
        }
        codex_state["resolve_repair_request"] = {"kind": "stall", "stage": "tester"}
        autoresolver.prepare_resolve_repair(codex_state, self.run_dir / "state.json")
        self.assertNotIn("transport_identities", codex_state["settings"])

    def test_drive_preserves_stop_now_and_retry_completions_and_restores_pause_state_on_timeout(self):
        # Case A: User Stop Now via job_failure leaves applied_stop + recovery_context['stage'] == 'resolve_repair'
        self.state["next_stage"] = "builder"
        self.assertTrue(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_BUILDER_RETRY_LIMIT", "Builder exhausted", before_investigator=True
            )
        )

        def stop_now_dispatch(state, stage):
            self.assertEqual("resolve_repair", stage)
            state["recovery_context"] = {"kind": "job_failure", "stage": "resolve_repair", "reason": "Stopped by user"}
            state["applied_interventions"] = [{"id": "int-1", "kind": "stop", "actor": "person"}]
            run_status.pause(
                state,
                "PAUSED_INTERVENTION",
                stop.STOPPED_NOW_REASON,
                next_stage="resolve_repair",
            )
            return None

        stuck_job.drive(
            self.state,
            stop_now_dispatch,
            active=lambda s: s.get("status") == "RUNNING",
            skip=object(),
            paused=support.Paused,
            investigate=True,
            queue=True,
        )
        self.assertEqual("PAUSED_INTERVENTION", self.state["status"])
        self.assertEqual(stop.STOPPED_NOW_REASON, self.state["stop_reason"])

        # Case B: Raised PAUSED_INTERVENTION during resolve_repair preserves PAUSED_INTERVENTION & releases route
        self.state = _base_state(self.root, self.run_dir, self.revision)
        self.state["next_stage"] = "builder"
        self.assertTrue(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_BUILDER_RETRY_LIMIT", "Builder exhausted", before_investigator=True
            )
        )

        def raise_intervention(_state, _stage):
            raise support.Paused("PAUSED_INTERVENTION", stop.STOP_REASON)

        with self.assertRaises(support.Paused) as paused_err:
            stuck_job.drive(
                self.state,
                raise_intervention,
                active=lambda s: s.get("status") == "RUNNING",
                skip=object(),
                paused=support.Paused,
                investigate=True,
                queue=True,
            )
        self.assertEqual("PAUSED_INTERVENTION", paused_err.exception.status)
        self.assertNotIn("repair_resolver", self.state["settings"]["roles"])

        # Case C: Attempt-2 ask_user after attempt-1 deadline extension clears recovery_context & keeps WAITING_FOR_USER
        self.state = _base_state(self.root, self.run_dir, self.revision)
        self.state["next_stage"] = "builder"
        self.assertTrue(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_BUILDER_RETRY_LIMIT", "Builder exhausted", before_investigator=True
            )
        )
        self.state["recovery_context"] = {
            "kind": "job_failure",
            "stage": "resolve_repair",
            "reason": "Stage deadline extended",
        }
        rt_c, _ = self._stub_runtime()
        rec_c = self._resolve_record("retry-ask")

        def ask_user_dispatch(state, stage):
            self.assertEqual("resolve_repair", stage)
            autopilot.apply_result(
                rt_c,
                state,
                "resolve_repair",
                {
                    "diagnosis": "API contract ambiguous",
                    "action": "ask_user",
                    "repaired_report_json": "",
                    "guidance": "",
                    "next_objective": "",
                    "affected_paths": [],
                    "user_question": "Should /users return 404 or empty list?",
                    "user_options": ["Return 404", "Return empty list"],
                    "evidence_refs": [],
                },
                rec_c,
                self.root,
                self.run_dir,
            )
            return rec_c

        stuck_job.drive(
            self.state,
            ask_user_dispatch,
            active=lambda s: s.get("status") == "RUNNING",
            skip=object(),
            paused=support.Paused,
            investigate=True,
            queue=True,
        )
        self.assertEqual("RESOLVER_PENDING", self.state["status"])
        self.assertNotIn("recovery_context", self.state)
        self.assertEqual("escalate", resolver_human.evaluate(self.state))
        self.assertEqual("WAITING_FOR_USER", self.state["status"])

        # Case D: Genuine unrecovered PAUSED_JOB_FAILURE on resolve_repair abandons cleanly & persists paused state
        self.state = _base_state(self.root, self.run_dir, self.revision)
        self.state["next_stage"] = "builder"
        self.assertTrue(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_BUILDER_RETRY_LIMIT", "Builder exhausted", before_investigator=True
            )
        )
        persisted_snapshots: list[dict] = []

        def job_failure_dispatch(state, stage):
            self.assertEqual("resolve_repair", stage)
            state["job_failure"] = {"stage": "resolve_repair", "kind": "timeout", "reason": "Resolver timed out"}
            state["recovery_context"] = {
                "kind": "job_failure",
                "stage": "resolve_repair",
                "reason": "Resolver timed out",
            }
            run_status.pause(state, "PAUSED_JOB_FAILURE", "Resolver timed out", next_stage="resolve_repair")
            return None

        with self.assertRaises(support.Paused) as timeout_err:
            stuck_job.drive(
                self.state,
                job_failure_dispatch,
                persist=lambda s: persisted_snapshots.append(copy.deepcopy(s)),
                active=lambda s: s.get("status") == "RUNNING",
                skip=object(),
                paused=support.Paused,
                investigate=True,
                queue=True,
            )
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", timeout_err.exception.status)
        self.assertIn("Resolver (could not finish): Resolver timed out", str(timeout_err.exception))
        self.assertTrue(persisted_snapshots)
        saved = persisted_snapshots[-1]
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", saved["status"])
        self.assertEqual("PAUSED_OR_BLOCKED", saved["phase"])
        self.assertEqual("builder", saved["next_stage"])
        self.assertNotIn("job_failure", saved)
        self.assertNotIn("recovery_context", saved)

    def test_resolve_repair_own_rejected_report_never_self_escalates_or_leaks_pending_repair(self):
        self.state["next_stage"] = "builder"
        self.assertTrue(
            resolve_repair_job.intercept_stall(
                self.state, "PAUSED_BUILDER_RETRY_LIMIT", "Builder exhausted", before_investigator=True
            )
        )
        self.assertIsNone(self.state.get("pending_report_repair"))
        self.state["pending_report_repair"] = {
            "original": {
                "stage": "resolve_repair",
                "output": str(self.run_dir / "res.json"),
                "source_revision": self.revision,
            },
            "attempts": 1,
        }
        self.assertFalse(resolve_repair_job.eligible_report_repair(self.state))
        self.assertFalse(resolve_repair_job.queue_from_pending(self.state, self.run_dir, force=True))
        self.assertEqual("builder", self.state["resolve_repair_request"]["stage"])

        status, _ = resolve_repair_job.abandon(self.state, "Report repair limit reached")
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", status)
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", self.state["status"])
        self.assertEqual("builder", self.state["next_stage"])
        self.assertNotIn("pending_report_repair", self.state)


if __name__ == "__main__":
    unittest.main()

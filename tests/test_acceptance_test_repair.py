"""Regression tests for protected acceptance-test truthfulness and post-build repair (#3350)."""

from __future__ import annotations

import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import autocode as runner
import autocode_acceptance_test_repair as test_repair
import autocode_acceptance_tests as acceptance
import autocode_acceptance_tests_job as acceptance_job
import autocode_goals as goals
import autocode_report_check as report_check
import autocode_report_schema as report_schema
import autocode_resolver_recovery as resolver_recovery
import autocode_run_view as run_view
import autocode_stuck_job as stuck_job
import autocode_support as support
import autocode_util as util
import autocode_verify as verify
import autocode_workflow as workflow
import autopilot
from autocode_report_repair_queue import ExecutionEvidenceRequired
from units import autoresolver, autoreview, common


def _init_git(workspace: Path, files: dict[str, str]) -> str:
    workspace.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    git = ["git", "-C", str(workspace), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    return subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def _save_snapshot(run_dir: Path, name: str, workspace: Path) -> tuple[str, dict]:
    snap = util.snapshot(workspace)
    path = run_dir / f"{name}.snapshot.json"
    util.atomic_json(path, snap)
    return str(path), snap


def _contract_state(
    workspace: Path,
    run_dir: Path,
    *,
    base_commit: str,
    cases: dict[str, tuple[str, str]],
    affected_paths: list[str],
) -> dict:
    criteria = [
        {"id": cid, "criterion": text, "verification_method": method, "human_review": False}
        for cid, (text, method) in cases.items()
    ]
    body = {
        "intended_outcome": "Deliver verified event processing",
        "intended_user": "Developer",
        "deliverables": affected_paths,
        "required_behaviors": [text for text, _ in cases.values()],
        "important_failure_cases": ["Missing transition handling"],
        "scope_exclusions": [],
        "constraints": [],
        "permission_boundaries": [],
        "accepted_assumptions": [],
        "delegated_decisions": [],
        "acceptance_criteria": criteria,
        "open_blocking_questions": [],
        "end_to_end_flow": ["Run suite"],
        "technical_approach": ["Update product module"],
        "milestones": [{"id": "M1", "objective": "Implement event transition", "acceptance_criteria": list(cases)}],
        "initial_task": {
            "objective": "Implement event transition",
            "affected_paths": affected_paths,
            "kind": "implement",
            "milestone_id": "M1",
            "requirements": ["R1"],
            "acceptance_criteria": list(cases),
            "validation_plan": ["Run unit tests"],
        },
    }
    contract = {"task_id": "goal-1", "revision": 1, "body": body, "origin": "finalize_plan"}
    contract["hash"] = util.digest({k: contract[k] for k in ("task_id", "revision", "body")})
    token = f"r1:{contract['hash']}"
    approval = {"kind": "goal_approval", "token": token, "actor": "user_cli", "at": "2026-11-17T00:00:00Z"}
    contract["approval_status"] = "approved"
    contract["approval_event"] = approval
    _, base_snap = _save_snapshot(run_dir, "base", workspace)
    return {
        "version": 3,
        "workspace": str(workspace),
        "run_dir": str(run_dir),
        "task": "Implement event transition",
        "status": "RUNNING",
        "phase": "EXECUTING",
        "iteration": 1,
        "next_stage": "builder",
        "next_action": "Implement event transition",
        "base_commit": base_commit,
        "settings": {
            "spec_first": True,
            "provider": "openai",
            "engine": "codex",
            "roles": {
                "planner": {"model": "gpt-5.4", "reasoning_effort": "high"},
                "task_planner": {"model": "gpt-5.4", "reasoning_effort": "high"},
                "builder": {"model": "claude-sonnet-4-6", "reasoning_effort": "high"},
                "tester": {"model": "glm-5", "reasoning_effort": "high"},
            },
        },
        "goal_contract": contract,
        "acceptance_criteria": [
            {"id": row["id"], "criterion": row["criterion"], "status": "unverified", "evidence": ""} for row in criteria
        ],
        "criteria_revision": util.digest(util.criteria_definition(criteria)),
        "current_task": {
            "id": "task-1",
            "kind": "implement",
            "milestone_id": "M1",
            "objective": "Implement event transition",
            "affected_paths": list(affected_paths),
            "requirements": ["R1"],
            "acceptance_criteria": list(cases),
            "validation_plan": ["Run unit tests"],
            "contract_hash": contract["hash"],
            "contract_revision": 1,
            "source_revision": base_snap["revision"],
        },
        "user_events": [approval],
        "stages": [],
        "history": [],
        "sessions": {},
    }


class PreBuildTruthfulnessAndCannotWriteTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(
            mock.patch.dict(
                os.environ, {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
            )
        )

    def test_skip_and_unreached_skip_feedback_forbid_inventing_unapproved_behavior(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "ws"
            evidence = Path(root) / "evidence"
            evidence.mkdir(parents=True)
            base_commit = _init_git(workspace, {"procinfo.py": "def pid():\n    return 1\n"})

            # 1. Direct skip on host: feedback forbids inventing substitute behavior and offers cannot_write.
            state = _contract_state(
                workspace,
                evidence,
                base_commit=base_commit,
                cases={"AC1": ("Read RSS from /proc/<pid>/stat", "test: test_ac1_reads_proc_rss")},
                affected_paths=["procinfo.py"],
            )
            acceptance.route(state)
            test_file = workspace / "test_procinfo.py"
            test_file.write_text(
                "import unittest\n\nclass ProcTests(unittest.TestCase):\n"
                "    @unittest.skip('Linux /proc only')\n"
                "    def test_ac1_reads_proc_rss(self):\n"
                "        self.fail()\n",
                encoding="utf-8",
            )
            report = {
                "tests": [{"case_id": "AC1", "test_name": "test_ac1_reads_proc_rss", "file": "test_procinfo.py"}],
                "cannot_write": [],
                "already_passing": [],
            }
            record = {"output": str(evidence / "w1.json"), "changed_files": ["test_procinfo.py"]}
            run_tests = lambda files: verify.scratch_run(workspace, evidence / "proof", tests=files)
            acceptance_job.apply(state, dict(report), record, workspace, run_tests=run_tests, evidence_dir=evidence)
            [skip_msg] = acceptance_job.packet(state)["runner_feedback"]
            self.assertIn("Do not assert different, fallback or additional behavior", skip_msg)
            self.assertIn("cannot_write", skip_msg)

            # 2. Unreached skip below base failure: feedback also forbids inventing substitute behavior.
            state2 = _contract_state(
                workspace,
                evidence,
                base_commit=base_commit,
                cases={"AC1": ("Read RSS from /proc/<pid>/stat", "test: test_ac1_reads_proc_rss")},
                affected_paths=["procinfo.py"],
            )
            acceptance.route(state2)
            test_file.write_text(
                "import os\nimport unittest\nimport procinfo\n\nclass ProcTests(unittest.TestCase):\n"
                "    def test_ac1_reads_proc_rss(self):\n"
                "        if not hasattr(procinfo, 'rss_kib'):\n"
                "            self.fail('rss_kib missing')\n"
                "        if not os.path.exists('/no/such/proc'):\n"
                "            self.skipTest('no /proc')\n"
                "        self.assertGreater(procinfo.rss_kib(), 0)\n",
                encoding="utf-8",
            )
            acceptance_job.apply(state2, dict(report), record, workspace, run_tests=run_tests, evidence_dir=evidence)
            [unreached_msg] = acceptance_job.packet(state2)["runner_feedback"]
            self.assertIn("Do not assert different, fallback or additional behavior", unreached_msg)
            self.assertIn("cannot_write", unreached_msg)

    def test_cannot_write_records_view_evidence_routes_to_investigator_and_restores_plan_feedback_pause(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "ws"
            run_dir = Path(root) / "run"
            run_dir.mkdir(parents=True)
            base_commit = _init_git(workspace, {"procinfo.py": "def pid():\n    return 1\n"})
            state = _contract_state(
                workspace,
                run_dir,
                base_commit=base_commit,
                cases={"AC1": ("Read live RSS from /proc/<pid>/stat", "test: test_ac1_reads_proc_rss")},
                affected_paths=["procinfo.py"],
            )
            acceptance.route(state)
            writer_out = run_dir / "writer-01.json"
            writer_report = {
                "tests": [],
                "cannot_write": [
                    {
                        "case_id": "AC1",
                        "reason": "macOS has no /proc/<pid>/stat filesystem and the approved case forbids mocking /proc.",
                    }
                ],
                "already_passing": [],
            }
            writer_out.write_text(json.dumps(writer_report), encoding="utf-8")
            acceptance_job.apply(
                state,
                writer_report,
                {"output": str(writer_out), "changed_files": []},
                workspace,
                run_tests=lambda files: self.fail("No test files should execute when all cases are cannot_write"),
                evidence_dir=run_dir / "evidence",
            )

            self.assertEqual("PAUSED_ACCEPTANCE_TESTS", state["status"])
            self.assertIn("cannot be tested on this host", state["stop_reason"])
            self.assertNotIn("approved plan and the code disagree", state["stop_reason"])
            self.assertTrue(acceptance.cannot_write_pending(state))
            shown_before = acceptance.view(state)
            self.assertIsNotNone(shown_before)
            self.assertEqual("AC1", shown_before["cannot_write"][0]["case_id"])

            # Other PAUSED_ACCEPTANCE_TESTS stops (already_passing / suite binding) must NOT go to Investigator.
            already_state = copy.deepcopy(state)
            already_state["acceptance_tests"].pop("cannot_write", None)
            acceptance_job._pause(already_state, ["AC1 already works: passed on base"])
            self.assertFalse(stuck_job.intercept(already_state, already_state["status"], already_state["stop_reason"]))

            # Genuine cannot_write stop intercepts to Investigator once before asking the person.
            retry_state = copy.deepcopy(state)
            self.assertTrue(stuck_job.intercept(retry_state, retry_state["status"], retry_state["stop_reason"]))
            self.assertEqual(("RUNNING", stuck_job.STAGE), (retry_state["status"], retry_state["next_stage"]))
            prompt, _ = stuck_job.prompt(retry_state, run_dir / "state.json", ["procinfo.py"], 10000, "codex")
            self.assertIn("cannot_write", prompt)

            # Branch A: Investigator recommends retry with a concrete host-independent fixture seam.
            inv_retry_out = run_dir / "inv-retry.json"
            inv_retry_report = {
                "cause": "missing_information",
                "diagnosis": "procinfo.py accepts an injected proc_root path parameter in existing helpers.",
                "recommendation": "retry",
                "guidance": "Pass a temporary proc_root directory to exercise /proc/<pid>/stat parsing without /proc.",
                "user_question": "",
                "example": "Tester assumed /proc was hardcoded instead of passing proc_root.",
                "probe": "",
                "untestable": "procinfo.py does not yet expose rss_kib to probe before Builder runs.",
                "evidence_refs": ["procinfo.py"],
                "counterexample": {"file": "", "find": "", "replace": ""},
            }
            inv_retry_out.write_text(json.dumps(inv_retry_report), encoding="utf-8")
            stuck_job.apply(
                retry_state,
                inv_retry_report,
                {"output": str(inv_retry_out), "changed_files": []},
                workspace,
                run_probe=lambda c, f: {"exit_code": 0},
                run_counterexample=lambda c, e, f: {"exit_code": 1},
            )
            self.assertEqual(("RUNNING", acceptance.STAGE), (retry_state["status"], retry_state["next_stage"]))

            # Branch B: Investigator confirms the criterion is genuinely host-bound ("pause").
            pause_state = copy.deepcopy(state)
            self.assertTrue(stuck_job.intercept(pause_state, pause_state["status"], pause_state["stop_reason"]))
            inv_pause_out = run_dir / "inv-pause.json"
            inv_pause_report = {
                "cause": "environment",
                "diagnosis": "Criterion AC1 explicitly requires reading real Linux /proc/<pid>/stat with no parameter seam, which is absent on macOS.",
                "recommendation": "pause",
                "guidance": "",
                "user_question": "",
                "example": "",
                "probe": "",
                "untestable": "",
                "evidence_refs": ["procinfo.py"],
                "counterexample": {"file": "", "find": "", "replace": ""},
            }
            inv_pause_out.write_text(json.dumps(inv_pause_report), encoding="utf-8")
            stuck_job.apply(
                pause_state,
                inv_pause_report,
                {"output": str(inv_pause_out), "changed_files": []},
                workspace,
                run_probe=lambda c, f: {"exit_code": 0},
                run_counterexample=lambda c, e, f: {"exit_code": 1},
            )
            self.assertEqual("PAUSED_ACCEPTANCE_TESTS", pause_state["status"])
            # Cannot re-intercept a second time for the same stop.
            self.assertFalse(stuck_job.intercept(pause_state, pause_state["status"], pause_state["stop_reason"]))

            public = run_view.view(pause_state, workspace=workspace, run_dir=run_dir)
            self.assertIn(
                "Before building, the Tester found that the approved plan cannot be tested on this host:",
                public["chat"]["notice"],
            )
            self.assertIn("Investigator:", public["chat"]["notice"])
            self.assertIn("absent on macOS", public["chat"]["notice"])
            self.assertEqual(
                "Before building, the Tester found an approved case cannot be tested directly in this host environment.",
                public["recovery"]["what_happened"],
            )
            self.assertEqual("feedback", public["chat"]["input"]["kind"])
            self.assertIn("--feedback", public["chat"]["input"]["next_command"])


class PostBuildProtectedTestRepairTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(
            mock.patch.dict(
                os.environ, {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
            )
        )

    def _seed_spec_first_candidate(
        self,
        workspace: Path,
        run_dir: Path,
        *,
        product_rel: str,
        base_product: str,
        candidate_product: str,
        test_rel: str,
        broken_test: str,
        case_text: str,
        case_method: str,
    ) -> dict:
        base_commit = _init_git(workspace, {".gitignore": ".autocode/\n", product_rel: base_product})
        run_dir.mkdir(parents=True, exist_ok=True)
        state = _contract_state(
            workspace,
            run_dir,
            base_commit=base_commit,
            cases={"C1": (case_text, case_method)},
            affected_paths=[product_rel],
        )
        acceptance.route(state)
        self.assertEqual(acceptance.STAGE, state["next_stage"])

        # Step 1: write_acceptance_tests writes the initial test (which fails on base).
        w1_before_ref, _ = _save_snapshot(run_dir, "w1-before", workspace)
        test_path = workspace / test_rel
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text(broken_test, encoding="utf-8")
        w1_out = run_dir / "iterations" / "001" / "acceptance-tests-01.json"
        w1_out.parent.mkdir(parents=True, exist_ok=True)
        w1_report = {
            "tests": [{"case_id": "C1", "test_name": case_method.split(": ", 1)[1], "file": test_rel}],
            "cannot_write": [],
            "already_passing": [],
        }
        w1_out.write_text(json.dumps(w1_report), encoding="utf-8")
        w1_after_ref, w1_after = _save_snapshot(run_dir, "w1-after", workspace)
        w1_record = {
            "stage": acceptance.STAGE,
            "iteration": 1,
            "output": str(w1_out),
            "changed_files": [test_rel],
            "before_ref": w1_before_ref,
            "after_ref": w1_after_ref,
            "source_revision": w1_after["revision"],
            "contract_hash": state["goal_contract"]["hash"],
            "contract_revision": 1,
            "task_id": state["current_task"]["id"],
            "exit_code": 0,
            "accounted": True,
        }
        autoreview.apply_job(acceptance.STAGE, state, w1_report, w1_record, workspace, run_dir=run_dir)
        state["stages"].append(w1_record)
        self.assertEqual("builder", state["next_stage"])
        self.assertEqual({"C1": "fails"}, acceptance.view(state)["results"])

        # Step 2: Builder implements product code cleanly without touching the protected test.
        b1_before_ref, _ = _save_snapshot(run_dir, "b1-before", workspace)
        (workspace / product_rel).write_text(candidate_product, encoding="utf-8")
        b1_after_ref, b1_after = _save_snapshot(run_dir, "b1-after", workspace)
        b1_out = run_dir / "iterations" / "001" / "builder-01.json"
        b1_events = run_dir / "iterations" / "001" / "builder-01.jsonl"
        b1_out.write_text(json.dumps({"summary": "Implemented product"}), encoding="utf-8")
        b1_events.write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "cmd1", "type": "command_execution", "command": "true", "exit_code": 0},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        b1_record = {
            "stage": "builder",
            "role": "builder",
            "iteration": 1,
            "output": str(b1_out),
            "events": str(b1_events),
            "changed_files": [product_rel],
            "before_ref": b1_before_ref,
            "after_ref": b1_after_ref,
            "source_revision": b1_after["revision"],
            "contract_hash": state["goal_contract"]["hash"],
            "contract_revision": 1,
            "task_id": state["current_task"]["id"],
            "exit_code": 0,
            "accounted": True,
        }
        b1_value = {
            "summary": "Implemented product",
            "changed_files": [product_rel],
            "commands_run": ["true"],
            "evidence_refs": ["event:cmd1"],
            "addressed_requirements": ["R1"],
            "untested_behavior": [],
            "recommended_checks": [],
            "contract_revision": 1,
            "contract_hash": state["goal_contract"]["hash"],
            "task_id": state["current_task"]["id"],
            "deferred_backlog": [],
            "user_request": {
                "kind": "none",
                "discovered": "",
                "impact": "",
                "decision_needed": "",
                "options": [],
                "proposed_delta": "",
            },
        }
        autopilot.apply_build_result(runner, state, b1_value, b1_record, workspace, run_dir)
        state["stages"].append(b1_record)
        self.assertEqual("tester", state["next_stage"])
        return state

    def _tester_fail_report(self, state: dict, repairs: list[dict]) -> dict:
        reason = repairs[0]["reason"] if repairs else "Protected test failed on candidate"
        return {
            "verdict": "FAIL",
            "checks_run": ["python3 -m unittest"],
            "findings": [
                {
                    "id": "",
                    "severity": "high",
                    "finding": reason,
                    "evidence": "event:t1",
                    "blocking": True,
                    "reproduction_steps": ["python3 -m unittest"],
                    "expected": "Test exercises the approved criterion accurately",
                    "actual": reason,
                    "why_it_matters": "Protected test is defective",
                    "suggested_correction": "Route protected acceptance test to write_acceptance_tests",
                }
            ],
            "unverified_criteria": ["C1"],
            "checks": [{"command": "python3 -m unittest", "exit_code": 1, "evidence_ref": "event:t1"}],
            "criterion_results": [{"id": "C1", "status": "FAIL", "evidence_refs": ["event:t1"]}],
            "end_to_end_result": {
                "status": "FAIL",
                "summary": "Protected test failed",
                "evidence_refs": ["event:t1"],
                "technical_result": None,
                "pending_human_criteria": [],
            },
            "finding_dispositions": [],
            "changed_code_exceptions": None,
            "acceptance_test_repairs": repairs,
            "contract_revision": 1,
            "contract_hash": state["goal_contract"]["hash"],
            "task_id": state["current_task"]["id"],
            "deferred_backlog": [],
            "user_request": {
                "kind": "none",
                "discovered": "",
                "impact": "",
                "decision_needed": "",
                "options": [],
                "proposed_delta": "",
            },
        }

    def _record_review_stage(
        self, state: dict, run_dir: Path, stage: str, role: str, value: dict, idx: int = 2
    ) -> dict:
        out = run_dir / "iterations" / f"{idx:03d}" / f"{stage}-01.json"
        events = run_dir / "iterations" / f"{idx:03d}" / f"{stage}-01.jsonl"
        schema_path = run_dir / "iterations" / f"{idx:03d}" / f"{stage}-01.schema.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(value), encoding="utf-8")
        events.write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "t1",
                        "type": "command_execution",
                        "command": "python3 -m unittest",
                        "exit_code": 1,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        base_schema = (
            workflow.checkpoint_schema(runner.SCHEMA_DIR)
            if stage == "review_checkpoint"
            else goals.role_schema(
                support.read(
                    runner.SCHEMA_DIR
                    / "v2"
                    / f"{'astra-decision' if role == 'task_planner' else 'sol-report'}.schema.json"
                ),
                role,
            )
        )
        bound = report_schema.review_generation_schema(base_schema, state, stage)
        util.atomic_json(schema_path, util.model_output_schema(bound))
        snap = util.snapshot(Path(state["workspace"]))
        return {
            "stage": stage,
            "role": role,
            "iteration": idx,
            "output": str(out),
            "events": str(events),
            "schema": str(schema_path),
            "changed_files": [],
            "source_revision": snap["revision"],
            "contract_hash": state["goal_contract"]["hash"],
            "contract_revision": 1,
            "task_id": state["current_task"]["id"],
            "exit_code": 0,
            "accounted": True,
        }

    def test_contradicts_case_from_tester_routes_to_writer_before_builder_and_handles_cannot_write(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "ws"
            run_dir = workspace / ".autocode" / "runs" / "run-1"
            state = self._seed_spec_first_candidate(
                workspace,
                run_dir,
                product_rel="procinfo.py",
                base_product="def pid():\n    return 1\n",
                candidate_product=("def pid():\n    return 1\n\ndef rss_kib():\n    raise OSError('no /proc')\n"),
                test_rel="tests/test_procinfo.py",
                broken_test=(
                    "import unittest\n\nclass ProcTests(unittest.TestCase):\n"
                    "    def test_c1_reads_proc_rss(self):\n"
                    "        import procinfo\n"
                    "        with self.assertRaises(OSError):\n"
                    "            procinfo.rss_kib()\n"
                ),
                case_text="Read live RSS KiB integer from /proc/self/stat on Linux",
                case_method="test: test_c1_reads_proc_rss",
            )

            # Verify Tester execution_request injects AUDIT_NOTE before CURRENT HANDOFF DATA and constrains enum.
            state_path = run_dir / "state.json"
            util.atomic_json(state_path, state)
            req = common.execution_request(state, "tester", state_path, runner.SCHEMA_DIR)
            self.assertIn("PROTECTED ACCEPTANCE TESTS:", req.prompt.split("CURRENT HANDOFF DATA\n", 1)[0])
            self.assertEqual(
                ["C1"],
                req.schema["properties"]["acceptance_test_repairs"]["items"]["properties"]["case_id"]["enum"],
            )
            self.assertIn("never add unapproved fallback or error behavior", acceptance.builder_note(state))

            # Tester catches the substituted OSError test and requests contradicts_case repair.
            repairs = [
                {
                    "case_id": "C1",
                    "kind": "contradicts_case",
                    "reason": "Protected test asserts OSError fallback instead of /proc/self/stat RSS parsing.",
                }
            ]
            t_val = self._tester_fail_report(state, repairs)
            t_rec = self._record_review_stage(state, run_dir, "tester", "tester", t_val)
            autopilot.apply_result(runner, state, "tester", t_val, t_rec, workspace, run_dir)
            self.assertEqual("builder", state["next_stage"])

            # Routing to Builder intercepts into write_acceptance_tests with packet["test_repair"]!
            acceptance.route(state)
            self.assertEqual(acceptance.STAGE, state["next_stage"])
            pkt = acceptance_job.packet(state)
            self.assertEqual(repairs, pkt["test_repair"]["repairs"])

            public_mid = run_view.view(state, workspace=workspace, run_dir=run_dir)
            headlines = [row["headline"] for row in public_mid["recoveries"]]
            self.assertIn("Independent acceptance-test repair (round 1 of 2)", headlines)

            # Writer now truthfully reports cannot_write -> fulfills test_repair -> Investigator -> PAUSED_ACCEPTANCE_TESTS.
            w2_out = run_dir / "iterations" / "003" / "acceptance-tests-01.json"
            w2_out.parent.mkdir(parents=True, exist_ok=True)
            w2_report = {
                "tests": [],
                "cannot_write": [{"case_id": "C1", "reason": "Host has no /proc/self/stat to test live RSS parsing."}],
                "already_passing": [],
            }
            w2_out.write_text(json.dumps(w2_report), encoding="utf-8")
            w2_rec = {
                "stage": acceptance.STAGE,
                "output": str(w2_out),
                "changed_files": [],
                "source_revision": t_rec["source_revision"],
            }
            autoreview.apply_job(
                acceptance.STAGE,
                state,
                w2_report,
                w2_rec,
                workspace,
                run_dir=run_dir,
            )
            state["stages"].append(w2_rec)
            self.assertEqual("PAUSED_ACCEPTANCE_TESTS", state["status"])
            self.assertIsNone(test_repair.pending(state, [{"id": "C1"}]))
            self.assertTrue(stuck_job.intercept(state, state["status"], state["stop_reason"]))

    def test_broken_fixture_repaired_end_to_end_across_tester_checkpoint_completion_and_resolver(self):
        base_product = (
            "def init_state():\n    return {'stages': [{'stage': 'tester', 'events': ['e1']}]}\n\n"
            "def process_event(state):\n    return []\n"
        )
        candidate_product = (
            "def init_state():\n    return {'stages': [{'stage': 'tester', 'events': ['e1']}]}\n\n"
            "def process_event(state):\n"
            "    events = list(state['stages'][-1]['events'])\n"
            "    state['stages'].append({'stage': 'review_completion'})\n"
            "    return events\n"
        )
        broken_test = (
            "import unittest\nimport events\n\nclass EventTests(unittest.TestCase):\n"
            "    def test_c1_processes_events(self):\n"
            "        state = events.init_state()\n"
            "        got = events.process_event(state)\n"
            "        self.assertEqual(['e1'], got)\n"
            "        self.assertEqual(got, state['stages'][-1]['events'])\n"
        )
        repaired_test = (
            "import unittest\nimport events\n\nclass EventTests(unittest.TestCase):\n"
            "    def test_c1_processes_events(self):\n"
            "        state = events.init_state()\n"
            "        got = events.process_event(state)\n"
            "        self.assertEqual(['e1'], got)\n"
            "        self.assertEqual(['e1'], state['stages'][-2]['events'])\n"
        )
        repairs = [
            {
                "case_id": "C1",
                "kind": "broken_fixture",
                "reason": "Test reads state['stages'][-1]['events'] after transition appended a new stage record.",
            }
        ]

        for trigger_stage in ("tester", "review_checkpoint", "review_completion", "resolve"):
            with self.subTest(trigger_stage=trigger_stage), tempfile.TemporaryDirectory() as root:
                workspace = Path(root) / "ws"
                run_dir = workspace / ".autocode" / "runs" / "run-1"
                state = self._seed_spec_first_candidate(
                    workspace,
                    run_dir,
                    product_rel="events.py",
                    base_product=base_product,
                    candidate_product=candidate_product,
                    test_rel="tests/test_events.py",
                    broken_test=broken_test,
                    case_text="process_event returns recorded events and advances to review_completion",
                    case_method="test: test_c1_processes_events",
                )

                if trigger_stage == "tester":
                    t_val = self._tester_fail_report(state, repairs)
                    t_rec = self._record_review_stage(state, run_dir, "tester", "tester", t_val)
                    autopilot.apply_result(runner, state, "tester", t_val, t_rec, workspace, run_dir)
                elif trigger_stage == "review_checkpoint":
                    state["status"] = "PAUSED_REQUESTED"
                    workflow.activate(state, approval_source="test")
                    state["status"] = "RUNNING"
                    state["next_stage"] = "review_checkpoint"
                    t_val = self._tester_fail_report(state, repairs)
                    decision = {
                        "status": "CONTINUE",
                        "acceptance_criteria": state["acceptance_criteria"],
                        "evidence": ["event:t1"],
                        "next_objective": "Repair protected acceptance test",
                        "blocker": "",
                        "plan": ["Repair test"],
                        "affected_paths": ["events.py"],
                        "contract_revision": 1,
                        "contract_hash": state["goal_contract"]["hash"],
                        "task_id": state["current_task"]["id"],
                        "deferred_backlog": [],
                        "agreed_limitations": [],
                        "findings": [],
                        "finding_dispositions": [],
                        "user_request": t_val["user_request"],
                        "next_task": {
                            "kind": "implement",
                            "milestone_id": "M1",
                            "requirements": ["R1"],
                            "acceptance_criteria": ["C1"],
                            "validation_plan": ["python3 -m unittest"],
                            "findings": [],
                        },
                    }
                    cp_val = {
                        "validation": t_val,
                        "decision": decision,
                        "consult_sol": {"requested": False, "question": "", "reason": ""},
                    }
                    cp_rec = self._record_review_stage(state, run_dir, "review_checkpoint", "task_planner", cp_val)
                    autopilot.apply_result(runner, state, "review_checkpoint", cp_val, cp_rec, workspace, run_dir)
                else:
                    # Run ordinary tester FAIL without repairs first so review_completion / resolve judges it.
                    t_val = self._tester_fail_report(state, [])
                    t_val["acceptance_test_repairs"] = None
                    t_rec = self._record_review_stage(state, run_dir, "tester", "tester", t_val)
                    autopilot.apply_result(runner, state, "tester", t_val, t_rec, workspace, run_dir)
                    self.assertEqual("review_completion", state["next_stage"])
                    open_id = state["findings_ledger"][0]["id"]
                    rc_decision = {
                        "status": "CONTINUE" if trigger_stage == "review_completion" else "REWORK",
                        "acceptance_criteria": state["acceptance_criteria"],
                        "evidence": ["event:t1"],
                        "next_objective": "Fix test fixture",
                        "blocker": "",
                        "plan": ["Fix test fixture"],
                        "affected_paths": ["events.py"],
                        "contract_revision": 1,
                        "contract_hash": state["goal_contract"]["hash"],
                        "task_id": state["current_task"]["id"],
                        "deferred_backlog": [],
                        "agreed_limitations": [],
                        "findings": [],
                        "finding_dispositions": [],
                        "acceptance_test_repairs": repairs if trigger_stage == "review_completion" else None,
                        "user_request": t_val["user_request"],
                        "next_task": {
                            "kind": "implement",
                            "milestone_id": "M1",
                            "requirements": ["R1"],
                            "acceptance_criteria": ["C1"],
                            "validation_plan": ["python3 -m unittest"],
                            "findings": [open_id],
                        },
                    }
                    rc_rec = self._record_review_stage(
                        state, run_dir, "review_completion", "task_planner", rc_decision, idx=3
                    )
                    if trigger_stage == "resolve":
                        autopilot.apply_result(
                            runner, state, "review_completion", rc_decision, rc_rec, workspace, run_dir
                        )
                        self.assertEqual("resolve", state["next_stage"])
                        state_path = run_dir / "state.json"
                        util.atomic_json(state_path, state)
                        res_req = autoresolver.prepare(state, "resolve", state_path, runner.SCHEMA_DIR)
                        self.assertIn(
                            "PROTECTED ACCEPTANCE TESTS:", res_req.prompt.split("CURRENT HANDOFF DATA\n", 1)[0]
                        )
                        self.assertEqual(
                            ["C1"],
                            res_req.schema["properties"]["acceptance_test_repairs"]["items"]["properties"]["case_id"][
                                "enum"
                            ],
                        )
                        res_val = {
                            **rc_decision,
                            "status": "REWORK",
                            "diagnosis": "Protected acceptance test reads stages[-1] instead of stages[-2].",
                            "recovery_change": None,
                            "acceptance_test_repairs": repairs,
                        }
                        res_rec = self._record_review_stage(state, run_dir, "resolve", "task_planner", res_val, idx=4)
                        autopilot.apply_result(runner, state, "resolve", res_val, res_rec, workspace, run_dir)
                    else:
                        autopilot.apply_result(
                            runner, state, "review_completion", rc_decision, rc_rec, workspace, run_dir
                        )

                self.assertEqual("builder", state["next_stage"])
                acceptance.route(state)
                self.assertEqual(acceptance.STAGE, state["next_stage"])
                self.assertEqual(repairs, acceptance_job.packet(state)["test_repair"]["repairs"])

                # Step 3: write_acceptance_tests repairs tests/test_events.py; runner proves pass-on-candidate + fail-on-base!
                w2_before_ref, _ = _save_snapshot(run_dir, "w2-before", workspace)
                (workspace / "tests" / "test_events.py").write_text(repaired_test, encoding="utf-8")
                w2_after_ref, w2_after = _save_snapshot(run_dir, "w2-after", workspace)
                w2_out = run_dir / "iterations" / "005" / "acceptance-tests-01.json"
                w2_out.parent.mkdir(parents=True, exist_ok=True)
                w2_report = {
                    "tests": [
                        {"case_id": "C1", "test_name": "test_c1_processes_events", "file": "tests/test_events.py"}
                    ],
                    "cannot_write": [],
                    "already_passing": [],
                }
                w2_out.write_text(json.dumps(w2_report), encoding="utf-8")
                w2_record = {
                    "stage": acceptance.STAGE,
                    "iteration": 5,
                    "output": str(w2_out),
                    "changed_files": ["tests/test_events.py"],
                    "before_ref": w2_before_ref,
                    "after_ref": w2_after_ref,
                    "source_revision": w2_after["revision"],
                    "contract_hash": state["goal_contract"]["hash"],
                    "contract_revision": 1,
                    "task_id": state["current_task"]["id"],
                    "exit_code": 0,
                    "accounted": True,
                }
                autoreview.apply_job(acceptance.STAGE, state, w2_report, w2_record, workspace, run_dir=run_dir)
                state["stages"].append(w2_record)
                self.assertEqual("builder", state["next_stage"])
                self.assertEqual(
                    {"C1": acceptance.CANDIDATE_FAIL_FIRST},
                    acceptance.view(state)["results"],
                )
                self.assertEqual(1, len(state["acceptance_tests"]["receipt_history"]))

                # Step 4: Post-repair Builder admission (including Resolver packet frontier!) + unchanged Builder candidate!
                b2_out = run_dir / "iterations" / "006" / "builder-01.json"
                b2_events = run_dir / "iterations" / "006" / "builder-01.jsonl"
                b2_out.parent.mkdir(parents=True, exist_ok=True)
                b2_out.write_text(json.dumps({"summary": "Product already satisfies repaired test"}), encoding="utf-8")
                b2_events.write_text(
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "b2cmd",
                                "type": "command_execution",
                                "command": "python3 -m unittest",
                                "exit_code": 0,
                            },
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                b2_record = {
                    "stage": "builder",
                    "role": "builder",
                    "iteration": 6,
                    "output": str(b2_out),
                    "events": str(b2_events),
                    "changed_files": [],
                    "before_ref": w2_after_ref,
                    "after_ref": w2_after_ref,
                    "source_revision": w2_after["revision"],
                    "contract_hash": state["goal_contract"]["hash"],
                    "contract_revision": 1,
                    "task_id": state["current_task"]["id"],
                    "exit_code": 0,
                    "accounted": True,
                }
                if trigger_stage == "resolve":
                    self.assertTrue(test_repair.owns_builder_round(state, w2_after["revision"]))
                    # Must not raise PAUSED_STALE_HANDOFF despite source_revision advancing S1 -> S2 in writer!
                    resolver_recovery.admit_dispatch(state, dict(b2_record), workspace, run_dir)

                b2_value = {
                    "summary": "Product already satisfies repaired test",
                    "changed_files": [],
                    "commands_run": ["python3 -m unittest"],
                    "evidence_refs": ["event:b2cmd"],
                    "addressed_requirements": ["R1"],
                    "untested_behavior": [],
                    "recommended_checks": [],
                    "contract_revision": 1,
                    "contract_hash": state["goal_contract"]["hash"],
                    "task_id": state["current_task"]["id"],
                    "deferred_backlog": [],
                    "user_request": {
                        "kind": "none",
                        "discovered": "",
                        "impact": "",
                        "decision_needed": "",
                        "options": [],
                        "proposed_delta": "",
                    },
                }
                autopilot.apply_build_result(runner, state, b2_value, b2_record, workspace, run_dir)
                state["stages"].append(b2_record)
                expected_review = "review_checkpoint" if trigger_stage == "review_checkpoint" else "tester"
                self.assertEqual(expected_review, state["next_stage"])
                self.assertEqual(0, state["no_progress_batches"])

    @unittest.skipUnless(shutil.which("node"), "node is required for JS test runner check")
    def test_broken_fixture_repair_works_on_javascript_node_test_layout(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "ws"
            run_dir = Path(root) / "run"
            run_dir.mkdir(parents=True)
            pkg = json.dumps({"name": "demo-events", "version": "1.0.0", "scripts": {"test": "node --test"}})
            base_commit = _init_git(
                workspace,
                {
                    "package.json": pkg,
                    "src/events.js": "exports.initState = () => ({ stages: [{ stage: 'tester', events: ['e1'] }] });\n",
                },
            )
            state = _contract_state(
                workspace,
                run_dir,
                base_commit=base_commit,
                cases={"C1": ("processEvent returns events", "test: test_c1_processes_events")},
                affected_paths=["src/events.js"],
            )
            acceptance.route(state)

            # Writer 1 writes broken JS test under test/events.test.js.
            w1_before_ref, _ = _save_snapshot(run_dir, "js-w1-before", workspace)
            js_test = workspace / "test" / "events.test.js"
            js_test.parent.mkdir(parents=True, exist_ok=True)
            js_test.write_text(
                "const test = require('node:test');\n"
                "const assert = require('node:assert/strict');\n"
                "test('test_c1_processes_events', () => {\n"
                "  const { initState, processEvent } = require('../src/events');\n"
                "  const s = initState();\n"
                "  const got = processEvent(s);\n"
                "  assert.deepEqual(got, s.stages.at(-1).events);\n"
                "});\n",
                encoding="utf-8",
            )
            w1_after_ref, w1_after = _save_snapshot(run_dir, "js-w1-after", workspace)
            w1_out = run_dir / "w1.json"
            w1_report = {
                "tests": [{"case_id": "C1", "test_name": "test_c1_processes_events", "file": "test/events.test.js"}],
                "cannot_write": [],
                "already_passing": [],
            }
            w1_out.write_text(json.dumps(w1_report), encoding="utf-8")
            w1_rec = {
                "stage": acceptance.STAGE,
                "output": str(w1_out),
                "changed_files": ["test/events.test.js"],
                "before_ref": w1_before_ref,
                "after_ref": w1_after_ref,
                "source_revision": w1_after["revision"],
                "contract_hash": state["goal_contract"]["hash"],
                "contract_revision": 1,
                "task_id": state["current_task"]["id"],
                "exit_code": 0,
                "accounted": True,
            }
            autoreview.apply_job(acceptance.STAGE, state, w1_report, w1_rec, workspace, run_dir=run_dir)
            state["stages"].append(w1_rec)
            self.assertEqual({"C1": "fails"}, acceptance.view(state)["results"])

            # Builder implements processEvent in src/events.js.
            (workspace / "src" / "events.js").write_text(
                "exports.initState = () => ({ stages: [{ stage: 'tester', events: ['e1'] }] });\n"
                "exports.processEvent = (s) => {\n"
                "  const ev = [...s.stages.at(-1).events];\n"
                "  s.stages.push({ stage: 'review_completion' });\n"
                "  return ev;\n"
                "};\n",
                encoding="utf-8",
            )
            _, b1_after = _save_snapshot(run_dir, "js-b1-after", workspace)
            state["validation"] = {"source_revision": b1_after["revision"]}

            # Tester reports broken_fixture.
            t_out = run_dir / "tester.json"
            t_val = self._tester_fail_report(
                state, [{"case_id": "C1", "kind": "broken_fixture", "reason": "s.stages.at(-1).events is undefined"}]
            )
            t_out.write_text(json.dumps(t_val), encoding="utf-8")
            t_rec = {
                "stage": "tester",
                "output": str(t_out),
                "changed_files": [],
                "source_revision": b1_after["revision"],
                "contract_hash": state["goal_contract"]["hash"],
                "contract_revision": 1,
                "task_id": state["current_task"]["id"],
                "exit_code": 0,
                "accounted": True,
            }
            state["stages"].append(t_rec)
            self.assertTrue(
                test_repair.route_from_review(state, t_val, t_rec, covered_case_ids=acceptance.covered(state))
            )
            acceptance.route(state)
            self.assertEqual(acceptance.STAGE, state["next_stage"])

            # Writer 2 fixes test/events.test.js; node --test passes on candidate and fails on base!
            js_test.write_text(
                "const test = require('node:test');\n"
                "const assert = require('node:assert/strict');\n"
                "test('test_c1_processes_events', () => {\n"
                "  const { initState, processEvent } = require('../src/events');\n"
                "  const s = initState();\n"
                "  const got = processEvent(s);\n"
                "  assert.deepEqual(got, ['e1']);\n"
                "  assert.deepEqual(s.stages.at(-2).events, ['e1']);\n"
                "});\n",
                encoding="utf-8",
            )
            _, w2_after = _save_snapshot(run_dir, "js-w2-after", workspace)
            w2_out = run_dir / "w2.json"
            w2_out.write_text(json.dumps(w1_report), encoding="utf-8")
            autoreview.apply_job(
                acceptance.STAGE,
                state,
                w1_report,
                {
                    "stage": acceptance.STAGE,
                    "output": str(w2_out),
                    "changed_files": ["test/events.test.js"],
                    "source_revision": w2_after["revision"],
                },
                workspace,
                run_dir=run_dir,
            )
            self.assertEqual({"C1": acceptance.CANDIDATE_FAIL_FIRST}, acceptance.view(state)["results"])

    def test_repair_limit_and_unchanged_writer_guards_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "ws"
            run_dir = workspace / ".autocode" / "runs" / "run-1"
            state = self._seed_spec_first_candidate(
                workspace,
                run_dir,
                product_rel="calc.py",
                base_product="def add(a, b):\n    return 0\n",
                candidate_product="def add(a, b):\n    return a + b\n",
                test_rel="tests/test_calc.py",
                broken_test=(
                    "import unittest\nclass T(unittest.TestCase):\n"
                    "    def test_c1_adds(self):\n"
                    "        from calc import add\n"
                    "        self.assertEqual(999, add(2, 2))\n"
                ),
                case_text="add sums two ints",
                case_method="test: test_c1_adds",
            )
            repairs = [{"case_id": "C1", "kind": "broken_fixture", "reason": "Wrong expected sum 999 in test"}]

            # Round 1 queued: unchanged writer reports get MAX_REWRITES runner nudges, then raise ExecutionEvidenceRequired!
            t1_val = self._tester_fail_report(state, repairs)
            t1_rec = self._record_review_stage(state, run_dir, "tester", "tester", t1_val, idx=2)
            autopilot.apply_result(runner, state, "tester", t1_val, t1_rec, workspace, run_dir)
            acceptance.route(state)
            noop_report = {
                "tests": [{"case_id": "C1", "test_name": "test_c1_adds", "file": "tests/test_calc.py"}],
                "cannot_write": [],
                "already_passing": [],
            }
            for attempt in range(acceptance_job.MAX_REWRITES):
                acceptance_job.apply(
                    state,
                    noop_report,
                    {"output": str(run_dir / f"noop-{attempt}.json"), "changed_files": []},
                    workspace,
                    run_tests=lambda f: {"results": {"passed": [], "failed": ["test_c1_adds"]}},
                )
                self.assertEqual(acceptance.STAGE, state["next_stage"])
                self.assertIn("unchanged files do not repair them", acceptance_job.packet(state)["runner_feedback"][-1])
            with self.assertRaisesRegex(ExecutionEvidenceRequired, "unchanged files do not repair them"):
                acceptance_job.apply(
                    state,
                    noop_report,
                    {"output": str(run_dir / "noop-final.json"), "changed_files": []},
                    workspace,
                    run_tests=lambda f: {"results": {"passed": [], "failed": ["test_c1_adds"]}},
                )

            # Tampering with the review output invalidates pending repair.
            tampered = copy.deepcopy(state)
            Path(t1_rec["output"]).write_text("{}", encoding="utf-8")
            self.assertIsNone(test_repair.pending(tampered, [{"id": "C1"}]))
            Path(t1_rec["output"]).write_text(json.dumps(t1_val), encoding="utf-8")

            # Spend 2 repair rounds, then verify round 3 pauses PAUSED_REPEATED_FAILURE!
            for round_no in (1, 2):
                if round_no == 2:
                    t2_val = self._tester_fail_report(state, repairs)
                    t2_rec = self._record_review_stage(state, run_dir, "tester", "tester", t2_val, idx=4)
                    autopilot.apply_result(runner, state, "tester", t2_val, t2_rec, workspace, run_dir)
                    acceptance.route(state)
                test_path = workspace / "tests" / "test_calc.py"
                test_path.write_text(
                    f"import unittest\nclass T(unittest.TestCase):\n"
                    f"    def test_c1_adds(self):\n"
                    f"        from calc import add\n"
                    f"        self.assertEqual({100 + round_no}, add(2, 2))\n",
                    encoding="utf-8",
                )
                _, snap = _save_snapshot(run_dir, f"r{round_no}", workspace)
                w_out = run_dir / f"w-r{round_no}.json"
                w_rep = {
                    "tests": [{"case_id": "C1", "test_name": "test_c1_adds", "file": "tests/test_calc.py"}],
                    "cannot_write": [],
                    "already_passing": [],
                }
                w_out.write_text(json.dumps(w_rep), encoding="utf-8")
                acceptance_job.apply(
                    state,
                    w_rep,
                    {
                        "output": str(w_out),
                        "changed_files": ["tests/test_calc.py"],
                        "source_revision": snap["revision"],
                    },
                    workspace,
                    run_tests=lambda f: {"results": {"passed": [], "failed": ["test_c1_adds"], "complete": True}},
                    evidence_dir=run_dir / "evidence",
                )
                state["validation"] = {"source_revision": snap["revision"]}

            t3_val = self._tester_fail_report(state, repairs)
            t3_rec = self._record_review_stage(state, run_dir, "tester", "tester", t3_val, idx=6)
            with self.assertRaisesRegex(util.Paused, "reached 2 round") as caught:
                autopilot.apply_result(runner, state, "tester", t3_val, t3_rec, workspace, run_dir)
            self.assertEqual("PAUSED_REPEATED_FAILURE", caught.exception.status)


class ReportCheckAcceptanceTestRepairTests(unittest.TestCase):
    def test_report_check_validates_acceptance_test_repairs_with_zero_false_rejections(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "ws"
            run_dir = Path(root) / "run"
            workspace.mkdir()
            run_dir.mkdir()
            archive = run_dir / "archived.py"
            archive.write_bytes(b"def test_c1(): assert False\n")

            # Non-spec-first state omits acceptance_test_repairs from wire schemas completely.
            plain_state = {"version": 3, "settings": {}, "goal_contract": {"hash": "h0", "revision": 1}}
            tester_base = goals.role_schema(support.read(runner.SCHEMA_DIR / "v2/sol-report.schema.json"), "tester")
            plain_wire = util.model_output_schema(
                report_schema.review_generation_schema(tester_base, plain_state, "tester")
            )
            self.assertNotIn("acceptance_test_repairs", plain_wire["properties"])

            # Spec-first state with covered C1 includes constrained enum and exposes check in autocode report-check.
            spec_state = {
                "version": 3,
                "settings": {"spec_first": True},
                "goal_contract": {
                    "hash": "h1",
                    "revision": 1,
                    "approval_status": "approved",
                    "body": {
                        "acceptance_criteria": [
                            {
                                "id": "C1",
                                "criterion": "x",
                                "verification_method": "test: test_c1",
                                "human_review": False,
                            }
                        ]
                    },
                },
                "current_task": {"id": "t1"},
                "acceptance_criteria": [{"id": "C1", "criterion": "x", "status": "unverified", "evidence": ""}],
            }
            acceptance.record_receipt(
                spec_state,
                acceptance.due(spec_state),
                {
                    "contract_hash": "h1",
                    "case_ids": ["C1"],
                    "files": {"tests/test_x.py": {"sha256": acceptance.sha256(archive), "archive": str(archive)}},
                },
            )
            spec_wire = util.model_output_schema(
                report_schema.review_generation_schema(tester_base, spec_state, "tester")
            )
            self.assertIn("acceptance_test_repairs", spec_wire["required"])
            spec = report_check.build_spec(stage="tester", workspace=workspace, schema=spec_wire, state=spec_state)
            self.assertEqual(["C1"], spec["covered_acceptance_cases"])
            spec_file = run_dir / "tester-spec.json"
            util.atomic_json(spec_file, spec)

            valid_base = {
                "verdict": "PASS",
                "checks_run": ["pytest"],
                "findings": [],
                "unverified_criteria": [],
                "checks": [{"command": "pytest", "exit_code": 0, "evidence_ref": "event:e1"}],
                "criterion_results": [{"id": "C1", "status": "PASS", "evidence_refs": ["check:1"]}],
                "end_to_end_result": {
                    "status": "PASS",
                    "summary": "All green",
                    "evidence_refs": ["check:1"],
                    "technical_result": None,
                    "pending_human_criteria": [],
                },
                "finding_dispositions": [],
                "changed_code_exceptions": None,
                "contract_revision": 1,
                "contract_hash": "h1",
                "task_id": "t1",
                "deferred_backlog": [],
                "user_request": {
                    "kind": "none",
                    "discovered": "",
                    "impact": "",
                    "decision_needed": "",
                    "options": [],
                    "proposed_delta": "",
                },
            }
            # Omitted, null, and [] all pass with 0% false rejections!
            for variant in (
                valid_base,
                {**valid_base, "acceptance_test_repairs": None},
                {**valid_base, "acceptance_test_repairs": []},
            ):
                self.assertEqual([], report_check.check_report(json.dumps(variant), spec=spec))

            # Verdict PASS with non-empty acceptance_test_repairs is rejected by report-check CLI!
            contradictory_pass = {
                **valid_base,
                "acceptance_test_repairs": [
                    {"case_id": "C1", "kind": "contradicts_case", "reason": "Substituted fallback"}
                ],
            }
            cand_file = run_dir / "bad.json"
            cand_file.write_text(json.dumps(contradictory_pass), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(1, report_check.cli(["--spec", str(spec_file), str(cand_file), "--json"]))
            errs = report_check.check_report(json.dumps(contradictory_pass), spec=spec)
            self.assertTrue(any("cannot report verdict PASS" in e for e in errs), errs)

            # Criterion PASS on repaired case_id is rejected!
            bad_criterion = {
                **contradictory_pass,
                "verdict": "FAIL",
                "end_to_end_result": {**valid_base["end_to_end_result"], "status": "FAIL"},
            }
            errs2 = report_check.check_report(json.dumps(bad_criterion), spec=spec)
            self.assertTrue(any("cannot have status PASS" in e for e in errs2), errs2)

            # Valid FAIL report naming C1 passes report-check cleanly!
            good_fail = {
                **bad_criterion,
                "checks": [{"command": "pytest", "exit_code": 1, "evidence_ref": "event:e1"}],
                "criterion_results": [{"id": "C1", "status": "FAIL", "evidence_refs": ["check:1"]}],
            }
            self.assertEqual([], report_check.check_report(json.dumps(good_fail), spec=spec))


if __name__ == "__main__":
    unittest.main()

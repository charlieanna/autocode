"""Regression tests for changed-code evidence ledger progress and false progress-stop reconciliation."""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_builder_policy as builder_policy
import autocode_goals as goals
import autocode_run_records as run_records
import autocode_support as support
import autopilot
import goal_fixtures


class SharedCauseIChangedCodeEvidenceLedgerProgressTests(unittest.TestCase):
    """evidence_ledger, builder_policy, and changed_code_rework track changed-line progress accurately (#49)."""

    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory(prefix="cc-ledger-progress-"))
        self.root = Path(scratch).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "tablet.py").write_text("def a():\n    return 1\ndef unused():\n    return 2\n")
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@e", "add", "tablet.py"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "init"],
            check=True,
        )
        self.run = self.root / ".autocode" / "runs" / "r1"
        (self.run / "iterations" / "007").mkdir(parents=True)

    def test_evidence_ledger_recognizes_changed_code_progress_repeat_and_deleted_line_progress(self):
        import autocode_evidence_ledger as ledger

        passing_val = {
            "checks": [{"command": "python3 -m unittest", "exit_code": 0}],
            "criterion_results": [{"id": "AC4", "status": "PASS"}],
        }
        cc_first = {
            "obligations": [
                {"path": "bigtable/tablet.py", "line": 19, "status": "reason_required"},
                {"path": "bigtable/tablet.py", "line": 112, "status": "excepted"},
            ]
        }
        seen_1 = ledger.observe(passing_val, [], ["bigtable/tablet.py"], cc_first)
        self.assertEqual(["changed_code:bigtable/tablet.py:19"], seen_1["failing"])
        self.assertIn("changed_code:bigtable/tablet.py:112", seen_1["resolved"])
        row_1 = {"evidence": "v1", "signature": ledger.signature(seen_1), "failing": seen_1["failing"]}

        # Resolving line 19 and failing on disjoint line 112 is progress.
        cc_second = {
            "obligations": [
                {"path": "bigtable/tablet.py", "line": 19, "status": "tested"},
                {"path": "bigtable/tablet.py", "line": 112, "status": "reason_required"},
            ]
        }
        seen_2 = ledger.observe(passing_val, [], ["tests/test_tablet.py"], cc_second)
        self.assertEqual("progress", ledger.assess([row_1], "v2", seen_2))
        row_2 = {"evidence": "v2", "signature": ledger.signature(seen_2), "failing": seen_2["failing"]}

        # Deleting line 112 so it is absent from obligations and failing only on new line 250 is also progress.
        cc_third = {
            "obligations": [
                {"path": "bigtable/tablet.py", "line": 19, "status": "tested"},
                {"path": "bigtable/tablet.py", "line": 250, "status": "reason_required"},
            ]
        }
        seen_3 = ledger.observe(passing_val, [], ["bigtable/tablet.py"], cc_third)
        self.assertEqual("progress", ledger.assess([row_1, row_2], "v3", seen_3))

        # Failing on the exact same line and changed_files is a repeat.
        self.assertEqual("repeat", ledger.assess([row_1, row_2], "v4", seen_2))

        # Breaking unit tests (no changed_code_evidence measured) is 'new', never false 'progress'.
        broken_val = {
            "checks": [{"command": "python3 -m unittest", "exit_code": 1}],
            "criterion_results": [{"id": "AC4", "status": "FAIL"}],
        }
        seen_broken = ledger.observe(broken_val, [], ["bigtable/tablet.py"], None)
        self.assertEqual("new", ledger.assess([row_1], "v5", seen_broken))

    def test_escalated_changed_code_progress_retries_and_exhausted_repeat_skips_wasted_investigator(self):
        import autocode_changed_code_rework as rework

        state = {
            "version": 3,
            "status": "RUNNING",
            "phase": "EXECUTING",
            "next_stage": "builder",
            "iteration": 5,
            "workspace": str(self.root),
            "settings": {
                "engine": "opencode",
                "tool_containment": False,
                "builder_retry": dict(builder_policy.DEFAULTS),
                "roles": {
                    "builder": {"engine": "opencode", "model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"},
                    "tester": {"engine": "opencode", "model": "openai/gpt-6-sol", "reasoning_effort": "high"},
                    "completion": {"engine": "opencode", "model": "openai/gpt-6-sol", "reasoning_effort": "high"},
                },
            },
            "goal_contract": {"hash": "h1", "revision": 1},
            "current_task": {"id": "t-m2", "kind": "implement", "milestone_id": "M2"},
            "validation": {
                "source_revision": "rev-1",
                "checks": [{"command": "python3 -m unittest", "exit_code": 0}],
                "criterion_results": [{"id": "AC4", "status": "PASS"}],
            },
            "findings_ledger": [],
            "changed_files": ["tablet.py"],
            "regression_proof": {
                "verdict": "PASS",
                "source_revision": "rev-1",
                "changed_code_evidence": {
                    "source_revision": "rev-1",
                    "status": "UNVERIFIED",
                    "reason": "Line 1 untested",
                    "obligations": [{"path": "tablet.py", "line": 1, "status": "reason_required"}],
                },
            },
        }
        self.assertEqual("retry", builder_policy.failure(state, "v1.json", "Line 1 untested"))
        state["validation"]["source_revision"] = "rev-2"
        state["regression_proof"]["source_revision"] = "rev-2"
        state["regression_proof"]["changed_code_evidence"] = {
            "source_revision": "rev-2",
            "status": "UNVERIFIED",
            "reason": "Line 2 untested",
            "obligations": [
                {"path": "tablet.py", "line": 1, "status": "reason_required"},
                {"path": "tablet.py", "line": 2, "status": "reason_required"},
            ],
        }
        self.assertEqual("escalate", builder_policy.failure(state, "v2.json", "Lines 1-2 untested"))
        self.assertEqual("openai/gpt-6-sol", state["settings"]["roles"]["builder"]["model"])

        # Escalated Builder resolves lines 1 and 2, failing only on disjoint line 4 -> progress ('retry', NOT stop.end!).
        progress_state = copy.deepcopy(state)
        progress_state["validation"]["source_revision"] = "rev-3"
        progress_state["regression_proof"]["source_revision"] = "rev-3"
        progress_state["regression_proof"]["changed_code_evidence"] = {
            "source_revision": "rev-3",
            "status": "UNVERIFIED",
            "reason": "Line 4 untested",
            "obligations": [
                {"path": "tablet.py", "line": 1, "status": "tested"},
                {"path": "tablet.py", "line": 2, "status": "tested"},
                {"path": "tablet.py", "line": 4, "status": "reason_required"},
            ],
        }
        self.assertEqual("retry", builder_policy.failure(progress_state, "v3.json", "Line 4 untested"))
        self.assertEqual("RUNNING", progress_state["status"])
        self.assertIsNone(runner.stop_policy.applied_stop(progress_state))

        # Conversely, when escalated Builder makes NO progress and Investigator already ran on this task,
        # changed_code_rework.route ends cleanly via builder_policy without queuing a doomed Investigator stage.
        exhausted_state = copy.deepcopy(state)
        exhausted_state["regression_proofs"] = [
            {
                "kind": "changed_code_rework",
                "task_id": "t-m2",
                "contract_hash": "h1",
                "contract_revision": 1,
                "source_revision": "rev-0",
                "review_output": "b1.json",
                "assessment": {},
                "action": "builder",
            },
            {
                "kind": "changed_code_rework",
                "task_id": "t-m2",
                "contract_hash": "h1",
                "contract_revision": 1,
                "source_revision": "rev-0b",
                "review_output": "b2.json",
                "assessment": {},
                "action": "builder",
            },
            {
                "kind": "changed_code_rework",
                "task_id": "t-m2",
                "contract_hash": "h1",
                "contract_revision": 1,
                "source_revision": "rev-2",
                "review_output": "v2.json",
                "assessment": exhausted_state["regression_proof"]["changed_code_evidence"],
                "action": "investigator",
                "investigation": {"diagnosis": "d", "guidance": "g", "evidence_refs": [], "output": "i", "sha256": "s"},
            },
        ]
        rec_repeat = {"stage": "tester", "role": "tester", "source_revision": "rev-2", "output": "v3-repeat.json"}
        self.assertTrue(rework.route(exhausted_state, rec_repeat, investigation_enabled=True))
        self.assertEqual("PAUSED_INTERVENTION", exhausted_state["status"])
        self.assertNotIn("stuck_investigation", exhausted_state)
        outcome = runner.stop_policy.outcome(exhausted_state)
        self.assertEqual("could_not_finish", outcome["result"])
        self.assertIn("changed_code:tablet.py:1", outcome["evidence"]["failing_checks"])

    def test_resume_paused_reconciles_false_changed_code_progress_stop_and_keeps_real_stop_terminal(self):
        snap = runner.source_scope.snapshot(self.root, {"workspace": str(self.root)}, base_snapshot=support.snapshot)
        val_5 = str(self.run / "iterations" / "007" / "val-5.json")
        val_6 = str(self.run / "iterations" / "007" / "val-6.json")
        val_7 = str(self.run / "iterations" / "007" / "val-7.json")
        inv_7 = self.run / "iterations" / "007" / "stuck-inv-7.json"
        inv_7.write_text(
            json.dumps(
                {
                    "diagnosis": "Line 4 unused helper.",
                    "cause": "stage_output",
                    "guidance": "Delete unused() at tablet.py:4.",
                    "recommendation": "retry",
                    "evidence_refs": [val_7],
                    "failure_class": "execution",
                }
            )
        )
        state = {
            "version": 3,
            "workspace": str(self.root),
            "run_dir": str(self.run),
            "task": "Build bigtable",
            "task_id": "t-bt",
            "status": "RUNNING",
            "phase": "EXECUTING",
            "next_stage": "builder",
            "next_action": "Changed product lines still need behavioral evidence: tablet.py:4",
            "iteration": 7,
            "settings": {
                "engine": "opencode",
                "tool_containment": False,
                "builder_retry": dict(builder_policy.DEFAULTS),
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
        snap = runner.source_scope.snapshot(self.root, state)
        state["status"] = "RUNNING"
        state["phase"] = "EXECUTING"
        state["next_stage"] = "builder"
        state["next_action"] = "Changed product lines still need behavioral evidence: tablet.py:4"
        state["iteration"] = 7
        state["validation"] = {
            "source_revision": snap["revision"],
            "checks": [{"command": "python3 -m unittest", "exit_code": 0}],
            "criterion_results": [{"id": "AC4", "status": "PASS"}],
        }
        state["findings_ledger"] = []
        state["changed_files"] = ["tests/test_tablet.py"]
        state["regression_proof"] = {
            "verdict": "PASS",
            "source_revision": snap["revision"],
            "changed_code_evidence": {
                "source_revision": snap["revision"],
                "status": "UNVERIFIED",
                "reason": "Changed product lines still need behavioral evidence: tablet.py:4",
                "obligations": [
                    {"path": "tablet.py", "line": 2, "status": "tested"},
                    {"path": "tablet.py", "line": 4, "status": "reason_required"},
                ],
            },
        }
        state["current_task"] = {
            "id": "task-m2",
            "kind": "implement",
            "milestone_id": "M2",
            "assigned_at": "2026-11-16T13:00:00+00:00",
            "contract_hash": state["goal_contract"]["hash"],
            "contract_revision": state["goal_contract"]["revision"],
        }
        m_key = builder_policy.key(state)
        state["regression_proofs"] = [
            {
                "kind": "changed_code_rework",
                "task_id": "task-m2",
                "contract_hash": state["goal_contract"]["hash"],
                "contract_revision": state["goal_contract"]["revision"],
                "source_revision": "old-rev",
                "review_output": val_6,
                "action": "investigator",
                "investigation": {"diagnosis": "d", "guidance": "g", "evidence_refs": [], "output": "i", "sha256": "s"},
                "assessment": {
                    "source_revision": "old-rev",
                    "status": "UNVERIFIED",
                    "obligations": [{"path": "tablet.py", "line": 2, "status": "reason_required"}],
                },
            },
            {
                "kind": "changed_code_rework",
                "task_id": "task-m2",
                "contract_hash": state["goal_contract"]["hash"],
                "contract_revision": state["goal_contract"]["revision"],
                "source_revision": snap["revision"],
                "review_output": val_7,
                "action": "investigator",
                "assessment": copy.deepcopy(state["regression_proof"]["changed_code_evidence"]),
            },
        ]
        state["builder_retries"] = {
            m_key: {
                "action": "pause",
                "position": 3,
                "failures": [val_5, val_6, val_7],
                "initial_route": {"engine": "opencode", "model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"},
                "ledger": [
                    {
                        "evidence": val_5,
                        "failing": ["criterion:AC4"],
                        "changed": ["tablet.py"],
                        "novelty": "new",
                        "signature": "sig5",
                    },
                    {"evidence": val_6, "failing": [], "changed": ["tablet.py"], "novelty": "new", "signature": "sig6"},
                    {
                        "evidence": val_7,
                        "failing": [],
                        "changed": ["tests/test_tablet.py"],
                        "novelty": "new",
                        "signature": "sig7",
                    },
                ],
            }
        }
        state["builder_retry_decisions"] = [
            {"action": "retry", "failure": val_5, "milestone_key": m_key, "outcome": "paused", "reason": "r5"},
            {"action": "escalate", "failure": val_6, "milestone_key": m_key, "outcome": "paused", "reason": "r6"},
            {"action": "pause", "failure": val_7, "milestone_key": m_key, "novelty": "new", "reason": "r7"},
        ]
        state["stuck_investigations"] = [
            {
                "trigger": "builder_failure",
                "outcome": "classified",
                "failure_class": "execution",
                "milestone_key": m_key,
                "output": str(inv_7),
                "reason": "Line 4 untested",
            }
        ]
        runner.stop_policy.end(
            state,
            builder_policy.EXHAUSTED,
            {
                "milestone_key": m_key,
                "stage": "builder",
                "failure": val_7,
                "attempt": 3,
                "novelty": "new",
                "failing_checks": [],
                "changed_files": ["tests/test_tablet.py"],
            },
            at="2026-11-16T17:05:32+00:00",
        )
        # Genuine non-progress checkpoint copy (line 2 still failing in val_7): must remain terminally stopped!
        real_stopped = copy.deepcopy(state)
        real_stopped["regression_proof"]["changed_code_evidence"]["obligations"][0]["status"] = "reason_required"
        self.assertFalse(autopilot.changed_code_rework.reconcile_false_changed_code_stop(real_stopped, self.root))
        self.assertIsNotNone(runner.stop_policy.applied_stop(real_stopped))

        # False pre-fix progress stop (all earlier items resolved, only disjoint line 4 open) reconciles on resume!
        run_records.write_json(self.run / "state.json", state)
        args_r, parser = runner.cli_args.parse(
            None,
            [
                "--workspace",
                str(self.root),
                "--run-dir",
                str(self.run),
                "--resume-paused",
                "--allow-uncontained-tools",
                "--no-chat",
            ],
            runner.opencode.DEFAULT_MODELS,
        )
        captured_entry: list[dict] = []

        def capture_build_entry(_runner, _args, current, *_rest, **_kw):
            captured_entry.append(copy.deepcopy(current))
            current["status"] = "TASK_COMPLETE"
            return 0

        with (
            patch.object(runner.opencode, "check_models", return_value=None),
            patch.object(runner.build_loop, "run", side_effect=capture_build_entry) as build_mock,
        ):
            code = runner.run_setup.run_locked(
                runner, args_r, parser, state, self.run / "state.json", self.run, self.root
            )
        self.assertEqual(0, code)
        build_mock.assert_called_once()
        reloaded = captured_entry[0]
        self.assertEqual("RUNNING", reloaded["status"])
        self.assertEqual("builder", reloaded["next_stage"])
        self.assertEqual(8, reloaded["iteration"])
        self.assertIsNone(runner.stop_policy.applied_stop(reloaded))
        self.assertEqual("retry", reloaded["builder_retries"][m_key]["action"])
        self.assertEqual(2, reloaded["builder_retries"][m_key]["position"])
        self.assertEqual("progress", reloaded["builder_retries"][m_key]["ledger"][-1]["novelty"])
        self.assertNotIn("outcome", reloaded["builder_retry_decisions"][0])
        self.assertEqual("retry", reloaded["builder_retry_decisions"][-1]["action"])
        self.assertIn("Delete unused() at tablet.py:4.", reloaded["next_action"])
        self.assertIsNotNone(reloaded["regression_proofs"][-1].get("investigation"))


if __name__ == "__main__":
    unittest.main()

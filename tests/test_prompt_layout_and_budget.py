"""Unit tests for stable-first prompt layout, field budgets, conditional policy trimming, and budget telemetry (#3347)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_context
import autocode_efficiency as efficiency
import autocode_field_budget as field_budget
import autocode_prompt_bound as prompt_bound
import autocode_prompt_layout as prompt_layout
import autocode_prompts as prompts
import autocode_stage_context as stage_context
import autocode_support as support
from providers import claude_stream
from units import autoplanner


def _minimal_state(workspace: Path, *, milestones: list[dict] | None = None, legacy: bool = False) -> dict:
    return {
        "version": 3,
        "status": "PLAN_APPROVED",
        "workspace": str(workspace),
        "task": "Fix helper formatting bug",
        "goal_contract": {
            "revision": 1,
            "hash": "contract-hash-1",
            "body": {
                "milestones": milestones or [{"id": "M1", "title": "Main milestone", "acceptance_criteria": ["AC1"]}],
            },
        },
        "current_task": {
            "id": "T1",
            "title": "Fix helper",
            "acceptance_criteria": ["AC1"],
            "affected_paths": ["app.py"],
        },
        "settings": {
            "engine": "codex",
            "model": "gpt-5.4",
            **({"legacy_prompt_layout": True} if legacy else {}),
        },
        "checkpoints": {"enabled": False},
        "answers": {"q1": "yes"},
        "criteria_revision": 1,
    }


class PromptLayoutTests(unittest.TestCase):
    def test_claude_system_handoff_keys_match_contract_pure_keys(self):
        self.assertEqual(prompt_layout.CONTRACT_PURE_KEYS, claude_stream.SYSTEM_HANDOFF_KEYS)

    def test_single_json_orders_contract_pure_then_scope_bound_then_changing_and_sorts_nested(self):
        handoff = {
            "validation": {"z_key": 2, "a_key": [{"b": 2, "a": 1}, {"first": True}]},
            "current_task": {"title": "Task", "affected_paths": ["b.py", "a.py"]},
            "workspace": "/repo",
            "stage": "builder",
            "workflow": {"z": 1, "a": 2},
            "acceptance_criteria": ["AC2", "AC1"],
            "unresolved_findings": [{"id": "F1"}],
        }
        prompt = prompt_layout.build_stage_prompt("STATIC INSTRUCTIONS\n", handoff)
        self.assertEqual(prompt.count("\nCURRENT HANDOFF DATA\n"), 1)
        instr, raw_json = prompt.split("\nCURRENT HANDOFF DATA\n", 1)
        self.assertEqual("STATIC INSTRUCTIONS\n", instr)
        self.assertNotIn("\n", raw_json)

        decoded = json.loads(raw_json)
        self.assertEqual(
            [
                "acceptance_criteria",
                "stage",
                "workspace",
                "current_task",
                "workflow",
                "unresolved_findings",
                "validation",
            ],
            list(decoded.keys()),
        )
        self.assertEqual(["a_key", "z_key"], list(decoded["validation"].keys()))
        self.assertEqual([{"a": 1, "b": 2}, {"first": True}], decoded["validation"]["a_key"])
        # List order is preserved verbatim.
        self.assertEqual(["b.py", "a.py"], decoded["current_task"]["affected_paths"])
        self.assertEqual(["AC2", "AC1"], decoded["acceptance_criteria"])

    def test_prefix_invariance_across_tasks_and_rework_retries(self):
        base_contract = {
            "stage": "builder",
            "workspace": "/repo",
            "task": "Implement widget",
            "goal_contract": {"revision": 2, "summary": "Same contract"},
            "acceptance_criteria": ["AC1", "AC2"],
        }
        task1 = {
            **base_contract,
            "current_task": {"id": "T1", "affected_paths": ["one.py"]},
            "git_head": "aaa111",
            "unresolved_findings": [{"id": "F1"}],
        }
        task2 = {
            **base_contract,
            "current_task": {"id": "T2", "affected_paths": ["two.py"]},
            "git_head": "bbb222",
            "unresolved_findings": [{"id": "F2"}, {"id": "F3"}],
        }
        p1 = prompt_layout.build_stage_prompt("STAGE BODY\n", task1)
        p2 = prompt_layout.build_stage_prompt("STAGE BODY\n", task2)
        b1_prefix = (
            "STAGE BODY\n\nCURRENT HANDOFF DATA\n"
            + json.dumps(
                prompt_layout.order_handoff(task1, contract_only=True)[0],
                separators=(",", ":"),
            )[:-1]
        )
        self.assertTrue(p1.startswith(b1_prefix))
        self.assertTrue(p2.startswith(b1_prefix))

        retry1 = {
            **task1,
            "validation": {"verdict": "FAIL"},
            "unresolved_findings": [{"id": "F1"}],
            "git_head": "111",
        }
        retry2 = {
            **task1,
            "validation": {"verdict": "REWORK", "extra": "more"},
            "unresolved_findings": [{"id": "F1"}, {"id": "F9"}],
            "git_head": "222",
        }
        r1 = prompt_layout.build_stage_prompt("STAGE BODY\n", retry1)
        r2 = prompt_layout.build_stage_prompt("STAGE BODY\n", retry2)
        stable_prefix = (
            "STAGE BODY\n\nCURRENT HANDOFF DATA\n"
            + json.dumps(
                prompt_layout.order_handoff(retry1)[0],
                separators=(",", ":"),
            )[:-1]
        )
        self.assertTrue(r1.startswith(stable_prefix))
        self.assertTrue(r2.startswith(stable_prefix))

    def test_legacy_layout_flag_and_prompts_hash_compatibility(self):
        self.assertFalse(prompt_layout.is_legacy_layout({}))
        self.assertFalse(prompt_layout.is_legacy_layout({"prompts_hash": prompts.HASH}))
        self.assertTrue(prompt_layout.is_legacy_layout({"prompts_hash": "older-hash"}))
        self.assertTrue(prompt_layout.is_legacy_layout({"settings": {"legacy_prompt_layout": True}}))

        raw_handoff = {"validation": {"x": 1}, "stage": "builder"}
        legacy_serialized = prompt_layout.serialize_handoff(raw_handoff, legacy=True)
        self.assertEqual("\nCURRENT HANDOFF DATA\n" + json.dumps(raw_handoff), legacy_serialized)

    def test_measure_prompt_and_refresh_metrics_exact_byte_sum(self):
        single = prompt_layout.build_stage_prompt(
            "INST",
            {
                "stage": "builder",
                "workspace": "/repo",
                "current_task": {"id": "T1"},
                "validation": {"verdict": "FAIL"},
            },
        )
        m_single = prompt_layout.measure_prompt(single)
        self.assertEqual(len(single.encode("utf-8")), m_single["prompt_bytes"])
        self.assertGreater(m_single["stable_handoff_bytes"], 0)
        self.assertGreater(m_single["changing_handoff_bytes"], 0)
        self.assertEqual(
            m_single["prompt_bytes"],
            m_single["instruction_bytes"] + m_single["stable_handoff_bytes"] + m_single["changing_handoff_bytes"],
        )

        stable_only = prompt_layout.build_stage_prompt(
            "INST",
            {"stage": "builder", "current_task": {"id": "T1"}},
        )
        m_stable_only = prompt_layout.measure_prompt(stable_only)
        self.assertEqual(0, m_stable_only["changing_handoff_bytes"])
        self.assertEqual(
            m_stable_only["prompt_bytes"],
            m_stable_only["instruction_bytes"] + m_stable_only["stable_handoff_bytes"],
        )

        split = (
            "SYS\n\nSTABLE HANDOFF DATA\n"
            + json.dumps({"stage": "builder"}, separators=(",", ":"))
            + "\nCURRENT HANDOFF DATA\n"
            + json.dumps({"validation": {"x": 1}}, separators=(",", ":"))
        )
        m_split = prompt_layout.measure_prompt(split)
        self.assertEqual(len(b"SYS\n"), m_split["instruction_bytes"])
        self.assertGreater(m_split["stable_handoff_bytes"], 0)
        self.assertGreater(m_split["changing_handoff_bytes"], 0)
        self.assertEqual(
            m_split["prompt_bytes"],
            m_split["instruction_bytes"] + m_split["stable_handoff_bytes"] + m_split["changing_handoff_bytes"],
        )

        legacy = "INST" + prompt_layout.serialize_handoff({"validation": {"x": 1}, "stage": "builder"}, legacy=True)
        m_legacy = prompt_layout.measure_prompt(legacy)
        self.assertEqual(0, m_legacy["stable_handoff_bytes"])
        self.assertEqual(
            m_legacy["prompt_bytes"],
            m_legacy["instruction_bytes"] + m_legacy["changing_handoff_bytes"],
        )

        plain = "NO HANDOFF MARKER"
        m_plain = prompt_layout.measure_prompt(plain)
        self.assertEqual(
            {
                "prompt_bytes": len(plain.encode("utf-8")),
                "instruction_bytes": len(plain.encode("utf-8")),
                "stable_handoff_bytes": 0,
                "changing_handoff_bytes": 0,
            },
            m_plain,
        )

        refreshed = prompt_layout.refresh_metrics(single, {"soft_budget_tokens": 10000})
        self.assertEqual(10000, refreshed["soft_budget_tokens"])
        self.assertEqual((m_single["prompt_bytes"] + 3) // 4, refreshed["estimated_prompt_tokens"])
        self.assertEqual(3, len(refreshed["top_handoff_fields"]))


class PromptBoundTests(unittest.TestCase):
    def test_top_fields_and_largest_field_cover_stable_and_current_blocks(self):
        split_prompt = (
            "SYS\nSTABLE HANDOFF DATA\n"
            + json.dumps({"requirements": {"nested": "r" * 400}, "stage": "review_completion"})
            + "\nCURRENT HANDOFF DATA\n"
            + json.dumps({"validation": {"note": "v" * 200}, "alpha": "a" * 50, "beta": "b" * 50})
        )
        top = prompt_bound.top_fields(split_prompt, limit=3)
        self.assertEqual(["requirements", "validation", "alpha"], [row["field"] for row in top])
        self.assertEqual([], prompt_bound.top_fields(split_prompt, limit=0))
        self.assertEqual([], prompt_bound.top_fields("plain prompt without json", limit=3))

        largest = prompt_bound.largest_field(split_prompt)
        self.assertEqual("requirements.nested", largest[0])


class FieldBudgetTests(unittest.TestCase):
    def test_under_budget_stays_inline_and_over_budget_summarizes_with_archive(self):
        small_packet = {
            "stage": "review_completion",
            "regression_proof": {"verdict": "PASS", "checks": [{"name": "c1", "passed": True}]},
        }
        moved_small: dict = {}
        self.assertEqual([], field_budget.apply(small_packet, moved_small, stage="review_completion"))
        self.assertEqual({}, moved_small)

        passing_rows = [{"name": f"pass_{i}", "passed": True, "detail": "x" * 250} for i in range(60)]
        failing_row = {"name": "fail_0", "passed": False, "detail": "broken assertion"}
        receipt_ok = {"name": "r_ok", "exit_code": 0, "passed": True, "tail": "t" * 400}
        receipt_bad = {"name": "r_bad", "exit_code": 1, "passed": False, "tail": "assertion failed"}
        oversized_proof = {
            "verdict": "FAIL",
            "summary": "one failed",
            "checks": [*passing_rows, failing_row],
            "changed_code_receipts": [receipt_ok, receipt_bad],
            "mutation_check": {
                "verdict": "PASS",
                "summary": "all killed",
                "killed_count": 3,
                "survivors_count": 0,
                "files": {"a.py": {"blob": "m" * 4000}},
            },
            "files": {"a.py": {"lines": list(range(100))}},
        }

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "run" / "state.json"
            state_path.parent.mkdir(parents=True)
            packet, externalized, budgeted = autocode_context.compact_with_metrics(
                {"stage": "review_completion", "regression_proof": oversized_proof},
                state_path,
                stage="review_completion",
            )
            self.assertEqual(["regression_proof"], budgeted)
            self.assertEqual(["regression_proof"], externalized)
            self.assertIn("context_artifact", packet)
            archive_path = Path(packet["context_artifact"]["path"])
            self.assertTrue(archive_path.is_file())
            self.assertEqual(oversized_proof, json.loads(archive_path.read_text(encoding="utf-8"))["regression_proof"])

            summarized = packet["regression_proof"]
            self.assertEqual("FAIL", summarized["verdict"])
            self.assertEqual([failing_row], summarized["checks"])
            self.assertEqual(len(passing_rows) + 1, summarized["checks_total"])
            self.assertEqual([receipt_bad], summarized["changed_code_receipts"])
            self.assertEqual(2, summarized["changed_code_receipts_total"])
            self.assertEqual(1, summarized["mutation_check"]["files_count"])
            self.assertEqual({"field": "regression_proof"}, summarized["details_artifact"])

        # Tester never budgets top-level regression_proof.
        tester_packet = {"stage": "tester", "regression_proof": oversized_proof}
        tester_moved: dict = {}
        self.assertEqual([], field_budget.apply(tester_packet, tester_moved, stage="tester"))
        self.assertEqual({}, tester_moved)
        self.assertEqual(len(passing_rows) + 1, len(tester_packet["regression_proof"]["checks"]))

    def test_validation_contract_author_rules_and_changed_code_evidence_budgets(self):
        val_rows = [{"id": f"c_{i}", "status": "passed", "detail": "v" * 250} for i in range(60)]
        val_fail = {"id": "c_fail", "status": "failed", "detail": "bad"}
        val_payload = {"verdict": "FAIL", "checks": [*val_rows, val_fail], "passing_checks": ["p1", "p2"]}

        for allowed in ("review_completion", "review_checkpoint", "plan_task"):
            pkt = {"stage": allowed, "validation": json.loads(json.dumps(val_payload))}
            moved: dict = {}
            self.assertEqual(["validation"], field_budget.apply(pkt, moved, stage=allowed))
            self.assertEqual([val_fail], pkt["validation"]["checks"])
            self.assertEqual(len(val_rows) + 1, pkt["validation"]["checks_total"])
            self.assertEqual(len(val_rows), pkt["validation"]["checks_passed_omitted"])
            self.assertEqual(2, pkt["validation"]["passing_checks_count"])
        for skipped in ("tester", "builder"):
            pkt = {"stage": skipped, "validation": json.loads(json.dumps(val_payload))}
            moved = {}
            self.assertEqual([], field_budget.apply(pkt, moved, stage=skipped))
            self.assertEqual({}, moved)

        rules_payload = {
            "short_rule": "Keep this verbatim.",
            "long_rule_a": "First line summary A\n" + ("word " * 1200),
            "long_rule_b": "First line summary B\n" + ("rule " * 1200),
        }
        for allowed in ("review_plan", "finalize_plan"):
            pkt = {"stage": allowed, "contract_author_rules": dict(rules_payload)}
            moved = {}
            self.assertEqual(["contract_author_rules"], field_budget.apply(pkt, moved, stage=allowed))
            self.assertEqual("Keep this verbatim.", pkt["contract_author_rules"]["short_rule"])
            self.assertEqual(
                "First line summary A",
                pkt["contract_author_rules"]["long_rule_a"]["summary"],
            )
            self.assertEqual(
                {"field": "contract_author_rules", "key": "long_rule_a"},
                pkt["contract_author_rules"]["long_rule_a"]["details_artifact"],
            )
        for skipped in ("draft_plan", "revise_plan"):
            pkt = {"stage": skipped, "contract_author_rules": dict(rules_payload)}
            moved = {}
            self.assertEqual([], field_budget.apply(pkt, moved, stage=skipped))

        obligations = [{"line": i, "settled": True, "detail": "ok " * 60} for i in range(50)]
        unsettled = {"line": 99, "settled": False, "detail": "needs test"}
        evidence_payload = {"verdict": "UNSETTLED", "obligations": [*obligations, unsettled], "files": {"x.py": {}}}
        builder_pkt = {"stage": "builder", "changed_code_evidence": evidence_payload}
        moved = {}
        self.assertEqual(["changed_code_evidence"], field_budget.apply(builder_pkt, moved, stage="builder"))
        self.assertEqual([unsettled], builder_pkt["changed_code_evidence"]["obligations"])
        self.assertEqual(51, builder_pkt["changed_code_evidence"]["obligations_total"])
        self.assertEqual(50, builder_pkt["changed_code_evidence"]["settled_obligations_omitted"])
        self.assertEqual(1, builder_pkt["changed_code_evidence"]["files_count"])

        tester_pkt = {"stage": "tester", "changed_code_evidence": json.loads(json.dumps(evidence_payload))}
        self.assertEqual([], field_budget.apply(tester_pkt, {}, stage="tester"))

    def test_never_summarize_and_fail_closed_guards(self):
        oversized_never = {
            "stage": "review_completion",
            "requirements": [{"id": "R1", "text": "x" * 25000}],
            "acceptance_criteria": ["AC" * 10000],
            "goal_contract": {"text": "g" * 25000},
            "unresolved_findings": [{"id": "F1", "text": "f" * 25000}],
            "open_findings": [{"id": "F2", "text": "o" * 25000}],
            "saved_answers": {"q": "a" * 25000},
            "task": "t" * 25000,
            "user_events": [{"text": "u" * 25000}],
            "clarification_episode": {"text": "c" * 25000},
            "verification_obligations": {"required_commands": ["cmd" * 5000]},
        }
        moved: dict = {}
        self.assertEqual([], field_budget.apply(oversized_never, moved, stage="review_completion"))
        self.assertEqual({}, moved)

        malformed = {
            "stage": "review_completion",
            "validation": {"verdict": "FAIL", "checks": ["not-a-dict"] * 2000},
        }
        self.assertEqual([], field_budget.apply(malformed, moved, stage="review_completion"))
        self.assertEqual({}, moved)

        tiny_savings = {
            "stage": "review_completion",
            "validation": {
                "verdict": "FAIL",
                "checks": [
                    {"id": "p", "passed": True, "text": "x" * 400},
                    {"id": "f", "passed": False, "text": "y" * 12500},
                ],
            },
        }
        self.assertEqual([], field_budget.apply(tiny_savings, moved, stage="review_completion"))
        self.assertEqual({}, moved)

    def test_field_budget_skips_review_checkpoint_and_already_projected_fields(self):
        passing_rows = [{"name": f"pass_{i}", "passed": True, "detail": "x" * 250} for i in range(60)]
        checkpoint_pkt = {
            "stage": "review_checkpoint",
            "regression_proof": {"verdict": "PASS", "checks": list(passing_rows)},
        }
        moved: dict = {}
        self.assertEqual([], field_budget.apply(checkpoint_pkt, moved, stage="review_checkpoint"))
        self.assertEqual({}, moved)

        for marker in ("proof_detail_instruction", "receipt_details_instruction", "passing_detail_instruction"):
            projected_pkt = {
                "stage": "review_completion",
                "regression_proof": {
                    "verdict": "PASS",
                    marker: "already projected",
                    "checks": list(passing_rows),
                },
            }
            projected_moved: dict = {}
            self.assertEqual([], field_budget.apply(projected_pkt, projected_moved, stage="review_completion"))
            self.assertEqual({}, projected_moved)

        val_rows = [{"id": f"c_{i}", "status": "passed", "detail": "v" * 250} for i in range(60)]
        projected_val = {
            "stage": "review_completion",
            "validation": {
                "verdict": "PASS",
                "checks": list(val_rows),
                "check_replay": {"passing_detail_instruction": "already projected"},
            },
        }
        val_moved: dict = {}
        self.assertEqual([], field_budget.apply(projected_val, val_moved, stage="review_completion"))
        self.assertEqual({}, val_moved)

    def test_autoplanner_context_skips_unwritable_synthetic_state_dir_but_keeps_existing_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            unwritable = Path(tmp) / "run"
            unwritable.mkdir()
            state_path = unwritable / "state.json"
            state = {
                "version": 3,
                "task_id": "task-1",
                "task": "Fix helper formatting bug",
                "workspace": str(workspace),
                "settings": {"joint_planning": True, "roles": {"plan_reviewer": {}}},
                "answers": {},
                "user_events": [],
                "acceptance_criteria": [],
            }
            real_compact = autocode_context.compact_with_metrics
            real_access = os.access
            with (
                mock.patch.object(
                    autoplanner.os,
                    "access",
                    side_effect=lambda p, mode: False if Path(p) == unwritable else real_access(p, mode),
                ),
                mock.patch.object(autocode_context, "compact_with_metrics", wraps=real_compact) as compact_spy,
            ):
                prompt, metrics = autoplanner.context(state, "review_plan", state_path)
            self.assertIn("Fix helper formatting bug", prompt)
            compact_spy.assert_not_called()
            self.assertEqual([], metrics["externalized_fields"])
            self.assertEqual([], metrics["budgeted_fields"])

            state_path.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(
                    autoplanner.os,
                    "access",
                    side_effect=lambda p, mode: False if Path(p) == unwritable else real_access(p, mode),
                ),
                mock.patch.object(autocode_context, "compact_with_metrics", wraps=real_compact) as compact_spy,
            ):
                autoplanner.context(state, "review_plan", state_path)
            compact_spy.assert_called_once()


class ConditionalTrimmingAndEfficiencyTests(unittest.TestCase):
    def test_conditional_baseline_and_single_milestone_trimming(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "proj"
            workspace.mkdir()
            git = ["git", "-C", str(workspace), "-c", "user.name=T", "-c", "user.email=t@example.test"]
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
            state_path = Path(tmp) / "run" / "state.json"
            state_path.parent.mkdir()

            single_state = _minimal_state(workspace)
            self.assertFalse(stage_context._uses_baseline_comparator(single_state, legacy=False))
            self.assertTrue(stage_context._uses_baseline_comparator(single_state, legacy=True))

            prompt_single, _ = stage_context.context_packet(single_state, "builder", state_path)
            self.assertNotIn("MILESTONE HANDOFF POLICY v1", prompt_single)
            self.assertNotIn(support.BASELINE_POLICY, prompt_single)
            pkt_single = json.loads(prompt_single.split("\nCURRENT HANDOFF DATA\n", 1)[1])
            self.assertNotIn("baseline_compare_command", pkt_single)

            with_baseline = {**single_state, "regression_baseline": {"status": "ready"}}
            self.assertTrue(stage_context._uses_baseline_comparator(with_baseline, legacy=False))
            prompt_base, _ = stage_context.context_packet(with_baseline, "builder", state_path)
            self.assertIn(support.BASELINE_POLICY, prompt_base)
            self.assertIn("baseline_compare_command", json.loads(prompt_base.split("\nCURRENT HANDOFF DATA\n", 1)[1]))

            multi_state = _minimal_state(
                workspace,
                milestones=[
                    {"id": "M1", "acceptance_criteria": ["AC1"]},
                    {"id": "M2", "acceptance_criteria": []},
                ],
            )
            prompt_multi, _ = stage_context.context_packet(multi_state, "builder", state_path)
            self.assertIn("MILESTONE HANDOFF POLICY v1", prompt_multi)

            prompt_legacy, _ = stage_context.context_packet(
                _minimal_state(workspace, legacy=True), "builder", state_path
            )
            self.assertIn("MILESTONE HANDOFF POLICY v1", prompt_legacy)
            self.assertIn(support.BASELINE_POLICY, prompt_legacy)

            (workspace / "package.json").write_text('{"devDependencies":{"vitest":"^2.0.0"}}', encoding="utf-8")
            self.assertTrue(stage_context._uses_baseline_comparator(single_state, legacy=False))

    def test_autoplanner_draft_failure_correction_scoping(self):
        review_rule = autoplanner._draft_failure_correction_rule("review_plan")
        self.assertIn("In review_plan,", review_rule)
        self.assertIn("unapproved_bugfix_compatibility_inference", review_rule)
        self.assertNotIn("In revise_plan,", review_rule)
        self.assertNotIn("In finalize_plan,", review_rule)

        revise_rule = autoplanner._draft_failure_correction_rule("revise_plan")
        self.assertIn("In revise_plan,", revise_rule)
        self.assertNotIn("In review_plan,", revise_rule)
        self.assertNotIn("In finalize_plan,", revise_rule)

        finalize_rule = autoplanner._draft_failure_correction_rule("finalize_plan")
        self.assertIn("In finalize_plan,", finalize_rule)
        self.assertNotIn("In review_plan,", finalize_rule)
        self.assertNotIn("In revise_plan,", finalize_rule)

        legacy_rule = autoplanner._draft_failure_correction_rule("revise_plan", legacy=True)
        for marker in ("In review_plan,", "In revise_plan,", "In finalize_plan,"):
            self.assertIn(marker, legacy_rule)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            draft_state = {
                "status": "DRAFT_PLAN",
                "workspace": str(workspace),
                "task": "Fix bug",
                "settings": {"engine": "codex", "model": "gpt-5.4"},
                "requirements_handoff": {
                    "stage": "requirements_gather",
                    "report": {"requirements": [{"id": "R1", "text": "Fix bug"}]},
                },
                "source_log": [{"id": "task:0", "kind": "task", "text": "Fix bug"}],
                "stages": [],
            }
            draft_prompt, _ = autoplanner.context(draft_state, "draft_plan", state_path=workspace / "state.json")
            self.assertNotIn("unapproved_model_inference", draft_prompt)

    def test_observe_prompt_budget_emits_suppressed_without_timing_gaps(self):
        state: dict = {
            "status": "PLAN_APPROVED",
            "stages": [],
            "settings": {"engine": "codex"},
        }
        under = {
            "prompt_bytes": 1000,
            "instruction_bytes": 700,
            "stable_handoff_bytes": 200,
            "changing_handoff_bytes": 100,
            "estimated_prompt_tokens": 250,
            "soft_budget_tokens": 10000,
            "budgeted_fields": [],
            "top_handoff_fields": [{"field": "task", "bytes": 100}],
        }
        self.assertFalse(efficiency.observe_prompt_budget(state, stage="builder", metrics=under))
        self.assertEqual([], state.get("efficiency_observations", []))

        over = {
            **under,
            "prompt_bytes": 48000,
            "estimated_prompt_tokens": 12000,
            "budgeted_fields": ["regression_proof"],
        }
        self.assertTrue(efficiency.observe_prompt_budget(state, stage="review_completion", metrics=over))
        self.assertTrue(efficiency.observe_prompt_budget(state, stage="review_completion", metrics=over))
        obs = state["efficiency_observations"]
        self.assertEqual(1, len(obs))
        self.assertEqual("suppressed", obs[0]["kind"])
        self.assertEqual(["regression_proof"], obs[0]["provenance"]["budgeted_fields"])

        projected = efficiency.summary(state)
        self.assertEqual(1, projected["repeats"]["suppressed_observed"])
        for cat in projected["by_category"].values():
            self.assertEqual(0, cat["activity_observations"])
            self.assertEqual(0.0, cat["summed_execution_seconds"])


if __name__ == "__main__":
    unittest.main()

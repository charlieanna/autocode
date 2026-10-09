"""Cycle-free role schema extraction preserves ordinary roles and opt-in checkpoints."""

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

import autocode_goals as goals
import autocode_role_schema as reports
import autocode_util as util


class RoleSchemaTests(unittest.TestCase):
    def test_cli_and_package_imports_preserve_compatibility_exports(self):
        root = Path(__file__).resolve().parents[1]
        exports = {
            "autocode_goals": ["revision_guard", "sealed", "USER_REQUEST", "role_schema", "PLANNER_ORIGINS"],
            "autocode_support": [
                "BASELINE_POLICY",
                "assert_no_legacy_process",
                "duplicate_runner_command",
                "hydrate_review_report",
                "review_generation_schema",
                "review_validation_schema",
                "changed_paths",
                "model_output_schema",
                "run_lock",
                "snapshot",
                "workspace_lock",
            ],
            "autocode": [
                "dependency",
                "figma",
                "follow_up",
                "run_view",
                "status_command",
                "workflows",
                "model_catalogue",
                "retained_dispositions",
                "REPAIR_REPORT_BYTES",
                "original_report_for_repair",
                "PLANNING_STAGES",
                "PROVENANCE_LISTS",
                "count_automatic_recovery",
                "timeout_recovery_route",
                "MAX_AUTOMATIC_CAPACITY_RECOVERIES",
                "abandon_stage",
                "archive_stale_report_repair",
                "authorize_failure_retry",
                "automatically_recover_capacity_stage",
                "automatically_recover_external_directory_denial",
                "automatically_recover_timed_out_stage",
                "prepare_abandoned_completion_revalidation",
                "prepare_exhausted_execution_report_retry",
                "prepare_planning_retry",
                "recover_legacy_report_repair",
                "retry_format_failed_report",
            ],
        }
        for prefix, search in [("", root / "tools"), ("tools.", root)]:
            with self.subTest(import_mode=prefix or "CLI"):
                script = (
                    "import importlib,sys; "
                    f"sys.path.insert(0,{str(search)!r}); "
                    f"exports={exports!r}; prefix={prefix!r}; "
                    "missing=[module+'.'+name for module,names in exports.items() "
                    "for name in names if not hasattr(importlib.import_module(prefix+module),name)]; "
                    "assert not missing, missing"
                )
                result = subprocess.run(
                    [sys.executable, "-I", "-B", "-c", script], capture_output=True, text=True, timeout=30
                )
                self.assertEqual(0, result.returncode, result.stderr)

    def legacy(self):
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["status"],
            "properties": {"status": {"type": "string", "enum": ["CONTINUE"]}},
        }

    def test_existing_schema_api_is_preserved_and_input_is_not_mutated(self):
        self.assertIs(goals.role_schema, reports.role_schema)
        self.assertIs(goals.USER_REQUEST, reports.USER_REQUEST)
        schema = self.legacy()
        before = copy.deepcopy(schema)
        result = goals.role_schema(schema, "astra")
        self.assertEqual(before, schema)
        self.assertIn("COMPLETE", result["properties"]["status"]["enum"])
        self.assertNotIn("progressive_checkpoint", result["properties"])
        self.assertNotIn("progressive_checkpoint", result["required"])

    def test_optional_checkpoint_is_explicit_boolean_and_old_reports_remain_valid(self):
        schema = goals.role_schema(self.legacy(), "astra", progressive=True)
        value = {
            "status": "CONTINUE",
            "contract_revision": 1,
            "contract_hash": "hash",
            "task_id": "T1",
            "user_request": {
                "kind": "none",
                "discovered": "",
                "impact": "",
                "decision_needed": "",
                "options": [],
                "proposed_delta": "",
            },
            "deferred_backlog": [],
            "next_task": {
                "kind": "none",
                "milestone_id": "",
                "requirements": [],
                "acceptance_criteria": [],
                "validation_plan": [],
            },
            "agreed_limitations": [],
        }
        util.validate_schema(value, schema)
        util.validate_schema({**value, "progressive_checkpoint": True}, schema)
        with self.assertRaises(ValueError):
            util.validate_schema({**value, "progressive_checkpoint": "true"}, schema)
        self.assertIn("progressive_checkpoint", util.model_output_schema(schema)["required"])

    def test_product_permission_request_enum_is_unchanged(self):
        self.assertEqual(
            ["none", "clarification", "contradiction", "infeasible", "permission", "goal_change", "blocker"],
            reports.USER_REQUEST["properties"]["kind"]["enum"],
        )

    def test_technical_flow_proof_is_optional_for_saved_reports_and_explicit_in_new_reports(self):
        legacy = json.loads(
            (Path(__file__).resolve().parents[1] / "tools/autocode-schemas/v2/sol-report.schema.json").read_text()
        )
        schema = reports.role_schema(legacy, "sol")["properties"]["end_to_end_result"]
        old = {"status": "PASS", "summary": "Executed flow", "evidence_refs": ["event:check"]}
        util.validate_schema(old, schema)
        current = {**old, "technical_result": None, "pending_human_criteria": []}
        strict = util.model_output_schema(schema)
        util.validate_schema(current, strict)
        proof = {"status": "PASS", "summary": "Executed technical steps", "evidence_refs": ["check:1"]}
        util.validate_schema(
            {**current, "status": "NOT_VERIFIED", "technical_result": proof, "pending_human_criteria": ["C1"]}, strict
        )
        with self.assertRaisesRegex(ValueError, "missing"):
            util.validate_schema(old, strict)
        with self.assertRaisesRegex(ValueError, "invalid enum"):
            util.validate_schema({**current, "technical_result": {**proof, "status": "CLAIMED"}}, strict)

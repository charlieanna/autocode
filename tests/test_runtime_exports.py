"""Exercise the real controller interfaces in package and direct-script imports."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"

# These are interfaces used by delegated status, planning, build and recovery
# modules through their runner/support arguments, rather than local imports.
EXPORTS = {
    "autocode": {
        "autocode_dependency": ["dependency"],
        "autocode_run_view": ["run_view"],
        "autocode_run_records": ["count_automatic_recovery"],
        "autocode_report_source": ["original_report_for_repair"],
        "autocode_report_findings": [["retained_dispositions", "retained"]],
        "autocode_stage_recovery": [
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
    },
    "autocode_goals": {
        "autocode_contract_revision": ["revision_guard"],
        "autocode_contract_identity": ["sealed"],
        "autocode_role_schema": ["role_schema"],
    },
    "autocode_support": {
        "autocode_baseline": ["BASELINE_POLICY"],
        "autocode_legacy_process": ["assert_no_legacy_process"],
        "autocode_report_schema": [
            "hydrate_review_report",
            "review_generation_schema",
            "review_validation_schema",
        ],
        "autocode_util": [
            "changed_paths",
            "model_output_schema",
            "run_lock",
            "snapshot",
            "workspace_lock",
        ],
    },
    "dashboard.planner_dispatch": {
        "autocode_planner_routes": ["conversation_planner_routes", "enforce_conversation_routes"],
    },
}

PROBE = """
import importlib, json, sys
sys.path.insert(0, sys.argv[1])
prefix, contracts = sys.argv[2], json.loads(sys.argv[3])
errors = []
for module, origins in contracts.items():
    interface = importlib.import_module(prefix + module)
    for origin, names in origins.items():
        implementation = importlib.import_module(prefix + origin)
        for item in names:
            public, original = item if isinstance(item, list) else (item, item)
            expected = implementation if public in ('dependency', 'run_view') else getattr(implementation, original)
            if getattr(interface, public, None) is not expected:
                errors.append(module + '.' + public)
print(json.dumps(errors))
"""


class RuntimeExportsTests(unittest.TestCase):
    def test_delegated_runtime_interfaces_resolve_in_both_import_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            for path, prefix in ((ROOT, "tools."), (TOOLS, "")):
                with self.subTest(import_mode=prefix or "direct script"):
                    result = subprocess.run(
                        [sys.executable, "-B", "-c", PROBE, str(path), prefix, json.dumps(EXPORTS)],
                        cwd=directory,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual([], json.loads(result.stdout), result.stdout)

    def test_public_cli_starts_from_source_and_package(self):
        for entry in ([str(TOOLS / "autocode.py")], ["-m", "tools.autocode"]):
            with self.subTest(entry=entry):
                result = subprocess.run(
                    [sys.executable, "-B", *entry, "--help"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn("--workspace", result.stdout)
                self.assertIn("--plan-reviewer-model", result.stdout)


if __name__ == "__main__":
    unittest.main()

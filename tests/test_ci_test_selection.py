"""Focused selection covers dynamic resources and public fake consumers."""

import unittest
from pathlib import Path

import ci_test_selection as dependencies
import run_suite


class DynamicDependenciesTests(unittest.TestCase):
    def select(self, path):
        consumers = {
            *dependencies.PROMPT_CONSUMERS,
            *dependencies.SCHEMA_CONSUMERS,
            *dependencies.CORE_CONSUMERS,
            *dependencies.REFUSAL_CONSUMERS,
            "tests.test_architecture",
            "tests.test_milestone_policy",
            "tests.test_launch_state_cli",
            "tests.test_completion_status",
            "tests.test_subprocess",
            "tests.test_report_delivery_cli",
            "tests.test_unrelated",
        }
        return run_suite.select_tests([path], {module: "" for module in consumers})

    def test_milestone_fragment_selects_rendering_identity_schema_and_policy(self):
        selected = self.select("tools/prompts/fragments/milestones/policy.md")
        self.assertEqual(
            {*dependencies.PROMPT_CONSUMERS, "tests.test_architecture", "tests.test_milestone_policy"}, set(selected)
        )
        self.assertNotIn("tests.test_launch_state_cli", selected)

    def test_schema_is_loaded_dynamically_by_report_and_rendering_consumers(self):
        selected = self.select("tools/autocode-schemas/v2/sol-report.schema.json")
        self.assertEqual({*dependencies.SCHEMA_CONSUMERS, "tests.test_architecture"}, set(selected))

    def test_core_cli_dependency_survives_the_slow_list_without_selecting_everything(self):
        selected = self.select("tools/autocode.py")
        self.assertIn("tests.test_launch_state_cli", selected)
        self.assertNotIn("tests.test_unrelated", selected)
        kept, skipped = run_suite.drop_slow(selected, {"tests.test_launch_state_cli": "34s"})
        self.assertIn("tests.test_launch_state_cli", kept)
        self.assertEqual([], skipped)

    def test_orchestration_and_completion_have_actual_public_fake_flow_consumers(self):
        for path in (
            "tools/autocode_build_loop.py",
            "tools/autocode_run_setup.py",
            "tools/autocode_run_actions.py",
            "tools/autocode_completion.py",
            "tools/autocode_milestones.py",
            "tools/units/autoreview.py",
        ):
            with self.subTest(path=path):
                selected = self.select(path)
                self.assertIn("tests.test_completion_status", selected)
                if "completion" not in path and "autoreview" not in path:
                    self.assertIn("tests.test_subprocess", selected)
                kept, _ = run_suite.drop_slow(selected, {module: "30s" for module in selected})
                self.assertIn("tests.test_completion_status", kept)

    def test_package_launch_wiring_selects_the_package_cli_consumer(self):
        # --package is checked at launch in autocode_run_setup.resolve; only the slow CLI module drives it (#711).
        for path in ("tools/autocode_package_scope.py", "tools/autocode_run_setup.py"):
            with self.subTest(path=path):
                self.assertIn("tests.test_package_scope_cli", dependencies.dependencies(path))

    def test_unknown_runtime_and_repository_data_select_actual_fake_flows(self):
        for path in ("tools/new_runtime_rule.json", "new_runtime_rule.json", "tools/new_runtime_rule.py"):
            with self.subTest(path=path):
                selected = self.select(path)
                self.assertTrue(set(dependencies.PUBLIC_FLOW_CONSUMERS) <= selected.keys())
                self.assertNotIn("tests.test_unrelated", selected)
        self.assertEqual({"tests.test_architecture"}, set(self.select("docs/new_runtime_rule.md")))

    def test_provider_process_and_session_changes_cannot_skip_strict_refusal_consumers(self):
        for path in (
            "tools/autocode_session_boundary.py",
            "tools/autocode_process.py",
            "tools/providers/command.py",
            "tools/autocode_task_preflight.py",
        ):
            with self.subTest(path=path):
                selected = self.select(path)
                self.assertTrue(set(dependencies.REFUSAL_CONSUMERS) <= selected.keys())
                self.assertNotIn("tests.test_unrelated", selected)

    def test_removed_prompt_resource_still_selects_its_dynamic_consumers(self):
        selected = self.select("tools/prompts/fragments/milestones/deleted-policy.md")
        self.assertIn("tests.test_milestone_policy", selected)
        self.assertTrue(set(dependencies.PROMPT_CONSUMERS) <= selected.keys())

    def test_native_javascript_resources_select_their_real_consumers_even_when_slow(self):
        for path, expected in (
            ("tools/autocode_capture_bootstrap.cjs", {"tests.test_native_capture_ipc", "tests.test_native_capture"}),
            ("tools/autocode_native_capture_linux.cjs", {"tests.test_native_capture_ipc", "tests.test_native_capture"}),
            ("tools/autocode_kilo_image_delivery.mjs", {"tests.test_kilo_image_delivery", "tests.test_native_image"}),
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {name: "" for name in (*expected, "tests.test_unrelated")})
                self.assertTrue(expected <= selected.keys())
                kept, _ = run_suite.drop_slow(selected, {name: "30s" for name in expected})
                self.assertTrue(expected <= set(kept))
                self.assertNotIn("tests.test_unrelated", kept)

    def test_explicit_registry_never_silently_refers_to_missing_test_modules(self):
        declared = {
            *dependencies.PROMPT_CONSUMERS,
            *dependencies.SCHEMA_CONSUMERS,
            *dependencies.CORE_CONSUMERS,
            *dependencies.REFUSAL_CONSUMERS,
            *dependencies.SUITE_CONSUMERS,
            *(module for _, modules in dependencies.RESOURCE_CONSUMERS for module in modules),
            *(module for _, modules in dependencies.CLI_CONSUMERS for module in modules),
        }
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            [],
            sorted(module for module in declared if not root.joinpath(*module.split(".")).with_suffix(".py").is_file()),
        )

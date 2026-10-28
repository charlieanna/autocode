"""Focused selection covers dynamic resources and public fake consumers."""

import unittest
from pathlib import Path

import ci_test_selection as dependencies
import run_suite


class DynamicDependenciesTests(unittest.TestCase):
    def test_builder_capture_guidance_keeps_its_public_capture_consumer(self):
        consumer = "tests.test_capture_event_contract"
        selected = run_suite.select_tests(
            ["tools/prompts/fragments/stage-context/context-packet-04.md"],
            {consumer: "", "tests.test_unrelated": ""},
        )
        kept, _ = run_suite.drop_slow(selected, {consumer: "slow"})
        self.assertEqual([consumer], list(kept))

    def test_scenario_map_inputs_keep_the_target_discovery_guard(self):
        consumer = "tests.test_run_suite"
        for path in (
            "tools/autocode_report_identity.py",
            "tools/autocode_planning_advice.py",
            "tools/autocode_stuck_job.py",
            "tools/autocode_regression.py",
            "tools/autocode_verify.py",
            "scenarios/test_adversarial_evidence.py",
            "scenarios/test_adversarial_planning.py",
            "scenarios/test_harness.py",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "slow"})
                self.assertEqual([consumer], list(kept))
        self.assertNotIn(
            consumer,
            run_suite.select_tests(
                ["scenarios/test_unrelated.py"],
                {consumer: "", "tests.test_unrelated": ""},
            ),
        )

    def test_scripted_retry_fixtures_keep_their_slow_public_consumers(self):
        mappings = {
            "tools/blackbox_build_provider.py": {
                "tests.test_failure_routing_cli",
                "tests.test_builder_policy",
                "tests.test_build_recovery_blackbox",
                "tests.test_build_blackbox",
            },
            "tools/fake_command_tool.py": {"tests.test_command_flow"},
        }
        for path, consumers in mappings.items():
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {module: "" for module in consumers})
                kept, _ = run_suite.drop_slow(selected, {module: "slow" for module in consumers})
                self.assertEqual(consumers, set(kept))

    def test_native_identity_changes_keep_the_public_repair_consumer(self):
        public = "tests.test_native_event_identity_cli"
        metadata = "tests.test_validation_metadata"
        for path in (
            "tools/autocode_event_matching.py",
            "tools/providers/opencode_events.py",
            "tools/autocode_support.py",
            "tests/test_report_repair.py",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {public: "", metadata: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {public: "slow"})
                self.assertEqual({public} if path.startswith("tests/") else {public, metadata}, set(kept))

    def test_launch_identity_keeps_the_public_http_and_evidence_consumers(self):
        consumers = {"tests.test_direct_connections_cli", "tests.test_route_execution_evidence"}
        for path in (
            "tools/autocode_route_evidence.py",
            "tools/autocode_provider_launch.py",
            "tools/autocode_usage.py",
            "tools/providers/direct.py",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {module: "" for module in consumers})
                kept, _ = run_suite.drop_slow(selected, {module: "slow" for module in consumers})
                self.assertEqual(consumers, set(kept))

    def test_qualification_evidence_keeps_the_public_selection_consumer(self):
        consumers = {"tests.test_model_catalogue", "tests.test_route_evidence"}
        for path in ("tools/autocode_route_evidence.py", "tools/autocode_route_selector.py", "tools/qualify_routes.py"):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {module: "" for module in consumers})
                kept, _ = run_suite.drop_slow(selected, {module: "slow" for module in consumers})
                self.assertEqual(consumers, set(kept))

    def test_claim_reference_changes_keep_the_public_consumer(self):
        consumer = "tests.test_verification_claim_cli"
        for path in (
            "tools/autocode_handoff_dedup.py",
            "tools/autocode_context.py",
            "tools/autocode_stage_context.py",
            "tests/stage_repair_provider.py",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "slow"})
                self.assertEqual([consumer], list(kept))

    def test_validation_evidence_admission_keeps_its_public_repair_regression(self):
        consumer = "tests.test_validation_evidence_cli"
        for path in (
            "tools/autocode_check_refs.py",
            "tools/autocode_report_repair_queue.py",
            "tools/autocode_completion.py",
            "tools/autopilot.py",
            "tools/autocode_resolver.py",
            "tools/prompts/validator.md",
            "tools/prompts/completion-review.md",
            "tests/stage_repair_provider.py",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
                self.assertEqual([consumer], list(kept))

    def test_planning_metadata_keeps_the_public_acceptance_consumer(self):
        consumer = "tests.test_acceptance_tests"
        selected = run_suite.select_tests(["tools/autocode_planning_metadata.py"], {consumer: ""})
        kept, _ = run_suite.drop_slow(selected, {consumer: "slow"})
        self.assertEqual([consumer], list(kept))

    def test_copied_recovery_providers_keep_their_public_consumers(self):
        for path, consumers in (
            ("tools/fake_codex.py", {"tests.test_subprocess"}),
            (
                "tests/failure_routing_provider.py",
                {"tests.test_failure_routing_cli", "tests.test_classification_recovery"},
            ),
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {module: "" for module in consumers})
                kept, _ = run_suite.drop_slow(selected, {module: "30s" for module in consumers})
                self.assertEqual(consumers, set(kept))

    def test_regression_detection_keeps_the_workflow_provenance_consumer(self):
        consumer = "tests.test_bugfix_workflow"
        for path in ("tools/autocode_regression.py", "tools/autocode_verify.py"):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "slow"})
                self.assertEqual([consumer], list(kept))

    def test_task_worktree_cleanup_keeps_the_json_refusal_consumer(self):
        consumer = "tests.test_json_output"
        for path in (
            "tools/autocode_native_invocation.py",
            "tools/autocode_run_setup.py",
            "tools/autocode_workspaces.py",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "slow"})
                self.assertEqual([consumer], list(kept))

    def test_launch_provenance_keeps_the_denied_admission_consumer(self):
        consumer = "tests.test_dashboard_integration"
        for path in (
            "tools/autocode_route_evidence.py",
            "tools/autocode_provider_launch.py",
            "tools/autocode_usage.py",
            "tools/providers/direct.py",
        ):
            selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
            kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
            self.assertEqual([consumer], list(kept))

    def test_review_handoffs_keep_the_public_followup_and_state_contract_consumers(self):
        consumers = {"tests.test_run_state", "tests.test_follow_up_acceptance_cli"}
        for path in (
            "tools/autocode_review_source.py",
            "tools/autocode_stage_context.py",
            "tools/autocode_bug_job.py",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {module: "" for module in consumers})
                kept, _ = run_suite.drop_slow(selected, {module: "30s" for module in consumers})
                self.assertEqual(consumers, set(kept))

    def test_planning_provenance_helpers_keep_the_importless_run_state_scan(self):
        consumer = "tests.test_run_state"
        for path in ("tools/autocode_planning_review_usage.py", "tools/autocode_assumption_provenance.py"):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                self.assertIn(consumer, selected)

    def test_proof_reference_prompt_keeps_planning_repair_and_public_follow_up_consumers(self):
        consumers = {
            "tests.test_planning_proof_reference",
            "tests.test_report_repair_context",
            "tests.test_requirements_job_cli",
        }
        selected = run_suite.select_tests(
            ["tools/prompts/fragments/autoplanner/proof-reference-rule.md"],
            {module: "" for module in consumers},
        )
        kept, _ = run_suite.drop_slow(selected, {module: "30s" for module in consumers})
        self.assertEqual(consumers, set(kept))

    def test_attention_dynamic_cli_keeps_public_flow_and_real_terminal_consumers(self):
        consumers = {"tests.test_attention_flow", "tests.test_chat_attention", "tests.test_chat_attention_screen"}
        for path in (
            "tools/autocode_attention_cli.py",
            "tools/autocode_chat_attention.py",
            "tools/autocode_runs_command.py",
        ):
            selected = run_suite.select_tests([path], {module: "" for module in consumers})
            kept, _ = run_suite.drop_slow(selected, {module: "30s" for module in consumers})
            self.assertEqual(consumers, set(kept))

    def test_lifecycle_metadata_keeps_schema_and_read_only_worktree_consumers(self):
        consumers = {"tests.test_json_contract", "tests.test_worktrees"}
        for path in ("tools/autocode_run_view.py", "tools/autocode_status_command.py", "tools/autocode_worker_view.py"):
            selected = run_suite.select_tests([path], {module: "" for module in consumers})
            kept, _ = run_suite.drop_slow(selected, {module: "30s" for module in consumers})
            self.assertEqual(consumers, set(kept))
        self.assertIn("tests.test_json_contract", dependencies.dependencies("docs/schemas/status-view.schema.json"))

    def test_kilo_profile_keeps_its_native_effort_consumer(self):
        consumer = "tests.test_kilocode_effort"
        selected = run_suite.select_tests(["scenarios/harness/profiles.py"], {consumer: "", "tests.test_unrelated": ""})
        kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
        self.assertEqual([consumer], list(kept))

    def test_clean_review_fixture_keeps_its_oracle_and_public_cli_consumers(self):
        consumers = {"tests.test_review_clean_fixture", "tests.test_review_attribution_cli"}
        for path in (
            "scenarios/catalog/review-clean-pr/oracle.py",
            "scenarios/catalog/review-clean-pr/seed/pr-191.patch",
        ):
            selected = run_suite.select_tests([path], {module: "" for module in consumers})
            kept, _ = run_suite.drop_slow(selected, {module: "30s" for module in consumers})
            self.assertEqual(consumers, set(kept))

    def test_chat_projections_keep_their_public_terminal_and_view_consumers(self):
        consumers = {
            "tests.test_chat_cost",
            "tests.test_chat_verification",
            "tests.test_chat_panel",
            "tests.test_chat_tui",
            "tests.test_chat_client",
            "tests.test_chat_polling_cli",
            "tests.test_chat_inventory_cli",
        }
        for path in (
            "tools/autocode_chat_cost.py",
            "tools/autocode_chat_verification.py",
            "tools/autocode_chat_panel.py",
            "tools/autocode_chat_tui.py",
        ):
            selected = run_suite.select_tests([path], {module: "" for module in consumers})
            kept, _ = run_suite.drop_slow(selected, {module: "30s" for module in consumers})
            self.assertEqual(consumers, set(kept))

    def test_python_entrypoints_keep_the_real_toolchain_consumer_even_when_slow(self):
        consumer = "tests.test_toolchain"
        for path in ("tools/autocode_python_entrypoints.py", "tools/autocode_toolchain.py"):
            selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
            kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
            self.assertEqual([consumer], list(kept))

    def test_shared_test_binding_resources_keep_the_acceptance_consumer_even_when_slow(self):
        consumer = "tests.test_acceptance_tests"
        for path in (
            "tools/autocode_acceptance_tests.py",
            "tools/autocode_acceptance_tests_job.py",
            "tools/autocode_test_cases.py",
            "tools/prompts/acceptance-tests.md",
            "tools/prompts/fragments/test-cases/diagnosis-builder-note.md",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
                self.assertEqual([consumer], list(kept))

    def test_python_collection_keeps_the_public_acceptance_consumer_even_when_slow(self):
        consumer = "tests.test_acceptance_tests"
        for path in ("tools/autocode_python_tests.py", "tools/autocode_verify.py"):
            selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
            kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
            self.assertEqual([consumer], list(kept))

    def test_resolver_fixture_keeps_its_cli_consumer_even_when_slow(self):
        consumer = "tests.test_resolver_recommendation_cli"
        selected = run_suite.select_tests(
            ["tests/resolver_recommendation_provider.py"], {consumer: "", "tests.test_unrelated": ""}
        )
        kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
        self.assertEqual([consumer], list(kept))

    def test_cost_aliases_and_attribution_keep_the_public_cap_consumer(self):
        consumer = "tests.test_cost_cap"
        for path in (
            "tools/autocode_prices.json",
            "tools/autocode_cost.py",
            "tools/autocode_cost_breakdown.py",
            "tools/autocode_usage.py",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
                self.assertEqual([consumer], list(kept))

    def test_checkpoint_projection_keeps_its_public_status_consumers(self):
        consumers = {"tests.test_liveness", "tests.test_usage"}
        selected = run_suite.select_tests(
            ["tools/autocode_code_checkpoints.py"],
            {**{module: "" for module in consumers}, "tests.test_unrelated": ""},
        )
        kept, _ = run_suite.drop_slow(selected, {module: "30s" for module in consumers})
        self.assertEqual(consumers, set(kept))

    def test_planning_prompt_policies_keep_the_dispatch_and_domain_consumers(self):
        for name, domain_consumers in (
            ("obligation", {"tests.test_autoplanner_step3"}),
            ("obligation-planner", {"tests.test_autoplanner_step3"}),
            ("obligation-review", {"tests.test_autoplanner_step3"}),
            ("progressive", {"tests.test_progressive_plan_flow", "tests.test_progressive_rules"}),
            ("progressive-proposal", {"tests.test_progressive_plan_flow", "tests.test_progressive_rules"}),
            ("progressive-review", {"tests.test_progressive_plan_flow", "tests.test_progressive_rules"}),
        ):
            with self.subTest(name=name):
                path = f"tools/prompts/fragments/autoplanner/{name}-policy.md"
                expected = {"tests.test_planning", "tests.test_reviewer_effort_cli", *domain_consumers}
                selected = run_suite.select_tests([path], {module: "" for module in expected})
                kept, _ = run_suite.drop_slow(selected, {module: "30s" for module in expected})
                self.assertEqual(expected, set(kept))

    def test_initial_coverage_policy_keeps_its_prompt_and_executable_proof_regression(self):
        consumer = "tests.test_initial_plan_coverage"
        selected = run_suite.select_tests(
            ["tools/prompts/fragments/autoplanner/example-criteria-rule.md"],
            {consumer: "", "tests.test_unrelated": ""},
        )
        kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
        self.assertEqual([consumer], list(kept))

    def test_investigation_reuse_resources_and_units_keep_the_public_cli_consumer(self):
        consumer = "tests.test_investigation_handoff_cli"
        for path in (
            "tools/autocode_investigation_evidence.py",
            "tools/units/autoplanner.py",
            "tools/units/autoresolver.py",
            "tools/prompts/discovery.md",
            "tools/prompts/fragments/autoplanner/bug-diagnosis-rule.md",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
                self.assertEqual([consumer], list(kept))

    def test_review_attribution_and_adversarial_nit_keep_the_public_consumer(self):
        consumer = "tests.test_review_attribution_cli"
        for path in (
            "tools/autocode_review_job.py",
            "tools/autocode_review_proof.py",
            "tools/autocode_review_attribution.py",
            "tools/prompts/review-change.md",
            "scenarios/catalog/review-clean-pr/broken/blocks-on-a-nit/review/findings.json",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
                self.assertEqual([consumer], list(kept))

    def test_brief_review_ownership_keeps_its_public_cli_consumer_even_when_slow(self):
        consumer = "tests.test_brief_cli"
        for path in (
            "tools/autocode_brief_acceptance.py",
            "tools/autocode_brief_obligations.py",
            "tools/autocode_plan_retention.py",
            "tools/autocode_planning_cycle.py",
            "tools/autocode_follow_up.py",
            "tools/units/autoplanner.py",
            "scenarios/harness/fake_codex.py",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
                self.assertEqual([consumer], list(kept))

    def test_reviewer_effort_producers_keep_the_public_dispatch_consumer(self):
        for path in ("tools/units/autoplanner.py", "tools/autocode_configure.py", "tools/autocode_role_route.py"):
            with self.subTest(path=path):
                consumer = "tests.test_reviewer_effort_cli"
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
                self.assertEqual([consumer], list(kept))

    def test_planning_and_replay_keep_the_saved_run_cli_consumer_even_when_slow(self):
        consumer = "tests.test_saved_run_replay_cli"
        for path in (
            "tools/units/autoplanner.py",
            "tools/autocode_replay_recording.py",
            "tools/autocode_replay_transport.py",
            "tools/autocode_replay_view.py",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
                self.assertEqual([consumer], list(kept))

    def test_kilo_launch_and_native_hook_keep_the_progress_consumer_even_when_slow(self):
        consumer = "tests.test_kilo_progress"
        for path in ("tools/providers/kilocode.py", "tools/providers/opencode_activity.mjs"):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
                self.assertEqual([consumer], list(kept))

    def test_kilo_defaults_keep_the_planning_consumer_even_when_slow(self):
        consumer = "tests.test_planning"
        selected = run_suite.select_tests(
            ["tools/providers/configs/kilocode.toml"], {consumer: "", "tests.test_unrelated": ""}
        )
        kept, _ = run_suite.drop_slow(selected, {consumer: "151s"})
        self.assertEqual([consumer], list(kept))

    def test_report_repair_context_keeps_its_public_cli_consumer_even_when_slow(self):
        consumer = "tests.test_stage_repair_cli"
        for path in ("tools/autocode_report_repair_context.py", "tools/autocode_plan_retention.py"):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
                self.assertEqual([consumer], list(kept))

    def test_typed_planning_allowance_keeps_its_public_consumer_even_when_slow(self):
        consumer = "tests.test_planning_allowance_cli"
        for path in (
            "tools/autocode_command_inputs.py",
            "tools/autocode_recovery_view.py",
            "tools/autocode_recovery_accounting.py",
            "tools/autocode_recovery_grants.py",
            "tools/autocode_run_view.py",
            "tools/autocode_args.py",
            "tools/autocode_control_tokens.py",
            "tools/autocode_pause_command.py",
            "scenarios/harness/conformance.py",
        ):
            selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
            kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
            self.assertEqual([consumer], list(kept))

    def test_checkpoint_reconciliation_keeps_its_public_cli_consumer(self):
        consumer = "tests.test_checkpoint_reconciliation"
        for path in (
            "tools/autocode_checkpoint_reconciliation.py",
            "tools/autocode_action_recovery.py",
            "tools/autocode_native_invocation.py",
            "tools/autocode_run_setup.py",
            "tools/autocode_run_view.py",
            "tools/autocode_args.py",
            "tools/autocode_control_tokens.py",
        ):
            selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
            kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
            self.assertEqual([consumer], list(kept))

    def test_activity_policy_keeps_its_public_wrapper_idle_consumer_even_when_slow(self):
        consumer = "tests.test_kilo_wrapper_idle_cli"
        for path in ("tools/autocode_activity.py", "tools/autocode_process.py"):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
                self.assertEqual([consumer], list(kept))

    def test_checkpoint_normalization_keeps_its_public_pause_regression_even_when_slow(self):
        consumer = "tests.test_paused_approval_cli"
        selected = run_suite.select_tests(["tools/autocode_run_records.py"], {consumer: "", "tests.test_unrelated": ""})
        kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
        self.assertIn(consumer, kept)
        self.assertNotIn("tests.test_unrelated", kept)
        self.assertLessEqual(
            {*dependencies.CORE_CONSUMERS, *dependencies.PUBLIC_FLOW_CONSUMERS},
            dependencies.dependencies("tools/autocode_run_records.py"),
        )

    def test_truncated_repair_timeout_keeps_its_public_cli_consumer_even_when_slow(self):
        consumer = "tests.test_truncated_review_recovery"
        for path in (
            "tools/autocode_stage_recovery.py",
            "tools/autocode_report_source.py",
            "tools/autocode_action_authority.py",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
                self.assertEqual([consumer], list(kept))

    def test_auth_preflight_keeps_its_credential_cli_consumer_even_when_slow(self):
        consumer = "tests.test_provider_credentials"
        for path in ("tools/autocode_model_preflight.py", "tools/autocode_provider_auth.py"):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "30s"})
                self.assertEqual([consumer], list(kept))
        self.assertLessEqual(
            set(dependencies.REFUSAL_CONSUMERS),
            dependencies.dependencies("tools/autocode_model_preflight.py"),
        )

    def test_chat_projection_keeps_its_public_cli_consumer(self):
        for path in ("tools/autocode_chat_view.py", "tools/autocode_run_view.py"):
            self.assertIn("tests.test_chat_cli", dependencies.dependencies(path))

    def test_plan_chat_and_its_prompt_select_the_public_regression(self):
        for path in ("tools/autocode_plan_chat.py", "tools/prompts/plan-chat.md"):
            self.assertIn("tests.test_plan_chat_cli", dependencies.dependencies(path))

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

    def test_parallel_batch_attribution_selects_its_orchestrated_cli_consumer_even_when_slow(self):
        for path in (
            "tools/autocode_dispatch.py",
            "tools/autocode_code_checkpoints.py",
            "tools/autocode_criterion_changes.py",
        ):
            with self.subTest(path=path):
                self.assertIn("tests.test_parallel_criterion_changes", dependencies.dependencies(path))
                selected = run_suite.select_tests(
                    [path], {"tests.test_parallel_criterion_changes": "", "tests.test_unrelated": ""}
                )
                kept, _ = run_suite.drop_slow(selected, {"tests.test_parallel_criterion_changes": "26s"})
                self.assertEqual(["tests.test_parallel_criterion_changes"], list(kept))

    def test_package_launch_wiring_selects_the_package_cli_consumer(self):
        # --package is checked at launch in autocode_run_setup.resolve, a change outside the package stops
        # the run at completion (autocode_package_writes, run by the regression proof before each review
        # dispatch from the build loop, which also takes a busy checkout's launch record), and
        # --accept-completion refuses it in its action step against the run's base (autocode_run_base); only
        # the slow CLI module drives them all (#711).
        for path in (
            "tools/autocode_package_scope.py",
            "tools/autocode_package_writes.py",
            "tools/autocode_run_setup.py",
            "tools/autocode_action_decisions.py",
            "tools/autocode_run_base.py",
            "tools/autocode_build_loop.py",
            "tools/autocode_regression.py",
        ):
            with self.subTest(path=path):
                self.assertIn("tests.test_package_scope_cli", dependencies.dependencies(path))
        self.assertNotIn(
            "tests.test_package_scope_cli", dependencies.dependencies("tools/autocode_regression_selection.py")
        )
        self.assertIn(
            "tests.test_package_writes", dependencies.dependencies("tools/prompts/fragments/package-scope/builder.md")
        )

    def test_the_package_investigator_wiring_selects_its_cli_consumer(self):
        # A --package bug fix's Investigator is told the package (autocode_package_writes, through
        # units.autoresolver, in the package-scope prompt fragment) and its diagnosis is checked by
        # autocode_bug_job against autocode_package_scope's rules; only the CLI module drives them (#711).
        for path in (
            "tools/autocode_bug_job.py",
            "tools/units/autoresolver.py",
            "tools/autocode_package_writes.py",
            "tools/autocode_package_scope.py",
            "tools/prompts/fragments/package-scope/investigator.md",
        ):
            with self.subTest(path=path):
                self.assertIn("tests.test_package_investigator_cli", dependencies.dependencies(path))
        self.assertNotIn(
            "tests.test_package_investigator_cli", dependencies.dependencies("tools/autocode_bug_questions.py")
        )
        # Mapping a module takes away the fake flows an unmapped one selects; these two had them and keep them.
        for path in ("tools/autocode_bug_job.py", "tools/units/autoresolver.py"):
            with self.subTest(path=path):
                self.assertLessEqual(
                    {*dependencies.CORE_CONSUMERS, *dependencies.PUBLIC_FLOW_CONSUMERS}, dependencies.dependencies(path)
                )

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

    def test_component_fixture_selects_both_end_to_end_consumers_even_when_slow(self):
        consumers = {"tests.test_multicomponent", "tests.test_local_compose_scenario"}
        selected = run_suite.select_tests(
            ["tools/fixtures/multicomponent_fake.py"], {module: "" for module in consumers}
        )
        kept, skipped = run_suite.drop_slow(selected, {module: "120s" for module in consumers})
        self.assertTrue(consumers <= kept.keys())
        self.assertEqual([], skipped)

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

    def test_internal_question_wiring_selects_its_public_cli_regression(self):
        consumer = "tests.test_technical_questions_cli"
        for path in (
            "tools/autocode_technical_questions.py",
            "tools/autocode_goal_lifecycle.py",
            "tools/autocode_contract_revision.py",
            "tools/autocode_requirements_handoff.py",
            "tools/autopilot.py",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests([path], {consumer: "", "tests.test_unrelated": ""})
                kept, _ = run_suite.drop_slow(selected, {consumer: "11 seconds"})
                self.assertIn(consumer, kept)
                self.assertNotIn("tests.test_unrelated", kept)

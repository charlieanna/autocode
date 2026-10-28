"""Explicit dependencies that Python imports cannot reveal to focused PR CI.

Resources are loaded by name and public CLI consumers launch another interpreter.
Keep these small consumer groups alongside the ordinary named/import selection;
master still discovers and runs every non-excluded module.
"""

from __future__ import annotations

PROMPT_CONSUMERS = (
    "tests.test_prompts",
    "tests.test_prompt_identity",
    "tests.test_prompt_rendering",
    "tests.test_schema_prompt_formatting",
)
SCHEMA_CONSUMERS = (
    *PROMPT_CONSUMERS,
    "tests.test_model_output_schema",
    "tests.test_role_schema",
    "tests.test_report_schema",
    "tests.test_schema_diagnostics",
)
CORE_CONSUMERS = (
    "tests.test_cli_surface",
    "tests.test_taskrun_seam",
    "tests.test_inprocess_cli",
)
PUBLIC_FLOW_CONSUMERS = ("tests.test_launch_state_cli", "tests.test_completion_status")
REFUSAL_CONSUMERS = (
    "tests.test_session_boundary",
    "tests.test_process_session_admission",
    "tests.test_supervision",
    "tests.test_command_supervision",
    "tests.test_provider_launch_containment",
    "tests.test_supervision_cli",
    "tests.test_preflight_worker_cli",
)
SUITE_CONSUMERS = (
    "tests.test_run_suite",
    "tests.test_run_suite_output",
    "tests.test_ci_test_selection",
    "tests.test_ci_scope",
    "tests.test_suite_definition_policy",
    *CORE_CONSUMERS,
)

# Black-box consumers cannot import their runtime producers. Job scope and the scenario runner share this
# map; its producer and top-level scenario files also select the map-discovery guard.
SCENARIO_RUNTIME_CONSUMERS = {
    "tools/autocode_report_identity.py": ("scenarios.test_adversarial_evidence",),
    "tools/autocode_planning_advice.py": ("scenarios.test_adversarial_planning",),
    "tools/autocode_stuck_job.py": ("scenarios.test_adversarial_planning",),
    "tools/autocode_regression.py": (
        "scenarios.test_harness.StockRefusalsRunTests",
        "scenarios.test_harness.HybridRunTests",
    ),
    "tools/autocode_verify.py": (
        "scenarios.test_harness.StockRefusalsRunTests",
        "scenarios.test_harness.HybridRunTests",
    ),
}
SCENARIO_SELECTION_INPUTS = {
    *SCENARIO_RUNTIME_CONSUMERS,
    *("/".join(target.split(".")[:2]) + ".py" for targets in SCENARIO_RUNTIME_CONSUMERS.values() for target in targets),
}

# Prefixes deliberately include resource fragments, whose callers generally do
# not contain the fragment filename. These policy tests check their own handoffs.
RESOURCE_CONSUMERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    *((path, ("tests.test_run_suite",)) for path in sorted(SCENARIO_SELECTION_INPUTS)),
    # The suite collector is executed by the verifier as a dynamic runner resource; its
    # own imports are invisible to the import selector (#2414).
    ("tools/autocode_component_tests.py", ("tests.test_verify", "tests.test_test_root")),
    ("tests/test_report_repair.py", ("tests.test_native_event_identity_cli",)),
    ("tools/prompts/fragments/stage-context/context-packet-04.md", ("tests.test_capture_event_contract",)),
    ("tools/prompts/discovery.md", ("tests.test_investigation_evidence", "tests.test_investigation_handoff_cli")),
    (
        "tools/prompts/fragments/autoplanner/bug-diagnosis-rule.md",
        ("tests.test_investigation_evidence", "tests.test_investigation_handoff_cli"),
    ),
    ("tools/prompts/review-change.md", ("tests.test_review_attribution_cli",)),
    (
        "tools/prompts/fragments/provider-opencode/review-output.md",
        ("tests.test_review_attribution_cli", "tests.test_schema_prompt_formatting", "tests.test_tool_handoff"),
    ),
    (
        "tools/prompts/validator.md",
        ("tests.test_validation_metadata", "tests.test_report_repair", "tests.test_validation_evidence_cli"),
    ),
    (
        "tools/prompts/completion-review.md",
        ("tests.test_completion_properties", "tests.test_validation_evidence_cli"),
    ),
    ("tests/stage_repair_provider.py", ("tests.test_validation_evidence_cli", "tests.test_verification_claim_cli")),
    (
        "tools/prompts/fragments/provider-opencode/prompt-for-schema.md",
        ("tests.test_schema_prompt_formatting", "tests.test_report_repair"),
    ),
    ("tools/prompts/fragments/autocode/execute-report-repair-06.md", ("tests.test_report_repair",)),
    ("tests/coordination_provider.py", ("tests.test_coordination_tui_e2e",)),
    ("docs/schemas/status-view.schema.json", ("tests.test_json_contract",)),
    ("scenarios/catalog/review-clean-pr/", ("tests.test_review_clean_fixture", "tests.test_review_attribution_cli")),
    ("scenarios/harness/profiles.py", ("tests.test_kilocode_effort",)),
    ("tools/prompts/acceptance-tests.md", ("tests.test_acceptance_tests", "tests.test_acceptance_runner_handoff")),
    (
        "tools/prompts/fragments/acceptance-tests/",
        ("tests.test_acceptance_tests", "tests.test_acceptance_provenance", "tests.test_acceptance_runner_handoff"),
    ),
    ("tools/prompts/fragments/test-cases/diagnosis-builder-note.md", ("tests.test_acceptance_tests",)),
    ("tools/providers/configs/kilocode.toml", ("tests.test_planning",)),
    ("tests/resolver_recommendation_provider.py", ("tests.test_resolver_recommendation_cli",)),
    ("tests/failure_routing_provider.py", ("tests.test_failure_routing_cli", "tests.test_investigator_route")),
    (
        "tools/blackbox_build_provider.py",
        (
            "tests.test_failure_routing_cli",
            "tests.test_builder_policy",
            "tests.test_build_recovery_blackbox",
            "tests.test_build_blackbox",
        ),
    ),
    ("tools/fake_command_tool.py", ("tests.test_command_flow",)),
    ("tools/fake_codex.py", ("tests.test_subprocess",)),
    # The #2133 CLI regression drives the box through the fake provider; its tests import neither module.
    ("tools/autocode_current_behavior.py", ("tests.test_bugfix_workflow",)),
    ("tools/prompts/fragments/autoplanner/bug-diagnosis-rule.md", ("tests.test_bugfix_workflow",)),
    ("tools/prompts/bug-investigation.md", ("tests.test_bug_job",)),
    (
        "tools/autocode_prices.json",
        ("tests.test_prices", "tests.test_cost", "tests.test_cost_cap", "tests.test_route_selector"),
    ),
    (
        "tools/autocode-schemas/evidence-report.schema.json",
        (
            "tests.test_evidence_report",
            "tests.test_review_mutation_evidence",
            "tests.test_review_attribution_cli",
            "tests.test_verify_task",
        ),
    ),
    ("scenarios/harness/conformance.py", ("tests.test_planning_allowance_cli",)),
    ("scenarios/harness/fake_codex.py", ("tests.test_brief_cli", "tests.test_requirements_job_cli")),
    ("tools/fake_codex.py", ("tests.test_subprocess",)),
    (
        "tests/failure_routing_provider.py",
        ("tests.test_failure_routing_cli", "tests.test_classification_recovery"),
    ),
    ("scenarios/harness/clarifying_requirements_provider.py", ("tests.test_requirements_review",)),
    ("scripts/homebrew_formula.py", ("tests.test_homebrew_formula",)),
    ("scenarios/catalog/review-clean-pr/broken/blocks-on-a-nit/", ("tests.test_review_attribution_cli",)),
    ("tools/prompts/fragments/report-repair-context/requirements-corrections.md", ("tests.test_report_repair",)),
    (
        "tools/prompts/fragments/report-repair-context/revision-review.md",
        ("tests.test_report_repair_context", "tests.test_stage_repair_cli"),
    ),
    (
        "tools/prompts/fragments/autoplanner/source-refs-rule.md",
        ("tests.test_report_repair", "tests.test_requirements_chat"),
    ),
    (
        "tools/prompts/fragments/report-repair-context/evidence-probe.md",
        ("tests.test_report_repair", "tests.test_test_cases"),
    ),
    (
        "tools/prompts/fragments/report-repair-context/evidence-counterexample.md",
        ("tests.test_report_repair", "tests.test_discuss_job"),
    ),
    ("tools/providers/requirements_stream.mjs", ("tests.test_requirements_stream", "tests.test_requirements_chat")),
    ("tools/providers/opencode_activity.mjs", ("tests.test_opencode_activity", "tests.test_kilo_progress")),
    (
        "tools/prompts/requirements-gather.md",
        ("tests.test_chat_cli", "tests.test_requirements_review", "tests.test_requirements_job_cli"),
    ),
    (
        "tools/prompts/fragments/provider-opencode/conversation-output.md",
        ("tests.test_schema_prompt_formatting", "tests.test_requirements_chat", "tests.test_plan_chat"),
    ),
    (
        "tools/prompts/fragments/provider-opencode/planning-output.md",
        ("tests.test_schema_prompt_formatting", "tests.test_opencode_fixture_schema", "tests.test_tool_handoff"),
    ),
    (
        "tools/prompts/fragments/autoplanner/repair-citations.md",
        ("tests.test_planning_artifacts", "tests.test_report_repair_context"),
    ),
    ("tools/prompts/fragments/autoplanner/brief-observation-review-rule.md", ("tests.test_brief_acceptance",)),
    (
        "tools/prompts/fragments/autoplanner/out-of-checkout-proxy-rule.md",
        ("tests.test_autoplanner_review",),
    ),
    ("tools/prompts/fragments/autoplanner/requirement-trace-rule.md", ("tests.test_proxied_trace",)),
    ("tools/autocode_capture_bootstrap.cjs", ("tests.test_native_capture_ipc", "tests.test_native_capture")),
    ("tools/autocode_native_capture_linux.cjs", ("tests.test_native_capture_ipc", "tests.test_native_capture")),
    ("tools/prompts/plan-chat.md", ("tests.test_plan_chat_cli",)),
    ("tools/autocode_kilo_image_delivery.mjs", ("tests.test_kilo_image_delivery", "tests.test_native_image")),
    ("tools/fixtures/multicomponent_fake.py", ("tests.test_multicomponent", "tests.test_local_compose_scenario")),
    (
        "tools/fixtures/kilo-context-overflow-run.jsonl",
        ("tests.test_context_window", "tests.test_job_failure_recovery"),
    ),
    ("tools/prompts/fragments/milestones/", ("tests.test_milestone_policy",)),
    ("tools/prompts/fragments/milestone-replan/", ("tests.test_milestone_policy", "tests.test_milestone_replan")),
    (
        "tools/prompts/fragments/autoplanner/proof-reference-rule.md",
        (
            "tests.test_planning_proof_reference",
            "tests.test_report_repair_context",
            "tests.test_requirements_job_cli",
        ),
    ),
    ("tools/prompts/fragments/autoplanner/", ("tests.test_autoplanner_review", "tests.test_planning_stages")),
    (
        "tools/prompts/fragments/autoplanner/feedback-interpretations.md",
        ("tests.test_feedback_assumption_provenance", "tests.test_report_repair_context", "tests.test_brief_cli"),
    ),
    (
        "tools/prompts/fragments/autoplanner/concern-only-contract-audit.md",
        ("tests.test_planning_report_policy", "tests.test_stage_repair_cli"),
    ),
    (
        "tools/prompts/fragments/autoplanner/example-criteria-rule.md",
        ("tests.test_initial_plan_coverage",),
    ),
    (
        "tools/prompts/fragments/autoplanner/concern-rows.md",
        ("tests.test_planning_report_policy", "tests.test_planner_intent"),
    ),
    (
        "tools/prompts/fragments/autoplanner/obligation-",
        ("tests.test_planning", "tests.test_autoplanner_step3", "tests.test_reviewer_effort_cli"),
    ),
    (
        "tools/prompts/fragments/autoplanner/progressive-",
        (
            "tests.test_planning",
            "tests.test_progressive_plan_flow",
            "tests.test_progressive_rules",
            "tests.test_reviewer_effort_cli",
        ),
    ),
    ("tools/prompts/fragments/adaptive-planning/", ("tests.test_adaptive_planning",)),
    ("tools/prompts/fragments/autoreview/", ("tests.test_completion_properties", "tests.test_rework_policy")),
    ("tools/prompts/fragments/workflow/", ("tests.test_workflow", "tests.test_final_workflow")),
    ("tools/prompts/fragments/provider-", ("tests.test_command_source_schema",)),
    ("tools/prompts/fragments/output-policy/", ("tests.test_autocode_output_policy",)),
    ("tools/prompts/fragments/protected-oracles/", ("tests.test_autocode_protected_oracles",)),
    ("tools/prompts/fragments/package-scope/", ("tests.test_package_writes", "tests.test_package_investigator_cli")),
)

# A CLI consumer is selected even if marked slow: otherwise the import-only gap
# that previously broke master would remain. These are fake-provider consumers,
# never a request to qualify against live models.
CLI_CONSUMERS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("tools/autocode_workspace_inventory.py",), ("tests.test_review_attribution_cli",)),
    (
        ("tools/autocode_protected_oracles.py", "tools/autocode_protected_commands.py"),
        ("tests.test_protected_oracles_cli",),
    ),
    (
        ("tools/autocode_regression.py", "tools/autocode_verify.py"),
        ("tests.test_bugfix_workflow",),
    ),
    (
        (
            "tools/autocode_native_invocation.py",
            "tools/autocode_run_setup.py",
            "tools/autocode_workspaces.py",
        ),
        ("tests.test_json_output",),
    ),
    (
        ("tools/autocode_requirement_retention.py", "tools/autocode_goals.py"),
        ("tests.test_requirement_retention_cli",),
    ),
    (
        (
            "tools/autocode_route_evidence.py",
            "tools/autocode_provider_launch.py",
            "tools/autocode_usage.py",
            "tools/providers/direct.py",
        ),
        (
            "tests.test_direct_connections_cli",
            "tests.test_route_execution_evidence",
            "tests.test_dashboard_integration",
        ),
    ),
    (
        ("tools/autocode_route_evidence.py", "tools/autocode_route_selector.py", "tools/qualify_routes.py"),
        ("tests.test_model_catalogue", "tests.test_route_evidence", "tests.test_route_execution_evidence"),
    ),
    (
        ("tools/autocode_handoff_dedup.py", "tools/autocode_context.py", "tools/autocode_stage_context.py"),
        ("tests.test_verification_claim_cli",),
    ),
    (
        ("tools/autocode_event_matching.py", "tools/providers/opencode_events.py", "tools/autocode_support.py"),
        ("tests.test_validation_metadata", "tests.test_native_event_identity_cli"),
    ),
    (
        (
            "tools/autocode_check_refs.py",
            "tools/autocode_report_repair_queue.py",
            "tools/autocode_completion.py",
            "tools/autopilot.py",
            "tools/autocode_resolver.py",
        ),
        ("tests.test_validation_evidence_cli",),
    ),
    (
        ("tools/autocode_investigation_evidence.py", "tools/units/autoplanner.py", "tools/units/autoresolver.py"),
        ("tests.test_investigation_handoff_cli",),
    ),
    (
        ("tools/autocode_review_result.py", "tools/autocode_requirements_chat.py", "tools/autocode_model_preflight.py"),
        ("tests.test_review_attribution_cli",),
    ),
    (
        (
            "tools/autocode_coordinate.py",
            "tools/autocode_coordination_",
            "tools/autocode_chat_coordination.py",
            "tools/autocode_chat_client.py",
            "tools/autocode_run_view.py",
            "tools/autocode_taskrun.py",
            "tools/autocode_related_profile.py",
            "tools/autocode_scope_assignment.py",
            "tools/autocode_action_scope.py",
            "tools/autocode_verify_task.py",
            "tools/autocode_verify_record.py",
            "tools/autocode_issue_delivery.py",
            "tools/autocode_github.py",
            "tools/autocode_scope_question.py",
            "tools/autocode_workspace_scope.py",
        ),
        ("tests.test_coordinate", "tests.test_coordination_tui_e2e", "tests.test_coordination_budget_cli"),
    ),
    (
        ("tools/autocode_run_view.py", "tools/autocode_status_command.py", "tools/autocode_worker_view.py"),
        ("tests.test_json_contract", "tests.test_worktrees"),
    ),
    (
        (
            "tools/autocode_attention",
            "tools/autocode_chat_attention",
            "tools/autocode_runs_command.py",
            "tools/autocode_run_view.py",
            "tools/autocode_chat_view.py",
            "tools/autocode_replay_view.py",
            "tools/autocode_status_command.py",
            "tools/autocode_subcommands.py",
            "tools/autocode_taskrun.py",
        ),
        ("tests.test_attention_flow", "tests.test_chat_attention", "tests.test_chat_attention_screen"),
    ),
    (
        (
            "tools/autocode_worker_",
            "tools/autocode_chat_worker.py",
            "tools/autocode_native_invocation.py",
            "tools/autocode_run_setup.py",
        ),
        (
            "tests.test_worker_flow",
            "tests.test_worker_cli",
            "tests.test_worker_client",
            "tests.test_worker_records",
            "tests.test_worker_service",
        ),
    ),
    (("tools/autocode_transport_recovery.py",), ("tests.test_transport_recovery", "tests.test_pause_status_cli")),
    (("tools/autocode_stop_explanations.py",), ("tests.test_completion_status",)),
    (
        (
            "tools/autocode_chat_cost.py",
            "tools/autocode_chat_verification.py",
            "tools/autocode_chat_panel.py",
            "tools/autocode_chat_tui.py",
        ),
        (
            "tests.test_chat_cost",
            "tests.test_chat_verification",
            "tests.test_chat_panel",
            "tests.test_chat_tui",
            "tests.test_chat_client",
            "tests.test_chat_polling_cli",
        ),
    ),
    (("tools/autocode_python_entrypoints.py", "tools/autocode_toolchain.py"), ("tests.test_toolchain",)),
    (
        (
            "tools/autocode_acceptance_tests.py",
            "tools/autocode_acceptance_tests_job.py",
            "tools/autocode_test_cases.py",
            "tools/autocode_planning_metadata.py",
        ),
        ("tests.test_acceptance_tests",),
    ),
    (("tools/autocode_python_tests.py", "tools/autocode_verify.py"), ("tests.test_acceptance_tests",)),
    (
        (
            "tools/autocode_acceptance_provenance.py",
            "tools/autocode_acceptance_recovery.py",
            "tools/autocode_acceptance_tests_job.py",
            "tools/autocode_follow_up_proof.py",
        ),
        (
            "tests.test_acceptance_provenance",
            "tests.test_acceptance_recovery_cli",
            "tests.test_follow_up_acceptance_cli",
            "tests.test_run_state",
        ),
    ),
    (
        ("tools/autocode_plan_chat.py", "tools/autocode_requirements_handoff.py", "tools/prompts/plan-chat.md"),
        ("tests.test_acceptance_tests",),
    ),
    (
        ("tools/autocode_plan_retention.py", "tools/autocode_planning_cycle.py", "tools/autocode_follow_up.py"),
        ("tests.test_brief_cli",),
    ),
    (
        ("tools/autocode_planning_review_usage.py", "tools/autocode_assumption_provenance.py"),
        ("tests.test_run_state",),
    ),
    (
        (
            "tools/autocode_review_source.py",
            "tools/autocode_stage_context.py",
            "tools/autocode_bug_job.py",
        ),
        ("tests.test_run_state", "tests.test_follow_up_acceptance_cli"),
    ),
    (
        (
            "tools/autocode_brief_acceptance.py",
            "tools/autocode_brief_obligations.py",
            "tools/autocode_assumption_provenance.py",
            "tools/autocode_contract_revision.py",
            "tools/units/autoplanner.py",
        ),
        ("tests.test_brief_cli",),
    ),
    (
        (
            "tools/autocode_planning_report_policy.py",
            "tools/autocode_plan_retention.py",
            "tools/autocode_plan_revision_reference.py",
            "tools/autocode_report_edits.py",
            "tools/autocode_report_text_edits.py",
            "tools/prompts/requirements-revise.md",
            "tools/autocode_report_repair_context.py",
            "tools/autocode_report_source.py",
            "tools/units/autoplanner.py",
        ),
        ("tests.test_stage_repair_cli",),
    ),
    (
        (
            "tools/autocode_job_failure.py",
            "tools/autocode_job_route.py",
            "tools/autocode_quota_route.py",
            "tools/autocode_job_args.py",
        ),
        ("tests.test_investigator_route", "tests.test_acceptance_writer_route"),
    ),
    (
        (
            "tools/autocode_args.py",
            "tools/autocode_job_args.py",
            "tools/autocode_job_failure.py",
            "tools/autocode_action_recovery.py",
            "tools/autocode_run_setup.py",
        ),
        ("tests.test_job_cap_change",),
    ),
    (
        (
            "tools/autocode_configure.py",
            "tools/autocode_resolver_retry_route.py",
            "tools/autocode_resolver_recovery.py",
            "tools/autocode_action_recovery.py",
            "tools/autocode_run_setup.py",
        ),
        ("tests.test_resolver_retry_route_cli",),
    ),
    (("tools/autocode_cost", "tools/autocode_usage.py"), ("tests.test_cost_cap",)),
    (("tools/autocode_handoff_dedup.py",), ("tests.test_handoff_dedup",)),
    (
        (
            "tools/autocode_recovery_accounting.py",
            "tools/autocode_recovery_streak.py",
            "tools/autocode_recovery_progress.py",
            "tools/autocode_run_view.py",
        ),
        ("tests.test_routine_recovery_cli", "tests.test_recovery_advice_conformance"),
    ),
    (
        ("tools/units/autoplanner.py", "tools/autocode_configure.py", "tools/autocode_role_route.py"),
        ("tests.test_reviewer_effort_cli",),
    ),
    (
        ("tools/units/autoplanner.py", "tools/autocode_replay_"),
        ("tests.test_saved_run_replay_cli",),
    ),
    (
        ("tools/providers/kilocode.py", "tools/providers/kilocode_policy.py"),
        (
            "tests.test_kilo_progress",
            "tests.test_kilocode_policy",
            "tests.test_kilocode_effort",
            "tests.test_output_cap",
        ),
    ),
    (
        ("tools/autocode_permission_progress.py", "tools/autocode_resolver_human.py"),
        ("tests.test_permission_loop_cli",),
    ),
    (
        (
            "tools/autocode_command_inputs.py",
            "tools/autocode_planning_review_usage.py",
            "tools/autocode_recovery_view.py",
            "tools/autocode_recovery_accounting.py",
            "tools/autocode_recovery_grants.py",
            "tools/autocode_run_view.py",
            "tools/autocode_args.py",
            "tools/autocode_control_tokens.py",
            "tools/autocode_pause_command.py",
        ),
        ("tests.test_planning_allowance_cli",),
    ),
    (
        (
            "tools/autocode_checkpoint_reconciliation.py",
            "tools/autocode_controller_owner.py",
            "tools/autocode_action_recovery.py",
            "tools/autocode_native_invocation.py",
            "tools/autocode_run_setup.py",
            "tools/autocode_run_view.py",
            "tools/autocode_args.py",
            "tools/autocode_control_tokens.py",
        ),
        ("tests.test_checkpoint_reconciliation",),
    ),
    (
        ("tools/autocode_activity.py", "tools/autocode_process.py"),
        ("tests.test_kilo_wrapper_idle_cli",),
    ),
    (
        (
            "tools/autocode_command_inputs.py",
            "tools/autocode_pause_command.py",
            "tools/autocode_recovery_view.py",
        ),
        ("tests.test_operational_information",),
    ),
    (
        ("tools/autocode_stage_recovery.py", "tools/autocode_report_source.py", "tools/autocode_action_authority.py"),
        ("tests.test_truncated_review_recovery",),
    ),
    (("tools/autocode_plan_chat.py",), ("tests.test_plan_chat_cli",)),
    (
        (
            "tools/autocode_technical_questions.py",
            "tools/autocode_goal_lifecycle.py",
            "tools/autocode_contract_revision.py",
            "tools/autocode_requirements_handoff.py",
            "tools/autopilot.py",
        ),
        ("tests.test_technical_questions_cli", "tests.test_revision_guard_cli"),
    ),
    (
        ("tools/autocode_direct_", "tools/providers/direct.py", "tools/autocode_connections.py"),
        ("tests.test_direct_connections", "tests.test_direct_connections_cli"),
    ),
    (
        ("tools/autocode_connections.py", "tools/autocode_subcommands.py", "tools/providers/command.py"),
        ("tests.test_connections",),
    ),
    (
        (
            "tools/autocode_run_modes.py",
            "tools/autocode_args.py",
            "tools/autocode_adaptive_planning.py",
            "tools/autocode_requirements_review.py",
        ),
        ("tests.test_run_modes",),
    ),
    (
        (
            "tools/autocode_requirements_handoff.py",
            "tools/autocode_requirements_review.py",
            "tools/autocode_workflows.py",
        ),
        ("tests.test_chat_cli", "tests.test_requirements_review", "tests.test_requirements_job_cli"),
    ),
    # A chat's Requirements names the job and the chat starts it (#1846); the test drives the CLI.
    (
        ("tools/autocode_requirements_chat.py", "tools/autocode_chat_view.py", "tools/units/autoplanner.py"),
        ("tests.test_requirements_job_cli",),
    ),
    (
        (
            "tools/autocode_review_",
            "tools/autocode_scratch_preparation.py",
            "tools/autocode_verify.py",
            "tools/units/autoreview.py",
        ),
        ("tests.test_review_attribution_cli",),
    ),
    (("tools/autocode_chat_", "tools/autocode_run_view.py", "tools/autocode_follow_up.py"), ("tests.test_chat_cli",)),
    # The bridge drives the CLI as a subprocess (its tests launch the CLI, so imports cannot select them).
    (
        ("tools/autocode_bridge_", "tools/autocode_github.py", "tools/autocode_subcommands.py"),
        ("tests.test_bridge_cli", "tests.test_bridge_end_to_end"),
    ),
    # `autocode changeset` drives runs as subprocesses, so imports cannot select its CLI test.
    (
        ("tools/autocode_registry.py",),
        (
            "tests.test_registry_project_cli",
            "tests.test_changeset_cli",
            "tests.test_changeset_delivery_cli",
            "tests.test_bug_questions",
        ),
    ),
    (
        (
            "tools/autocode_workspace_scope",
            "tools/autocode_scope_question.py",
            "tools/autocode_bug_questions.py",
            "tools/autocode_bug_job.py",
            "tools/autocode_requirements_handoff.py",
            "tools/autocode_requirements_review.py",
        ),
        ("tests.test_scope_question_cli", "tests.test_bug_questions"),
    ),
    (
        (
            "tools/autocode_changeset",
            "tools/autocode_run_view.py",
            "tools/autocode_scope_question.py",
            "tools/autocode_runs_command.py",
            "tools/autocode_chat_changeset.py",
        ),
        (
            "tests.test_changeset_cli",
            "tests.test_changeset_delivery_cli",
            "tests.test_changeset_chat",
            "tests.test_changeset_bridge",
        ),
    ),
    # The full-screen chat in a real terminal (#1646): its tests launch the CLI, so imports cannot select them.
    (("tools/autocode_chat_", "tools/autocode_client_actions.py"), ("tests.test_chat_screens",)),
    (
        ("tools/autocode_chat_", "tools/autocode_client_actions.py"),
        ("tests.test_chat_polling_cli", "tests.test_chat_inventory_cli"),
    ),
    (("tools/autocode.py", "tools/autopilot.py", "tools/autocode_taskrun.py"), ("tests.test_launch_state_cli",)),
    (
        ("tools/autocode_build_loop", "tools/autocode_run_setup", "tools/autocode_run_actions"),
        (*PUBLIC_FLOW_CONSUMERS, "tests.test_subprocess"),
    ),
    (("tools/autocode_launch", "tools/autocode_input_preflight"), ("tests.test_launch_state_cli",)),
    (
        ("tools/autocode_completion", "tools/autocode_milestone", "tools/units/autoreview"),
        ("tests.test_completion_properties", "tests.test_completion_status"),
    ),
    (("tools/autocode_milestone",), ("tests.test_subprocess",)),
    # These public status consumers reach the checkpoint projection through run_view.
    (("tools/autocode_code_checkpoints",), ("tests.test_liveness", "tests.test_usage")),
    # A parallel batch's integrated step is attributed to each member's requirements (#730) only through
    # a real orchestrated run: dispatch integrates it, code_checkpoints records it, criterion_changes reads it.
    (
        ("tools/autocode_dispatch", "tools/autocode_code_checkpoints", "tools/autocode_criterion_changes"),
        ("tests.test_parallel_criterion_changes",),
    ),
    (("tools/autocode_report", "tools/autocode_cmd_only_report"), ("tests.test_report_delivery_cli",)),
    # autocode_planning also matches autocode_planning_cycle; stage_roles and model_request left the planning unit (#695).
    (
        (
            "tools/autocode_planning",
            "tools/units/autoplanner",
            "tools/autocode_stage_roles",
            "tools/units/model_request",
        ),
        ("tests.test_planning_cycle_cli",),
    ),
    (("tools/autocode_provider_auth",), ("tests.test_provider_auth_cli", "tests.test_provider_credentials")),
    # An early access stop must retain the configured-provider environment guidance.
    (
        ("tools/autocode_access_evidence", "tools/autocode_model_preflight", "tools/autocode_provider_launch"),
        ("tests.test_access_evidence_cli",),
    ),
    (("tools/autocode_model_preflight",), ("tests.test_provider_credentials",)),
    (
        ("tools/autocode_run_records.py",),
        (*CORE_CONSUMERS, *PUBLIC_FLOW_CONSUMERS, "tests.test_paused_approval_cli"),
    ),
    (("tools/autocode_task_preflight", "tools/autocode_preflight"), ("tests.test_preflight_worker_cli",)),
    # --package's launch checks (resume, --test-root, committed files) are in autocode_run_setup.resolve,
    # and its out-of-package finding (autocode_package_writes) stops a run only through the CLI flow, as
    # does its --accept-completion refusal (the autocode_action_decisions step, reading autocode_run_base).
    # The regression proof runs the check itself: it records a launch's files outside the package (start) and
    # checks the candidate before each review dispatch (before_review), both called from the build loop. Its
    # path is given in full, since autocode_regression_selection does not reach the check.
    (
        (
            "tools/autocode_package_scope",
            "tools/autocode_package_writes",
            "tools/autocode_run_setup",
            "tools/autocode_action_decisions",
            "tools/autocode_run_base",
            "tools/autocode_build_loop",
            "tools/autocode_regression.py",
        ),
        ("tests.test_package_scope_cli",),
    ),
    # A --package bug fix's Investigator (#711): units.autoresolver hands it the package from
    # autocode_package_writes.context, and autocode_bug_job.check refuses a diagnosis outside the package by
    # autocode_package_scope's path rules. Only the CLI module drives them through TaskRun, with a stand-in
    # Investigator.
    (
        (
            "tools/autocode_bug_job",
            "tools/units/autoresolver",
            "tools/autocode_package_writes",
            "tools/autocode_package_scope",
        ),
        ("tests.test_package_investigator_cli",),
    ),
    # A mapped path no longer gets the fake flows every unmapped tools/ module selects (``dependencies``); these
    # two had them before the row above, and keep them.
    (("tools/autocode_bug_job", "tools/units/autoresolver"), (*CORE_CONSUMERS, *PUBLIC_FLOW_CONSUMERS)),
)

SUITE_PATHS = (
    "tools/run_suite.py",
    "tools/ci_test_selection.py",
    "tools/ci_scope.py",
    "tests/__init__.py",
    "tests/suite_exclusions.json",
    "tests/suite_slow.json",
    ".github/workflows/tests.yml",
    "pyproject.toml",
)
PROCESS_TOKENS = ("process", "provider", "session", "containment", "supervision", "preflight", "worker", "keeper")


def dependencies(path: str) -> set[str]:
    """Known dynamic consumers for one repository-relative changed path."""
    selected: set[str] = set()
    if path in SUITE_PATHS:
        selected.update(SUITE_CONSUMERS)
    if not path.startswith(
        (
            "tools/",
            "tests/",
            "scenarios/",
            "test-scenarios/",
            "macos-app/",
            ".github/",
            "docs/",
            "examples/",
            "README",
            "CHANGELOG",
            "LICENSE",
            "AGENTS.md",
            ".git",
            "mkdocs.",
        )
    ):
        # Unknown repository inputs may influence setup through data rather than
        # imports. Exercise a real fake-provider launch and completion, not only help.
        selected.update((*CORE_CONSUMERS, *PUBLIC_FLOW_CONSUMERS))
    if path.startswith("tools/prompts/") or path in (
        "tools/autocode_prompts.py",
        "tests/prompt_rendering_fixtures.json",
    ):
        selected.update(PROMPT_CONSUMERS)
    if path.startswith("tools/autocode-schemas/") or path in (
        "tools/autocode_role_schema.py",
        "tools/autocode_report_schema.py",
    ):
        selected.update(SCHEMA_CONSUMERS)
    for prefix, consumers in RESOURCE_CONSUMERS:
        if path.startswith(prefix):
            selected.update(consumers)
    if path.startswith("tools/") and not path.startswith(("tools/dashboard/", "tools/prompts/")):
        for prefixes, consumers in CLI_CONSUMERS:
            if path.startswith(prefixes):
                selected.update(consumers)
        if (
            any(token in path.rsplit("/", 1)[-1] for token in PROCESS_TOKENS)
            or path.startswith("tools/providers/")
            or path in ("tools/autocode.py", "tools/autopilot.py")
        ):
            selected.update(REFUSAL_CONSUMERS)
        if not path.endswith(".py") and not path.startswith("tools/autocode-schemas/") and not selected:
            selected.update((*CORE_CONSUMERS, *PUBLIC_FLOW_CONSUMERS))
    return selected

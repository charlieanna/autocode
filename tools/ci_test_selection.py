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
REQUIREMENTS_PROMPT_CONSUMERS = (
    *PROMPT_CONSUMERS,
    "tests.test_requirements_chat",
    "tests.test_chat_cli",
    "tests.test_requirements_review",
    "tests.test_requirements_job_cli",
    "tests.test_requirements_question_contract",
)
CORE_CONSUMERS = (
    "tests.test_cli_surface",
    "tests.test_taskrun_seam",
    "tests.test_inprocess_cli",
)
PUBLIC_FLOW_CONSUMERS = ("tests.test_launch_state_cli", "tests.test_completion_status")

# These helpers are loaded dynamically by the extension-host tests, not by a
# Python import. Keep the consuming test named so fixture-only edits run it.
EXTENSION_HOST_RESOURCES = {
    "tests/first_go_vscode_fixture.py": ("vscode-extension/test/host/python-semantic.test.cjs",),
    "tools/autocode_first_go_proof.py": ("vscode-extension/test/host/python-semantic.test.cjs",),
    "scenarios/harness/fake_codex.py": (
        "vscode-extension/test/host/editor-host.test.cjs",
        "vscode-extension/test/host/python-semantic.test.cjs",
    ),
    "vscode-extension/test/python-semantic-transition.cjs": (
        "vscode-extension/test/host/named-proof-refusal.test.cjs",
        "vscode-extension/test/host/python-semantic.test.cjs",
        "vscode-extension/test/host/semver-reference-repair.test.cjs",
    ),
    "vscode-extension/test/host/held_cli.py": (
        "vscode-extension/test/host/editor-host.test.cjs",
        "vscode-extension/test/host/python-semantic.test.cjs",
    ),
    "vscode-extension/test/owned-fixture-cleanup.cjs": (
        "vscode-extension/test/host/classification-outcome.test.cjs",
        "vscode-extension/test/host/editor-host.test.cjs",
        "vscode-extension/test/host/job-route-effort.test.cjs",
        "vscode-extension/test/host/kilo-route-effort.test.cjs",
        "vscode-extension/test/host/named-proof-refusal.test.cjs",
        "vscode-extension/test/host/python-semantic.test.cjs",
        "vscode-extension/test/host/semver-reference-repair.test.cjs",
        "vscode-extension/test/host/status-retry.test.cjs",
    ),
    "tools/autocode_changed_code_evidence.py": ("vscode-extension/test/host/python-semantic.test.cjs",),
    "tools/autocode_changed_code_measurement.py": ("vscode-extension/test/host/python-semantic.test.cjs",),
    "tools/autocode_changed_code_rework.py": ("vscode-extension/test/host/python-semantic.test.cjs",),
    "tools/autocode_changed_code_review.py": ("vscode-extension/test/host/python-semantic.test.cjs",),
    "tools/autocode_changed_code_review_application.py": ("vscode-extension/test/host/python-semantic.test.cjs",),
    "tools/autocode_diff_coverage.py": ("vscode-extension/test/host/python-semantic.test.cjs",),
    "tools/autocode_mutants.py": ("vscode-extension/test/host/python-semantic.test.cjs",),
    "tools/autocode_python_semantic_lines.py": ("vscode-extension/test/host/python-semantic.test.cjs",),
    "tests/guard_host_hook/sitecustomize.py": ("vscode-extension/test/host/job-route-effort.test.cjs",),
    "tests/fixture_plan_review.py": (
        "vscode-extension/test/host/named-proof-refusal.test.cjs",
        "vscode-extension/test/host/semver-reference-repair.test.cjs",
    ),
    "tests/host_reader_cleanup.py": (
        "vscode-extension/test/host/editor-host.test.cjs",
        "vscode-extension/test/host/named-proof-refusal.test.cjs",
        "vscode-extension/test/host/semver-reference-repair.test.cjs",
        "vscode-extension/test/host/classification-outcome.test.cjs",
        "vscode-extension/test/host/python-semantic.test.cjs",
        "vscode-extension/test/host/status-retry.test.cjs",
    ),
    "tests/classification_vscode_fixture.py": ("vscode-extension/test/host/classification-outcome.test.cjs",),
    **dict.fromkeys(
        (
            "tests/test_failure_routing_cli.py",
            "tests/test_build_blackbox.py",
            "tests/failure_routing_provider.py",
            "tests/operational_fixtures.py",
            "tools/blackbox_build_provider.py",
            "tools/inprocess_cli.py",
        ),
        (
            "vscode-extension/test/host/classification-outcome.test.cjs",
            "vscode-extension/test/host/named-proof-refusal.test.cjs",
        ),
    ),
    "tests/host_status_site/sitecustomize.py": ("vscode-extension/test/host/status-retry.test.cjs",),
    "tests/job_route_vscode_cli.py": (
        "vscode-extension/test/host/job-route-effort.test.cjs",
        "vscode-extension/test/host/kilo-route-effort.test.cjs",
    ),
    "tests/job_route_vscode_fixture.py": ("vscode-extension/test/host/job-route-effort.test.cjs",),
    "tests/kilo_route_vscode_fixture.py": ("vscode-extension/test/host/kilo-route-effort.test.cjs",),
    "tools/autocode_job_retry_admission.py": ("vscode-extension/test/host/kilo-route-effort.test.cjs",),
    "tests/provider_auth_fixture.py": ("vscode-extension/test/host/editor-host.test.cjs",),
    "tests/protected_link_vscode_fixture.py": ("vscode-extension/test/host/protected-link-recovery.test.cjs",),
    "tests/protected_directory_vscode_fixture.py": ("vscode-extension/test/host/python-semantic.test.cjs",),
    "tests/python_semantic_vscode_fixture.py": ("vscode-extension/test/host/python-semantic.test.cjs",),
    "tests/fixtures/python-semantic-host/displayed-plan.json": ("vscode-extension/test/host/python-semantic.test.cjs",),
    "tests/rustup_vscode_fixture.py": ("vscode-extension/test/host/rustup-containment.test.cjs",),
    "tests/named_proof_vscode_fixture.py": ("vscode-extension/test/host/named-proof-refusal.test.cjs",),
    "tests/semver_reference_vscode_fixture.py": ("vscode-extension/test/host/semver-reference-repair.test.cjs",),
    "tests/semver_reference_provider.py": ("vscode-extension/test/host/semver-reference-repair.test.cjs",),
    "vscode-extension/test/host/semver-reference-cli.py": (
        "vscode-extension/test/host/semver-reference-repair.test.cjs",
    ),
    **dict.fromkeys(
        (
            "tests/fixtures/semver-reference-m1/criteria.json",
            "tests/fixtures/semver-reference-m1/jsonio-source.py.txt",
            "tests/fixtures/semver-reference-m1/version-to-json.txt",
            "tests/fixtures/semver-reference-m1/version-from-json.txt",
            "tests/fixtures/semver-reference-m1/initial-test.py.txt",
            "tests/fixtures/semver-reference-m1/strengthened-test.py.txt",
            "tests/fixtures/semver-reference-m1/strengthened-diagnostic-test.py.txt",
            "tests/fixtures/semver-reference-m1/repaired-test.py.txt",
            "tests/fixtures/semver-reference-m1/diagnostic-test.py.txt",
            "tests/fixtures/semver-reference-m1/control-missing-export.py",
            "tests/fixtures/semver-reference-m1/control-missing-field.py",
            "tests/fixtures/semver-reference-m1/control-initial-field.py",
            "tests/fixtures/semver-reference-m1/control-grounded.py",
            "tests/fixtures/semver-reference-m1/control-delimiters.py",
            "tests/fixtures/semver-reference-m1/control-api.py",
        ),
        ("vscode-extension/test/host/semver-reference-repair.test.cjs",),
    ),
    "tests/source_recovery_vscode_fixture.py": ("vscode-extension/test/host/source-recovery.test.cjs",),
    "tests/status_retry_host_cleanup.py": (
        "vscode-extension/test/host/classification-outcome.test.cjs",
        "vscode-extension/test/host/editor-host.test.cjs",
        "vscode-extension/test/host/job-route-effort.test.cjs",
        "vscode-extension/test/host/kilo-route-effort.test.cjs",
        "vscode-extension/test/host/named-proof-refusal.test.cjs",
        "vscode-extension/test/host/python-semantic.test.cjs",
        "vscode-extension/test/host/status-retry.test.cjs",
        "vscode-extension/test/host/semver-reference-repair.test.cjs",
    ),
    "tests/status_retry_host_cli.py": ("vscode-extension/test/host/status-retry.test.cjs",),
    "tests/status_retry_host_fixture.py": ("vscode-extension/test/host/status-retry.test.cjs",),
}
ACTIONLESS_CONSUMERS = (
    "tests.test_actionless_consumers",
    "tests.test_issue",
    "tests.test_multicomponent",
    "tests.test_demo",
    "tests.test_conformance_actionless",
    "tests.test_taskrun_no_action",
    "tests.test_taskrun_recovery",
)
OPERATIONAL_CONSUMERS = (
    "tests.test_operational_disposition",
    "tests.test_operational_exhaustion_public_contract",
    "tests.test_operational_information",
    "tests.test_operational_pause_authority",
    "tests.test_operational_reconsideration",
    "tests.test_operational_recovery",
    "tests.test_human_publication",
    "tests.test_resolver_human",
    "tests.test_resolver_runtime",
    "tests.test_startup_timeout_person_view",
    "tests.test_pause_authority",
    "tests.test_goals",
    "tests.test_operational_block",
    "tests.test_metadata_operational_block",
    "tests.test_model_listing_failure",
    "tests.test_model_listing_failure_cli",
    "tests.test_verification_preparation_failure",
    "tests.test_repaired_acceptance_no_progress",
    "tests.test_operational_hold",
    "tests.test_approval_provenance_cli",
    "tests.test_dispatch",
    "tests.test_planning",
    "tests.test_quota_worker",
    "tests.test_finalizer_timeouts",
    "tests.test_activity_runtime",
    "tests.test_autocode",
    "tests.test_member_stop_settings",
    "tests.test_subprocess",
    "tests.test_unlimited_iterations",
    "tests.test_completion_timeout_recovery",
    "tests.test_milestone_checkpoints",
    "tests.test_saved_run_replay_cli",
)
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

# These callers enter scratch preparation through the verifier or an owned
# worker, so their source does not directly import either shared copy module.
SCRATCH_COPY_CONSUMERS = (
    "tests.test_autocode_protected_oracles",
    "tests.test_design_check_job",
    "tests.test_protected_test_links",
    "tests.test_review_mutation_runtime",
    "tests.test_review_proof",
    "tests.test_scratch_copy_identity",
    "tests.test_scratch_link_binding",
    "tests.test_source_reconciliation",
    "tests.test_verification_preparation",
)

# Black-box consumers cannot import their runtime producers. Job scope and the scenario runner share this
# map; its producer and top-level scenario files also select the map-discovery guard.
SCENARIO_RUNTIME_CONSUMERS = {
    "tools/autocode_run_view.py": ("scenarios.test_escalation",),
    "tools/autocode_recovery_view.py": ("scenarios.test_escalation",),
    "tools/autocode_light_builder.py": ("scenarios.test_escalation",),
    "tools/autocode_builder_policy.py": ("scenarios.test_escalation",),
    **{
        f"scenarios/catalog/{scenario}/oracle.py": ("scenarios.test_reliability_oracle_outcomes",)
        for scenario in (
            "greenfield-todo-cli",
            "feature-timesheet-by-project",
            "bugfix-iso-weeks",
            "bugfix-realrepo-docutils-number-lines",
        )
    },
    **{
        f"scenarios/catalog/{scenario}/{resource}": (
            "scenarios.test_harness.AdaptiveCompletionTests.test_progressive_delegations_retain_the_review_that_authorizes_execution",
        )
        for scenario in ("progressive-cumulative-regression", "progressive-split-learning-journey")
        for resource in ("seed/test_journey.py", "slices/S1/lessons.py", "oracle.py", "scenario.toml")
    },
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
    ("tests/fixture_plan_review.py", ("tests.test_semver_reference_plan_review",)),
    ("tests/semver_reference_vscode_fixture.py", ("tests.test_semver_reference_plan_review",)),
    ("tests/semver_reference_provider.py", ("tests.test_semver_reference_provider",)),
    *(
        (
            path,
            (
                "tests.test_response_language",
                "tests.test_role_launch",
                "tests.test_provider_launch",
                "tests.test_native_image",
                "tests.test_native_report_repair",
            ),
        )
        for path in (
            "tools/autocode_response_language.py",
            "tools/prompts/fragments/support/response-language.md",
        )
    ),
    ("tests/operational_fixtures.py", OPERATIONAL_CONSUMERS),
    (
        "tools/ci_tested_source.py",
        ("tests.test_ci_tested_source", "tests.test_attribute_failure", "tests.test_attribute_failure_source"),
    ),
    (
        "tools/autocode_codex_settings.py",
        (
            "tests.test_autocode_support",
            "tests.test_catalogue_t09",
            "tests.test_catalogue_t11",
            "tests.test_metadata_preflight",
            "tests.test_metadata_startup",
            "tests.test_run_setup_boundary",
        ),
    ),
    (
        "tools/autocode_acceptance_tests.py",
        ("tests.test_acceptance_archive_order", "tests.test_replay_transport", "tests.test_usage"),
    ),
    (
        "tools/autocode_attempt_ledger.py",
        (
            "tests.test_attempt_ledger",
            "tests.test_planning_calls",
            "tests.test_usage",
            "tests.test_cost",
            "tests.test_cost_breakdown",
            "tests.test_escalation_view",
            "tests.test_efficiency",
            "tests.test_resolve_repair",
            "tests.test_retained_attempt_display",
        ),
    ),
    (
        "tools/autocode_stage_activity.py",
        (
            "tests.test_retained_attempt_display",
            "tests.test_stage_pipeline",
            "tests.test_stage_decisions",
            "tests.test_progress_view",
            "tests.test_liveness",
            "tests.test_run_stage_models",
            "tests.test_chat_view",
            "tests.test_chat_progress",
            "tests.test_json_contract",
            "tests.test_json_status_observer_cli",
            *CORE_CONSUMERS,
            *PUBLIC_FLOW_CONSUMERS,
        ),
    ),
    ("tests/test_changed_code_evidence_cli.py", ("tests.test_mutation_continuation_cli",)),
    (
        "tests/metadata_capture_fixture.py",
        ("tests.test_metadata_recovery", "tests.test_metadata_parent_recovery"),
    ),
    *(
        (path, ("tests.test_storage_errors", "tests.test_verification_preparation_failure"))
        for path in ("scenarios/harness/driver.py", "docs/schemas/status-view.schema.json")
    ),
    *(
        (
            path,
            (
                "tests.test_python_self_suite",
                "tests.test_python_self_suite_cli",
                "tests.test_acceptance_self_suite_preflight",
            ),
        )
        for path in (
            "tools/autocode_python_self_suite.py",
            "tools/autocode_wrapped_runner.py",
            "tools/autocode_acceptance_tests_job.py",
            "tools/autocode_verify.py",
            "tools/autocode_scratch_suites.py",
            "tools/autocode_verification_preparation.py",
            "tools/autocode_verification_prepare_worker.py",
            "tools/autocode_action_authority.py",
            "tools/units/autoreview.py",
            "tools/prompts/acceptance-tests.md",
            "tools/prompts/fragments/test-cases/named-proof-note.md",
            "tools/prompts/fragments/autoplanner/example-criteria-rule.md",
            "tests/fixtures/python_self_suite.py.txt",
        )
    ),
    *(
        (path, ("tests.test_tester_executed_proof", "tests.test_proof_projection"))
        for path in (
            "tools/autocode.py",
            "tools/autocode_context.py",
            "tools/autocode_role_launch.py",
            "tools/autocode_provider_launch.py",
            "tools/autocode_native_image.py",
            "tools/autocode_proof_projection.py",
            "tools/prompts/fragments/provider-launch/containment-prompt-06.md",
            "tools/prompts/fragments/provider-launch/containment-prompt-07.md",
        )
    ),
    ("pyproject.toml", ("tests.test_repository_pytest",)),
    (".github/workflows/tests.yml", ("tests.test_ci_java",)),
    ("tools/autocode_stage_context.py", ("tests.test_ready_capture_batch_cli",)),
    (
        "tools/autocode_ready_capture_batch.py",
        ("tests.test_ready_capture_batch_cli", "tests.test_capture_event_contract"),
    ),
    *(
        (path, ("tests.test_review_report_repair_cli",))
        for path in ("tools/autocode_report_edits.py", "tools/autocode_report_repair_context.py")
    ),
    ("tools/autocode_pause_codes.py", ("tests.test_run_status",)),
    ("tools/autocode_stuck_job.py", ("tests.test_investigator_pause_cli",)),
    ("tools/autocode_planning_advice.py", ("tests.test_investigator_pause_cli",)),
    (
        "tools/prompts/fragments/autoplanner/example-check-rule.md",
        ("tests.test_planning_scope_discipline",),
    ),
    (
        "tools/prompts/fragments/goals/job-type-policy.md",
        ("tests.test_planning_scope_discipline",),
    ),
    ("tools/autocode_acceptance_policy.py", ("tests.test_planning_scope_discipline",)),
    ("tools/autocode_legacy_process.py", ("tests.test_json_status_observer_cli",)),
    (
        "tools/autocode_saved_names.py",
        ("tests.test_saved_names", "tests.test_saved_names_cli", "tests.test_recovery_view"),
    ),
    ("tools/autocode_chat_confirmation.py", ("tests.test_chat_confirmation", "tests.test_chat_client")),
    ("tools/prompts/acceptance-tests.md", ("tests.test_go_prerequisites_execution",)),
    ("tools/autocode_capability_startup.py", ("tests.test_capability_startup", "tests.test_demo")),
    ("tools/autocode_native_invocation.py", ("tests.test_capability_startup", "tests.test_demo")),
    (
        "scenarios/harness/fake_changed_code.py",
        (
            "tests.test_risk_cli",
            "tests.test_brief_cli",
            "tests.test_scenario_changed_code_fixtures",
            "tests.test_native_scenario_fixture_reasons",
            "tests.test_acceptance_tests",
            "tests.test_acceptance_writer_route",
            "tests.test_follow_up_acceptance_cli",
            "tests.test_requirements_first_cli",
        ),
    ),
    (
        "scenarios/catalog/ladder-18-durable-lease-queue/broken/non-atomic-claim/",
        ("tests.test_risk_cli",),
    ),
    (
        "scenarios/catalog/ladder-18-durable-lease-queue/broken/process-local-tokens/",
        ("tests.test_risk_cli",),
    ),
    (
        "scenarios/catalog/ladder-19-transactional-outbox/broken/ack-with-exception-rollback/",
        ("tests.test_risk_cli",),
    ),
    (
        "tools/goal_fixtures.py",
        (
            "tests.test_goal_fixture_changed_code",
            "tests.test_artifact_review",
            "tests.test_workflow",
            "tests.test_final_workflow",
            "tests.test_protected_oracles_cli",
        ),
    ),
    (
        "tools/live_fixture_provider.py",
        (
            "tests.test_live_fixture_changed_code",
            "tests.test_taskrun",
            "tests.test_agent_step",
            "tests.test_codex_capture_copy",
        ),
    ),
    *(
        (path, ("tests.test_changed_code_evidence_cli",))
        for path in (
            "tools/autocode_affected_tests.py",
            "tools/autocode_changed_code_evidence.py",
            "tools/autocode_changed_code_measurement.py",
            "tools/autocode_changed_code_proof.py",
            "tools/autocode_changed_code_trigger.py",
            "tools/autocode_changed_code_review.py",
            "tools/autocode_changed_code_rework.py",
            "tools/autocode_changed_code_review_application.py",
            "tools/prompts/fragments/regression/changed-code-evidence.md",
            "tools/autocode_diff_coverage.py",
            "tools/autocode_mutation_check.py",
            "tools/autocode_project_suite_health.py",
            "tools/autocode_regression.py",
            "tools/autocode_report_schema.py",
            "tools/autocode_role_schema.py",
            "tools/autocode_stage_context.py",
            "tools/autocode_stuck_job.py",
            "tools/autocode_workflow.py",
            "tools/autocode_completion.py",
            "tools/autopilot.py",
        )
    ),
    *(
        (path, ("tests.test_execution_criteria_prompt", "tests.test_builder_criteria_cli"))
        for path in (
            "tools/autocode_execution_prompt.py",
            "tools/autocode_stage_context.py",
            "tools/autocode_report_schema.py",
            "tools/units/common.py",
            "tools/units/autoresolver.py",
            "tools/prompts/fragments/goals/execution-prompt.md",
            "tools/prompts/fragments/goals/criteria-",
        )
    ),
    ("tools/autocode_trace_coverage.py", ("tests.test_conflict_generation_cli",)),
    ("tools/autocode_planning_report_policy.py", ("tests.test_conflict_generation_cli",)),
    ("tools/units/autoplanner.py", ("tests.test_conflict_generation_cli", "tests.test_prompt_layout_and_budget")),
    (
        "tools/autocode_proof_detail.py",
        (
            "tests.test_scenario_changed_code_fixtures",
            "tests.test_adversarial_fixture_reasons",
            "tests.test_goal_fixture_changed_code",
            "tests.test_live_fixture_changed_code",
            "tests.test_multicomponent_fake_reasons",
        ),
    ),
    ("tools/autocode_fixture_state.py", ("tests.test_run_state", "tests.test_run_state_slices")),
    (
        "tools/autocode_budget_state.py",
        ("tests.test_run_state", "tests.test_run_state_slices", "tests.test_cost", "tests.test_cost_breakdown"),
    ),
    (
        "tools/autocode_regression_state.py",
        ("tests.test_run_state", "tests.test_run_state_slices", "tests.test_focused_suite"),
    ),
    (
        "tools/autocode_test_results.py",
        (
            "tests.test_verify_domain_split",
            "tests.test_cargo_tests",
            "tests.test_go_tests",
            "tests.test_jest_tests",
            "tests.test_rspec_tests",
            "tests.test_vitest_tests",
        ),
    ),
    (
        "tools/autocode_suite_boundary.py",
        ("tests.test_verify_domain_split", "tests.test_suite_definition_policy", "tests.test_definition_refusals"),
    ),
    ("tools/autocode_draft_failure_correction.py", ("tests.test_draft_failure_correction_cli",)),
    (
        "tools/autocode_report_repair_context.py",
        ("tests.test_draft_failure_correction_cli", "tests.test_build_graph_cli"),
    ),
    (
        "tools/prompts/fragments/autoplanner/draft-failure-correction.md",
        ("tests.test_draft_failure_correction", "tests.test_draft_failure_correction_cli"),
    ),
    *((path, ("tests.test_run_suite",)) for path in sorted(SCENARIO_SELECTION_INPUTS)),
    # The suite collector is executed by the verifier as a dynamic runner resource; its
    # own imports are invisible to the import selector (#2414).
    ("tools/autocode_component_tests.py", ("tests.test_verify", "tests.test_test_root")),
    ("tests/test_report_repair.py", ("tests.test_native_event_identity_cli",)),
    # The mutation catalogue invokes this detector by module name in a disposable subprocess.
    ("tests/test_catalogue_t06.py", ("tests.test_catalogue_t14",)),
    # The quota worker's generated bootstrap imports the offline fixture inside its
    # subprocess, invisible to the import selector (#2442).
    ("tests/opencode_fixture_cli.py", ("tests.test_quota_worker",)),
    ("tools/prompts/fragments/stage-context/context-packet-04.md", ("tests.test_capture_event_contract",)),
    ("tools/prompts/fragments/stage-context/builder-source.md", ("tests.test_review_source",)),
    (
        "tools/prompts/fragments/stage-context/implementation-discrepancy.md",
        ("tests.test_implementation_discrepancy",),
    ),
    ("tools/autocode_scratch_preparation.py", SCRATCH_COPY_CONSUMERS),
    ("tools/autocode_source_snapshot.py", SCRATCH_COPY_CONSUMERS),
    ("tools/autocode_source_directory.py", (*SCRATCH_COPY_CONSUMERS, "tests.test_protected_data_links")),
    ("tools/autocode_named_collection.py", ("tests.test_requirements_observations",)),
    ("tools/autocode_suite_claim_replay.py", ("tests.test_requirements_observations",)),
    ("tools/autocode_diagnostics_comparison.py", ("tests.test_claim_evidence", "tests.test_metadata_custody")),
    ("tools/autocode_reference_diagnostics.py", ("tests.test_claim_evidence",)),
    ("tools/autocode_capture_command.py", ("tests.test_claim_evidence",)),
    (
        "tools/autocode_captured_process.py",
        ("tests.test_captured_process", "tests.test_claim_evidence", "tests.test_metadata_custody"),
    ),
    ("tools/autocode_test_examples.py", ("tests.test_builder_source_examples_cli",)),
    (
        "tools/prompts/fragments/test-examples/",
        ("tests.test_test_examples", "tests.test_builder_source_examples_cli"),
    ),
    (
        "tools/prompts/fragments/stage-context/context-packet-07.md",
        ("tests.test_milestone_checkpoints", "tests.test_build_graph_cli"),
    ),
    (
        "tools/prompts/discovery.md",
        (
            "tests.test_requirements_observations",
            "tests.test_requirements_observations_cli",
        ),
    ),
    (
        "tools/prompts/fragments/autoplanner/requirements-observation-reuse.md",
        ("tests.test_requirements_observations", "tests.test_requirements_observations_cli"),
    ),
    (
        "tools/prompts/fragments/autoplanner/requirements-round-observations.md",
        ("tests.test_requirements_observations", "tests.test_requirements_observations_cli"),
    ),
    ("tools/prompts/fragments/autoplanner/time-budget.md", ("tests.test_planning", "tests.test_planning_time")),
    (
        "tools/prompts/fragments/autoplanner/retained-review-observations.md",
        ("tests.test_planning_recovery_observations",),
    ),
    (
        "tools/autocode_prompt_layout.py",
        ("tests.test_prompt_layout_and_budget", "tests.test_usage_outcome_report"),
    ),
    (
        "tools/autocode_field_budget.py",
        ("tests.test_prompt_layout_and_budget", "tests.test_usage_outcome_report"),
    ),
    ("tools/prompts/fragments/check-replay/validator-note.md", ("tests.test_check_replay",)),
    (
        "tools/prompts/fragments/provider-opencode/review-output.md",
        ("tests.test_schema_prompt_formatting", "tests.test_tool_handoff"),
    ),
    (
        "tools/prompts/validator.md",
        ("tests.test_validation_metadata", "tests.test_report_repair", "tests.test_validation_evidence_cli"),
    ),
    (
        "tools/prompts/completion-review.md",
        ("tests.test_completion_properties", "tests.test_validation_evidence_cli"),
    ),
    (
        "tests/stage_repair_provider.py",
        (
            "tests.test_validation_evidence_cli",
            "tests.test_verification_claim_cli",
            "tests.test_stage_repair_cli",
            "tests.test_discovery_report_edits_cli",
        ),
    ),
    (
        "tools/prompts/fragments/provider-opencode/prompt-for-schema.md",
        ("tests.test_schema_prompt_formatting", "tests.test_report_repair"),
    ),
    (
        "tools/prompts/fragments/provider-opencode/prompt-for-schema-02.md",
        ("tests.test_schema_prompt_formatting", "tests.test_report_repair", "tests.test_tool_handoff"),
    ),
    (
        "tools/prompts/fragments/provider-claude/prompt-for-schema.md",
        ("tests.test_claude_provider", "tests.test_report_repair"),
    ),
    ("tools/prompts/fragments/autocode/execute-report-repair-06.md", ("tests.test_report_repair",)),
    (
        "tests/coordination_provider.py",
        ("tests.test_coordination_tui_e2e", "tests.test_planning_scope_cli", "tests.test_coordination_gate_cli"),
    ),
    ("docs/schemas/status-view.schema.json", ("tests.test_json_contract", "tests.test_plan_review_cli")),
    ("docs/", ("tests.test_doc_examples",)),
    ("examples/", ("tests.test_doc_examples",)),
    ("README.md", ("tests.test_doc_examples",)),
    ("scenarios/catalog/greenfield-todo-cli/reference/", ("tests.test_brief_cli",)),
    ("scenarios/catalog/review-clean-pr/", ("tests.test_review_clean_fixture",)),
    ("scenarios/catalog/local-compose-two-services/", ("tests.test_local_compose_scenario",)),
    ("scenarios/harness/components_driver.py", ("tests.test_local_compose_scenario",)),
    ("tools/prompts/fragments/provider-launch/tool-free-report-repair.md", ("tests.test_provider_launch",)),
    ("scenarios/harness/profiles.py", ("tests.test_kilocode_effort",)),
    ("scenarios/harness/java_toolchain.py", ("tests.test_ci_java",)),
    (
        "tools/prompts/acceptance-tests.md",
        ("tests.test_acceptance_tests", "tests.test_acceptance_runner_handoff", "tests.test_acceptance_test_repair"),
    ),
    (
        "tools/prompts/fragments/acceptance-tests/",
        (
            "tests.test_acceptance_tests",
            "tests.test_acceptance_provenance",
            "tests.test_acceptance_runner_handoff",
            "tests.test_acceptance_test_repair",
        ),
    ),
    ("tools/prompts/fragments/test-cases/diagnosis-builder-note.md", ("tests.test_acceptance_tests",)),
    (
        "tools/prompts/fragments/test-cases/builder-proof-note.md",
        ("tests.test_test_cases", "tests.test_bugfix_workflow"),
    ),
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
            "tests.test_build_graph_cli",
        ),
    ),
    *(
        (path, ("tests.test_build_graph_cli",))
        for path in (
            "tools/autocode_dispatch.py",
            "tools/autocode_builder_worker.py",
            "tools/autocode_milestones.py",
            "tests/test_build_blackbox.py",
        )
    ),
    ("tools/fake_command_tool.py", ("tests.test_command_flow", "tests.test_command_source_schema")),
    ("tools/fake_codex.py", ("tests.test_subprocess",)),
    # The #2133 CLI regression drives the box through the fake provider; its tests import neither module.
    ("tools/autocode_current_behavior.py", ("tests.test_bugfix_workflow",)),
    (
        "tools/autocode_prices.json",
        ("tests.test_prices", "tests.test_cost", "tests.test_cost_cap", "tests.test_route_selector"),
    ),
    (
        "tools/autocode-schemas/evidence-report.schema.json",
        ("tests.test_evidence_report",),
    ),
    (
        "scenarios/harness/conformance.py",
        ("tests.test_planning_allowance_cli", "tests.test_conformance_actionless"),
    ),
    ("scenarios/harness/driver.py", ACTIONLESS_CONSUMERS),
    (
        "scenarios/harness/fake_codex.py",
        (
            "tests.test_fake_codex_event_identity",
            "tests.test_brief_cli",
            "tests.test_risk_cli",
            "tests.test_requirements_job_cli",
            "tests.test_changed_code_evidence_cli",
            "tests.test_scenario_report_envelope",
            "tests.test_scenario_requirements_fixtures",
        ),
    ),
    ("tools/autocode_report_edits.py", ("tests.test_changed_code_evidence_cli", "tests.test_scenario_report_envelope")),
    ("tools/fake_codex.py", ("tests.test_subprocess",)),
    (
        "tools/autocode_report_repair_route.py",
        ("tests.test_report_repair", "tests.test_classification_recovery"),
    ),
    ("tools/autocode_report_repair_queue.py", ("tests.test_resolver_runtime",)),
    (
        "tests/failure_routing_provider.py",
        ("tests.test_failure_routing_cli", "tests.test_classification_recovery"),
    ),
    ("scenarios/harness/clarifying_requirements_provider.py", ("tests.test_requirements_review",)),
    ("scenarios/harness/hidden_assumptions_provider.py", ("tests.test_scenario_person_answers",)),
    ("scenarios/harness/person_answers.py", ("tests.test_scenario_person_answers",)),
    ("tools/autocode_requirements_handoff.py", ("tests.test_requirements_citation_cli",)),
    ("scripts/homebrew_formula.py", ("tests.test_homebrew_formula",)),
    ("scripts/run_linux_fake.py", ("tests.test_linux_fake_container",)),
    ("scripts/linux-fake.Dockerfile", ("tests.test_linux_fake_container",)),
    ("scripts/update-vscode-extension.sh", ("tests.test_update_vscode_extension",)),
    ("scripts/publish-vscode-extension.sh", ("tests.test_publish_vscode_extension",)),
    (".github/workflows/publish-vscode-extension.yml", ("tests.test_publish_vscode_extension",)),
    (
        "tools/prompts/fragments/stuck-repair-context/evidence-instruction-02.md",
        ("tests.test_stuck_repair_context", "tests.test_classification_recovery"),
    ),
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
        "tools/prompts/fragments/autoplanner/code-refs-rule.md",
        ("tests.test_planning", "tests.test_report_repair"),
    ),
    (
        "tools/prompts/fragments/report-repair-context/evidence-probe.md",
        ("tests.test_report_repair", "tests.test_test_cases"),
    ),
    (
        "tools/prompts/fragments/report-repair-context/evidence-counterexample.md",
        ("tests.test_report_repair",),
    ),
    ("tools/providers/requirements_stream.mjs", ("tests.test_requirements_stream", "tests.test_requirements_chat")),
    ("tools/providers/opencode_activity.mjs", ("tests.test_opencode_activity", "tests.test_kilo_progress")),
    ("tools/autocode_requirements_prompt.py", REQUIREMENTS_PROMPT_CONSUMERS),
    ("tools/prompts/requirements-gather.md", REQUIREMENTS_PROMPT_CONSUMERS),
    ("tools/prompts/fragments/autoplanner/requirements-conversation.md", REQUIREMENTS_PROMPT_CONSUMERS),
    ("tools/prompts/fragments/autoplanner/assumption-policy.md", REQUIREMENTS_PROMPT_CONSUMERS),
    ("tools/prompts/fragments/autoplanner/question-policy.md", REQUIREMENTS_PROMPT_CONSUMERS),
    (
        "tools/prompts/fragments/goals/decision-provenance.md",
        (*PROMPT_CONSUMERS, "tests.test_assumption_ledger", "tests.test_goals"),
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
    ("tools/prompts/gate-chat.md", ("tests.test_gate_chat_cli",)),
    ("tools/prompts/coordinated-plan-chat.md", ("tests.test_coordination_gate_cli",)),
    (
        "tools/prompts/run-chat.md",
        (
            "tests.test_run_messages",
            "tests.test_message_context",
            "tests.test_run_messages_cli",
            "tests.test_task_message_context_cli",
        ),
    ),
    ("tools/autocode_kilo_image_delivery.mjs", ("tests.test_kilo_image_delivery", "tests.test_native_image")),
    (
        "tools/fixtures/multicomponent_fake.py",
        ("tests.test_multicomponent", "tests.test_local_compose_scenario", "tests.test_multicomponent_fake_reasons"),
    ),
    (
        "tools/fixtures/kilo-context-overflow-run.jsonl",
        ("tests.test_context_window", "tests.test_job_failure_recovery"),
    ),
    ("tools/prompts/fragments/milestones/", ("tests.test_milestone_policy",)),
    ("tools/prompts/fragments/milestone-replan/", ("tests.test_milestone_policy", "tests.test_milestone_replan")),
    (
        "tools/prompts/fragments/autoplanner/evidence-facts-02.md",
        ("tests.test_planning_proof_reference", "tests.test_bugfix_workflow"),
    ),
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
    ("tools/prompts/fragments/package-scope/", ("tests.test_package_writes",)),
    (
        "tools/autocode_report_check.py",
        ("tests.test_report_check", "tests.test_report_check_conversations", "tests.test_report_check_retention"),
    ),
    ("docs/efficiency-targets.json", ("tests.test_usage_outcome_report",)),
    *(
        (path, ("tests.test_stage_access",))
        for path in (
            "tools/autocode_design_check_job.py",
            "tools/autocode_stuck_job.py",
            "tools/autocode_resolve_repair_job.py",
            "tools/autocode_design_intake.py",
            "tools/autocode_acceptance_tests_job.py",
        )
    ),
)

# A CLI consumer is selected even if marked slow: otherwise the import-only gap
# that previously broke master would remain. These are fake-provider consumers,
# never a request to qualify against live models.
CLI_CONSUMERS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        (
            "tools/autocode.py",
            "tools/autocode_action_decisions.py",
            "tools/autocode_requirements_chat.py",
            "tools/autocode_skip_requirements.py",
        ),
        (
            "tests.test_chat_cli",
            "tests.test_command_flow",
            "tests.test_skip_requirements",
            "tests.test_requirements_review",
        ),
    ),
    (
        (
            "tools/autocode_issue.py",
            "tools/autocode_multicomponent.py",
            "tools/autocode_demo.py",
        ),
        ACTIONLESS_CONSUMERS,
    ),
    # Saved operational facts have public/recovery consumers beyond their import graph.
    (
        (
            "tools/autocode_operational_disposition.py",
            "tools/autocode_operational_admission.py",
            "tools/autocode_operational_outcome.py",
            "tools/autocode_operational_receipt_identity.py",
            "tools/autocode_operational_hold.py",
            "tools/autocode_owned_operations.py",
            "tools/autocode_recovery_accounting.py",
        ),
        OPERATIONAL_CONSUMERS,
    ),
    (("tools/autocode_util.py",), ("tests.test_util_digest_prefix",)),
    # Captured commands execute this private helper by file path, beyond Python imports.
    (
        (
            "tools/autocode_capture_reaper.py",
            "tools/autocode_capture_family.py",
            "tools/autocode_captured_process.py",
            "tools/autocode_metadata_custody.py",
        ),
        (
            "tests.test_captured_process",
            "tests.test_capture_family",
            "tests.test_connection_processes",
            "tests.test_local_run",
            "tests.test_metadata_custody",
            "tests.test_metadata_preflight",
            "tests.test_metadata_recovery",
            "tests.test_metadata_parent_recovery",
            "tests.test_metadata_startup",
            "tests.test_process_without_psutil",
            "tests.test_taskrun",
            "tests.test_tool_containment_parallel",
        ),
    ),
    (
        (
            "tools/autocode_mutation_checkpoint.py",
            "tools/autocode_mutation_check.py",
            "tools/autocode_changed_code_continuation.py",
            "tools/autocode_changed_code_proof.py",
            "tools/autocode_completion_result.py",
            "tools/autocode_regression.py",
        ),
        ("tests.test_mutation_continuation_cli",),
    ),
    (
        ("tools/autocode_contract_identity.py",),
        (
            "tests.test_native_abandon_constructor",
            "tests.test_native_report_repair",
            "tests.test_verification_repair_retirement",
            "tests.test_changeset_barrier",
        ),
    ),
    (
        (
            "tools/providers/command.py",
            "tools/autocode_model_listing_failure.py",
            "tools/autocode_model_listing_failure_recovery.py",
            "tools/autocode_run_actions.py",
            "tools/autocode_action_recovery.py",
            "tools/autocode_run_view.py",
            "tools/autocode_recovery_view.py",
            "tools/autocode_status_command.py",
        ),
        ("tests.test_model_listing_failure", "tests.test_model_listing_failure_cli"),
    ),
    (
        (
            "tools/autocode_storage_errors.py",
            "tools/autocode_verification_copy.py",
            "tools/autocode_verification_failure.py",
            "tools/autocode_verification_failure_recovery.py",
            "tools/autocode_provider_launch.py",
            "tools/autocode_recovery_dispatch.py",
            "tools/autocode_action_recovery.py",
            "tools/autocode_run_view.py",
            "tools/autocode_recovery_view.py",
            "tools/autocode_recovery_history.py",
            "tools/autocode_status_command.py",
            "tools/autocode_status_summary.py",
            "tools/autocode_stop_explanations.py",
            "tools/autocode_progress_view.py",
            "tools/autocode_chat_view.py",
            "tools/autocode_chat_client.py",
            "tools/autocode_build_loop.py",
        ),
        ("tests.test_storage_errors", "tests.test_verification_preparation_failure"),
    ),
    (
        ("tools/autocode_plan_file.py", "tools/autocode_run_view.py"),
        ("tests.test_plan_review_cli", "tests.test_plan_overview"),
    ),
    (
        ("tools/autocode_plan_file.py", "tools/autocode_plan_command.py"),
        ("tests.test_acceptance_tests",),
    ),
    (("tools/autocode_visual_protection.py",), ("tests.test_native_rework_transport",)),
    (
        (
            "tools/autocode_verification_repair.py",
            "tools/autocode_verification_config.py",
            "tools/autocode_run_setup.py",
        ),
        ("tests.test_verification_repair_retirement", "tests.test_verification_config"),
    ),
    (
        (
            "tools/autocode_phase_timing.py",
            "tools/autocode_unit_request.py",
            "tools/autocode.py",
            "tools/autocode_regression.py",
            "tools/autocode_role_launch.py",
        ),
        ("tests.test_phase_timing_cli",),
    ),
    (
        (
            "tools/autocode_requirements_observations.py",
            "tools/autocode_suite_observation_view.py",
            "tools/autocode_report_edits.py",
            "tools/autocode_repaired_result.py",
            "tools/autocode_requirements_handoff.py",
            "tools/autocode_job_source.py",
            "tools/units/autoplanner.py",
        ),
        ("tests.test_requirements_observations_cli",),
    ),
    (
        ("tools/autocode_suite_observation_view.py", "tools/autocode_status_observation_worker.py"),
        ("tests.test_requirements_observations", "tests.test_status_observation"),
    ),
    (
        ("tools/autocode_source_metadata.py", "tools/autocode_source_trace.py"),
        ("tests.test_ticket_title_cli", "tests.test_source_trace_cli"),
    ),
    (
        (
            "tools/autocode_role_transport.py",
            "tools/autocode_quota_route.py",
            "tools/autocode_action_resolver.py",
            "tools/fake_command_tool.py",
            "tools/autocode_taskrun.py",
        ),
        ("tests.test_provider_route_recovery_cli",),
    ),
    (
        (
            "tools/autocode_python_packaging.py",
            "tools/autocode_python_test_roles.py",
            "tools/autocode_test_role_inventory.py",
            "tools/autocode_test_role_correction.py",
            "tools/autocode_run_setup.py",
            "tools/autocode_verify.py",
            "tools/autocode_protected_oracles.py",
        ),
        ("tests.test_python_test_roles_cli",),
    ),
    (
        ("tools/autocode_regression.py", "tools/autocode_test_role_correction.py"),
        ("tests.test_saved_run_replay_cli",),
    ),
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
        ("tools/autocode_goals.py", "tools/autocode_adaptive_planning.py"),
        ("tests.test_intervention_plan_feedback_cli",),
    ),
    (
        (
            "tools/autocode_planning_cycle.py",
            "tools/autocode_budget_recovery.py",
            "tools/autocode_planning_correction_budget.py",
            "tools/autocode_default_budget_runtime.py",
        ),
        ("tests.test_planning_correction_budget_cli",),
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
        (
            "tools/autocode_handoff_dedup.py",
            "tools/autocode_context.py",
            "tools/autocode_proof_detail.py",
            "tools/autocode_stage_context.py",
        ),
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
            "tools/autocode_source_reconciliation.py",
            "tools/autocode_proof_git.py",
            "tools/autocode_preparation_source_failure.py",
        ),
        ("tests.test_chat_cli",),
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
    (
        ("tools/autocode_pause_command.py", "tools/autocode_run_view.py"),
        ("tests.test_pause_status_cli", "tests.test_checkpoint_reconciliation", "tests.test_worker_flow"),
    ),
    (("tools/autocode_stop_explanations.py",), ("tests.test_completion_status",)),
    (
        (
            "tools/autocode_chat_cost.py",
            "tools/autocode_chat_spend.py",
            "tools/autocode_chat_verification.py",
            "tools/autocode_chat_panel.py",
            "tools/autocode_chat_tui.py",
        ),
        (
            "tests.test_chat_cost",
            "tests.test_run_metrics",
            "tests.test_chat_verification",
            "tests.test_chat_panel",
            "tests.test_chat_tui",
            "tests.test_chat_client",
            "tests.test_chat_polling_cli",
        ),
    ),
    (("tools/autocode_chat_tui.py",), ("tests.test_chat_prompt_exit_cli",)),
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
            "tools/autocode_acceptance_reference.py",
            "tools/autocode_acceptance_tests_job.py",
            "tools/units/autoreview.py",
        ),
        ("tests.test_acceptance_reference",),
    ),
    (
        ("tools/autocode_coordinate.py", "tools/autocode_chat_history.py"),
        ("tests.test_coordinate_receipt_cli",),
    ),
    (
        (
            "tools/autocode_go_test_prerequisites.py",
            "tools/autocode_acceptance_tests.py",
            "tools/autocode_acceptance_tests_job.py",
            "tools/autocode_verify.py",
        ),
        ("tests.test_go_prerequisites_execution",),
    ),
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
        ("tests.test_stage_repair_cli", "tests.test_discovery_report_edits_cli"),
    ),
    (
        ("tools/autocode_report_source.py",),
        ("tests.test_report_formatting_repair_cli",),
    ),
    (
        (
            "tools/autocode_report_correction.py",
            "tools/autocode_report_revalidation.py",
            "tools/autocode_action_recovery.py",
            "tools/autocode_report_repair_queue.py",
        ),
        ("tests.test_report_correction_resume_cli",),
    ),
    (
        ("tools/autocode_report_source.py", "tools/autocode_report_syntax.py", "tools/providers/opencode.py"),
        ("tests.test_report_syntax_cli",),
    ),
    (
        ("tools/autocode_configure.py", "tools/autocode_requirements_triage.py"),
        ("tests.test_requirements_triage",),
    ),
    (("tools/autocode_requirements_triage.py",), ("tests.test_component_design",)),
    (
        ("tools/autocode_job_retry_admission.py",),
        ("tests.test_job_route", "tests.test_needs_execution_matrix", "tests.test_status_observation"),
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
            "tools/autocode_owned_operations.py",
            "tools/autocode_job_args.py",
            "tools/autocode_job_failure.py",
            "tools/autocode_action_recovery.py",
            "tools/autocode_run_setup.py",
        ),
        ("tests.test_job_cap_change", "tests.test_job_stage_limit"),
    ),
    (
        ("tools/autocode_taskrun.py", "tools/autocode_job_args.py"),
        (
            "tests.test_job_failure_recovery",
            "tests.test_output_length_route_cli",
            "tests.test_job_stage_limit",
            "tests.test_job_route",
            "tests.test_run_finder",
        ),
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
        ("tools/autocode_no_progress_recovery.py",),
        ("tests.test_repaired_acceptance_no_progress", "tests.test_recovery_view"),
    ),
    (
        (
            "tools/autocode_validation_exhaustion_recovery.py",
            "tools/autocode_validation_scope_recovery.py",
            "tests/test_validation_scope_recovery.py",
            "tests/operational_fixtures.py",
            "tools/autocode_validation_rounds.py",
            "tools/autocode_resolver_human.py",
            "tools/autocode_action_authority.py",
            "tools/autocode_run_view.py",
        ),
        ("tests.test_validation_recovery_cli", "tests.test_validation_scope_recovery_cli"),
    ),
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
        (
            "tools/autocode_run_view.py",
            "tools/autocode_answer_options.py",
            "tools/autocode_replay_view.py",
            "tools/autocode_replay_recording.py",
        ),
        (
            "tests.test_gate_chat_cli",
            "tests.test_replay_recording",
            "tests.test_saved_run_replay_cli",
        ),
    ),
    (("tools/autocode_replay_recording.py",), ("tests.test_replay_native_capture_export_cli",)),
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
            "tools/autocode_owned_operations.py",
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
            "tools/autocode_owned_operations.py",
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
            "tools/autocode_command_supervision.py",
            "tools/autocode_runner_check.py",
            "tools/autocode_process.py",
            "tools/autocode_interventions.py",
            "tools/autocode_stop.py",
        ),
        ("tests.test_stop_now_runner_check_cli", "tests.test_runner_check_cancellation"),
    ),
    (
        (
            "tools/autocode_job_failure.py",
            "tools/autocode_job_source.py",
            "tools/autocode_run_records.py",
            "tools/autocode_stop.py",
            "tools/autocode_interventions.py",
        ),
        ("tests.test_job_person_stop", "tests.test_stop_now_job_cli"),
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
            "tools/autocode_action_authority.py",
            "tools/autocode_run_setup.py",
            "tools/autocode_args.py",
            "tools/autocode_owned_operations.py",
            "tests/test_repeated_tester_timeout_cli.py",
        ),
        ("tests.test_standalone_limit_pause_cli",),
    ),
    (
        ("tools/autocode_stage_stalls.py", "tools/autocode_stage_recovery.py"),
        ("tests.test_repeated_tester_timeout_cli",),
    ),
    (("tools/autocode_coordination_request_runtime.py",), ("tests.test_coordination_request_cli",)),
    (
        (
            "tools/autocode_coordination_gate.py",
            "tools/prompts/coordinated-plan-chat.md",
            "tools/autocode_chat_coordination.py",
            "tests/coordination_provider.py",
        ),
        ("tests.test_coordination_gate_cli",),
    ),
    (
        (
            "tools/autocode_gate_chat.py",
            "tools/prompts/gate-chat.md",
            "tools/autocode_chat_client.py",
            "tools/autocode_answer_options.py",
            "tools/autocode_replay_view.py",
        ),
        ("tests.test_gate_chat_cli",),
    ),
    (
        (
            "tools/autocode_planning_scope.py",
            "tools/prompts/fragments/autoplanner/late-repository-scope.md",
        ),
        ("tests.test_planning_scope", "tests.test_planning_scope_cli"),
    ),
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
            "tools/autocode_owned_operations.py",
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
        (
            "tests.test_chat_cli",
            "tests.test_requirements_review",
            "tests.test_requirements_job_cli",
            "tests.test_requirements_first_cli",
            "tests.test_saved_run_replay_cli",
        ),
    ),
    # A chat's Requirements names the job and the chat starts it (#1846); the test drives the CLI.
    (
        ("tools/autocode_requirements_chat.py", "tools/autocode_chat_view.py", "tools/units/autoplanner.py"),
        ("tests.test_requirements_job_cli",),
    ),
    (("tools/autocode_chat_", "tools/autocode_run_view.py", "tools/autocode_follow_up.py"), ("tests.test_chat_cli",)),
    (
        (
            "tools/autocode_chat_",
            "tools/autocode_client_actions.py",
            "tools/autocode_interventions.py",
            "tools/autocode_run_messages.py",
            "tools/prompts/run-chat.md",
        ),
        ("tests.test_chat_live_messages_cli",),
    ),
    (
        (
            "tools/autocode_message_context.py",
            "tools/autocode_stop.py",
            "tools/units/autoplanner.py",
            "tools/autocode_run_messages.py",
            "tools/autocode_interventions.py",
            "tools/autocode_plan_chat.py",
            "tools/autocode_requirements_handoff.py",
            "tools/autocode_chat_history.py",
        ),
        ("tests.test_task_message_context_cli",),
    ),
    (
        (
            "tools/autocode_coordination_budget.py",
            "tools/autocode_coordination_planning_limits.py",
            "tools/autocode_coordinate.py",
        ),
        ("tests.test_coordination_source_accounting_cli",),
    ),
    (
        (
            "tools/autocode_coordination_conversation",
            "tools/autocode_coordination_message_",
            "tools/autocode_coordination_request.py",
            "tools/autocode_coordinate.py",
            "tools/autocode_taskrun.py",
        ),
        ("tests.test_coordination_conversation_cli",),
    ),
    (
        (
            "tools/autocode_chat_task_messages.py",
            "tools/autocode_chat_messages.py",
            "tools/autocode_chat_coordination.py",
            "tools/autocode_chat_client.py",
            "tools/autocode_chat_tui.py",
            "tools/autocode_interventions.py",
            "tools/autocode_stop.py",
            "tools/autocode_coordination_conversation.py",
        ),
        ("tests.test_chat_task_messages_cli",),
    ),
    # The bridge drives the CLI as a subprocess (its tests launch the CLI, so imports cannot select them).
    (
        (
            "tools/autocode_bridge_",
            "tools/autocode_issue_admission.py",
            "tools/autocode_github.py",
            "tools/autocode_subcommands.py",
        ),
        ("tests.test_bridge_cli", "tests.test_bridge_end_to_end"),
    ),
    # `autocode changeset` drives runs as subprocesses, so imports cannot select its CLI test.
    (
        ("tools/autocode_registry.py",),
        (
            "tests.test_registry_project_cli",
            "tests.test_changeset_cli",
            "tests.test_changeset_delivery_cli",
        ),
    ),
    (
        (
            "tools/autocode_workspace_scope",
            "tools/autocode_scope_question.py",
            "tools/autocode_requirements_handoff.py",
            "tools/autocode_requirements_review.py",
        ),
        ("tests.test_scope_question_cli",),
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
    # The chat waits for the person's answer and settles a saved run's retired background pass (#3233);
    # its CLI case launches the chat, so no import reaches it from the controller or the accounting.
    (
        ("tools/autocode.py", "tools/autocode_requirements_overlap", "tools/autocode_usage"),
        ("tests.test_requirements_overlap",),
    ),
    # A run's prompts share one reader of the person's answers. The case that pipes answers past the
    # model-replacement prompt into the questions launches the CLI, so no import reaches it from them.
    (
        ("tools/autocode_requirements_overlap_input", "tools/model_catalogue.py", "tools/autocode_action_resolver.py"),
        ("tests.test_opencode",),
    ),
    (
        ("tools/autocode_build_loop", "tools/autocode_run_setup", "tools/autocode_run_actions"),
        (*PUBLIC_FLOW_CONSUMERS, "tests.test_subprocess"),
    ),
    (("tools/autocode_launch", "tools/autocode_input_preflight"), ("tests.test_launch_state_cli",)),
    (
        ("tools/autocode_action_decisions", "tools/autocode_operational_admission", "tools/autocode_run_actions"),
        ("tests.test_rejected_machinery_input",),
    ),
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
    # ENTRYPOINT_FIXTURE dynamically imports these modules in fresh source/package CLI children.
    (
        ("tools/autocode_tool_admission.py", "tools/autocode_model_preflight.py", "tools/autocode_provider_launch.py"),
        ("tests.test_tool_admission_cli",),
    ),
    # Black-box mixed transports must be admitted before an earlier role spends.
    (
        (
            "tools/autocode_build_loop.py",
            "tools/autocode_tool_admission.py",
            "tools/autocode_tool_handoff.py",
            "tools/providers/kilocode.py",
            "tools/providers/kilocode_policy.py",
        ),
        ("tests.test_kilo_tool_readiness_cli",),
    ),
    (("tools/autocode_model_preflight",), ("tests.test_provider_credentials",)),
    # The CLI reaches access transport through model_preflight, beyond the direct import selector.
    (("tools/provider_conformance_transport.py",), ("tests.test_model_preflight_budgets",)),
    # Concrete Kilo fixtures exercise early readiness before paid access probes,
    # budget/billing refusal, and selected-ELF discovery across public resumes.
    (
        (
            "tools/autocode_build_loop.py",
            "tools/autocode_tool_admission.py",
            "tools/autocode_tool_handoff.py",
            "tools/providers/kilocode.py",
            "tools/providers/kilocode_policy.py",
        ),
        ("tests.test_model_preflight_budgets",),
    ),
    (
        ("tools/autocode_access_identity", "tools/autocode_access_evidence", "tools/autocode_model_preflight"),
        ("tests.test_preflight_relocation_cli", "tests.test_model_preflight"),
    ),
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
    (
        ("tools/autocode_entry.py", "tools/autocode_source_host.py", "tools/autocode_serve_cli.py"),
        (*CORE_CONSUMERS, "tests.test_source_host", "tests.test_dev_server", "tests.test_git_environment_isolation"),
    ),
    (
        ("tools/autocode_resolve_repair_job.py", "tools/autocode_stuck_job.py"),
        ("tests.test_failure_retry", "tests.test_milestone_checkpoints"),
    ),
    (
        # The retained acceptance retry's one answer (#3927) and the surfaces that read it; the CLI test
        # reaches the build loop and the status command's progress line through the in-process CLI.
        (
            "tools/autocode_acceptance_retry_admission.py",
            "tools/autocode_acceptance_retry_next.py",
            "tools/autocode_status_observation.py",
            "tools/autocode_status_observation_worker.py",
            "tools/autocode_build_loop.py",
            "tools/autocode_status_command.py",
            "tools/autocode_progress_view.py",
        ),
        (
            "tests.test_acceptance_retry_resume_cli",
            "tests.test_acceptance_verification_retry",
            "tests.test_recovery_history",
            "tests.test_status_observation",
            "tests.test_status_updates",
            "tests.test_status_command",
            "tests.test_report_repair_view",
            "tests.test_catalog_reconciliation",
            "tests.test_source_reconciliation",
            "tests.test_visual_runtime",
        ),
    ),
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
        "tools/autocode_prompt_layout.py",
        "tools/autocode_field_budget.py",
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
    if path.startswith("tools/") and not path.startswith("tools/prompts/"):
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

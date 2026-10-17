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

# Prefixes deliberately include resource fragments, whose callers generally do
# not contain the fragment filename. These policy tests check their own handoffs.
RESOURCE_CONSUMERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tools/prompts/requirements-gather.md", ("tests.test_chat_cli", "tests.test_requirements_review")),
    ("tools/autocode_capture_bootstrap.cjs", ("tests.test_native_capture_ipc", "tests.test_native_capture")),
    ("tools/autocode_native_capture_linux.cjs", ("tests.test_native_capture_ipc", "tests.test_native_capture")),
    ("tools/autocode_kilo_image_delivery.mjs", ("tests.test_kilo_image_delivery", "tests.test_native_image")),
    ("tools/fixtures/multicomponent_fake.py", ("tests.test_multicomponent", "tests.test_local_compose_scenario")),
    ("tools/prompts/fragments/milestones/", ("tests.test_milestone_policy",)),
    ("tools/prompts/fragments/milestone-replan/", ("tests.test_milestone_policy", "tests.test_milestone_replan")),
    ("tools/prompts/fragments/autoplanner/", ("tests.test_autoplanner_review", "tests.test_planning_stages")),
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
    (
        (
            "tools/autocode_requirements_handoff.py",
            "tools/autocode_requirements_review.py",
            "tools/autocode_workflows.py",
        ),
        ("tests.test_chat_cli", "tests.test_requirements_review"),
    ),
    (("tools/autocode_chat_", "tools/autocode_run_view.py", "tools/autocode_follow_up.py"), ("tests.test_chat_cli",)),
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
    (("tools/autocode_provider_auth",), ("tests.test_provider_auth_cli",)),
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

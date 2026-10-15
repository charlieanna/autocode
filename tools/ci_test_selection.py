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
    ("tools/autocode_capture_bootstrap.cjs", ("tests.test_native_capture_ipc", "tests.test_native_capture")),
    ("tools/autocode_native_capture_linux.cjs", ("tests.test_native_capture_ipc", "tests.test_native_capture")),
    ("tools/autocode_kilo_image_delivery.mjs", ("tests.test_kilo_image_delivery", "tests.test_native_image")),
    ("tools/prompts/fragments/milestones/", ("tests.test_milestone_policy",)),
    ("tools/prompts/fragments/milestone-replan/", ("tests.test_milestone_policy", "tests.test_milestone_replan")),
    ("tools/prompts/fragments/autoplanner/", ("tests.test_autoplanner_review", "tests.test_planning_stages")),
    ("tools/prompts/fragments/adaptive-planning/", ("tests.test_adaptive_planning",)),
    ("tools/prompts/fragments/autoreview/", ("tests.test_completion_properties", "tests.test_rework_policy")),
    ("tools/prompts/fragments/workflow/", ("tests.test_workflow", "tests.test_final_workflow")),
    ("tools/prompts/fragments/provider-", ("tests.test_command_source_schema",)),
    ("tools/prompts/fragments/output-policy/", ("tests.test_autocode_output_policy",)),
    ("tools/prompts/fragments/protected-oracles/", ("tests.test_autocode_protected_oracles",)),
)

# A CLI consumer is selected even if marked slow: otherwise the import-only gap
# that previously broke master would remain. These are fake-provider consumers,
# never a request to qualify against live models.
CLI_CONSUMERS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
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
    (("tools/autocode_planning", "tools/units/autoplanner"), ("tests.test_planning_cycle_cli",)),
    (("tools/autocode_provider_auth",), ("tests.test_provider_auth_cli",)),
    (("tools/autocode_task_preflight", "tools/autocode_preflight"), ("tests.test_preflight_worker_cli",)),
    # --package's launch checks (resume, --test-root, committed files) are in autocode_run_setup.resolve.
    (("tools/autocode_package_scope", "tools/autocode_run_setup"), ("tests.test_package_scope_cli",)),
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

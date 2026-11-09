"""Emulate a historical launch for retained workflow-job compatibility tests.

Until the job's stage is named, a run behaves as an older runtime did: it may omit the standard
implementation-flow marker and routes a recognized or named job to the job's own
first stage (the Code Reviewer, Investigator, Designer or Analyst), and an explicitly
selected review skips the chat's Requirements, as every run did before all new tasks
went through the full pipeline (#3010). The real controller
creates the run; once the job's stage is named, resumes, retries and answers use the
current runtime and the run's saved stage, as an upgraded installation does with a
run it saved earlier.
No checkpoint is fabricated or edited and no production option is added.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMAND = (sys.executable, str(Path(__file__).resolve()))


def historical_first_stage(workflows):
    """The job entry a run created before #3010 recorded (autocode_workflows.first_stage then)."""

    def first_stage(state, kind, default):
        if kind == "bugfix" and (state.get("settings") or {}).get(workflows.IMPLEMENTATION_FLOW) == (
            workflows.STANDARD_IMPLEMENTATION
        ):
            return default
        return workflows.FIRST_STAGE.get(kind) or default

    return first_stage


def historical_gather_first(gather_first):
    """Before #3010 an explicitly selected review started its named job instead of Requirements."""

    def first(state):
        workflow = state.get("workflow") or {}
        if not state.get("stages") and workflow.get("kind") == "review" and workflow.get("source") == "user":
            return False
        return gather_first(state)

    return first


def install(runner, *, drop_marker=True):
    """Make the next new run launch as an older runtime did; ``drop_marker`` also omits the flow marker."""
    import autocode_requirements_chat as requirements_chat
    import autocode_workflows as workflows

    configure = runner.configure
    current = workflows.first_stage
    gather_first = requirements_chat.gather_first

    def legacy_configuration(args, state, **kwargs):
        settings = configure(args, state, **kwargs)
        if not args.run_dir and drop_marker:
            settings.pop("implementation_flow", None)
        # The older runtime ran until it named the job's stage (at launch, or once Requirements named
        # the job); every invocation after that runs the current runtime on what the run saved.
        named = args.run_dir and (state.get("workflow") or {}).get("kind")
        workflows.first_stage = current if named else historical_first_stage(workflows)
        requirements_chat.gather_first = gather_first if named else historical_gather_first(gather_first)
        return settings

    runner.configure = legacy_configuration


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "tools"))
    import autocode

    install(autocode)
    raise SystemExit(autocode.cli())

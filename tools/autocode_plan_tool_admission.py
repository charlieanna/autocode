"""Admit the plan's commands to the tool boundary before the person approves the plan (#4015).

A contained stage (a Kilo or OpenCode Builder, Tester or other code stage in the kernel tool boundary)
admits the plan's commands when it launches: ``autocode_toolchain.discover`` selects a read-only runtime
for each command's program and refuses a program it has no rule for (``uv``, ``make``, ``bundle``) or
cannot find. That launch came only after approval, so a plan that ran ``uv run pytest`` was approved and
then paused ``PAUSED_TOOL_CONTAINMENT`` before its first stage, and resuming paused again: the approved
commands could not change.

The same decision is taken here, without a model and before the plan is shown for approval: ``defect``
runs ``discover`` over each criterion's commands, as the launch takes them
(``autocode_verification_plan.commands``), and turns a refusal into a ``plan_defect`` question naming
those criteria. ``autocode_plan_defects`` then asks the Planner to correct the plan, once per round of
the person's input, and only a repeated refusal reaches the person, where the test environment is
theirs to decide. The launch still rediscovers its tools; this grants no boundary.

A run with no contained code stage (Claude Code, Codex, a configured command tool, or the person's saved
``--allow-uncontained-tools``) is not checked, and neither is a boundary that refuses even with no plan
command: that is the host's setup, not the plan's, and the launch reports it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_role_transport as role_transport
    from . import autocode_stage_roles as stage_roles
    from . import autocode_tool_admission as tool_admission
    from . import autocode_toolchain as toolchain
    from . import autocode_verification_plan as verification_plan
except ImportError:
    import autocode_role_transport as role_transport
    import autocode_stage_roles as stage_roles
    import autocode_tool_admission as tool_admission
    import autocode_toolchain as toolchain
    import autocode_verification_plan as verification_plan

QUESTION_ID = "plan-tool-boundary"
# The stages that run the plan's commands after approval; the Builder writes, the others read.
CODE_ROLES = {"builder": True, "tester": False, "acceptance_tester": True, "completion": False}
ERRORS = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)


def contained(settings) -> bool:
    """Whether a code stage of this run launches in the kernel tool boundary, by the launch's own rule."""
    settings = settings or {}
    for role, allow_write in CODE_ROLES.items():
        engine = stage_roles.engine_for(settings, role)
        if engine != "opencode":
            continue
        try:
            # None is the run's default OpenCode or Kilo tool: both launch a code stage in the boundary.
            adapter = role_transport.adapter(settings, role, None)
        except ERRORS:
            continue  # a saved transport the launch itself refuses; not this check's to report
        policy = tool_admission.shell_policy(
            engine=engine, adapter=adapter, planning=False, allow_write=allow_write, settings=settings
        )
        if policy.kernel_shell:
            return True
    return False


def refusals(workspace, body, environment=None) -> list[tuple[str, str, str]]:
    """(criterion id, command, refusal) for each criterion command the boundary does not admit."""
    environment = tool_admission.environment() if environment is None else environment
    try:
        toolchain.discover(workspace, (), environment)
    except ERRORS:
        return []  # the boundary refuses with no plan command: the host's setup, reported at launch
    found = []
    for row in (body or {}).get("acceptance_criteria") or []:
        if not isinstance(row, dict) or row.get("human_review"):
            continue
        try:
            commands = verification_plan.commands(row.get("verification_method", ""))
        except ValueError:
            continue  # the plan check owns a method it cannot read
        for command in commands:
            try:
                toolchain.discover(workspace, [command], environment)
            except toolchain.Unadmitted as error:
                found.append((str(row.get("id", "")), command, str(error)))
            except ERRORS:
                continue  # not the command's program: reported by the launch with its own reason
    return found


def question(found) -> dict:
    ids = list(dict.fromkeys(ident for ident, _command, _error in found))
    commands = list(dict.fromkeys(f"`{command}`" for _ident, command, _error in found))
    errors = list(dict.fromkeys(error for _ident, _command, error in found))
    return {
        "id": QUESTION_ID,
        "kind": "plan_defect",
        "category": "permission",
        "delegable": False,
        "convention_ref": "",
        "question": (
            f"{', '.join(ids)} {'runs' if len(ids) == 1 else 'run'} {', '.join(commands)}, which this run's "
            "tool boundary does not admit. Which command runs these checks with a program it admits?"
        ),
        "why": (
            " ".join(errors) + " AutoCode checked this before approval with the same admission the contained "
            "Builder and Tester apply at launch, so the plan as written would pause before its first stage."
        ),
        "options": [
            "Run these checks with a program the tool boundary admits",
            "Prepare the project's test environment so its tests run with an admitted program",
        ],
        "proposed_default": "Run these checks with a program the tool boundary admits",
    }


def defect(state: RunState, body):
    """The plan-defect question for criterion commands the boundary would refuse, or None."""
    if not contained(state.get("settings")):
        return None
    workspace = state.get("workspace")
    if not workspace or not Path(workspace).is_dir():
        return None
    found = refusals(workspace, body)
    return question(found) if found else None

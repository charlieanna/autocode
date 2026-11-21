"""Admit the plan's commands to the tool boundary before the person approves the plan (#4015).

A contained stage (a Kilo or OpenCode Builder, Tester or other code stage in the kernel tool boundary)
admits the plan's commands when it launches: ``autocode_toolchain.discover`` selects a read-only runtime
for each command's program (past a leading ``env`` and ``NAME=value`` assignments) and refuses a program
it has no rule for (``uv``, ``make``, ``bundle``), cannot find or cannot qualify. That launch came only
after approval, so a plan that ran ``uv run pytest`` was approved and
then paused ``PAUSED_TOOL_CONTAINMENT`` before its first stage, and resuming paused again: the approved
commands could not change.

The same decision is taken here, without a model and before the plan is shown for approval: ``defect``
runs ``discover`` over each criterion's commands, as the launch takes them
(``autocode_verification_plan.commands``), and turns a refusal into a ``plan_defect`` question naming
those criteria. ``autocode_plan_defects`` then asks the Planner to correct the plan, once per round of
the person's input, and only a repeated refusal reaches the person, where the test environment is
theirs to decide. The launch still rediscovers its tools; this grants no boundary.

A plan approved before this check existed, or one whose admission changed after approval, is reopened the
same way when the run next reaches a stage (``reopen``, from autocode_completion_result.before_review,
which both dispatch paths call before a launch): AutoCode records the same correction, the approval
returns to draft as plan feedback would return it, and the Planner corrects the plan, which the person
then approves. A second refusal in the same round leaves the launch's pause, which names the program.

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
    from . import autocode_contract_identity as contract_identity
    from . import autocode_plan_defects as plan_defects
    from . import autocode_planning_cycle as planning_cycle
    from . import autocode_role_transport as role_transport
    from . import autocode_roles as roles
    from . import autocode_run_status as run_status
    from . import autocode_stage_roles as stage_roles
    from . import autocode_tool_admission as tool_admission
    from . import autocode_toolchain as toolchain
    from . import autocode_verification_plan as verification_plan
except ImportError:
    import autocode_contract_identity as contract_identity
    import autocode_plan_defects as plan_defects
    import autocode_planning_cycle as planning_cycle
    import autocode_role_transport as role_transport
    import autocode_roles as roles
    import autocode_run_status as run_status
    import autocode_stage_roles as stage_roles
    import autocode_tool_admission as tool_admission
    import autocode_toolchain as toolchain
    import autocode_verification_plan as verification_plan

QUESTION_ID = "plan-tool-boundary"
# The stages that run the plan's commands after approval; the Builder writes, the others read.
CODE_ROLES = {"builder": True, "tester": False, "acceptance_tester": True, "completion": False}
ERRORS = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)
# Discovery reads each runtime's loader files (seconds on macOS) and ``reopen`` asks before every launch: one
# answer per workspace, commands and PATH for this process. The launch itself still discovers afresh.
_ANSWERS: dict[tuple, list[tuple[str, str, str]]] = {}


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
    rows: list[tuple[str, str]] = []
    for row in (body or {}).get("acceptance_criteria") or []:
        if not isinstance(row, dict) or row.get("human_review"):
            continue
        try:
            rows.extend(
                (str(row.get("id", "")), command)
                for command in verification_plan.commands(row.get("verification_method", ""))
            )
        except ValueError:
            continue  # the plan check owns a method it cannot read
    key = (str(workspace), tuple(rows), environment.get("PATH", ""))
    if key not in _ANSWERS:
        _ANSWERS[key] = _refused(workspace, rows, environment)
    return list(_ANSWERS[key])


def _refused(workspace, rows, environment):
    try:
        # One discovery of every command, as the launch makes it; only a refusal needs each command alone.
        toolchain.discover(workspace, [command for _ident, command in rows], environment)
        return []
    except ERRORS:
        pass
    try:
        toolchain.discover(workspace, (), environment)
    except ERRORS:
        return []  # the boundary refuses with no plan command: the host's setup, reported at launch
    # The boundary admits no command at all, so a command it refuses alone is refused for its own program,
    # whichever rule refuses it: an unsupported or missing program, or one it cannot qualify (a pytest
    # launcher not paired with its interpreter). Observed, not listed.
    found = []
    for ident, command in rows:
        try:
            toolchain.discover(workspace, [command], environment)
        except ERRORS as error:
            found.append((ident, command, str(error)))
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


def reopen(state: RunState) -> bool:
    """Return an approved plan whose commands the boundary refuses to the Planner; True when it was reopened.

    False, changing nothing, when the plan is not approved, the run has no contained code stage, every
    command is admitted, or AutoCode already asked the Planner to correct a plan this round: the launch
    then pauses, naming the program (autocode_tool_admission.failure).
    """
    contract = state.get("goal_contract") or {}
    body = contract.get("body") or {}
    if contract.get("approval_status") != "approved" or not body:
        return False
    found = defect(state, body)
    if found is None or plan_defects.named_parts(found, body) == []:
        return False
    if plan_defects.settle(state, [found], body, contract_identity.token(contract)):
        return False  # not settled this round: the launch's own pause stands
    try:
        from . import autocode_goals as goals
    except ImportError:
        import autocode_goals as goals
    contract.update(approval_status="draft", approval_event=None)
    goals.invalidate(state, "The approved plan runs a program the tool boundary does not admit (#4015)")
    planning_cycle.internal_answer_restart(state)
    run_status.running(
        state,
        "DISCOVERING",
        next_stage="requirements" if roles.planning_flow(state) == "v2" else "draft_plan",
        pending_questions=[],
    )
    return True

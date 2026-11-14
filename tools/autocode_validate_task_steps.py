"""Run a validate task's new steps where their author can still change them (a real run, 2026-11-14).

A Completion Reviewer or Resolver that finds a criterion unverified hands the Tester a validate task, with
a plan of its own. A step of that plan that is a command must exit 0 in the runner's clean replay of the
Tester's report (autocode_check_replay), and a Tester cannot change it. In a real run the Completion
Reviewer wrote such a step itself, ``assert 'gzip' not in s`` over a test file whose approved test name
contains "gzip". The code was right and the Tester's own check passed, yet its report was refused for that
step, the Investigator found the step at fault, and the person was asked to have its author replace it.

A validate task changes no code, so a step that fails on the source its author reviewed fails the same way
in the Tester's replay. The runner therefore runs each new command step once, in the same clean copy, when
the task is written, and refuses the author's report with the output: the author corrects the step in a
report repair, or returns a task that fixes the code when the step shows a defect. It runs only what is new:

- not a command the approved plan already runs (a criterion's verification method or the initial task's
  plan), whose failure has its own routes (autocode_planned_check_rework);
- not a command the last accepted validation's replay passed on this same source.

A failure the original code has identically is the base's, as in the replay, and is not refused. Nothing
runs for an implement task (its steps may fail until the Builder works), in a replay of recorded checks,
under a native check owner or in progressive verification, whose replays have their own rules.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_check_replay as check_replay
    from . import autocode_launch_inputs as launch_inputs
    from . import autocode_progressive_state as progressive_state
    from . import autocode_regression as regression
    from . import autocode_replay_checks as recorded_checks
    from . import autocode_verification_plan as verification_plan
    from . import autocode_verify as verify
except ImportError:
    import autocode_check_replay as check_replay
    import autocode_launch_inputs as launch_inputs
    import autocode_progressive_state as progressive_state
    import autocode_regression as regression
    import autocode_replay_checks as recorded_checks
    import autocode_verification_plan as verification_plan
    import autocode_verify as verify


def refuse(state: RunState, task, record, workspace, run_dir, runtime, continuation=False) -> None:
    """Refuse the task a Completion Reviewer or Resolver has just written when its plan cannot be followed.

    Raises ValueError, which sends the report back to its author: for a row that names git status or plans a
    command the shell cannot parse (verification_plan.refuse_new_plan), and for a validate task's new step
    that fails now (``check``). The steps are not run for a Resolver task applied again after a diagnosis
    (``continuation``), under a native check owner or in progressive verification.
    """
    verification_plan.refuse_new_plan(*verification_plan.task_rows(task, "next_task"))
    if continuation or getattr(runtime, "native_calls", None) is not None or progressive_state.enabled(state):
        return
    check(
        state,
        task,
        record,
        workspace,
        run_dir,
        launch_inputs.runners(state, workspace, run_dir, verify.scratch_run)[0],
        base_run=lambda: regression.clean_base_run(state, workspace, run_dir),
    )


def _approved(state: RunState) -> set[str]:
    """The commands the approved plan runs: every criterion's method and the initial task's plan."""
    body = (state.get("goal_contract") or {}).get("body") or {}
    methods = [row.get("verification_method", "") for row in body.get("acceptance_criteria") or []]
    methods += list((body.get("initial_task") or {}).get("validation_plan") or [])
    return {command for method in methods for command in _commands(method)}


def _commands(method) -> list[str]:
    try:
        return verification_plan.commands(method)
    except ValueError:  # a malformed exit declaration is refused where commands are checked, not here
        return []


def _passed(state: RunState, revision) -> set[str]:
    """The commands the last accepted validation's clean replay passed on the source at ``revision``."""
    validation = state.get("validation") or {}
    if not revision or validation.get("source_revision") != revision:
        return set()
    rows = (validation.get("check_replay") or {}).get("checks") or []
    return {
        row["command"] for row in rows if isinstance(row, dict) and row.get("command") and row.get("exit_code") == 0
    }


def new_steps(state: RunState, task, revision) -> list[tuple[str, str]]:
    """The (step, command) pairs of validate ``task`` that nothing has run on the source at ``revision``."""
    if not isinstance(task, dict) or task.get("kind") != "validate":
        return []
    known = _approved(state) | _passed(state, revision)
    found: dict[str, str] = {}
    for step in task.get("validation_plan") or []:
        for command in _commands(step):
            if command not in known:
                found.setdefault(command, str(step))
    return [(step, command) for command, step in found.items()]


def check(state: RunState, task, record, workspace, run_dir, scratch_run, *, base_run=None) -> None:
    """Raise ValueError for the first new step of validate ``task`` that fails in the runner's clean copy.

    ``scratch_run`` is the clean runner of the Tester's replay (autocode_launch_inputs.runners over
    autocode_verify.scratch_run). ``base_run`` is called only when a step is to be run, and returns that
    replay's base runner (autocode_regression.clean_base_run) or None.
    """
    if recorded_checks.simulated(state.get("settings")):
        return
    steps = new_steps(state, task, record.get("source_revision"))
    if not steps:
        return
    out = Path(run_dir) / "check-replay" / f"task-steps-{uuid.uuid4().hex}"
    out.mkdir(parents=True, exist_ok=False)
    on_base = base_run() if base_run is not None else None
    for number, (step, command) in enumerate(steps, 1):
        failure = check_replay.probe(
            command, workspace, out / f"step-{number:02d}", scratch_run, state=state, base_run=on_base
        )
        if failure:
            raise ValueError(
                f"next_task.validation_plan `{step}` "
                + ("" if step.strip() == command else f"plans the command `{command}`, which ")
                + f"{failure['what']} when the runner ran it from the repository root in a clean copy of the "
                "current source"
                + (f"; its output ended: {failure['tail'].strip()[-300:]}" if failure["tail"].strip() else "")
                + ". A validate task changes no code, so the Tester's report would be refused for this step, and "
                "a Tester cannot change it. If the step is wrong, reword it so that it passes on correct code "
                "and still shows what it is for; if it shows a defect, return a task that fixes the code and "
                "cite this output. " + verification_plan.EXPECTED_FAILURE_RULE
            )

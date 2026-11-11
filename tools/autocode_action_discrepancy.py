"""``--report-discrepancy TEXT --review-token TOKEN``: report built code that departs from the approved plan (#3045).

A step of autocode_run_actions.handle. It records the report through autocode_implementation_discrepancy
and saves it without launching anything: the plan stays approved, the pause the person requested stays
held, and the Tester reads the report when the person resumes. A refusal leaves the run unchanged.

Like every step of autocode_run_actions.handle, it takes the invocation's arguments and ``decided``,
returns an exit code to stop or None to go on, and calls the runner's own functions as runner.X.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_implementation_discrepancy as discrepancy
    from . import autocode_source_scope as source_scope
    from . import autocode_support as support
except ImportError:
    import autocode_implementation_discrepancy as discrepancy
    import autocode_source_scope as source_scope
    import autocode_support as support


def apply(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Save the report, or refuse it; None when the flag is absent."""
    text = getattr(args, "report_discrepancy", None)
    if text is None:
        return None
    candidate = copy.deepcopy(state)
    try:
        if runner.interventions.pending(run_dir):
            raise ValueError("Apply the queued intervention before reporting a discrepancy")
        problem = discrepancy.refusal(candidate)
        if problem:
            raise ValueError(problem)
        current = source_scope.snapshot(workspace, candidate, base_snapshot=support.snapshot)
        report, added = discrepancy.record(
            candidate, text, args.review_token, source_revision=current["revision"], at=runner.now()
        )
        if added:
            runner.commit_user_action(state, candidate, run_dir)
    except (ValueError, KeyError) as error:
        return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
    print(
        ("Saved" if added else "Already saved")
        + f" discrepancy {report['id']} for task {report['task_id']}; the plan stays approved and the run stays "
        "paused. When you resume, the Tester checks it against the approved plan. No agent launched."
    )
    return 0

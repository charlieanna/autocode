"""A follow-up asking to put the finished work up for review: no plan, no stage, a pull request (owner request 2026-11-28).

After a run completed a task, "create a PR" is a follow-up message, and Requirements is the one stage that
reads free text. Before, it could only name build, bugfix, review, design or discuss, so the request was
either planned as a build (a Planner, Builder and reviews spent on nothing to build) or answered with a reply
that could not push, since no stage may (autocode_publish_guard). Requirements now names the job ``publish``
for it. The runner then ends the turn as a discussion Requirements answered (nothing is planned or built,
autocode_requirements_answer), records the request here, and opens the pull request itself once the turn is
saved (``autocode_pull_request.deliver_requested``): the same step, with the same checks, as a client's
"Open pull request" button. The person's message is the go-ahead; nothing is merged.

Pure over the state dict; imports only autocode_requirements_answer and autocode_workflows.

State key written here:
    pull_request_request: {"event_id": the follow-up's brief_feedback id, "at": when the turn ended,
                           "outcome": {"kind": "opened", "url", "branch"} or {"kind": "refused", "reason"},
                           written by autocode_pull_request.deliver_requested once it ran,
                           "finished_at": when it ran}
        Present only after a publish turn. Read by deliver_requested (absent outcome: not yet run), by the
        chat's turn result (autocode_chat_history.response, via ``outcome_text``) and by people reading state.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_requirements_answer as requirements_answer
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_requirements_answer as requirements_answer
    import autocode_workflows as workflows

JOB = "publish"
KEY = "pull_request_request"


def named(report) -> bool:
    """Whether Requirements read the request as putting finished work up for review, with nothing to build."""
    return (
        isinstance(report, dict)
        and report.get("job") == JOB
        and not report.get("required_behaviors")
        and not report.get("acceptance_tests")
    )


def finish(state: RunState, report: dict, record: dict) -> None:
    """End the turn on Requirements' reply and, on a follow-up, record the pull request it asked for.

    The turn is labelled a discussion: it changed nothing and built nothing. The first message of a run has
    no finished work to propose, so it is only answered; ``autocode pr`` itself refuses a run with no
    branch of its own or nothing on it."""
    workflows.from_requirements(state, {**report, "job": "discuss"}, record)
    requirements_answer.answer_from_requirements(state, report, record)
    turns = state.get("turns") or []
    if turns:
        state[KEY] = {"event_id": turns[-1].get("event_id"), "at": dt.datetime.now(dt.UTC).isoformat()}


def outcome_text(state: RunState) -> str:
    """What the publish turn did, for the chat; '' when the turn asked for no pull request or it has not run."""
    wanted = state.get(KEY)
    outcome = wanted.get("outcome") if isinstance(wanted, dict) else None
    if not isinstance(outcome, dict):
        return ""
    if outcome.get("kind") == "opened":
        return f"Opened pull request {outcome.get('url')} for branch {outcome.get('branch')}."
    return f"No pull request was opened: {outcome.get('reason')}"

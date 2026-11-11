"""A person's report that the built code departs from the approved plan, kept without revising the plan (#3045).

At a clean requested pause during execution, ``--feedback`` is the only way to say something, and it
starts a new requirements episode: the approved plan becomes a draft that needs a fresh review and
approval, even when the person means only "the code does not do what the plan already says". This
module is the narrower input for that case. ``--report-discrepancy TEXT --review-token TOKEN`` records
the report bound to the approved plan token the person was shown, the current task and the source
revision, and changes nothing else: the plan stays approved, the pause stays held, and no finding,
acceptance, completion, limit or retry is granted.

The report reaches the independent reviewers instead: the Tester (or the reviewer acting as Tester) of
the same task and the Completion Reviewer of the same plan read it as an allegation to check against the
approved plan (``context``). A reviewer that confirms it reports a finding, and the existing review loop
sends the Builder back within the approved plan; a reviewer that does not leaves it unacted on. A change to
the plan itself still takes ``--feedback`` and a fresh approval.

State: ``implementation_discrepancies``, written only by ``record`` and read here. Domain layer: it reads
saved state and imports no runner, controller or interface module.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as identity
    from . import autocode_pause_authority as pause_authority
    from . import autocode_prompts as prompts
    from . import autocode_util as util
except ImportError:
    import autocode_contract_identity as identity
    import autocode_pause_authority as pause_authority
    import autocode_prompts as prompts
    import autocode_util as util

KEY = "implementation_discrepancies"
FLAG = "--report-discrepancy"
MAX_TEXT = 4000
# Reports open for one task at a time, so a reviewer's context stays bounded.
MAX_OPEN = 5
TASK_STAGES = ("tester", "review_checkpoint")
PLAN_STAGES = ("review_completion",)
LABEL = "Report a discrepancy"
EFFECT = (
    "Tell the reviewers where the code departs from the approved plan. The plan stays approved and the run "
    "stays paused; the Tester checks your report when the run resumes."
)


def _task_id(state: RunState):
    task = state.get("current_task")
    return task.get("id") if isinstance(task, dict) and isinstance(task.get("id"), str) and task["id"] else None


def refusal(state: RunState):
    """Why a discrepancy may not be reported on ``state`` now; None when it may."""
    if _sealed_token(state) is None or not identity.approved(state):
        return (
            "A discrepancy is reported against an approved plan; this run has none. Use --feedback to change the plan"
        )
    if not _task_id(state):
        return "A discrepancy is reported once the Builder has a task from the approved plan"
    if not pause_authority.requested_feedback(state):
        return (
            "A discrepancy is reported at a pause you requested with nothing else waiting; "
            f"this run is {state.get('status')}. Request a pause, or answer what the run is waiting for first"
        )
    return None


def _open(state: RunState, *, contract_token, task_id=None):
    rows = state.get(KEY)
    return [
        row
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict)
        and row.get("contract_token") == contract_token
        and (task_id is None or row.get("task_id") == task_id)
    ]


def record(state: RunState, text, review_token, *, source_revision, at=None):
    """Save one report bound to the displayed approved plan; return (report, added).

    The same text for the same plan and task is kept once: sending it again returns the saved report and
    ``added`` False. Nothing else in ``state`` changes. Raises ValueError when the run is not at an eligible
    pause, the token is not the approved plan's, or the text is empty or too long.
    """
    problem = refusal(state)
    if problem:
        raise ValueError(problem)
    contract = cast(dict, state["goal_contract"])
    current = identity.token(contract)
    if review_token != current:
        raise ValueError(
            "The discrepancy names another plan revision; show the plan again and pass its token with --review-token"
        )
    body = text.strip() if isinstance(text, str) else ""
    if not body:
        raise ValueError(f"{FLAG} needs nonempty text")
    if len(body) > MAX_TEXT:
        raise ValueError(f"{FLAG} takes at most {MAX_TEXT} characters; report one discrepancy at a time")
    task_id = _task_id(state)
    identifier = "discrepancy-" + util.digest({"contract_token": current, "task_id": task_id, "text": body})[:12]
    earlier = _open(state, contract_token=current, task_id=task_id)
    for row in earlier:
        if row.get("id") == identifier:
            return row, False
    if len(earlier) >= MAX_OPEN:
        raise ValueError(
            f"This task already has {MAX_OPEN} discrepancy reports waiting for review; resume so the Tester checks them"
        )
    report = {
        "id": identifier,
        "kind": "implementation_discrepancy",
        "actor": "user_cli",
        "at": at or util.now(),
        "text": body,
        "contract_token": current,
        "contract_revision": contract.get("revision"),
        "task_id": task_id,
        "milestone_id": cast(dict, state.get("current_task") or {}).get("milestone_id"),
        "source_revision": source_revision,
        "pause_status": state.get("status"),
        "next_stage": state.get("next_stage"),
    }
    cast(dict, state).setdefault(KEY, []).append(report)
    return report, True


def _sealed_token(state: RunState):
    """The saved plan's token, or None when there is no intact saved plan to bind a report to."""
    contract = state.get("goal_contract")
    try:
        return identity.token(contract) if isinstance(contract, dict) and identity.sealed(contract) else None
    except (KeyError, TypeError):
        return None


def current(state: RunState, stage):
    """The reports ``stage`` must check: the current task's for its Tester, the whole plan's for completion."""
    if stage not in TASK_STAGES + PLAN_STAGES or not state.get(KEY):
        return []
    token = _sealed_token(state)
    if token is None:
        return []
    if stage in TASK_STAGES:
        task_id = _task_id(state)
        return _open(state, contract_token=token, task_id=task_id) if task_id else []
    return _open(state, contract_token=token)


def context(state: RunState, stage):
    """The reviewer context packet's entry, or None when ``stage`` has nothing to check."""
    reports = current(state, stage)
    if not reports:
        return None
    return {
        "instruction": prompts.get("fragments/stage-context/implementation-discrepancy.md").strip(),
        "reports": [
            {key: deepcopy(row.get(key)) for key in ("id", "text", "task_id", "milestone_id", "source_revision", "at")}
            for row in reports
        ],
    }


def project(state: RunState, *, run_dir=None, workspace=None):
    """The status view's ``implementation_discrepancy``: the offer and the reports, or None when neither applies.

    ``offer.next_command`` is the argument array a client runs with ``MESSAGE`` replaced by the person's text.
    ``reports`` lists every report with ``current`` true while a reviewer of this plan still reads it.
    """
    rows = state.get(KEY)
    rows = [row for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)]
    offer = None
    if run_dir is not None and workspace is not None and refusal(state) is None:
        offer = {
            "label": LABEL,
            "effect": EFFECT,
            "next_command": [
                "--workspace",
                str(workspace),
                "--run-dir",
                str(run_dir),
                "--review-token",
                identity.token(cast(dict, state["goal_contract"])),
                FLAG,
                "MESSAGE",
            ],
        }
    if offer is None and not rows:
        return None
    token = _sealed_token(state)
    return {
        "offer": offer,
        "reports": [
            {
                **{
                    key: deepcopy(row.get(key))
                    for key in ("id", "text", "at", "contract_token", "task_id", "milestone_id", "source_revision")
                },
                "current": token is not None and row.get("contract_token") == token,
            }
            for row in rows
        ],
    }

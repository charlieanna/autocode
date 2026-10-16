"""Which plan file a run's plan was loaded from (#720).

`autocode --goal-file PATH` names the file in the record of the revision it installs: its path and goal hash
under ``plan_file`` (autocode_goal_file.record). ``loaded`` finds the latest revision loaded from a file since
the last approved one. The approval stop describes the plan waiting for approval against it
(autocode_goal_file.approval_lines); the status view's ``approved_contract`` and the completed run's evidence
report cite it (``citation``).

Pure: imports only the modules that name the runner's records and the plan file format.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_brief_obligations as brief_obligations
    from . import autocode_plan_file as plan_file
    from . import autocode_risk_obligations as risk_obligations
    from . import autocode_source_trace as source_trace
except ImportError:
    import autocode_brief_obligations as brief_obligations
    import autocode_plan_file as plan_file
    import autocode_risk_obligations as risk_obligations
    import autocode_source_trace as source_trace

FIELD = "plan_file"
# The runner's records in a contract body. Each one's ``review`` is the evidence of the Plan Reviewer
# execution that sealed it, so a review of an unchanged plan seals it anew and changes only that.
RUNNER_RECORDS = (brief_obligations.KEY, risk_obligations.KEY, source_trace.KEY)


def loaded(state: RunState) -> dict | None:
    """The latest revision loaded from a plan file since the last approved one, or None.

    Any revision before the current one that was approved ends the search, the loaded revision included:
    a plan drafted after an approval is described against that approval, not the file. A revision counts
    as approved when it still carries its approval or when a saved goal_approval event names its token,
    since feedback, an answer or a rejected assumption resets an approved revision to draft in place.
    """
    current = state.get("goal_contract") or {}
    approvals = {
        event.get("token")
        for event in state.get("user_events") or []
        if isinstance(event, dict) and event.get("kind") == "goal_approval"
    }
    for contract in reversed([*(state.get("contract_history") or []), current]):
        if not isinstance(contract, dict):
            continue
        if contract is not current and (
            contract.get("approval_status") == "approved"
            or contract.get("approval_event")
            or f"r{contract.get('revision')}:{contract.get('hash')}" in approvals
        ):
            return None
        if isinstance(contract.get(FIELD), dict):
            return contract
    return None


def changed(before: dict, after: dict) -> list[str]:
    """The parts of the plan ``after`` that differ from ``before``, apart from the Plan Reviewer's evidence."""

    def plan(body):
        return {
            key: {name: item for name, item in value.items() if name != "review"}
            if key in RUNNER_RECORDS and isinstance(value, dict)
            else value
            for key, value in body.items()
        }

    before, after = plan(before), plan(after)
    return sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))


def citation(state: RunState) -> dict | None:
    """The plan file the current plan was loaded from, as the status view and the evidence report cite it.

    ``path`` and ``goal_hash`` are the file's, as recorded when it was loaded; ``revision`` is the plan
    revision it was loaded as; ``changed`` names the parts of the current plan that differ from that
    revision's, apart from the Plan Reviewer's evidence (``changed()``). None when the plan was not loaded
    from a file since the last approval before it, and for a record that is not one ``--goal-file`` writes:
    a malformed record is not cited, and it never takes the approved plan out of the status view.
    """
    try:
        source = loaded(state)
        if source is None:
            return None
        named, body = source[FIELD], (state.get("goal_contract") or {}).get("body")
        path, goal, revision = named.get("path"), named.get("goal_hash"), source.get("revision")
        if not (
            isinstance(path, str)
            and path
            and isinstance(goal, str)
            and plan_file.HASH.fullmatch(goal)
            and type(revision) is int
            and isinstance(source.get("body"), dict)
            and isinstance(body, dict)
        ):
            return None
        return {"path": path, "goal_hash": goal, "revision": revision, "changed": changed(source["body"], body)}
    except (KeyError, TypeError, AttributeError, ValueError):
        return None

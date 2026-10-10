"""Retain approved initial checks while the same criteria undergo rework."""


def initial_validation(state):
    """Return declarations from the current contract, never historical tasks.

    A rework task can describe an existing check in prose. That does not revoke
    the executable declaration sealed in the approved initial task. An unrelated
    criterion slice must not inherit that task's commands.
    """
    body = (state.get("goal_contract") or {}).get("body") or {}
    initial = body.get("initial_task") or {}
    current = state.get("current_task") or {}
    selected = set(current.get("acceptance_criteria") or [])
    if selected and not selected.intersection(initial.get("acceptance_criteria") or []):
        return []
    return list(initial.get("validation_plan") or [])

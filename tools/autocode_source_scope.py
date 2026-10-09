"""Source selection from the sealed, explicitly approved run contract.

All milestones stay in scope, including completed ones: changing a previously
accepted ignored deliverable must invalidate later completion and recovery.
No Builder report, current-task suggestion, or ambient workspace file grants
additional source scope.
"""

try:
    from . import autocode_contract_identity as contracts
    from . import autocode_source_snapshot as source
except ImportError:
    import autocode_contract_identity as contracts
    import autocode_source_snapshot as source


def paths(state):
    try:
        authorized = contracts.approved(state)
    except (KeyError, TypeError, AttributeError):
        authorized = False
    if not authorized:
        return []
    body = state["goal_contract"]["body"]
    rows = [*body.get("milestones", []), body.get("initial_task") or {}]
    selected = [path for row in rows for path in row.get("affected_paths", [])]
    return source.literal_paths(selected)


def snapshot(workspace, state, *, base_snapshot=None):
    return source.snapshot(workspace, paths=paths(state), base_snapshot=base_snapshot)

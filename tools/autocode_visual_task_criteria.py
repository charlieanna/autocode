"""Pure read compatibility for canonical visual task acceptance criteria.

Reading an alias grants no approval, task, source, producer or image authority.
The original task mapping stays unchanged for exact retained binding checks.
"""


def ids(task):
    """Return detached ordered CIDs; reject malformed or conflicting fields.

    acceptance_criteria is canonical. criterion_ids is a read-only legacy alias
    only when canonical is absent. If both exist they must match exactly; a
    present empty/null/malformed canonical value never falls back to the alias.
    """
    if not isinstance(task, dict):
        raise ValueError("Visual task must be a mapping")
    keys = [key for key in ("acceptance_criteria", "criterion_ids") if key in task]
    if not keys:
        raise ValueError("Visual task has no acceptance criteria")
    for key in keys:
        value = task[key]
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(cid, str) or not cid.strip() for cid in value)
            or len(value) != len(set(value))
        ):
            raise ValueError("Visual task has malformed " + key)
    if len(keys) == 2 and task[keys[0]] != task[keys[1]]:
        raise ValueError("Visual task acceptance criteria aliases disagree")
    return list(task[keys[0]])

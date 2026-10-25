"""Fit a role's effort to the reasoning variants its model declares (#2006).

``--single-model`` pins one model for every role, and a route answer at a model stop moves one role
to another model; either way the role keeps the effort its profile gave it, while a model serves only
the variants its tool declares (GLM 5.3 declares low, high and max, not medium). The next launch
then refused, and roles such as the Analyst have no effort flag a person could pin. Here an effort the
runner chose moves to the nearest variant the model declares and the move is recorded; an effort a
person named explicitly is never moved: the run refuses and names the flag to change.

Pure functions. Imports nothing from AutoCode.
"""

from __future__ import annotations

SCALE = ("low", "medium", "high", "xhigh", "max")
# Roles with a --ROLE-reasoning-effort flag of their own (autocode_args); the others take --reasoning-effort.
FLAGGED_ROLES = frozenset({"astra", "terra", "sol", "completion", "glm", "requirements", "resolver", "plan_reviewer"})


def nearest(effort: str, declared) -> str | None:
    """The variant among ``declared`` nearest to ``effort``: itself, else the next stronger one, else the
    strongest weaker one. None when nothing is declared (unknown: the launch check decides)."""
    declared = [level for level in declared or () if level in SCALE]
    if not declared:
        return None
    if effort in declared:
        return effort
    if effort not in SCALE:
        return declared[0]
    rank = SCALE.index(effort)
    stronger = [level for level in SCALE[rank + 1 :] if level in declared]
    weaker = [level for level in reversed(SCALE[:rank]) if level in declared]
    return (stronger or weaker)[0]


def flag(role: str) -> str:
    """The effort flag that names ``role``'s variant: its own, or --reasoning-effort for a role without one."""
    return f"--{role.replace('_', '-')}-reasoning-effort" if role in FLAGGED_ROLES else "--reasoning-effort"


def reconcile(roles: dict, model: str, declared, *, explicit=()) -> dict:
    """Move every runner-chosen effort the model cannot serve to its nearest declared variant, in place.

    Returns ``{role: {"from": old, "to": new}}`` for the roles moved. A role in ``explicit`` (a person
    named its effort, per role or for every role) that the model cannot serve raises ValueError naming
    the flag and the declared variants instead of being moved. Nothing declared: nothing moves.
    """
    declared = [level for level in declared or () if level in SCALE]
    if not declared:
        return {}
    moved: dict[str, dict] = {}
    for role, route in roles.items():
        effort = route.get("reasoning_effort")
        if not effort or effort in declared:
            continue
        if role in explicit:
            raise ValueError(
                f"{model} does not declare reasoning variant {effort!r} for {flag(role)}; it declares "
                f"{', '.join(declared)}. Name one of those with {flag(role)} (or --reasoning-effort for every role)"
            )
        target = nearest(effort, declared)
        route["reasoning_effort"] = target
        moved[role] = {"from": effort, "to": target}
    return moved


def notice(moved: dict, model: str, declared) -> str | None:
    """One line saying which roles' efforts moved and why; None when none did."""
    if not moved:
        return None
    changes = ", ".join(f"{role} {row['from']} -> {row['to']}" for role, row in sorted(moved.items()))
    levels = ", ".join(level for level in declared if level in SCALE)
    return (
        f"--single-model {model} declares the reasoning variants {levels}: moved {changes}. "
        "Pin one yourself with the role's --ROLE-reasoning-effort flag."
    )


DECLARED = "declared_variants"  # settings key: {model: [levels]} a new run read from the tool's listing (#2020)


def declared_for(settings: dict, model: str) -> list | None:
    """The variants a run recorded for ``model``: the listing a ``--single-model`` run saved (``DECLARED``), else
    the one it read when it chose its routes (``route_selection.candidates``), whichever role listed it; None
    when the run holds no listing for it."""
    recorded = ((settings or {}).get(DECLARED) or {}).get(model)
    if isinstance(recorded, list):
        return list(recorded)
    candidates = ((settings or {}).get("route_selection") or {}).get("candidates") or {}
    for rows in candidates.values():
        for row in rows or ():
            if isinstance(row, dict) and row.get("model") == model and isinstance(row.get("levels"), list):
                return list(row["levels"])
    return None

"""Fit a role's effort to the reasoning variants its model declares (#2006, #2057).

A model serves only the variants its tool declares (GLM 5.3 declares low, high and max, not
medium), while AutoCode derives efforts in several places: a single model for every role, a
route answer at a model stop, the Plan Reviewer's reduced tier, the Builder's light first
attempt, an escalation rung. Each site used to fit the effort itself, or not at all, so a new
derived tier was a new place to forget. This module holds the one rule they share: an effort the
runner chose moves to the nearest declared variant and the move is recorded; an effort a person
named is never moved and refuses with the flag and the declared variants; with no listing the
launch records the effort as unverified and the tool's own check decides.

``launch`` is the one runtime entry point: every stage launch calls it with the effort it is
about to pass, so a climb, a reduced tier, a route answer and a single-model run all fit at the
same boundary.

Pure functions over saved settings. Imports ``autocode_util`` and nothing higher.
"""

from __future__ import annotations

from collections import namedtuple

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

SCALE = ("low", "medium", "high", "xhigh", "max")
# Roles with a --ROLE-reasoning-effort flag of their own (autocode_args); the others take --reasoning-effort.
FLAGGED_ROLES = frozenset({"astra", "terra", "sol", "completion", "glm", "requirements", "resolver", "plan_reviewer"})
DECLARED = "declared_variants"  # settings key: {model: [levels]} a run read from the tool's listing
# settings key: {model: [levels]} a preflight probe passed at. Proof that each level works, never a
# listing: a level no probe tried is still unknown, so it neither moves nor pauses (#2057).
CERTIFIED = "certified_variants"
# Derived evidence about effort, never a route choice: a settings-identity digest leaves it out
# so a launch-time fit does not look like a person changing what a held recovery acts on (#2057).
DERIVED = ("effort_fits", DECLARED, CERTIFIED)
USER_EXPLICIT = "user_explicit"  # settings.effort_origins value: a person named the effort
# A person-named effort the model does not declare: a pause with a named answer (autocode_effort_question).
STATUS = "PAUSED_EFFORT_UNSUPPORTED"

# reason: declared (the model serves it), fitted (moved to the nearest declared variant),
# unverified (no listing; the launch check decides).
Fit = namedtuple("Fit", "model effort from_effort reason explicit")


class EffortUnsupported(util.Paused, ValueError):
    """A person-named effort the model does not declare: said once, with the flag that changes it."""

    def __init__(self, model, effort, declared, role):
        self.model, self.effort, self.declared, self.role = model, effort, list(declared), role
        super().__init__(
            STATUS,
            f"{model} does not declare reasoning variant {effort!r} for {flag(role)}; it declares "
            f"{', '.join(declared)}. Name one of those with {flag(role)} (or --reasoning-effort for every role)",
        )


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


def declared_for(settings: dict, model: str) -> list | None:
    """The variants the tool lists for ``model``: the listing a new run saved (``DECLARED``) and the one
    it read when it chose its routes (``route_selection.candidates``). Both listings are evidence about
    one model, so they are merged rather than one masking the other; None when the run holds no listing
    for it. A level a probe passed is ``certified_for``, not a listing."""
    settings = settings or {}
    recorded = (settings.get(DECLARED) or {}).get(model)
    levels = [level for level in recorded if level in SCALE] if isinstance(recorded, list) else []
    candidates = (settings.get("route_selection") or {}).get("candidates") or {}
    for rows in candidates.values():
        for row in rows or ():
            if isinstance(row, dict) and row.get("model") == model and isinstance(row.get("levels"), list):
                levels.extend(level for level in row["levels"] if level in SCALE)
    if not levels:
        return list(recorded) if isinstance(recorded, list) else None
    return [level for level in SCALE if level in set(levels)]


def certified_for(settings: dict, model: str) -> list:
    """The levels a preflight probe passed for ``model`` (#2057): each one proven, the rest unknown."""
    recorded = ((settings or {}).get(CERTIFIED) or {}).get(model)
    return [level for level in recorded if level in SCALE] if isinstance(recorded, list) else []


def person_named(settings: dict, role: str) -> bool:
    """Whether a person named ``role``'s effort (autocode_role_route records only its own or the runner's)."""
    return ((settings or {}).get("effort_origins") or {}).get(role) == USER_EXPLICIT


def fit(settings: dict, role: str, *, effort=None, model=None, declared=None) -> Fit:
    """Fit ``effort`` (default: the saved route's) to the variants ``role``'s model declares.

    ``effort`` is the level the caller is about to pass, which may be a tier derived from the
    saved one (the Builder's light attempt, the Plan Reviewer's reduced depth). Only the saved
    level can be a person's; a tier the runner derived always moves. ``model`` overrides the
    saved route's model when the launch uses another (a reviewer fallback). Raises
    EffortUnsupported when a person's level is not declared.
    """
    route = ((settings or {}).get("roles") or {}).get(role) or {}
    model = model or route.get("model")
    saved = route.get("reasoning_effort")
    requested = effort if effort is not None else saved
    levels = declared if declared is not None else declared_for(settings, model)
    levels = [level for level in levels or () if level in SCALE]
    explicit = person_named(settings, role) and requested == saved
    if requested is not None and declared is None and requested in certified_for(settings, model):
        return Fit(model, requested, requested, "declared", explicit)  # a probe proved this level
    if not levels:
        return Fit(model, requested, requested, "unverified", explicit)
    if requested is None or requested in levels:
        return Fit(model, requested, requested, "declared", explicit)
    if explicit:
        raise EffortUnsupported(model, requested, levels, role)
    return Fit(model, nearest(requested, levels), requested, "fitted", False)


def record(settings: dict, role: str, result: Fit, *, cause=None) -> dict | None:
    """Save a fitted move as ``settings.effort_fits[role]``; a declared level records nothing."""
    if result.reason != "fitted":
        return None
    row = {"from": result.from_effort, "to": result.effort, "reason": cause or "route_fit"}
    settings.setdefault("effort_fits", {})[role] = row
    return row


def record_unverified(settings: dict, role: str, result: Fit, *, stage=None) -> dict | None:
    """Save an unlisted effort the launch used; an existing fit for the role stays as recorded."""
    if result.reason != "unverified" or result.effort is None:
        return None
    if role in (settings.get("effort_fits") or {}):
        return None
    row = {"from": result.from_effort, "to": result.effort, "reason": "unverified"}
    if stage:
        row["stage"] = stage
    settings.setdefault("effort_fits", {})[role] = row
    return row


def unsupported(settings: dict) -> list[EffortUnsupported]:
    """Every saved role whose person-named effort its model does not declare, in role order."""
    found = []
    for role in sorted((settings or {}).get("roles") or {}):
        try:
            fit(settings, role)
        except EffortUnsupported as error:
            found.append(error)
    return found


def guard(settings: dict) -> None:
    """Pause while any role's person-named effort is one its model does not declare (#2057).

    Checked for every role at every launch, so a level the Builder cannot run pauses the run
    before the Planner spends anything, not after planning is paid for.
    """
    if rows := unsupported(settings):
        raise rows[0]


def launch(settings: dict, role: str, effort, stage=None, *, model=None):
    """The effort this launch passes: fit it once at the boundary, record the decision, return it.

    Every role's named level is checked first (``guard``), so the run pauses before any launch.
    """
    guard(settings)
    result = fit(settings, role, effort=effort, model=model)
    record(settings, role, result)
    record_unverified(settings, role, result, stage=stage)
    return result.effort


def identity(settings: dict) -> dict:
    """The settings an approval, retry or recovery binds: derived effort evidence left out."""
    return {key: value for key, value in (settings or {}).items() if key not in DERIVED}


def same(left, right) -> bool:
    """Whether two saved settings authorize the same routes, a launch-time fit aside.

    The fit records how an effort was derived, never which route was approved, so a copy
    captured before a launch still matches the settings after it (#2057).
    """
    return left == right or identity(left) == identity(right)


def digests(settings: dict) -> tuple:
    """The digests a captured settings identity may have: with and without derived evidence."""
    return (util.digest(settings), util.digest(identity(settings)))


def reconcile(roles: dict, model: str, declared, *, explicit=()) -> dict:
    """Move every runner-chosen effort the model cannot serve to its nearest declared variant, in place.

    The single-model case (#2006): returns ``{role: {"from": old, "to": new}}`` for the roles moved.
    A role in ``explicit`` (a person named its effort, per role or for every role) that the model
    cannot serve raises instead of being moved. Nothing declared: nothing moves.
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
            raise EffortUnsupported(model, effort, declared, role)
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

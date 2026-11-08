"""A format-only report repair runs below the stage's saved reasoning level (#2868, #2835).

A repair fixes the report's format, not the task: in the 2026-11-07 interval-merger run a
one-comma JSON slip sent the GLM-5.3 high discovery repair through 63,994 reasoning tokens
into the 64k output cap and stopped the run. The repair launches at the highest level the
model offers below the configured one — for GLM 5.3 (low, high, max) that is low, not the
review rule's immediate scale step. A model with no lower level known or offered keeps the
configured level and the decision records why. The launch records the decision as
``effort_choice`` beside the review ceiling rule (autocode_review_effort); this is AutoCode's
own machinery decision, never a question for the person, and it never rewrites the saved route.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_route_fit as route_fit
except ImportError:
    import autocode_route_fit as route_fit

REASON = "format_repair"
TEXT = "this repair only fixes the report's format"


def decide(state: RunState, configured, *, model=None) -> dict | None:
    """The level a format repair runs at, with why; None when there is no level to bound."""
    if not configured or configured not in route_fit.SCALE:
        return None
    offered = _offered(state.get("settings") or {}, model)
    if offered is None:
        return _kept(configured, "levels_unknown", f"the levels {model} offers are not known")
    lower = [level for level in offered if route_fit.SCALE.index(level) < route_fit.SCALE.index(configured)]
    if not lower:
        return _kept(configured, "no_lower_level", f"{model} offers no level below {configured}")
    return {"level": lower[-1], "ceiling": configured, "lowered": True, "reason": REASON, "text": TEXT}


def _kept(configured, reason, text) -> dict:
    return {"level": configured, "ceiling": configured, "lowered": False, "reason": reason, "text": text}


def _offered(settings, model) -> list | None:
    """The levels the run knows ``model`` serves: the tool's listing and every level a probe passed."""
    if not isinstance(model, str):
        return None
    listed = route_fit.declared_for(settings, model)
    if listed is None:
        return None
    return sorted(set(listed) | set(route_fit.certified_for(settings, model)), key=route_fit.SCALE.index)

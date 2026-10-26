"""Route provenance for an explicit one-shot retry of a held Resolver incident.

Only model and reasoning effort may change. The retry owns the audit; this module
never grants a call, changes settings, or rewrites the immutable incident packet.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_escalation as escalation
    from . import autocode_quota_route as quota_route
except ImportError:
    import autocode_escalation as escalation
    import autocode_quota_route as quota_route


def change(state: RunState, previous_settings):
    if previous_settings is None or state.get("next_stage") != "astra_resolve":
        return None
    before = (previous_settings.get("roles") or {}).get("resolver")
    effective = before or (previous_settings.get("roles") or {}).get("astra")
    after = (state.get("settings", {}).get("roles") or {}).get("resolver")
    origins = previous_settings.get("route_origins") or {}
    selected_origins = state.get("settings", {}).get("route_origins") or {}
    efforts = previous_settings.get("effort_origins") or {}
    selected_efforts = state.get("settings", {}).get("effort_origins") or {}
    if not isinstance(effective, dict) or not isinstance(after, dict):
        return None
    if effective == after and origins.get("resolver") == selected_origins.get("resolver"):
        return None
    if (
        origins.get("resolver") != selected_origins.get("resolver")
        and selected_origins.get("resolver") != "user_explicit"
    ):
        raise ValueError("A scoped Resolver retry must retain an explicit route choice")
    changed = {key for key in effective.keys() | after.keys() if effective.get(key) != after.get(key)}
    if not changed <= {"model", "reasoning_effort"}:
        raise ValueError("A scoped Resolver retry can change only its model or reasoning effort")
    return {
        "before": copy.deepcopy(before),
        "after": copy.deepcopy(after),
        "origin_before": origins.get("resolver"),
        "origin_after": selected_origins.get("resolver"),
        "origin_present": "resolver" in origins,
        "origins_present": "route_origins" in previous_settings,
        # The retry's own effort flag rewrites the effort's provenance (#2057); it is the
        # same deliberate route change, not independent new evidence.
        "effort_origin_before": efforts.get("resolver"),
        "effort_origin_after": selected_efforts.get("resolver"),
        "effort_origin_present": "resolver" in efforts,
        "effort_origins_present": "effort_origins" in previous_settings,
    }


def unassigned(state: RunState, settings):
    """Undo recorded exact routes for evidence binding, never for actual dispatch.

    As with quota route assignments, changing a model is not new causal evidence.
    Only a route still exactly equal to the recorded assignment is normalized.
    """
    settings = copy.deepcopy(settings)
    pending = [
        grant["resolver_route_change"]
        for grant in reversed(state.get("failure_retry_authorizations") or [])
        if grant.get("resolver_route_change")
        and grant.get("actor") == "user_cli"
        and grant.get("stage") == "astra_resolve"
        and grant.get("novelty_packet")
    ]
    # Try explicit assignments before ordinary climbs. A climb after an
    # assignment may expose its exact route on the next pass. Each assignment
    # is undone at most once, including when the person chose an earlier rung.
    for _ in range(len(pending) + 1):
        before_pass = copy.deepcopy(settings)
        pending_count = len(pending)
        for assignment in pending[:]:
            roles = settings.get("roles") or {}
            origins = settings.get("route_origins") or {}
            if roles.get("resolver") != assignment["after"] or origins.get("resolver") != assignment["origin_after"]:
                continue
            pending.remove(assignment)
            if assignment["before"] is None:
                roles.pop("resolver", None)
            else:
                roles["resolver"] = copy.deepcopy(assignment["before"])
            if assignment["origin_present"]:
                origins["resolver"] = assignment["origin_before"]
            else:
                origins.pop("resolver", None)
            if not origins and not assignment["origins_present"]:
                settings.pop("route_origins", None)
            if "effort_origin_after" in assignment:
                efforts = settings.get("effort_origins") or {}
                if assignment["effort_origin_present"]:
                    efforts["resolver"] = assignment["effort_origin_before"]
                else:
                    efforts.pop("resolver", None)
                if not efforts and not assignment["effort_origins_present"]:
                    settings.pop("effort_origins", None)
        settings = escalation.unclimbed(state, quota_route.unassigned(state, settings), "resolver")
        if settings == before_pass and len(pending) == pending_count:
            break
    return settings

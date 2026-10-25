"""The inactivity limit a stage actually runs under (#298).

The runner default (300 seconds, autocode_configure) stops a provider that emits no event for that
long. OpenCode reports a model's reasoning only as completed blocks, so a model family that thinks
in long silent blocks looks idle while it is working: live MiMo Builders were stopped at 300 s
twice with no file written, and finished once the limit was 900 s. Such routes get a higher
default floor. The report-only planning stages (Requirements, Planner, Plan Reviewer) emit nothing
until their report, whatever the model: on the 2026-10-20 vague-brief trial every GLM 5.3 Planner
call for a large plan took 300 to 340 s, three were killed at 308 to 310 s by the runner default
and replayed under the same limit, spending all three automatic recoveries, while the two that
finished took 313 and 339 s. Those stages get their own floor. A limit the user set explicitly is
always used as given, and the saved setting is never rewritten: a floor applies per launch, from
the stage and its route model, and the stage timeout still bounds the call.

Imports only the names tables; run_role calls ``effective`` before building the ActivityMonitor.
"""

from __future__ import annotations

try:
    from . import autocode_roles as role_names
    from .model_catalogue import lineage
except ImportError:
    import autocode_roles as role_names
    from model_catalogue import lineage

# Model families observed to go silent for longer than the runner default mid-turn.
LONG_SILENCE_FLOORS = {"mimo": 900}
FAMILY_NAMES = {"mimo": "MiMo"}
# The planning stages that return one report and nothing before it (autocode_roles.ALL_PLANNING_STAGES).
PLANNING_FLOOR = 600
PLANNING_FLOOR_ORIGIN = "runner default for Requirements, Planner and Plan Reviewer launches"


def effective(limit, origin, model, stage=None):
    """(seconds, origin) for this launch: the configured limit and its saved origin, or the highest
    applicable floor, labelled for the stop text, when the configured limit is the runner default and
    lower than that floor. Floors: the route family's (LONG_SILENCE_FLOORS) and the report-only
    planning stages' (PLANNING_FLOOR); a MiMo Planner gets MiMo's 900 s."""
    if origin != "runner_default" or not limit:
        return limit, origin
    floors = []
    family = lineage(model or "")
    if LONG_SILENCE_FLOORS.get(family):
        floors.append((LONG_SILENCE_FLOORS[family], f"runner default for {FAMILY_NAMES[family]} routes"))
    if stage in role_names.ALL_PLANNING_STAGES:
        floors.append((PLANNING_FLOOR, PLANNING_FLOOR_ORIGIN))
    floor, label = max(floors, default=(0, origin))
    return (floor, label) if floor > limit else (limit, origin)

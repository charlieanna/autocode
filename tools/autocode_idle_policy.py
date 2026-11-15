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
# Bounded startup stream-liveness budget: fail fast when a wedged transport emits zero events (#3465).
FIRST_EVENT_SECONDS = 90
FIRST_EVENT_ORIGIN = "runner default before first provider event"


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


# A stopped attempt's ``stall_phase`` when no provider event arrived at all (autocode_stall_recovery).
SILENT_START = "before_first_event"


def silent_start(recovery, stage) -> bool:
    """Whether the run's recovery note says the attempt of ``stage`` now being retried stopped before
    its first provider event."""
    return isinstance(recovery, dict) and recovery.get("stage") == stage and recovery.get("stall_phase") == SILENT_START


def first_event_limit(limit, origin, *, stage_timeout=None, silent_start=False):
    """Return ``(seconds, origin)`` for initial stream liveness when using runner defaults.

    ``silent_start``: this launch retries an attempt that already ended at this budget. The budget cannot
    tell a wedged transport from a first answer that is slow to begin, and a second budget of the same
    length tells them apart no better. On a busy host, with OpenCode taking 45 to 49 s to start and
    planning prompts of 177 to 211 KB, three Plan Reviewer calls in a row on MiMo ended at it after 103
    to 105 s each, which spent the run's automatic recoveries and asked the person to authorize more;
    two GLM Requirements calls and two more MiMo reviews ended the same way (2026-11-14). The retry
    therefore gets no startup budget: it runs under the stage's ordinary inactivity limit (``effective``),
    which still stops a transport that is wedged.
    """
    if origin != "runner_default" or not limit or silent_start:
        return None, None
    candidates = [limit, FIRST_EVENT_SECONDS]
    if isinstance(stage_timeout, (int, float)) and stage_timeout > 0:
        candidates.append(stage_timeout)
    return min(candidates), FIRST_EVENT_ORIGIN

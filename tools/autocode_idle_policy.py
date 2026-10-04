"""The inactivity limit a stage actually runs under (#298).

The runner default (300 seconds, autocode_configure) stops a provider that emits no event for that
long. OpenCode reports a model's reasoning only as completed blocks, so a model family that thinks
in long silent blocks looks idle while it is working: live MiMo Builders were stopped at 300 s
twice with no file written, and finished once the limit was 900 s. Such routes get a higher
default floor. A limit the user set explicitly is always used as given, and the saved setting is
never rewritten: the floor applies per launch, from the stage's route model.

Imports nothing from the runner; run_role calls ``effective`` before building the ActivityMonitor.
"""
from __future__ import annotations

try:
    from .model_catalogue import lineage
except ImportError:
    from model_catalogue import lineage

# Model families observed to go silent for longer than the runner default mid-turn.
LONG_SILENCE_FLOORS = {"mimo": 900}
FAMILY_NAMES = {"mimo": "MiMo"}


def effective(limit, origin, model):
    """(seconds, origin) for this launch: the configured limit and its saved origin, or the route
    family's floor, labelled for the stop text, when the configured limit is the runner default and
    lower than that floor."""
    family = lineage(model or "")
    floor = LONG_SILENCE_FLOORS.get(family)
    if origin == "runner_default" and floor and limit and limit < floor:
        return floor, f"runner default for {FAMILY_NAMES[family]} routes"
    return limit, origin

"""A stage that spent its whole output limit on reasoning runs again one level lower, without asking.

In a live run (2026-11-11) Requirements on MiMo 2.6 Pro at the saved level ``medium`` reasoned for
64,003 tokens in one request, wrote no visible output, and stopped at the 64k output limit
(PAUSED_OUTPUT_CAP). The run then asked the person to name another model: a question about
AutoCode's own machinery, and the model they had chosen was not at fault, its level was.

So, before that pause stands, ``recover`` checks the stopped attempt. When it is the original
stage (not a format repair, which has its own bound, #2836), it exited cleanly, it changed no
source, almost all of its output was reasoning, the level it ran at came from the stage choices
or the runner (a level typed as a flag stays exact), and the model offers a lower level, the
runner sets the attempt aside through the existing abandonment boundary and runs the stage again
on the same model at the highest level it offers below that one. The decision is recorded in
``reasoning_exhaustion_recoveries`` and, at the next launch, as the stage's ``effort_choice``
(``decide``, via autocode_review_effort.launch). It keeps applying to that stage on that model
for the rest of the run. Each (stage, model, level) is lowered once, so the scale bounds it:
a stage that exhausts its limit at the model's lowest level pauses as before.

The model is never changed here; that stays the person's (autocode_quota_route). Imports only
lower-level modules; the runtime is passed in, keeping this below the controller.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_output_cap as output_cap
    from . import autocode_quota_route as quota_route
    from . import autocode_role_route as role_route
    from . import autocode_route_fit as route_fit
    from . import autocode_run_status as run_status
    from . import autocode_source_scope as source_scope
    from . import autocode_support as support
except ImportError:
    import autocode_output_cap as output_cap
    import autocode_quota_route as quota_route
    import autocode_role_route as role_route
    import autocode_route_fit as route_fit
    import autocode_run_status as run_status
    import autocode_source_scope as source_scope
    import autocode_support as support

KEY = "reasoning_exhaustion_recoveries"
REASON = "output_cap_reasoning"
# The share of the attempt's output that was reasoning for the stop to count as reasoning-bound.
# A report that ran long is a different stop: a lower level would not shorten it.
REASONING_SHARE = 0.95


def lowered(state: RunState, stage, model) -> dict | None:
    """The latest recorded lowering for ``stage`` on ``model``, or None."""
    rows = [
        row
        for row in state.get(KEY) or []
        if isinstance(row, dict) and row.get("stage") == stage and row.get("model") == model
    ]
    return rows[-1] if rows else None


def decide(state: RunState, stage, configured, *, model=None) -> dict | None:
    """The level a launch of ``stage`` runs at after a reasoning-bound output stop, or None when none applies."""
    row = lowered(state, stage, model)
    if not row or configured not in route_fit.SCALE or row.get("to") not in route_fit.SCALE:
        return None
    if route_fit.SCALE.index(configured) <= route_fit.SCALE.index(row["to"]):
        return None
    return {
        "level": row["to"],
        "ceiling": configured,
        "lowered": True,
        "reason": REASON,
        "text": f"an attempt at {row['from']} spent its whole output limit on reasoning",
    }


def _offered(settings, model) -> list | None:
    if not isinstance(model, str):
        return None
    listed = route_fit.declared_for(settings, model)
    if listed is None:
        return None
    return sorted(set(listed) | set(route_fit.certified_for(settings, model)), key=route_fit.SCALE.index)


def _reasoning_bound(record) -> dict | None:
    """The attempt's output and reasoning token counts when nearly all of its output was reasoning."""
    events = record.get("events")
    if not events or not Path(events).is_file():
        return None
    tokens = (support.event_metrics(events) or {}).get("provider_tokens") or {}
    output, reasoning = tokens.get("output_tokens"), tokens.get("reasoning_output_tokens")
    if not isinstance(output, int) or not isinstance(reasoning, int) or output <= 0:
        return None
    if reasoning < REASONING_SHARE * output:
        return None
    return {"output_tokens": output, "reasoning_tokens": reasoning}


def recover(runtime, state: RunState, run_dir, workspace, error) -> bool:
    """One reasoning-bound output stop, one lower level on the same model; False leaves the pause standing."""
    if getattr(runtime, "native_calls", None) is not None or error.status != output_cap.STATUS:
        return False
    record = state.get("active_stage") or {}
    stopped = quota_route.stopped_attempt(state, failure_status=support.failure_status)
    if not stopped or not stopped.get("active") or stopped.get("workflow_job") or record.get("report_only"):
        return False
    if record.get("exit_code") != 0 or record.get("timed_out") or record.get("interrupted"):
        return False
    role, stage, model = stopped.get("role"), stopped.get("stage"), stopped.get("model")
    settings = state.get("settings") or {}
    if (settings.get("effort_origins") or {}).get(role) == role_route.USER_EXPLICIT:
        return False  # a level the person typed stays exact
    level = record.get("reasoning_effort")
    offered = _offered(settings, model)
    if level not in route_fit.SCALE or not offered:
        return False
    lower = [value for value in offered if route_fit.SCALE.index(value) < route_fit.SCALE.index(level)]
    if not lower or any(
        row.get("from") == level
        for row in state.get(KEY) or []
        if row.get("stage") == stage and row.get("model") == model
    ):
        return False
    tokens = _reasoning_bound(record)
    if tokens is None:
        return False
    before = record.get("before_ref")
    if not before or not Path(before).is_file():
        return False
    after = source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)
    if json.loads(Path(before).read_text()).get("revision") != after.get("revision"):
        return False  # the attempt changed source; setting it aside is a person's call
    if state.get("pause_requested") or (Path(run_dir) / "pause-requested").exists():
        return False
    runtime.timeout_recovery_guard(state)
    candidate = copy.deepcopy(state)
    candidate.setdefault(KEY, []).append(
        {
            "at": runtime.now(),
            "attempt_id": stopped["attempt_id"],
            "stage": stage,
            "role": role,
            "model": model,
            "from": level,
            "to": lower[-1],
            **tokens,
        }
    )
    runtime.count_automatic_recovery(candidate)
    runtime.abandon_stage(candidate, Path(run_dir), Path(workspace), stopped["attempt_id"], actor="runner")
    run_status.running(candidate, state.get("phase") or "PLANNING", next_stage=stage)
    runtime.write_json(Path(run_dir) / "state.json", candidate)
    cast(dict, state).clear()
    state.update(candidate)
    job = quota_route.roles.screen_name(stage or role, state)
    print(
        f"{job}: {model} at {level} spent its whole output limit on reasoning "
        f"({tokens['reasoning_tokens']} of {tokens['output_tokens']} output tokens); "
        f"running it again at {lower[-1]} on the same model without asking",
        flush=True,
    )
    return True

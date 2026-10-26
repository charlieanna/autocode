"""A person-named effort the model does not declare: pause and ask for one it does (#2057).

The launch-boundary fit (autocode_route_fit) moves an effort the runner chose to the nearest
declared variant on its own, but never a level a person named: it raises EffortUnsupported.
That used to be a refusal in prose, which no client could answer, so the chat offered only
``Continue? [y/N]`` and continuing stopped at the same launch again (#2020, #2039).

Now the launch boundary checks every role first (``route_fit.guard``), so the run pauses before
anything launches, as ``PAUSED_EFFORT_UNSUPPORTED`` with one question, ``effort-<role>``, derived from the
saved routes like the cross-model question (autocode_quota_route.cross_model_question): its
options are the variants the model declares, it has no proposed default and only a person
answers it. ``--answer effort-ROLE=EFFORT`` saves that level as the person's own choice and
records an ``effort_assignment`` in ``user_events``; --resume-paused then continues.

Pure functions over the saved state. Imports the fit rule and role names, nothing higher.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_roles as roles
    from . import autocode_route_fit as route_fit
except ImportError:
    import autocode_roles as roles
    import autocode_route_fit as route_fit

STATUS = route_fit.STATUS
PREFIX = "effort-"
CATEGORY = "effort"  # a person's decision, never a delegable requirements answer
KIND = "effort_assignment"
PLACEHOLDER = "EFFORT"


def question(state: RunState) -> dict | None:
    """The effort question for the first role a person named an undeclared level for, or None."""
    settings = state.get("settings") or {}
    rows = route_fit.unsupported(settings)
    if not rows:
        return None
    row = rows[0]
    job = roles.screen_name(row.role, state)
    nearest = route_fit.nearest(row.effort, row.declared)
    levels = ", ".join(row.declared)
    return {
        "id": PREFIX + row.role,
        "question": f"{row.model} does not offer {row.effort!r} effort for the {job}; choose one it offers ({levels})",
        "why": (
            f"You chose {row.effort!r} reasoning effort for the {job}, and {row.model} declares only {levels}. "
            f"AutoCode never changes a level you chose. Choose one of those for the {job}; nothing has run at "
            f"{row.effort!r}."
        ),
        "options": list(row.declared),
        "proposed_default": "",
        "kind": "decision",
        "category": CATEGORY,
        "delegable": False,
        "route_role": row.role,
        "job": job,
        "model": row.model,
        "requested": row.effort,
        "declared": list(row.declared),
        "nearest": nearest,
        "flag": route_fit.flag(row.role),
    }


def need(asked: dict) -> dict:
    """``needs.effort``: what a client shows beside the question and the options it may offer."""
    return {
        "question_id": asked["id"],
        "role": asked["route_role"],
        "job": asked.get("job"),
        "model": asked.get("model"),
        "requested": asked.get("requested"),
        "declared": list(asked.get("declared") or ()),
        "nearest": asked.get("nearest"),
    }


def parse_answer(answers, asked: dict) -> str:
    """The level ``--answer effort-ROLE=EFFORT`` names for ``asked``; ValueError otherwise."""
    if len(answers) != 1:
        raise ValueError("Answer the effort question on its own: --answer effort-ROLE=EFFORT")
    question_id, separator, effort = answers[0].partition("=")
    if not separator or question_id != asked["id"]:
        raise ValueError(f"{question_id} is not the effort question of this pause; answer {asked['id']}")
    effort = effort.strip().lower()
    if effort not in asked["declared"]:
        raise ValueError(
            f"{asked['model']} declares {', '.join(asked['declared'])} for the {asked['job']}; "
            f"answer {asked['id']}=LEVEL with one of those"
        )
    return effort


def assign(state: RunState, asked: dict, effort: str, *, at: str, actor: str = "user_cli") -> dict:
    """Save ``effort`` as the person's level for the role and record it; return the record."""
    role = asked["route_role"]
    settings = state["settings"]
    route = settings["roles"][role]
    record = {
        "kind": KIND,
        "actor": actor,
        "at": at,
        "via": "answer",
        "role": role,
        "job": asked.get("job"),
        "model": route.get("model"),
        "from": route.get("reasoning_effort"),
        "to": effort,
        "declared": list(asked.get("declared") or ()),
        "pause_status": STATUS,
    }
    route["reasoning_effort"] = effort
    settings.setdefault("effort_origins", {})[role] = route_fit.USER_EXPLICIT
    (settings.get("effort_fits") or {}).pop(role, None)
    if settings.get("effort_fits") == {}:
        settings.pop("effort_fits")
    state.setdefault("user_events", []).append(record)
    return copy.deepcopy(record)

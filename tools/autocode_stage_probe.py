"""The measure-and-approve offer on each probed stage of Stage models (#3630 step 2).

Stage models (``autocode stages list --json``) lists every model a stage may run. For a stage that
has a probe, this adds ``probe``: the same models as candidates to measure, the free shortlist's
picks ticked (``autocode_model_shortlist``), each with what an earlier probe found
(``autocode_measured_routes``), and two argument arrays following ``autocode`` that a client fills
only with candidates from this list:

- ``estimate.next_command`` prints what measuring the ticked candidates can cost and runs nothing;
- ``run.next_command`` runs the probe on exactly those candidates within ``CAP``, the estimate's own
  ``max_usd`` the person was shown.

In both, the word ``MEASURE`` stands for ``--measure MODEL LEVEL`` once per ticked candidate, with that
candidate's ``model`` and ``effort`` (``default`` for none). Pressing the run button is the person's one
approval of that spend (owner direction on #3630, 2026-11-15); nothing here runs a probe, and an
unticked model is never measured.

A probe pins the other stages it needs to the routes this list shows for them (the Planner's probe
needs its Plan Reviewer, the Builder's needs its Planner and a checker), so the probe measures the
candidate against the person's own setup; the Tester's and Completion Reviewer's probes pin nothing,
since every other stage there is a script. A stage whose pinned route has no model, or a checker's
probe on Codex, has no ``run``, and ``stop`` says why.

Pure: the stage list's own options and routes in, the offer out; it launches nothing.
"""

from __future__ import annotations

try:
    from . import autocode_measured_routes as measured_routes
    from . import autocode_model_shortlist as model_shortlist
    from . import autocode_route_fit as route_fit
except ImportError:
    import autocode_measured_routes as measured_routes
    import autocode_model_shortlist as model_shortlist
    import autocode_route_fit as route_fit

# The stages `autocode probe --stage` measures (autocode_model_probe.STAGES; tests keep them equal).
PROBED = ("requirements", "planner", "plan_reviewer", "builder", "tester", "completion")
MEASURE = "MEASURE"
CAP = "CAP"
DEFAULT_LEVEL = "default"
# What each probe pins, as (its flag, the stage whose route it takes).
PINNED = {
    "requirements": (),
    "planner": (("--checker", "plan_reviewer"), ("--requirements", "requirements")),
    "plan_reviewer": (("--planner", "planner"), ("--requirements", "requirements")),
    "builder": (("--planner", "planner"), ("--checker", "tester"), ("--requirements", "requirements")),
    # Every other stage of a checker's probe is the bundled script, which calls no model (autocode_check_probe).
    "tester": (),
    "completion": (),
}
# The checkers' probes run their scripted stages as a tool of their own, which Codex cannot run.
SCRIPTED = ("tester", "completion")
_STAGE_LABELS = {
    "plan_reviewer": "Plan Reviewer",
    "planner": "Planner",
    "tester": "Tester",
    "requirements": "Requirements",
}


def offer(role, options, routes, *, backend, default_effort=None, document=None) -> dict | None:
    """``role``'s probe offer, or None for a stage without a probe.

    ``options`` are the stage's rows from the stage list (``model``, ``levels``, ``levels_listed``,
    ``billing``, ``context``, ``reasoning``, ``release_date``, ``unavailable``); ``routes`` maps each
    stage to the ``{"model", "effort"}`` it runs now; ``default_effort`` is the stage's default level,
    which each candidate runs at, moved to the nearest level it offers.
    """
    if role not in PROBED:
        return None
    usable = [option for option in options if not option.get("unavailable")]
    rows = model_shortlist.shortlist([_candidate(option) for option in usable])
    ticked = {row["model"]: row for row in rows}
    measured = {row["model"]: row for row in measured_routes.rank(_results(role, document), role)}
    candidates = []
    for option in usable:
        row = ticked.get(option["model"]) or {}
        found = measured.get(option["model"])
        candidates.append(
            {
                "model": option["model"],
                "effort": _level(option, default_effort),
                "family": option.get("family"),
                "billing": option.get("billing"),
                "ticked": bool(row.get("suggested")),
                "reason": row.get("reason"),
                "measured": (
                    {"qualified": found["qualified"], "reason": found["reason"], "date": found.get("date")}
                    if found
                    else None
                ),
            }
        )
    candidates.sort(key=lambda item: (not item["ticked"], item["model"]))
    pinned, stop = [], None
    if role in SCRIPTED and backend == "codex":
        stop = "this probe runs on OpenCode, Kilo or Claude Code; Codex cannot run its scripted stages"
    for flag, stage in PINNED[role]:
        route = routes.get(stage) or {}
        if not route.get("model"):
            if stage == "requirements":
                continue  # the probe then takes the saved Requirements choice, as a task would
            stop = f"the {_STAGE_LABELS[stage]} has no model to pin; choose one above first"
            break
        pinned += [flag, route["model"], route.get("effort") or DEFAULT_LEVEL]
    base = ["probe", "--stage", role, *_backend(backend), *pinned, "--json", MEASURE]
    return {
        "stage": role,
        "candidates": candidates,
        "estimate": {"next_command": base},
        "run": None if stop else {"next_command": [*base, "--max-cost-usd", CAP]},
        "stop": stop,
    }


def _candidate(option):
    """A stage-list option in the shape the shortlist reads (``autocode_route_discovery`` candidates)."""
    return {
        "model": option["model"],
        "billing": option.get("billing"),
        "levels": option.get("levels") if option.get("levels_listed") else None,
        "metadata": {
            "reasoning": option.get("reasoning"),
            "context": option.get("context"),
            "release_date": option.get("release_date"),
        },
    }


def _level(option, default_effort):
    """The level a candidate is measured at: the stage's default level, at the nearest one it offers."""
    if option.get("levels_listed"):
        if not option.get("levels"):
            return None
        return route_fit.nearest(default_effort, option["levels"]) if default_effort else None
    return default_effort


def _results(role, document):
    document = measured_routes.load() if document is None else document
    saved = (document.get("roles") or {}).get(role)
    if not isinstance(saved, dict) or saved.get("probe") != measured_routes.PROBES[role][0]:
        return []
    return [item for item in saved.get("results") or () if measured_routes._valid_result(item)]


def _backend(backend):
    """The probe's own options that make it run on the list's backend, as a task there would."""
    if backend in ("codex", "claude"):
        return ["--engine", backend]
    return ["--provider", backend] if backend else []

"""The route the scoreboard would suggest for each stage of a new run, recorded beside the route that ran (#4251 step 2).

Shadow only: nothing here changes a route. A new run records, for each stage, the route it runs, the route the
measured results would suggest, and why, as ``settings["route_suggestion"]``. That record is how the restart of
automatic model choice (#2049) is judged before any run is allowed to take a suggestion: it is the run's evidence,
never a question to the person.

The rules, each a test:

- **The person's choice wins.** A stage whose route a flag, the project's config, a saved stage choice, this
  conversation or ``--single-model`` named is recorded as kept for the person, with no suggestion.
- **Only work the ledger can judge.** The scoreboard sees whether the next stage sent a Builder's or a Planner's
  work back (``autocode_route_scoreboard``). Whether a check was right is not visible, so the checking stages
  (Plan Reviewer, Tester, Completion Reviewer) and every other stage get no suggestion, and say why.
- **Enough evidence, beyond chance.** Both the route that runs and the candidate need ``MIN_ATTEMPTS`` counted
  attempts on the same backend and job, and the candidate's 95% range must sit wholly above the running route's:
  a point estimate never decides. Ties go to the higher lower bound, then the lower burn per run.
- **Independence stays.** A suggestion is dropped when its model would be checked by a model that shares its
  family under ``autocode_verifier_independence``, judged against the run's own checking routes.
- **No checker weaker than the work it checks.** AutoCode has no answer yet for how a model's strength compares
  with another's across a vendor or a tier (#4249 answers it only for one tool's own generations). So a suggestion
  stands only when it moves the stage to a model of the same family that the price table does not price above the
  running one, which cannot make the producer stronger than the checker that checked the running one. Any other
  suggestion is recorded with ``blocked`` naming why, so the shadow evidence still shows it.

``AUTOCODE_AUTO_CHOICE`` and ``~/.config/autocode/auto-choice.json`` (written by ``autocode stages auto``) hold the
one switch: ``shadow`` (the default) records, ``off`` records nothing, and a run is then exactly as before.

Pure apart from reading that switch; imports only lower-level modules.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    from . import autocode_model_identity as model_identity
    from . import autocode_prices as prices
    from . import autocode_role_route as role_route
    from . import autocode_verifier_independence as independence
except ImportError:
    import autocode_model_identity as model_identity
    import autocode_prices as prices
    import autocode_role_route as role_route
    import autocode_verifier_independence as independence

SCHEMA = 1
MIN_ATTEMPTS = 8
OFF = "off"
SHADOW = "shadow"
MODES = (OFF, SHADOW)
ENV = "AUTOCODE_AUTO_CHOICE"
FILE_NAME = "auto-choice.json"
# The stages whose work a later stage can send back, by route, with the job the scoreboard names them by.
JUDGED = {"builder": "Builder", "planner": "Planner"}
PERSON_ORIGINS = (role_route.USER_EXPLICIT, role_route.SINGLE_MODEL, role_route.STAGE_CHOICE)
BASIS = (
    "Shadow only: the routes that ran are unchanged. A suggestion needs "
    f"{MIN_ATTEMPTS} counted attempts for both routes on live, completed runs, and a 95% range wholly above the "
    "running route's."
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def switch_path(config_dir: Path) -> Path:
    """Where ``autocode stages auto`` keeps the switch: beside ``stages.toml``."""
    return Path(config_dir) / FILE_NAME


def mode(config_dir: Path | None) -> str:
    """``shadow`` or ``off``: the environment first, then the saved switch, else ``shadow``."""
    named = os.environ.get(ENV)
    if named in MODES:
        return named
    if config_dir is not None:
        try:
            saved = json.loads(switch_path(config_dir).read_text(encoding="utf-8")).get("mode")
        except (OSError, ValueError, AttributeError):
            saved = None
        if saved in MODES:
            return saved
    return SHADOW


def save_mode(config_dir: Path, value: str) -> Path:
    """Save the switch; raises ValueError for anything but ``off`` or ``shadow``."""
    if value not in MODES:
        raise ValueError(f"the switch is {' or '.join(MODES)}, not {value!r}")
    target = switch_path(config_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"mode": value}) + "\n", encoding="utf-8")
    return target


def _stats(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": entry["model"],
        "effort": entry["effort"],
        "attempts": entry["attempts"],
        "first_time": entry["first_time"],
        "first_time_range": entry["first_time_range"],
        "usd_per_run": entry["usd_per_run"],
    }


def _price(model: str) -> float | None:
    found = prices.rate(model)
    if not found:
        return None
    try:
        return float(found["input"]) + float(found["output"])
    except (KeyError, TypeError, ValueError):
        return None


def _not_stronger(candidate: str, running: str) -> str | None:
    """Why moving ``running`` to ``candidate`` could make the producer stronger than its checker, or None."""
    if model_identity.family(candidate) != model_identity.family(running):
        return "checker_strength_unknown: another model family"
    mine, theirs = _price(candidate), _price(running)
    if mine is None or theirs is None:
        return "checker_strength_unknown: no price for one of the models"
    if mine > theirs:
        return "checker_strength_unknown: priced above the running model"
    return None


def _clash(role: str, model: str, settings: dict[str, Any]) -> str | None:
    moved = {
        **settings,
        "roles": {
            **_dict(settings.get("roles")),
            role: {**_dict(_dict(settings.get("roles")).get(role)), "model": model},
        },
    }
    for row in independence.clashes(moved):
        if row["producer"] == role:
            return f"independence: {row['label']}: {row['problem']}"
    return None


def suggest(settings: dict[str, Any], board: dict[str, Any], *, backend: str) -> dict[str, Any]:
    """The shadow record for a new run's ``settings``, from the scoreboard ``board``. Changes nothing."""
    routes = _dict(settings.get("roles"))
    origins = _dict(settings.get("route_origins"))
    measured = [entry for entry in board.get("routes") or () if entry.get("backend") == backend]
    stages: dict[str, Any] = {}
    for role in sorted(routes):
        route = _dict(routes.get(role))
        ran = {"model": route.get("model"), "effort": route.get("reasoning_effort")}
        record: dict[str, Any] = {"ran": ran, "suggested": None}
        stages[role] = record
        if origins.get(role) in PERSON_ORIGINS or settings.get("single_model_mode"):
            record["reason"] = "kept: the person chose this stage's route"
            continue
        job = JUDGED.get(role)
        if job is None:
            record["reason"] = "not judged: whether this stage's answer was right is not visible in the ledger"
            continue
        for_job = [entry for entry in measured if entry["job"] == job]
        running = next(
            (
                entry
                for entry in for_job
                if entry["model"] == ran["model"] and entry["effort"] == (ran["effort"] or "default")
            ),
            None,
        )
        if running is None or running["attempts"] < MIN_ATTEMPTS:
            have = 0 if running is None else running["attempts"]
            record["reason"] = f"too little evidence: the running route has {have} of {MIN_ATTEMPTS} counted attempts"
            continue
        record["running"] = _stats(running)
        top = running["first_time_range"][1]
        better = [
            entry
            for entry in for_job
            if entry is not running and entry["attempts"] >= MIN_ATTEMPTS and entry["first_time_range"][0] > top
        ]
        if not better:
            others = sum(1 for entry in for_job if entry is not running and entry["attempts"] >= MIN_ATTEMPTS)
            record["reason"] = (
                f"no better route: {others} other measured route(s) with {MIN_ATTEMPTS}+ attempts, "
                "none with a 95% range wholly above the running route's"
            )
            continue
        better.sort(key=lambda entry: (-entry["first_time_range"][0], entry["usd_per_run"] or 0.0, entry["model"]))
        pick = better[0]
        record["suggested"] = _stats(pick)
        blocked = _clash(role, pick["model"], settings) or (
            None if pick["model"] == ran["model"] else _not_stronger(pick["model"], ran["model"])
        )
        if blocked:
            record["blocked"] = blocked
            record["reason"] = "suggested, but blocked by the rules"
        else:
            record["reason"] = "suggested: measured results rank it above the running route"
    return {
        "schema": SCHEMA,
        "mode": SHADOW,
        "backend": backend,
        "basis": BASIS,
        "counted_runs": board.get("counted_runs", 0),
        "stages": stages,
    }

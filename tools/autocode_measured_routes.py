"""The measured ranking of a stage's models on the person's own accounts (#3630 step 5).

The probe measures the models the person ticked (``autocode_model_shortlist``) and grades each
answer mechanically (``autocode_requirements_probe``, ``autocode_planner_probe``,
``autocode_plan_reviewer_probe``). This module keeps the result, in one file
beside ``stages.toml`` (``~/.config/autocode/measured-routes.json``), and turns it into the
qualification entries the selector reads. The selector (``autocode_route_selector.qualified``) is
the one reader: automatic selection, the quota fallback in ``autocode_route_pool`` and the model
listing all go through it, so they cannot come to disagree (AGENTS.md: one answer, read from one
place).

A result is kept per model and level, so a model measured once is not measured again when it
shows up through another project; recording a new result for the same model and level replaces
the old one. Once a stage has been measured, only its qualified measured models are chosen
automatically, in measured order, and the quota fallback moves only among them; when none
qualified, the person chooses the stage's model. The person can still name any model for a stage
themselves.

Ranking (#3630 step 4): for each model, the lowest level that qualifies (higher levels added time
and blocking confirmations without better questions on 2026-11-15); then models by passes, then
whether they answer within the latency limit, then cost, then level and id. A model qualifies once
it has run every request and passed at least ``QUALIFY_SHARE`` of them.

File state written here: ``{"schema_version": 1, "roles": {role: {"probe": version, "results":
[{"model", "effort", "tool", "account", "date", "grades": [grade], "summary": summary}]}}}``.
"""

from __future__ import annotations

import json
import os
from fractions import Fraction
from pathlib import Path

try:
    from . import autocode_model_identity as model_identity
    from . import autocode_plan_reviewer_probe as reviewer_probe
    from . import autocode_planner_probe as planner_probe
    from . import autocode_requirements_probe as requirements_probe
    from . import autocode_util as util
except ImportError:
    import autocode_model_identity as model_identity
    import autocode_plan_reviewer_probe as reviewer_probe
    import autocode_planner_probe as planner_probe
    import autocode_requirements_probe as requirements_probe
    import autocode_util as util

SCHEMA_VERSION = 1
FILE_NAME = "measured-routes.json"
# Six of nine was the lowest score the owner accepted as a default on 2026-11-15 (GLM-5.3 low).
QUALIFY_SHARE = Fraction(2, 3)
# What each stage's probe asks, so a later probe version is not ranked against an older one, and the
# time within which an answer counts as prompt when ranking.
PROBES = {
    "requirements": ("requirements-v1", len(requirements_probe.REQUESTS)),
    "planner": ("planner-v1", len(planner_probe.REQUESTS)),
    "plan_reviewer": ("plan-reviewer-v1", len(reviewer_probe.REQUESTS)),
}
LATENCY_LIMITS = {
    "requirements": requirements_probe.LATENCY_LIMIT_SECONDS,
    "planner": planner_probe.TIME_LIMIT_SECONDS,
    "plan_reviewer": reviewer_probe.TIME_LIMIT_SECONDS,
}
_LEVELS = {level: index for index, level in enumerate(util.EFFORTS)}


def path() -> Path:
    """``measured-routes.json`` beside the user's ``stages.toml`` (``$XDG_CONFIG_HOME/autocode``)."""
    # An empty XDG_CONFIG_HOME means unset, never the current directory.
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "autocode" / FILE_NAME


def load(file=None) -> dict:
    """The saved document, or an empty one when there is none.

    A file that cannot be read as this schema is treated as no measurement: selection then uses the
    shipped table, as it did before any probe ran, and nothing is chosen from a broken record.
    """
    try:
        document = json.loads(Path(file or path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        return _empty()
    if not isinstance(document.get("roles"), dict):
        return _empty()
    return document


def record(role, results, *, file=None) -> dict:
    """Merge ``results`` into ``role``'s record, replacing any result for the same model and level."""
    if role not in PROBES:
        raise ValueError(f"no probe is defined for {role!r}")
    for result in results:
        _check_result(result)
    document = load(file)
    version = PROBES[role][0]
    saved = document["roles"].get(role) if isinstance(document["roles"].get(role), dict) else {}
    kept = saved.get("results") if saved.get("probe") == version else []
    replaced = {_key(result) for result in results}
    merged = [result for result in kept or () if _key(result) not in replaced] + list(results)
    document["roles"][role] = {"probe": version, "results": sorted(merged, key=_key)}
    target = Path(file or path())
    target.parent.mkdir(parents=True, exist_ok=True)
    util.atomic_json(target, document)
    return document


def result(model, effort, grades, *, tool=None, account=None, date=None) -> dict:
    """One measured model and level, from its graded requests."""
    grades = list(grades)
    return {
        "model": model,
        "effort": effort,
        "tool": tool,
        "account": account,
        "date": date or util.now()[:10],
        "grades": grades,
        "summary": requirements_probe.summary(grades),
    }


def qualifies(result, role="requirements") -> bool:
    """Every request ran and at least ``QUALIFY_SHARE`` of them passed."""
    summary = result.get("summary") or {}
    total = PROBES[role][1]
    passed = summary.get("passed")
    return summary.get("total") == total and type(passed) is int and Fraction(passed, total) >= QUALIFY_SHARE


def rank(results, role="requirements") -> list:
    """One row per model, best first: ``{"model", "effort", "qualified", "summary", "reason"}``."""
    by_model: dict = {}
    for item in results:
        by_model.setdefault(model_identity.identity(item["model"]), []).append(item)
    rows = []
    for items in by_model.values():
        passing = sorted((item for item in items if qualifies(item, role)), key=_level)
        if passing:
            best = passing[0]
            rows.append(_ranked(best, True, f"lowest level that qualifies: {_score(best)}"))
        else:
            best = max(items, key=lambda item: ((item.get("summary") or {}).get("passed") or 0, -_level(item)))
            rows.append(_ranked(best, False, f"did not qualify: {_score(best)}"))
    return sorted(rows, key=lambda row: _order(row, role))


def entries(role, document=None) -> list:
    """The qualification entries for ``role``'s qualified measured models, in ranked order."""
    return [
        {
            "model": row["model"],
            "routes": [row["model"]],
            "effort": row["effort"],
            "preference": index,
            "provenance": "measured",
            "evidence": f"measured on {row['date']} with the {PROBES[role][0]} probe; {row['reason']}",
        }
        for index, row in enumerate(row for row in _rows(role, document) if row["qualified"])
    ]


def measured(role, document=None) -> bool:
    """True when the probe has measured any model for ``role``, whether or not it qualified."""
    return bool(_rows(role, document))


def failed(role, document=None) -> set:
    """The identities of models the probe measured for ``role`` and found not to qualify."""
    return {model_identity.identity(row["model"]) for row in _rows(role, document) if not row["qualified"]}


def _rows(role, document):
    if role not in PROBES:
        return []
    document = load() if document is None else document
    saved = (document.get("roles") or {}).get(role)
    if not isinstance(saved, dict) or saved.get("probe") != PROBES[role][0]:
        return []
    results = [item for item in saved.get("results") or () if _valid_result(item)]
    return rank(results, role)


def _empty():
    return {"schema_version": SCHEMA_VERSION, "roles": {}}


def _key(item):
    return (model_identity.identity(item["model"]), item.get("effort") or "")


def _level(item):
    return _LEVELS.get(item.get("effort"), len(_LEVELS))


def _score(item):
    summary = item.get("summary") or {}
    seconds = summary.get("mean_seconds")
    cost = summary.get("cost_usd")
    parts = [f"{summary.get('passed')}/{summary.get('total')} requests at {item.get('effort') or 'its default level'}"]
    parts.append(f"{seconds} s per reply" if seconds is not None else "time unmeasured")
    parts.append(f"${cost} in all" if cost is not None else "cost unmeasured")
    return ", ".join(parts)


def _ranked(item, qualified, reason):
    return {
        "model": item["model"],
        "effort": item.get("effort"),
        "tool": item.get("tool"),
        "account": item.get("account"),
        "date": item.get("date"),
        "qualified": qualified,
        "summary": item.get("summary"),
        "reason": reason,
    }


def _order(row, role="requirements"):
    summary = row.get("summary") or {}
    seconds = summary.get("mean_seconds")
    cost = summary.get("cost_usd")
    return (
        not row["qualified"],
        -(summary.get("passed") or 0),
        not (seconds is not None and seconds <= LATENCY_LIMITS[role]),
        cost if cost is not None else float("inf"),
        _LEVELS.get(row.get("effort"), len(_LEVELS)),
        row["model"],
    )


def _valid_result(item):
    try:
        _check_result(item)
    except ValueError:
        return False
    return True


def _check_result(item):
    if not isinstance(item, dict) or not isinstance(item.get("model"), str) or not item["model"]:
        raise ValueError("a measured result needs a model")
    if item.get("effort") is not None and item["effort"] not in _LEVELS:
        raise ValueError(f"{item['model']}: unknown level {item['effort']!r}")
    if not isinstance(item.get("summary"), dict) or not isinstance(item.get("grades"), list):
        raise ValueError(f"{item['model']}: a measured result needs its grades and summary")

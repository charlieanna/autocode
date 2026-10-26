"""The automatic route selector (#1426 step 7): the deterministic choice itself.

One pure function from what the accounts offer to one route per role, or a stop that
names what is missing in account terms. Nothing here lists models, connects an account,
reads a credential or calls a provider: discovery (steps 4-5) produces the candidates,
the access check probes unknown routes, and new-run configuration persists the choice.
Saved runs reuse their routes without discovery.

A model is eligible for a role only when the bundled qualification table
(autocode_route_qualification.json) lists it for that role: the minimum standard, so
"cheapest" can never pick a weak or free model. Within a role's qualified candidates
the ranking is billing first (a subscription route beats any pay-per-token route, as
the catalogue's advice has always ordered), then a route whose access is already known
over one the access check would have to probe, then the price-table sum of input and
output per million tokens, then the model id. The whole assignment is then searched
under the verifier-independence pairs, cheapest first with backtracking, so the result
is deterministic and explainable, never merely optimal.

Candidates arrive as dicts the discovery steps define: ``model`` (the id exactly as the
tool lists it), ``tool``, ``account`` (the login that bills it, e.g. "Kilo Gateway"),
``billing`` ("subscription", "pay per token", or None when unknown), ``levels`` (the
reasoning levels the model advertises, when discovery could read them) and ``probe``
("known" when access is already proven). The qualification table's reasoning level is
the level AutoCode's qualifying runs used, so it is passed as recorded unless discovery
reports the model lacks it; a model with no recorded level runs at its own default.
Candidates from one tool only — selection works one tool per run (#1426 answer 1), so
the wiring scopes the candidate list to the run's tool before calling.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from statistics import median

try:
    from . import autocode_model_identity as model_identity
    from . import autocode_prices as prices
    from . import autocode_role_route as role_route
    from . import autocode_roles as roles
    from . import autocode_verifier_independence as independence
except ImportError:
    import autocode_model_identity as model_identity
    import autocode_prices as prices
    import autocode_role_route as role_route
    import autocode_roles as roles
    import autocode_verifier_independence as independence

QUALIFICATION_PATH = Path(__file__).with_name("autocode_route_qualification.json")
PROVENANCE = ("qualified", "provisional")
# The roles a run can ask for, in the order selection assigns them: the volume roles
# first, the checkers that must dodge them next, the unconstrained astra route last.
PIPELINE_ROLES = ("requirements", "glm", "terra", "plan_reviewer", "sol", "completion", "astra")
# Billing before probe before price (#1426 rule 2 and answer 2): never nudge a
# subscription onto per-token billing to save a probe, never probe while a known route
# qualifies, then cheapest. None (unknown billing) sits between the two, as the
# catalogue's BILLING_ORDER has always placed it.
BILLING_ORDER = {"subscription": 0, None: 1, "pay per token": 2}
# The login names the tools themselves print (kilocode.toml [auth]), for stop messages
# that say what to connect. Display labels only; no routing reads them.
ACCOUNT_LABELS = {
    "zai-coding-plan": "the Z.AI Coding Plan",
    "zhipuai-coding-plan": "the Zhipu AI Coding Plan",
    "xiaomi-token-plan-sgp": "the Xiaomi Token Plan",
    "alibaba-token-plan": "the Alibaba Token Plan",
    "openai": "the ChatGPT login",
    "anthropic": "the Anthropic (Claude) login",
    "kilo": "Kilo Gateway credits",
}
_ROLE_LABELS = {
    "requirements": lambda: roles.SCREEN["requirements"],
    "glm": lambda: roles.SCREEN["planner"],
    "terra": lambda: roles.SCREEN["builder"],
    "plan_reviewer": lambda: roles.SCREEN["plan_reviewer"],
    "sol": lambda: roles.SCREEN["tester"],
    "completion": lambda: roles.SCREEN["completion"],
    "astra": lambda: f"per-task {roles.SCREEN['planner']} and {roles.SCREEN['resolver']}",
}


def parse(document):
    """The validated qualification table from a decoded file; ValueError names the first bad field."""
    if not isinstance(document, dict) or not isinstance(document.get("roles"), dict):
        raise ValueError("the qualification table needs a roles object")
    if not isinstance(document.get("as_of"), str) or not document["as_of"]:
        raise ValueError("the qualification table needs as_of")
    for role, entries in document["roles"].items():
        if not isinstance(role, str) or not role or not isinstance(entries, list) or not entries:
            raise ValueError(f"bad qualification role {role!r}")
        for entry in entries:
            _validate_entry(role, entry)
    excluded = _validate_excluded(document, document["roles"])
    return {"as_of": document["as_of"], "roles": document["roles"], "excluded": excluded}


def _validate_entry(role, entry):
    """Validate one entry; every field the selector reads must be present and well formed."""
    where = f"{role}.{entry.get('model')!r}"
    if not isinstance(entry, dict) or not isinstance(entry.get("model"), str) or not entry["model"]:
        raise ValueError(f"bad qualification entry in {role}: needs a model")
    routes = entry.get("routes") or [entry["model"]]
    if any(not isinstance(route, str) or not route for route in routes) or entry["model"] not in routes:
        raise ValueError(f"{where}: routes must be a nonempty list of model ids including the model itself")
    if entry.get("provenance") not in PROVENANCE:
        raise ValueError(f"{where}: provenance must be one of {PROVENANCE}")
    if not isinstance(entry.get("evidence"), str) or not entry["evidence"]:
        raise ValueError(f"{where}: evidence is required")
    if entry.get("effort") is not None and (not isinstance(entry["effort"], str) or not entry["effort"]):
        raise ValueError(f"{where}: effort must be a level name or null")
    if "preference" in entry and (type(entry["preference"]) is not int or entry["preference"] < 0):
        raise ValueError(f"{where}: preference must be a nonnegative integer")
    benchmark = entry.get("benchmark")
    if benchmark is not None and (
        not isinstance(benchmark, dict)
        or not isinstance(benchmark.get("name"), str)
        or not benchmark["name"]
        or not isinstance(benchmark.get("source"), str)
        or not benchmark["source"]
    ):
        raise ValueError(f"{where}: benchmark needs a name and a source")
    runs = entry.get("runs")
    if runs is not None and (not isinstance(runs, list) or not all(_valid_run(run) for run in runs)):
        raise ValueError(
            f"{where}: runs must be records with a date, a run, and minutes and cost_usd as numbers or null"
        )


def _valid_run(run):
    """One AutoCode run that confirmed the route (#2059), as tools/qualify_routes.py writes it."""

    def amount(value):
        return value is None or (type(value) in (int, float) and value >= 0)

    return (
        isinstance(run, dict)
        and isinstance(run.get("date"), str)
        and bool(run["date"])
        and isinstance(run.get("run"), str)
        and bool(run["run"])
        and amount(run.get("minutes"))
        and amount(run.get("cost_usd"))
    )


def evidence_summary(entry):
    """One line on what admitted ``entry``, for `autocode models --selection` (#2059)."""
    runs = entry.get("runs") or []
    if not runs:
        if entry.get("provenance") == "provisional":
            return "provisional: no AutoCode run yet"
        return "qualified: no recorded run"
    latest = max(run["date"] for run in runs)
    parts = [f"{len(runs)} AutoCode run{'s' if len(runs) != 1 else ''}, latest {latest}"]
    minutes = [run["minutes"] for run in runs if run.get("minutes") is not None]
    costs = [run["cost_usd"] for run in runs if run.get("cost_usd") is not None]
    if minutes:
        parts.append(f"median {median(minutes):.0f} min")
    if costs:
        parts.append(f"median ${median(costs):.2f}")
    return f"{entry.get('provenance')}: " + ", ".join(parts)


def _validate_excluded(document, roles):
    """The models a role must never auto-select, with the decision that says so.

    A profile can pin a route the owner ruled out for automatic selection, so a live run still
    reaches it; recording the exclusion here keeps the qualification script from re-admitting it
    and keeps selection from choosing it (#2121)."""
    excluded = document.get("excluded")
    if excluded is None:
        return {}
    if not isinstance(excluded, dict):
        raise ValueError("the qualification table's excluded list must be an object of roles")
    for role, entries in excluded.items():
        if not isinstance(role, str) or role not in roles:
            raise ValueError(f"bad excluded role {role!r}")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"excluded {role} needs a nonempty list")
        listed = {
            model_identity.identity(route_id)
            for entry in roles[role]
            for route_id in entry.get("routes") or [entry["model"]]
        }
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("model"), str) or not entry["model"]:
                raise ValueError(f"bad excluded entry in {role}: needs a model")
            where = f"excluded {role}.{entry['model']}"
            if not isinstance(entry.get("evidence"), str) or not entry["evidence"]:
                raise ValueError(f"{where}: evidence is required")
            if model_identity.identity(entry["model"]) in listed:
                raise ValueError(f"{where}: excluded and also listed as a qualified model")
    return excluded


@lru_cache(maxsize=1)
def table():
    """The shipped qualification table, read once per process."""
    return parse(json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8")))


def qualified(role):
    """The table's entries for ``role``, in file order; () when the table names none."""
    entries = table()["roles"].get(role)
    return tuple(entries) if isinstance(entries, list) else ()


def _label(role):
    """The role's job on screen (autocode_roles), with a plain fallback for unknown keys."""
    maker = _ROLE_LABELS.get(role)
    return maker() if maker else role


def _account_label(model):
    """Which login a model id bills, in the tools' own words; display only."""
    if not isinstance(model, str) or "/" not in model:
        return "the tool's own login"
    return ACCOUNT_LABELS.get(model.split("/", 1)[0], model.split("/", 1)[0])


def _rank(candidate, entry):
    """The sort key that orders one role's candidates: the table's preference, billing, probe, price, then id.

    Preference is the owner's explicit order for a role (the Plan Reviewer: GPT-6 Sol, then Claude Opus,
    2026-10-20); an entry without one ranks 0, so every other role keeps billing-then-price order.
    """
    billing = candidate.get("billing") if candidate.get("billing") in BILLING_ORDER else None
    row = prices.rate(candidate.get("model")) or prices.rate(entry["model"])
    total = row["input"] + row["output"] if row else math.inf
    return (
        entry.get("preference", 0),
        BILLING_ORDER[billing],
        0 if candidate.get("probe") == "known" else 1,
        total,
        candidate.get("model") or "",
    )


def _effort(entry, candidate):
    """The level to pass, or None for the model's own default; never an invented level.

    The table's level is the level AutoCode's qualifying runs used on that model, so it
    is evidence, not invention; discovery's advertised levels exist to veto it when a
    tool reports the model lacks the level. Without either, the model's own default.
    """
    wanted = (entry or {}).get("effort")
    if not wanted:
        return None
    levels = candidate.get("levels")
    if isinstance(levels, (list, tuple)) and wanted not in levels:
        return None
    return wanted


def _options(role, candidates, permitted):
    """One role's usable candidates paired with their table entries, cheapest first.

    A candidate the permissions leave out never reaches this list, so a role whose
    models are all connected but none allowed reads as empty here; the stop message is
    what separates the two cases.
    """
    usable = []
    for candidate in candidates:
        identity = model_identity.identity(candidate["model"])
        if permitted is not None and candidate.get("account") not in permitted:
            continue
        for entry in qualified(role):
            if identity in {model_identity.identity(route) for route in entry.get("routes") or [entry["model"]]}:
                usable.append((candidate, entry))
                break
    return sorted(usable, key=lambda pair: _rank(*pair))


def permitted_set(candidates):
    """The per-role candidate lists a run saves for recovery (#1426 step 9).

    Every qualified, connected candidate for each role, as discovery found it: the
    routes a quota stop may switch to without asking, because every account in them
    was already permitted when the run chose its routes. Selection stores this under
    ``route_selection["candidates"]``.
    """
    return {role: [candidate for candidate, _entry in _options(role, candidates, None)] for role in PIPELINE_ROLES}


def next_route(selection, role, stopped_model, *, failed_models=()):
    """The route a stopped model's role switches to on its own, or None (#1426 step 9).

    Re-selects ``role`` from the saved permitted set minus all models already stopped for this role, with
    every other role pinned to its current route so checker independence holds. The
    answer never bills an account the run was not already permitted to bill; when no
    candidate remains the result is None and the person is asked, as before.
    """
    saved = (selection or {}).get("candidates") or {}
    routes = (selection or {}).get("routes") or {}
    if role not in routes:
        return None
    pool = [candidate for candidates in saved.values() for candidate in candidates]
    stopped = model_identity.identity(stopped_model)
    failed = {stopped, *(model_identity.identity(model) for model in failed_models)}
    alive = [candidate for candidate in pool if model_identity.identity(candidate["model"]) not in failed]
    pinned = {other: route["model"] for other, route in routes.items() if other != role and route.get("model")}
    result = select(list(routes), alive, pinned=pinned)
    if result["stop"] is not None or result["routes"] is None:
        return None
    route = result["routes"][role]
    if route.get("origin") != role_route.AUTO_SELECTED or model_identity.identity(route["model"]) == stopped:
        return None
    return route


def _clashes(role, model, assigned):
    """Why ``model`` cannot take ``role`` given the routes already assigned, or None."""
    for producer, verifier, label in independence.PAIRS:
        if role not in (producer, verifier):
            continue
        other = assigned.get(verifier if role == producer else producer)
        if not other:
            continue
        problem = independence.clash(other, model) if role == verifier else independence.clash(model, other)
        if problem:
            return f"{label}: {problem}"
    return None


def explains_clash(role, model, routes) -> str | None:
    """Why ``model`` could not take ``role`` against a finished assignment, or None.

    The public face of the search's clash check, for `autocode models --selection`:
    ``routes`` is a finished selection's ``routes`` mapping of role -> route dict.
    """
    assigned = {other: route.get("model") for other, route in (routes or {}).items()}
    return _clashes(role, model, assigned)


def _search(order, options, assigned):
    """The cheapest independent assignment over ``order`` as role -> model, or None.

    ``assigned`` carries the placed model ids, not the candidates, because that is
    what the independence check compares; the winning candidates are looked up from
    ``options`` once a full assignment exists.
    """
    if not order:
        return assigned
    role, rest = order[0], order[1:]
    for candidate, _entry in options[role]:
        if _clashes(role, candidate["model"], assigned):
            continue
        result = _search(rest, options, {**assigned, role: candidate["model"]})
        if result is not None:
            return result
    return None


def _greedy_context(order, options, pinned):
    """The assignment a person reading top to bottom would expect, for stop messages.

    The search itself backtracks; this replay walks the same order taking each role's
    cheapest route that does not clash with what it already placed, so a role the search
    could not fit is judged against routes that are easy to point at.
    """
    placed = dict(pinned)
    stalled = []
    for role in order:
        if role in pinned:
            continue
        for candidate, _entry in options[role]:
            if not _clashes(role, candidate["model"], placed):
                placed[role] = candidate["model"]
                break
        else:
            stalled.append(role)
    return placed, stalled


def _stop(order, options, pinned, candidates):
    """The message that names, in account terms, what a failed selection is missing."""
    placed, stalled = _greedy_context(order, options, pinned)
    lines = ["AutoCode cannot choose the models for this task by itself.", ""]
    for role in stalled:
        if options[role]:
            clashes = [
                f"{candidate['model']} — {_clashes(role, candidate['model'], placed)}"
                for candidate, _entry in options[role]
                if _clashes(role, candidate["model"], placed)
            ]
            lines.append(f"{_label(role)}: every qualified model would check its own family's work:")
            lines.extend(f"  {reason}" for reason in clashes)
            lines.append("  Connect a second model family, so a checker never grades its own work.")
            continue
        lines.append(f"{_label(role)}: no qualified model is available.")
        for entry in qualified(role):
            connected = [
                candidate
                for candidate in candidates
                if model_identity.identity(candidate["model"])
                in {model_identity.identity(route) for route in entry.get("routes") or [entry["model"]]}
            ]
            if connected:
                billed_by = connected[0].get("account") or _account_label(entry["model"])
                lines.append(f"  {entry['model']} — bills {billed_by}, which you have not allowed")
            else:
                lines.append(f"  {entry['model']} — connect {_account_label(entry['model'])}")
    lines.append("")
    lines.append("Connect what is missing, or name each role's model yourself with --<role>-model.")
    return "\n".join(lines)


def select(task_roles, candidates, *, permissions=None, pinned=None):
    """One deterministic route per requested role, or the stop that explains the gap.

    ``task_roles`` names the roles this task runs; ``candidates`` is what discovery found
    on the run's one tool; ``permissions`` (when given) is the collection of account
    labels allowed to be billed; ``pinned`` maps a role to the model a person's own flag
    named, which is kept exactly and only checked for independence. The result carries
    ``routes`` (model, tool, account, reasoning level and origin per role —
    ``role_route.AUTO_SELECTED`` for the choice, ``user_explicit`` for a pin) and
    ``probe``, the selected models whose access the access check (step 8) still has to
    confirm. A result whose ``routes`` is None carries ``stop`` instead: read it to the
    person; it never suggests --single-model (#1426 answer 3).
    """
    candidates = [
        candidate
        for candidate in candidates or []
        if isinstance(candidate, dict) and isinstance(candidate.get("model"), str) and candidate["model"]
    ]
    tools = sorted({candidate.get("tool") for candidate in candidates if candidate.get("tool")})
    if len(tools) > 1:
        return _stopped(f"Automatic selection works one tool per run; these candidates span {', '.join(tools)}.")
    pinned = dict(pinned or {})
    order = [role for role in PIPELINE_ROLES if role in task_roles]
    unknown = [role for role in task_roles if role not in PIPELINE_ROLES]
    if unknown:
        return _stopped(f"No automatic route is defined for {', '.join(sorted(unknown))}.")
    permitted = set(permissions) if permissions is not None else None
    options = {role: _options(role, candidates, permitted) for role in order}
    for role, model in pinned.items():
        if role in options:
            candidate = next((c for c in candidates if c["model"] == model), {"model": model})
            options[role] = [(candidate, None)]
    assignment = _search(order, options, {})
    if assignment is None:
        return {"routes": None, "probe": [], "stop": _stop(order, options, pinned, candidates)}
    chosen = {
        role: next((candidate, entry) for candidate, entry in options[role] if candidate["model"] == model)
        for role, model in assignment.items()
    }
    routes = {
        role: {
            "model": candidate["model"],
            "tool": candidate.get("tool"),
            "account": candidate.get("account"),
            "reasoning_effort": _effort(entry, candidate),
            "origin": role_route.USER_EXPLICIT if role in pinned else role_route.AUTO_SELECTED,
        }
        for role, (candidate, entry) in chosen.items()
    }
    return {
        "routes": routes,
        "probe": sorted(
            {
                candidate["model"]
                for role, (candidate, _entry) in chosen.items()
                if role not in pinned and candidate.get("probe") != "known"
            }
        ),
        "stop": None,
    }


def _stopped(message):
    return {"routes": None, "probe": [], "stop": message}

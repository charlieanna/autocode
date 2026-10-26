#!/usr/bin/env python3
"""Discover available models and present role suggestions to the operator.

Selection is never a hardcoded guess: we search the live `opencode models`
catalogue, then suggest a verifier≠producer map and
print the shortlist for the user to accept or override.

When a new run's models are absent from the provider catalogue, ``choose`` stops
it before any model call and shows what is available, grouped by plan and tier, with a replacement for each
role. ``autocode models`` shows the same list at any time.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

try:
    from . import autocode_model_identity as model_identity
    from . import autocode_prices as prices
    from . import autocode_role_route as role_route
    from . import autocode_role_transport as role_transport
    from . import autocode_roles as roles
    from . import autocode_route_ladder as route_ladder
    from . import autocode_route_selector as route_selector
    from . import autocode_verifier_independence as verifier_independence
except ImportError:
    import autocode_model_identity as model_identity
    import autocode_prices as prices
    import autocode_role_route as role_route
    import autocode_role_transport as role_transport
    import autocode_roles as roles
    import autocode_route_ladder as route_ladder
    import autocode_route_selector as route_selector
    import autocode_verifier_independence as verifier_independence

# Ladder entry points when both families are present (docs/models.md).
PREFERRED = {
    "requirements": ("zai-coding-plan/glm-5.3", "medium"),
    "planner": ("zai-coding-plan/glm-5.3", "high"),
    "reviewer": ("openai/gpt-6-sol", "high"),
    "builder": ("zai-coding-plan/glm-5.3", "medium"),
    "validator": ("openai/gpt-6-sol", "high"),
    "completion": ("openai/gpt-6-sol", "medium"),
    "resolver": ("openai/gpt-6-astra", "high"),
}
INDEPENDENCE = (
    ("planner", "reviewer"),
    ("builder", "validator"),
    ("builder", "completion"),
)

PIN_PATH = Path(__file__).resolve().parent / "model-pins.json"


def load_pins(path: Path | None = None) -> dict | None:
    """Remembered operator shortlist (tools/model-pins.json). None if absent."""
    pin_file = path or PIN_PATH
    if not pin_file.exists():
        return None
    return json.loads(pin_file.read_text())


def roles_from_pins_or_suggest(models: list[str], pins: dict | None = None) -> dict[str, dict]:
    """Prefer the remembered pin when its IDs are still in the catalogue."""
    pins = pins if pins is not None else load_pins()
    if pins and pins.get("roles"):
        available = set(models)
        if all(cfg.get("model") in available for cfg in pins["roles"].values() if cfg.get("model")):
            return {
                role: {"model": cfg["model"], "effort": cfg.get("effort"), "source": "pinned"}
                for role, cfg in pins["roles"].items()
            }
    return suggest(models)


def list_catalogue(workspace: Path | None = None, command=("opencode", "models")) -> list[str]:
    result = subprocess.run(
        list(command), cwd=str(workspace) if workspace else None, capture_output=True, text=True, timeout=180
    )
    if result.returncode:
        raise RuntimeError(f"model catalogue failed ({result.returncode}): {(result.stderr or result.stdout)[:300]}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def usable(models: list[str]) -> list[str]:
    """All well-formed provider routes, without model or provider blacklists."""
    return sorted(
        {
            m
            for m in models
            if isinstance(m, str) and "/" in m and all(m.split("/", 1)) and not any(c.isspace() for c in m)
        }
    )


def family(model: str) -> str:
    """The one family rule (autocode_model_identity): GLM, MiMo, Claude and GPT each one family, via any gateway."""
    return model_identity.family(model)


def by_provider(models: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for m in models:
        groups[m.split("/", 1)[0]].append(m)
    return dict(sorted(groups.items()))


def suggest(models: list[str]) -> dict[str, dict]:
    """Role → {model, effort} using preferred ids when present, else available
    pair with verifier≠producer across the two strongest families."""
    available = set(models)
    roles: dict[str, dict] = {}

    def pick(preferred: str, avoid_family: str | None = None) -> str | None:
        if preferred in available:
            return preferred
        candidates = [m for m in models if not avoid_family or family(m) != avoid_family]
        return candidates[0] if candidates else (models[0] if models else None)

    # Seed builder first so validators can avoid its family.
    builder_pref, builder_effort = PREFERRED["builder"]
    builder = pick(builder_pref)
    builder_fam = family(builder) if builder else None
    roles["builder"] = {"model": builder, "effort": builder_effort, "source": "catalogue"}

    for role, (pref, effort) in PREFERRED.items():
        if role == "builder":
            continue
        avoid = None
        for producer, verifier in INDEPENDENCE:
            if role == verifier and producer == "builder":
                avoid = builder_fam
            if role == verifier and producer == "planner":
                avoid = family(roles.get("planner", {}).get("model") or "")
        model = pick(pref, avoid_family=avoid)
        roles[role] = {
            "model": model,
            "effort": effort,
            "source": "preferred" if model == pref else "catalogue-fallback",
        }
    # Re-check independence; if violated, swap verifier to another model
    # (other family if possible, else a different id) when the catalogue allows.
    for producer, verifier in INDEPENDENCE:
        p_model = roles.get(producer, {}).get("model")
        v_model = roles.get(verifier, {}).get("model")
        if not p_model or not v_model or p_model != v_model:
            # family independence when possible
            if p_model and v_model and family(p_model) == family(v_model):
                other = [m for m in models if family(m) != family(p_model) and m != p_model]
                if other:
                    roles[verifier]["model"] = other[0]
                    roles[verifier]["source"] = "independence-swap"
                else:
                    other = [m for m in models if m != p_model and m != v_model]
                    if other:
                        roles[verifier]["model"] = other[0]
                        roles[verifier]["source"] = "independence-swap"
            continue
        other = [m for m in models if m != p_model]
        if other:
            roles[verifier]["model"] = other[0]
            roles[verifier]["source"] = "independence-swap"
    return roles


def render(models: list[str], roles: dict[str, dict]) -> str:
    lines = ["# Available models", ""]
    for provider, ids in by_provider(models).items():
        lines.append(f"## {provider}")
        for m in ids:
            lines.append(f"- {m}")
        lines.append("")
    lines.append("# Suggested role map (verifier ≠ producer)")
    lines.append("")
    lines.append("| Role | Model | Effort | Source |")
    lines.append("| --- | --- | --- | --- |")
    for role in ("requirements", "planner", "reviewer", "builder", "validator", "completion", "resolver"):
        cfg = roles.get(role) or {}
        lines.append(
            f"| {role} | `{cfg.get('model') or '—'}` | {cfg.get('effort') or '—'} | {cfg.get('source') or '—'} |"
        )
    lines.append("")
    lines.append("Present this shortlist to the operator; accept as-is or override any role.")
    return "\n".join(lines) + "\n"


def discover_and_suggest(workspace: Path | None = None) -> dict:
    raw = list_catalogue(workspace)
    models = usable(raw)
    return {
        "raw_count": len(raw),
        "usable": models,
        "providers": by_provider(models),
        "roles": roles_from_pins_or_suggest(models) if models else {},
    }


# --- When a run's models cannot all be used ------------------------------------------

# What a model is for, by its name after the provider prefix. Cheap workers plan and build,
# strong judges check. These are preferences, never model restrictions.
# A model missing here is shown as "tier unknown", never guessed.
TIERS = {
    "glm-5.3": "worker",
    "gpt-6-luna": "worker",
    "gpt-5.6-terra": "worker",
    "gpt-6-sol": "judge",
    "gpt-5.6-sol": "judge",
    "gpt-6-astra": "resolver",
}
TIER_LABELS = {"worker": "cheap worker", "judge": "strong judge", "resolver": "Resolver tier", None: "tier unknown"}
WANTED = {"worker": "cheap worker", "judge": "strong judge", "resolver": "Resolver-tier model"}
# How a provider prefix bills: one table, in autocode_role_route (#1924).
PLANS = role_route.PLANS
# A run's roles in pipeline order: screen name (autocode_roles), the flag that
# selects its model, the tier it wants. Labels are not a second name table.
ROLES = {
    "requirements": (roles.SCREEN["requirements"], "--requirements-model", "worker"),
    "glm": (roles.SCREEN["planner"], "--glm-model", "worker"),
    "terra": (roles.SCREEN["builder"], "--terra-model", "worker"),
    "plan_reviewer": (roles.SCREEN["plan_reviewer"], "--plan-reviewer-model", "judge"),
    "sol": (roles.SCREEN["tester"], "--sol-model", "judge"),
    "completion": (roles.SCREEN["completion"], "--completion-model", "judge"),
    # The per-task Planner runs on the astra route, and the Resolver starts as a copy of it unless
    # --resolver-model sets the Resolver's own route (docs/models.md).
    "astra": (f"{roles.SCREEN['resolver']} and per-task {roles.SCREEN['planner']}", "--astra-model", "resolver"),
}
# Producer and checker roles that must not share a model (autocode_verifier_independence.PAIRS).
CHECKS = tuple((producer, verifier) for producer, verifier, _label in verifier_independence.PAIRS)
# Preference order: billing first (never nudge a subscription user onto per-token billing),
# then the tier the role wants.
BILLING_ORDER = ("subscription", None, "pay per token")
TIER_ORDER = {
    "worker": ("worker", None, "judge", "resolver"),
    "judge": ("judge", None, "worker", "resolver"),
    "resolver": ("resolver", "judge", None, "worker"),
}


def tier(model: str) -> str | None:
    return TIERS.get(model.rsplit("/", 1)[-1])


plan = role_route.plan


def lineage(model: str) -> str:
    """The family autocode_dispatch compares: GLM, MiMo, or the model itself (GPT tiers are independent)."""
    name = model.rsplit("/", 1)[-1]
    if model.startswith("zai-coding-plan/") or name.startswith("glm-"):
        return "glm"
    if model.startswith("xiaomi-token-plan-sgp/") or name.startswith("mimo-"):
        return "mimo"
    return model


def independent(producer: str, checker: str) -> bool:
    """The dispatch rule (autocode_verifier_independence): never the same model, and GLM or MiMo
    never checks its own family."""
    return not verifier_independence.shares(producer, checker)


def advise(roles: dict, available, *, openai_auth: str | None = None, refused_missing: bool = True) -> dict:
    """Report unlisted routes and suggest independent replacements from the catalogue.

    Billing and tier guide suggestions; neither excludes a configured model.
    ``refused_missing`` remains accepted for caller compatibility.
    """
    available = set(available)
    routes = {role: route["model"] for role, route in roles.items() if role in ROLES and route.get("model")}
    missing = {role: model for role, model in routes.items() if model not in available}
    chosen = {role: model for role, model in routes.items() if role not in missing}
    candidates = usable(sorted(available))
    suggestions = {}
    for role, (_label, _flag, want) in ROLES.items():
        if role not in missing:
            continue
        others = [
            chosen[other] for pair in CHECKS if role in pair for other in pair if other != role and other in chosen
        ]
        allowed = [m for m in candidates if all(independent(m, o) for o in others)]
        pick = min(
            allowed,
            default=None,
            key=lambda m: (
                BILLING_ORDER.index(plan(m, openai_auth)[1]),
                TIER_ORDER[want].index(tier(m)),
                family(m) in {family(o) for o in others},
                m not in chosen.values(),
                m,
            ),
        )
        if pick is None:
            why = (
                ("nothing you can use keeps it independent of " + " and ".join(others))
                if others
                else "nothing you can use"
            )
        else:
            chosen[role] = pick
            name, billing = plan(pick, openai_auth)
            why = f"{TIER_LABELS[tier(pick)]} on {name} ({billing or 'billing unknown'})"
            if tier(pick) != want:
                why += f"; no {WANTED[want]} is usable"
        suggestions[role] = {"model": pick, "why": why}
    defaults = defaultdict(list)
    for role in ROLES:
        if role in routes:
            defaults[routes[role]].append(ROLES[role][0])
    kept = usable(sorted(available))
    catalogue = [
        {
            "model": m,
            "plan": plan(m, openai_auth)[0],
            "billing": plan(m, openai_auth)[1],
            "tier": tier(m),
            "defaults": defaults.get(m, []),
        }
        for m in kept
    ]
    flags = [
        part
        for role in ROLES
        if (suggestions.get(role) or {}).get("model")
        for part in (ROLES[role][1], suggestions[role]["model"])
    ]
    hidden = [m for m in available if "/" in m and not any(c.isspace() for c in m) and m not in kept]
    return {
        "missing": missing,
        "suggestions": suggestions,
        "flags": flags,
        "catalogue": catalogue,
        "hidden": len(hidden),
    }


def render_catalogue(advice: dict) -> list[str]:
    lines = ["Models your plans offer. Cheap workers plan and build; strong judges check.", ""]
    groups = defaultdict(list)
    for entry in advice["catalogue"]:
        groups[(entry["plan"], entry["billing"])].append(entry)
    width = max((len(entry["model"]) for entry in advice["catalogue"]), default=0)
    billing_text = {
        "subscription": "subscription",
        "pay per token": "pay per token",
        None: "billing unknown",
        "refused": "billing unknown",
    }
    for (name, billing), entries in sorted(
        groups.items(),
        key=lambda item: (
            BILLING_ORDER.index(item[0][1]) if item[0][1] in BILLING_ORDER else len(BILLING_ORDER),
            item[0][0],
        ),
    ):
        lines.append(f"{name} · {billing_text[billing]}")
        for entry in entries:
            note = f"   default for {', '.join(entry['defaults'])}" if entry["defaults"] else ""
            lines.append(f"  {entry['model']:<{width}}  {TIER_LABELS[entry['tier']]:<14}{note}".rstrip())
        lines.append("")
    if not advice["catalogue"]:
        lines += ["  (none)", ""]
    if advice["hidden"]:
        count = advice["hidden"]
        lines += [f"{count} malformed catalogue {'entry was' if count == 1 else 'entries were'} ignored.", ""]
    return lines


def render_advice(advice: dict, provider: str = "OpenCode") -> str:
    """The stop message for a run whose models cannot all be used."""
    by_model = defaultdict(list)
    for role, model in advice["missing"].items():
        by_model[model].append(ROLES[role][0])
    lines = [
        f"Cannot use with {provider}: "
        + "; ".join(f"{m} ({', '.join(labels)})" for m, labels in by_model.items())
        + ".",
        "",
    ]
    lines += render_catalogue(advice)
    lines.append("Suggested replacements:")
    for role, suggestion in advice["suggestions"].items():
        label, flag, _want = ROLES[role]
        target = suggestion["model"] or f"choose one from the list with {flag}"
        lines.append(
            f"  {label} ({flag}): {advice['missing'][role]} → {target}. {suggestion['why'][:1].upper()}{suggestion['why'][1:]}."
        )
    if advice["flags"]:
        lines += ["", "To use them, start the run with:", "  " + " ".join(advice["flags"])]
        per_token = sorted(
            {
                s["model"]
                for s in advice["suggestions"].values()
                if s["model"] and plan(s["model"])[1] == "pay per token"
            }
        )
        if per_token:
            lines.append(
                "  "
                + ", ".join(per_token)
                + (" bills" if len(per_token) == 1 else " bill")
                + " per token, not by subscription."
            )
    lines += ["", "Nothing was launched. AutoCode never changes a model without you."]
    return "\n".join(lines)


def choose(
    settings: dict, provider, workspace, *, interactive: bool, ask=input, out=print, inventory=None, named_strong=None
) -> dict:
    """New runs: stop before any model call when a role's model cannot be used.

    Checks the OpenCode-shaped routes against the provider's own list: a saved run could
    never use an unlisted model, since resuming keeps the saved routes. A provider that
    cannot list its models starts the run as before.
    In chat the user may accept every replacement, which is written into the routes as
    --<role>-model flags would be; otherwise the run stops with the list and the flags.
    The same list then checks every rung the run's roles may climb to
    (autocode_route_ladder.serve): ``named_strong`` is a --builder-strong-model the
    person named, which is refused rather than dropped when the list lacks a model it needs.
    """
    if role_transport.enabled(settings):
        for facade, routes, _identity in role_transport.groups(settings, provider, workspace):
            subset = {
                **settings,
                "roles": {role: dict(route) for role, route in routes.items()},
                "transport_identities": {},
            }
            # The subgroup retains the concrete provider so the identity validator
            # does not mistake a deliberately bounded catalogue view for a lost marker.
            subset["provider"] = routes[next(iter(routes))].get("provider") or settings.get("provider")
            selected = choose(
                subset,
                facade,
                workspace,
                interactive=interactive,
                ask=ask,
                out=out,
                inventory=inventory if facade is provider else None,
                named_strong=named_strong,
            )
            for role, route in selected["roles"].items():
                settings["roles"][role]["model"] = route["model"]
            if selected.get("route_origins"):
                # The subgroup's copy shares the real record when the run has one; a run saved
                # before route origins existed gets its replacements recorded here instead.
                settings.setdefault("route_origins", {}).update(selected["route_origins"])
            if "route_ladders" in selected:
                settings["route_ladders"] = selected["route_ladders"]
            if "builder_retry" in selected:
                settings["builder_retry"] = selected["builder_retry"]
        return settings
    roles = {
        role: route
        for role, route in settings.get("roles", {}).items()
        if role in ROLES and (route.get("engine") or settings.get("engine")) == "opencode"
    }
    lister = inventory.selection_models if inventory is not None else getattr(provider, "available_models", None)
    if not roles or lister is None:
        return settings
    try:
        available = lister(workspace)
    except (RuntimeError, OSError):
        return settings
    if available is None:
        return settings
    # Availability comes from the provider, not a hardcoded model policy.
    advice = advise(roles, available, refused_missing=False)
    if not advice["missing"]:
        return _serve(settings, available, named_strong, roles=settings.get("roles"))
    signed_in = getattr(provider, "openai_auth", None)
    if signed_in and any(model.startswith("openai/") for model in available):
        advice = advise(roles, available, openai_auth=signed_in(workspace), refused_missing=False)
    report = render_advice(advice, getattr(provider, "NAME", "OpenCode"))
    if not interactive or not all(s["model"] for s in advice["suggestions"].values()):
        raise RuntimeError(report)
    out(report.rsplit("\n", 1)[0])
    if ask("Use the suggested replacements for this run? [y/N] ").strip().lower() not in ("y", "yes"):
        raise RuntimeError("No model was changed and nothing was launched; start the run with the flags above")
    for role, suggestion in advice["suggestions"].items():
        settings["roles"][role]["model"] = suggestion["model"]
        settings.setdefault("route_origins", {})[role] = role_route.CATALOGUE_REPLACEMENT
    return _serve(settings, available, named_strong, roles=settings.get("roles"))


def _serve(settings: dict, available, named_strong, roles=None) -> dict:
    try:
        return route_ladder.serve(settings, available, roles=roles, named_strong=named_strong)
    except ValueError as error:
        raise RuntimeError(f"{error}. Nothing was launched") from None


def default_roles(provider) -> dict[str, dict]:
    """The profile's own route for every role on ``provider``: the fixed defaults a run falls back to.

    A new run does not always take these; see `route_advice`, which also weighs automatic selection.
    """
    return {role: {"model": model} for role, model in provider.DEFAULT_MODELS.items() if role in ROLES}


# `autocode_route_discovery.discover` picks its producer from these two declarations, so asking the
# same way here keeps a provider that declares neither from ever reaching a listing command.
SELECTION_DECLARATIONS = ("auth_table", "auth_logins")


def selection_possible(provider) -> bool:
    """Whether automatic selection could field routes for ``provider`` from its own local listings."""
    return any(callable(getattr(provider, key, None)) for key in SELECTION_DECLARATIONS)


class _ListedModels:
    """A listing already read, handed to discovery so checking the selected routes lists once."""

    def __init__(self, models):
        self._models = list(models)

    def available_models(self, workspace=None, env=None):
        return self._models


def route_advice(provider, available, workspace=None) -> dict:
    """The routes a new run would take on ``provider``, and which of them it does not offer.

    A profile route the run would never use is not a gap: `autocode_configure.select_routes` replaces
    the profile defaults with the selected routes whenever selection can field a set, and only falls
    back to the profile when it cannot. So the profile is weighed first and selection is consulted
    only when the profile shows a gap, which keeps the ordinary check to the one listing the caller
    already has. Listing only: this launches no model and touches no credential.

    ``source`` names which routes were weighed ("profile" or "selection"), ``replaced`` lists the
    roles selection moved off their profile route, and ``reason`` says why selection could not.
    """
    fixed = default_roles(provider)
    result = {
        "roles": fixed,
        "missing": advise(fixed, available)["missing"],
        "source": "profile",
        "replaced": {},
        "reason": None,
    }
    if not result["missing"] or not selection_possible(provider):
        return result
    try:
        try:
            from . import autocode_route_discovery as discovery
            from . import autocode_route_selector as route_selector
        except ImportError:
            import autocode_route_discovery as discovery
            import autocode_route_selector as route_selector

        found = discovery.discover(
            provider, workspace, inventory=_ListedModels(available) if available is not None else None
        )
        chosen = route_selector.select(route_selector.PIPELINE_ROLES, found["candidates"])
    except (RuntimeError, ValueError, OSError) as error:
        result["reason"] = str(error)
        return result
    if chosen.get("stop"):
        result["reason"] = chosen["stop"]
        return result
    routes = {
        role: {"model": route["model"]}
        for role, route in (chosen.get("routes") or {}).items()
        if role in ROLES and route.get("model")
    }
    if not routes:
        result["reason"] = "automatic selection named no route for any role"
        return result
    return {
        "roles": routes,
        "missing": advise(routes, available)["missing"],
        "source": "selection",
        "replaced": {
            role: (fixed[role]["model"], route["model"])
            for role, route in routes.items()
            if role in fixed and fixed[role]["model"] != route["model"]
        },
        "reason": None,
    }


def price_report(routes: dict[str, dict]) -> dict:
    """The dated price table and the rate each default route is priced at (None: no rate) (#721)."""
    table = prices.table()
    return {
        "as_of": table["as_of"],
        "currency": table["currency"],
        "unit": "per million tokens",
        "models": table["models"],
        "defaults": {
            role: {
                "model": route["model"],
                "priced_as": prices.key(route["model"]),
                "rate": prices.rate(route["model"]),
            }
            for role, route in routes.items()
        },
    }


def render_prices(report: dict, provider_name: str) -> str:
    """`autocode models --prices` as text. A model without a row reads "no rate", never a zero price."""

    def rates(row):
        cached = f"  cached input {row['cached_input']:.4g}" if "cached_input" in row else ""
        return f"input {row['input']:.4g}{cached}  output {row['output']:.4g}"

    width = max([len(model) for model in report["models"]] + [0])
    lines = [f"Prices as of {report['as_of']}, USD per million tokens (tools/autocode_prices.json):"]
    for model, row in sorted(report["models"].items()):
        lines.append(f"  {model:<{width}}  {rates(row)}  (read {row['retrieved']}: {row['source']})")
    lines += ["", f"Default routes on {provider_name}:"]
    for role, route in report["defaults"].items():
        label = f"  {ROLES[role][0]} ({ROLES[role][1]}): {route['model']}" if role in ROLES else f"  {role}"
        lines.append(f"{label}  " + (rates(route["rate"]) if route["rate"] else "no rate: its cost is unknown"))
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    """The arguments `autocode models` reads; `autocode completion` reads them too."""
    result = argparse.ArgumentParser(
        prog="autocode models",
        description="List the models your plans offer, grouped by plan and tier, "
        "and check the default route of every role; --prices shows what each model's tokens cost.",
    )
    result.add_argument("--provider", help="provider to list (default: the one new runs use)")
    result.add_argument("--workspace", type=Path, default=Path.cwd(), help="folder to run the provider in")
    result.add_argument("--json", action="store_true", help="print the result as JSON")
    result.add_argument(
        "--prices", action="store_true", help="print the dated price table and each default route's rate, then exit"
    )
    result.add_argument(
        "--selection",
        action="store_true",
        help="show what automatic selection would choose on this tool's connected accounts, "
        "and why each qualified model was not chosen (#1426)",
    )
    return result


def render_selection(result: dict, found: dict) -> str:
    """`autocode models --selection` as text: the chosen routes, and why the alternatives lost."""
    lines = [f"Automatic selection on {found['tool']}, from the accounts this tool reports:", ""]
    for account in found["accounts"]:
        billing = account["billing"] or "billing unknown"
        prefixes = ", ".join(account["prefixes"])
        lines.append(f"  login: {account['name']} ({account['mode']}, {billing}) serves {prefixes}")
    lines.append("")
    chosen = result["routes"]
    connected = {model_identity.identity(candidate["model"]): candidate for candidate in found["candidates"]}
    for role in route_selector.PIPELINE_ROLES:
        if role not in chosen:
            continue
        route = chosen[role]
        label = f"{ROLES[role][0]}" if role in ROLES else role
        level = route.get("reasoning_effort") or "model default"
        lines.append(f"{label}: {route['model']} on {route.get('account') or 'the tool'} ({level})")
        for entry in route_selector.qualified(role):
            spellings = {model_identity.identity(route_id) for route_id in entry.get("routes") or [entry["model"]]}
            candidate = next((connected.get(identity) for identity in spellings if identity in connected), None)
            if candidate is None:
                lines.append(f"  not available: {entry['model']} — not connected on this tool's logins")
                continue
            if candidate["model"] == route["model"]:
                row = prices.rate(candidate["model"])
                cost = f"input+output {row['input'] + row['output']:.4g} per million" if row else "no price row"
                billing = candidate.get("billing") or "billing unknown"
                lines.append(f"  chosen: {candidate['model']} — {cost}, {billing}")
                continue
            clash = route_selector.explains_clash(role, candidate["model"], chosen)
            if clash:
                lines.append(f"  not chosen: {candidate['model']} — {clash}")
            else:
                lines.append(f"  not chosen: {candidate['model']} — a preferred or cheaper qualified route won")
    if result["probe"]:
        lines.append("")
        lines.append(
            "Still to prove before the first stage (one tiny call each, within the cost cap): "
            + ", ".join(result["probe"])
        )
    return "\n".join(lines)


def preview(workspace=None, provider=None) -> dict:
    """The routes automatic selection would choose now, from the tool's own local listings (#2057).

    Listing only: discovery reads the tool's model and login lists and the selector is a pure
    function; nothing launches a model and no credential is returned. ``stop`` explains a
    selection that cannot field an independent checker; an unreadable listing raises the
    discovery error.
    """
    try:
        from . import autocode_providers
        from . import autocode_route_discovery as discovery
        from . import autocode_route_selector as route_selector
    except ImportError:
        import autocode_providers
        import autocode_route_discovery as discovery
        import autocode_route_selector as route_selector
    module = autocode_providers.resolve(provider or autocode_providers.default_name())
    found = discovery.discover(module, workspace)
    result = route_selector.select(route_selector.PIPELINE_ROLES, found["candidates"])
    return {**result, "tool": found["tool"], "accounts": found["accounts"], "candidates": found["candidates"]}


def route_rows(result: dict, roles=None) -> list[dict]:
    """One row per role the selection fills, for `connections` clients (#2057)."""
    if result.get("stop"):
        return []
    connected = {candidate["model"]: candidate for candidate in result.get("candidates") or ()}
    rows = []
    for role in route_selector.PIPELINE_ROLES:
        if roles is not None and role not in roles:
            continue
        route = (result.get("routes") or {}).get(role)
        if not route:
            continue
        candidate = connected.get(route["model"]) or {}
        rows.append(
            {
                "role": role,
                "job": ROLES[role][0],
                "model": route["model"],
                "tool": route.get("tool") or result.get("tool"),
                "account": route.get("account") or candidate.get("account"),
                "billing": candidate.get("billing"),
                "reasoning_effort": route.get("reasoning_effort"),
                "origin": route.get("origin"),
            }
        )
    return rows


def route_lines(result: dict, roles=None) -> list[str]:
    """The per-role line `connections` and a new run print: model, level and why (#2057)."""
    lines = []
    for row in route_rows(result, roles):
        label = row["job"]
        level = row["reasoning_effort"] or "its own default"
        account = row["account"] or row["tool"] or "the tool"
        billing = f" {row['billing']}" if row["billing"] else ""
        lines.append(f"{label}: {row['model']} at {level} ({account}{billing}; qualified for {label})")
    return lines


def cli(argv: list[str]) -> int:
    """`autocode models`: what your plans offer, and whether the default routes can be used."""
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.selection:
        # Local listings only: discovery reads the tool's model and login lists, and
        # the selector is a pure function; neither launches a model.
        try:
            found = preview(args.workspace, args.provider)
        except (RuntimeError, ValueError, OSError) as error:
            print(f"autocode models: {error}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({key: value for key, value in found.items() if key != "candidates"}, indent=2))
        elif found["stop"]:
            print(found["stop"])
        else:
            print(render_selection(found, found))
        return 1 if found["stop"] else 0
    if args.prices:
        # Offline: the table ships with AutoCode, and resolving the provider runs nothing.
        try:
            try:
                from . import autocode_providers
            except ImportError:
                import autocode_providers
            name = args.provider or autocode_providers.default_name()
            report = price_report(default_roles(autocode_providers.resolve(name)))
        except (RuntimeError, ValueError, OSError) as error:
            print(f"autocode models: {error}", file=sys.stderr)
            return 2
        print(json.dumps(report, indent=2) if args.json else render_prices(report, name))
        return 0
    try:
        try:
            from . import autocode_providers
        except ImportError:
            import autocode_providers
        name = args.provider or autocode_providers.default_name()
        provider = autocode_providers.resolve(name)
        available = provider.available_models(args.workspace)
        if available is None:
            raise RuntimeError(f"provider {name} does not list its models (no models or models_command)")
        routes = route_advice(provider, available, args.workspace)
        roles = routes["roles"]
        signed_in = getattr(provider, "openai_auth", None)
        openai_auth = (
            signed_in(args.workspace) if signed_in and any(m.startswith("openai/") for m in available) else None
        )
    except (RuntimeError, ValueError, OSError) as error:
        print(f"autocode models: {error}", file=sys.stderr)
        return 2
    advice = advise(roles, available, openai_auth=openai_auth)
    if args.json:
        print(json.dumps(advice, indent=2))
    elif advice["missing"]:
        print(render_advice(advice, getattr(provider, "NAME", "OpenCode")))
    else:
        lines = render_catalogue(advice) + [
            "Every default route can be used:"
            if routes["source"] == "profile"
            else "Every route a new run takes can be used:"
        ]
        lines += [f"  {ROLES[role][0]} ({ROLES[role][1]}): {route['model']}" for role, route in roles.items()]
        if routes["replaced"]:
            lines.append(
                "Automatic selection replaces the profile's route for: "
                + "; ".join(f"{ROLES[role][0]} {seed} -> {taken}" for role, (seed, taken) in routes["replaced"].items())
            )
        print("\n".join(lines))
    return 1 if advice["missing"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Search available models and present role suggestions.")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    bundle = discover_and_suggest(args.workspace)
    text = render(bundle["usable"], bundle["roles"])
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(bundle, indent=2))
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

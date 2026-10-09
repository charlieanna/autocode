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
    from . import autocode_roles as roles
except ImportError:
    import autocode_roles as roles

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
            return {role: {"model": cfg["model"], "effort": cfg.get("effort"), "source": "pinned"}
                    for role, cfg in pins["roles"].items()}
    return suggest(models)


def list_catalogue(workspace: Path | None = None, command=("opencode", "models")) -> list[str]:
    result = subprocess.run(list(command), cwd=str(workspace) if workspace else None,
                            capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError(f"model catalogue failed ({result.returncode}): "
                           f"{(result.stderr or result.stdout)[:300]}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def usable(models: list[str]) -> list[str]:
    """All well-formed provider routes, without model or provider blacklists."""
    return sorted({m for m in models if isinstance(m, str) and "/" in m
                   and all(m.split("/", 1)) and not any(c.isspace() for c in m)})


def family(model: str) -> str:
    lineage_name = lineage(model)
    return lineage_name if lineage_name in ("glm", "mimo") else model.split("/", 1)[0]


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
        roles[role] = {"model": model, "effort": effort,
                       "source": "preferred" if model == pref else "catalogue-fallback"}
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
        lines.append(f"| {role} | `{cfg.get('model') or '—'}` | {cfg.get('effort') or '—'} | {cfg.get('source') or '—'} |")
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
TIERS = {"glm-5.3": "worker", "gpt-6-luna": "worker", "gpt-5.6-terra": "worker",
         "gpt-6-sol": "judge", "gpt-5.6-sol": "judge", "gpt-6-astra": "resolver"}
TIER_LABELS = {"worker": "cheap worker", "judge": "strong judge", "resolver": "Resolver tier",
               None: "tier unknown"}
WANTED = {"worker": "cheap worker", "judge": "strong judge", "resolver": "Resolver-tier model"}
# How a provider prefix bills. OpenAI depends on how OpenCode signs in (see ``plan``).
PLANS = {"zai-coding-plan": ("Z.AI Coding Plan", "subscription"),
         "github-copilot": ("GitHub Copilot", "subscription"),
         "zai": ("Z.AI API", "pay per token"), "opencode": ("OpenCode Zen", "pay per token"),
         "kilo": ("Kilo Gateway", "pay per token")}
# A run's roles in pipeline order: screen name (autocode_roles), the flag that
# selects its model, the tier it wants. Labels are not a second name table.
ROLES = {"requirements": (roles.SCREEN["requirements"], "--requirements-model", "worker"),
         "glm": (roles.SCREEN["planner"], "--glm-model", "worker"),
         "terra": (roles.SCREEN["builder"], "--terra-model", "worker"),
         "plan_reviewer": (roles.SCREEN["plan_reviewer"], "--plan-reviewer-model", "judge"),
         "sol": (roles.SCREEN["tester"], "--sol-model", "judge"),
         "completion": (roles.SCREEN["completion"], "--completion-model", "judge"),
         "astra": (roles.SCREEN["resolver"], "--astra-model", "resolver")}
# Producer and checker roles that must not share a model (autocode_dispatch._VERIFIER_PAIRS).
CHECKS = (("glm", "plan_reviewer"), ("terra", "sol"), ("terra", "completion"))
# Preference order: billing first (never nudge a subscription user onto per-token billing),
# then the tier the role wants.
BILLING_ORDER = ("subscription", None, "pay per token")
TIER_ORDER = {"worker": ("worker", None, "judge", "resolver"), "judge": ("judge", None, "worker", "resolver"),
              "resolver": ("resolver", "judge", None, "worker")}


def tier(model: str) -> str | None:
    return TIERS.get(model.rsplit("/", 1)[-1])


def plan(model: str, openai_auth: str | None = None) -> tuple[str, str | None]:
    """(plan, billing) for display; authentication never bans a model."""
    provider = model.split("/", 1)[0]
    if provider != "openai":
        return PLANS.get(provider, (provider, None))
    if openai_auth == "oauth":
        return "ChatGPT login", "subscription"
    if openai_auth == "api":
        return "OpenAI via api", "pay per token"
    return "OpenAI", None


def lineage(model: str) -> str:
    """The family autocode_dispatch compares: GLM, MiMo, or the model itself (GPT tiers are independent)."""
    name = model.rsplit("/", 1)[-1]
    if model.startswith("zai-coding-plan/") or name.startswith("glm-"):
        return "glm"
    if model.startswith("xiaomi-token-plan-sgp/") or name.startswith("mimo-"):
        return "mimo"
    return model


def independent(producer: str, checker: str) -> bool:
    """autocode_dispatch's rule: never the same model, and GLM or MiMo never checks its own family."""
    return producer != checker and not (lineage(producer) in ("glm", "mimo") and lineage(producer) == lineage(checker))


def advise(roles: dict, available, *, openai_auth: str | None = None, refused_missing: bool = True) -> dict:
    """Report unlisted routes and suggest independent replacements from the catalogue.

    Billing and tier guide suggestions; neither excludes a configured model.
    ``refused_missing`` remains accepted for caller compatibility.
    """
    available = set(available)
    routes = {role: route["model"] for role, route in roles.items() if role in ROLES and route.get("model")}
    missing = {role: model for role, model in routes.items()
               if model not in available}
    chosen = {role: model for role, model in routes.items() if role not in missing}
    candidates = usable(sorted(available))
    suggestions = {}
    for role, (_label, _flag, want) in ROLES.items():
        if role not in missing:
            continue
        others = [chosen[other] for pair in CHECKS if role in pair for other in pair
                  if other != role and other in chosen]
        allowed = [m for m in candidates if all(independent(m, o) for o in others)]
        pick = min(allowed, default=None, key=lambda m: (
            BILLING_ORDER.index(plan(m, openai_auth)[1]), TIER_ORDER[want].index(tier(m)),
            family(m) in {family(o) for o in others}, m not in chosen.values(), m))
        if pick is None:
            why = ("nothing you can use keeps it independent of " + " and ".join(others)) if others else "nothing you can use"
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
    catalogue = [{"model": m, "plan": plan(m, openai_auth)[0], "billing": plan(m, openai_auth)[1],
                  "tier": tier(m), "defaults": defaults.get(m, [])} for m in kept]
    flags = [part for role in ROLES if (suggestions.get(role) or {}).get("model")
             for part in (ROLES[role][1], suggestions[role]["model"])]
    hidden = [m for m in available if "/" in m and not any(c.isspace() for c in m) and m not in kept]
    return {"missing": missing, "suggestions": suggestions, "flags": flags,
            "catalogue": catalogue, "hidden": len(hidden)}


def render_catalogue(advice: dict) -> list[str]:
    lines = ["Models your plans offer. Cheap workers plan and build; strong judges check.", ""]
    groups = defaultdict(list)
    for entry in advice["catalogue"]:
        groups[(entry["plan"], entry["billing"])].append(entry)
    width = max((len(entry["model"]) for entry in advice["catalogue"]), default=0)
    billing_text = {"subscription": "subscription", "pay per token": "pay per token", None: "billing unknown",
                    "refused": "billing unknown"}
    for (name, billing), entries in sorted(groups.items(), key=lambda item: (
            BILLING_ORDER.index(item[0][1]) if item[0][1] in BILLING_ORDER else len(BILLING_ORDER), item[0][0])):
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
    lines = [f"Cannot use with {provider}: " + "; ".join(f"{m} ({', '.join(labels)})" for m, labels in by_model.items())
             + ".", ""]
    lines += render_catalogue(advice)
    lines.append("Suggested replacements:")
    for role, suggestion in advice["suggestions"].items():
        label, flag, _want = ROLES[role]
        target = suggestion["model"] or f"choose one from the list with {flag}"
        lines.append(f"  {label} ({flag}): {advice['missing'][role]} → {target}. {suggestion['why'][:1].upper()}{suggestion['why'][1:]}.")
    if advice["flags"]:
        lines += ["", "To use them, start the run with:", "  " + " ".join(advice["flags"])]
        per_token = sorted({s["model"] for s in advice["suggestions"].values()
                            if s["model"] and plan(s["model"])[1] == "pay per token"})
        if per_token:
            lines.append("  " + ", ".join(per_token) + (" bills" if len(per_token) == 1 else " bill") + " per token, not by subscription.")
    lines += ["", "Nothing was launched. AutoCode never changes a model without you."]
    return "\n".join(lines)


def choose(settings: dict, provider, workspace, *, interactive: bool, ask=input, out=print) -> dict:
    """New runs: stop before any model call when a role's model cannot be used.

    Checks the OpenCode-shaped routes against the provider's own list: a saved run could
    never use an unlisted model, since resuming keeps the saved routes. A provider that
    cannot list its models starts the run as before.
    In chat the user may accept every replacement, which is written into the routes as
    --<role>-model flags would be; otherwise the run stops with the list and the flags.
    """
    roles = {role: route for role, route in settings.get("roles", {}).items()
             if role in ROLES and (route.get("engine") or settings.get("engine")) == "opencode"}
    lister = getattr(provider, "available_models", None)
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
        return settings
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
    return settings


def default_roles(provider) -> dict[str, dict]:
    """The route every role of a new run takes on ``provider``: what `autocode models` and doctor check."""
    return {role: {"model": model} for role, model in provider.DEFAULT_MODELS.items() if role in ROLES}


def cli(argv: list[str]) -> int:
    """`autocode models`: what your plans offer, and whether the default routes can be used."""
    parser = argparse.ArgumentParser(prog="autocode models",
                                     description="List the models your plans offer, grouped by plan and tier, "
                                                 "and check the default route of every role.")
    parser.add_argument("--provider", help="provider to list (default: the one new runs use)")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="folder to run the provider in")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)
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
        roles = default_roles(provider)
        signed_in = getattr(provider, "openai_auth", None)
        openai_auth = signed_in(args.workspace) if signed_in and any(m.startswith("openai/") for m in available) else None
    except (RuntimeError, ValueError, OSError) as error:
        print(f"autocode models: {error}", file=sys.stderr)
        return 2
    advice = advise(roles, available, openai_auth=openai_auth)
    if args.json:
        print(json.dumps(advice, indent=2))
    elif advice["missing"]:
        print(render_advice(advice, getattr(provider, "NAME", "OpenCode")))
    else:
        lines = render_catalogue(advice) + ["Every default route can be used:"]
        lines += [f"  {ROLES[role][0]} ({ROLES[role][1]}): {route['model']}" for role, route in roles.items()]
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

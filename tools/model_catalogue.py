#!/usr/bin/env python3
"""Discover available models and present role suggestions to the operator.

Selection is never a hardcoded guess: we search the live `opencode models`
catalogue, drop free/flash routes, then suggest a verifier≠producer map and
print the shortlist for the user to accept or override.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

# User rule (2026-09-26): subscription models only — never free-tier or flash.
FORBIDDEN_SUBSTRINGS = ("-free", "flash", "highspeed")
DEAD_ROUTES = ("mimo-token-plan/",)  # Invalid API key on this machine

# Ladder entry points when both families are present (docs/models.md).
PREFERRED = {
    "requirements": ("zai-coding-plan/glm-5.3", "medium"),
    "planner": ("zai-coding-plan/glm-5.3", "high"),
    "reviewer": ("xiaomi-token-plan-sgp/mimo-v2.6-pro", "high"),
    "builder": ("xiaomi-token-plan-sgp/mimo-v2.6-pro", "medium"),
    "validator": ("zai-coding-plan/glm-5.3", "high"),
    "completion": ("zai-coding-plan/glm-5.3", "medium"),
    "resolver": ("xiaomi-token-plan-sgp/mimo-v2.6-pro", "high"),
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
    out = []
    for m in models:
        low = m.lower()
        if any(bad in low for bad in FORBIDDEN_SUBSTRINGS):
            continue
        if any(m.startswith(p) for p in DEAD_ROUTES):
            continue
        if " " in m or "/" not in m:
            continue
        out.append(m)
    return sorted(set(out))


def family(model: str) -> str:
    if model.startswith("zai-coding-plan/"):
        return "glm"
    if model.startswith("xiaomi-token-plan-sgp/") or model.startswith("mimo-"):
        return "mimo"
    return model.split("/", 1)[0]


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
    lines = ["# Available subscription models", ""]
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

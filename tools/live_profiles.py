"""Pinned model profiles for live trials.

A profile is part of the evidence: two bundles are comparable only when their
profile fields match, or when the profile is the variable under test. Keep the
2026-09-24 GLM 5.3 record reproducible under its own profile name.
"""
from __future__ import annotations

# Effort keys are semantic roles, not stage ids: the driver maps stages to these
# so a profile stays valid when internal stage names change.
EFFORT_ROLES = ("requirements", "planner", "reviewer", "builder", "validator",
                "resolver", "completion")

PROFILES = {
    # Baseline from audits/autopilot-test-catalogue/LIVE_TRIALS.md (2026-09-24).
    "glm53": {
        "provider": "kilocode",
        "base": "zai-coding-plan/glm-5.3",
        "effort": {"planner": "max", "reviewer": "high", "completion": "high",
                   "builder": "low", "requirements": "low", "resolver": "high"},
    },
    # Stronger reviewer route: the named mitigation for finding L3.
    "sol-hi": {
        "provider": "opencode",
        "base": "openai/gpt-5.6-sol",
        "effort": {"planner": "high", "reviewer": "high", "completion": "high",
                   "builder": "medium", "requirements": "low", "resolver": "high"},
    },
    # Offline scripted provider. No model spend; proves the harness before live runs.
    "fixture": {
        "provider": "fixture",
        "base": "fixture/live-fixture",
        "effort": {role: "none" for role in EFFORT_ROLES},
    },
    # Project's own OpenCode defaults (gpt-5.6-sol / gpt-5.6-terra). No flags
    # are passed, so the runner's DEFAULT_ROLE_MODELS apply. Best first live run.
    "default": {
        "provider": "opencode",
        "base": "runner-defaults",
        "effort": {role: "none" for role in EFFORT_ROLES},
        "passthrough": True,
    },
    # Provisioned OpenCode models confirmed to respond (2026-09-25).
    # cursor-acp named models are plan-gated; "auto" is the free-plan route and
    # returned exact structured JSON in the connectivity probe.
    "cursor-auto": {
        "provider": "opencode",
        "base": "cursor-acp/auto",
        "effort": {role: "none" for role in EFFORT_ROLES},
    },
    # Subscription-only. Two hard rules (user 2026-09-26):
    # 1) Verifier ≠ producer — MiMo checks GLM work and GLM checks MiMo work.
    # 2) Start at the ladder's medium rung where it says medium; shift to higher
    #    reasoning inside the stage when evidence shows struggle.
    # Never free-tier or flash. `mimo-token-plan/` is dead (Invalid API key).
    "glm53-mimo": {
        "provider": "opencode",
        "role_models": {
            "requirements": "zai-coding-plan/glm-5.3",
            "planner": "zai-coding-plan/glm-5.3",
            "reviewer": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "builder": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "validator": "zai-coding-plan/glm-5.3",
            "completion": "zai-coding-plan/glm-5.3",
            "resolver": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
        },
        "effort": {
            "requirements": "medium",
            "planner": "high",
            "reviewer": "high",
            "builder": "medium",
            "validator": "high",
            "completion": "medium",
            "resolver": "high",
        },
    },
}


def resolve(name: str) -> dict:
    try:
        return dict(PROFILES[name])
    except KeyError:
        known = ", ".join(sorted(PROFILES))
        raise ValueError(f"unknown live profile {name!r}; choose one of: {known}") from None


def model_for(profile: dict, role: str) -> str:
    """Provider/model id for a semantic role."""
    if profile.get("role_models"):
        try:
            return profile["role_models"][role]
        except KeyError:
            raise ValueError(f"profile has no model for role {role!r}") from None
    return profile["base"]


def effort_for(profile: dict, role: str) -> str:
    return profile.get("effort", {}).get(role, "medium")


def describe(profile: dict) -> str:
    """One-line model description for evidence bundles."""
    if profile.get("role_models"):
        return "; ".join(f"{role}={model}" for role, model in profile["role_models"].items())
    return profile.get("base", "runner-defaults")


def cli_overrides(profile: dict) -> list[str]:
    """CLI flags that pin every role to this profile."""
    if profile["provider"] == "fixture":
        return []
    if profile.get("passthrough"):
        # Runner defaults: pass no model/effort flags at all.
        return []
    flags: list[str] = []
    # Stage/role flags kept under their legacy names (see docs/models.md).
    role_flags = {
        "requirements": "--requirements-model",
        "planner": "--glm-model",
        "reviewer": "--plan-reviewer-model",
        "builder": "--terra-model",
        "validator": "--sol-model",
        "resolver": "--astra-model",
        "completion": "--completion-model",
    }
    effort_flags = {
        "requirements": "--requirements-reasoning-effort",
        "planner": "--glm-reasoning-effort",
        "reviewer": "--plan-reviewer-reasoning-effort",
        "builder": "--reasoning-effort",
        "validator": "--sol-reasoning-effort",
        "resolver": "--astra-reasoning-effort",
        "completion": "--completion-reasoning-effort",
    }
    for role, flag in role_flags.items():
        flags.extend([flag, model_for(profile, role)])
        effort = effort_for(profile, role)
        if effort and effort != "none":
            flags.extend([effort_flags[role], effort])
    return flags

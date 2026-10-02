"""Model profiles for live runs.

A profile is part of the evidence: two results are comparable only when their
profiles match, or when the profile is the variable under test.
"""
from __future__ import annotations

ROLES = ("requirements", "planner", "reviewer", "builder", "validator", "resolver", "completion")

PROFILES = {
    # Bounded qualification using the same OAuth models as the September 29
    # smoke test. Pin execution roles and the Investigator: recovery must not
    # move a checker to GLM or use Kilo's non-OpenAI Investigator default.
    "codex-only": {
        "provider": "opencode",
        "models": {
            "requirements": "openai/gpt-5.6-terra", "planner": "openai/gpt-5.6-terra",
            "reviewer": "openai/gpt-5.6-sol", "builder": "openai/gpt-5.6-terra",
            "validator": "openai/gpt-5.6-sol", "completion": "openai/gpt-5.6-sol",
            "resolver": "openai/gpt-5.6-sol",
        },
        "effort": {role: "medium" for role in ROLES},
        "extra": ["--engine", "opencode", "--investigator-model", "openai/gpt-5.6-sol",
                  "--resolver-model", "openai/gpt-5.6-sol", "--max-parallel-builders", "1",
                  "--pin-model-role", "astra", "--pin-model-role", "terra",
                  "--pin-model-role", "sol", "--pin-model-role", "completion"],
    },
    # Runner defaults: no model flags, so AutoCode's DEFAULT_ROLE_MODELS apply.
    "default": {"provider": "opencode", "passthrough": True},
    "glm53": {
        "provider": "kilocode",
        "models": {role: "zai-coding-plan/glm-5.3" for role in ROLES},
        "effort": {"planner": "max", "reviewer": "high", "completion": "high",
                   "builder": "low", "requirements": "low", "resolver": "high"},
    },
    # Verifier differs from producer: OpenAI GPT checks GLM work and GLM checks GPT work.
    # MiMo is never used (user 2026-09-27): it twice spent its whole reasoning budget on
    # a design review and returned nothing. OpenAI models go through the ChatGPT login.
    "glm53-openai": {
        "provider": "opencode",
        "models": {
            "requirements": "zai-coding-plan/glm-5.3", "planner": "zai-coding-plan/glm-5.3",
            "reviewer": "openai/gpt-6-sol", "builder": "openai/gpt-6-sol",
            "validator": "zai-coding-plan/glm-5.3", "completion": "zai-coding-plan/glm-5.3",
            "resolver": "openai/gpt-6-astra",
        },
        "effort": {"requirements": "medium", "planner": "high", "reviewer": "high", "builder": "medium",
                   "validator": "high", "completion": "medium", "resolver": "high"},
    },
    # Every role on OpenAI via the ChatGPT login (user 2026-09-27, while the Z.AI plan
    # was out of quota). Each verifier is a different model from its producer
    # (planner astra / plan reviewer sol; builder sol / validator astra / completion
    # luna), which AutoCode's cross-model check requires; the independence is weaker
    # than across vendors, so results are comparable only with other openai-only runs.
    "openai-only": {
        "provider": "opencode",
        "models": {
            "requirements": "openai/gpt-6-luna", "planner": "openai/gpt-6-astra",
            "reviewer": "openai/gpt-6-sol", "builder": "openai/gpt-6-sol",
            "validator": "openai/gpt-6-astra", "completion": "openai/gpt-6-luna",
            "resolver": "openai/gpt-6-astra",
        },
        "effort": {"requirements": "medium", "planner": "high", "reviewer": "high", "builder": "medium",
                   "validator": "high", "completion": "medium", "resolver": "high"},
    },
}

# AutoCode's CLI still names these flags after the internal stage names (see docs/models.md).
MODEL_FLAGS = {
    "requirements": "--requirements-model", "planner": "--glm-model", "reviewer": "--plan-reviewer-model",
    "builder": "--terra-model", "validator": "--sol-model", "resolver": "--astra-model",
    "completion": "--completion-model",
}
EFFORT_FLAGS = {
    "requirements": "--requirements-reasoning-effort", "planner": "--glm-reasoning-effort",
    "reviewer": "--plan-reviewer-reasoning-effort", "builder": "--reasoning-effort",
    "validator": "--sol-reasoning-effort", "resolver": "--astra-reasoning-effort",
    "completion": "--completion-reasoning-effort",
}


def resolve(name: str) -> dict:
    if name not in PROFILES:
        raise ValueError(f"unknown profile {name!r}; choose one of: {', '.join(sorted(PROFILES))}")
    return PROFILES[name]


def flags(profile: dict) -> list[str]:
    result = ["--provider", profile["provider"], "--joint-planning", *profile.get("extra", [])]
    if profile.get("passthrough"):
        return result
    for role in ROLES:
        result += [MODEL_FLAGS[role], profile["models"][role]]
        if profile.get("effort", {}).get(role):
            result += [EFFORT_FLAGS[role], profile["effort"][role]]
    return result

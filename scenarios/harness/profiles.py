"""Model profiles for live runs.

A profile is part of the evidence: two results are comparable only when their
profiles match, or when the profile is the variable under test.
"""

from __future__ import annotations

ROLES = ("requirements", "planner", "reviewer", "builder", "validator", "resolver", "completion")

PROFILES = {
    # Freeze the 2026-10-02 production defaults for paired full-build tests.
    # Both arms keep normal recovery/escalation and independent verification.
    "build-comparison": {
        "provider": "opencode",
        "models": {
            "requirements": "zai-coding-plan/glm-5.3",
            "planner": "zai-coding-plan/glm-5.3",
            "reviewer": "openai/gpt-6-sol",
            "builder": "zai-coding-plan/glm-5.3",
            "validator": "openai/gpt-6-sol",
            "completion": "openai/gpt-6-sol",
            "resolver": "openai/gpt-6-astra",
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
        "extra": ["--resolver-model", "openai/gpt-6-astra", "--resolver-reasoning-effort", "high"],
    },
    # Bounded qualification using the same OAuth models as the September 29
    # smoke test. Pin execution roles and the Investigator: recovery must not
    # move a checker to GLM or use Kilo's non-OpenAI Investigator default.
    "codex-only": {
        "provider": "opencode",
        "models": {
            "requirements": "openai/gpt-5.6-terra",
            "planner": "openai/gpt-5.6-terra",
            "reviewer": "openai/gpt-5.6-sol",
            "builder": "openai/gpt-5.6-terra",
            "validator": "openai/gpt-5.6-sol",
            "completion": "openai/gpt-5.6-sol",
            "resolver": "openai/gpt-5.6-sol",
        },
        "effort": {role: "medium" for role in ROLES},
        "extra": [
            "--engine",
            "opencode",
            "--investigator-model",
            "openai/gpt-5.6-sol",
            "--resolver-model",
            "openai/gpt-5.6-sol",
            "--max-parallel-builders",
            "1",
            "--pin-model-role",
            "astra",
            "--pin-model-role",
            "terra",
            "--pin-model-role",
            "sol",
            "--pin-model-role",
            "completion",
        ],
    },
    # Explicit bounded live visual-repair route matching the approved Native20 role models.
    # This fixture retains its own shorter time and call limits; it is not Native20 acceptance.
    "native20-ui-repair": {
        "provider": "opencode",
        "models": {
            "requirements": "zai-coding-plan/glm-5.3-flash",
            "planner": "zai-coding-plan/glm-5.3",
            "reviewer": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "builder": "zai-coding-plan/glm-5.3",
            "validator": "openai/gpt-6-sol",
            "completion": "openai/gpt-6-sol",
            "resolver": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
        },
        "effort": {role: "high" for role in ROLES},
        "extra": [
            "--engine",
            "opencode",
            "--resolver-model",
            "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "--resolver-reasoning-effort",
            "high",
            "--pin-model-role",
            "astra",
            "--pin-model-role",
            "terra",
            "--pin-model-role",
            "sol",
            "--pin-model-role",
            "completion",
        ],
    },
    # Runner defaults: no pinned model flags; new runs select from connected accounts.
    "default": {"provider": "opencode", "passthrough": True},
    "glm53": {
        "provider": "kilocode",
        "models": {role: "zai-coding-plan/glm-5.3" for role in ROLES},
        "effort": {
            "planner": "max",
            "reviewer": "high",
            "completion": "high",
            "builder": "low",
            "requirements": "low",
            "resolver": "high",
        },
    },
    # Verifier differs from producer: OpenAI GPT checks GLM work and GLM checks GPT work.
    # This profile pairs GLM with OpenAI; it is not a restriction on other profiles.
    # OpenAI models go through the ChatGPT login.
    "glm53-openai": {
        "provider": "opencode",
        "models": {
            "requirements": "zai-coding-plan/glm-5.3",
            "planner": "zai-coding-plan/glm-5.3",
            "reviewer": "openai/gpt-6-sol",
            "builder": "openai/gpt-6-sol",
            "validator": "zai-coding-plan/glm-5.3",
            "completion": "zai-coding-plan/glm-5.3",
            "resolver": "openai/gpt-6-astra",
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
    # GLM and MiMo only, through OpenCode: one way to make the live run before a pull request (AGENTS.md).
    # Same routes as the trial harness's 2026-09-26 glm53-mimo profile.
    # Each checker is the other family from its producer (GLM plans, MiMo reviews the plan;
    # MiMo builds, GLM validates and completes), as AutoCode's cross-model check requires.
    # The Investigator and Resolver are pinned so recovery does not route to OpenAI.
    # MiMo reasons silently for long stretches: keep --max-stage-seconds at 900 or more.
    "glm53-mimo": {
        "provider": "opencode",
        "models": {
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
        "extra": [
            "--resolver-model",
            "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "--resolver-reasoning-effort",
            "high",
            "--investigator-model",
            "zai-coding-plan/glm-5.3",
        ],
    },
    # GLM and MiMo through the Kilo Code provider only: the same family split as glm53-mimo
    # (GLM plans and validates, MiMo reviews the plan and builds). GLM uses the Z.AI Coding
    # Plan route and MiMo the Xiaomi Token Plan route. kilo/xiaomi/... is not used: it bills the
    # Kilo Gateway and fails with HTTP 402 without prepaid credits.
    "glm-mimo-kilo": {
        "provider": "kilocode",
        "models": {
            "requirements": "zai-coding-plan/glm-5.3",
            "planner": "zai-coding-plan/glm-5.3",
            "reviewer": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "builder": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "validator": "zai-coding-plan/glm-5.3",
            "completion": "zai-coding-plan/glm-5.3",
            "resolver": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
        },
        # Match the bundled Kilo GLM default: its native variants exclude medium.
        "effort": {
            "requirements": "high",
            "planner": "high",
            "reviewer": "high",
            "builder": "medium",
            "validator": "high",
            "completion": "high",
            "resolver": "high",
        },
        "extra": [
            "--resolver-model",
            "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "--resolver-reasoning-effort",
            "high",
            "--investigator-model",
            "zai-coding-plan/glm-5.3",
        ],
    },
    # Every role on OpenAI via the ChatGPT login (user 2026-09-27, while the Z.AI plan
    # was out of quota). Each verifier is a different model from its producer
    # (planner astra / plan reviewer sol; builder sol / validator astra / completion
    # luna), which AutoCode's cross-model check requires; the independence is weaker
    # than across vendors, so results are comparable only with other openai-only runs.
    "openai-only": {
        "provider": "opencode",
        "models": {
            "requirements": "openai/gpt-6-luna",
            "planner": "openai/gpt-6-astra",
            "reviewer": "openai/gpt-6-sol",
            "builder": "openai/gpt-6-sol",
            "validator": "openai/gpt-6-astra",
            "completion": "openai/gpt-6-luna",
            "resolver": "openai/gpt-6-astra",
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
    # GoCode cross-model scorecard (RELIABILITY.md live cases): Luna drafts requirements
    # and the plan; Terra builds; Sol reviews, validates and completes; Astra resolves.
    # The gocode provider sets its own child env; strip OPENAI_API_KEY / CODEX_API_KEY /
    # OPENAI_BASE_URL from the AutoCode process (docs/providers.md, "GoCode").
    "gocode-cross": {
        "provider": "gocode",
        "models": {
            "requirements": "gpt-5.6-luna",
            "planner": "gpt-5.6-luna",
            "reviewer": "gpt-5.6-sol",
            "builder": "gpt-5.6-terra",
            "validator": "gpt-5.6-sol",
            "completion": "gpt-5.6-sol",
            "resolver": "gpt-6-astra",
        },
        "effort": {role: "medium" for role in ROLES},
        "extra": [
            "--max-stage-seconds",
            "1200",
            "--investigator-model",
            "gpt-5.6-sol",
            "--resolver-model",
            "gpt-6-astra",
        ],
    },
    # Claude Code tiers (RELIABILITY.md live cases) on the built-in claude provider (tools/providers/claude.py,
    # #717): Sonnet drafts requirements and the plan and validates; Opus reviews the plan, resolves and completes;
    # Haiku builds. No token cap: Claude counts cache reads as input, so a 6M cap stopped runs that had cost $4;
    # the guard rails are --max-stage-seconds and the harness timeout. The Investigator pin equals today's
    # default (the Plan Reviewer's model) and keeps recovery on the Claude login.
    "claude-tiers": {
        "provider": "claude",
        "models": {
            "requirements": "claude-sonnet-5-5",
            "planner": "claude-sonnet-5-5",
            "reviewer": "claude-opus-5-5",
            "builder": "claude-haiku-4-5-20251001",
            "validator": "claude-sonnet-5-5",
            "resolver": "claude-opus-5-5",
            "completion": "claude-opus-5-5",
        },
        "effort": {role: "medium" for role in ROLES},
        "extra": ["--max-stage-seconds", "1200", "--investigator-model", "claude-opus-5-5"],
    },
}

# AutoCode's CLI still names these flags after the internal stage names (see docs/models.md).
MODEL_FLAGS = {
    "requirements": "--requirements-model",
    "planner": "--glm-model",
    "reviewer": "--plan-reviewer-model",
    "builder": "--terra-model",
    "validator": "--sol-model",
    "resolver": "--astra-model",
    "completion": "--completion-model",
}
EFFORT_FLAGS = {
    "requirements": "--requirements-reasoning-effort",
    "planner": "--glm-reasoning-effort",
    "reviewer": "--plan-reviewer-reasoning-effort",
    "builder": "--reasoning-effort",
    "validator": "--sol-reasoning-effort",
    "resolver": "--astra-reasoning-effort",
    "completion": "--completion-reasoning-effort",
}


def resolve(name: str) -> dict:
    if name not in PROFILES:
        raise ValueError(f"unknown profile {name!r}; choose one of: {', '.join(sorted(PROFILES))}")
    return PROFILES[name]


def with_provider(profile: dict, provider: str | None) -> dict:
    """The same routes through another provider (``--provider``): any tool AutoCode can drive."""
    return {**profile, "provider": provider} if provider else profile


def flags(profile: dict) -> list[str]:
    result = ["--provider", profile["provider"], "--joint-planning", *profile.get("extra", [])]
    if profile.get("passthrough"):
        return result
    for role in ROLES:
        result += [MODEL_FLAGS[role], profile["models"][role]]
        if profile.get("effort", {}).get(role):
            result += [EFFORT_FLAGS[role], profile["effort"][role]]
    return result

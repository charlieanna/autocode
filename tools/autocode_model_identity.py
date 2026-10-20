"""Cycle-free model identity shared by route policies."""

from __future__ import annotations


def identity(model):
    """The model and its vendor, without a gateway or a native engine's spelling in front of them.

    One model reached two ways is one model, so neither route may check the other's work:
    ``kilo/anthropic/claude-opus-5.5`` (the Kilo Gateway) and ``anthropic/claude-opus-5.5``
    (a direct login) run the same weights. A route that reaches another vendor's model
    therefore loses its leading segment; a segment that *is* the vendor keeps the model
    distinct, so ``custom/gpt-6-sol`` is not ``gpt-6-sol``.
    """
    if not isinstance(model, str) or not model:
        return ""
    parts = model.split("/")
    if len(parts) > 2:
        parts = parts[-2:]
    name = "/".join(parts)
    if name.startswith("openai/gpt-"):
        # Native Codex and OpenCode spell the same exact GPT tier differently, and a gateway
        # in front of that login does not make it a different model.
        name = name.removeprefix("openai/")
    return name.strip().lower()


def family(model):
    """GLM, MiMo and Claude each share a family; GPT tiers and other model IDs stay distinct."""
    if not isinstance(model, str) or not model:
        return ""
    name = identity(model).rsplit("/", 1)[-1]
    if model.startswith("zai-coding-plan/") or name.startswith("glm-"):
        return "glm"
    if model.startswith("xiaomi-token-plan-sgp/") or name.startswith("mimo-"):
        return "mimo"
    if name.startswith("claude-"):
        return "claude"
    return model


def shared_family(left, right):
    """Recognize the verifier policy's families and one model billed through two routes."""
    if not isinstance(left, str) or not left or not isinstance(right, str) or not right:
        return False
    left_id, right_id = identity(left), identity(right)
    if left_id and left_id == right_id:
        return True
    return family(left) in ("glm", "mimo", "claude") and family(left) == family(right)

"""Cycle-free model identity shared by route policies."""

from __future__ import annotations


def family(model):
    """GLM/MiMo share a family; GPT tiers and other model IDs stay distinct."""
    if not isinstance(model, str) or not model:
        return ""
    name = model.rsplit("/", 1)[-1].lower()
    if model.startswith("zai-coding-plan/") or name.startswith("glm-"):
        return "glm"
    if model.startswith("xiaomi-token-plan-sgp/") or name.startswith("mimo-"):
        return "mimo"
    return model


def shared_family(left, right):
    """Recognize the verifier policy's families and exact native/OpenAI GPT aliases."""
    if not isinstance(left, str) or not left or not isinstance(right, str) or not right:
        return False
    left_id, right_id = (
        model.removeprefix("openai/") if model.startswith("openai/gpt-") else model for model in (left, right)
    )
    return left_id == right_id or family(left) in ("glm", "mimo") and family(left) == family(right)

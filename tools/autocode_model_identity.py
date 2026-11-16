"""Cycle-free model identity shared by route policies."""

from __future__ import annotations

import re


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


# The model lines whose name says which lab trained them (owner decision 2026-09-26 for GLM and
# MiMo, 2026-10-20 for GPT; the rest 2026-11-16, found when a provider listed Gemini, Kimi, Grok,
# Qwen and others beside Claude and GPT). Models of one family share training and judging biases,
# so one never checks another's work. Nothing a listing reports names the lab, so the name is read:
# each pattern matches the start of the model's own name, after any gateway or vendor segment and
# after a Bedrock-style "vendor." prefix ("meta.llama3-70b"). GPT is matched apart (below), because
# another vendor's segment in front of a gpt-* name keeps it distinct.
FAMILY_NAMES = (
    ("glm", re.compile(r"^glm[-.\d]")),
    ("mimo", re.compile(r"^mimo[-.\d]")),
    ("claude", re.compile(r"^claude[-.\d]")),
    ("gemini", re.compile(r"^(?:gemini|gemma)[-.\d]")),
    ("kimi", re.compile(r"^(?:kimi|moonshot)[-.\d]")),
    ("grok", re.compile(r"^grok[-.\d]")),
    ("qwen", re.compile(r"^(?:qwen|qwq)[-.\d]")),
    ("deepseek", re.compile(r"^deepseek[-.\d]")),
    ("llama", re.compile(r"^llama[-.\d:]")),
    ("mistral", re.compile(r"^(?:mistral|mixtral|codestral|devstral|magistral|ministral|pixtral)[-.\d]")),
    ("minimax", re.compile(r"^minimax[-.\d]")),
)
FAMILIES = ("openai", *(name for name, _pattern in FAMILY_NAMES))
# A gateway's router ("auto", "auto-frontier") picks the model per request, so nothing can show which
# family answered (#3630). Lists that offer models to measure or choose leave it out.
_ROUTER = re.compile(r"^auto(?:$|-)")
_BEDROCK_VENDOR = re.compile(r"^[a-z]+\.(?=[a-z])")


def family(model):
    """The family of a model from a known lab (``FAMILIES``); any other model ID stays distinct.

    GPT tiers joined on 2026-10-20 (owner decision): Luna, Sol and Astra share training and judging
    biases like any other family, so one never checks another's work outside a GPT-only engine."""
    if not isinstance(model, str) or not model:
        return ""
    name = identity(model).rsplit("/", 1)[-1]
    if model.startswith("zai-coding-plan/"):
        return "glm"
    if model.startswith("xiaomi-token-plan-sgp/"):
        return "mimo"
    for candidate in (name, _BEDROCK_VENDOR.sub("", name)):
        for label, pattern in FAMILY_NAMES:
            if pattern.match(candidate):
                return label
    if identity(model).startswith("gpt-") or re.match(r"^(?:openai/)?o\d+(?:$|-)", identity(model)):
        return "openai"  # OpenAI's own spellings and gateways in front of it; another provider's gpt-* stays distinct
    return model


def router(model):
    """True for a gateway's router, which answers with a model it picks per request."""
    return isinstance(model, str) and bool(_ROUTER.match(identity(model).rsplit("/", 1)[-1]))


def shared_family(left, right):
    """Recognize the verifier policy's families and one model billed through two routes."""
    if not isinstance(left, str) or not left or not isinstance(right, str) or not right:
        return False
    left_id, right_id = identity(left), identity(right)
    if left_id and left_id == right_id:
        return True
    return family(left) in FAMILIES and family(left) == family(right)

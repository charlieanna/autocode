"""Which answers a clarifying question accepts (#1013).

A question that offers ``options`` is a choice among things AutoCode will act on, so an answer
must name one of them, or the question's ``proposed_default``. Formatting differences in a
complete label are ignored: markdown backticks, surrounding quotes, apostrophe shape, case and
spacing. Free-form conversation requires interpretation, not a substring match. A question without options takes
any nonempty text. Pure: imports nothing from AutoCode, so every answer surface (``--answer``,
``TaskRun.answer()``, ``--chat``) applies the same rule through ``autocode_goals.answer``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

_STRICT_PREFIXES = ("route-", "effort-", "workspace-scope-", "rewording-", "resolver-")
_STRICT_CATEGORIES = ("quota", "effort")
QUESTION_FIELDS = ("id", "question", "why", "options", "proposed_default")


def question_view(question, *, request_kind=None, resolver_scope=None, route=False, effort=False):
    """One semantic question projection for live status and recorded decisions."""
    return {
        **{field: deepcopy(question.get(field)) for field in QUESTION_FIELDS},
        "allow_custom": allows_custom(
            question, request_kind=request_kind, resolver_scope=resolver_scope, route=route, effort=effort
        ),
    }


def allows_custom(
    question,
    *,
    request_kind=None,
    resolver_scope=None,
    route: bool = False,
    effort: bool = False,
) -> bool:
    """Whether ``question`` accepts a custom free-text answer beyond its offered ``options`` (#3650).

    Product and assumption clarification questions allow custom answers directly through ``--answer``
    so multi-question batches are never wiped by ``--feedback``. Operational, route, effort, scope
    and rewording questions remain strict choices among the exact options AutoCode can execute.
    """
    if isinstance(question.get("allow_custom"), bool):
        return question["allow_custom"]
    if (
        route
        or effort
        or request_kind in ("permission", "blocker")
        or resolver_scope in ("permission", "blocker", "operational_exhaustion")
    ):
        return False
    qid = str(question.get("id") or "")
    return not (
        "operational_options" in question
        or qid == "resolver-decision"
        or qid.startswith(_STRICT_PREFIXES)
        or question.get("category") in _STRICT_CATEGORIES
    )


def labels(question):
    """The answers ``question`` accepts verbatim; empty when it takes free text."""
    offered = [label for label in question.get("options") or [] if isinstance(label, str) and label.strip()]
    default = question.get("proposed_default")
    if offered and isinstance(default, str) and default.strip() and default not in offered:
        offered.append(default)
    return offered


def _normalized(text):
    """One comparison form: markdown ticks, quotes, apostrophe shape, case and spacing ignored."""
    plain = text.replace("`", "").replace("\u2019", "'").strip().strip("'\"").strip()
    return " ".join(plain.casefold().split())


def _named(valid, text):
    """Match a complete label, preserving exact choices before normalizing their formatting."""
    exact = [label for label in valid if label.strip() == text.strip()]
    if len(exact) == 1:
        return exact[0]
    answer = _normalized(text)
    if not answer:
        return None
    matches = [label for label in valid if _normalized(label) == answer]
    return matches[0] if len(matches) == 1 else None


def refusal(question, text, *, feedback="--feedback TEXT", allow_custom: bool | None = None):
    """Why ``text`` does not answer ``question``; None when it does."""
    custom = bool(question.get("allow_custom")) if allow_custom is None else bool(allow_custom)
    valid = labels(question)
    if not valid or _named(valid, text) is not None or (custom and text.strip()):
        return None
    return (
        f"{question['id']} offers options; answer with one of these labels exactly: "
        + "; ".join(repr(label) for label in valid)
        + f". To ask for something the options do not cover, use {feedback}."
    )


def offered(question, text, *, allow_custom: bool | None = None):
    """The offered label ``text`` names, or ``text`` for a free-text or custom-allowed question.

    Raises ValueError, naming the valid labels, for any other answer.
    """
    custom = bool(question.get("allow_custom")) if allow_custom is None else bool(allow_custom)
    valid = labels(question)
    match = _named(valid, text) if valid else None
    if valid and match is None:
        if custom and text.strip():
            return text.strip()
        raise ValueError(refusal(question, text, allow_custom=custom))
    return match or text


def allows_custom_in_state(state: RunState, question, *, resolver_scope=None) -> bool:
    """Whether ``question`` allows custom free-text in ``state``."""
    scope = resolver_scope
    if scope is None and isinstance(state, dict):
        scope = (state.get("resolver_human_request") or {}).get("scope")
    kind = (state.get("user_request") or {}).get("kind") if isinstance(state, dict) else None
    return allows_custom(question, request_kind=kind, resolver_scope=scope)


def state_refusal(state: RunState, question, text, *, feedback="--feedback TEXT", resolver_scope=None):
    """Refusal for ``question`` in ``state``; None when ``text`` is accepted."""
    return refusal(
        question,
        text,
        feedback=feedback,
        allow_custom=allows_custom_in_state(state, question, resolver_scope=resolver_scope),
    )


def state_offered(state: RunState, question, text):
    """Offered or custom free-text answer for ``question`` in ``state``."""
    return offered(question, text, allow_custom=allows_custom_in_state(state, question))

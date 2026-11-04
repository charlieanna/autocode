"""Which answers a clarifying question accepts (#1013).

A question that offers ``options`` is a choice among things AutoCode will act on, so an answer
must name one of them, or the question's ``proposed_default``. Formatting differences in a
complete label are ignored: markdown backticks, surrounding quotes, apostrophe shape, case and
spacing. Free-form conversation requires interpretation, not a substring match. A question without options takes
any nonempty text. Pure: imports nothing from AutoCode, so every answer surface (``--answer``,
``TaskRun.answer()``, ``--chat``) applies the same rule through ``autocode_goals.answer``.
"""

from __future__ import annotations


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


def refusal(question, text, *, feedback="--feedback TEXT"):
    """Why ``text`` does not answer ``question``; None when it does."""
    valid = labels(question)
    if not valid or _named(valid, text) is not None:
        return None
    return (
        f"{question['id']} offers options; answer with one of these labels exactly: "
        + "; ".join(repr(label) for label in valid)
        + f". To ask for something the options do not cover, use {feedback}."
    )


def offered(question, text):
    """The offered label ``text`` names, or ``text`` for a free-text question.

    Raises ValueError, naming the valid labels, for any other answer.
    """
    valid = labels(question)
    match = _named(valid, text) if valid else None
    if valid and match is None:
        raise ValueError(refusal(question, text))
    return match or text

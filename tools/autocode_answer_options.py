"""Which answers a clarifying question accepts (#1013).

A question that offers ``options`` is a choice among things AutoCode will act on, so an answer
must name one of them, or the question's ``proposed_default``. A question without options takes
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


def refusal(question, text, *, feedback="--feedback TEXT"):
    """Why ``text`` does not answer ``question``; None when it does."""
    valid = labels(question)
    if not valid or text.strip() in {label.strip() for label in valid}:
        return None
    return (
        f"{question['id']} offers options; answer with one of these labels exactly: "
        + "; ".join(repr(label) for label in valid)
        + f". To ask for something the options do not cover, use {feedback}."
    )


def offered(question, text):
    """The offered label ``text`` names (surrounding spaces ignored), or ``text`` for a free-text question.

    Raises ValueError, naming the valid labels, for any other answer.
    """
    problem = refusal(question, text)
    if problem:
        raise ValueError(problem)
    return next((label for label in labels(question) if label.strip() == text.strip()), text)

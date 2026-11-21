"""What a declared login route accepts, and how each accepted login bills.

A configured tool's ``[[auth.routes]]`` entry (``command.py``) names the login mode that
proves its account is connected: ``expect = "api"``. One login can be connected more
than one way, and the way decides who pays: OpenAI is a ChatGPT login (``oauth``, the
subscription) or an API key (``api``, per token). Such a route lists every mode it
accepts, ``expect = ["oauth", "api"]``, and bills by the mode it found,
``billing = { oauth = "subscription", api = "pay per token" }``. A single string still
means one mode and one billing.

The provider's login check, route discovery and the connection view read a route only
through these functions, so they agree on what a connected login is. Pure; imports
nothing from AutoCode.
"""

from __future__ import annotations

import re

BILLING_KINDS = ("subscription", "pay per token")
# OpenAI's one login bills by how it is connected (OpenCode's listing and Kilo's agree).
OPENAI_BILLING = {"oauth": "subscription", "api": "pay per token"}


def modes(route) -> tuple[str, ...]:
    """The login modes ``route`` accepts, in declared order."""
    expect = route.get("expect")
    return (expect,) if isinstance(expect, str) else tuple(expect or ())


def found(route, summary) -> list[str]:
    """The modes the login listing ``summary`` shows for ``route``'s login."""
    return re.findall(route["pattern"], summary, re.MULTILINE)


def connected(route, summary) -> str | None:
    """The accepted mode ``summary`` shows for ``route``, or None when none is accepted.

    A login listed more than once is connected only when every row is an accepted mode
    and they agree, so a run never guesses which of two logins the tool will bill.
    """
    shown = set(found(route, summary))
    if len(shown) != 1:
        return None
    (mode,) = shown
    return mode if mode in modes(route) else None


def billing(route, mode=None) -> str | None:
    """How ``route`` bills when connected with ``mode``; None when the config does not say."""
    declared = route.get("billing")
    if isinstance(declared, dict):
        return declared.get(mode)
    return declared


def describe(route) -> str:
    """The accepted modes for a refusal message: ``'oauth'`` or ``'oauth' or 'api'``."""
    return " or ".join(repr(mode) for mode in modes(route))


def problems(route) -> list[str]:
    """What is wrong with a route's ``expect`` and ``billing``, for config validation."""
    found_problems = []
    expect = route.get("expect")
    accepted = modes(route)
    if isinstance(expect, str):
        if not expect.strip():
            found_problems.append("[auth] route expect must be a non-empty string or array of strings")
    elif (
        not isinstance(expect, list)
        or not expect
        or not all(isinstance(mode, str) and mode.strip() for mode in expect)
        or len(set(expect)) != len(expect)
    ):
        found_problems.append("[auth] route expect must be a non-empty string or array of strings")
    declared = route.get("billing")
    if isinstance(declared, dict):
        if set(declared) - set(accepted) or not all(value in BILLING_KINDS for value in declared.values()):
            found_problems.append(
                "[auth] route billing table must map expected modes to 'subscription' or 'pay per token'"
            )
    elif declared is not None and declared not in BILLING_KINDS:
        found_problems.append("[auth] route billing must be 'subscription' or 'pay per token' when set")
    return found_problems

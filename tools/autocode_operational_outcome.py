"""Pure binding of an owner's observed answer; never recovery eligibility.

Runtime owners construct answers only after their existing transaction succeeds.
The saved reader checks those exact observations without importing a runtime
registry. Work admission separately repeats the registered owner's frontier.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def bind(state: RunState, answer, *, fields=(), members=()):
    if not isinstance(answer, dict) or not answer:
        return None
    return {
        "schema": 1,
        "answer": copy.deepcopy(answer),
        "fields": {key: {"present": key in state, "digest": util.digest(state.get(key))} for key in fields},
        "members": [{"key": key, "value": copy.deepcopy(value)} for key, value in members],
    }


def read(state: RunState, observed):
    """Authenticate only recorded equality, not an owner's cause or allowance."""
    if not isinstance(observed, dict) or set(observed) != {"schema", "answer", "fields", "members"}:
        return None
    answer, fields, members = observed["answer"], observed["fields"], observed["members"]
    if type(observed["schema"]) is not int or observed["schema"] != 1 or not isinstance(answer, dict) or not answer:
        return None
    if not isinstance(fields, dict) or not isinstance(members, list):
        return None
    for key, expected in fields.items():
        if (
            not isinstance(key, str)
            or not isinstance(expected, dict)
            or set(expected) != {"present", "digest"}
            or type(expected["present"]) is not bool
            or expected["present"] != (key in state)
            or expected["digest"] != util.digest(state.get(key))
        ):
            return None
    for member in members:
        if not isinstance(member, dict) or set(member) != {"key", "value"} or not isinstance(member["key"], str):
            return None
        current = state.get(member["key"])
        if not isinstance(current, list) or member["value"] not in current:
            return None
    return copy.deepcopy(answer)


def proof(state: RunState, observed):
    """The actual handler return at capture, or its authenticated saved value."""
    result = observed.get("result")
    if isinstance(result, dict) and set(result) == {"schema", "answer", "fields", "members"}:
        answer = read(state, result)
        return answer.get("proof") if answer else None
    return result


def answer(state: RunState, proof, facts, *, fields=(), members=()):
    if not isinstance(facts, dict) or not facts:
        return None
    return bind(state, {"proof": copy.deepcopy(proof), "facts": facts}, fields=fields, members=members)

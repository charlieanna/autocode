"""What happens today, kept beside each bug-fix criterion instead of inside it (#2133).

The Investigator's observed behavior is evidence the runner binds to its report (autocode_bug_job.apply:
``investigation.observed`` under ``output_hash`` and ``source_revision``). A planning draft that wrote that
text into a criterion and later corrected it could not be told from a draft changing the agreed behavior,
so each correction cost a report repair and a question to the person. The ``current_behavior`` box holds
that text instead: the runner fills an empty one from the bound diagnosis, the approval screen shows it as
"Today:", and a revision that sets it to exactly the diagnosis text is a correction the runner can check,
recorded as a receipt (autocode_contract_revision.baseline_corrections). It is never part of the agreed
outcome: the criterion, its request quotes and its check stay protected as before.

Pure functions over run state and contract bodies. Depends only on autocode_test_cases.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_test_cases as test_cases
except ImportError:
    import autocode_test_cases as test_cases

KEY = "current_behavior"
# The receipt kind in a contract's declared_changes for a box set to the bound diagnosis text.
CHANGE = "current_behavior"


def normalized(text) -> str:
    return " ".join(str(text or "").split())


def of(row) -> str:
    """A criterion's current_behavior, whitespace-normalized; "" when unset."""
    return normalized(row.get(KEY)) if isinstance(row, dict) else ""


def evidence(state: RunState) -> dict | None:
    """The bound diagnosis's observed behavior: its text, output hash and source revision, or None.

    Only this turn's reproduced investigation counts (autocode_test_cases.current_diagnosis), only one whose
    report is pinned by output_hash, and only one whose probe the runner saw exit 0 on today's code
    (autocode_bug_job.apply records that as probe_result): an unbound, stale or untestable diagnosis is the
    Investigator's word, not evidence of today's behavior.
    """
    found = test_cases.current_diagnosis(state)
    if not isinstance(found, dict) or found.get("outcome") != "reproduced" or not found.get("output_hash"):
        return None
    probe = found.get("probe_result")
    if not isinstance(probe, dict) or probe.get("exit_code") != 0:
        return None
    text = normalized(found.get("observed"))
    if not text:
        return None
    return {"text": text, "output_hash": found["output_hash"], "source_revision": found.get("source_revision")}


def matches(text, found) -> bool:
    """Whether ``text`` is the bound diagnosis's observed behavior, up to whitespace."""
    return bool(found) and normalized(text) == found["text"]


def prepare_body(state: RunState, body, origin):
    """Fill each empty box: the bound diagnosis text on a criterion whose named test must fail today
    (``test:``), else the text the previous draft held for the same criterion. A guard: criterion describes
    behavior that already works, so it gets no diagnosis text. The person's own edit is kept as written."""
    rows = body.get("acceptance_criteria") if isinstance(body, dict) else None
    if origin == "user_cli_edit" or not isinstance(rows, list):
        return body
    found = evidence(state)
    previous = {
        row["id"]: of(row)
        for row in ((state.get("goal_contract") or {}).get("body") or {}).get("acceptance_criteria") or []
        if isinstance(row, dict) and row.get("id")
    }
    filled = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("id") or of(row):
            continue
        if found and test_cases.mark(row.get("verification_method")) == test_cases.MARK:
            filled[row["id"]] = found["text"]
        elif previous.get(row["id"]):
            filled[row["id"]] = previous[row["id"]]
    if not filled:
        return body
    body = copy.deepcopy(body)
    for row in body["acceptance_criteria"]:
        if isinstance(row, dict) and row.get("id") in filled:
            row[KEY] = filled[row["id"]]
    return body


def receipt(cid: str, text: str, found: dict) -> dict:
    """The declared_changes row recording that ``cid``'s box was set to the bound diagnosis text."""
    return {
        "item": cid,
        "change": CHANGE,
        "basis": "agent_proposed",
        "answer_id": "",
        "replacement": text,
        "evidence": {"output_hash": found["output_hash"], "source_revision": found.get("source_revision")},
    }

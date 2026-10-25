"""Distinguish new model interpretations of feedback from authenticated human wording.

Pure classification over saved contracts and sources. A source ID authenticates
the feedback, not every inference a model attaches to it. Existing approval,
human-edit and declared-change receipts still protect their exact wording.
"""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_prompts as prompts
    from . import autocode_requirement_cues as cues
except ImportError:
    import autocode_prompts as prompts
    import autocode_requirement_cues as cues


def _comparable(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().rstrip(".;:!").lower()


def _quotes_person(state: RunState, text):
    line = _comparable(text)
    return not line or any(
        said and (said in line or line in said)
        for source in cues.scan_texts(state)
        for sentence in cues.sentences(source)
        if (said := _comparable(sentence))
    )


def feedback_interpretations(state: RunState, *, model_origins, saved_user_basis):
    """New feedback paraphrases editable in this explicitly model-authored draft.

    Missing or malformed protected history cannot establish that a row is new.
    Answers and actual source quotations are never classified as model inferences.
    """
    contract = state.get("goal_contract") or {}
    if (
        contract.get("origin") not in model_origins
        or contract.get("approval_status") != "draft"
        or contract.get("approval_event")
    ):
        return []
    body = contract.get("body")
    history = state.get("contract_history", [])
    if not isinstance(body, dict) or not isinstance(history, list) or any(not isinstance(row, dict) for row in history):
        return []
    events, feedback = state.get("user_events", []), state.get("brief_feedback", [])
    if not isinstance(events, list) or not isinstance(feedback, list):
        return []
    approvals = [
        event.get("token") for event in events if isinstance(event, dict) and event.get("kind") == "goal_approval"
    ]
    known = {
        f"r{row['revision']}:{row['hash']}"
        for row in [*history, contract]
        if type(row.get("revision")) is int and row["revision"] > 0 and isinstance(row.get("hash"), str) and row["hash"]
    }
    if any(not isinstance(token, str) or token not in known for token in approvals):
        return []  # The missing approved contract cannot establish that this interpretation is new.
    if f"r{contract.get('revision')}:{contract.get('hash')}" in approvals:
        return []
    rows = body.get("accepted_assumptions")
    if not isinstance(rows, list):
        return []
    protected: set[str] = set()
    for prior in history:
        if not (
            prior.get("approval_status") == "approved"
            or prior.get("approval_event")
            or prior.get("origin") == "user_cli_edit"
            or f"r{prior.get('revision')}:{prior.get('hash')}" in approvals
        ):
            continue
        old = prior.get("body")
        if not isinstance(old, dict):
            return []
        for key in ("accepted_assumptions", "delegated_decisions"):
            prior_rows = old.get(key)
            if not isinstance(prior_rows, list) or any(
                not isinstance(row, dict) or not isinstance(row.get("text"), str) or not row["text"].strip()
                for row in prior_rows
            ):
                return []
            protected.update(row["text"] for row in prior_rows)
    # A replacement actually authorized by the person remains theirs even if it
    # has not subsequently been approved as a complete plan.
    for prior in [*history, contract]:
        changes = prior.get("declared_changes", [])
        if not isinstance(changes, list) or any(not isinstance(row, dict) for row in changes):
            return []
        for change in changes:
            if saved_user_basis(state, change.get("basis"), change.get("answer_id")):
                protected.update(text for key in ("item", "replacement") if isinstance(text := change.get(key), str))
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.get("basis") != "user_feedback":
            continue
        text, event_id = row.get("text"), row.get("answer_id")
        if not isinstance(text, str) or not text.strip() or text in protected:
            continue
        sources = [
            event
            for event in feedback
            if isinstance(event, dict)
            and event.get("id") == event_id
            and event in events
            and isinstance(event.get("text"), str)
            and event["text"].strip()
        ]
        if len(sources) != 1 or not saved_user_basis(state, "user_feedback", event_id) or _quotes_person(state, text):
            continue
        result.append(
            {"text": text, "basis": "user_feedback", "answer_id": event_id, "source_text": sources[0]["text"]}
        )
    return result


def corrections(interpretations, body):
    """Interpretations corrected while retaining the same authenticated source.

    Dropping its source or relabeling it as the agent's own untraceable decision
    does not qualify. The ordinary revision guard still checks every other field.
    """
    rows = body.get("accepted_assumptions") or []
    return {
        before["text"]
        for before in interpretations
        if all(not isinstance(row, dict) or row.get("text") != before["text"] for row in rows)
        and any(
            isinstance(row, dict)
            and row.get("basis") == before["basis"]
            and row.get("answer_id") == before["answer_id"]
            and isinstance(row.get("text"), str)
            and row["text"].strip()
            for row in rows
        )
    }


def context(interpretations):
    """Explain the exact source and the limited correction, without changing state."""
    if not interpretations:
        return {}
    return {
        "instruction": prompts.get("fragments/autoplanner/feedback-interpretations.md").rstrip("\n"),
        "rows": copy.deepcopy(interpretations),
    }

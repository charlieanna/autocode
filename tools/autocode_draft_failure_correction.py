"""Bind one independently reviewed correction of an unapproved failure-case inference.

The reviewer's semantic judgment is necessary, not machine-proven ownership. The
core only checks provenance, freshness, exact changes and retained obligations;
final review and the person's exact-plan approval remain required.
"""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_requirement_cues as cues
    from . import autocode_source_log as source_log
    from . import autocode_util as util
except ImportError:
    import autocode_requirement_cues as cues
    import autocode_source_log as source_log
    import autocode_util as util

FIELD = "draft_failure_correction"
ORIGINS = {"glm_draft", "astra_discovery", "glm_revise", "astra_finalize"}
LISTS = ("required_behaviors", "scope_exclusions", "constraints", "permission_boundaries")
SOURCES = (
    "task",
    "source_log",
    "answers",
    "brief_feedback",
    "user_events",
    "requirements_handoff",
    "requirements_history",
    "investigation",
    "turns",
    "workflow",
    "design_constraint",
    "design_review",
)
RECEIPT_FIELDS = ("snapshot", "judgment", "before", "after", "rationale")
RECEIPT_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "snapshot": {"type": "string"},
        "judgment": {"type": "string", "enum": ["unapproved_model_inference"]},
        "before": {"type": "string"},
        "after": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": list(RECEIPT_FIELDS),
    "additionalProperties": False,
}


def offered(schema):
    """A saved pre-upgrade concern schema cannot accept a correction receipt."""
    return FIELD in schema.get("properties", {}).get("concerns", {}).get("items", {}).get("properties", {})


def snapshot(state: RunState):
    """No offer for an approved, edited, unsealed or incompletely known lineage."""
    history = state.get("contract_history", [])
    contract = state.get("goal_contract")
    events = state.get("user_events", [])
    if not isinstance(state.get("task_id"), str) or not state["task_id"]:
        return None
    if not isinstance(history, list) or not isinstance(contract, dict) or not isinstance(events, list):
        return None
    if any(
        not isinstance(e, dict) or not isinstance(e.get("kind"), str) or e["kind"] == "goal_approval" for e in events
    ):
        return None
    for index, revision in enumerate([*history, contract], 1):
        if (
            not isinstance(revision, dict)
            or revision.get("origin") not in ORIGINS
            or revision.get("approval_status") != "draft"
            or revision.get("approval_event") is not None
            or "approval_event" not in revision
            or revision.get("task_id") != state.get("task_id")
            or type(revision.get("revision")) is not int
            or revision.get("revision") != index
            or not isinstance(revision.get("body"), dict)
            or revision.get("hash") != util.digest({k: revision.get(k) for k in ("task_id", "revision", "body")})
        ):
            return None
        changes = revision.get("declared_changes")
        if not isinstance(changes, list) or any(
            not isinstance(row, dict) or row.get("basis") != "agent_proposed" or row.get("answer_id") != ""
            for row in changes
        ):
            return None
    if not isinstance(state.get("task"), str) or not isinstance(state.get("answers", {}), dict):
        return None
    if any(not isinstance(row, dict) for row in state.get("answers", {}).values()):
        return None
    for key in ("source_log", "brief_feedback", "requirements_history", "turns"):
        rows = state.get(key, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return None
    if any(
        any(not isinstance(row.get(key), str) or not row[key] for key in ("id", "kind", "text", "sha256"))
        for row in state.get("source_log", [])
    ):
        return None
    for key in ("requirements_handoff", "investigation", "workflow", "design_constraint", "design_review"):
        value = state.get(key)
        if value is not None and not isinstance(value, dict):
            return None
    report = (state.get("requirements_handoff") or {}).get("report", {})
    if not isinstance(report, dict):
        return None
    for key in ("requirements", "required_behaviors", "constraints", "acceptance_tests"):
        if not isinstance(report.get(key, []), list):
            return None
    return "draft-failure-v1:" + util.digest(
        {
            "contract": contract,
            "history": history,
            "sources": {key: state.get(key) for key in SOURCES},
        }
    )


def _quoted(state: RunState, item):
    comparable = lambda value: re.sub(r"\s+", " ", value).strip().lower().rstrip(".!?")
    line = comparable(item)
    texts = cues.scan_texts(state) + [row["text"] for row in source_log.sources(state) or []]
    report = (state.get("requirements_handoff") or {}).get("report") or {}
    for row in report.get("requirements", []):
        if isinstance(row, dict) and isinstance(row.get("source_quote"), str):
            texts.append(row["source_quote"])
    if (state.get("workflow") or {}).get("requirements_approved") is True:
        texts += [report.get("intended_outcome", "")]
        for key in ("required_behaviors", "constraints", "acceptance_tests"):
            texts += report.get(key, [])
    return any(
        said and (said in line or line in said)
        for text in texts
        if isinstance(text, str)
        for sentence in cues.sentences(text)
        if (said := comparable(sentence))
    )


def receipts(state: RunState, report):
    """Validate only explicit fresh first-review receipts; legacy concerns grant nothing."""
    allowed = {}
    token = snapshot(state)
    body = (state.get("goal_contract") or {}).get("body", {})
    for concern in report.get("concerns", []):
        receipt = concern.get(FIELD)
        if receipt is None:
            continue
        if (
            not isinstance(receipt, dict)
            or set(receipt) != set(RECEIPT_FIELDS)
            or any(not isinstance(value, str) or not value.strip() for value in receipt.values())
            or not token
            or receipt.get("snapshot") != token
            or receipt.get("judgment") != "unapproved_model_inference"
            or concern.get("blocking") is not True
            or not isinstance(concern.get("id"), str)
            or not concern["id"].strip()
        ):
            raise ValueError("Draft failure correction needs a fresh first-review inference receipt")
        before, after = receipt["before"], receipt["after"]
        decisions = [
            row.get("text") for key in ("accepted_assumptions", "delegated_decisions") for row in body.get(key, [])
        ]
        if (
            before == after
            or before in allowed
            or body.get("important_failure_cases", []).count(before) != 1
            or any(before in body.get(key, []) for key in LISTS)
            or before in decisions
            or any(before == row.get("criterion") for row in body.get("acceptance_criteria", []))
            or _quoted(state, before)
        ):
            raise ValueError("Draft failure correction cannot replace protected human or dual-owned text")
        allowed[before] = {"concern_id": concern["id"], "receipt": copy.deepcopy(receipt)}
    return allowed


def _obligation_mismatch(before, after, replacements, interpretation_corrections):
    # The failure receipt grants no authority over adjacent obligations. Only the caller's
    # existing feedback-interpretation guard may independently permit an assumption text update.
    for key in (*LISTS, "delegated_decisions"):
        if before.get(key) != after.get(key):
            return key
    old_assumptions = before.get("accepted_assumptions", [])
    new_assumptions = after.get("accepted_assumptions", [])
    if len(old_assumptions) != len(new_assumptions):
        return "accepted_assumptions"
    for index, (old, new) in enumerate(zip(old_assumptions, new_assumptions, strict=True)):
        if old == new:
            continue
        if not (
            isinstance(old, dict)
            and isinstance(new, dict)
            and old.get("text") in interpretation_corrections
            and isinstance(new.get("text"), str)
            and new["text"].strip()
            and {**old, "text": new["text"]} == new
        ):
            return f"accepted_assumptions[{index}]"
    expected = [replacements.get(item, item) for item in before.get("important_failure_cases", [])]
    if expected != after.get("important_failure_cases", []):
        return "important_failure_cases outside the exact receipted replacements"
    new_criteria = {row["id"]: row for row in after.get("acceptance_criteria", [])}
    for old in before.get("acceptance_criteria", []):
        new = new_criteria.get(old["id"])
        if new is None or {k: v for k, v in old.items() if k != "sources"} != {
            k: v for k, v in new.items() if k != "sources"
        }:
            return f"acceptance_criteria[{old['id']}]"
        if any(source not in new.get("sources", []) for source in old.get("sources", [])):
            return f"acceptance_criteria[{old['id']}].sources"
    return None


def corrections(state: RunState, body, changes, origin, *, interpretation_corrections=()):
    """Authority for exact glm_revise pairs only; every other change uses its existing guard."""
    if origin != "glm_revise":
        return {}
    report = ((state.get("planning") or {}).get("reports", {}).get("astra_challenge") or {}).get("report")
    if not isinstance(report, dict):
        return {}
    try:
        offered = receipts(state, report)
    except ValueError:
        return {}  # stale evidence cannot authorize a revision; ordinary saved-basis refusal follows
    allowed = {}
    for item, binding in offered.items():
        rows = [row for row in changes if row.get("item") == item]
        if len(rows) != 1:
            continue
        row = rows[0]
        if (
            row.get("change") == "reworded"
            and row.get("basis") == "agent_proposed"
            and row.get("answer_id") == ""
            and row.get("replacement") == binding["receipt"]["after"]
            and row.get("example_correction") is None
            and item not in body.get("important_failure_cases", [])
        ):
            allowed[item] = binding
    if not allowed:
        return {}
    replacements = {item: binding["receipt"]["after"] for item, binding in allowed.items()}
    contract = cast(dict, state["goal_contract"])  # revision_guard has already established the previous contract
    mismatch = _obligation_mismatch(contract["body"], body, replacements, interpretation_corrections)
    if mismatch:
        hint = (
            " An assumption text update must independently qualify under the feedback-interpretation rule "
            "and retain its list position, basis and answer_id."
            if mismatch.startswith("accepted_assumptions")
            else " Keep that obligation unchanged."
        )
        raise ValueError(
            f"Draft failure correction receipt matches, but adjacent {mismatch} is not retained."
            + hint
            + " The receipt authorizes only its exact failure-case replacement."
        )
    return allowed

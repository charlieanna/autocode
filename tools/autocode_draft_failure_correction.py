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
    from . import autocode_names as names
    from . import autocode_requirement_cues as cues
    from . import autocode_roles as roles
    from . import autocode_source_log as source_log
    from . import autocode_util as util
except ImportError:
    import autocode_names as names
    import autocode_requirement_cues as cues
    import autocode_roles as roles
    import autocode_source_log as source_log
    import autocode_util as util

FIELD = "draft_failure_correction"
COMPATIBILITY = "unapproved_bugfix_compatibility_inference"
TARGETS = {"unapproved_model_inference": "important_failure_cases", COMPATIBILITY: "required_behaviors"}
ORIGINS = {"planner_draft", "draft_plan", "revise_plan", "finalize_plan"}
LISTS = ("required_behaviors", "scope_exclusions", "constraints", "permission_boundaries")
PLANNING_RECOVERY_STAGES = frozenset((*roles.ALL_PLANNING_STAGES, "investigate_stuck", "resolve_repair"))
PLANNING_RECOVERY_ROLES = frozenset(
    {"requirements", "planner", "plan_reviewer", "task_planner", "investigator", "repair_resolver"}
)
# Structural fields and gate kinds that carry human input, approvals, external drift or execution scope.
_NON_BOOKKEEPING_KEYS = frozenset(
    {
        "text",
        "answer_id",
        "question_id",
        "questions",
        "assumption_id",
        "criterion",
        "contract_token",
        "contract_hash",
        "contract_revision",
        "milestone_id",
        "milestone_ids",
        "batch_id",
        "compared_binding",
        "paused_attempt_id",
    }
)
_PERSON_OR_APPROVAL_KINDS = frozenset(
    {
        "goal_approval",
        "workflow_approval",
        "brief_feedback",
        "answer",
        "delegated",
        "checkpoint_answer",
        "permission_answer",
        "reject_assumption",
        "human_review",
        "plan_message",
        "gate_message",
        "run_message",
        "completion_accept",
    }
)
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
        "judgment": {"type": "string", "enum": list(TARGETS)},
        "before": {"type": "string"},
        "after": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": list(RECEIPT_FIELDS),
    "additionalProperties": False,
}


def _planning_stage(stage) -> bool:
    if not isinstance(stage, str) or not stage:
        return False
    return names.stage(stage.removesuffix("_report_repair")) in PLANNING_RECOVERY_STAGES


def readonly_planning_runner_event(event) -> bool:
    """Whether ``event`` is internal read-only planning runner bookkeeping rather than person input."""
    if (
        not isinstance(event, dict)
        or event.get("actor") != "runner"
        or not isinstance(event.get("kind"), str)
        or not event["kind"].strip()
        or event["kind"] in _PERSON_OR_APPROVAL_KINDS
        or any(event.get(key) not in (None, []) for key in ("changed_files", "changed", "changed_paths"))
        or bool(_NON_BOOKKEEPING_KEYS & event.keys())
    ):
        return False
    stages = [event[key] for key in ("stage", "next_stage") if event.get(key) is not None]
    if stages:
        return all(_planning_stage(stage) for stage in stages)
    if event.get("role") is not None:
        return names.route(str(event.get("role") or "")) in PLANNING_RECOVERY_ROLES
    if event.get("attempt_id") is not None:
        return _planning_stage(names.stage_from_attempt_id(event.get("attempt_id")))
    return False


def source_events(state: RunState) -> list:
    """Events in ``user_events`` that reflect human input, approvals or non-planning mutations."""
    events = state.get("user_events")
    if not isinstance(events, list):
        return []
    return [event for event in events if not readonly_planning_runner_event(event)]


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
    sources = {key: state.get(key) for key in SOURCES}
    if isinstance(state.get("user_events"), list):
        sources["user_events"] = source_events(state)
    return "draft-failure-v1:" + util.digest(
        {
            "contract": contract,
            "history": history,
            "sources": sources,
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
    contract = state.get("goal_contract") or {}
    body = contract.get("body", {})
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
            or receipt.get("judgment") not in TARGETS
            or concern.get("blocking") is not True
            or not isinstance(concern.get("id"), str)
            or not concern["id"].strip()
        ):
            raise ValueError("Draft failure correction needs a fresh first-review inference receipt")
        before, after = receipt["before"], receipt["after"]
        target = TARGETS[receipt["judgment"]]
        if target == "required_behaviors":
            handoff = (state.get("requirements_handoff") or {}).get("report") or {}
            if (
                body.get("task_kind") != "bugfix"
                or contract.get("revision") != 1
                or (state.get("workflow") or {}).get("requirements_approved")
                or any(state.get(key) for key in ("answers", "brief_feedback", "requirements_history"))
                or bool(source_events(state))
                or handoff.get(target, []).count(before) != 1
            ):
                raise ValueError("Draft failure correction cannot replace protected human or dual-owned text")
        decisions = [
            row.get("text") for key in ("accepted_assumptions", "delegated_decisions") for row in body.get(key, [])
        ]
        if (
            before == after
            or before in allowed
            or body.get(target, []).count(before) != 1
            or any(before in body.get(key, []) for key in (*LISTS, "important_failure_cases") if key != target)
            or before in decisions
            or any(before == row.get("criterion") for row in body.get("acceptance_criteria", []))
            or _quoted(state, before)
        ):
            raise ValueError("Draft failure correction cannot replace protected human or dual-owned text")
        allowed[before] = {"concern_id": concern["id"], "receipt": copy.deepcopy(receipt)}
    return allowed


def repair_obligations(state: RunState):
    """Supply exact adjacent rows omitted by the human-protected-field snapshot."""
    report = ((state.get("planning") or {}).get("reports", {}).get("review_plan") or {}).get("report")
    if not isinstance(report, dict):
        return None
    try:
        if not receipts(state, report):
            return None
    except ValueError:
        return None
    body = (state.get("goal_contract") or {}).get("body", {})
    return {key: copy.deepcopy(body.get(key, [])) for key in ("accepted_assumptions", "delegated_decisions")}


def _kept(criterion, ignored):
    """A criterion's retained fields; an empty supersedes list equals an omitted one (#2748: it replaces no test)."""
    return {
        key: value
        for key, value in criterion.items()
        if key not in ignored and not (key == "supersedes" and value == [])
    }


def _obligation_mismatches(before, after, replacements, interpretation_corrections, criterion_corrections):
    # The failure receipt grants no authority over adjacent obligations. Only the caller's
    # existing assumption and criterion guards independently authorize their own draft corrections.
    mismatches = []
    for key in (*LISTS, "delegated_decisions"):
        expected = before.get(key)
        if key == "required_behaviors" and expected is not None:
            expected = [replacements.get(item, item) for item in expected]
        if expected != after.get(key):
            mismatches.append(key)
    old_assumptions = before.get("accepted_assumptions", [])
    new_assumptions = after.get("accepted_assumptions", [])
    if len(old_assumptions) != len(new_assumptions):
        mismatches.append("accepted_assumptions")
    for index, (old, new) in enumerate(zip(old_assumptions, new_assumptions, strict=False)):
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
            mismatches.append(f"accepted_assumptions[{index}]")
    expected = [replacements.get(item, item) for item in before.get("important_failure_cases", [])]
    if expected != after.get("important_failure_cases", []):
        mismatches.append("important_failure_cases outside the exact receipted replacements")
    new_criteria = {row["id"]: row for row in after.get("acceptance_criteria", [])}
    for old in before.get("acceptance_criteria", []):
        new = new_criteria.get(old["id"])
        if new is None:
            mismatches.append(f"acceptance_criteria[{old['id']}]")
            continue
        independent = {
            key
            for key, ids in (criterion_corrections or {}).items()
            if key in ("criterion", "verification_method", "current_behavior") and old["id"] in ids
        }
        ignored = {"sources"} | independent
        if _kept(old, ignored) != _kept(new, ignored):
            mismatches.append(f"acceptance_criteria[{old['id']}]")
        if any(source not in new.get("sources", []) for source in old.get("sources", [])):
            mismatches.append(f"acceptance_criteria[{old['id']}].sources")
    return mismatches


def corrections(state: RunState, body, changes, origin, *, interpretation_corrections=(), criterion_corrections=None):
    """Authority for exact revise_plan pairs only; every other change uses its existing guard."""
    if origin != "revise_plan":
        return {}
    report = ((state.get("planning") or {}).get("reports", {}).get("review_plan") or {}).get("report")
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
            and item not in body.get(TARGETS[binding["receipt"]["judgment"]], [])
        ):
            allowed[item] = binding
    if not allowed:
        return {}
    replacements = {item: binding["receipt"]["after"] for item, binding in allowed.items()}
    contract = cast(dict, state["goal_contract"])  # revision_guard has already established the previous contract
    mismatches = _obligation_mismatches(
        contract["body"], body, replacements, interpretation_corrections, criterion_corrections
    )
    if mismatches:
        hint = (
            " An assumption text update must independently qualify under the feedback-interpretation rule "
            "and retain its list position, basis and answer_id."
            if any(item.startswith("accepted_assumptions") for item in mismatches)
            else " Keep every listed obligation unchanged."
        )
        retention = "is not retained" if len(mismatches) == 1 else "are not retained"
        raise ValueError(
            f"Draft failure correction receipt matches, but adjacent {', '.join(mismatches)} {retention}."
            + hint
            + " The receipt authorizes only its exact replacement."
        )
    return allowed

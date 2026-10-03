"""Protect agreed behavior while permitting proof repairs in unapproved drafts.

Pure functions over contract bodies and user events. No runner imports or writes.
"""
from __future__ import annotations

import re

try:
    from . import autocode_protected_text as protected, autocode_test_cases as test_cases, autocode_draft_examples as examples, autocode_contract_delta as delta
except ImportError:
    import autocode_protected_text as protected, autocode_test_cases as test_cases, autocode_draft_examples as examples, autocode_contract_delta as delta

PLANNER_ORIGINS = {"glm_draft", "glm_revise", "astra_finalize", "astra_discovery"}
PROTECTED_LISTS = ("required_behaviors", "scope_exclusions", "constraints", "important_failure_cases")


def protected_proof(state: dict, criterion: dict) -> bool:
    """Recognize this proof in an approved or user-authored saved revision."""
    approvals = {e.get("token") for e in state.get("user_events", []) if e.get("kind") == "goal_approval"}
    for revision in [*(state.get("contract_history") or []), state.get("goal_contract") or {}]:
        rows = (revision.get("body") or {}).get("acceptance_criteria", [])
        if not any(row.get("id") == criterion["id"] and
                   test_cases.proof(row.get("verification_method")) == test_cases.proof(criterion["verification_method"])
                   for row in rows):
            continue
        token = f"r{revision.get('revision')}:{revision.get('hash')}"
        if (revision.get("approval_status") == "approved" or revision.get("approval_event")
                or token in approvals or revision.get("origin") == "user_cli_edit"):
            return True
        if any(change.get("item") == criterion["id"] and
               saved_user_basis(state, change.get("basis"), change.get("answer_id"))
               for change in revision.get("declared_changes", [])):
            return True
    return False


def draft_proof_corrections(state: dict, before: dict, after: dict) -> set[str]:
    """Only planner-generated draft proofs may change without a saved user basis.

    Preserve criterion text, human review and runner-enforced proof requirements.
    Verification mentioned only in free-form task text has no structured provenance.
    """
    contract = state.get("goal_contract") or {}
    if contract.get("approval_status") != "draft" or contract.get("approval_event"):
        return set()
    old = {row["id"]: row for row in before.get("acceptance_criteria", [])}
    new = {row["id"]: row for row in after.get("acceptance_criteria", [])}
    marked = lambda method: test_cases.proof(method) != str(method or "").strip()
    return {cid for cid in old.keys() & new.keys()
            if old[cid]["criterion"] == new[cid]["criterion"]
            and old[cid].get("human_review") == new[cid].get("human_review")
            and str(new[cid].get("verification_method") or "").strip()
            and old[cid]["verification_method"] != new[cid]["verification_method"]
            and not (marked(old[cid]["verification_method"]) and not marked(new[cid]["verification_method"]))
            and not protected_proof(state, old[cid])}


def saved_user_basis(state, basis, answer_id):
    if basis == "user_answer":
        return bool(answer_id) and answer_id in state.get("answers", {})
    if basis == "user_feedback":
        return bool(answer_id) and any(event.get("id") == answer_id and event in state.get("user_events", [])
                                       for event in state.get("brief_feedback", []))
    return False


def canonical_items(previous: dict, changes: list) -> list:
    """Declared changes, each naming its protected item exactly as the guard compares it.

    GLM 5.3 sometimes wrapped the item it declared (`required_behaviors: "<text>"`, `AC12: '<text>'`,
    `AC9.criterion`), and the guard refused a correctly declared, user-backed change: 8 of 50 guard
    refusals to 2026-10-02. A reference is rewritten only when it holds exactly one protected text of the
    previous contract with little else around it and names no criterion, or names exactly one criterion ID
    (quoting at most that criterion's own wording). A reference that could mean two items is left for the
    guard to refuse.
    """
    criteria = {row["id"]: row.get("criterion", "") for row in previous.get("acceptance_criteria", []) if row.get("id")}
    texts = {text for key in (*PROTECTED_LISTS, "permission_boundaries") for text in previous.get(key, [])}

    def canonical(item):
        if not isinstance(item, str) or item in texts or item in criteria:
            return item
        wrapped = [text for text in texts if text in item and len(item) - len(text) <= 80]
        rest = item
        for text in wrapped:
            rest = rest.replace(text, "")
        named = [cid for cid in criteria if re.search(r"(?<![\w-])" + re.escape(cid) + r"(?![\w-])", rest)]
        # An explicit criterion ID names that criterion, as long as any protected text it quotes is the
        # criterion's own wording; quoting another item's text beside it is ambiguous (review of #259).
        if len(named) == 1 and all(text == criteria[named[0]] for text in wrapped):
            return named[0]
        return wrapped[0] if len(wrapped) == 1 and not named else item

    return [dict(raw, item=canonical(raw.get("item"))) if isinstance(raw, dict) else raw for raw in changes]


def revision_guard(state, body, changes, origin):
    """A planner revision may not drop protected text or widen permissions on its own.

    Returns the declared changes with each item named exactly (canonical_items)."""
    previous_contract = state.get("goal_contract") or {}
    previous = previous_contract.get("body")
    if origin not in PLANNER_ORIGINS or not previous:
        return changes
    # A new draft may replace an unapproved one. Revising the current draft, or
    # replacing an approved contract, cannot drop protected text on its own.
    if origin in ("glm_draft", "astra_discovery") and previous_contract.get("approval_status") != "approved":
        return changes
    if not isinstance(changes, list):
        raise ValueError("Planner revision needs contract_changes")
    changes = canonical_items(previous, changes)
    protected.restore_spelling(previous, body, {raw.get("item") for raw in changes if isinstance(raw, dict)}, PROTECTED_LISTS)
    proof_corrections = draft_proof_corrections(state, previous, body) - {
        raw.get("item") for raw in changes if isinstance(raw, dict)
        and saved_user_basis(state, raw.get("basis"), raw.get("answer_id"))}
    example_corrections = examples.corrections(state, previous, body, changes, saved_user_basis)
    protected_changes = []
    for raw in changes:
        if not isinstance(raw, dict) or raw.get("change") not in ("removed", "reworded", "permission_changed"):
            raise ValueError("contract_changes entries need item, change, basis and answer_id")
        if (raw.get("item") in proof_corrections and raw["change"] == "reworded"
                and raw.get("basis") == "agent_proposed" and not raw.get("answer_id")):
            continue  # Older reports declared this engineering correction as a contract delta.
        if delta.engineering_delta(raw, previous, body, proof_corrections, PROTECTED_LISTS):
            continue
        if raw.get("item") in example_corrections:
            continue
        protected_changes.append(raw)
        basis = raw.get("basis")
        if not saved_user_basis(state, basis, raw.get("answer_id")):
            raise ValueError(f"Changing a protected contract item {raw.get('item')!r} needs a saved user answer or feedback event")
    declared = {}
    for raw in protected_changes:
        declared.setdefault(raw["item"], []).append(raw)

    def consume(item, kind):
        rows = declared.get(item, [])
        match = next((row for row in rows if row["change"] == kind), None)
        if match is None:
            raise ValueError(f"Planner revision drops or changes {item!r} without a user-backed contract change")
        rows.remove(match)
        if kind == "reworded":
            replacement = str(match.get("replacement", "")).strip()
            if not replacement:
                raise ValueError(f"Rewording {item!r} needs the replacement text")
            return "user", replacement
        return "user", None

    for key in PROTECTED_LISTS:
        for item in previous.get(key, []):
            if item in body.get(key, []):
                continue
            _, replacement = consume(item, "reworded" if any(row["change"] == "reworded" for row in declared.get(item, [])) else "removed")
            if replacement and replacement not in body.get(key, []):
                raise ValueError(f"Rewording {item!r} must appear in {key}")
    new_rows = {row["id"]: row for row in body.get("acceptance_criteria", [])}
    comparable = [dict(row, verification_method=new_rows[row["id"]]["verification_method"])
                  if row["id"] in proof_corrections else row for row in previous.get("acceptance_criteria", [])]
    comparable = [dict(row, criterion=new_rows[row["id"]]["criterion"]) if row["id"] in example_corrections
                  else row for row in comparable]
    if previous_contract.get("approval_status") == "draft" and not previous_contract.get("approval_event"):
        comparable = [dict(row, human_review=True) if (
            not row.get("human_review") and new_rows.get(row["id"], {}).get("human_review") is True
            and not declared.get(row["id"]) and not protected_proof(state, row)) else row for row in comparable]
    signature = lambda row: (row["criterion"], test_cases.proof(row["verification_method"]), row.get("human_review", False))
    old_criteria = {row["id"]: signature(row) for row in comparable}
    new_criteria = {cid: signature(row) for cid, row in new_rows.items()}
    for cid, text in old_criteria.items():
        if new_criteria.get(cid) == text:
            continue
        consume(cid, "removed" if cid not in new_criteria else "reworded")
    previous_permissions = previous.get("permission_boundaries", [])
    if previous_permissions and set(previous_permissions) != set(body.get("permission_boundaries", [])):
        changed = set(previous.get("permission_boundaries", [])) ^ set(body.get("permission_boundaries", []))
        for item in changed:
            consume(item, "permission_changed")
    if any(rows for rows in declared.values()):
        raise ValueError("contract_changes contains an item that was not changed in the protected contract")
    return changes

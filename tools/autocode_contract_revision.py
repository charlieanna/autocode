"""Protect agreed behavior while permitting proof repairs in unapproved drafts.

Pure functions over contract bodies and user events. No runner imports or writes.
"""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_assumption_provenance as assumption_provenance
    from . import autocode_contract_delta as delta
    from . import autocode_current_behavior as current_behavior
    from . import autocode_draft_examples as examples
    from . import autocode_draft_failure_correction as failure_correction
    from . import autocode_protected_text as protected
    from . import autocode_python_tests as python_tests
    from . import autocode_requirement_cues as cues
    from . import autocode_rewording_question as rewording
    from . import autocode_test_cases as test_cases
    from .autocode_user_authority import saved_user_basis
except ImportError:
    import autocode_assumption_provenance as assumption_provenance
    import autocode_contract_delta as delta
    import autocode_current_behavior as current_behavior
    import autocode_draft_examples as examples
    import autocode_draft_failure_correction as failure_correction
    import autocode_protected_text as protected
    import autocode_python_tests as python_tests
    import autocode_requirement_cues as cues
    import autocode_rewording_question as rewording
    import autocode_test_cases as test_cases
    from autocode_user_authority import saved_user_basis

PLANNER_ORIGINS = {"glm_draft", "glm_revise", "astra_finalize", "astra_discovery"}
PROTECTED_LISTS = ("required_behaviors", "scope_exclusions", "constraints", "important_failure_cases")
# The lists a revision may change on its own when the Plan Reviewer blocked the draft and the line is the
# Planner's own (reviewer_requested_own_text): a constraint or a permission boundary that only restates how the
# Planner split the work. Other lists stay protected; failure_correction separately binds a fresh independent
# review's exact correction of an unapproved model inference, retaining the rest of the contract.
PLANNER_OWN_LISTS = ("constraints", "permission_boundaries")
# The person's own words. A delegated decision is the Planner's choice made on the person's behalf, so
# the Planner may revise it like its own proposal; a delegated answer can still authorize a change.
PERSON_TEXT_BASES = ("user_answer", "user_feedback")
DROP_ASSUMPTION = "(the revised plan no longer keeps this assumption)"


def decision_rows(body: dict) -> dict:
    return {
        row["text"]: row
        for key in ("accepted_assumptions", "delegated_decisions")
        for row in body.get(key, [])
        if isinstance(row, dict) and isinstance(row.get("text"), str) and row.get("text")
    }


def user_backed_assumptions(body: dict) -> dict:
    """Assumption texts saved from the person's own answer or feedback."""
    return {text: row for text, row in decision_rows(body).items() if row.get("basis") in PERSON_TEXT_BASES}


def protected_proof(state: RunState, criterion: dict) -> bool:
    """Recognize this proof in an approved or user-authored saved revision."""
    approvals = {e.get("token") for e in state.get("user_events", []) if e.get("kind") == "goal_approval"}
    for revision in [*(state.get("contract_history") or []), state.get("goal_contract") or {}]:
        rows = (revision.get("body") or {}).get("acceptance_criteria", [])
        if not any(
            row.get("id") == criterion["id"]
            and test_cases.proof(row.get("verification_method")) == test_cases.proof(criterion["verification_method"])
            for row in rows
        ):
            continue
        token = f"r{revision.get('revision')}:{revision.get('hash')}"
        if (
            revision.get("approval_status") == "approved"
            or revision.get("approval_event")
            or token in approvals
            or revision.get("origin") == "user_cli_edit"
        ):
            return True
        if any(
            change.get("item") == criterion["id"]
            and saved_user_basis(state, change.get("basis"), change.get("answer_id"))
            for change in revision.get("declared_changes", [])
        ):
            return True
    return False


def _demarked_literal_suite(before, after):
    """An invalid test-name binding may retain exactly the same literal Python suite."""
    original = str(before or "").strip()
    if test_cases.mark(original) != test_cases.MARK:
        return False
    command = original[len(test_cases.MARK) :].strip()
    return str(after or "").strip() == command and python_tests.parse(command) is not None


def draft_proof_corrections(state: RunState, before: dict, after: dict, reworded=()) -> set[str]:
    """Only planner-generated draft proofs may change without a saved user basis.

    Preserve criterion text, human review and runner-enforced proof requirements.
    Verification mentioned only in free-form task text has no structured provenance.
    A criterion the unapproved-wording allowance may reword may correct its verification with
    the same revision: a live revision aligned AC9's README prose with the user's storage answer,
    kept the request quote, and was still refused because the proof had moved with the wording
    (a live planning revision, 2026-10-21). A real marked test/guard never becomes an unmarked command.
    Only a literal Python suite mistakenly prefixed test: may lose that exact prefix while keeping
    the command identical. A user-protected proof never changes here.
    """
    contract = state.get("goal_contract") or {}
    if contract.get("approval_status") != "draft" or contract.get("approval_event"):
        return set()
    old = {row["id"]: row for row in before.get("acceptance_criteria", [])}
    new = {row["id"]: row for row in after.get("acceptance_criteria", [])}
    marked = lambda method: test_cases.proof(method) != str(method or "").strip()
    return {
        cid
        for cid in old.keys() & new.keys()
        if (old[cid]["criterion"] == new[cid]["criterion"] or cid in reworded)
        and old[cid].get("human_review") == new[cid].get("human_review")
        and str(new[cid].get("verification_method") or "").strip()
        and old[cid]["verification_method"] != new[cid]["verification_method"]
        and not (
            marked(old[cid]["verification_method"])
            and not marked(new[cid]["verification_method"])
            and not _demarked_literal_suite(old[cid]["verification_method"], new[cid]["verification_method"])
        )
        and not protected_proof(state, old[cid])
    }


def draft_own_criteria(previous_contract, previous, body, protected_revisions):
    """Criterion IDs still the Planner's own in a plan nobody has approved yet: in the previous draft, in no
    approved or user-edited revision, and still citing every request quote they cited before."""
    if (
        previous_contract.get("approval_status") != "draft"
        or previous_contract.get("approval_event")
        or previous_contract.get("origin") == "user_cli_edit"
    ):
        return set()
    protected_ids = set()
    for contract in protected_revisions:
        historical_body = contract.get("body")
        rows = historical_body.get("acceptance_criteria") if isinstance(historical_body, dict) else None
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip() for row in rows
        ):
            return set()  # Incomplete legacy history cannot establish that an ID is new.
        protected_ids.update(row["id"] for row in rows)
    old = {row["id"]: row for row in previous.get("acceptance_criteria", []) if isinstance(row, dict) and row.get("id")}
    own = set()
    for row in body.get("acceptance_criteria", []):
        before = old.get(row.get("id")) if isinstance(row, dict) else None
        if not before or row.get("id") in protected_ids:
            continue
        sources = [source for source in before.get("sources") or [] if isinstance(source, dict)]
        if sources and all(source in (row.get("sources") or []) for source in sources):
            own.add(row["id"])
    return own


def unapproved_rewordings(previous_contract, previous, body, protected_revisions):
    """Criterion IDs a planner may reword in a plan nobody has approved yet (#1691).

    The Planner's own first draft can be wrong (a live run miscounted the spaces of an expected
    output), and the next planning step must be able to correct it. In the current draft,
    a criterion's wording may change when it still cites every
    request quote it cited before. IDs present in approved or user-edited history remain protected;
    an earlier turn's approval does not approve a new ID in the current draft.
    """
    old = {row["id"]: row for row in previous.get("acceptance_criteria", []) if isinstance(row, dict) and row.get("id")}
    new = {row["id"]: row for row in body.get("acceptance_criteria", []) if isinstance(row, dict) and row.get("id")}
    return {
        cid
        for cid in draft_own_criteria(previous_contract, previous, body, protected_revisions)
        if old[cid].get("criterion") != new[cid].get("criterion")
    }


def baseline_corrections(state: RunState, previous_contract, previous, body, protected_revisions, changes):
    """Criteria whose current_behavior a revision may change on its own, those a saved user basis backs, and
    the receipts for boxes set to the bound diagnosis text (#2133).

    The box holds what happens today, never the agreed outcome. A change that matches the Investigator's
    bound observation (autocode_current_behavior.evidence) is the runner's own correction: it is recorded as
    a receipt and asks nobody. An empty box may be filled, and the Planner's own unapproved criteria
    (draft_own_criteria) may change it as they may their wording. Any other change needs a saved user
    answer, declared as a rewording of the criterion, as the live run's refusal said (2026-10-25).
    """
    old = {row["id"]: row for row in previous.get("acceptance_criteria", []) if isinstance(row, dict) and row.get("id")}
    new = {row["id"]: row for row in body.get("acceptance_criteria", []) if isinstance(row, dict) and row.get("id")}
    changed = sorted(
        cid for cid in old.keys() & new.keys() if current_behavior.of(old[cid]) != current_behavior.of(new[cid])
    )
    if not changed:
        return set(), set(), []
    found = current_behavior.evidence(state)
    own = draft_own_criteria(previous_contract, previous, body, protected_revisions)
    backed = {
        raw.get("item")
        for raw in changes
        if isinstance(raw, dict)
        and raw.get("change") == "reworded"
        and saved_user_basis(state, raw.get("basis"), raw.get("answer_id"))
    }
    allowed, consumed, receipts = set(), set(), []
    for cid in changed:
        before, after = current_behavior.of(old[cid]), current_behavior.of(new[cid])
        if found and current_behavior.matches(after, found):
            allowed.add(cid)
            receipts.append(current_behavior.receipt(cid, after, found))
        elif not before or cid in own:
            allowed.add(cid)
        elif cid in backed:
            consumed.add(cid)
        else:
            from_evidence = (
                f"copy bug_diagnosis.observed exactly ({found['text'][:120]!r})"
                if found
                else "no reproduced diagnosis is bound to this run, so it cannot be set from evidence"
            )
            raise ValueError(
                f"Changing a protected contract item {cid!r} needs a saved user answer or feedback event; "
                f"change='current_behavior'. {cid}'s current_behavior is what happens today, the Investigator's "
                f"observed behavior, not the agreed outcome: {from_evidence}, or keep the previous text "
                f"({before[:120]!r})."
            )
    return allowed, consumed, receipts


def feedback_interpretations(state: RunState):
    return assumption_provenance.feedback_interpretations(
        state, model_origins=PLANNER_ORIGINS, saved_user_basis=saved_user_basis
    )


def feedback_interpretation_context(state: RunState):
    return assumption_provenance.context(feedback_interpretations(state))


def protected_contract_snapshot(state: RunState):
    """Exact protected fields for a report repair, including saved assumption provenance."""
    body = (state.get("goal_contract") or {}).get("body") or {}
    snapshot = {
        key: copy.deepcopy(body.get(key)) for key in (*PROTECTED_LISTS, "acceptance_criteria", "permission_boundaries")
    }
    editable = {row["text"] for row in feedback_interpretations(state)}
    for key in ("accepted_assumptions", "delegated_decisions"):
        rows = [
            row
            for row in body.get(key) or []
            if row.get("basis") in PERSON_TEXT_BASES and row.get("text") not in editable
        ]
        if rows:
            snapshot[key] = copy.deepcopy(rows)
    return snapshot


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
    # A declared assumption change may wrap its text the same way (`accepted_assumptions: "<text>"`).
    texts |= set(decision_rows(previous))

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


def _criterion_signature(row):
    return (row["criterion"], test_cases.proof(row["verification_method"]), row.get("human_review", False))


def _reject_unchanged_rewordings(previous, body, changes):
    """A false change declaration is a report error, never a person's decision."""
    old = {row["id"]: row for row in previous.get("acceptance_criteria", [])}
    new = {row["id"]: row for row in body.get("acceptance_criteria", [])}
    for row in changes:
        if row["change"] != "reworded":
            continue
        item = row.get("item")
        if item in old:
            unchanged = (
                item in new
                and _criterion_signature(old[item]) == _criterion_signature(new[item])
                and current_behavior.of(old[item]) == current_behavior.of(new[item])
            )
        else:
            owners = [key for key in PROTECTED_LISTS if item in previous.get(key, [])]
            if owners:
                unchanged = all(item in body.get(key, []) for key in owners)
            else:
                unchanged = item in decision_rows(previous) and item in decision_rows(body)
        if unchanged:
            raise ValueError(
                f"contract_changes names {item!r}, which was not changed in the protected contract; drop that row"
            )


def reviewer_backed(state: RunState, item) -> str | None:
    """The id of the saved first review's blocking concern that names criterion ``item``, else None.

    When the Plan Reviewer asked for a change the guard refuses, neither model may make it and neither can
    settle it: the person decides. Asking at once replaces the report repairs a live revision spent on the
    refusal (#1630). Only a blocking concern of the review the revision answers counts, and only one naming
    the criterion id as a whole token."""
    if not isinstance(item, str) or not item:
        return None
    reports = (state.get("planning") or {}).get("reports") or {}
    pattern = re.compile(r"(?<![\w-])" + re.escape(item) + r"(?![\w-])")
    for stage in ("astra_challenge", "plan_review"):
        report = (reports.get(stage) or {}).get("report") or {}
        for concern in report.get("concerns") or []:
            if not isinstance(concern, dict) or not concern.get("blocking") or not concern.get("id"):
                continue
            if pattern.search(" ".join(str(concern.get(key) or "") for key in ("concern", "requested_change"))):
                return str(concern["id"])
    return None


def blocking_review_concerns(state: RunState) -> list[str]:
    """The ids of the blocking concerns in this planning cycle's saved first review, in report order."""
    reports = (state.get("planning") or {}).get("reports") or {}
    ids = []
    for stage in ("astra_challenge", "plan_review"):
        report = (reports.get(stage) or {}).get("report") or {}
        for concern in report.get("concerns") or []:
            if isinstance(concern, dict) and concern.get("blocking") and concern.get("id"):
                ids.append(str(concern["id"]))
    return ids


def _comparable(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().rstrip(".;:!").lower()


def quotes_person_text(state: RunState, item) -> bool:
    """Whether a contract line repeats a sentence of the person's own text, or sits inside one.

    The person's text is what the requirement tracer scans (autocode_requirement_cues.scan_texts): the brief,
    their feedback and their answers, never a model default. A constraint that restates "Python standard
    library only." from the brief is theirs; the Planner's own file split is not."""
    line = _comparable(item)
    if not line:
        return True
    for text in cues.scan_texts(state):
        for sentence in cues.sentences(text):
            said = _comparable(sentence)
            if said and (said in line or line in said):
                return True
    return False


def reviewer_requested_own_text(state: RunState, previous_contract: dict, previous: dict, rows, origin) -> dict:
    """Declared constraint and permission changes a revision may make without the person: {item: concern ids}.

    A live vague-brief run (2026-10-20) put its milestone file split into a constraint and a permission
    boundary; the Plan Reviewer's blocking concern asked to rebalance the milestones, and the revision that did
    so was refused for changing protected text, which cost a repair, a question AutoCode answered itself and a
    planning restart from scratch. In a contract nobody has approved or edited, a ``glm_revise`` may reword or
    drop a constraint, or replace a permission boundary, when the current cycle's first review holds a blocking
    concern, the change is declared as the Planner's own (``agent_proposed``) with its replacement, and the line
    repeats none of the person's sentences (quotes_person_text). The declared change is shown at approval with
    the concern that asked for it, and the final review judges it. Anything the person wrote, every other
    protected list and every approved contract keep the saved-basis rule here; failure_correction owns its
    separately receipted exception."""
    if (
        origin != "glm_revise"
        or previous_contract.get("approval_status") != "draft"
        or previous_contract.get("approval_event")
        or any(
            revision.get("approval_status") == "approved"
            or revision.get("approval_event")
            or revision.get("origin") == "user_cli_edit"
            for revision in state.get("contract_history", [])
        )
    ):
        return {}
    concerns = blocking_review_concerns(state)
    if not concerns:
        return {}
    allowed: dict = {}
    for row in rows:
        item = row.get("item")
        if row.get("basis") != "agent_proposed" or row.get("answer_id") or not isinstance(item, str):
            continue
        owners = [key for key in (*PROTECTED_LISTS, "permission_boundaries") if item in previous.get(key, [])]
        if len(owners) != 1 or owners[0] not in PLANNER_OWN_LISTS:
            continue  # a line that is also a required behavior or an exclusion stays the person's
        replacement = str(row.get("replacement") or "").strip()
        if owners[0] == "constraints":
            declared = row.get("change") == "removed" or (row.get("change") == "reworded" and replacement)
        else:  # dropping a boundary widens what the Builder may touch; only a replacement is the Planner's own
            declared = row.get("change") == "permission_changed" and bool(replacement)
        if not declared or quotes_person_text(state, item):
            continue
        allowed[item] = list(concerns)
    return allowed


def _review_question(state: RunState, previous: dict, body: dict, items, message: str, proposed=None):
    """Raise the person's question for refused changes only they can settle, if every one is.

    A criterion needs a blocking review concern naming it (#1630). An assumption saved from the
    person's answer, feedback or delegation is their own text: the Planner may not change it
    without their answer, and the person is asked even when the contract is still an unapproved
    draft (a live planning revision, 2026-10-21). A declared rewording supplies its proposed text; an undeclared drop is named
    as such.
    """
    rows = []
    for item in items:
        old = next((row for row in previous.get("acceptance_criteria", []) if row.get("id") == item), None)
        if old is None:
            assumption = decision_rows(previous).get(item)
            if assumption is None or assumption.get("basis") not in PERSON_TEXT_BASES:
                return
            rows.append(
                {
                    "item": item,
                    "approved": item,
                    "proposed": str((proposed or {}).get(item) or "").strip() or DROP_ASSUMPTION,
                    "traced": True,
                }
            )
            continue
        concern = reviewer_backed(state, item)
        new = next((row for row in body.get("acceptance_criteria", []) if row.get("id") == item), None)
        if concern is None or new is None:
            return
        rows.append(
            {
                "item": item,
                "approved": old.get("criterion"),
                "proposed": new.get("criterion"),
                "approved_proof": str(old.get("verification_method") or ""),
                "proposed_proof": str(new.get("verification_method") or ""),
                "concern": concern,
            }
        )
    if rows:
        raise rewording.ProtectedRewording(message, rows)


def refusal_hint(previous: dict, body: dict, item, *, approved: bool) -> str:
    """What to change when the guard refuses a criterion's revision, so the repair is one step (#1630).

    The refusals themselves are right: a request quote may not be dropped while a draft nobody approved is
    reworded, and a runner-checked ``test:``/``guard:`` proof may not become an unmarked command. They used to say
    only that a user answer was missing, which sent two live revisions through report repair into a pause."""
    old = next((row for row in previous.get("acceptance_criteria", []) if row.get("id") == item), None)
    new = next((row for row in body.get("acceptance_criteria", []) if row.get("id") == item), None)
    if not old or not new:
        return ""
    notes = []
    kept = new.get("sources") or []
    lost = [source for source in old.get("sources") or [] if source not in kept]
    if old.get("criterion") != new.get("criterion") and not approved and lost:
        notes.append(
            f"{item} may be reworded in a plan nobody approved only while it still cites every request quote it "
            "cited before; it no longer cites "
            + "; ".join(f"[{source.get('source_id')}] {str(source.get('quote', ''))[:80]!r}" for source in lost)
            + ". Keep those citations beside any new ones."
        )
    marked = lambda method: test_cases.proof(method) != str(method or "").strip()
    if marked(old.get("verification_method")) and not marked(new.get("verification_method")):
        notes.append(
            f"{item}'s verification_method names a proof the runner checks ({str(old['verification_method'])[:60]!r}); "
            "a revision cannot replace it with an unmarked command. Keep a test:/guard: name for what that test proves, "
            "and put an aggregate suite run in its own criterion with an ordinary verification_method."
        )
    return " ".join(notes)


def revision_guard(state: RunState, body, changes, origin):
    """A planner revision may not drop protected text or widen permissions on its own.

    Returns the declared changes with each item named exactly (canonical_items)."""
    previous_contract = state.get("goal_contract") or {}
    previous = previous_contract.get("body")
    if origin not in PLANNER_ORIGINS or not previous:
        return changes
    # A new draft may replace an unapproved one. Revising the current draft, or
    # replacing an approved contract, cannot drop protected text on its own.
    internal_restart = any(
        event.get("kind") == "technical_decision"
        and event.get("actor") == "autocode"
        and str(event.get("contract_token", "")).startswith(f"r{previous_contract.get('revision')}:")
        for event in state.get("user_events", [])
    )
    protected_revisions = [
        revision
        for revision in state.get("contract_history", [])
        if revision.get("approval_status") == "approved"
        or revision.get("approval_event")
        or revision.get("origin") == "user_cli_edit"
    ]
    protected_history = bool(protected_revisions)
    interpretation_corrections = assumption_provenance.corrections(feedback_interpretations(state), body)
    if (
        origin in ("glm_draft", "astra_discovery")
        and previous_contract.get("approval_status") != "approved"
        and not (internal_restart and protected_history)
    ):
        return changes
    if not isinstance(changes, list):
        raise ValueError("Planner revision needs contract_changes")
    changes = canonical_items(previous, changes)
    protected.restore_spelling(
        previous, body, {raw.get("item") for raw in changes if isinstance(raw, dict)}, PROTECTED_LISTS
    )
    reworded = unapproved_rewordings(previous_contract, previous, body, protected_revisions)
    old_rows = {row["id"]: row for row in previous.get("acceptance_criteria", [])}
    new_rows = {row["id"]: row for row in body.get("acceptance_criteria", [])}
    allowed_baseline, backed_baseline, receipts = baseline_corrections(
        state, previous_contract, previous, body, protected_revisions, changes
    )
    proof_corrections = draft_proof_corrections(state, previous, body, reworded) - {
        raw.get("item")
        for raw in changes
        if isinstance(raw, dict) and saved_user_basis(state, raw.get("basis"), raw.get("answer_id"))
    }
    example_corrections = examples.corrections(state, previous, body, changes, saved_user_basis)
    # A change that declares an example correction is judged by the example guard alone: its receipt says
    # only the stdout literal moved, so the unapproved-wording allowance (#1691) must not pass one that
    # also changes an input. Declared as a plain rewording, the same text is the Planner's own to correct.
    receipted = {raw.get("item") for raw in changes if isinstance(raw, dict) and raw.get("example_correction")}
    # A row the person backed stays a declared change: the allowance for the Planner's own unapproved
    # wording must not swallow the receipt that authorizes it (#1764).
    user_backed = {
        raw.get("item")
        for raw in changes
        if isinstance(raw, dict) and saved_user_basis(state, raw.get("basis"), raw.get("answer_id"))
    }
    example_corrections |= reworded - receipted - user_backed
    protected_changes = []
    folded = []  # the Planner's rows for a box its receipt records
    for raw in changes:
        if not isinstance(raw, dict) or raw.get("change") not in ("removed", "reworded", "permission_changed"):
            raise ValueError("contract_changes entries need item, change, basis and answer_id")
        if (
            raw.get("item") in proof_corrections
            and raw["change"] == "reworded"
            and raw.get("basis") == "agent_proposed"
            and not raw.get("answer_id")
        ):
            continue  # Older reports declared this engineering correction as a contract delta.
        if (
            raw.get("item") in allowed_baseline
            and raw["change"] == "reworded"
            and raw.get("basis") == "agent_proposed"
            and not raw.get("answer_id")
            and _criterion_signature(old_rows[raw["item"]]) == _criterion_signature(new_rows[raw["item"]])
        ):
            folded.append(raw)  # Only the current_behavior box changed: its receipt records that (#2133).
            continue
        if delta.engineering_delta(raw, previous, body, proof_corrections, PROTECTED_LISTS):
            continue
        if raw.get("item") in example_corrections:
            continue
        if (
            raw.get("item") in interpretation_corrections
            and raw.get("basis") == "agent_proposed"
            and not raw.get("answer_id")
        ):
            continue
        protected_changes.append(raw)
    _reject_unchanged_rewordings(previous, body, protected_changes)
    own_text = reviewer_requested_own_text(state, previous_contract, previous, protected_changes, origin)
    failure_receipts = failure_correction.corrections(
        state, body, protected_changes, origin, interpretation_corrections=interpretation_corrections
    )
    own_text.update({item: [binding["concern_id"]] for item, binding in failure_receipts.items()})
    for raw in protected_changes:
        if raw.get("item") in failure_receipts:
            raw[failure_correction.FIELD] = failure_receipts[raw["item"]]["receipt"]
        if raw.get("item") in own_text:  # shown at approval: the concern that asked for the Planner's own change
            raw["review_concerns"] = own_text[raw["item"]]
    unbacked = [
        raw
        for raw in protected_changes
        if raw.get("item") not in own_text and not saved_user_basis(state, raw.get("basis"), raw.get("answer_id"))
    ]
    if unbacked:
        raw = unbacked[0]
        message = f"Changing a protected contract item {raw.get('item')!r} needs a saved user answer or feedback event; change={raw['change']!r}"
        approved = protected_history or previous_contract.get("approval_status") == "approved"
        if (
            not approved
            and origin == "glm_revise"
            and any(raw.get("item") in previous.get(key, []) for key in PLANNER_OWN_LISTS)
            and quotes_person_text(state, raw.get("item"))
        ):
            message += f"; {str(raw.get('item'))[:80]!r} repeats the person's own words, so only they can change it"
        rewordings = []
        for row in unbacked:
            item = row["item"]
            assumption = decision_rows(previous).get(item)
            proposed = str(row.get("replacement") or "").strip()
            if row["change"] == "removed" and assumption is not None and assumption.get("basis") in PERSON_TEXT_BASES:
                proposed = proposed or DROP_ASSUMPTION
            if row["change"] not in ("reworded", "removed") or not proposed:
                continue
            entry = {"item": item, "approved": rewording.approved_text(previous, item), "proposed": proposed}
            if assumption is not None:
                entry["traced"] = True
            rewordings.append(entry)
        if (
            approved
            and len(rewordings) == len(unbacked)
            and all(row["approved"] and row["proposed"] for row in rewordings)
        ):
            raise rewording.ProtectedRewording(message, rewordings)  # the person is asked (#1691)
        if not approved:
            _review_question(  # (#1630, live revision loop 2026-10-21)
                state,
                previous,
                body,
                [row["item"] for row in unbacked],
                message,
                proposed={row["item"]: row.get("replacement") for row in unbacked},
            )
        hint = refusal_hint(previous, body, raw.get("item"), approved=approved)
        raise ValueError(f"{message}. {hint}" if hint else message)
    declared: dict = {}
    for raw in protected_changes:
        declared.setdefault(raw["item"], []).append(raw)

    def consume(item, kind):
        rows = declared.get(item, [])
        match = next((row for row in rows if row["change"] == kind), None)
        if match is None:
            if not (protected_history or previous_contract.get("approval_status") == "approved"):
                _review_question(
                    state,
                    previous,
                    body,
                    [item],
                    f"Planner revision changes {item!r} as the Plan Reviewer asked, without a user-backed contract change",
                )
            hint = refusal_hint(
                previous,
                body,
                item,
                approved=protected_history or previous_contract.get("approval_status") == "approved",
            )
            raise ValueError(
                f"Planner revision drops or changes {item!r} without a user-backed contract change"
                + (f". {hint}" if hint else "")
            )
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
            _, replacement = consume(
                item, "reworded" if any(row["change"] == "reworded" for row in declared.get(item, [])) else "removed"
            )
            if replacement and replacement not in body.get(key, []):
                raise ValueError(f"Rewording {item!r} must appear in {key}")
    comparable = [
        dict(row, verification_method=new_rows[row["id"]]["verification_method"])
        if row["id"] in proof_corrections
        else row
        for row in previous.get("acceptance_criteria", [])
    ]
    comparable = [
        dict(row, criterion=new_rows[row["id"]]["criterion"]) if row["id"] in example_corrections else row
        for row in comparable
    ]
    if previous_contract.get("approval_status") == "draft" and not previous_contract.get("approval_event"):
        comparable = [
            dict(row, human_review=True)
            if (
                not row.get("human_review")
                and new_rows.get(row["id"], {}).get("human_review") is True
                and not declared.get(row["id"])
                and not protected_proof(state, row)
            )
            else row
            for row in comparable
        ]
    old_criteria = {row["id"]: _criterion_signature(row) for row in comparable}
    new_criteria = {cid: _criterion_signature(row) for cid, row in new_rows.items()}
    for cid, text in old_criteria.items():
        if new_criteria.get(cid) == text:
            if cid in backed_baseline:
                consume(cid, "reworded")  # a user-backed change confined to current_behavior (#2133)
            continue
        consume(cid, "removed" if cid not in new_criteria else "reworded")
    previous_permissions = previous.get("permission_boundaries", [])
    current_permissions = body.get("permission_boundaries", [])
    if previous_permissions and set(previous_permissions) != set(current_permissions):
        # One row per changed boundary, as the Planner is asked: the previous text as its item and the
        # new text as its replacement. A boundary added with no such replacement needs a row of its own.
        replaced = set()
        for item in sorted(set(previous_permissions) - set(current_permissions)):
            replacement = next(
                (
                    str(row.get("replacement") or "").strip()
                    for row in declared.get(item, [])
                    if row["change"] == "permission_changed"
                ),
                "",
            )
            consume(item, "permission_changed")
            if replacement and replacement not in {text.strip() for text in current_permissions}:
                raise ValueError(f"Permission replacement {replacement!r} must appear in permission_boundaries")
            replaced.add(replacement)
        for item in sorted(set(current_permissions) - set(previous_permissions)):
            if any(row["change"] == "permission_changed" for row in declared.get(item, [])):
                consume(item, "permission_changed")
            elif item.strip() not in replaced:
                raise ValueError(
                    f"Planner revision adds permission boundary {item!r} without a user-backed "
                    "permission_changed entry; declare it as the replacement of the boundary it changes"
                )
    kept = {
        row.get("text")
        for key in ("accepted_assumptions", "delegated_decisions")
        for row in body.get(key, [])
        if isinstance(row, dict)
    }
    # An assumption saved from the person's answer is their own text: a revision may not drop
    # or reword one without a saved basis. Its own agent_proposed assumptions are engineering detail
    # and are recognized as an engineering delta above.
    added = [text for text in decision_rows(body) if text not in decision_rows(previous)]
    for text in user_backed_assumptions(previous):
        if text in interpretation_corrections:
            continue
        if text in kept:
            continue
        kind = "reworded" if any(row["change"] == "reworded" for row in declared.get(text, [])) else "removed"
        authorizing = next((row for row in declared.get(text, []) if row["change"] == kind), None)
        if (
            authorizing is None
            and len(added) == 1
            and not (protected_history or previous_contract.get("approval_status") == "approved")
        ):
            # An undeclared rewording: name the text that replaced it, not a drop.
            _review_question(
                state,
                previous,
                body,
                [text],
                f"Planner revision changes {text!r} without a user-backed contract change",
                proposed={text: added[0]},
            )
        _, replacement = consume(text, kind)
        if replacement is None or authorizing is None:
            continue
        rows = [row for row in decision_rows(body).values() if row.get("text") == replacement]
        if not rows:
            raise ValueError(
                f"Rewording {text!r} declares replacement {replacement!r}, which the revision does not keep"
            )
        # The person's answer authorized this wording, so it stays theirs: saved as agent_proposed, the next
        # revision could change it again as the Planner's own detail.
        for row in rows:
            row["basis"], row["answer_id"] = authorizing["basis"], authorizing["answer_id"]
    # A user-backed row for an assumption or a delegated decision the revision really dropped is a true
    # record of the change, though neither list is protected.
    for key in ("accepted_assumptions", "delegated_decisions"):
        for row in previous.get(key, []):
            if isinstance(row, dict) and row.get("text") not in kept:
                declared.pop(row.get("text"), None)
    left = sorted(item for item, rows in declared.items() if rows)
    if left:
        raise ValueError(
            f"contract_changes names {left[0]!r}, which was not changed in the protected contract; drop that row"
        )
    return [*(row for row in changes if not any(row is folded_row for folded_row in folded)), *receipts]

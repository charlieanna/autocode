"""Ask the person what to do when a request's work spans another repository (#2066, step 3).

Requirements reports the parts of a request that belong to another repository, and the runner keeps
only the grounded ones (autocode_workspace_scope). This module turns a kept part into one
nondelegable question before any plan, and reads the person's answer back:

- ``question(state)``: the open scope question, or None. Its answer must be one of its options
  (the CLI refuses any other text and offers --feedback instead);
- ``decision(state)``: ``"this_repository"`` for the person's exact answer,
  ``"linked_repositories"`` for a current core-owned assignment, else None;
- ``uncovered(state)``: the parts the person left out of this run, which the plan must list in its
  scope_exclusions (``exclusion_errors``) and the status view shows (``view``).

A run in the other repository is linked outside this run (``autocode changeset link``,
autocode_changeset); this run still records that part as not covered here. Depends only on
workspace-scope policy, so the question gate and contract checks can both use it.

The person may correct one authenticated boundary's wording with a saved answer
(``effective_exclusions``): the exact item moves to the replacement text, which still names the
same peer target and stays enforced in every later draft. The correction is recorded as a
receipt bound to its scope and assignment when a contract that carries the replacement installs
(``record_installed_corrections``); a removal, a moved boundary, an unbacked declaration or a
changed scope enforces the original instead (#2467).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

from pathlib import Path

try:
    from . import autocode_scope_assignment as assignment
    from . import autocode_user_authority as user_authority
    from . import autocode_util as util
    from . import autocode_workspace_scope_state as workspace_scope
except ImportError:
    import autocode_scope_assignment as assignment
    import autocode_user_authority as user_authority
    import autocode_util as util
    import autocode_workspace_scope_state as workspace_scope

PREFIX = "workspace-scope-"
THIS_REPOSITORY = "This repository only"
EXCLUSION_PREFIX = "Not covered: changes in "
# Receipts for person-authorized exact rewords of authenticated peer exclusions (#2467).
# Run-state key ``scope_exclusion_corrections``: written only by
# autocode_goal_lifecycle.install_draft through record_installed_corrections, which
# validates each row independently of the revision guard so an unguarded discovery
# or replacement draft gains nothing by declaring one; read only by
# effective_exclusions, which re-binds every receipt to the workspace-scope
# identity, the turn and the authenticated assignment digest it was recorded under.
CORRECTIONS_KEY = "scope_exclusion_corrections"


def _scope(state: RunState) -> dict:
    return workspace_scope.current(state)


def parts(state: RunState) -> list[dict]:
    """The validated external parts of the current handoff; [] when it spans nothing."""
    scope = _scope(state)
    if not scope.get("spans_repositories"):
        return []
    return [part for part in scope.get("external_parts") or [] if isinstance(part, dict) and part.get("target")]


def decision(state: RunState) -> str | None:
    """The person's exact choice, else a current linked assignment, else None."""
    offered = _question(state)
    if not offered:
        return None
    for answer in (state.get("answers") or {}).values():
        if (
            not isinstance(answer, dict)
            or answer.get("actor") != "user_cli"
            or answer not in state.get("user_events", [])
        ):
            continue
        question = answer.get("question") or {}
        same = question == offered
        # An authenticated old receipt remains valid for the exact same displayed
        # work, before any follow-up. It never accepts an unbound text-only answer.
        legacy = (
            not state.get("turns")
            and not workspace_scope.intent(state)["feedback"]
            and str(question.get("id", "")).removeprefix(PREFIX).isdigit()
        )
        if legacy:
            same = {**question, "id": offered["id"]} == offered
        if same and " ".join(str(answer.get("text") or "").split()).casefold() == THIS_REPOSITORY.casefold():
            return "this_repository"
    return "linked_repositories" if assignment.current(state) else None


def question(state: RunState) -> dict | None:
    """The scope question the person has not answered yet, or None."""
    return None if decision(state) else _question(state)


def matches(state: RunState, offered: dict) -> bool:
    """Only the current scope's exact question may receive an answer."""
    current = _question(state)
    if not current:
        return False
    if offered == current:
        return True
    legacy = (
        not state.get("turns")
        and not workspace_scope.intent(state)["feedback"]
        and str(offered.get("id", "")).removeprefix(PREFIX).isdigit()
    )
    return bool(legacy and {**offered, "id": current["id"]} == current)


def check_answer(state: RunState, offered: dict) -> None:
    if str(offered.get("id", "")).startswith(PREFIX) and not matches(state, offered):
        raise ValueError("The scope changed after this question was shown; answer the current scope question")


def execution_guard(state: RunState) -> None:
    if question(state):
        raise util.Paused(
            "PAUSED_SCOPE_DECISION", "Work in another repository still needs the person's current scope decision"
        )


def _question(state: RunState) -> dict | None:
    found = parts(state)
    if not found:
        return None
    lines = [f"- {part['target']}: {part.get('work', '')}" for part in found]
    return {
        "id": PREFIX + workspace_scope.identity(state),
        "question": (
            "Part of this request belongs to another repository, and this run can change only this one:\n"
            + "\n".join(lines)
            + f"\nAnswer '{THIS_REPOSITORY}' to plan and deliver this repository's part, with the rest recorded "
            "as not covered, or give feedback to change the request. To stop here instead, leave this unanswered: "
            "the run's status lists a ready brief for each repository (scope.briefs)."
        ),
        "why": "A run in this repository alone cannot deliver the other repository's part.",
        "options": [THIS_REPOSITORY],
        "proposed_default": "",
        "kind": "decision",
        "category": "requested_outcome",
        "delegable": False,
    }


def uncovered(state: RunState) -> list[dict]:
    """The external parts the person chose to leave out of this run."""
    if decision(state) not in ("this_repository", "linked_repositories"):
        return []
    return [{"target": part["target"], "work": part.get("work", "")} for part in parts(state)]


def exclusion(part: dict) -> str:
    """The scope_exclusions entry that records one uncovered part."""
    return f"{EXCLUSION_PREFIX}{part['target']}: {part.get('work', '')}".rstrip(": ")


def retain_assigned_exclusions(state: RunState, contract, changes=()):
    """Keep exact core-owned edit boundaries in an automatic member's draft.

    Assignment authenticates every label to an existing peer run. Those factual
    exclusions need no model inference or user waiver. Preserve every work item,
    including differently worded items for aliases of one peer, and every authored
    entry. A person-authorized exact rewording keeps the boundary and moves it to
    the corrected text (``effective_exclusions``): the corrected string is what is
    retained, never the superseded one. Manual choices and malformed drafts retain
    their ordinary checks.
    """
    if not isinstance(contract, dict):
        return contract
    listed = contract.get("scope_exclusions")
    if not isinstance(listed, list) or not all(isinstance(row, str) for row in listed):
        return contract
    if decision(state) != "linked_repositories":
        return contract
    missing = [row for row in dict.fromkeys(effective_exclusions(state, contract, changes)) if row not in listed]
    return {**contract, "scope_exclusions": [*listed, *missing]} if missing else contract


def _assignment_digest(state: RunState):
    """A comparison token for the authenticated assignment's member and alias mapping.

    None when no current assignment authenticates the scope. Only the digest is
    stored and compared; member run destinations and aliases never become path or
    remote authority through this binding.
    """
    receipt = assignment.current(state)
    if not isinstance(receipt, dict):
        return None
    return util.digest({"members": receipt.get("members") or {}, "aliases": receipt.get("aliases") or {}})[:16]


def _installed_corrections(state: RunState):
    """Correction receipts that still bind to the current scope and assignment, oldest first.

    A receipt exists only for a correction ``record_installed_corrections`` established
    when a contract installed; a draft's own declaration, or a row merely sitting in
    contract history, is model prose until then. Each receipt names the workspace-scope
    identity and turn it was installed under and a digest of the assignment receipt that
    authenticated the scope then, so a renewed turn, new feedback intent, or a
    coordinator re-assignment with different member destinations or aliases can never
    borrow an older correction. Malformed rows are inert.
    """
    scope = _scope(state)
    if not scope.get("spans_repositories"):
        return []
    binding = {"scope_identity": workspace_scope.identity(state), "turn": scope.get("turn")}
    applicable = []
    for receipt in state.get(CORRECTIONS_KEY) or []:
        if (
            not isinstance(receipt, dict)
            or set(receipt) != {"item", "replacement", "scope_identity", "turn", "assignment", "revision"}
            or not isinstance(receipt.get("item"), str)
            or not isinstance(receipt.get("replacement"), str)
            or not receipt["item"]
            or not receipt["replacement"]
            or receipt.get("scope_identity") != binding["scope_identity"]
            or receipt.get("turn") != binding["turn"]
            or not isinstance(receipt.get("assignment"), str)
            or receipt["assignment"] != _assignment_digest(state)
        ):
            continue
        applicable.append({"item": receipt["item"], "replacement": receipt["replacement"]})
    return applicable


def _candidate_corrections(state: RunState, contract, changes):
    """The report's own reword declarations that carry a saved person's basis and replacement.

    The report being checked counts only here, in the pass that performs the
    correction: expansion and the scope gate run before the revision guard consumes
    those rows, so without this the superseded text would be re-added and the
    person's correction refused (#2467). A candidate row must declare itself a
    ``reworded`` change — a removal or any other declared type never becomes a
    correction, whatever text accompanies it — cite a basis the person actually
    saved, and carry its replacement in the contract being checked; whether it names
    one current part's exact effective text and stays inside that peer's target is
    judged by ``_advance``, and anything else fails closed to the original
    obligation instead of adopting a new one.
    """
    listed = contract.get("scope_exclusions") if isinstance(contract, dict) else None
    if not isinstance(listed, list) or not isinstance(changes, list):
        return []
    candidates = []
    for row in changes:
        if not isinstance(row, dict) or row.get("change") != "reworded":
            continue
        replacement = str(row.get("replacement") or "").strip()
        if (
            not isinstance(row.get("item"), str)
            or not row["item"]
            or not replacement
            or replacement not in listed
            or not user_authority.saved_user_basis(state, row.get("basis"), row.get("answer_id"))
        ):
            continue
        candidates.append({"item": row["item"], "replacement": replacement})
    return candidates


def _advance(effective, found, rows):
    """Move each validated exact reword onto every exact instance of its boundary text.

    An accumulated scope can hold two parts of one peer whose effective exclusion
    text is currently identical; a correction of that exact text moves every
    instance, while any distinct other peer, work item or alias stays untouched.
    Only rows that matched at least one current text are returned as applied.
    """
    applied = []
    for row in rows:
        matched = False
        for index, part in enumerate(found):
            if row["item"] == effective[index] and _names_target(row["replacement"], str(part["target"])):
                effective[index] = row["replacement"]
                matched = True
        if matched:
            applied.append(row)
    return applied


def record_installed_corrections(state: RunState, body, changes, revision):
    """Record the exact-reword receipts an installed contract established (#2467).

    Single writer, called by ``install_draft`` with the installed body and the
    declared changes the report carried: no other path may add a receipt. The
    revision guard stays the ordinary gate for guarded origins; this validator
    independently establishes the same facts for every origin, so a discovery or
    replacement draft whose declarations the guard never judged still records
    nothing unless the row declares a reword, the person's saved basis, the current
    authenticated assignment, the part's exact current text, a same-peer
    replacement and that replacement's presence in the installed body all hold.
    Each receipt is bound to the workspace-scope identity, the turn, and a digest
    of the assignment receipt that authenticated the scope when it was recorded,
    and one logical transition is recorded once.
    """
    listed = body.get("scope_exclusions") if isinstance(body, dict) else None
    if not isinstance(listed, list) or not isinstance(changes, list):
        return
    found = uncovered(state)
    if not found or decision(state) != "linked_repositories":
        return
    binding = {
        "scope_identity": workspace_scope.identity(state),
        "turn": len(state.get("turns") or []),
        "assignment": _assignment_digest(state),
    }
    if binding["assignment"] is None:
        return
    effective = [exclusion(part) for part in found]
    _advance(effective, found, _installed_corrections(state))
    recorded = state.setdefault(CORRECTIONS_KEY, [])
    for row in _advance(effective, found, _candidate_corrections(state, body, changes)):
        if any(
            existing.get("item") == row["item"]
            and existing.get("replacement") == row["replacement"]
            and existing.get("scope_identity") == binding["scope_identity"]
            and existing.get("turn") == binding["turn"]
            and existing.get("assignment") == binding["assignment"]
            for existing in recorded
        ):
            continue
        recorded.append(
            {
                "item": row["item"],
                "replacement": row["replacement"],
                "scope_identity": binding["scope_identity"],
                "turn": binding["turn"],
                "assignment": binding["assignment"],
                "revision": revision,
            }
        )


def _names_target(text: str, target: str) -> bool:
    """Whether an exclusion string describes work in exactly ``target`` and nothing wider."""
    if not text.startswith(f"{EXCLUSION_PREFIX}{target}"):
        return False
    rest = text[len(EXCLUSION_PREFIX) + len(target) :]
    # exclusion() drops a trailing ": " when a part carries no work text; both that
    # shape and ": <work>" name this target alone. A longer target that merely starts
    # with this name (an alias) does not.
    return rest == "" or (rest.startswith(":") and bool(rest[1:].strip()))


def effective_exclusions(state: RunState, contract=None, changes=()) -> list[str]:
    """The exclusion strings this run's current authenticated scope must list.

    Each uncovered part contributes ``exclusion(part)`` unless the person's saved
    answer or feedback authorized an exact rewording of that exact string to a
    replacement that still names the same peer target; the boundary then moves to
    the corrected text instead of disappearing. Corrections apply only while a
    current core-owned assignment authenticates the scope. Installed receipts come
    first (oldest first, so a chain of corrections follows in order); the report
    being checked contributes its own validated declarations only while its
    contract carries each replacement (``contract``), which expansion and the scope
    gate pass because they run before the revision guard consumes those rows. A
    removal, a cross-target replacement, an unbacked or stale basis, a missing
    replacement, or a row naming anything but the part's current exact string
    changes nothing: the original obligation stays enforced for the guard to
    refuse or the person to settle. Receipts are bound to the workspace-scope
    identity, the turn and the authenticated assignment they were recorded under,
    so a repeated replan or a reopen in the same scope keeps a correction while a
    changed assignment, a renewed turn or new feedback intent does not (#2467).
    """
    found = uncovered(state)
    if not found or decision(state) != "linked_repositories":
        return [exclusion(part) for part in found]
    effective = [exclusion(part) for part in found]
    installed = _installed_corrections(state)
    _advance(effective, found, installed)
    if contract is not None:
        candidates = _candidate_corrections(state, contract, list(changes or []))
        _advance(effective, found, candidates)
    return effective


def exclusion_errors(state: RunState, contract: dict, changes=()) -> list[str]:
    """One error per uncovered part whose effective exclusion the contract does not name."""
    listed = set(str(row) for row in (contract or {}).get("scope_exclusions") or [])
    return [
        f"scope_exclusions must record that this run does not cover {part['target']}; add {text!r}"
        for part, text in zip(uncovered(state), effective_exclusions(state, contract, changes), strict=True)
        if text not in listed
    ]


def check(state: RunState, contract: dict, changes=()) -> None:
    """Refuse a plan that does not record each part the person left out of this run."""
    if question(state):
        raise ValueError("Work in another repository needs the person's current scope decision before planning")
    errors = exclusion_errors(state, contract, changes)
    if errors:
        raise ValueError("; ".join(errors))


def briefs(state: RunState) -> list[dict]:
    """A ready brief for each repository the request spans, this one first; [] when it spans nothing.

    Each is plain text a person can start a run with in that repository. Nothing is started: running
    them, and keeping the two sides consistent, stays with the person until runs can be linked."""
    found = parts(state)
    if not found:
        return []
    task = " ".join(str(state.get("task") or "").split())
    here = Path(str(state.get("workspace") or "")).name or "this repository"
    elsewhere = "; ".join(f"{part['target']}: {part.get('work', '')}" for part in found)
    rows = [
        {
            "repository": here,
            "brief": f"{task}\n\nNot covered here, a separate run in each repository: {elsewhere}.",
        }
    ]
    grouped: dict[str, list[str]] = {}
    for part in found:
        grouped.setdefault(part["target"], []).append(part.get("work", ""))
    for target, work in grouped.items():
        rows.append(
            {
                "repository": target,
                "brief": (
                    f"In {target}: {'; '.join(work)}.\n\nThis is the {target} side of a request "
                    f"made in {here}: {task}\n\nThis run owns only {target}'s side. Keep the interface the "
                    f"two sides share (field names, payloads, schemas) as the {here} run expects it."
                ),
            }
        )
    return rows


def view(state: RunState, dropped=None) -> dict:
    """The status view's ``scope``: what the request spans and what this run leaves out."""
    found = parts(state)
    return {
        "identity": workspace_scope.identity(state),
        "spans_repositories": bool(found),
        "external_parts": [{"target": part["target"], "work": part.get("work", "")} for part in found],
        "uncovered": [part["target"] for part in uncovered(state)],
        "decision": decision(state),
        "dropped": list(dict.fromkeys([*(_scope(state).get("dropped") or []), *(dropped or [])])),
        "linked": [],
        "briefs": briefs(state),
    }

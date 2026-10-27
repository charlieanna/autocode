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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

from pathlib import Path

try:
    from . import autocode_scope_assignment as assignment
    from . import autocode_util as util
    from . import autocode_workspace_scope_state as workspace_scope
except ImportError:
    import autocode_scope_assignment as assignment
    import autocode_util as util
    import autocode_workspace_scope_state as workspace_scope

PREFIX = "workspace-scope-"
THIS_REPOSITORY = "This repository only"
EXCLUSION_PREFIX = "Not covered: changes in "


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


def exclusion_errors(state: RunState, contract: dict) -> list[str]:
    """One error per uncovered part the contract's scope_exclusions do not name."""
    listed = set(str(row) for row in (contract or {}).get("scope_exclusions") or [])
    return [
        f"scope_exclusions must record that this run does not cover {part['target']}; add {exclusion(part)!r}"
        for part in uncovered(state)
        if exclusion(part) not in listed
    ]


def check(state: RunState, contract: dict) -> None:
    """Refuse a plan that does not record each part the person left out of this run."""
    if question(state):
        raise ValueError("Work in another repository needs the person's current scope decision before planning")
    errors = exclusion_errors(state, contract)
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

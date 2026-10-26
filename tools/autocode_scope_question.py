"""Ask the person what to do when a request's work spans another repository (#2066, step 3).

Requirements reports the parts of a request that belong to another repository, and the runner keeps
only the grounded ones (autocode_workspace_scope). This module turns a kept part into one
nondelegable question before any plan, and reads the person's answer back:

- ``question(state)``: the open scope question, or None. Its answer must be one of its options
  (the CLI refuses any other text and offers --feedback instead);
- ``decision(state)``: ``"this_repository"`` once an answer chose it, else None;
- ``uncovered(state)``: the parts the person left out of this run, which the plan must list in its
  scope_exclusions (``exclusion_errors``) and the status view shows (``view``).

A run in the other repository is linked outside this run (``autocode changeset link``,
autocode_changeset); this run still records that part as not covered here. Imports nothing from AutoCode, so the question gate
(autocode_requirements_review) and the contract checks (autocode_goals) can both use it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

from pathlib import Path

PREFIX = "workspace-scope-"
THIS_REPOSITORY = "This repository only"
EXCLUSION_PREFIX = "Not covered: changes in "


def _scope(state: RunState) -> dict:
    report = (state.get("requirements_handoff") or {}).get("report") or {}
    scope = report.get("workspace_scope")
    return scope if isinstance(scope, dict) else {}


def parts(state: RunState) -> list[dict]:
    """The validated external parts of the current handoff; [] when it spans nothing."""
    scope = _scope(state)
    if not scope.get("spans_repositories"):
        return []
    return [part for part in scope.get("external_parts") or [] if isinstance(part, dict) and part.get("target")]


def _answers(state: RunState) -> list[tuple[str, str]]:
    """The saved answers to scope questions, oldest first, as (question id, text)."""
    rows = []
    for key, answer in (state.get("answers") or {}).items():
        if not str(key).startswith(PREFIX):
            continue
        text = answer.get("text") if isinstance(answer, dict) else answer
        rows.append((str(key), str(text or "")))
    return sorted(rows, key=lambda row: int(row[0][len(PREFIX) :]) if row[0][len(PREFIX) :].isdigit() else 0)


def decision(state: RunState) -> str | None:
    """The person's choice for this run's scope, or None while no answer names one."""
    chosen = {" ".join(text.split()).casefold() for _, text in _answers(state)}
    return "this_repository" if THIS_REPOSITORY.casefold() in chosen else None


def question(state: RunState) -> dict | None:
    """The scope question the person has not answered yet, or None."""
    found = parts(state)
    if not found or decision(state):
        return None
    lines = [f"- {part['target']}: {part.get('work', '')}" for part in found]
    return {
        "id": f"{PREFIX}{len(_answers(state)) + 1}",
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
    if decision(state) != "this_repository":
        return []
    return [{"target": part["target"], "work": part.get("work", "")} for part in parts(state)]


def exclusion(part: dict) -> str:
    """The scope_exclusions entry that records one uncovered part."""
    return f"{EXCLUSION_PREFIX}{part['target']}: {part.get('work', '')}".rstrip(": ")


def exclusion_errors(state: RunState, contract: dict) -> list[str]:
    """One error per uncovered part the contract's scope_exclusions do not name."""
    listed = " ".join(str(row) for row in (contract or {}).get("scope_exclusions") or [])
    return [
        f"scope_exclusions must record that this run does not cover {part['target']}; add {exclusion(part)!r}"
        for part in uncovered(state)
        if f"{EXCLUSION_PREFIX}{part['target']}" not in listed
    ]


def check(state: RunState, contract: dict) -> None:
    """Refuse a plan that does not record each part the person left out of this run."""
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
    for part in found:
        rows.append(
            {
                "repository": part["target"],
                "brief": (
                    f"In {part['target']}: {part.get('work', '')}.\n\nThis is the {part['target']} side of a request "
                    f"made in {here}: {task}\n\nThis run owns only {part['target']}'s side. Keep the interface the "
                    f"two sides share (field names, payloads, schemas) as the {here} run expects it."
                ),
            }
        )
    return rows


def view(state: RunState, dropped=None) -> dict:
    """The status view's ``scope``: what the request spans and what this run leaves out."""
    found = parts(state)
    return {
        "spans_repositories": bool(found),
        "external_parts": [{"target": part["target"], "work": part.get("work", "")} for part in found],
        "uncovered": [part["target"] for part in uncovered(state)],
        "decision": decision(state),
        "dropped": list(dropped or []),
        "linked": [],
        "briefs": briefs(state),
    }

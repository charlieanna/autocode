"""Which of a run's assumptions the person must confirm before planning (#3603).

One rule, read from one place by the requirements gate, the technical-question settlement and
the Planner's own assumptions. An assumption is a choice nobody stated. Who may make it depends
on what it changes, not on the ``kind`` label the model gave it:

- a user-visible behavior (category ``behavior``: edge cases, error semantics, output, CLI and
  API shapes, compatibility), anything unclassified (``other``), or a protected choice (cost,
  quota, permission, external side effect, the requested outcome) is the person's. It counts as
  confirmed only when the person confirmed its id, or it cites a saved answer or feedback event
  of theirs (which also settles any other assumption: the person said so);
- an engineering convention (category ``technical``) is AutoCode's when it cites a workspace
  citation the stage actually read (``autocode_code_citations.verify_citation``). An empty
  workspace has nothing to read, and that function accepts any reference there: in a greenfield
  project an engineering choice is AutoCode's own, while a behavior still goes to the person.

Inference is kept apart from delegation on purpose. ``NON_INFERABLE_CATEGORIES`` also decides
which questions a person (or an unattended ``--skip-requirements`` run, which is the person's own
standing delegation) may accept by default. A behavior default stays delegable: the person who
delegates has decided. What this module closes is the model deciding for them without asking.

``unconfirmed`` is pure: the caller injects ``verify`` and ``cites_person``. ``verifier`` and
``cites_person`` bind them to run state; they import nothing above the citation and authority
rules.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_code_citations as code_citations
    from .autocode_user_authority import saved_user_basis
except ImportError:
    import autocode_code_citations as code_citations
    from autocode_user_authority import saved_user_basis

ASSUMPTION_CATEGORIES = (
    "cost",
    "quota",
    "permission",
    "external_side_effect",
    "requested_outcome",
    "behavior",
    "technical",
    "other",
)
# A choice in one of these categories changes cost, quota, permissions, external
# side effects, or the user's literal requested outcome. It may never be silently
# inferred from convention, and it may never be bulk-delegated.
NON_INFERABLE_CATEGORIES = frozenset({"cost", "quota", "permission", "external_side_effect", "requested_outcome"})
# Who confirms an assumption. Only an engineering convention may rest on the code; every other
# category is the person's to confirm. Delegation keeps its own, narrower set above.
CONVENTION_CATEGORIES = frozenset({"technical"})
PERSON_CATEGORIES = frozenset(ASSUMPTION_CATEGORIES) - CONVENTION_CATEGORIES
# The planning stages whose reads may evidence a Planner's or Requirements' citation.
PLANNING_STAGES = ("requirements_gather", "draft_plan", "revise_plan", "review_plan", "finalize_plan")


def normalize(row):
    """A legacy proposed_assumptions entry (plain string) becomes an unstructured,
    id-less record: displayed and counted, but never evidenced and never a
    target for --reject-assumption."""
    if isinstance(row, str):
        return {
            "id": None,
            "text": row,
            "kind": "inferable",
            "category": "requested_outcome",
            "convention_ref": "",
            "rationale": "",
            "supports": [],
            "legacy": True,
        }
    if not isinstance(row, dict):
        raise ValueError("proposed_assumptions entries must be a string or an object")
    return {
        "id": row.get("id"),
        "text": str(row.get("text", "")),
        "kind": row.get("kind", "inferable"),
        "category": row.get("category", "requested_outcome"),
        "convention_ref": str(row.get("convention_ref", "")),
        "rationale": str(row.get("rationale", "")),
        "supports": list(row.get("supports") or []),
        "legacy": False,
    }


def reason(row, *, verify: Callable[[str], str | None], cites_person: Callable[[str], bool]) -> str | None:
    """Why the person must confirm one normalized assumption, or None when it may stand."""
    if row["legacy"]:
        return "an unstructured assumption names no category or evidence"
    ref = row["convention_ref"].strip()
    if ref and cites_person(ref):
        return None  # the person's own answer or feedback said so
    category = row["category"] if row["category"] in ASSUMPTION_CATEGORIES else "requested_outcome"
    if category in PERSON_CATEGORIES:
        return f"a {category} choice is the person's to confirm"
    failure = verify(ref)
    if failure is None:
        return None
    return f"the engineering convention is not evidenced by code this run read: {failure}"


def unconfirmed(
    assumptions: Iterable,
    *,
    confirmed_ids: Iterable,
    verify: Callable[[str], str | None],
    cites_person: Callable[[str], bool] = lambda ref: False,
) -> list[dict]:
    """The assumptions that need the person, normalized, each with its ``reason``, in input order."""
    confirmed = {item for item in confirmed_ids if item}
    rows = []
    for raw in assumptions:
        row = normalize(raw)
        if row["id"] and row["id"] in confirmed:
            continue
        why = reason(row, verify=verify, cites_person=cites_person)
        if why:
            rows.append({**row, "reason": why})
    return rows


def verifier(state: RunState, stage: str | tuple[str, ...] | None = "requirements_gather"):
    """``verify`` bound to the citations the stage's provider actually read this turn."""
    return lambda ref: code_citations.verify_citation(state, stage, ref, field="convention_ref")


def cites_person(state: RunState):
    """``cites_person`` bound to the person's saved answers and authenticated feedback."""
    return lambda ref: saved_user_basis(state, "user_answer", ref) or saved_user_basis(state, "user_feedback", ref)

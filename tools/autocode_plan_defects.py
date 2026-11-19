"""Keep a plan's own defects out of the person's questions (#3818).

A planning stage sometimes asks the person whether the plan will be corrected: two of its parts
contradict each other, or a criterion cannot be carried out as written (an HTTP/2 PING test with a
nine-byte payload). Such a question has one answer a person could give, "correct it", and it costs a
round trip or, unattended, the whole run. The plan is the Planner's to correct.

A stage asks such a question with ``kind="plan_defect"`` and names, in its question or why, the ids of
the acceptance criteria or milestones it is about. The label alone settles nothing: the runner checks
that at least one named id is a criterion or milestone of the plan the stage returned. What AutoCode
records is a correction request it writes itself, never one of the question's options: the Planner
revises those parts so they agree and can be carried out, keeping every requested outcome, and ordinary
plan review and the person's plan approval follow. The decision is a ``technical_decision`` event
(``settled_as: plan_defect``), the receipt the Planner, the Plan Reviewer and the revision guard already
read for AutoCode's own defaults (autocode_technical_questions).

A ``plan_defect`` question AutoCode does not settle reaches the person as an ordinary decision, with the
reason in its why: one that names no part of the plan, one about a rejected-assumption obligation, or a
second plan defect after AutoCode settled one since the person last answered, so a Planner that cannot
correct its plan never loops.
"""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

KIND = "plan_defect"
USER_INPUT = ("answer", "brief_feedback", "goal_edit")


def plan_ids(body) -> set[str]:
    """The ids of the plan's acceptance criteria and milestones."""
    return {
        row["id"]
        for key in ("acceptance_criteria", "milestones")
        for row in (body or {}).get(key) or []
        if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"].strip()
    }


def named_parts(question, body) -> list[str]:
    """The plan's criterion and milestone ids the question names as whole words in its question or why."""
    text = " ".join(str(question.get(key) or "") for key in ("question", "why"))
    return sorted(
        ident
        for ident in plan_ids(body)
        if re.search(r"(?<![A-Za-z0-9_.-])" + re.escape(ident) + r"(?![A-Za-z0-9_])", text)
    )


def settled_this_round(state: RunState) -> bool:
    """Whether AutoCode has settled a plan defect since the person's last answer or feedback."""
    for event in reversed(state.get("user_events") or []):
        if event.get("actor") == "user_cli" and event.get("kind") in USER_INPUT:
            return False
        if event.get("kind") == "technical_decision" and event.get("settled_as") == KIND:
            return True
    return False


def correction(question, parts) -> str:
    return (
        "Correct the plan instead of asking the person: "
        + str(question.get("question", "")).strip()
        + " Revise "
        + ", ".join(parts)
        + " so the plan's parts agree with each other and every acceptance criterion can be "
        "carried out as written, keeping every outcome the person asked for. If the correction needs a choice "
        "only the person can make, ask that choice as a decision question."
    )


def _asked(question, reason):
    """A plan defect AutoCode did not settle, asked as the ordinary decision it now is."""
    return {**question, "kind": "decision", "why": reason + " " + str(question.get("why", ""))}


def settle(state: RunState, questions, body, contract_token):
    """Record AutoCode's correction for each plan-defect question and return the questions the person answers."""
    obligations = {row["id"] for row in state.get("deferred_obligations", []) if row.get("status") != "resolved"}
    spent = settled_this_round(state)
    human = []
    for question in questions:
        if question.get("kind") != KIND:
            human.append(question)
            continue
        parts = named_parts(question, body)
        if not parts:
            human.append(_asked(question, "It names no acceptance criterion or milestone of the plan."))
        elif question.get("id") in obligations:
            human.append(_asked(question, "It concerns an assumption the person rejected."))
        elif spent:
            human.append(_asked(question, "AutoCode already asked the Planner to correct the plan this round."))
        else:
            state.setdefault("user_events", []).append(
                {
                    "kind": "technical_decision",
                    "actor": "autocode",
                    "settled_as": KIND,
                    "at": util.now(),
                    "question_id": question["id"],
                    "question": copy.deepcopy(question),
                    "plan_parts": parts,
                    "text": correction(question, parts),
                    "reason": "The question asks whether the plan will be corrected, and the parts it names are in "
                    "the plan, so the Planner corrects them and the person approves the corrected plan.",
                    "contract_token": contract_token,
                }
            )
    return human

"""New-run review routing, disclosed in and bound to the exact approved contract.

Pure policy helpers shared by configuration, the goal lifecycle and workflow guards.
They do not launch a stage, select a model or approve a plan.
"""

from __future__ import annotations

import copy

FLAG = "--review-routing"
SETTING = "review_routing"
MODE = "glm_first_v1"
POLICY = (
    "Combined review: the configured Completion Reviewer independently tests the Builder's work and "
    "judges completion in one call. The separate Tester runs only for a concrete escalation. "
    "This replaces separate enforced milestone-checkpoint routing. "
    "Regression proof, changed-code evidence, model independence, required human reviews and limits still apply."
)


def add_argument(parser) -> None:
    parser.add_argument(
        FLAG,
        choices=("separate", "combined"),
        default=None,
        help="New runs: separate Tester and Completion Reviewer calls (default), or combined independent "
        "testing/completion on the configured Completion Reviewer route; disclosed in the plan you approve",
    )


def configured(settings: dict, requested, *, started: bool) -> str:
    saved = settings.get(SETTING, "separate")
    if started and requested is not None and requested != saved:
        raise ValueError(f"{FLAG} is a new-run policy; a saved run keeps its review routing")
    selected = saved if requested is None else requested
    if selected == "combined" and (settings.get("milestone_checkpoints") or {}).get("enabled"):
        raise ValueError("Combined review cannot use enforced milestone checkpoints")
    return selected


def contract_body(state: dict, body: dict) -> dict:
    if (state.get("settings") or {}).get(SETTING) != "combined":
        return body
    result = copy.deepcopy(body)
    constraints = result.setdefault("constraints", [])
    if POLICY not in constraints:
        constraints.append(POLICY)
    return result


def bind_approval(state: dict) -> None:
    if (state.get("settings") or {}).get(SETTING) != "combined":
        return
    contract = state["goal_contract"]
    if POLICY not in contract["body"].get("constraints", []):
        raise ValueError("Combined review must be included in the displayed plan before approval")
    state["settings"]["workflow"] = {
        "mode": MODE,
        "approval_basis": "goal",
        "approval_event": contract["approval_event"],
    }

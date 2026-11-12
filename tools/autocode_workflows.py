"""The kind of job a run is, and where it starts (owner decision 2026-11-10: one path for every task).

Every task takes the same path: Requirements, Planner, Plan Reviewer, Builder, Tester and
Completion Reviewer (#3010). There is no job recognizer and no job with a first stage of its own
any more; the Code Reviewer, the bug Investigator, the Designer and the Analyst were removed. The
kind (``build``, ``bugfix``, ``review``, ``design`` or ``discuss``) labels the run and its report.

Requirements is the front door of every run and follow-up turn whose saved flow has a
Requirements role (``requirements_first``, owner decision 2026-10-22): the run begins there
(``begin_requirements``) and its report names the job (``job``, #1846, #1860).
``from_requirements`` then starts the Planner: at once on a plain CLI run or a follow-up turn,
once the person approves what Requirements understood in a chat run
(autocode_requirements_chat). A flow without Requirements (the v2 planning flow, a run without
a Requirements role) starts as a build at its first planning stage (``begin``). ``--workflow
KIND`` names the kind instead (``pin``), before the first stage runs. A build that names an
existing, approved design document still starts at the Design Reviewer (``check_design``). The
answer is saved under ``state["workflow"]``; the status view reports it as ``workflow``
(docs/task-run.md). This module is pure: it imports nothing from the runner.

State key written here (and read by autocode_run_view, autopilot):
    workflow: {"kind": one of WORKFLOWS or None, "reason": str, "signals": [str],
               "source": "model" | "user" | "default", "then": the build pipeline's entry, the
               stage a build goes to once its kind is known (the Planner when Requirements
               already ran),
               "clarity": "clear" | "vague", only in adaptive-planning runs,
               "brief_size": "small" | "medium", a brief-only size signal (#1564) read by
               autocode_review_effort.small_clear before a plan is sized,
               "requirements_skipped": bool, saved only by runs from before 2026-10-22; read by
               autocode_adaptive_planning.view for the status view,
               "requirements_approved": True, once a chat run's person approved what
               Requirements understood (autocode_requirements_chat); _start then plans a build
               instead of gathering it again,
               "named_by": "requirements_gather", when the Requirements report named the job
               (from_requirements, #1846)}
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import re
from pathlib import Path

try:
    from . import autocode_adaptive_planning as adaptive
    from . import autocode_names as names
    from . import autocode_roles as roles
except ImportError:
    import autocode_adaptive_planning as adaptive
    import autocode_names as names
    import autocode_roles as roles

WORKFLOWS = ("build", "bugfix", "review", "design", "discuss")
# The stages earlier runtimes ran and this one does not: the job recognizer and the four jobs that
# had a first stage of their own (owner decision 2026-11-10). A run saved at one of them cannot be
# resumed (retired_stage); its status and report stay readable. Their screen names stay in
# autocode_roles, for that saved history.
RETIRED_STAGES = ("recognize_workflow", "review_change", "investigate_bug", "review_design", "answer_question")
# A build that implements an existing, approved design document starts by checking
# the design against the repository (autocode_design_check_job).
DESIGN_CHECK_STAGE = "check_design"
# New runs record that they use the standard implementation sequence (autocode_configure). Runs from
# before it may lack the marker; autocode_compact_planning reads it for those.
IMPLEMENTATION_FLOW = "implementation_flow"
STANDARD_IMPLEMENTATION = "standard"


def retired_stage(state: RunState) -> str | None:
    """Why a saved run cannot continue, when it is waiting at a stage this runtime no longer runs or was
    saved under the old route names (autocode_names), else None."""
    saved = ((state.get("settings") or {}).get("roles")) or {}
    if old := sorted(role for role in saved if role in names.RETIRED_ROUTES):
        renamed = ", ".join(f"{role} (now {names.route(role)})" for role in old)
        return (
            f"This run was saved before AutoCode named its stages after their jobs: its routes {renamed} "
            "use the old names, which this runtime no longer runs. Its status and saved reports stay "
            "readable; start a new task to continue the work."
        )
    # The last renaming step renamed the planning record's review counter; a run saved between the
    # steps has job-named routes and the old counter, which this runtime no longer keeps.
    if names.OLD_PLANNING_COUNTER in (state.get("planning") or {}):
        return (
            "This run was saved before AutoCode finished naming its records after their jobs: its plan "
            f"review count is kept as {names.OLD_PLANNING_COUNTER}, which this runtime no longer reads. Its "
            "status and saved reports stay readable; start a new task to continue the work."
        )
    stage = str(state.get("next_stage") or "").removesuffix("_report_repair")
    if stage not in RETIRED_STAGES:
        return None
    job = roles.screen_name(stage)
    return (
        f"This run was saved at the {job} stage ({stage}), which AutoCode no longer runs: every task now "
        "takes the same path (Requirements, Planner, Plan Reviewer, Builder, Tester, Completion Reviewer). "
        "Its status and saved reports stay readable; start a new task to continue the work."
    )


# Who may approve a goal contract. Normally only the user (actor "user_cli"). A run an earlier
# runtime saved may hold a contract its bug-fix Investigator built under a policy the user agreed
# to: it carries one of these origins and was approved by actor "workflow_policy", recorded with the
# policy text. No new run creates one. goals.approved and the resolver both ask here.
POLICY_ORIGINS = ("bugfix_small_correction",)


def approval_actor_ok(origin, approval) -> bool:
    actor = (approval or {}).get("actor")
    return actor == "user_cli" or (actor == "workflow_policy" and origin in POLICY_ORIGINS)


def follow_up(state: RunState) -> dict | None:
    """The earlier turn, for Requirements to name a follow-up's job while it is not yet known (autocode_follow_up), else None."""
    turns = state.get("turns") or []
    if not turns or kind(state):
        return None
    previous = turns[-1]["previous"]
    review = previous.get("review") or {}
    return {
        "message": turns[-1]["say"],
        "previous_workflow": previous.get("workflow"),
        "previous_request": previous.get("task", ""),
        **(
            {
                "previous_review": {
                    "verdict": review.get("verdict"),
                    "blocking": len(review.get("blocking") or []),
                    "advisory": len(review.get("advisory") or []),
                }
            }
            if review
            else {}
        ),
        # What a design turn produced: a build of it names the document (approved_design checks it).
        **(
            {"previous_design": design_context(previous["design"], state.get("workspace"))}
            if previous.get("design")
            else {}
        ),
    }


def design_context(design: dict, workspace) -> dict:
    """What Requirements is told about an earlier design turn: paths, a verdict and counts. The report's
    own text (summaries, questions) stays out of its prompt, as previous_review's does."""
    if design.get("mode") == "propose":
        return {
            "mode": "propose",
            "documents": [path for path in design.get("documents") or [] if workspace_file(workspace, path)],
        }
    reviewed = str(design.get("design_under_review") or "")
    return {
        "mode": "review",
        "design_under_review": reviewed if workspace_file(workspace, reviewed) else "",
        "verdict": design.get("verdict") if design.get("verdict") in ("approve", "request_changes") else None,
        **{key: len(design.get(key) or []) for key in ("blocking", "advisory", "questions")},
    }


REQUIREMENTS_STAGE = "requirements_gather"


def requirements_first(state: RunState) -> bool:
    """Whether this run's saved flow starts every turn at Requirements, which names the job (#1860).

    The v2 planning flow and a run without a Requirements role start as a build instead (begin)."""
    roles_saved = (state.get("settings") or {}).get("roles") or {}
    return roles.planning_flow(state) != "v2" and "requirements" in roles_saved


def begin_requirements(state: RunState) -> None:
    """Make Requirements the first stage of a new run or follow-up turn (owner decision 2026-10-22).

    Its report names the job (``from_requirements``); a build then goes on to the Planner, the
    build pipeline's entry once requirements are gathered."""
    state["workflow"] = {"kind": None, "reason": "", "signals": [], "source": None, "then": planner_stage(state)}
    state["next_stage"] = REQUIREMENTS_STAGE


def begin(state: RunState, then: str) -> None:
    """Start a run or turn without Requirements at ``then``, its first planning stage, as a build.

    No stage names the job on such a flow, and every task takes the same path; ``--workflow`` may
    still name another kind before ``then`` runs (pin)."""
    state["workflow"] = {
        "kind": "build",
        "reason": "No Requirements stage names the job on this flow; planned as a build",
        "signals": [],
        "source": "default",
        "output": None,
        "then": then,
        "brief_size": brief_size(state.get("task") or ""),
    }
    state["next_stage"] = then


def brief_size(task: str) -> str:
    """Size a brief from what exists before planning: its words and numbered asks (#1564).

    Deliberately coarse: it only needs to identify the bug-fix-shaped end of the
    scale, where review depth should shrink. plan_size finalizes the call once a
    draft exists; anything not clearly small here keeps full-depth review.
    """
    words = len(task.split())
    numbered = len(re.findall(r"(?m)^\s*(?:\d+[.)])\s+\S", task))
    return "small" if words <= 120 and numbered <= 8 else "medium"


def _start(state: RunState, value: dict, record: dict) -> None:
    """Save the job's kind and start the build pipeline's entry (``then``), or the Design Reviewer
    for a build of an approved design document."""
    if value.get("workflow") not in WORKFLOWS:
        raise ValueError(f"Unknown workflow {value.get('workflow')!r}; expected one of {WORKFLOWS}")
    previous = state.get("workflow") or {}
    then = previous.get("then") or REQUIREMENTS_STAGE
    state["workflow"] = {
        "kind": value["workflow"],
        "reason": value.get("reason", ""),
        "signals": list(value.get("signals") or []),
        "source": "model",
        "output": record.get("output"),
        "then": then,
    }
    if adaptive.enabled(state) and value.get("clarity"):
        state["workflow"]["clarity"] = value["clarity"]
    # A brief-only size signal available before planning (#1564): the plan does not exist yet, so
    # the durable plan_size finalization happens at first review admission. Reuses adaptive's
    # small/medium/large vocabulary, not a second one.
    state["workflow"]["brief_size"] = brief_size(state.get("task") or "")
    design = approved_design(state, value)
    if design:
        state["workflow"]["design_document"] = design
    first = then
    if previous.get("requirements_approved"):
        state["workflow"]["requirements_approved"] = True
        if first == REQUIREMENTS_STAGE:
            # A chat run's Requirements spoke and was approved (autocode_requirements_chat).
            first = planner_stage(state)
    state.update({"status": "RUNNING", "next_stage": DESIGN_CHECK_STAGE if design else first})


def named_by_requirements(report: dict) -> str | None:
    """The job a Requirements report names (#1846), or None when it names none yet.

    Requirements reads the request before the job is known, so its report says which of the five
    jobs it understood; that replaces a second model call to recognize it."""
    named = (report or {}).get("job")
    return named if named in WORKFLOWS else None


def from_requirements(state: RunState, report: dict, record: dict | None = None) -> None:
    """Start the job Requirements named: the build pipeline's entry, every kind alike (#3010).

    The record keeps ``source: "model"``: a model decided the kind, here the Requirements stage. A
    report that names no job is planned as a build (#1860). A build's design document counts only
    when it exists in the workspace (approved_design). In an adaptive run, Requirements judges the
    request's clarity (adaptive.requirements_judge_clarity), which a small task's Tester and
    Completion Reviewer levels read (autocode_review_effort.small_clear)."""
    named = named_by_requirements(report)
    outcome = str(report.get("intended_outcome") or "").strip().rstrip(".")
    value = {
        "workflow": named or "build",
        "reason": "Requirements understood: " + (outcome or "no outcome stated")
        if named
        else "Requirements named no job; planned as a build",
        "signals": [],
        "design_document": str(report.get("design_document") or ""),
        **({"clarity": report["clarity"]} if report.get("clarity") in adaptive.CLARITY else {}),
    }
    _start(state, value, record or {})
    state["workflow"]["named_by"] = REQUIREMENTS_STAGE


def pin(state: RunState, kind: str) -> None:
    """The user named the kind of job (``--workflow``) before the run's first stage ran.

    The kind only labels the run: it takes the same path as every other (#3010). On a run that
    begins at Requirements (begin_requirements), the run still gathers its requirements first."""
    if kind not in WORKFLOWS:
        raise ValueError(f"Unknown workflow {kind!r}; expected one of {WORKFLOWS}")
    current = state.get("workflow") or {}
    unnamed = not current.get("kind") or current.get("source") == "default"
    if "then" not in current or not unnamed or _turn_stages(state):
        why = f"This run already runs as {current['kind']!r}" if current.get("kind") else "This run predates job naming"
        raise ValueError(
            f"{why}; --workflow applies before the job is named. Start a new run with --workflow {kind} instead"
        )
    state["workflow"] = {
        "kind": kind,
        "reason": "Named by the user with --workflow",
        "signals": [],
        "source": "user",
        "output": None,
        "then": current["then"],
        **({"brief_size": current["brief_size"]} if "brief_size" in current else {}),
    }
    state["status"] = "RUNNING"


def _turn_stages(state: RunState) -> list:
    """The stage records of the current turn: since the newest follow-up was said, else the whole run."""
    turns = state.get("turns") or []
    start = turns[-1].get("stage_index") if turns else 0
    return list((state.get("stages") or [])[start if isinstance(start, int) else 0 :])


def ran_this_turn(state: RunState, stage: str) -> bool:
    """Whether ``stage`` already ran in the current turn (an earlier turn's run belongs to its own job)."""
    return any(isinstance(row, dict) and row.get("stage") == stage for row in _turn_stages(state))


def approval_note(state: RunState) -> str:
    """The job kind for the plan the user is asked to approve: what it is, why, and how to correct it ('' before it is named).

    The kind is stated where the user approves, with the way out (a run's kind is fixed once it is
    named, so a wrong one is corrected by a new run)."""
    found = state.get("workflow") or {}
    if not found.get("kind"):
        return ""
    reason = (found.get("reason") or "").strip().rstrip(".") or "no reason given"
    if found.get("source") == "user":
        why = "named by you with --workflow"
    elif found.get("source") == "default":
        why = "every task without a Requirements stage is planned as a build"
    elif found.get("named_by") == "requirements_gather":
        why = reason  # already "Requirements understood: …" (#1846)
    else:
        why = "recognized: " + reason  # a run an earlier runtime's job recognizer named
    return (
        f"Job kind: {found['kind']} ({why}). Not what you meant? Do not approve; start a new run with "
        f"--workflow {'|'.join(WORKFLOWS)}."
    )


def approved_design(state: RunState, value: dict) -> str:
    """The approved design a build asks to implement, if Requirements named one that exists.

    In a follow-up, only a design the previous turn produced, or one its design review approved
    (``buildable``), counts: anything else Requirements names is dropped, and the build gathers
    requirements as usual."""
    design = str(value.get("design_document") or "").strip()
    if value.get("workflow") != "build" or not design:
        return ""
    turns = state.get("turns") or []
    if turns and design not in buildable((turns[-1].get("previous") or {}).get("design")):
        return ""
    return design if workspace_file(state.get("workspace") or ".", design) else ""


def buildable(design: dict | None) -> list[str]:
    """The documents a follow-up may build as approved: the ones a design turn produced, or the
    design a design review approved (a review that requested changes approved nothing)."""
    design = design or {}
    if design.get("mode") == "propose":
        return [str(path) for path in design.get("documents") or []]
    if design.get("mode") == "review" and design.get("verdict") == "approve" and design.get("design_under_review"):
        return [str(design["design_under_review"])]
    return []


def workspace_file(workspace, path) -> bool:
    """A regular file at the relative ``path`` inside ``workspace``. A symbolic link is refused even
    when it resolves inside: its target can change, and one outside would be read as the design."""
    relative = Path(str(path or ""))
    if not str(path or "").strip() or relative.is_absolute() or ".." in relative.parts:
        return False
    root, target = Path(workspace or "."), Path(workspace or ".") / relative
    try:
        return target.is_file() and not target.is_symlink() and target.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def planner_stage(state: RunState) -> str:
    """Where a workflow hands over to planning: the Planner's first stage in the saved flow."""
    return "plan" if roles.planning_flow(state) == "v2" else "draft_plan"


def kind(state: RunState) -> str | None:
    """The run's kind of job, or None before it is named (and for runs that predate naming it)."""
    return (state.get("workflow") or {}).get("kind")

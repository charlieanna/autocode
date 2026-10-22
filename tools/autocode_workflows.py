"""Workflow recognition: what kind of engineering job a request is.

AutoPilot runs ``STAGE`` first on every new run, before any requirements or
planning stage. A chat run (spec-first) talks with Requirements first, and its
Requirements report names the job (``job``, #1846): ``from_requirements`` starts
that job without the recognizer. Only a chat whose Requirements named no job runs
the recognizer once the person approves what Requirements understood
(autocode_requirements_chat), and the recognizer is then given that understanding.
The answer is saved under ``state["workflow"]``; the status
view reports it as ``workflow`` (docs/task-run.md). There are five kinds,
described in scenarios/README.md ("Workflows"). Normally the request itself is
read; ``--workflow KIND`` names the kind instead (``pin``), before the recognizer
runs. A recognized run keeps its kind: the stages already run belong to it, so a
wrong recognition is corrected by a new run with ``--workflow``. The console line
after recognition (``describe``) says which kind was chosen, why, and how to
override it, so a misroute is visible before more is spent. This module is pure:
it builds the prompt and schema and interprets the report. It imports nothing
from the runner.

State key written here (and read by autocode_run_view, autopilot):
    workflow: {"kind": one of WORKFLOWS or None, "reason": str, "signals": [str],
               "source": "model" | "user", "then": the stage to run after recognition,
               "clarity": "clear" | "vague", only in adaptive-planning runs,
               "brief_size": "small" | "medium", a brief-only size signal (#1564) read by
               units.autoplanner.review_depth_reduced to keep small clear briefs on one
               total review allowance across planning cycles,
               "requirements_skipped": bool, only where clarity decides whether Requirements
               runs (an adaptive build recognized with Requirements next and no approved
               design); read by autocode_adaptive_planning.view for the status view,
               "requirements_approved": True, only between a chat run's approval and its
               recognition (autocode_requirements_chat); apply then plans a build instead of
               gathering it again,
               "named_by": "requirements_gather", only when a chat run's Requirements report named
               the job (from_requirements, #1846) and the recognizer did not run}
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_prompts as prompts
    from . import autocode_roles as roles
except ImportError:
    import autocode_prompts as prompts
    import autocode_roles as roles


import json
import re
from pathlib import Path, PurePosixPath

try:
    from . import autocode_adaptive_planning as adaptive
except ImportError:
    import autocode_adaptive_planning as adaptive

STAGE = "recognize_workflow"
WORKFLOWS = ("build", "bugfix", "review", "design", "discuss")
# Workflows with their own first stage. The others continue into the build
# pipeline (the stage saved as ``then`` when recognition began) for now.
REVIEW_STAGE = "review_change"
INVESTIGATE_STAGE = "investigate_bug"
DESIGN_STAGE = "review_design"
DISCUSS_STAGE = "answer_question"
# A build that implements an existing, approved design document starts by checking
# the design against the repository (autocode_design_check_job).
DESIGN_CHECK_STAGE = "check_design"
FIRST_STAGE = {"review": REVIEW_STAGE, "bugfix": INVESTIGATE_STAGE, "design": DESIGN_STAGE, "discuss": DISCUSS_STAGE}

# Who may approve a goal contract. Normally only the user (actor "user_cli"). A
# contract a workflow built under a policy the user agreed to carries one of these
# origins and is approved by actor "workflow_policy", recorded with the policy text
# (autocode_bug_job.SMALL_FIX_POLICY). goals.approved and the resolver both ask here.
POLICY_ORIGINS = ("bugfix_small_correction",)


def approval_actor_ok(origin, approval) -> bool:
    actor = (approval or {}).get("actor")
    return actor == "user_cli" or (actor == "workflow_policy" and origin in POLICY_ORIGINS)


DESCRIPTIONS = {
    "build": prompts.get("fragments/workflows/descriptions.md"),
    "bugfix": prompts.get("fragments/workflows/descriptions-02.md"),
    "review": prompts.get("fragments/workflows/descriptions-03.md"),
    "design": prompts.get("fragments/workflows/descriptions-04.md"),
    "discuss": prompts.get("fragments/workflows/descriptions-05.md"),
}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["workflow", "reason", "signals"],
    "properties": {
        "workflow": {"type": "string", "enum": list(WORKFLOWS)},
        "reason": {"type": "string"},
        "signals": {"type": "array", "items": {"type": "string"}},
        # Optional so older reports stay valid; generation schemas require every field.
        "design_document": {"type": "string"},
    },
}

PROMPT = (
    prompts.get("recognize-workflow-02.md")
    + "\n".join(f"- {name}: {text}" for name, text in DESCRIPTIONS.items())
    + prompts.get("recognize-workflow.md")
)


def packet(state: RunState, inventory: dict | None = None, engine: str | None = None) -> dict:
    # goal_contract, current_task and saved_answers are empty by definition here (nothing has
    # been planned yet); they and execution_engine are present because every provider reads
    # them from the packet.
    context = follow_up(state)
    # A follow-up is recognized from the new message; the earlier turn is context, not the request.
    return {
        "stage": STAGE,
        "task": context["message"] if context else state["task"],
        **({"follow_up": context} if context else {}),
        "workspace": state.get("workspace"),
        "execution_engine": engine,
        "workspace_inventory": inventory or {},
        "goal_contract": None,
        "current_task": None,
        "saved_answers": {},
        # A request with a Figma design is still recognized by what the user wants back.
        **({"figma_file": state["settings"]["figma_file"]} if (state.get("settings") or {}).get("figma_file") else {}),
        **({"confirmed_understanding": understanding} if (understanding := confirmed_understanding(state)) else {}),
    }


def confirmed_understanding(state: RunState) -> str:
    """In a chat run, the outcome the person approved with Requirements before the job is recognized."""
    if not (state.get("settings") or {}).get("spec_first") or state.get("turns"):
        return ""
    report = (state.get("requirements_handoff") or {}).get("report") or {}
    return str(report.get("intended_outcome") or "").strip()


def follow_up(state: RunState) -> dict | None:
    """The earlier turn, for recognizing a follow-up whose kind is not yet known (autocode_follow_up), else None."""
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
    """What the recognizer is told about a design turn: paths, a verdict and counts. The report's
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


def prompt(
    state: RunState, inventory: dict | None = None, soft_budget_tokens: int = 10000, engine: str | None = None
) -> tuple[str, dict]:
    text = (
        PROMPT
        + adaptive.recognizer_rule(state)
        + (
            "\nCONFIRMED UNDERSTANDING: The user has already talked with Requirements and approved "
            "confirmed_understanding as what they want. Recognize the job from it, with task as the "
            "user's own words; do not ask again what they want.\n"
            if confirmed_understanding(state)
            else ""
        )
        + "\nCURRENT HANDOFF DATA\n"
        + json.dumps(packet(state, inventory, engine), indent=2)
    )
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def begin(state: RunState, then: str) -> None:
    """Make recognition the first stage of a new run; ``then`` is the stage that follows it."""
    state["workflow"] = {"kind": None, "reason": "", "signals": [], "source": None, "then": then}
    state["next_stage"] = STAGE


def brief_size(task: str) -> str:
    """Size a brief from what exists at recognition: its words and numbered asks (#1564).

    Deliberately coarse: it only needs to identify the bug-fix-shaped end of the
    scale, where review depth should shrink. plan_size finalizes the call once a
    draft exists; anything not clearly small here keeps full-depth review.
    """
    words = len(task.split())
    numbered = len(re.findall(r"(?m)^\s*(?:\d+[.)])\s+\S", task))
    return "small" if words <= 120 and numbered <= 8 else "medium"


def apply(state: RunState, value: dict, record: dict) -> None:
    """Save the recognized kind and hand over to the stage recognition deferred."""
    if value.get("workflow") not in WORKFLOWS:
        raise ValueError(f"Unknown workflow {value.get('workflow')!r}; expected one of {WORKFLOWS}")
    # ``then`` stays the build pipeline's entry stage: a workflow with its own first
    # stage (FIRST_STAGE) may still hand over to the build pipeline afterwards.
    previous = state.get("workflow") or {}
    then = previous.get("then") or "requirements_gather"
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
    # A brief-only size signal available at recognition (#1564): the plan does not
    # exist yet, so the durable plan_size finalization happens at first review
    # admission. Reuses adaptive's small/medium/large vocabulary, not a second one.
    state["workflow"]["brief_size"] = brief_size(state.get("task") or "")
    design = approved_design(state, value)
    if design:
        state["workflow"]["design_document"] = design
    first = adaptive.entry_stage(state, value, then, planner_stage(state))
    if adaptive.enabled(state) and value["workflow"] == "build" and not design and then == "requirements_gather":
        # Saved where clarity decides it; the status view shows it as planning_depth.requirements_skipped (#697).
        state["workflow"]["requirements_skipped"] = first != then
    if previous.get("requirements_approved") and first == then:
        # A chat run's Requirements spoke and was approved before recognition (autocode_requirements_chat).
        first = planner_stage(state)
    state.update(
        {
            "status": "RUNNING",
            "next_stage": DESIGN_CHECK_STAGE if design else FIRST_STAGE.get(value["workflow"]) or first,
        }
    )


def named_by_requirements(report: dict) -> str | None:
    """The job a chat run's Requirements report names (#1846), or None when it names none yet.

    In a spec-first chat Requirements talks with the person before the job is known, so its report
    says which of the five jobs it understood; that replaces a second model call to recognize it."""
    named = (report or {}).get("job")
    return named if named in WORKFLOWS else None


def from_requirements(state: RunState, report: dict, record: dict | None = None) -> None:
    """Start the job a chat run's Requirements named, as recognition would (apply), without the recognizer.

    The record keeps ``source: "model"``: a model decided the kind, here the Requirements stage the person
    talked with. A build's design document counts only as apply counts the recognizer's (approved_design)."""
    named = named_by_requirements(report)
    if named is None:
        raise ValueError("Requirements named no job to start")
    outcome = str(report.get("intended_outcome") or "").strip().rstrip(".")
    value = {
        "workflow": named,
        "reason": "Requirements understood: " + (outcome or "no outcome stated"),
        "signals": [],
        "design_document": str(report.get("design_document") or ""),
        # An adaptive chat's Requirements judges clarity as the recognizer did; apply keeps it only there.
        **({"clarity": report["clarity"]} if report.get("clarity") in adaptive.CLARITY else {}),
    }
    apply(state, value, record or {})
    state["workflow"]["named_by"] = "requirements_gather"


def pin(state: RunState, kind: str) -> None:
    """The user named the kind of job (``--workflow``): skip recognition and start it."""
    if kind not in WORKFLOWS:
        raise ValueError(f"Unknown workflow {kind!r}; expected one of {WORKFLOWS}")
    current = state.get("workflow") or {}
    if state.get("next_stage") != STAGE or current.get("kind"):
        why = (
            f"This run already runs as {current['kind']!r}"
            if current.get("kind")
            else "This run predates workflow recognition"
        )
        raise ValueError(
            f"{why}; --workflow applies before the recognizer runs. Start a new run with --workflow {kind} instead"
        )
    then = current.get("then") or "requirements_gather"
    state["workflow"] = {
        "kind": kind,
        "reason": "Named by the user with --workflow",
        "signals": [],
        "source": "user",
        "output": None,
        "then": then,
    }
    state.update({"status": "RUNNING", "next_stage": FIRST_STAGE.get(kind) or then})


def describe(state: RunState, stage: str) -> str:
    """The console line after recognition: the kind, why, and how to override it ('' for other stages)."""
    if stage != STAGE:
        return ""
    found = state.get("workflow") or {}
    signals = ", ".join(found.get("signals") or [])
    return (
        f"\nWorkflow: {found.get('kind')}. {(found.get('reason') or '').rstrip('.')}."
        + (f" Signals: {signals}." if signals else "")
        + f"\nNot what you meant? Start again with --workflow {'|'.join(WORKFLOWS)}."
    )


def approval_note(state: RunState) -> str:
    """The job kind for the plan the user is asked to approve: what it is, why, and how to correct it ('' before recognition).

    Read as "build" when it was really a question costs a little time; read as a question when it was really "build"
    writes code nobody asked for, so the kind is stated where the user approves, with the way out (a run's kind is
    fixed once recognition has run, so a wrong one is corrected by a new run)."""
    found = state.get("workflow") or {}
    if not found.get("kind"):
        return ""
    reason = (found.get("reason") or "").strip().rstrip(".") or "no reason given"
    if found.get("source") == "user":
        why = "named by you with --workflow"
    elif found.get("named_by") == "requirements_gather":
        why = reason  # already "Requirements understood: …" (#1846)
    else:
        why = "recognized: " + reason
    return (
        f"Job kind: {found['kind']} ({why}). Not what you meant? Do not approve; start a new run with "
        f"--workflow {'|'.join(WORKFLOWS)}."
    )


def approved_design(state: RunState, value: dict) -> str:
    """The approved design a build asks to implement, if the recognizer named one that exists.

    In a follow-up, only a design the previous turn produced, or one its design review approved
    (``buildable``), counts: anything else the recognizer names is dropped, and the build gathers
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
    return "plan" if roles.planning_flow(state) == "v2" else "astra_discovery"


def kind(state: RunState) -> str | None:
    """The recognized workflow, or None before recognition (and for runs that predate it)."""
    return (state.get("workflow") or {}).get("kind")


def design_rewrites(state: RunState, body: dict) -> list[tuple[str, str]]:
    """(planned path, earlier file) for each file an earlier turn of this conversation wrote that a new
    design's plan would let its Builder change, unless the newest message names that file.

    A design job proposing a design writes that design. A live design turn's plan also listed the
    decision record the discuss turn had written (docs/decisions/metadata-cache.json) in its
    affected_paths, and its Builder rewrote it (#664). A planned directory that holds such a file
    counts too: the Builder may change anything under it. Other jobs, and a design review, are
    not checked here."""
    if kind(state) != "design" or (state.get("design_review") or {}).get("mode") != "propose":
        return []
    turns = state.get("turns") or []
    earlier = list(
        dict.fromkeys(
            str(path).strip().removeprefix("./")
            for turn in turns
            for path in (turn.get("previous") or {}).get("wrote") or []
            if str(path).strip()
        )
    )
    said = str((turns[-1] if turns else {}).get("say") or "")
    asked = {path for path in earlier if path in said or PurePosixPath(path).name in said}
    tasks = [body.get("initial_task") or {}, *(body.get("milestones") or [])]
    planned = dict.fromkeys(
        str(path).strip().removeprefix("./")
        for task in tasks
        for path in task.get("affected_paths") or []
        if str(path).strip()
    )
    found = []
    for path in planned:
        folder = path.rstrip("/") + "/"
        for done in earlier:
            if done not in asked and (done == path.rstrip("/") or done.startswith(folder)):
                found.append((path, done))
    return found

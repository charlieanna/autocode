"""Workflow recognition: what kind of engineering job a request is.

AutoPilot runs ``STAGE`` first on every new run, before any requirements or
planning stage, and saves the answer under ``state["workflow"]``; the status
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
               "clarity": "clear" | "vague", only in adaptive-planning runs}
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import json
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
    "build": "Make or change something: a feature, a new tool, a behavior change. The user wants working code "
    "at the end. Steps: understand the requirements, plan, review the plan, build, test, review.",
    "bugfix": "Something misbehaves and the user reports it (an error, a wrong result, 'why does X happen'). "
    "The user wants the cause found and fixed. Steps: investigate and reproduce, diagnose, fix, test, review.",
    "review": "Judge an existing change: a patch, a pull request, a diff, a branch. The user wants findings, not "
    "edits. Steps: read the change and its context, test where useful, report findings.",
    "design": "Judge or produce an architecture or design: review a design document, propose how something should "
    "be structured, 'design this but do not implement it'. Nothing is built. Steps: understand, challenge, design.",
    "discuss": "A question, a tradeoff or an investigation: 'should we use X or Y', 'why does the code do this', "
    "'what would break if'. The user wants an answer with evidence, not code. Steps: investigate, answer.",
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
    """You are the job recognizer for an AI engineering team. You read one request from a user and decide
which ONE kind of job it is, so the right specialists are assigned. You do not do the job.

The five kinds, with what the user wants at the end of each:
"""
    + "\n".join(f"- {name}: {text}" for name, text in DESCRIPTIONS.items())
    + """

How to decide:
- Go by what the user wants to receive: code (build), a fix plus its cause (bugfix), findings about an
  existing change (review), a design or a design review with nothing built (design), or an answer (discuss).
- "Fix", "figure out why", "stopped working", "creates duplicates", a described misbehavior: bugfix, even
  when the user also names a suspected cause.
- "Review", "look over", "is it safe to merge", a named patch, PR or diff: review. Reviewing a design
  document is design, not review.
- design means the user wants a DESIGN back: a new design produced ("design how X should work",
  "how should we structure", "sketch the architecture, don't implement") or an existing design
  document reviewed.
- discuss means the user wants an ANSWER back: a choice between named options ("should we use A or B",
  "stay X or move to Y"), a reason ("why does the code do X") or a consequence ("what would break if").
  This holds for architecture questions too, and when the user asks for the analysis or recommendation
  to be written down (a decision record or note), as long as nothing is to be built or fixed and no
  design document is to be produced or reviewed. "I want the analysis, not code" is discuss.
- When a request asks for several things, choose the kind of the FIRST thing that must happen. "Review
  this and fix what you find" starts as review; "why does this fail, then fix it" starts as bugfix.
- A follow-up (follow_up in the handoff data) continues a finished job in the same conversation: task is
  the user's new message and follow_up says what came before. Judge the new message in that context.
  Asking to act on a review's findings ("fix them", "land it with those fixed", "apply the fixes") is
  build: the review already found and located the problems, and they are the task list.
  Asking to build or implement what a design turn produced ("build it", "implement the design") is
  build, with design_document set to the one document in follow_up.previous_design.documents (after
  a design review, its design_under_review, and only when its verdict is approve).
  Answering or correcting a finished design review (follow_up.previous_design.mode is review: "ordering
  is per-domain", "that is fine", "you missed X") is design: the Architect revises that review.
- Do not guess build when unsure. Build is the most expensive path; the other kinds are cheaper and can
  lead to a build later in the same conversation.

Return JSON only: {"workflow": one of build|bugfix|review|design|discuss, "reason": one sentence,
"signals": the words or phrases in the request that decided it, "design_document": for a build that asks
to implement an EXISTING design document as written (approved, decided, "don't redesign it", or the
design a follow-up asks to build), that document's path in the repository; otherwise ""}. Read nothing
but the request and the file listing below; do not open files.
"""
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
    }


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
        + "\nCURRENT HANDOFF DATA\n"
        + json.dumps(packet(state, inventory, engine), indent=2)
    )
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def begin(state: RunState, then: str) -> None:
    """Make recognition the first stage of a new run; ``then`` is the stage that follows it."""
    state["workflow"] = {"kind": None, "reason": "", "signals": [], "source": None, "then": then}
    state["next_stage"] = STAGE


def apply(state: RunState, value: dict, record: dict) -> None:
    """Save the recognized kind and hand over to the stage recognition deferred."""
    if value.get("workflow") not in WORKFLOWS:
        raise ValueError(f"Unknown workflow {value.get('workflow')!r}; expected one of {WORKFLOWS}")
    # ``then`` stays the build pipeline's entry stage: a workflow with its own first
    # stage (FIRST_STAGE) may still hand over to the build pipeline afterwards.
    then = (state.get("workflow") or {}).get("then") or "requirements_gather"
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
    design = approved_design(state, value)
    if design:
        state["workflow"]["design_document"] = design
    first = adaptive.entry_stage(state, value, then, planner_stage(state))
    state.update(
        {
            "status": "RUNNING",
            "next_stage": DESIGN_CHECK_STAGE if design else FIRST_STAGE.get(value["workflow"]) or first,
        }
    )


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
    why = (
        "named by you with --workflow"
        if found.get("source") == "user"
        else "recognized: " + ((found.get("reason") or "").strip().rstrip(".") or "no reason given")
    )
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
    return "plan" if (state.get("settings") or {}).get("planning_flow") == "v2" else "astra_discovery"


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

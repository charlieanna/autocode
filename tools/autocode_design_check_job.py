"""Implementing an approved design: check it against the repository before any planning or building.

When a build request asks to implement an existing, approved design document as
written (autocode_workflows records its path as ``design_document``), the run starts
with ``STAGE``. The Architect reads the design and the code it changes and reports
either conflicts or the design's binding decisions. The runner then:

- rejects the report if the stage changed anything in the workspace,
- runs each conflict's ``probe`` when it has one: a command that exits 0 exactly
  when the repository holds the constraint the design breaks. A failing probe
  rejects the report (autocode_test_cases.run_probes),
- on conflicts: writes them to ``<design>.blockers.json`` beside the design and
  STOPS the run (``PAUSED_DESIGN_CONFLICT``). No Builder runs; the user decides,
- otherwise: saves the design's binding decisions as ``state["design_constraint"]``
  and continues straight to the Planner. Requirements gathering is skipped because
  the approved design already is the requirements, and the Planner is told the
  design is a constraint it may not redesign or ask about. In a follow-up that
  builds the design the previous turn proposed, that turn's contract,
  requirements handoff, planning record and task move to their histories first
  (autocode_follow_up.plan_afresh): the build gets a new contract, which the
  user approves, rather than revising the design job's.

Pure module: prompt, schema, transition; the unit passes in the function that
runs probes. Imports nothing from the runner. State keys written:
``design_check`` and ``design_constraint``.
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    from . import autocode_stage_access as stage_access, autocode_stray_writes as stray_writes
    from . import autocode_workflows as workflows, autocode_follow_up as follow_up
    from .autocode_test_cases import run_probes
except ImportError:
    import autocode_stage_access as stage_access
    import autocode_stray_writes as stray_writes
    import autocode_workflows as workflows
    import autocode_follow_up as follow_up
    from autocode_test_cases import run_probes

STAGE = workflows.DESIGN_CHECK_STAGE
STOP_STATUS = "PAUSED_DESIGN_CONFLICT"
TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
CONFLICT = {
    "type": "object", "additionalProperties": False,
    "required": ["design_says", "conflicts_with", "files", "options", "example", "probe"],
    # example: the conflict as one concrete case in plain English; probe: a shell command, run from the
    # repository root, that exits 0 exactly when the repository holds the constraint. Both "" when the
    # constraint lives in prose (a README rule) that no command can show.
    "properties": {"design_says": TEXT, "conflicts_with": TEXT, "files": TEXTS, "options": TEXTS,
                   "example": TEXT, "probe": TEXT},
}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["design_document", "summary", "constraints", "conflicts"],
    "properties": {
        "design_document": TEXT,
        "summary": TEXT,
        "constraints": TEXTS,
        "conflicts": {"type": "array", "items": CONFLICT},
    },
}

PROMPT = """You are the Architect. The user has an APPROVED design document and wants it implemented exactly as
written. Before anyone plans or builds, check whether it CAN be implemented as written in this repository.
You do not write code and you do not edit anything.

1. Read the design (design_document in the handoff) and the code, tests, README and docs it touches.
2. conflicts: every place where implementing the design as written would break something this repository
   states or relies on: a frozen or published API, a documented invariant, a caller that depends on
   today's behaviour, a version or compatibility rule. For each: design_says (quote or paraphrase the
   design), conflicts_with (the constraint and why they cannot both hold), files (repository files where
   the constraint lives), options (how the user could resolve it), example (the conflict as one concrete
   case in plain English: "Given <today's code>, when <what the design specifies happens>, then <what
   breaks>") and, when the constraint is something the code enforces today, probe: a shell command run from
   the repository root that exits 0 exactly when the repository holds that constraint (for example:
   python3 -c "from ratelimit.bucket import TokenBucket; assert TokenBucket().try_acquire('k') is True").
   The runner runs every probe in a scratch copy and rejects the report if one fails. A constraint that
   lives only in prose (a README rule) has probe "". Do not list style preferences, better
   alternatives you would have chosen, or gaps the implementation can fill without contradicting anything:
   the design is approved, and your job is not to redesign it.
3. constraints: the design's binding decisions the plan must follow exactly (module and file layout,
   names, signatures, rules such as "only the injected clock is a source of time", rejected alternatives).
4. summary: two sentences on what the design specifies.

""" + stage_access.scratch_rule(STAGE) + """ Otherwise never write into the workspace. The runner
writes any conflicts beside the design and stops the run so the user can decide; with no conflicts, planning
starts from your constraints. Return JSON only, matching the schema the runner gives you.
"""


def packet(state: dict, inventory: dict | None = None, engine: str | None = None) -> dict:
    return {"stage": STAGE, "task": state["task"], "workspace": state.get("workspace"),
            "design_document": (state.get("workflow") or {}).get("design_document", ""),
            "execution_engine": engine, "workspace_inventory": inventory or {},
            # Present because every provider reads them; nothing is planned yet.
            "goal_contract": None, "current_task": None, "saved_answers": {}}


def prompt(state: dict, inventory: dict | None = None, soft_budget_tokens: int = 10000,
           engine: str | None = None) -> tuple[str, dict]:
    text = PROMPT + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet(state, inventory, engine), indent=2)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def blockers_path(design_document: str) -> str:
    return Path(design_document).with_suffix(".blockers.json").as_posix()


def check(state: dict, value: dict, changed_files, workspace) -> None:
    stray = stage_access.stray(STAGE, changed_files)
    if stray:
        raise stray_writes.StrayWrites(
            "Checking a design must not change the repository; this attempt changed: " + ", ".join(stray), stray)
    expected = (state.get("workflow") or {}).get("design_document", "")
    if value["design_document"].strip() != expected:
        raise ValueError(f"The check must be of the approved design {expected!r}, not {value['design_document']!r}")
    for conflict in value["conflicts"]:
        missing = [f for f in conflict["files"] if not (Path(workspace) / f.split(":", 1)[0]).is_file()]
        if not conflict["files"] or missing or not conflict["conflicts_with"].strip() or not conflict["options"]:
            raise ValueError("Each conflict needs the constraint it breaks, the existing files where that "
                             f"constraint lives, and options for the user; missing files: {missing}")
        if not conflict.get("example", "").strip():
            raise ValueError("Each conflict needs an example of what would break, in plain English: "
                             f"{conflict['design_says']!r}")
    if not value["conflicts"] and not value["constraints"]:
        raise ValueError("With no conflicts, the check must state the design's binding decisions for the plan")


def apply(state: dict, value: dict, record: dict, workspace, run_probe=None) -> None:
    """``run_probe(command)`` runs a probe in a scratch copy (the unit passes autocode_verify.scratch_run);
    without it a probed conflict is rejected rather than trusted."""
    check(state, value, record.get("changed_files"), workspace)
    shown = run_probes(value["conflicts"], run_probe or (lambda command: {"error": "no probe runner was given"}),
                       what="conflict", key="design_says")
    design = value["design_document"].strip()
    state["design_check"] = {"design_document": design, "conflicts": len(value["conflicts"]),
                             "output": record.get("output"), "probes": shown}
    if value["conflicts"]:
        target = blockers_path(design)
        path = Path(workspace) / target
        path.parent.mkdir(parents=True, exist_ok=True)
        proven = {row["design_says"]: row["probe"] for row in shown}
        path.write_text(json.dumps({"conflicts": [{**c, "proven_by": proven.get(c["design_says"], "")}
                                                  for c in value["conflicts"]]}, indent=2) + "\n")
        state["design_check"]["blockers"] = target
        state.update(status=STOP_STATUS, phase="PAUSED_OR_BLOCKED",
                     stop_reason=f"The approved design {design} conflicts with this repository in "
                                 f"{len(value['conflicts'])} place(s); nothing was built. Decide using {target}.")
        return
    state["design_constraint"] = {"design_document": design, "summary": value["summary"],
                                  "constraints": value["constraints"]}
    # A follow-up that builds the design the previous turn proposed is planned from it, not as a
    # revision of that turn's contract (autocode_follow_up.plan_afresh); otherwise nothing moves.
    follow_up.plan_afresh(state)
    state.update(status="RUNNING", phase="PLANNING", next_stage=workflows.planner_stage(state))


def owns(state: dict) -> bool:
    """A design check never completes a run: it stops it or hands it to planning."""
    return False


def render(state: dict) -> str:
    return ""

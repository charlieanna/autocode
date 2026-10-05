"""The design workflow's first stage: an Architect judges a design before anyone builds it.

A run recognized as ``design`` (autocode_workflows) starts with ``STAGE``. The
Architect reads the design document the request names and the code the design
changes, then reports in one of two modes:

- ``review``: the request asks to judge an existing design. The runner rejects
  the report if the stage changed anything in the workspace outside ``review/``,
  writes ``REPORT_PATH`` from the validated report (goals the design meets,
  concerns with severities, questions only the user can answer) and completes
  the run. Nothing is built. Every blocking concern states the problem as a
  plain-English example, and a concern about what the CODE does today (an
  invariant the design breaks) carries a ``probe``: a command that exits 0
  exactly when that is so. The runner runs every probe in a scratch copy of the
  code and rejects the report if one fails (autocode_test_cases.run_probes). A
  concern about the design text alone has no probe.
- ``propose``: the request asks for a new design. For now that is produced by
  the build pipeline, as before this stage existed: the run continues at the
  stage saved when recognition began.

Pure module: prompt, schema, transition, rendering; the unit passes in the
function that runs probes. Imports nothing from the runner. State key written:
``design_review``.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

try:
    from . import autocode_stage_access as stage_access, autocode_stray_writes as stray_writes
    from . import autocode_workflows as workflows
    from .autocode_test_cases import run_probes
except ImportError:
    import autocode_stage_access as stage_access
    import autocode_stray_writes as stray_writes
    import autocode_workflows as workflows
    from autocode_test_cases import run_probes

STAGE = workflows.DESIGN_STAGE
REPORT_PATH = "review/design-review.json"
SEVERITIES = ("blocking", "advisory")
TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
CONCERN = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "area", "severity", "summary", "evidence", "example", "probe"],
    # example: the problem as one concrete case in plain English; probe: a shell command, run from the
    # repository root, that exits 0 exactly when the code behaves as the concern says. "" when the
    # concern is about the design text alone.
    "properties": {"id": TEXT, "area": TEXT, "severity": {"type": "string", "enum": list(SEVERITIES)},
                   "summary": TEXT, "evidence": TEXT, "example": TEXT, "probe": TEXT},
}
QUESTION = {
    "type": "object", "additionalProperties": False, "required": ["id", "question", "options"],
    "properties": {"id": TEXT, "question": TEXT, "options": TEXTS},
}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["mode", "design_under_review", "verdict", "summary", "satisfied", "concerns", "questions"],
    "properties": {
        "mode": {"type": "string", "enum": ["review", "propose"]},
        "design_under_review": TEXT,
        "verdict": {"type": "string", "enum": ["approve", "request_changes", "not_applicable"]},
        "summary": TEXT,
        "satisfied": TEXTS,
        "concerns": {"type": "array", "items": CONCERN},
        "questions": {"type": "array", "items": QUESTION},
    },
}

PROMPT = """You are the Architect: a senior engineer asked to judge a design before anyone builds it.
You report. You do not write code and you do not edit the design or anything else in the repository.

First decide the mode:
- review: the request asks you to review, challenge or assess an existing design (a document in the
  repository, or one pasted in the request). Do steps 1-5.
- propose: the request asks you to produce a NEW design. Return mode propose, verdict not_applicable,
  design_under_review "", and empty lists; the design is produced by a later stage.

1. Read the design and state its goals. Read the code it changes: the current implementation often
   states requirements the design must keep (ordering, idempotency, compatibility, invariants in
   docstrings and READMEs). A design that reads well can still contradict the code it replaces.
2. Challenge it on correctness, failure modes, operations, scale and migration (including how to roll
   back). """ + stage_access.scratch_rule(STAGE) + """
   Otherwise never write into the workspace: the runner compares it before and after and rejects a
   review that changed anything outside review/.
3. satisfied: the goals the design meets as written, each with why.
4. concerns, each with a severity:
   - blocking: the design cannot be approved until this is resolved (it breaks a requirement, loses or
     duplicates data, has no way back).
   - advisory: worth fixing, not a reason to stop.
   Give evidence: the part of the design and the code that shows it. A concern the design already
   answers is not a concern. Do not pad the list to look thorough.
   Judge the proposed design against its stated requirements and their scope. An explicitly accepted
   tradeoff is advisory unless it violates another binding requirement: name that requirement and
   explain the conflict. Do not silently strengthen a goal or reopen a decision the design settles.
   In particular, rollback to the previous version may explicitly restore its previous behavior,
   including a known bug. Do not require that old version to retain the new version's guarantee unless
   the request or design requires that guarantee during rollback. A probe showing the old bug proves
   old behavior; it does not by itself prove a defect in the proposed design. Still block missing
   rollback procedures, incompatible persisted data, or violations of an explicit rollback guarantee.
   Give every blocking concern an example: the problem as one concrete case in plain English, "Given
   <exact starting state>, when <exact event or action>, then <what goes wrong>". When the concern rests
   on what the CODE does today (an invariant, an ordering check, a charge per call), also give probe: a
   shell command run from the repository root that exits 0 exactly when the code behaves as you say (for
   example: python3 -c "from events.processor import Processor; assert Processor.STRICT_SEQ"). The runner
   runs every probe in a scratch copy and rejects the review if one fails, so only probe what you have
   checked. A concern about the design text alone (a missing rollback step) has probe "".
5. questions: decisions only the requester can make because the design leaves a requirement choice open
   (for example whether strict ordering is required and for which consumers). When a blocking concern
   can be resolved only by that choice, ask it here rather than assuming one interpretation; give each
   the realistic options. Do not ask about a choice the design or the code already settles.
verdict: request_changes when there is at least one blocking concern, otherwise approve.

Return JSON only, matching the schema the runner gives you. The runner saves your report as
review/design-review.json; you do not write that file.
"""


def packet(state: dict, inventory: dict | None = None, engine: str | None = None) -> dict:
    return {"stage": STAGE, "task": state["task"], "workspace": state.get("workspace"),
            "execution_engine": engine, "report_path": REPORT_PATH, "workspace_inventory": inventory or {},
            # Present because every provider reads them; nothing is planned yet.
            "goal_contract": None, "current_task": None, "saved_answers": {}}


def prompt(state: dict, inventory: dict | None = None, soft_budget_tokens: int = 10000,
           engine: str | None = None) -> tuple[str, dict]:
    text = PROMPT + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet(state, inventory, engine), indent=2)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def check(value: dict, changed_files) -> None:
    stray = stage_access.stray(STAGE, changed_files)
    if stray:
        raise stray_writes.StrayWrites(
            "A design review must not change the repository; this attempt changed: " + ", ".join(stray), stray)
    if value["mode"] == "propose":
        if value["concerns"] or value["satisfied"] or value["verdict"] != "not_applicable":
            raise ValueError("A request for a new design is handed on, not reviewed: leave the review fields empty")
        return
    if not value["design_under_review"].strip():
        raise ValueError("A design review must say which design it reviewed")
    blocking = sum(1 for concern in value["concerns"] if concern["severity"] == "blocking")
    if value["verdict"] == "not_applicable" or (value["verdict"] == "approve") != (blocking == 0):
        raise ValueError("verdict must be request_changes exactly when there is a blocking concern")
    unexampled = [c["id"] for c in value["concerns"] if c["severity"] == "blocking" and not c.get("example", "").strip()]
    if unexampled:
        raise ValueError(f"Every blocking concern needs an example of the problem in plain English: {unexampled}")


def apply(state: dict, value: dict, record: dict, workspace, run_probe=None) -> None:
    """``run_probe(command)`` runs a probe in a scratch copy (the unit passes autocode_verify.scratch_run);
    without it a probed concern is rejected rather than trusted."""
    check(value, record.get("changed_files"))
    if value["mode"] == "propose":
        state["design_review"] = {"mode": "propose", "output": record.get("output")}
        state.update(status="RUNNING", phase="DISCOVERING",
                     next_stage=(state.get("workflow") or {}).get("then") or "requirements_gather")
        return
    shown = run_probes(value["concerns"], run_probe or (lambda command: {"error": "no probe runner was given"}),
                       what="concern", key="id")
    report = {key: value[key] for key in ("design_under_review", "verdict", "summary", "satisfied",
                                           "concerns", "questions")}
    target = Path(workspace) / REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n")
    counts = {severity: sum(1 for c in value["concerns"] if c["severity"] == severity) for severity in SEVERITIES}
    state["design_review"] = {"mode": "review", **counts, "verdict": value["verdict"], "report_path": REPORT_PATH,
                              "questions": len(value["questions"]), "design_under_review": value["design_under_review"],
                              "output": record.get("output"), "probes": shown}
    state.update(status="TASK_COMPLETE", phase="COMPLETE", next_stage=None,
                 completed_at=dt.datetime.now(dt.timezone.utc).isoformat())


def owns(state: dict) -> bool:
    """The run ended at the design review (a review, not a request for a new design)."""
    return workflows.kind(state) == "design" and (state.get("design_review") or {}).get("mode") == "review"


def render(state: dict) -> str:
    found = state.get("design_review") or {}
    lines = [f"DESIGN REVIEW COMPLETE — {found.get('verdict', '?')}: {found.get('blocking', 0)} blocking, "
             f"{found.get('advisory', 0)} advisory, {found.get('questions', 0)} question(s) for you",
             "Reviewed: " + str(found.get("design_under_review", "")),
             "Workspace unchanged: " + str(state.get("workspace")),
             "Report: " + str(Path(state.get("workspace", "")) / found.get("report_path", REPORT_PATH))]
    if found.get("output"):
        lines.append("Architect report: " + str(found["output"]))
    if found.get("questions"):
        # The run is finished and waits for nothing: the reply is the next turn (autocode_follow_up).
        lines.append("Reply with --follow-up TEXT to answer them in this run.")
    return "\n".join(lines)

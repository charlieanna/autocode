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

A review never waits for its questions: the run completes, and the user replies
with ``--follow-up``. A reply recognized as design makes the Architect revise the
same review (``revise mode``, autocode_follow_up.design_review_to_revise): its
packet carries the previous report and the user's message, its schema
(``REVISION_SCHEMA``) gives every concern a status (open or resolved) and a
resolution, and the runner refuses a revision that drops or renumbers an earlier
concern, resolves one without saying how, or resolves one it only now raised. A
reply that names a different design gets a fresh review. The report keeps the
whole trail: ``revision`` (1 for a first review) and ``revisions``, one entry
per review with the user's message that prompted it (``said``, null for the
first), its turn's receipt (``event_id``), the verdict, and the ids of the open
blocking, open advisory and resolved concerns and of the questions.

Pure module: prompt, schema, transition, rendering; the unit passes in the
function that runs probes. Imports nothing from the runner. State key written:
``design_review``: {"mode", "verdict", "blocking" and "advisory" (OPEN concerns),
"resolved", "questions" (counts), "report_path", "design_under_review",
"revision", "report_sha256" (the report as written; autocode_follow_up refuses
to carry an edited one), "output", "probes"}. Read by autocode_follow_up, the
completion summary (``render``) and autocode_progress_view.
"""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    from . import autocode_follow_up as follow_up
    from . import autocode_stage_access as stage_access
    from . import autocode_stray_writes as stray_writes
    from . import autocode_workflows as workflows
    from .autocode_test_cases import run_probes
except ImportError:
    import autocode_follow_up as follow_up
    import autocode_stage_access as stage_access
    import autocode_stray_writes as stray_writes
    import autocode_workflows as workflows
    from autocode_test_cases import run_probes

STAGE = workflows.DESIGN_STAGE
REPORT_PATH = "review/design-review.json"
SEVERITIES = ("blocking", "advisory")
TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
CONCERN: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "area", "severity", "summary", "evidence", "example", "probe"],
    # example: the problem as one concrete case in plain English; probe: a shell command, run from the
    # repository root, that exits 0 exactly when the code behaves as the concern says. "" when the
    # concern is about the design text alone.
    "properties": {
        "id": TEXT,
        "area": TEXT,
        "severity": {"type": "string", "enum": list(SEVERITIES)},
        "summary": TEXT,
        "evidence": TEXT,
        "example": TEXT,
        "probe": TEXT,
    },
}
QUESTION = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "question", "options"],
    "properties": {"id": TEXT, "question": TEXT, "options": TEXTS},
}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
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
# Revise mode: every concern also says whether it is still open, and how a resolved one was settled.
STATUSES = ("open", "resolved")
REVISED_CONCERN = {
    **CONCERN,
    "required": [*CONCERN["required"], "status", "resolution"],
    "properties": {**CONCERN["properties"], "status": {"type": "string", "enum": list(STATUSES)}, "resolution": TEXT},
}
REVISION_SCHEMA = {
    **SCHEMA,
    "properties": {**SCHEMA["properties"], "concerns": {"type": "array", "items": REVISED_CONCERN}},
}
# The order a concern's fields, and a revisions entry's, are written in the report.
CONCERN_ORDER = ("id", "area", "severity", "status", "resolution", "summary", "evidence", "example", "probe")
ENTRY_ORDER = ("revision", "said", "event_id", "verdict", "blocking", "advisory", "resolved", "questions")
# A repository path in design_under_review: one with a directory, or a document's file name.
PATH = re.compile(r"(?:[\w.-]+/)+[\w.-]*\w|[\w-][\w.-]*\.(?:md|markdown|rst|txt|adoc|html)\b", re.I)
DOCUMENT = re.compile(r"\.(?:md|markdown|rst|txt|adoc|html)$", re.I)

PROMPT = prompts.get("design-review-02.md") + stage_access.scratch_rule(STAGE) + prompts.get("design-review.md")

# The revision rules, for a revision and for a report-only repair of one.
REVISION_RULES = prompts.get("fragments/design-job/revision-rules.md")
REVISE = prompts.get("fragments/design-job/revise.md") + REVISION_RULES
REPAIR_RULES = prompts.get("fragments/design-job/repair-rules.md") + REVISION_RULES


def revising(state: dict) -> dict | None:
    """The design review the request replies to (autocode_follow_up.design_review_to_revise), or None."""
    return follow_up.design_review_to_revise(state)


def repair_context(state: dict) -> dict:
    """For a report-only repair of a revision: the review it must keep and the reply (jobs.repair_context)."""
    handoff = packet(state)
    return {key: handoff[key] for key in ("previous_review", "user_message") if key in handoff}


def schema_for(state: dict) -> dict:
    """The report schema: SCHEMA for a first review, REVISION_SCHEMA when the request replies to one."""
    return REVISION_SCHEMA if revising(state) else SCHEMA


def packet(state: dict, inventory: dict | None = None, engine: str | None = None) -> dict:
    previous = revising(state)
    return {
        "stage": STAGE,
        "task": state["task"],
        "workspace": state.get("workspace"),
        "execution_engine": engine,
        "report_path": REPORT_PATH,
        "workspace_inventory": inventory or {},
        # Present because every provider reads them; nothing is planned yet.
        "goal_contract": None,
        "current_task": None,
        "saved_answers": {},
        **(
            {
                "previous_review": {
                    key: previous.get(key)
                    for key in (
                        "revision",
                        "design_under_review",
                        "verdict",
                        "summary",
                        "satisfied",
                        "concerns",
                        "questions",
                    )
                },
                "user_message": previous.get("said"),
            }
            if previous
            else {}
        ),
    }


def prompt(
    state: dict, inventory: dict | None = None, soft_budget_tokens: int = 10000, engine: str | None = None
) -> tuple[str, dict]:
    text = (
        PROMPT
        + (REVISE if revising(state) else "")
        + "\nCURRENT HANDOFF DATA\n"
        + json.dumps(packet(state, inventory, engine), indent=2)
    )
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def same_design(before, after) -> bool:
    """Whether two reviews' design_under_review name the same design: a repository path both name (a bare
    file name matches the path it ends), comparing design documents when both name one, so a code path
    or a word like read/write mentioned beside the design does not count; otherwise the same words."""
    paths = [[path.lower().removeprefix("./") for path in PATH.findall(str(text or ""))] for text in (before, after)]
    documents = [[path for path in found if DOCUMENT.search(path)] for found in paths]
    if all(documents):
        paths = documents
    if all(paths):
        return any(
            first == second or first.endswith("/" + second) or second.endswith("/" + first)
            for first in paths[0]
            for second in paths[1]
        )
    words = [" ".join(str(text or "").lower().split()) for text in (before, after)]
    return bool(words[0]) and words[0] == words[1]


def _status(concern: dict) -> str:
    return concern.get("status") or "open"


def check(value: dict, changed_files, previous: dict | None = None) -> None:
    """``previous`` is the review this one replies to (``revising``), or None for a first review."""
    stray = stage_access.stray(STAGE, changed_files)
    if stray:
        raise stray_writes.StrayWrites(
            "A design review must not change the repository; this attempt changed: " + ", ".join(stray), stray
        )
    if value["mode"] == "propose":
        if value["concerns"] or value["satisfied"] or value["verdict"] != "not_applicable":
            raise ValueError("A request for a new design is handed on, not reviewed: leave the review fields empty")
        return
    if not value["design_under_review"].strip():
        raise ValueError("A design review must say which design it reviewed")
    concerns = value["concerns"]
    blocking = [c for c in concerns if c["severity"] == "blocking" and _status(c) == "open"]
    if value["verdict"] == "not_applicable" or (value["verdict"] == "approve") != (not blocking):
        raise ValueError("verdict must be request_changes exactly when there is an open blocking concern")
    unexampled = [c["id"] for c in blocking if not c.get("example", "").strip()]
    if unexampled:
        raise ValueError(f"Every blocking concern needs an example of the problem in plain English: {unexampled}")
    ids = [c["id"] for c in concerns]
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    if previous is not None and duplicated:
        raise ValueError(f"Each concern needs its own id; used more than once: {duplicated}")
    resolved = [c for c in concerns if _status(c) == "resolved"]
    if previous is None or not same_design(previous.get("design_under_review"), value["design_under_review"]):
        if resolved:
            again = (
                f" If it is the review of {previous.get('design_under_review')!r} again, name that design as "
                "previous_review does."
                if previous is not None
                else ""
            )
            raise ValueError(
                "A first review of a design resolves nothing: every concern is open; resolved here: "
                + ", ".join(c["id"] for c in resolved)
                + "."
                + again
            )
        return
    earlier = {c.get("id"): c for c in previous.get("concerns") or []}
    missing = [f"{key} ({earlier[key].get('summary', '')})" for key in earlier if key not in ids]
    if missing:
        raise ValueError(
            "A revision keeps every earlier concern, open or resolved, under its id; missing: " + "; ".join(missing)
        )
    unexplained = [c["id"] for c in resolved if not str(c.get("resolution") or "").strip()]
    if unexplained:
        raise ValueError(f"A resolved concern says what settled it in resolution: {unexplained}")
    new = [c["id"] for c in resolved if c["id"] not in earlier]
    if new:
        raise ValueError(f"Only an earlier concern can be resolved; raised in this revision: {new}")


def apply(state: dict, value: dict, record: dict, workspace, run_probe=None) -> None:
    """``run_probe(command)`` runs a probe in a scratch copy (the unit passes autocode_verify.scratch_run);
    without it a probed concern is rejected rather than trusted."""
    previous = revising(state)
    check(value, record.get("changed_files"), previous)
    if value["mode"] == "propose":
        state["design_review"] = {"mode": "propose", "output": record.get("output")}
        state.update(
            status="RUNNING",
            phase="DISCOVERING",
            next_stage=(state.get("workflow") or {}).get("then") or "requirements_gather",
        )
        return
    shown = run_probes(
        value["concerns"],
        run_probe or (lambda command: {"error": "no probe runner was given"}),
        what="concern",
        key="id",
    )
    concerns = [
        {key: concern.get(key, "") for key in CONCERN_ORDER} | {"status": _status(concern)}
        for concern in value["concerns"]
    ]
    revised = previous is not None and same_design(previous.get("design_under_review"), value["design_under_review"])
    revision = (previous.get("revision") or 1) + 1 if (revised and previous is not None) else 1
    entry = {
        "revision": revision,
        "said": previous.get("said") if previous else None,
        "event_id": previous.get("event_id") if previous else None,
        "verdict": value["verdict"],
        **_ids(concerns),
        "questions": [{"id": q["id"], "question": q["question"]} for q in value["questions"]],
    }
    history = (
        [{key: row.get(key) for key in ENTRY_ORDER} for row in previous.get("revisions") or [_first_entry(previous)]]
        if (revised and previous is not None)
        else []
    )
    report = {
        **{key: value[key] for key in ("design_under_review", "verdict", "summary", "satisfied")},
        "concerns": concerns,
        "questions": value["questions"],
        "revision": revision,
        "revisions": [*history, entry],
    }
    text = json.dumps(report, indent=2) + "\n"
    target = Path(workspace) / REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    state["design_review"] = {
        "mode": "review",
        **{key: len(entry[key]) for key in ("blocking", "advisory", "resolved")},
        "verdict": value["verdict"],
        "report_path": REPORT_PATH,
        "questions": len(value["questions"]),
        "design_under_review": value["design_under_review"],
        "revision": revision,
        "report_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "output": record.get("output"),
        "probes": shown,
    }
    state.update(
        status="TASK_COMPLETE", phase="COMPLETE", next_stage=None, completed_at=dt.datetime.now(dt.UTC).isoformat()
    )


def _ids(concerns: list[dict]) -> dict:
    """The ids of the open blocking, open advisory and resolved concerns."""
    unresolved = [c for c in concerns if _status(c) != "resolved"]
    return {
        "blocking": [c.get("id") for c in unresolved if c.get("severity") == "blocking"],
        "advisory": [c.get("id") for c in unresolved if c.get("severity") != "blocking"],
        "resolved": [c.get("id") for c in concerns if _status(c) == "resolved"],
    }


def _first_entry(previous: dict) -> dict:
    """The trail entry of a review saved before reports kept their revisions. A reply recorded before
    then carried only its open concerns' ids (blocking, advisory), not the concerns."""
    ids = (
        _ids(previous["concerns"])
        if previous.get("concerns") is not None
        else {key: [row.get("id") for row in previous.get(key) or []] for key in ("blocking", "advisory")}
        | {"resolved": []}
    )
    return {
        "revision": previous.get("revision") or 1,
        "said": None,
        "event_id": None,
        "verdict": previous.get("verdict"),
        **ids,
        "questions": [{"id": q.get("id"), "question": q.get("question")} for q in previous.get("questions") or []],
    }


def owns(state: dict) -> bool:
    """The run ended at the design review (a review, not a request for a new design)."""
    return workflows.kind(state) == "design" and (state.get("design_review") or {}).get("mode") == "review"


def render(state: dict) -> str:
    found = state.get("design_review") or {}
    lines = [
        f"DESIGN REVIEW COMPLETE — {found.get('verdict', '?')}: {found.get('blocking', 0)} blocking, "
        f"{found.get('advisory', 0)} advisory, {found.get('questions', 0)} question(s) for you",
        *(
            [f"Revision {found['revision']}: {found.get('resolved', 0)} concern(s) resolved"]
            if (found.get("revision") or 1) > 1
            else []
        ),
        "Reviewed: " + str(found.get("design_under_review", "")),
        "Workspace unchanged: " + str(state.get("workspace")),
        "Report: " + str(Path(state.get("workspace", "")) / found.get("report_path", REPORT_PATH)),
    ]
    if found.get("output"):
        lines.append("Architect report: " + str(found["output"]))
    if found.get("questions"):
        # The run is finished and waits for nothing: the reply is the next turn (autocode_follow_up).
        lines.append("Reply with --follow-up TEXT to answer them in this run.")
    return "\n".join(lines)

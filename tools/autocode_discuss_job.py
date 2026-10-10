"""The discuss workflow: answer a question or weigh a tradeoff from the repository, building nothing.

A run recognized as ``discuss`` (autocode_workflows) has one stage, ``STAGE``. The
Analyst reads the code (and may run it in a scratch copy of its own) and returns an
answer, the evidence behind it as claims tied to files in the repository, and at most
three questions only the user can answer. When the request asks for a written note
(a decision record, an investigation note), the report carries the note's path and
content and the runner writes it. The runner then:

- rejects the report if the stage changed anything in the workspace (the runner, not
  the model, writes the one requested note),
- rejects evidence that cites a file which does not exist, so every claim is grounded,
- runs each claim's ``probe``, when it has one: a command that exits 0 exactly when the
  claim holds, run by the runner in a scratch copy of the code as it is. A probe that
  fails rejects the answer. The claim's ``example`` states in plain English the concrete
  case the probe checks. Claims without a probe stay grounded by their source file only,
- writes the requested note (a ``.json`` note must parse), and completes the run.

Pure module: prompt, schema, transition, rendering; the unit passes in the function
that runs probes. Imports nothing from the runner. State key written: ``answer``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import datetime as dt
import json
from pathlib import Path

try:
    from . import autocode_stage_access as stage_access
    from . import autocode_stray_writes as stray_writes
    from . import autocode_workflows as workflows
    from .autocode_test_cases import run_probes
except ImportError:
    import autocode_stage_access as stage_access
    import autocode_stray_writes as stray_writes
    import autocode_workflows as workflows
    from autocode_test_cases import run_probes

STAGE = workflows.DISCUSS_STAGE
NOTES_PREFIX = "docs/"
NOTE_SUFFIXES = (".json", ".md", ".txt")
MAX_QUESTIONS = 3
TEXT = {"type": "string"}
EVIDENCE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["claim", "source", "example", "probe"],
    # example: the concrete case a probe checks ("Given ..., when ..., then ..."); probe: a shell
    # command, run from the repository root, that exits 0 exactly when the claim holds. Both "" when
    # the claim is shown by its source alone.
    "properties": {"claim": TEXT, "source": TEXT, "example": TEXT, "probe": TEXT},
}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "evidence", "questions", "note_path", "note_content"],
    "properties": {
        "answer": TEXT,
        "evidence": {"type": "array", "items": EVIDENCE},
        "questions": {"type": "array", "maxItems": MAX_QUESTIONS, "items": TEXT},
        "note_path": TEXT,
        "note_content": TEXT,
    },
}

PROMPT = (
    """You are the Analyst: an engineer asked a question about this codebase. You answer it with evidence.
You do not change the code, and nothing gets built.

The request may be a question ("why does the code do X", "what would break if we removed Y") or a
tradeoff ("should we use A or B"). Either way:

1. Find the facts in the repository before anything else: the code, its configuration and deployment
   files, its docs and tests. Deciding facts are often in a different file from the one the question
   names (how the service is deployed, a limit in a docstring, who calls a function). """
    + stage_access.scratch_rule(STAGE)
    + """
   Otherwise never write into the workspace: the runner compares it before and after and rejects an
   answer that changed anything.
2. answer: the answer in plain words. For a tradeoff, give a recommendation AND its consequences, and
   what would change it; do not just pick one.
3. evidence: each claim your answer rests on, with source = the repository file (optionally
   "path:line") that shows it. The runner checks that every source file exists.
   A claim about what the code DOES (a count, a result, what breaks) is stronger when shown by running
   it. For such a claim give example, one concrete case in plain English ("Given ..., when ..., then
   ..."), and probe, a shell command run from the repository root that exits 0 exactly when the claim
   holds (for example: python3 -c "from cache import TTL; assert TTL == 3600"). The runner runs every
   probe in a scratch copy of the code as it is now and rejects the answer if one fails, so only probe
   what you have checked. A claim shown by its source alone has example and probe "".
4. questions: only facts you could not find in the repository and that would change the answer; at
   most three. A fact the repository states is not a question.
5. If the request asks for a written note (a file path and its format), put the path in note_path
   (under docs/) and the file's full content in note_content, exactly in the format the request
   describes; for a .json file, note_content is the JSON text. The runner writes it. If no file is
   requested, note_path and note_content are "".

Return JSON only, matching the schema the runner gives you.
"""
)


def packet(state: RunState, inventory: dict | None = None, engine: str | None = None) -> dict:
    return {
        "stage": STAGE,
        "task": state["task"],
        "workspace": state.get("workspace"),
        "execution_engine": engine,
        "workspace_inventory": inventory or {},
        # Present because every provider reads them; nothing is planned yet.
        "goal_contract": None,
        "current_task": None,
        "saved_answers": {},
    }


def prompt(
    state: RunState, inventory: dict | None = None, soft_budget_tokens: int = 10000, engine: str | None = None
) -> tuple[str, dict]:
    text = PROMPT + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet(state, inventory, engine), indent=2)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def _inside(path: str) -> bool:
    parts = Path(path).parts
    return bool(path.strip()) and not Path(path).is_absolute() and ".." not in parts and ".git" not in parts


def check(value: dict, changed_files, workspace) -> None:
    """Reject an answer that changed the repository, cites missing files, or asks for a bad note."""
    note = value["note_path"].strip()
    stray = sorted(str(path) for path in (changed_files or []) if str(path) != note)
    if stray:
        raise stray_writes.StrayWrites(
            "A discussion must not change the repository; this attempt changed: " + ", ".join(stray), stray
        )
    if not value["answer"].strip():
        raise ValueError("The answer is empty")
    if not value["evidence"]:
        raise ValueError("An answer needs evidence from the repository")
    missing = []
    for row in value["evidence"]:
        source = row["source"].split(":", 1)[0].strip()
        if not _inside(source) or not (Path(workspace) / source).is_file():
            missing.append(row["source"])
    if missing:
        raise ValueError(f"Evidence must cite files in the repository; not found: {missing}")
    if len(value["questions"]) > MAX_QUESTIONS:
        raise ValueError(f"At most {MAX_QUESTIONS} questions")
    if note:
        if not _inside(note) or not note.startswith(NOTES_PREFIX) or not note.endswith(NOTE_SUFFIXES):
            raise ValueError(f"note_path must be a {'/'.join(NOTE_SUFFIXES)} file under {NOTES_PREFIX}: {note!r}")
        if note.endswith(".json"):
            try:
                json.loads(value["note_content"])
            except ValueError as error:
                raise ValueError(f"note_content for {note} is not valid JSON: {error}") from None
    elif value["note_content"].strip():
        raise ValueError("note_content without a note_path")


def apply(state: RunState, value: dict, record: dict, workspace, run_probe=None) -> None:
    """``run_probe(command)`` runs a probe in a scratch copy (the unit passes autocode_verify.scratch_run);
    without it, an answer with probes is rejected rather than trusted."""
    check(value, record.get("changed_files"), workspace)
    shown = run_probes(value["evidence"], run_probe or (lambda command: {"error": "no probe runner was given"}))
    note = value["note_path"].strip()
    if note:
        target = Path(workspace) / note
        target.parent.mkdir(parents=True, exist_ok=True)
        content = value["note_content"]
        if note.endswith(".json"):
            content = json.dumps(json.loads(content), indent=2) + "\n"
        target.write_text(content)
    state["answer"] = {
        "answer": value["answer"],
        "evidence": value["evidence"],
        "questions": value["questions"],
        "note_path": note,
        "output": record.get("output"),
        "probes": shown,
    }
    state.update(
        {
            "status": "TASK_COMPLETE",
            "phase": "COMPLETE",
            "next_stage": None,
            "completed_at": dt.datetime.now(dt.UTC).isoformat(),
        }
    )


def owns(state: RunState) -> bool:
    return workflows.kind(state) == "discuss" and bool(state.get("answer"))


def render(state: RunState) -> str:
    found = state.get("answer") or {}
    lines = ["ANSWER — nothing was changed", "", found.get("answer", ""), "", "Evidence:"]
    probed = {row["claim"] for row in found.get("probes") or []}
    lines += [
        f"  - {row['claim']} ({row['source']})"
        + ("; shown by running: " + row["probe"] if row["claim"] in probed else "")
        for row in found.get("evidence") or []
    ]
    lines += ["Question for you: " + question for question in found.get("questions") or []]
    if found.get("note_path"):
        lines.append("Note written: " + str(Path(state.get("workspace", "")) / found["note_path"]))
    if found.get("output"):
        lines.append("Analyst report: " + str(found["output"]))
    if found.get("questions"):
        # The run is finished and waits for nothing: the reply is the next turn (autocode_follow_up).
        lines.append("Reply with --follow-up TEXT to answer them in this run.")
    return "\n".join(lines)

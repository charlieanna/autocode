"""A run's part in the durable project notes (#2943): what it was told, and what it learned.

- ``install``: a fresh launch freezes the project's notes as ``state.project_notes.input``. Resumes
  never reload it, so editing or retracting a note cannot change a run in progress.
- ``context``: the active notes the Requirements, Planner and Investigator prompts are given as
  advisory context, never as authority.
- ``proposals``: the Investigator may name facts it paid to discover (``project_notes`` in its
  report). They are kept only with a receipt: the probe the runner ran over the cited files, which
  exited 0 and failed on the counterexample (autocode_stuck_job.apply). A report with no passing
  probe records why nothing was kept.
- ``publish``: after the investigation's checkpoint, the receipted notes are written to the project
  file (autocode_project_notes) and the investigation records the IDs, or why it could not. A
  failure never stops the run.
- ``view``: the status view's ``project_notes``.

Domain layer: pure functions over run state plus the project-notes store; nothing from the runner.
Only this module writes ``state.project_notes`` and an investigation's ``project_notes``.
"""

from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_project_memory as memory
    from . import autocode_project_memory_interface as memory_interface
    from . import autocode_project_notes as store
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_project_memory as memory
    import autocode_project_memory_interface as memory_interface
    import autocode_project_notes as store
    import autocode_prompts as prompts

KEY = "project_notes"
# The stages given the notes as advisory context: Requirements and the Planner. The Investigator
# gets them in its own handoff (autocode_stuck_job.packet).
READERS = frozenset(
    {"requirements_gather", "requirements", "draft_plan", "revise_plan", "plan", "plan_revise", "plan_task"}
)
# The rule a reading stage's prompt carries with the notes, and the Investigator's report-field rule.
RULE = prompts.get("fragments/project-notes/advisory.md")
INVESTIGATOR_RULE = prompts.get("fragments/project-notes/investigator.md")
# At most this many notes from one investigation, so one report cannot crowd the file.
MAX_PER_INVESTIGATION = 3
# The Investigator's optional report field (autocode_stuck_job.SCHEMA).
REPORT_SCHEMA = {
    "type": "array",
    "maxItems": MAX_PER_INVESTIGATION,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "text"],
        "properties": {
            "kind": {"type": "string", "enum": list(store.KINDS)},
            "text": {"type": "string"},
        },
    },
}


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def load(workspace) -> dict:
    """The selected project's notes, from a project folder or one of its task worktrees."""
    try:
        project = memory_interface.selected(workspace)
    except (OSError, ValueError):
        return {
            "status": "invalid",
            "notes": [],
            "sha256": None,
            "note": "Project notes could not identify the selected project; they were ignored.",
        }
    return store.load(project)


def install(state: RunState, snapshot) -> None:
    """Freeze the launch snapshot once, at a fresh launch."""
    if KEY in state:
        return
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    try:
        notes = store.sanitize({"version": store.VERSION, "notes": snapshot.get("notes") or []})["notes"]
    except ValueError:
        notes = []
    state["project_notes"] = {
        "version": 1,
        "input": {
            "status": snapshot.get("status") if snapshot.get("status") in ("loaded", "invalid") else "absent",
            "notes": store.active(notes),
            "sha256": snapshot.get("sha256") if isinstance(snapshot.get("sha256"), str) else None,
        },
    }


def _input_notes(state: RunState) -> list[dict]:
    memory = state.get(KEY)
    return (memory.get("input") or {}).get("notes") or [] if isinstance(memory, dict) else []


def context(state: RunState) -> list[dict]:
    """The launch's active notes for a stage prompt; [] when there are none."""
    return store.context(_input_notes(state))


def for_stage(state: RunState, stage) -> list[dict]:
    """The notes ``stage``'s handoff carries: the launch's active notes for a reader, else []."""
    return context(state) if stage in READERS else []


def _run_name(run_dir) -> str | None:
    if not run_dir:
        return None
    name = Path(run_dir).name
    return name if store.RUN_PATTERN.fullmatch(name) else None


def proposals(value: dict, shown: list[dict], entry: dict, *, run_dir) -> dict | None:
    """What an accepted investigation report's ``project_notes`` become, before they are written.

    ``shown`` is the probe receipts autocode_test_cases.run_probes returned: a note is kept only when
    a probe the runner ran showed the diagnosed cause. None when the report names no note.
    """
    rows = value.get("project_notes")
    if not isinstance(rows, list) or not rows:
        return None
    shown = [row for row in shown or [] if row.get("exit_code") == 0]
    run = _run_name(run_dir)
    if not shown or run is None:
        return {
            "status": "not_recorded",
            "proposed": len(rows),
            "reason": "No probe the runner ran showed the diagnosed cause, so there is no receipt to keep a note on.",
        }
    probe = shown[0]["probe"]
    observation = (
        "The Investigator's probe exited 0 over the files it cited"
        + (" and failed once one was changed" if (shown[0].get("counterexample") or {}).get("exit_code") else "")
        + ": "
        + probe
    )
    if len(observation) > store.MAX_OBSERVATION or not memory.safe(observation):
        observation = "The Investigator's probe exited 0 over the files it cited (its command is not shareable)."
    receipt = {
        "run": run,
        "stage": "investigate_stuck",
        "investigation": entry.get("identity"),
        "observation": observation,
        "source_revision": entry.get("source_revision"),
        "recorded_at": entry.get("finished_at") or now(),
    }
    kept: list[dict] = []
    omitted = 0
    for row in rows[:MAX_PER_INVESTIGATION]:
        note = store.candidate(row.get("kind"), row.get("text"), receipt) if isinstance(row, dict) else None
        if note is None or any(note["id"] == other["id"] for other in kept):
            omitted += 1
        else:
            kept.append(note)
    omitted += max(0, len(rows) - MAX_PER_INVESTIGATION)
    if not kept:
        return {
            "status": "not_recorded",
            "proposed": len(rows),
            "reason": "Every proposed note was malformed or held a secret or a local path.",
        }
    return {"status": "pending", "notes": kept, "omitted": omitted}


def publish(path, state: RunState) -> bool:
    """Write every pending investigation's notes to the project; True when the state changed.

    Called after a run's state.json checkpoint (autocode_status), so a note is never written for an
    investigation the run has not saved.
    """
    if Path(path).name != "state.json":
        return False
    root = state.get("project_workspace") or state.get("workspace")
    changed = False
    for entry in state.get("stuck_investigations") or []:
        pending = entry.get("project_notes") if isinstance(entry, dict) else None
        if not isinstance(pending, dict) or pending.get("status") != "pending":
            continue
        try:
            if not isinstance(root, str) or not root:
                raise ValueError("No selected project")
            ids = store.add(memory_interface.selected(root), pending["notes"])
            entry["project_notes"] = {
                "status": "written",
                "ids": ids,
                "omitted": pending.get("omitted", 0),
                "notes": pending["notes"],
            }
        except (OSError, TypeError, ValueError, KeyError):
            entry["project_notes"] = {
                **pending,
                "status": "unavailable",
                "reason": "The project notes file could not be written; the run is unchanged.",
            }
        changed = True
    return changed


def learned(state: RunState) -> list[dict]:
    """The notes this run wrote to the project, oldest first."""
    rows: list[dict] = []
    for entry in state.get("stuck_investigations") or []:
        record = entry.get("project_notes") if isinstance(entry, dict) else None
        if isinstance(record, dict) and record.get("status") == "written":
            rows.extend(copy.deepcopy(record.get("notes") or []))
    return rows


def view(state: RunState) -> dict:
    """The status view's ``project_notes``: what this run was told, what it learned, and the list action.

    ``notes`` are the launch's active notes, each with its receipt; ``learned`` the notes this run
    wrote. ``next_command`` lists the project's current notes, each with the argument arrays that
    retract or correct it (``autocode notes list --json``); None when the run has no project folder.
    """
    root = state.get("project_workspace") or state.get("workspace")
    return {
        "notes": copy.deepcopy(store.active(_input_notes(state))),
        "learned": learned(state),
        "next_command": ["notes", "list", "--workspace", root, "--json"] if isinstance(root, str) and root else None,
    }

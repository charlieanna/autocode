"""Deliver a build that completed in a scratch project to a named project beside the person's files.

A task started in a folder outside Git runs in a private scratch project in that folder's
own AutoCode store (``autocode_scratch_projects``). The run's saved state names that path
throughout, so the scratch project never moves. When a build or a bug fix completes there
and its source differs from what was last delivered, ``deliver``:

1. commits the source to the scratch project's branch ``autocode/delivered`` with a private
   index; only that ref moves, so HEAD and the snapshot the completion evidence is pinned to
   stay where they are;
2. on the first delivery, clones that branch into ``FOLDER/NAME`` on branch ``main``, NAME
   taken from the message that asked for the work (a folder that already exists is never
   reused: ``-2``, ``-3`` and so on). The clone's remote ``autocode`` is the scratch project;
3. on a later turn of the same chat, fast-forwards that project when it is still on the last
   delivered commit with no changes of its own. Otherwise the update waits on the remote branch
   and the note says how to take it.

A chat that never builds (a greeting, a question, a review) delivers nothing, so nothing
appears among the person's files. Each step is safe to repeat after a crash.

The record ``SCRATCH/.autocode/delivered-project.json`` names the project and keeps one note
per delivering turn; the chat history shows those notes (``notes``).

Lower layer only: Git, files, ``autocode_worktrees`` and ``autocode_scratch_projects``; never
``autocode``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import json
import os
import re
import subprocess
from pathlib import Path

try:
    from . import autocode_scratch_projects as scratch_projects
    from . import autocode_util as util
    from . import autocode_worktrees as worktrees
except ImportError:
    import autocode_scratch_projects as scratch_projects
    import autocode_util as util
    import autocode_worktrees as worktrees

BRANCH = "autocode/delivered"
REMOTE = "autocode"
RECORD = "delivered-project.json"
# The workflows whose completion leaves source to hand over. A discussion, a review or a design
# proposal answers in the chat; its notes are not a project.
DELIVERING = ("build", "bugfix")
# Words that say what the person wants done rather than what the project is.
FILLER = frozenset(
    re.findall(
        "[a-z]+",
        "a an and app application are be build can could create for from has have help i in is it its let "
        "lets make me my new of on people person please program project quick should simple small so some "
        "that the their them they this to tool us use user users using want we which who will with write "
        "would you your ",
    )
)
NAME_WORDS = 3


def _selected(state: RunState, workspace) -> Path | None:
    """The folder the person chose, when this run works in one of its scratch projects."""
    record = state.get("workspace_bootstrap")
    if not isinstance(record, dict):
        return None
    selected, project = record.get("selected"), record.get("project")
    if not isinstance(selected, str) or not isinstance(project, str):
        return None
    if Path(project) != Path(workspace).resolve() or not scratch_projects.is_scratch(selected, project):
        return None
    return Path(selected)


def name(text: str) -> str:
    """A folder name from a description: its first few words that say what the project is."""
    words = re.findall("[a-z0-9]+", (text or "").lower())
    kept = [word for word in words if word not in FILLER][:NAME_WORDS]
    return "-".join(kept or words[:NAME_WORDS])[:40].strip("-") or "project"


def _described(state: RunState) -> str:
    """What the approved plan says it builds, else the message that asked for it."""
    body = (state.get("goal_contract") or {}).get("body")
    outcome = body.get("intended_outcome") if isinstance(body, dict) else None
    return outcome if isinstance(outcome, str) and outcome.strip() else _request(state)


def _request(state: RunState) -> str:
    """The message that asked for this turn's work: the newest follow-up, else the opening request."""
    turns = state.get("turns") or []
    if turns and isinstance(turns[-1].get("say"), str):
        return turns[-1]["say"]
    return str(state.get("task") or "").strip().splitlines()[0] if state.get("task") else ""


def _free(selected: Path, base: str) -> Path:
    candidate, number = selected / base, 2
    while candidate.exists() or candidate.is_symlink():
        candidate, number = selected / f"{base}-{number}", number + 1
    return candidate


def _record_path(project) -> Path:
    return Path(project) / ".autocode" / RECORD


def _read(project) -> dict | None:
    path = _record_path(project)
    try:
        if path.is_symlink():
            return None
        data = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("project"), str) else None


def _write(project, record: dict) -> None:
    util.atomic_json(_record_path(project), record)


def _commit(state: RunState, workspace: Path) -> str | None:
    """Commit the delivered source on BRANCH; the commit to hand over, or None when nothing was built."""
    tree = worktrees.source_tree(workspace)
    tip = worktrees._git(workspace, "rev-parse", "--verify", "-q", f"refs/heads/{BRANCH}", check=False) or None
    parent = tip or worktrees._git(workspace, "rev-parse", "HEAD")
    if tree == worktrees._git(workspace, "rev-parse", f"{parent}^{{tree}}"):
        return tip
    title = (_request(state) or "AutoCode task").strip().splitlines()[0][:72]
    message = f"{title}\n\nDelivered by AutoCode run {Path(state.get('run_dir', '')).name or 'unknown'}.\n"
    commit = subprocess.run(
        ["git", "-C", str(workspace), "commit-tree", tree, "-p", parent],
        input=message,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **worktrees.IDENTITY},
    ).stdout.strip()
    worktrees._git(workspace, "update-ref", f"refs/heads/{BRANCH}", commit, tip or "")
    return commit


def _note(record: dict, turn: int, text: str) -> str:
    record.setdefault("notes", {})[str(turn)] = text
    return text


def deliver(state: RunState, workspace) -> str:
    """Hand a completed build in a scratch project to its named project; the line to print, or ''."""
    if state.get("status") != worktrees.COMPLETE:
        return ""
    if (state.get("workflow") or {}).get("kind", "build") not in DELIVERING:
        return ""
    selected = _selected(state, workspace)
    if selected is None:
        return ""
    workspace = Path(workspace).resolve()
    turn = len(state.get("turns") or [])
    try:
        commit = _commit(state, workspace)
        if commit is None:
            return ""
        record = _read(workspace)
        if record is None:
            # Named and recorded before cloning, so a retry after a crash reuses this name.
            record = {"project": str(_free(selected, name(_described(state)))), "commit": None}
            _write(workspace, record)
        target = Path(record["project"])
        if record.get("commit") == commit:
            text = (record.get("notes") or {}).get(str(turn)) or f"Your project is in {target}."
            return "\n" + text
        if record.get("commit") is None:
            if not target.exists():
                worktrees._git(
                    selected, "clone", "-q", "--origin", REMOTE, "--branch", BRANCH, str(workspace), str(target)
                )
                worktrees._git(target, "branch", "-q", "-m", BRANCH, "main")
            record["commit"] = commit
            text = _note(
                record,
                turn,
                f"Your project is ready in {target}: a Git repository on branch main with what I built. "
                "Rename or move the folder whenever you like; push it to GitHub when you want it there.",
            )
        elif (
            (target / ".git").is_dir()
            and worktrees._git(target, "rev-parse", "HEAD", check=False) == record["commit"]
            and not worktrees._git(target, "status", "--porcelain", check=False)
        ):
            worktrees._git(target, "fetch", "-q", REMOTE)
            worktrees._git(target, "merge", "-q", "--ff-only", commit)
            record["commit"] = commit
            text = _note(record, turn, f"I updated your project in {target} with this change.")
        else:
            record["pending"] = commit
            text = _note(
                record,
                turn,
                f"Your project in {target} has changes of its own or has moved, so I left it as it is. "
                f"This change is on branch {BRANCH} of its {REMOTE} remote: "
                f"`git -C {target} pull {REMOTE} {BRANCH}` takes it.",
            )
        _write(workspace, record)
        return "\n" + text
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        return f"\nThe project was not delivered: {error}"


def notes(state: RunState) -> dict[int, str]:
    """What each delivering turn said about the project, by turn number; empty outside a scratch project."""
    record = state.get("workspace_bootstrap")
    if not isinstance(record, dict):
        return {}
    project = record.get("project")
    if not isinstance(project, str) or not scratch_projects.is_scratch(record.get("selected") or "", project):
        return {}
    saved = (_read(project) or {}).get("notes")
    if not isinstance(saved, dict):
        return {}
    return {int(turn): text for turn, text in saved.items() if turn.isdigit() and isinstance(text, str)}

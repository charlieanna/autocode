"""Where a chat changes files: its worktree and branch, and the places a new chat may choose.

Two VS Code chats in one folder used to work on the same files and branch: every chat started
``--in-place``, so each changed the folder the person had open, on whatever branch was checked out
there, and a chat stopped when another one (or the person) changed the files it was reading. A chat
now says where it works, and a new chat chooses (owner request 2026-11-11):

- ``new_branch`` (the default): Requirements talks with the person in the folder, then the chat plans
  and builds in a worktree and branch of its own (``--build-in-worktree``, autocode_build_worktree).
- ``branch``: the same, on an existing local branch (``--worktree-branch NAME``), for example a
  finished chat's branch to carry on. A branch checked out anywhere else, or one another chat is about
  to take, is not offered: two chats never share one worktree or branch.
- ``folder``: the chat changes the folder's own files on its current branch (``--in-place``). Its
  ``warning`` says that this can conflict with the person's edits and names the other chats working
  there; a client shows it and asks the person to confirm before starting.

``where`` is the status view's ``worktree`` field, from the saved run alone (and the checkout's
``HEAD`` file for a run in the person's folder, read without starting Git). ``options`` and
``autocode worktrees list --json`` are what a client reads before a new chat starts; each place
names the ``start_args`` the client passes to the start unchanged.

Lower layer: files, Git and the run registry; never ``autocode`` or ``autopilot``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

SCHEMA = 1
KEY = "build_worktree"  # autocode_build_worktree.KEY
PLANNING = "planning"  # autocode_build_worktree.PLANNING: started in the folder, moves before the Planner
NEW_BRANCH, BRANCH, FOLDER = "new_branch", "branch", "folder"
WORKTREE_FLAG, BRANCH_FLAG, IN_PLACE_FLAG = "--build-in-worktree", "--worktree-branch", "--in-place"
COMPLETE = ("TASK_COMPLETE", "COMPLETE")
# Branches a Builder worker makes for one batch, removed again once it is integrated: never a chat's.
WORKER_BRANCH = "autocode/builder-"
BRANCHES_SHOWN = 30
CHATS_NAMED = 3
TITLE_LIMIT = 60


def branch_of(checkout) -> str | None:
    """The branch checked out in ``checkout``, from its ``HEAD`` file; None when detached or unreadable."""
    marker = Path(checkout) / ".git"
    try:
        if marker.is_dir():
            git_dir = marker
        elif marker.is_file():
            line = marker.read_text().strip()
            if not line.startswith("gitdir:"):
                return None
            git_dir = Path(line[len("gitdir:") :].strip())
            git_dir = git_dir if git_dir.is_absolute() else Path(checkout) / git_dir
        else:
            return None
        head = (git_dir / "HEAD").read_text().strip()
    except OSError:
        return None
    prefix = "ref: refs/heads/"
    return head[len(prefix) :] if head.startswith(prefix) else None


def where(state: RunState, workspace=None) -> dict | None:
    """Where this run changes files, as a person reads it: the status view's ``worktree`` (None before a workspace).

    ``kind`` is ``worktree`` (a worktree and branch of its own), ``moving`` (Requirements in the
    person's folder, then its own worktree from the Planner on), ``project`` (a project AutoCode made for
    this chat) or ``folder`` (the person's folder itself, shared with their edits and other chats)."""
    path = str(workspace) if workspace is not None else state.get("workspace")
    if not isinstance(path, str) or not path:
        return None
    saved = state.get(KEY)
    record: dict = saved if isinstance(saved, dict) else {}
    branch, project = state.get("task_branch"), state.get("project_workspace")
    if isinstance(branch, str) and branch and isinstance(project, str) and project:
        existing = bool(record.get("existing_branch"))
        whose = "your branch" if existing else "its own branch"
        return _place(
            "worktree",
            path,
            branch,
            project,
            f"Own worktree · branch {branch}",
            f"This chat changes files only in a worktree of its own, on {whose}. Your folder and other chats "
            "are not changed; review and merge the branch when it is done.",
            existing_branch=existing,
        )
    here = branch_of(path)
    on = f"branch {here}" if here else "a detached HEAD"
    if record.get("state") == PLANNING:
        target = record.get("branch") if isinstance(record.get("branch"), str) else None
        later = f"your branch {target}" if target else "a new branch of its own"
        return _place(
            "moving",
            path,
            here,
            path,
            f"Your folder until planning · then {target or 'a new branch'}",
            "This chat reads your folder while it talks with you about the task. From planning on it works in a "
            f"worktree of its own on {later}, so your folder is not changed.",
            existing_branch=bool(target),
            moves_to_branch=target,
        )
    if state.get("workspace_bootstrap"):
        return _place(
            "project",
            path,
            here,
            path,
            f"Own project · {on}",
            "AutoCode made this folder a project for this chat; it changes files there.",
        )
    return _place(
        "folder",
        path,
        here,
        path,
        f"Your folder · {on}",
        "This chat changes files directly in your folder, on the branch checked out there. Your own edits "
        "there and other chats working in this folder can conflict with it.",
    )


def _place(kind, path, branch, folder, label, sentence, *, existing_branch=False, moves_to_branch=None) -> dict:
    return {
        "kind": kind,
        "path": path,
        "branch": branch,
        "folder": folder,
        "existing_branch": existing_branch,
        "moves_to_branch": moves_to_branch,
        "label": label,
        "sentence": sentence,
    }


# ---- A new chat's choices ------------------------------------------------------------------------


def options(folder) -> dict:
    """The places a new chat in ``folder`` may work, with the chats already working there."""
    folder = Path(folder).resolve()
    try:
        root = Path(util.git(folder, "rev-parse", "--show-toplevel", timeout=30)).resolve()
        util.git(folder, "rev-parse", "--verify", "HEAD", timeout=30)
        checkout = root == folder
    except (OSError, ValueError):
        checkout = False
    if not checkout:
        # Not the root of a checkout with a commit: the chat works where it is, as every new chat there does
        # (autocode_build_worktree.request builds in place; a folder outside Git is made a project).
        place = {
            "id": FOLDER,
            "label": "This folder",
            "does": "This folder is not the top of a Git checkout with a commit, so the chat works here.",
            "start_args": [WORKTREE_FLAG],
            "warning": None,
        }
        return {
            "schema": SCHEMA,
            "folder": str(folder),
            "branch": None,
            "default": FOLDER,
            "places": [place],
            "active": [],
        }
    current = branch_of(folder)
    active = _active(folder)
    checked_out = _checked_out(folder)
    taken = {chat["moves_to_branch"]: chat for chat in active if chat.get("moves_to_branch")}
    new_branch = {
        "id": NEW_BRANCH,
        "label": "New worktree and branch",
        "does": (
            "The chat talks with you here, then plans and builds in a worktree of its own on a new branch named "
            "from the task, starting from your last commit. Your folder and other chats are not changed."
        ),
        "start_args": [WORKTREE_FLAG],
        "warning": None,
    }
    rows = []
    for name in _branches(folder):
        row: dict[str, Any] = {"name": name, "start_args": [WORKTREE_FLAG, BRANCH_FLAG, name], "unavailable": None}
        if name in checked_out:
            tree = checked_out[name]
            if tree == folder:
                row["unavailable"] = "It is checked out in your folder; choose This folder to work on it there."
            else:
                chat = next((chat for chat in active if chat["path"] == str(tree)), None)
                row["unavailable"] = f"It is checked out in {tree}" + (
                    f", where the chat “{chat['title']}” works." if chat else "."
                )
        elif name in taken:
            row["unavailable"] = f"The chat “{taken[name]['title']}” plans and builds on it."
        if row["unavailable"]:
            row["start_args"] = None
        rows.append(row)
    branch = {
        "id": BRANCH,
        "label": "Existing branch",
        "does": (
            "The chat talks with you here, then plans and builds in a worktree of its own on the branch you pick, "
            "starting from that branch's last commit. A branch another chat or worktree uses is not offered."
        ),
        "branches": rows,
    }
    here = [chat for chat in active if chat["path"] == str(folder)]
    on = f"branch {current}" if current else "a detached HEAD"
    warning = (
        f"This chat changes the files in {folder} directly, on {on}. Your own uncommitted edits there, and any "
        "other chat working in this folder, can conflict with it."
    )
    if here:
        named = ", ".join(f"“{chat['title']}”" for chat in here[:CHATS_NAMED])
        more = f" and {len(here) - CHATS_NAMED} more" if len(here) > CHATS_NAMED else ""
        count = "1 other chat works" if len(here) == 1 else f"{len(here)} other chats work"
        warning += f" {count} in this folder now: {named}{more}."
    in_folder = {
        "id": FOLDER,
        "label": f"This folder ({on})",
        "does": "The chat changes this folder's files on its current branch, as you have it open.",
        "start_args": [IN_PLACE_FLAG],
        "warning": warning,
    }
    return {
        "schema": SCHEMA,
        "folder": str(folder),
        "branch": current,
        "default": NEW_BRANCH,
        "places": [new_branch, branch, in_folder],
        "active": active,
    }


def branch_problem(project, branch: str, run_dir=None) -> str | None:
    """Why a chat cannot plan and build on the existing ``branch`` of ``project``; None when it can.

    ``run_dir`` is the chat asking, when it is already saved: its own choice of the branch is not another's."""
    project = Path(project).resolve()
    try:
        util.git(project, "check-ref-format", "--branch", branch, timeout=30)
        util.git(project, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", timeout=30)
    except (OSError, ValueError):
        return f"There is no local branch {branch} in {project}."
    tree = _checked_out(project).get(branch)
    if tree is not None:
        return f"The branch {branch} is checked out in {tree}; two chats never work on one branch."
    mine = str(Path(run_dir).resolve()) if run_dir is not None else None
    chat = next(
        (
            chat
            for chat in _active(project)
            if chat.get("moves_to_branch") == branch and str(Path(chat["run_dir"]).resolve()) != mine
        ),
        None,
    )
    if chat is not None:
        return f"The chat “{chat['title']}” plans and builds on {branch}; two chats never work on one branch."
    return None


def _branches(folder: Path) -> list[str]:
    """Local branches, most recently committed first, without Builder workers' batch branches."""
    try:
        listed = util.git(
            folder,
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname:short)",
            "refs/heads",
            timeout=30,
        )
    except (OSError, ValueError):
        return []
    names = [name for name in listed.splitlines() if name and not name.startswith(WORKER_BRANCH)]
    return names[:BRANCHES_SHOWN]


def _checked_out(folder: Path) -> dict[str, Path]:
    """Each branch checked out in a worktree of this repository, with that worktree's path."""
    try:
        listed = util.git(folder, "worktree", "list", "--porcelain", timeout=30)
    except (OSError, ValueError):
        return {}
    result: dict[str, Path] = {}
    tree = None
    for line in listed.splitlines():
        if line.startswith("worktree "):
            tree = Path(line[len("worktree ") :])
        elif line.startswith("branch refs/heads/") and tree is not None:
            try:
                result[line[len("branch refs/heads/") :]] = tree.resolve()
            except OSError:
                result[line[len("branch refs/heads/") :]] = tree
    return result


def _active(folder: Path) -> list[dict]:
    """The chats working in this folder or its task worktrees: not finished, not archived, not moved."""
    try:
        from . import autocode_registry as registry
        from . import autocode_run_finder as run_finder
    except ImportError:
        import autocode_registry as registry
        import autocode_run_finder as run_finder
    try:
        archived = registry.archived_run_dirs(registry.project_storage(folder))
    except (OSError, registry.RegistryError):
        archived = set()
    chats = []
    for checkout in (folder, *run_finder.task_worktrees(folder)):
        root = checkout / ".autocode" / "runs"
        try:
            children = sorted(root.iterdir()) if root.is_dir() and not root.is_symlink() else []
        except OSError:
            continue
        for run_dir in children:
            path = run_dir / "state.json"
            if run_dir.is_symlink() or path.is_symlink() or not path.is_file() or str(run_dir) in archived:
                continue
            try:
                loaded = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(loaded, dict) or loaded.get("status") in COMPLETE:
                continue
            state = cast("RunState", loaded)
            record = loaded.get(KEY)
            if isinstance(record, dict) and record.get("state") == "moved":
                continue  # its row is the run in its own worktree
            place = where(state, checkout) or {}
            chats.append(
                {
                    "title": _title(state.get("task")),
                    "run_dir": str(run_dir),
                    "path": str(checkout),
                    "kind": place.get("kind"),
                    "branch": place.get("branch"),
                    "moves_to_branch": place.get("moves_to_branch"),
                    "status": state.get("status"),
                }
            )
    return chats


def _title(task) -> str:
    line = (str(task or "").strip().splitlines() or ["Untitled chat"])[0].strip()
    return line if len(line) <= TITLE_LIMIT else line[: TITLE_LIMIT - 1].rstrip() + "…"


# ---- autocode worktrees ------------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="autocode worktrees",
        description="Where a new chat may work: a new worktree and branch, an existing branch, or this folder.",
    )
    result.add_argument("action", nargs="?", choices=("list",), default="list", help="What to do")
    result.add_argument("--workspace", type=Path, default=None, help="The folder the chat starts in (default: here)")
    result.add_argument("--json", action="store_true", help="Print the list as JSON, for a client")
    return result


def text(listing: dict) -> str:
    lines = [f"A new chat in {listing['folder']} can work in:"]
    for place in listing["places"]:
        mark = " (default)" if place["id"] == listing["default"] else ""
        lines.append(f"- {place['label']}{mark}: {place['does']}")
        if place.get("warning"):
            lines.append(f"  Warning: {place['warning']}")
        for row in place.get("branches") or []:
            lines.append(
                f"    {row['name']}" + (f" (not available: {row['unavailable']})" if row["unavailable"] else "")
            )
    return "\n".join(lines)


def cli(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    folder = args.workspace or Path.cwd()
    if not Path(folder).is_dir():
        print(f"autocode worktrees: {folder} is not a folder", file=sys.stderr)
        return 2
    listing = options(folder)
    print(json.dumps(listing, indent=2) if args.json else text(listing))
    return 0

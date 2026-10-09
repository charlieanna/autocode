"""What happens to AutoCode's Git worktrees once their work is done.

Three pieces, each safe to repeat after a crash:

``deliver``
    When a run in a task worktree (``autocode_workspaces.create``) completes, commit
    the delivered source to its task branch ``autocode/<task>``. The commit is built
    from the worktree's files with a private index and only the branch ref moves:
    the worktree is detached at the commit it started from, so its HEAD, and with it
    the snapshot revision the completion evidence is pinned to
    (``autocode_support.snapshot``), do not change.

``retire_builders``
    Once a parallel Builder batch is integrated into its run (its combined patch is
    applied and saved), remove each worker's source checkout and its
    ``autocode/builder-*`` branch. The worker's run records (``.autocode/`` inside
    it) stay at the same paths, which the parent run's stages and batch history
    refer to. Failed, paused and superseded batches keep their worktrees for
    inspection.

``cli`` (``autocode clean-worktrees``)
    List the project's task worktrees and remove those whose runs are all complete
    and whose branch holds all their source. Run records are archived to
    ``.autocode/archive/<worktree>/`` first and the branch is kept for merging.
    Nothing is removed without ``--yes``. Program and component worktrees belong to
    their own commands and are never touched.

Lower layer only: Git, files and ``autocode_workspaces``; never ``autocode``.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    from . import autocode_workspaces as workspaces
except ImportError:
    import autocode_workspaces as workspaces

IDENTITY = {"GIT_AUTHOR_NAME": "AutoCode", "GIT_AUTHOR_EMAIL": "autocode@localhost",
            "GIT_COMMITTER_NAME": "AutoCode", "GIT_COMMITTER_EMAIL": "autocode@localhost"}
# The same exclusions autocode_support.snapshot applies: runner state and bytecode are not source.
SOURCE = ["--", ".", ":(exclude).autocode", ":(exclude).autocode-ui",
          ":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc"]
COMPLETE = "TASK_COMPLETE"


def _git(root, *args, env=None, check=True):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                            env={**os.environ, **(env or {})})
    if check and result.returncode:
        raise ValueError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def source_tree(worktree) -> str:
    """The Git tree of the worktree's current source, built without touching its index."""
    with tempfile.TemporaryDirectory() as scratch:
        env = {"GIT_INDEX_FILE": str(Path(scratch) / "index")}
        _git(worktree, "read-tree", "HEAD", env=env)
        _git(worktree, "add", "-A", *SOURCE, env=env)
        return _git(worktree, "write-tree", env=env)


def _task_branch(workspace, *, legacy=False):
    """The branch of a task worktree AutoCode created for one task, or None.

    ``legacy`` also accepts worktrees made before task worktrees recorded their kind;
    only clean-up does, and it checks program ownership separately.
    """
    try:
        data = workspaces.metadata(workspace)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return data["branch"] if data and (data.get("kind") == "task" or legacy and "kind" not in data) else None


def deliver(state, workspace) -> str:
    """Commit a completed task worktree's source to its branch; the line to print, or ''."""
    branch = _task_branch(workspace) if state.get("status") == COMPLETE else None
    if not branch:
        return ""
    try:
        tip = _git(workspace, "rev-parse", "--verify", f"refs/heads/{branch}")
        tree = source_tree(workspace)
        if tree == _git(workspace, "rev-parse", f"{tip}^{{tree}}"):
            return f"\nDelivered on branch {branch} ({tip[:12]})."
        # Detach first, so moving the branch leaves this worktree's HEAD where it is.
        if _git(workspace, "symbolic-ref", "-q", "HEAD", check=False) == f"refs/heads/{branch}":
            _git(workspace, "checkout", "-q", "--detach")
        title = (state.get("task") or "AutoCode task").strip().splitlines()[0][:72]
        contract = state.get("goal_contract") or {}
        message = (f"{title}\n\nDelivered by AutoCode run {Path(state.get('run_dir', '')).name or 'unknown'}"
                   f" (contract revision {contract.get('revision', '?')}).\n")
        commit = subprocess.run(["git", "-C", str(workspace), "commit-tree", tree, "-p", tip],
                                input=message, capture_output=True, text=True, check=True,
                                env={**os.environ, **IDENTITY}).stdout.strip()
        _git(workspace, "update-ref", f"refs/heads/{branch}", commit, tip)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        return f"\nThe delivered source was not committed to {branch}: {error}"
    return (f"\nDelivered on branch {branch} ({commit[:12]}). Merge it, then remove this worktree "
            f"with `autocode clean-worktrees --workspace {state.get('project_workspace', '<project>')} --yes`.")


def remove_checkout(repo, tree) -> None:
    """Remove a worktree's checkout and registration, keeping its ``.autocode/`` records in place.

    The records are moved aside while Git removes the directory and moved back after,
    so every saved path into them still resolves. A crash in between leaves them in
    the sibling ``.<name>-records`` directory, which the next call puts back.
    """
    tree = Path(tree)
    aside = tree.parent / f".{tree.name}-records"
    if (tree / ".git").exists():
        if (tree / ".autocode").is_dir() and not aside.exists():
            os.replace(tree / ".autocode", aside)
        _git(repo, "worktree", "remove", "--force", str(tree))
    if aside.is_dir() and not (tree / ".autocode").exists():
        tree.mkdir(parents=True, exist_ok=True)
        os.replace(aside, tree / ".autocode")


def retire_builders(batch, workspace) -> None:
    """Remove an integrated batch's worker checkouts and branches; their run records stay."""
    for row in batch["workers"]:
        remove_checkout(workspace, row["workspace"])
        if _git(workspace, "branch", "--list", row["branch"]):
            _git(workspace, "branch", "-D", row["branch"])
        row["worktree_removed"] = True
    _git(workspace, "worktree", "prune", check=False)


@contextlib.contextmanager
def _locked(paths):
    """Hold every runner lock under ``paths`` (non-blocking), or raise BlockingIOError."""
    handles = []
    try:
        for path in paths:
            handle = path.open("a+")
            handles.append(handle)
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        for handle in handles:
            handle.close()


def _registered(project):
    """Registered worktrees of ``project``'s repository: {path: branch or None}."""
    found, path = {}, None
    for line in _git(project, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):]).resolve()
            found[path] = None
        elif line.startswith("branch refs/heads/") and path:
            found[path] = line[len("branch refs/heads/"):]
    return found


def _program_owned(project):
    """Worktree paths any program under ``project`` recorded (autocode_program owns those)."""
    owned = set()
    for state in (project / ".autocode" / "programs").glob("*/state.json"):
        try:
            owned.update(Path(text) for text in _strings(json.loads(state.read_text())))
        except (OSError, ValueError):
            continue
    return {path.resolve() for path in owned if path.is_absolute()}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def assess(project, tree, branch, registered):
    """(removable, reason) for one task worktree."""
    runs = sorted((tree / ".autocode" / "runs").glob("*/state.json"))
    if not runs:
        return False, "no AutoCode run here"
    for path in runs:
        try:
            status = json.loads(path.read_text()).get("status")
        except (OSError, ValueError):
            return False, f"{path.parent.name}: unreadable state"
        if status != COMPLETE:
            return False, f"{path.parent.name} is {status}; finish or abandon it first"
    if not branch:
        return False, "no branch; commit its work to a branch first"
    try:
        if source_tree(tree) != _git(project, "rev-parse", f"refs/heads/{branch}^{{tree}}"):
            return False, f"source differs from {branch}; commit it to the branch first"
    except ValueError as error:
        return False, str(error)
    nested = [p for p in registered if p != tree and p.is_relative_to(tree)]
    return True, f"complete; {branch} holds its source" + (f"; {len(nested)} Builder worktree(s) inside" if nested else "")


def remove(project, tree, registered):
    """Archive the run records, then remove the worktree and any worktrees nested in it."""
    locks = [tree / ".autocode" / "writer.lock", *(p.parent / "writer.lock"
                                                    for p in (tree / ".autocode" / "runs").glob("*/state.json"))]
    with _locked(locks):
        # Builder worktrees nested in it (from runs before retire_builders existed) go first,
        # so the archive holds their records but no Git checkout.
        for nested, branch in registered.items():
            if nested != tree and nested.is_relative_to(tree):
                remove_checkout(project, nested)
                if branch and branch.startswith("autocode/builder-"):
                    _git(project, "branch", "-D", branch, check=False)
        archive = workspaces.keep_out_of_git(project) / "archive" / tree.name
        shutil.copytree(tree / ".autocode", archive, symlinks=True, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("writer.lock"))
        # --force: the worktree is detached with the delivered source uncommitted relative to
        # HEAD; assess() has just shown the branch holds exactly that source.
        _git(project, "worktree", "remove", "--force", str(tree))
    _git(project, "worktree", "prune", check=False)
    return archive


def cli(argv) -> int:
    parser = argparse.ArgumentParser(
        prog="autocode clean-worktrees",
        description="Remove task worktrees whose runs are complete and whose branch holds their work. "
                    "Lists what it would do unless --yes is given; branches are kept for merging.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="The project (or one of its task worktrees)")
    parser.add_argument("--yes", action="store_true", help="Remove the worktrees listed as removable")
    args = parser.parse_args(argv)
    try:
        project = Path(_git(args.workspace, "rev-parse", "--show-toplevel")).resolve()
        data = workspaces.metadata(project)
        if data:
            project = Path(data["project_workspace"]).resolve()
        registered = _registered(project)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    parent = (project / ".autocode" / "worktrees").resolve()
    programs = _program_owned(project)
    trees = sorted(p for p in registered if p.parent == parent)
    if not trees:
        print(f"No task worktrees under {parent}.")
        return 0
    failed = False
    for tree in trees:
        if tree in programs:
            print(f"keep    {tree}: owned by an `autocode program`")
            continue
        # A delivered worktree is detached, so its branch comes from its own metadata.
        branch = _task_branch(tree, legacy=True) or registered[tree]
        removable, reason = assess(project, tree, branch, registered)
        if not removable:
            print(f"keep    {tree}: {reason}")
        elif not args.yes:
            print(f"remove  {tree}: {reason} (dry run; add --yes)")
        else:
            try:
                archive = remove(project, tree, registered)
                print(f"removed {tree}: branch {branch} kept; run records in {archive}")
            except BlockingIOError:
                print(f"keep    {tree}: a runner holds its lock")
            except (OSError, ValueError) as error:
                failed = True
                print(f"error   {tree}: {error}")
    return 1 if failed else 0

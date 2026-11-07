"""`autocode issue plan`: the plan a run drafted for an issue, as a pull request carrying its plan file (#720).

On a team, what will be built is worth reviewing before any model builds it. The issue's run stops with its
plan waiting for approval; this exports that plan through the task-run interface (``TaskRun.export_plan``,
which runs `autocode plan --export`) and commits it as one file on a branch of its own, made from the commit
the issue's run started from. The commit is built with a temporary index, so the issue's worktree, its run
and the person's checkout are untouched. Reviewers edit the file in the pull request; once it is merged,
whoever holds the run loads it with `autocode --goal-file` and approves it there as usual
(docs/workflow.md#a-teams-plan-review). Nothing here approves, loads or merges a plan.

Run again after the run's plan changed while the pull request is open, it commits the new plan on top of the
branch's last commit, so the same pull request shows the change; an unchanged plan reuses that commit. A branch
that moved since is refused; one deleted since holds nothing to replace, so it is made again at that commit. A
merged pull request is never reused: once the base branch holds its file (``merged_into``), a changed plan starts
a new pull request from that base, on the next branch (``-plan-2``).

Imports only autocode_plan_file (pure) and the standard library.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

try:
    from . import autocode_plan_file as plan_file
except ImportError:
    import autocode_plan_file as plan_file

FIELD = "plan_pr"  # the issue record's entry for its plan pull request
NOT_IN_REPOSITORY = (".git", ".autocode")  # Git's own directory, and the run data autocode issue never commits


def default_path(number: int) -> str:
    return f"plans/issue-{number}.md"


def branch(record: dict, round_: int = 1) -> str:
    """The plan's branch: the issue's own branch name and ``-plan``, so the two never collide.

    ``round_`` counts the issue's plan pull requests: the second, opened after the first was merged, is on
    ``-plan-2``, and so on, so a new one never lands on a merged one's branch.
    """
    name = f"{record['branch']}-plan"
    return name if round_ == 1 else f"{name}-{round_}"


def title(record: dict) -> str:
    return f"Plan: {record['title']} (#{record['number']})"


def repository_path(value: str) -> str:
    """``value`` as a path from the repository's top level; ValueError when it cannot be one."""
    path = PurePosixPath(value)
    if not value.strip() or path.is_absolute() or not path.parts or ".." in path.parts or "\\" in value:
        raise ValueError(f"--path {value!r} must be a relative path inside the repository, such as plans/issue-7.md")
    if path.parts[0] in NOT_IN_REPOSITORY:
        raise ValueError(f"--path {value!r} is under {path.parts[0]}/, which is never committed")
    return str(path)


def prepare(project: Path, record: dict, run, path: str, start: str | None = None) -> dict:
    """Export ``run``'s plan and commit it at ``path`` on the plan branch; the record's new ``plan_pr`` entry.

    ``run`` is the issue's TaskRun. ``start``, the commit of the pull request's base that holds the merged plan
    pull request (``merged_into``), begins a new plan pull request from it, on the next branch and with no URL.
    A branch deleted since the last commit made here (``git branch -D``, ``gh pr merge --delete-branch``) is made
    again, at that commit or the new one on top of it. ValueError, with nothing changed, when the branch moved
    since that commit, when the record already carries the plan at another path, or when the base commit already
    holds this exact plan at ``path``.
    """
    saved = record.get(FIELD) or {}
    if saved and saved["path"] != path:
        raise ValueError(f"this issue's plan pull request carries {saved['path']}; pass --path {saved['path']}")
    if start:
        round_, parent, previous, url = saved.get("round", 1) + 1, start, None, None
    else:
        round_, parent = saved.get("round", 1), saved.get("parent") or record["base_commit"]
        previous, url = saved.get("commit"), saved.get("url")
    name = branch(record, round_)
    current = _object(project, f"refs/heads/{name}")
    if previous and current and current != previous:
        raise ValueError(
            f"branch {name} no longer points at {previous[:12]}, the plan commit autocode issue made; "
            "it never replaces commits it did not make"
        )
    with tempfile.TemporaryDirectory(prefix="autocode-issue-plan-") as temporary:
        exported = Path(temporary) / PurePosixPath(path).name
        run.export_plan(exported)
        with exported.open(encoding="utf-8", newline="") as stream:
            plan = plan_file.read(stream.read())
        ref = f"{record['owner']}/{record['repo']}#{record['number']}"
        message = (
            f"Plan for {ref}: {record['title']}\n\nPlan revision {plan.revision} of AutoCode run "
            f"{Path(record['run_dir']).name}, exported for review before it is built (goal hash {plan.goal_hash})."
        )
        made = commit(project, previous or parent, path, exported, message)
    tip = made or previous
    if tip is None:
        raise ValueError(f"{path} at the base commit already holds this plan; there is nothing to propose")
    if previous is None or not current:
        # A new branch, or one deleted since: nothing on it can be replaced. `git branch` refuses one that exists.
        _git(project, "branch", "--no-track", name, tip)
    elif made is not None:
        _git(project, "update-ref", f"refs/heads/{name}", made, previous)
    return {
        "path": path,
        "branch": name,
        "round": round_,
        "parent": parent,  # the base commit the branch started from: what merged_into compares the base with
        "commit": tip,
        "revision": plan.revision,
        "goal_hash": plan.goal_hash,
        "run_goal_hash": plan.exported_hash,
        "url": url,
    }


def merged_into(project: Path, record: dict, bases) -> str | None:
    """The commit of the first of ``bases`` (refs) holding the record's plan pull request merged; None otherwise.

    A base holds it once its file at the pull request's path is no longer the one the plan branch started
    from, whatever the merge method (fast-forward, merge commit, squash or rebase). A missing ref holds nothing.
    """
    entry = record.get(FIELD)
    if not entry:
        return None
    started = _object(project, f"{entry.get('parent') or record['base_commit']}:{entry['path']}")
    for ref in bases:
        tip = _object(project, f"{ref}^{{commit}}")
        if tip and _object(project, f"{tip}:{entry['path']}") != started:
            return tip
    return None


def carried(entry: dict, token: str) -> bool:
    """Whether the plan waiting for approval under ``token`` (``r<revision>:<hash>``) is the revision ``entry`` carries."""
    return token.startswith(f"r{entry['revision']}:")


def commit(project: Path, parent: str, path: str, source: Path, message: str) -> str | None:
    """A commit of ``parent`` with ``source``'s bytes at ``path``; None when ``parent`` already holds them.

    The blob is stored as `git add` would store it, and the tree is built in a temporary index, so no
    worktree or index of the repository changes.
    """
    blob = _git(project, "hash-object", "-w", f"--path={path}", "--", str(source))
    with tempfile.TemporaryDirectory(prefix="autocode-issue-index-") as temporary:
        environment = {**os.environ, "GIT_INDEX_FILE": str(Path(temporary) / "index")}
        _git(project, "read-tree", parent, env=environment)
        _git(project, "update-index", "--add", "--cacheinfo", "100644", blob, path, env=environment)
        tree = _git(project, "write-tree", env=environment)
    if tree == _git(project, "rev-parse", f"{parent}^{{tree}}"):
        return None
    return _git(project, "commit-tree", tree, "-p", parent, "-m", message)


def body(record: dict, entry: dict) -> str:
    """The plan pull request's description: what the file is, how to review it, and what merging does not do."""
    ref = f"{record['owner']}/{record['repo']}#{record['number']}"
    path, goal = entry["path"], entry["goal_hash"]
    lines = [
        f"The plan AutoCode drafted for {ref}, for review before anything is built.",
        "",
        f"`{path}` is plan revision {entry['revision']} of AutoCode run `{Path(record['run_dir']).name}`, "
        f"waiting for approval. Its goal hash is `{goal}`, what `autocode plan --check {path}` prints.",
    ]
    if entry["run_goal_hash"] != goal:
        lines += [
            "",
            "Text that looked like a credential is masked as `[REDACTED]` in this file, so its goal hash differs "
            f"from the run's plan (`{entry['run_goal_hash']}`). The run refuses the file while it holds the mask: "
            "restore those values in the front matter, or reword that text, before it is loaded.",
        ]
    return (
        "\n".join(
            lines
            + [
                "",
                "## Reviewing it",
                "",
                "The JSON front matter between the two `---` lines is the plan; the text under it is generated from "
                f"it. To change the plan, edit the front matter, run `autocode plan --render {path}` and commit the "
                f"regenerated file. `autocode plan --check {path}` prints the new goal hash, and exits 2 while the "
                "text and the front matter disagree.",
                "",
                "Merging this pull request approves nothing. Once it is merged, whoever holds the run loads the "
                f"merged file into it with `autocode --goal-file {path}`, which checks it as it checks a planner's "
                "draft, and approves the plan there as usual. A new run cannot be started from the file yet.",
            ]
        )
        + "\n"
    )


def _object(root: Path, name: str) -> str:
    """The object ``name`` names, such as ``REF:PATH``; empty when there is none."""
    return _git(root, "rev-parse", "-q", "--verify", name, check=False)


def _git(root: Path, *args: str, check: bool = True, env: dict | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env
    )
    if check and result.returncode:
        raise ValueError(f"git {' '.join(args[:2])}: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()

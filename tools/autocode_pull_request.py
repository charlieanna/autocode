"""Open a pull request for a finished run's delivered branch, with no model call (owner request 2026-11-28).

A run in a worktree of its own commits its checked source to its branch when it completes
(``autocode_worktrees.deliver``). Putting that branch up for review needs no plan, no Builder and no
review, so nothing here launches a stage. ``open_pull_request`` pushes the branch to the project's
GitHub remote and opens a draft pull request whose body is the run's canonical evidence report; the
caller has already read that report and the run's status view through the public task-run interface
(``autocode_pull_request_cli``, ``autocode pr``).

It refuses, changing nothing on GitHub, unless:

* the status view says the run is done and it worked in a worktree of its own (``worktree.kind``);
* the evidence report is the run's own and the workspace source is exactly what its independent validation
  checked (``checked``): a current report, or a recorded one after a later turn that built nothing;
* the branch holds exactly the worktree's source now (``source_tree``): the bytes pushed are the bytes
  the completion checked, not an edit made after it;
* the branch differs from the commit the run started from, so there is something to propose.

Two ways in, one step: the status view's ``pull_request.next_command`` (a client's "Open pull
request"), and a follow-up message Requirements reads as a request to publish the finished work
(``autocode_publish_turn``), after which the runner calls ``deliver_requested`` once the turn is saved.

The run directory's ``pull-request.json`` records the pull request this run opened, so pressing
again pushes the branch's newer commits to the same pull request rather than opening another.

Lower layer only: Git, files, ``autocode_github``, ``autocode_workspaces`` and ``autocode_worktrees``;
never ``autocode`` or ``autocode_taskrun``.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_github as github
    from . import autocode_util as util
    from . import autocode_workspaces as workspaces
    from . import autocode_worktrees as worktrees
except ImportError:
    import autocode_github as github
    import autocode_util as util
    import autocode_workspaces as workspaces
    import autocode_worktrees as worktrees

RECORD = "pull-request.json"
MAX_BODY = 60_000  # GitHub rejects bodies over 65,536 characters (as autocode_issue.MAX_PR_BODY)
REMOTE = "origin"
TIMEOUT = 300  # seconds for one `autocode pr` launched after a publish turn: a push and two API calls


class PullRequestError(RuntimeError):
    """Why no pull request was opened; nothing was pushed or opened when it is raised before ``push``."""


def _git(root, *args, check=True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=util.git_environment(),
    )
    if check and result.returncode:
        raise PullRequestError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def record(run_dir) -> dict | None:
    """The pull request this run opened, as ``open_pull_request`` saved it, or None."""
    path = Path(run_dir) / RECORD
    try:
        value = json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else None
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("url"), str) else None


def repository(project, remote: str = REMOTE) -> tuple[str, str] | None:
    """OWNER, REPO of the project's ``remote``, or None when it has none GitHub can name."""
    url = _git(project, "remote", "get-url", remote, check=False)
    return github.repo_from_remote(url) if url else None


def offer(*, done: bool, place, workspace, run_dir) -> dict | None:
    """The status view's ``pull_request``: the action that opens (or updates) this run's pull request.

    None unless the run is done, worked in a worktree and branch of its own, and its project has a
    remote GitHub can name; the command itself checks the rest (current evidence, the branch's bytes)."""
    if not done or not isinstance(place, dict) or place.get("kind") != "worktree" or run_dir is None:
        return None
    project, branch = place.get("folder"), place.get("branch")
    if not isinstance(project, str) or not isinstance(branch, str) or not branch:
        return None
    try:
        named = repository(project)
    except (OSError, PullRequestError):
        named = None
    if not named:
        return None
    opened = record(run_dir)
    return {
        "branch": branch,
        "repository": "/".join(named),
        "url": opened["url"] if opened else None,
        "label": "Update pull request" if opened else "Open pull request",
        "next_command": [
            "pr",
            "--workspace",
            str(workspace),
            "--run-dir",
            str(run_dir),
            "--open",
            "--json",
        ],
    }


def _base(client, project, owner: str, repo: str, explicit: str | None) -> str:
    """The branch the pull request targets: ``explicit``, else the repository's default branch."""
    if explicit:
        return explicit
    try:
        found = client.repository(owner, repo).get("default_branch")
    except github.GitHubError as error:
        raise PullRequestError(f"cannot read {owner}/{repo} on GitHub: {error}") from error
    if not isinstance(found, str) or not found:
        raise PullRequestError(f"{owner}/{repo} names no default branch; pass --base BRANCH")
    return found


def checked(view: dict, report: dict) -> None:
    """Refuse unless the evidence report is the run's own and the source is exactly what its validation checked.

    A ``current`` report already says both. A follow-up turn that changed nothing (a question, or the
    publish turn itself) leaves the report ``recorded``: its files are the authentic ones bound to the
    run, but the finished build's completion gate is not re-run for a turn that built nothing. It counts
    when the status view's own inspection finds the workspace source unchanged since the independent
    validation (``verification.freshness`` current, at the revision the report names). Anything else
    (stale, invalid, missing, or source changed since) is refused."""
    availability = report.get("availability")
    if availability == "current":
        return
    found = view.get("verification") or {}
    revision = ((report.get("document") or {}).get("revision") or {}).get("source_revision")
    if (
        availability == "recorded"
        and found.get("freshness") == "current"
        and revision
        and found.get("source_revision") == revision
        and found.get("inspected_source_revision") == revision
    ):
        return
    reasons = "; ".join(report.get("reasons") or []) or f"its evidence report is {availability or 'missing'}"
    raise PullRequestError(
        f"the run's source is no longer what its completion checked ({reasons}); continue the run so it "
        "checks the current source first"
    )


def _title(report: dict, subject: str, run_dir) -> str:
    """The plan's intended outcome, else the delivery commit's subject, else the run's name."""
    outcome = (((report.get("document") or {}).get("plan") or {}).get("intended_outcome") or "").strip()
    first = (outcome or subject or "").strip().splitlines()
    title = first[0].strip().rstrip(".") if first else ""
    if len(title) > 120:
        title = title[:117].rsplit(" ", 1)[0] + "..."
    return title or f"AutoCode run {Path(run_dir).name}"


def body(markdown: str, diffstat: str, run_dir, base_commit: str) -> str:
    """The pull request's body: the exact canonical evidence report, then what changed."""
    if not isinstance(markdown, str) or not markdown:
        raise PullRequestError("the run's evidence report is missing; complete or revalidate the run first")
    text = (
        markdown.rstrip("\n")
        + "\n\n## Changes\n\n<pre>"
        + html.escape(diffstat.strip() or "(none)")
        + "</pre>\n\n"
        + f"Opened by AutoCode from run `{Path(run_dir).name}`, base commit `{base_commit[:12]}`. "
        "No model ran to open it, and nothing has been merged.\n"
    )
    if util.redact(text) != text:
        raise PullRequestError("the evidence report holds text that is unsafe to publish")
    if len(text) > MAX_BODY:
        raise PullRequestError("the evidence report exceeds GitHub's pull-request body limit")
    return text


def open_pull_request(
    view: dict,
    report: dict,
    *,
    workspace,
    run_dir,
    remote: str = REMOTE,
    base: str | None = None,
    ready: bool = False,
    push: bool = True,
    client=None,
) -> dict:
    """Push the run's delivered branch and open (or update) its draft pull request.

    ``view`` is the run's ``--status --inspect-evidence`` view and ``report`` its current evidence
    report, both read by the caller through TaskRun. Returns {"url", "number", "branch", "base",
    "repository", "commit", "opened"}; ``opened`` is False when the run's pull request already existed
    and the push only updated it. With ``push=False`` it checks everything and returns the would-be
    title and body without pushing (``url`` None)."""
    if not view.get("done"):
        raise PullRequestError(
            f"the run has not completed (status {view.get('status')}); a pull request needs its completion gate to pass"
        )
    checked(view, report)
    place = view.get("worktree") or {}
    if place.get("kind") != "worktree":
        raise PullRequestError(
            "this run changed files in your folder rather than on a branch of its own, so there is no run branch "
            "to propose; commit and push your branch yourself"
        )
    workspace = Path(workspace).resolve()
    try:
        meta = workspaces.metadata(workspace)
    except (OSError, ValueError) as error:
        raise PullRequestError(f"cannot read the run's worktree: {error}") from error
    if not meta or meta.get("kind") != "task" or meta.get("branch") != place.get("branch"):
        raise PullRequestError("the run's worktree does not match the branch its status names")
    project, branch, base_commit = Path(meta["project_workspace"]), meta["branch"], meta["base_commit"]
    tip = _git(project, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")
    if tip == base_commit:
        raise PullRequestError("the run's branch holds no commit of its own yet; there is nothing to propose")
    if _git(project, "rev-parse", f"{tip}^{{tree}}") != worktrees.source_tree(workspace):
        raise PullRequestError(
            f"{branch} does not hold the run's worktree source as it is now; the worktree changed after the run "
            "completed. Continue the run so it checks and delivers that change first"
        )
    named = repository(project, remote)
    if not named:
        raise PullRequestError(f"the project has no GitHub remote named {remote}")
    owner, repo = named
    title = _title(report, _git(project, "log", "-1", "--format=%s", tip), run_dir)
    diffstat = _git(project, "diff", "--stat", base_commit, tip)
    text = body(report.get("markdown"), diffstat, run_dir, base_commit)
    result = {
        "branch": branch,
        "repository": f"{owner}/{repo}",
        "commit": tip,
        "title": title,
        "url": None,
        "number": None,
        "base": base,
        "opened": False,
    }
    if not push:
        return {**result, "body": text}
    client = client or github.signed_in()
    existing = record(run_dir)
    target = (existing or {}).get("base") or _base(client, project, owner, repo, base)
    # A normal push: it cannot overwrite a reviewer's commits on the remote branch.
    _git(project, "push", "-q", remote, f"{tip}:refs/heads/{branch}")
    if existing:
        return {**result, **{key: existing.get(key) for key in ("url", "number")}, "base": target}
    try:
        found = client.pulls_for_branch(owner, repo, head=branch, base=target)
        open_ones = [row for row in found if row.get("state") == "open"]
        pull = (
            open_ones[0]
            if open_ones
            else client.open_pull_request(
                owner, repo, head=branch, base=target, title=title, body=text, draft=not ready
            )
        )
    except github.GitHubError as error:
        raise PullRequestError(f"pushed {branch}, but GitHub refused the pull request: {error}") from error
    if not isinstance(pull.get("html_url"), str):
        raise PullRequestError(f"pushed {branch}, but GitHub returned no pull request address")
    saved = {
        "url": pull["html_url"],
        "number": pull.get("number"),
        "branch": branch,
        "base": target,
        "repository": f"{owner}/{repo}",
        "commit": tip,
        "opened_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    util.atomic_json(Path(run_dir) / RECORD, saved)
    return {**result, "url": saved["url"], "number": saved["number"], "base": target, "opened": not open_ones}


# ---------------------------------------------------------------- a publish turn (autocode_publish_turn)


def deliver_requested(state: RunState, workspace, run_dir, save) -> str:
    """Open the pull request a publish turn asked for, once that turn is complete and saved; the line to print.

    Runs ``autocode pr`` as its own process, which reads this run only through ``--status``, the same
    way a client's press does: the turn's state is saved first (``save``), and the outcome is kept in
    ``pull_request_request`` so the chat shows it and a repeated completion does not open it twice."""
    wanted = state.get("pull_request_request")
    turns = state.get("turns") or []
    if (
        state.get("status") != "TASK_COMPLETE"
        or not isinstance(wanted, dict)
        or wanted.get("outcome")
        # Only the turn that asked: a later turn's completion never opens one it did not ask for.
        or not turns
        or wanted.get("event_id") != turns[-1].get("event_id")
    ):
        return ""
    save(state)
    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "autocode.py"),
        "pr",
        "--workspace",
        str(workspace),
        "--run-dir",
        str(run_dir),
        "--open",
        "--json",
    ]
    try:
        done = subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=TIMEOUT)
        answer = json.loads(done.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        done, answer = None, {"error": f"autocode pr did not finish: {type(error).__name__}"}
    if done is not None and done.returncode == 0 and isinstance(answer.get("url"), str):
        wanted["outcome"] = {"kind": "opened", "url": answer["url"], "branch": answer.get("branch")}
        line = f"\nOpened pull request {answer['url']} for branch {answer.get('branch')}."
    else:
        reason = str(answer.get("error") or (done.stderr.strip() if done is not None else "") or "unknown reason")
        wanted["outcome"] = {"kind": "refused", "reason": reason[:2000]}
        line = f"\nNo pull request was opened: {wanted['outcome']['reason']}"
    wanted["finished_at"] = dt.datetime.now(dt.UTC).isoformat()
    save(state)
    return line

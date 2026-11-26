"""Many GitHub issues on one project: list them, and start them a few at a time.

    autocode issue list [--label L] [--json]            the repository's open issues, each with its run
    autocode issue queue add ISSUE... [--max-active K] [-- OPTS]
                                                         queue issues to start; OPTS as for `issue start`
    autocode issue queue run                            start queued issues while fewer than K work
    autocode issue queue status [--json]                the queue, and the command that resumes it
    autocode issue queue remove ISSUE...                take issues that have not started off the queue

`queue run` is the one scheduler for a project. It starts each queued issue with `autocode issue start
ISSUE --no-chat`, so every issue gets its own branch, worktree and run exactly as a single start does,
and it starts the next one only while fewer than K of the project's issue runs are working
(`autocode runs`' own "working" bucket). A run waiting for a person uses no model, so it does not hold a
slot: ten issues can all be waiting for their plans to be approved while at most K use models. A
person's answer or approval continues its own run whatever the limit; the queue gates only new starts.
It exits once every queued issue has started. A queue whose scheduler stopped (VS Code closed, the
terminal ended) names `queue run` as its `next_command`, so any client can resume it.

A layer over the task-run interface (docs/task-run.md): it starts runs only through `autocode issue
start`, counts them only through `autocode runs --json`, and never reads a run's saved state. It decides
nothing that belongs to a person: each issue's Requirements, plan approval and pull request stay theirs.
See docs/issues.md.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
from pathlib import Path

try:
    from . import autocode_github as github
    from . import autocode_issue_admission as admission
    from . import autocode_util as util
    from .autocode_taskrun import AUTOCODE
except ImportError:
    import autocode_github as github
    import autocode_issue_admission as admission
    import autocode_util as util
    from autocode_taskrun import AUTOCODE

SCHEMA = 1
STORE = Path(".autocode") / "issues"  # autocode_issue.STORE: each issue's record and worktree
QUEUE = Path(".autocode") / "issue-queue.json"
LOCK = Path(".autocode") / "issue-queue.lock"
DEFAULT_MAX_ACTIVE = 3
POLL_SECONDS = 10.0
COMPLETE = ("TASK_COMPLETE", "COMPLETE")  # autocode_run_view.COMPLETE, as `autocode runs` shows a row's status
# What an entry of the queue is doing. A started entry is the issue's own record from then on.
QUEUED, STARTING, STARTED, FAILED = "queued", "starting", "started", "failed"
MAX_ERROR = 2000  # characters of a failed start's output kept with its entry


class QueueError(RuntimeError):
    """A refusal with a message for the person."""


def record_path(project: Path, ref: github.IssueRef) -> Path:
    """Where `autocode issue start` keeps the issue's record (autocode_issue.record_path)."""
    return project / STORE / f"{ref.slug}.json"


# ---------------------------------------------------------------- the queue file


def _empty() -> dict:
    return {"version": 1, "max_active": DEFAULT_MAX_ACTIVE, "options": [], "entries": []}


def load(project: Path) -> dict:
    path = project / QUEUE
    if not path.is_file():
        return _empty()
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise QueueError(f"cannot read {path}: {error}") from error
    if not isinstance(saved, dict) or saved.get("version") != 1 or not isinstance(saved.get("entries"), list):
        raise QueueError(f"{path} is not an issue queue this AutoCode reads")
    return {**_empty(), **saved}


def save(project: Path, queue: dict) -> None:
    util.atomic_json(project / QUEUE, queue)


@contextlib.contextmanager
def editing(project: Path):
    """Read, change and write the queue under its store lock, so the scheduler and an `add` lose nothing."""
    with util.store_lock(project / QUEUE):
        queue = load(project)
        yield queue
        save(project, queue)


def runner_active(project: Path) -> bool:
    """Whether a `queue run` holds this project's scheduler lock now."""
    path = project / LOCK
    if not path.exists():
        return False
    with path.open("a+") as handle:
        try:
            util.lock_file(handle)
        except BlockingIOError:
            return True
        util.unlock_file(handle)
        return False


# ---------------------------------------------------------------- what is working


def runs(project: Path) -> list[dict]:
    """This project's runs, its issue worktrees' included, as `autocode runs --json` lists them."""
    reply = subprocess.run(
        [*AUTOCODE, "runs", "--workspace", str(project), "--json"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    try:
        return list(json.loads(reply.stdout)["runs"])
    except (ValueError, KeyError, TypeError) as error:
        raise QueueError(f"autocode runs did not list the project's runs: {reply.stderr.strip()}") from error


def issue_worktree(project: Path, row: dict) -> str | None:
    """The issue worktree a run row belongs to, or None for any other run of the project."""
    try:
        workspace = Path(row["workspace"]).resolve()
    except (KeyError, TypeError):
        return None
    return str(workspace) if workspace.parent == (project / STORE).resolve() else None


def working(project: Path, rows: list[dict], starting=()) -> int:
    """How many issue runs use models now: the working rows, and the starts that have not saved a run yet."""
    busy = {str((project / STORE / slug).resolve()) for slug in starting}
    busy.update(
        worktree for row in rows if row.get("bucket") == "working" and (worktree := issue_worktree(project, row))
    )
    return len(busy)


# ---------------------------------------------------------------- views for people and clients


def _autocode(*words: str) -> list[str]:
    """A command for a client, as the other client views name them: the arguments after `autocode`."""
    return ["issue", *words]


def _record_for(project: Path, ref: github.IssueRef) -> dict | None:
    path = record_path(project, ref)
    try:
        return json.loads(path.read_text()) if path.is_file() else None
    except (OSError, ValueError):
        return None


def _rows_by_dir(rows: list[dict]) -> dict:
    """Each run row by its run directory, and each issue worktree's newest run by the worktree."""
    found = {}
    for row in sorted((row for row in rows if row.get("run_dir")), key=lambda row: row["run_dir"]):
        found[str(Path(row["run_dir"]).resolve())] = row
        if row.get("workspace"):
            found[("worktree", str(Path(row["workspace"]).resolve()))] = row
    return found


def run_view(project: Path, ref: github.IssueRef, record: dict, rows_by_dir: dict) -> dict:
    """An issue's run, as `autocode runs` sees it, with the commands a client may take for it.

    `issue start` saves the run directory in the record only once the run's first advance returns, so
    until then the run is found by the issue's own worktree, where it is the only run."""
    if record.get("run_dir"):
        row = rows_by_dir.get(str(Path(record["run_dir"]).resolve()))
    else:
        row = rows_by_dir.get(("worktree", str(Path(record["worktree"]).resolve()))) if record.get("worktree") else None
    actions = {}
    if row and row.get("status") in COMPLETE and not record.get("pr_url"):
        # `issue pr` itself refuses a run whose completion gate has not passed.
        actions["open_pr"] = {
            "label": "Open pull request",
            "next_command": _autocode("pr", str(ref), "--project", str(project), "--open"),
        }
    return {
        "ref": str(ref),
        "number": ref.number,
        "title": record.get("title"),
        "url": record.get("url"),
        "worktree": record.get("worktree"),
        "run_dir": record.get("run_dir") or (row or {}).get("run_dir"),
        "branch": record.get("branch"),
        "pr_url": record.get("pr_url"),
        "bucket": (row or {}).get("bucket"),
        "status": (row or {}).get("status"),
        "needs": (row or {}).get("needs"),
        "actions": actions,
    }


def started(project: Path, rows: list[dict]) -> list[dict]:
    """Every issue of the project that has a run, newest record first: no GitHub read."""
    store = project / STORE
    by_dir = _rows_by_dir(rows)
    views = []
    paths = sorted(store.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True) if store.is_dir() else []
    for path in paths:
        try:
            record = json.loads(path.read_text())
            ref = github.IssueRef(record["owner"], record["repo"], int(record["number"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue  # not an issue record
        views.append(run_view(project, ref, record, by_dir))
    return views


def queue_view(project: Path, queue: dict | None = None, rows: list[dict] | None = None) -> dict:
    """The queue as a client shows it, with the one command that resumes a stopped scheduler.

    With ``rows`` (`autocode runs --json`) it also counts the working issue runs and lists every issue
    run of the project (``issues``), each with its named actions."""
    queue = load(project) if queue is None else queue
    active = runner_active(project)
    waiting = [entry for entry in queue["entries"] if entry.get("state") in (QUEUED, STARTING)]
    return {
        "max_active": queue["max_active"],
        "runner_active": active,
        "working": None if rows is None else working(project, rows),
        "entries": [
            {key: entry.get(key) for key in ("ref", "number", "title", "state", "error", "added_at")}
            for entry in queue["entries"]
        ],
        "issues": None if rows is None else started(project, rows),
        # Nothing starts a queued issue but the scheduler: when none is running, its command is the next step.
        "next_command": _autocode("queue", "run", "--project", str(project)) if waiting and not active else None,
    }


def issue_row(project: Path, ref: github.IssueRef, found: dict, rows_by_dir: dict, queued: dict) -> dict:
    """One open issue with what AutoCode is doing about it, and the commands a client may take for it."""
    record = _record_for(project, ref)
    run = run_view(project, ref, record, rows_by_dir) if record else None
    entry = queued.get(ref.slug)
    return {
        "ref": str(ref),
        "number": ref.number,
        "title": (found.get("title") or "").strip(),
        "url": found.get("html_url"),
        "labels": [label.get("name") for label in found.get("labels") or () if isinstance(label, dict)],
        "queue_state": entry.get("state") if entry else None,
        "run": run,
        "actions": run["actions"] if run else {},
    }


def listing(project: Path, owner: str, repo: str, found: list[dict], rows: list[dict]) -> dict:
    queue = load(project)
    queued = {entry["slug"]: entry for entry in queue["entries"] if isinstance(entry, dict) and "slug" in entry}
    rows_by_dir = _rows_by_dir(rows)
    issues = [
        issue_row(project, github.IssueRef(owner, repo, int(item["number"])), item, rows_by_dir, queued)
        for item in found
        if isinstance(item.get("number"), int)
    ]
    return {
        "schema_version": SCHEMA,
        "repository": f"{owner}/{repo}",
        "project": str(project),
        "issues": issues,
        "queue": queue_view(project, queue, rows),
        # ISSUES is one or more of issues[].ref, LIMIT a whole number from 1; a client may follow them with
        # `--` and the AutoCode options a new chat starts with (its run mode's flags, its saved options).
        "queue_add": {
            "next_command": _autocode("queue", "add", "--project", str(project), "--max-active", "LIMIT", "ISSUES"),
            "default_max_active": queue["max_active"],
        },
    }


# ---------------------------------------------------------------- the scheduler


def _start(project: Path, entry: dict, options: list[str]) -> subprocess.Popen:
    argv = [*AUTOCODE, "issue", "start", entry["ref"], "--project", str(project), "--no-chat"]
    if options:
        argv += ["--", *options]
    log = project / ".autocode" / "issue-queue" / f"{entry['slug']}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as out:
        return subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)


def schedule(project: Path, *, poll: float = POLL_SECONDS, out=sys.stdout) -> int:
    """Start queued issues while fewer than the limit work; return once every one has started."""
    lock = project / LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as handle:
        try:
            util.lock_file(handle)
        except BlockingIOError:
            print("The issue queue is already running for this project.", file=out)
            return 0
        try:
            return _schedule(project, poll, out)
        finally:
            util.unlock_file(handle)


def _schedule(project: Path, poll: float, out) -> int:
    children: dict[str, subprocess.Popen] = {}
    with editing(project) as queue:
        for entry in queue["entries"]:
            if entry.get("state") == STARTING:  # a scheduler that stopped mid-start: `issue start` refuses a repeat
                entry["state"] = STARTED if record_path(project, _ref(project, entry["ref"])).exists() else QUEUED
    failed = 0
    while True:
        for slug, child in list(children.items()):
            if child.poll() is None:
                continue
            del children[slug]
            with editing(project) as queue:
                entry = next(e for e in queue["entries"] if e.get("slug") == slug)
                if child.returncode == 0:
                    entry["state"] = STARTED
                    print(f"Started {entry['ref']}: it works until it needs you.", file=out)
                else:
                    log = project / ".autocode" / "issue-queue" / f"{slug}.log"
                    entry["state"], entry["error"] = FAILED, log.read_text()[-MAX_ERROR:] if log.is_file() else ""
                    failed += 1
                    print(f"Could not start {entry['ref']}; see {log}", file=out)
        queue = load(project)
        waiting = [entry for entry in queue["entries"] if entry.get("state") == QUEUED]
        if not waiting and not children:
            return 1 if failed else 0
        if waiting:
            decision = admission.admit(waiting, working(project, runs(project), children), queue["max_active"])
            if decision.start:
                with editing(project) as queue:
                    for chosen in decision.start:
                        entry = next(e for e in queue["entries"] if e.get("slug") == chosen["slug"])
                        if entry.get("state") != QUEUED:
                            continue  # removed or changed while this round read the runs
                        if record_path(project, _ref(project, entry["ref"])).exists():
                            entry["state"] = STARTED  # started by hand since it was queued: it is its own run
                            continue
                        entry["state"] = STARTING
                        children[entry["slug"]] = _start(project, entry, list(entry.get("options") or ()))
                        print(f"Starting {entry['ref']}.", file=out)
        util.sleep(poll)


# ---------------------------------------------------------------- commands


def _ref(project: Path, text: str, repo: tuple[str, str] | None = None) -> github.IssueRef:
    """An issue named on the command line, or one the queue saved (always OWNER/REPO#N)."""
    return github.parse_ref(text, repo)


def cmd_list(args, project: Path, repo: tuple[str, str] | None) -> int:
    if repo is None:
        raise QueueError(f"the {args.remote} remote of {project} is not a GitHub repository")
    owner, name = repo
    view = listing(project, owner, name, github.signed_in().open_issues(owner, name, args.label), runs(project))
    if args.json:
        print(json.dumps(view, indent=2))
        return 0
    if not view["issues"]:
        print(f"{owner}/{name} has no open issues" + (f" labeled {args.label}." if args.label else "."))
    for row in view["issues"]:
        run = row["run"]
        state = (
            f"run {run['bucket'] or 'started'}" + (f", PR {run['pr_url']}" if run.get("pr_url") else "")
            if run
            else (row["queue_state"] or "")
        )
        print(f"#{row['number']:<6} {row['title'][:60]:<60} {state}".rstrip())
    return 0


def cmd_add(args, project: Path, repo: tuple[str, str] | None) -> int:
    if args.max_active is not None and args.max_active < 1:
        raise QueueError("--max-active must be a whole number of runs, 1 or more")
    refs = [_ref(project, text, repo) for text in args.issues]
    with editing(project) as queue:
        if args.max_active is not None:
            queue["max_active"] = args.max_active
        states = {entry.get("slug"): entry.get("state") for entry in queue["entries"]}
        for ref in refs:
            if states.get(ref.slug) not in (None, FAILED):
                print(f"{ref} is already on the queue.")
                continue
            if record_path(project, ref).exists():
                print(f"{ref} already has a run; see: autocode issue status {ref}")
                continue
            queue["entries"] = [entry for entry in queue["entries"] if entry.get("slug") != ref.slug]
            queue["entries"].append(
                {
                    "ref": str(ref),
                    "slug": ref.slug,
                    "number": ref.number,
                    "state": QUEUED,
                    "options": list(args.autocode_options),
                    "added_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
            )
            states[ref.slug] = QUEUED
            print(f"Queued {ref}.")
    view = queue_view(project)
    if view["next_command"]:
        print(f"Start them, at most {view['max_active']} working at once: autocode issue queue run")
    return 0


def cmd_run(args, project: Path, repo) -> int:
    return schedule(project)


def cmd_status(args, project: Path, repo) -> int:
    view = queue_view(project, rows=runs(project))
    if args.json:
        print(json.dumps({"schema_version": SCHEMA, "project": str(project), **view}, indent=2))
        return 0
    print(
        f"At most {view['max_active']} issue runs work at once; {view['working']} working now; "
        f"the scheduler is {'running' if view['runner_active'] else 'not running'}."
    )
    for entry in view["entries"]:
        print(f"  {entry['ref']:<32} {entry['state']}")
    if view["next_command"]:
        print("Resume it: autocode issue queue run")
    return 0


def cmd_remove(args, project: Path, repo: tuple[str, str] | None) -> int:
    slugs = {_ref(project, text, repo).slug for text in args.issues}
    with editing(project) as queue:
        kept = []
        for entry in queue["entries"]:
            if entry.get("slug") in slugs and entry.get("state") in (QUEUED, FAILED):
                print(f"Removed {entry['ref']} from the queue.")
                continue
            if entry.get("slug") in slugs:
                print(f"{entry['ref']} has started; its run is its own now (autocode issue status {entry['ref']}).")
            kept.append(entry)
        queue["entries"] = kept
    return 0


QUEUE_COMMANDS = {"add", "run", "status", "remove"}


def add_parsers(commands, common) -> None:
    """`list` and `queue` beside `autocode issue`'s other commands; ``common`` adds --project and --remote.

    Each handler is set as ``queue_handler`` and called with the project and its GitHub repository,
    which autocode_issue resolves, so this module never imports it.
    """
    listed = commands.add_parser("list", help="list the repository's open issues and what AutoCode is doing on each")
    common(listed)
    listed.add_argument("--label", help="only issues with this label")
    listed.add_argument("--json", action="store_true", help="print the listing as JSON for a client")
    listed.set_defaults(queue_handler=cmd_list)
    queued = commands.add_parser("queue", help="start many issues, a few working at a time")
    steps = queued.add_subparsers(dest="queue_command", required=True)
    add = steps.add_parser("add", help="queue issues to start")
    add.add_argument("issues", nargs="+", help="issue URLs, OWNER/REPO#N, or N for the project's remote")
    add.add_argument("--max-active", type=int, help=f"most issue runs working at once (default {DEFAULT_MAX_ACTIVE})")
    add.set_defaults(queue_handler=cmd_add)
    run = steps.add_parser("run", help="start queued issues while fewer than the limit work")
    run.set_defaults(queue_handler=cmd_run)
    status = steps.add_parser("status", help="show the queue and the command that resumes it")
    status.add_argument("--json", action="store_true", help="print the queue as JSON for a client")
    status.set_defaults(queue_handler=cmd_status)
    remove = steps.add_parser("remove", help="take issues that have not started off the queue")
    remove.add_argument("issues", nargs="+", help="issue URLs, OWNER/REPO#N, or N for the project's remote")
    remove.set_defaults(queue_handler=cmd_remove)
    for sub in (add, run, status, remove):
        common(sub)

"""Find the saved run an autocode invocation means when it names none.

Without --run-dir, ``autocode --status``, ``autocode`` (or ``autocode resume``) and the
user actions (``--answer``, ``--approve-goal``, ``--follow-up`` ...) act on the one saved
run the directory they start from can only mean. choose() returns that run, or raises
RunNotFound carrying the whole message for the user: the runs it found and the exact
command for each.

Where it looks, from the start directory (``--workspace``, by default the current one):

1. A run directory ``X/.autocode/runs/<id>`` that is the start or contains it: that run
   (once finished, only for --status, --dry-run and --follow-up). A parallel Builder's run (its state records ``parent_run``) stands for its parent. A run
   directory whose state cannot be used (or one ``autocode clean-worktrees`` archived) is
   refused, never swapped for another run.
2. Otherwise the nearest enclosing Git checkout. A task or program worktree (one with a
   valid ``.autocode/task-workspace.json``): its own runs. A project: its in-place runs
   and the runs of each task worktree under ``.autocode/worktrees``.
3. Outside any Git checkout: the task projects ``autocode`` bootstrapped in
   ``autocode-projects/``.

Only ``<checkout>/.autocode/runs/<id>/state.json`` is read, one level deep, never through
a symlink, and only when that state names the checkout as its workspace (what the run
registry requires). Builder worker, UI, archived, program and task-flow records live
elsewhere and are never candidates.

choose() says which run each kind of invocation takes. A bare ``autocode`` never takes a
finished run (relaunching one rechecks and redelivers it) or a run that ``autocode
program`` or ``autocode tasks`` drives; those are listed with the command that names them.
checkout_of() gives an explicit --run-dir its own checkout, resume_acknowledges() says at which
saved statuses a bare ``autocode resume`` stands for --resume-paused, and continue_hint() the
line printed after a user action saved on a run.

A lower-layer module beside autocode_util: it imports nothing from AutoCode, so it can
never join an import cycle. It is read-only: it creates no directory, takes no lock,
touches no registry and launches nothing. A run that is live when it is chosen is refused
later by the run lock, as for an explicit --run-dir.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path
import shlex

# What an invocation does with the run it names (autocode_args classifies every flag):
# advance - relaunch it (no action flag, --resume-paused and its companions, --unit);
# read - --status and --dry-run, which return before the run lock and change nothing;
# follow_up - --follow-up;
# act - every other user action (answers, approvals, feedback, --show-goal, ...): these
# take the run lock and save the run, so unlike read they never fall back to a finished run.
ACTIONS = ("advance", "read", "follow_up", "act")
COMPLETE = "TASK_COMPLETE"
# autocode_program.PLAN_PREAMBLE starts with this (tests/test_run_finder.py checks it). The
# program's plan run is started with --unit autoplanner, which is not saved, so relaunching
# it with a plain autocode would start building in the project checkout.
PROGRAM_PLAN_PREFIX = "PROGRAM PLANNING. "
OWNER_COMMAND = {"program": "autocode program", "task flow": "autocode tasks"}
LIST_LIMIT = 15
TASK_WIDTH = 72


class RunNotFound(ValueError):
    """No single run fits the invocation; str() is the complete message for the user."""


@dataclasses.dataclass(frozen=True)
class Candidate:
    """One saved run the invocation could mean."""
    run_dir: Path        # resolved
    workspace: Path      # the run's own checkout (its state's "workspace")
    status: str
    task: str            # the request's first line, shortened
    created: float       # POSIX seconds: created_at, else the run directory's name, else 0
    completed: float     # completed_at, else created
    stopped: bool        # an applied durable stop: terminal (autocode_stop)
    owner: str | None    # None, "program" or "task flow": the command that drives the run
    program_plan: bool = False   # the in-place plan run of ``autocode program plan``
    reason: str = ""     # why choose() took it; empty in a listing

    @property
    def complete(self) -> bool:
        """Finished with TASK_COMPLETE: the only state --follow-up continues."""
        return self.status == COMPLETE and not self.stopped

    @property
    def finished(self) -> bool:
        return self.status == COMPLETE or self.stopped

    @property
    def open(self) -> bool:
        return not self.finished


@dataclasses.dataclass
class _Search:
    where: Path                  # the checkout (or plain folder) messages call "here"
    looked_in: list[str]
    candidates: list[Candidate]  # newest first
    unreadable: list[Path]
    direct: bool = False         # the start directory is inside this one run


def candidates(start) -> list[Candidate]:
    """Every saved run reachable from ``start``, newest first."""
    return _search(start).candidates


def checkout_of(run_dir) -> Path | None:
    """The checkout ``X`` of a user's run directory ``X/.autocode/runs/<id>`` whose state names X.

    None otherwise, and for a parallel Builder's run (its state records ``parent_run``): its
    orchestrator drives it, so naming it alone keeps the usual workspace errors.
    """
    run_dir = Path(run_dir).resolve()
    if run_dir.parent.name != "runs" or run_dir.parent.parent.name != ".autocode":
        return None
    workspace = run_dir.parent.parent.parent
    state = _read_json(run_dir / "state.json")
    if not isinstance(state, dict) or state.get("parent_run"):
        return None
    return workspace if state.get("workspace") == str(workspace) else None


def choose(start, action: str, flags: str = "", unit: str | None = None) -> Candidate:
    """The run an invocation of kind ``action`` (see ACTIONS) started in ``start`` means.

    follow_up: the most recently completed run, preferring one no program or task flow drives;
    refused when an unfinished run no program or task flow drives was started after it finished.
    read: the only unfinished run, or with none the latest finished one.
    advance, act: the only unfinished run no program or task flow drives; with none, an act
    takes the only driven one, while advance refuses it and names the command that drives it.
    Several runs, none, or only finished ones for advance and act raise RunNotFound.
    Inside a run directory: that run, except that advance and act refuse it once finished,
    and advance refuses a driven one.
    ``flags`` is the rest of the user's command line and ``unit`` the unit it runs (--unit or
    the entry point); the commands a refusal lists repeat both after ``--run-dir RUN``.
    """
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    search = _search(start)
    found = search.candidates
    if search.direct:
        (run,) = found
        if action in ("advance", "act") and run.finished:
            # As from the project: relaunching rechecks and redelivers it, and a user action saves
            # it (a legacy run's migration on that path even reopens it).
            raise RunNotFound(_finished_message(search, run, here=True, action=action))
        if action == "advance" and run.owner:
            raise RunNotFound(_owned_message(search, run))
        return dataclasses.replace(run, reason="the run in this directory")
    if not found:
        raise RunNotFound(_none_message(search))
    unfinished = [run for run in found if run.open]
    if action == "follow_up":
        complete = [run for run in found if run.complete]
        if complete:
            latest = max(complete, key=lambda run: (run.owner is None, run.completed, str(run.run_dir)))
            # A run started after that one finished is the conversation the user is in now. It is
            # not finished, so a follow-up would silently reopen the older run instead.
            newer = [run for run in unfinished if not run.owner and run.created > latest.completed]
            if newer:
                raise RunNotFound(_follow_up_message(search, newer, latest))
            return dataclasses.replace(latest, reason="the latest completed run")
        raise RunNotFound(_follow_up_message(search, unfinished))
    if action == "read":
        if len(unfinished) == 1:
            return dataclasses.replace(unfinished[0], reason="the only unfinished run")
        if not unfinished:
            return dataclasses.replace(_latest_finished(found), reason="the latest finished run")
        raise RunNotFound(_ambiguous_message(search, unfinished, flags, unit, action))
    if not unfinished:
        raise RunNotFound(_finished_message(search, _latest_finished(found), here=False, action=action))
    free = [run for run in unfinished if not run.owner]
    if len(free) == 1:
        return dataclasses.replace(free[0], reason="the only unfinished run" if len(unfinished) == 1
                                   else "the only unfinished run no program or task flow drives")
    if not free and len(unfinished) == 1:
        if action == "act":
            return dataclasses.replace(unfinished[0], reason="the only unfinished run")
        raise RunNotFound(_owned_message(search, unfinished[0]))
    raise RunNotFound(_ambiguous_message(search, unfinished, flags, unit, action))


# Pauses `autocode resume` only shows: nothing guards them on relaunch, so acknowledging one would
# rerun its stage before the user acted (the Design Reviewer would rewrite <design>.blockers.json).
WAITS_FOR_AN_EDIT = ("PAUSED_DESIGN_CONFLICT",)


def resume_acknowledges(status) -> bool:
    """Whether a bare ``autocode resume`` stands for --resume-paused at this saved status.

    PAUSED_*, BLOCKED_*, *_REWORK_REQUIRED and RESOLVER_PENDING: the statuses a plain relaunch
    only shows and --resume-paused continues. autocode_args applies it, and also lets a resume
    companion acknowledge a verified operational pause that AutoResolver published as
    WAITING_FOR_USER. A plain ``autocode`` only shows a pause.
    """
    status = str(status or "")
    return (status.startswith(("PAUSED_", "BLOCKED_")) or status.endswith("_REWORK_REQUIRED")
            or status == "RESOLVER_PENDING") and status not in WAITS_FOR_AN_EDIT


def continue_hint(run_dir, state: dict, unit: str | None = None) -> str:
    """The line after a user action saved on ``run_dir`` (its state is ``state``): how to go on.

    ``unit`` is the unit the action ran under (--unit or the entry point); continuing repeats it,
    since a plain relaunch runs every unit (after an approval, the build).
    """
    run_dir = Path(run_dir).resolve()
    workspace = state.get("workspace")
    run = _candidate(run_dir, Path(workspace) if isinstance(workspace, str) else run_dir, state, {})
    if run.program_plan:
        return (f"Continue planning with: {_command(run, '--unit autoplanner')}; once the plan is approved: "
                f"autocode program derive --run-dir {shlex.quote(str(run_dir))} --output program.json")
    if run.finished:
        hint = f"It has finished ({run.status}); show it with: {_command(run, '--status')}"
        return hint + (f'; continue it with: {_command(run, "--follow-up")} "TEXT"' if run.complete else "")
    flags = f"--unit {unit}" if unit else ""
    if run.owner:
        return (f"`{OWNER_COMMAND[run.owner]}` drives it: rerun that command to advance it, or relaunch "
                f"it yourself with: {_command(run, flags)}")
    if resume_acknowledges(run.status):
        # A plain relaunch only shows this stop; the word resume acknowledges it (autocode_args).
        flags = " ".join(part for part in (flags, "resume") if part)
        return (f"Continue with: {_command(run, flags)} (or autocode {flags} from its project while it "
                "is the only unfinished run there)")
    plain = f"autocode {flags}" if flags else "plain autocode"
    return (f"Continue with: {_command(run, flags)} (or {plain} from its project while it is the only "
            "unfinished run there)")


# --- where to look ------------------------------------------------------------------

def _search(start) -> _Search:
    start = Path(start).resolve()
    if not start.is_dir():
        raise RunNotFound(f"No AutoCode run found: {start} is not a directory. Pass the project with "
                          "--workspace, or name a saved run with --run-dir.")
    owners: dict[Path, dict[Path, str]] = {}
    unreadable: list[Path] = []
    run_dir = _enclosing_run(start)
    if run_dir is not None:
        state = run_dir / "state.json"
        run = (None if _archived(run_dir) or state.is_symlink() or not state.is_file()
               else _load(run_dir, run_dir.parent.parent.parent, owners, unreadable))
        if run is None:
            # Standing in a run means that run: never fall back to another one of the checkout.
            raise RunNotFound(_unusable_message(run_dir))
        return _Search(run.workspace, [str(run.run_dir)], [run], unreadable, direct=True)
    checkout = next((path for path in (start, *start.parents) if (path / ".git").exists()), None)
    if checkout is None:
        bootstrapped = start / "autocode-projects"
        projects = ([path for path in sorted(bootstrapped.iterdir())
                     if not path.is_symlink() and path.is_dir() and (path / ".git").exists()]
                    if not bootstrapped.is_symlink() and bootstrapped.is_dir() else [])
        places, looked_in = [], []
        for project in projects:
            trees = _task_worktrees(project)
            places += [project, *trees]
            looked_in.append(_described(project, trees))
        where = start
        if not projects:
            looked_in = [f"{bootstrapped} (no task projects; {start} is not in a Git checkout)"]
    elif _worktree_metadata(checkout):
        places, looked_in, where = [checkout], [str(checkout / ".autocode" / "runs")], checkout
    else:
        trees = _task_worktrees(checkout)
        places, looked_in, where = [checkout, *trees], [_described(checkout, trees)], checkout
    found: dict[Path, Candidate] = {}
    for place in places:
        for run_dir in _run_dirs(place):
            run = _load(run_dir, place, owners, unreadable)
            if run is not None:
                found.setdefault(run.run_dir, run)
    ordered = sorted(found.values(), key=lambda run: (run.created, str(run.run_dir)), reverse=True)
    return _Search(where, looked_in, ordered, unreadable)


def _described(project: Path, trees: list[Path]) -> str:
    text = str(project / ".autocode" / "runs")
    if trees:
        text += (f" and {len(trees)} task worktree{'s' if len(trees) != 1 else ''} under "
                 f"{project / '.autocode' / 'worktrees'}")
    return text


def _enclosing_run(start: Path) -> Path | None:
    """The run directory ``start`` is in, whatever its state: ``X/.autocode/runs/<id>``, or one
    ``autocode clean-worktrees`` archived under ``X/.autocode/archive/<worktree>/runs/<id>``."""
    for path in (start, *start.parents):
        if path.parent.name == "runs" and (path.parent.parent.name == ".autocode" or _archived(path)):
            return path
    return None


def _archived(run_dir: Path) -> bool:
    return run_dir.parent.parent.parent.name == "archive" and run_dir.parent.parent.parent.parent.name == ".autocode"


def _worktree_metadata(tree: Path) -> dict | None:
    """The task/program worktree metadata of ``tree`` when it plainly describes ``tree``.

    The cheap half of autocode_workspaces.metadata(): no Git calls and never raises.
    """
    path = tree / ".autocode" / "task-workspace.json"
    try:
        if tree.is_symlink() or path.is_symlink() or not path.is_file():
            return None
        if not path.resolve().is_relative_to(tree.resolve()):
            return None
        data = _read_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("project_workspace"), str):
            return None
        root = tree.resolve()
        if data.get("workspace") != str(root):
            return None
        if not root.is_relative_to(Path(data["project_workspace"]).resolve() / ".autocode" / "worktrees"):
            return None
    except (OSError, ValueError):
        return None
    return data


def _task_worktrees(project: Path) -> list[Path]:
    """The worktrees under ``project/.autocode/worktrees`` whose metadata names ``project``."""
    parent = project / ".autocode" / "worktrees"
    try:
        if parent.is_symlink() or not parent.is_dir() or parent.resolve() != parent:
            return []
        children = sorted(parent.iterdir())
    except OSError:
        return []
    trees = []
    for child in children:
        if child.is_symlink() or not child.is_dir():
            continue
        data = _worktree_metadata(child)
        if data and data["project_workspace"] == str(project):
            trees.append(child)
    return trees


def _run_dirs(checkout: Path) -> list[Path]:
    """``checkout/.autocode/runs/<id>`` with a regular state.json, never through a symlink."""
    root = checkout / ".autocode" / "runs"
    try:
        if root.is_symlink() or not root.is_dir() or root.resolve() != root:
            return []
        children = sorted(root.iterdir())
    except OSError:
        return []
    return [child for child in children
            if not child.is_symlink() and child.is_dir()
            and not (child / "state.json").is_symlink() and (child / "state.json").is_file()]


# --- one run -------------------------------------------------------------------------

def _load(run_dir: Path, workspace: Path, owners, unreadable, *, parent_of=None) -> Candidate | None:
    state = _read_json(run_dir / "state.json")
    if not isinstance(state, dict):
        unreadable.append(run_dir)
        return None
    if state.get("workspace") != str(workspace):
        return None
    if state.get("parent_run"):
        # A parallel Builder's worker run: its parent run is the user's run.
        parent = state["parent_run"]
        if parent_of is not None or not isinstance(parent, str) or not Path(parent).is_absolute():
            return None
        parent = Path(parent).resolve()
        state_path = parent / "state.json"
        if (parent.parent.name != "runs" or parent.parent.parent.name != ".autocode"
                or state_path.is_symlink() or not state_path.is_file()):
            return None
        return _load(parent, parent.parent.parent.parent, owners, unreadable, parent_of=run_dir)
    return _candidate(run_dir, workspace, state, owners)


def _candidate(run_dir: Path, workspace: Path, state: dict, owners) -> Candidate:
    task = state.get("task") if isinstance(state.get("task"), str) else ""
    plan = task.startswith(PROGRAM_PLAN_PREFIX)
    created = _moment(state.get("created_at")) or _moment_from_name(run_dir.name) or 0.0
    receipts = state.get("applied_interventions")
    owner = _owner(run_dir, workspace, owners) or ("program" if plan else None)
    return Candidate(
        run_dir=run_dir, workspace=workspace, status=str(state.get("status") or "UNKNOWN"),
        task=_first_line(task.split("\nREQUEST:\n", 1)[-1] if plan else task),
        created=created, completed=_moment(state.get("completed_at")) or created,
        stopped=isinstance(receipts, list) and any(
            isinstance(receipt, dict) and receipt.get("kind") == "stop" for receipt in receipts),
        owner=owner, program_plan=plan)


def _owner(run_dir: Path, workspace: Path, owners) -> str | None:
    """"program" or "task flow" when that command recorded this run or its worktree."""
    data = _worktree_metadata(workspace)
    project = Path(data["project_workspace"]).resolve() if data else workspace
    if project not in owners:
        owners[project] = _owned_paths(project)
    owned = owners[project]
    # Programs and task flows record their project too; only a worktree of its own is owned.
    return owned.get(run_dir) or (owned.get(workspace) if workspace != project else None)


def _owned_paths(project: Path) -> dict[Path, str]:
    """Every absolute path a program or task flow under ``project`` recorded, by owner."""
    owned: dict[Path, str] = {}
    for owner, folder in (("task flow", "task-flows"), ("program", "programs")):
        base = project / ".autocode" / folder
        try:
            if base.is_symlink() or not base.is_dir():
                continue
            paths = sorted(base.glob("*/state.json"))
        except OSError:
            continue
        for path in paths:
            if path.is_symlink():
                continue
            for text in _strings(_read_json(path)):
                if text.startswith("/") and "\n" not in text and len(text) < 4096:
                    try:
                        owned[Path(text).resolve()] = owner
                    except (OSError, ValueError, RuntimeError):
                        continue
    return owned


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _moment(text) -> float | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        moment = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.timestamp()


def _moment_from_name(name: str) -> float | None:
    """Run directories start with their local creation time: ``20261004-153012-<slug>-<hex>``."""
    try:
        return dt.datetime.strptime(name[:15], "%Y%m%d-%H%M%S").timestamp()
    except ValueError:
        return None


def _first_line(text: str) -> str:
    line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return line if len(line) <= TASK_WIDTH else line[:TASK_WIDTH - 3].rstrip() + "..."


def _latest_finished(found: list[Candidate]) -> Candidate:
    return max((run for run in found if run.finished), key=lambda run: (run.completed, str(run.run_dir)))


# --- messages -------------------------------------------------------------------------

def _command(run: Candidate, flags: str = "") -> str:
    return f"autocode --run-dir {shlex.quote(str(run.run_dir))}" + (f" {flags}" if flags else "")


def _listed_flags(run: Candidate, flags: str, unit: str | None, action: str) -> str:
    """The user's unit and flags, as a listed command repeats them after ``--run-dir RUN``."""
    if action == "advance" and run.program_plan:
        # Relaunched without its unit, a program's plan run would build in the project checkout.
        unit = "autoplanner"
    return " ".join(part for part in (f"--unit {unit}" if unit else "", flags) if part)


def _entry(run: Candidate, flags: str = "") -> str:
    when = dt.datetime.fromtimestamp(run.created).strftime("%Y-%m-%d %H:%M") if run.created else "unknown time"
    driven = f", driven by `{OWNER_COMMAND[run.owner]}`" if run.owner else ""
    return f"  {when}  {run.status}{driven}: {run.task or '(no task text)'}\n      {_command(run, flags)}"


def _entries(runs: list[Candidate], flags: str | list[str] = "") -> list[str]:
    """One entry per run, each with ``flags`` (or its own flags, given as a list)."""
    each = flags if isinstance(flags, list) else [flags] * len(runs)
    lines = [_entry(run, run_flags) for run, run_flags in list(zip(runs, each))[:LIST_LIMIT]]
    if len(runs) > LIST_LIMIT:
        lines.append(f"  ... and {len(runs) - LIST_LIMIT} older (not shown)")
    return lines


def _unreadable_note(search: _Search) -> list[str]:
    count = len(search.unreadable)
    if not count:
        return []
    names = ", ".join(str(path) for path in search.unreadable[:5]) + (f" and {count - 5} more" if count > 5 else "")
    return [f"Skipped {count} run director{'ies' if count != 1 else 'y'} whose state.json could not be read: {names}."]


def _unusable_message(run_dir: Path) -> str:
    """Why the run directory the start is in cannot be used (what _load refused)."""
    checkout = run_dir.parent.parent.parent
    path = run_dir / "state.json"
    state = _read_json(path)
    if _archived(run_dir):
        why = "autocode clean-worktrees archived it when it removed the run's worktree, so it cannot be resumed"
    elif path.is_symlink():
        why = "its state.json is a symbolic link"
    elif not path.is_file():
        why = "it has no state.json"
    elif not isinstance(state, dict):
        why = "its state.json could not be read"
    elif state.get("workspace") != str(checkout):
        why = (f"its state.json names the workspace {state.get('workspace')}, not {checkout} "
               "(a moved project or a copied run)")
    else:
        why = f"it is a parallel Builder's run, and its parent run {state.get('parent_run')} cannot be used"
    return (f"The saved run {run_dir} you are in cannot be used: {why}. No other run was chosen. "
            "Pass --run-dir to choose a run, or run autocode from the project or task worktree.")


def _none_message(search: _Search) -> str:
    return "\n".join([
        f"No AutoCode run found in {search.where} (looked in {'; '.join(search.looked_in)}).",
        *_unreadable_note(search),
        'Start a task with: autocode "your task"',
        "or name a saved run: autocode --run-dir /path/to/project/.autocode/runs/RUN"])


def _ambiguous_message(search: _Search, unfinished: list[Candidate], flags: str,
                       unit: str | None, action: str) -> str:
    finished = len(search.candidates) - len(unfinished)
    lines = [f"{len(unfinished)} unfinished AutoCode runs in {search.where}; add --run-dir to choose one:",
             *_entries(unfinished, [_listed_flags(run, flags, unit, action) for run in unfinished]),
             "Inside a task worktree, the same command without --run-dir chooses that worktree's run."]
    if action == "advance" and any(run.program_plan for run in unfinished[:LIST_LIMIT]):
        lines.append("A run `autocode program` plans continues with --unit autoplanner; once its plan is "
                     "approved, derive the program with autocode program derive (docs/program.md).")
    if finished:
        lines.append(f"Not listed: {finished} finished run{'s' if finished != 1 else ''}.")
    return "\n".join(lines)


def _finished_message(search: _Search, latest: Candidate, *, here: bool, action: str = "advance") -> str:
    if here:
        doing = ("a bare autocode does not relaunch it" if action == "advance"
                 else "this command needs an unfinished run; to act on it anyway, name it with --run-dir")
        head = f"The run in this directory has finished ({latest.status}); {doing}:"
    else:
        doing = "nothing to resume" if action == "advance" else "this command needs an unfinished run"
        head = f"No unfinished AutoCode run in {search.where}; {doing}. The latest run has finished:"
    lines = [head, _entry(latest, "--status"), "Show it: autocode --status"]
    if latest.complete:
        follow_up = "autocode --follow-up" if here or not latest.owner else _command(latest, "--follow-up")
        lines.append(f'Continue it with a new request: {follow_up} "TEXT"')
    lines.append('Start a new task from the project: autocode "your task"')
    return "\n".join(lines)


def _owned_message(search: _Search, run: Candidate) -> str:
    if run.program_plan:
        hint = ("It plans a program: answer and approve it with the usual flags (autocode --status shows what "
                f"it needs), continue its planning with {_command(run, '--unit autoplanner')}, then derive "
                "the program with autocode program derive (docs/program.md).")
    else:
        rerun = "autocode program run MANIFEST" if run.owner == "program" else "autocode tasks MANIFEST"
        hint = (f"Rerun {rerun} to advance it. Its questions and approvals take the usual flags here "
                "(autocode --status shows what it needs); to relaunch it yourself, name it with --run-dir.")
    return "\n".join([f"The only unfinished AutoCode run in {search.where} is driven by "
                      f"`{OWNER_COMMAND[run.owner]}`; a bare autocode does not advance it:",
                      _entry(run, "--status"), hint])


def _follow_up_message(search: _Search, unfinished: list[Candidate], latest: Candidate | None = None) -> str:
    if latest:
        return "\n".join([
            f"--follow-up continues a finished run, but a run in {search.where} started after the latest "
            "one finished has not finished. It takes --answer, --approve-goal, --feedback or a resume instead:",
            *_entries(unfinished, "--status"),
            f'To continue the finished run anyway: {_command(latest, "--follow-up")} "TEXT"'])
    lines = [f"--follow-up continues a finished run, and no run in {search.where} has completed."]
    if unfinished:
        lines += ["An unfinished run takes --answer, --feedback or a plain resume instead:",
                  *_entries(unfinished, "--status")]
    else:
        lines.append('Start a new task with: autocode "your task"')
    return "\n".join(lines)

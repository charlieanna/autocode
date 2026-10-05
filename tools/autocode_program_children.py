"""The program's only path to its child runs: a thin adapter over the task-run interface.

Each program workstream is an ordinary AutoCode run in its own worktree.
autocode_program schedules and merges them; this module reaches the runs, and
only through autocode_taskrun (docs/task-run.md):

- it starts a workstream's run, or, after an interrupted controller, reattaches
  to the one run that start created in its worktree;
- it advances a run exactly once per call;
- it reads the run's status view (``autocode --status``) and maps it onto the
  program's workstream record (``apply_view``);
- it keeps each invocation's stdout.log, stderr.log, exit code and command;
- it sends the program's own feedback to a child whose displayed plan drops
  a requirement the workstream inherited (``feedback``).

It never approves, answers or resumes a pause on anyone's behalf, and never
reads a child's state.json: the status view is the only source of a
workstream's status. Worker threads pass the program's state lock as ``lock``;
the CLI calls themselves run outside it.
"""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

try:
    from . import autocode_taskrun as taskrun, autocode_util as util
except ImportError:
    import autocode_taskrun as taskrun
    import autocode_util as util

# The child CLI. Read on every call, so a test can substitute a stand-in script.
CHILD_COMMAND = taskrun.AUTOCODE
MULTIPLE = "Multiple child checkpoints found; select the correct run before retrying"
TERMINAL_CODE = {"TASK_COMPLETE"}
WAITING_CODE = {"WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL"}
NO_LOCK = nullcontext()
VIEW_FIELDS = ("needs", "progress")  # copied from the child's latest status view by apply_view
INTERNAL_FIELDS = ("command", "runs_before")  # this module's bookkeeping; a program summary leaves it out
RUN_FIELDS = ("run_dir", "run_status", "runs_before", "exit_code", "command", "last_invocation_at", *VIEW_FIELDS)


def _run(workspace, run_dir) -> taskrun.TaskRun:
    return taskrun.TaskRun(Path(workspace), Path(run_dir), command=tuple(CHILD_COMMAND), cwd=Path(workspace))


def prepare_start(record, workspace) -> None:
    """Before a record without a run is started, note the runs its worktree already holds.

    None of them is the workstream's: the integration worktree is shared for the
    whole program, and a person may run AutoCode there. The program saves this
    with the record before invoking the child, so that after an interruption
    ``handle`` reattaches only to a run created since. Call it under the state lock.
    """
    if not record.get("run_dir"):
        record["runs_before"] = [str(path) for path in taskrun.TaskRun.runs_in(workspace)]


def handle(record, *, lock=NO_LOCK) -> taskrun.TaskRun | None:
    """The record's run: from its run_dir, or by reattaching to the one run its start created.

    Reattaching considers only runs created in the worktree since ``prepare_start``;
    a reattached run's directory is adopted into the record. Returns None when
    there is no such run (or no start was prepared); raises TaskRunError
    (MULTIPLE) when there are several.
    """
    if record.get("run_dir"):
        return _run(record["workspace"], record["run_dir"])
    if not record.get("workspace") or "runs_before" not in record:
        return None
    try:
        run = taskrun.TaskRun.attach(record["workspace"], command=tuple(CHILD_COMMAND), cwd=record["workspace"],
                                     exclude=record["runs_before"])
    except taskrun.TaskRunError:
        raise taskrun.TaskRunError(MULTIPLE) from None
    if run is not None:
        with lock:
            record["run_dir"] = str(run.run_dir)
    return run


def apply_view(record, view) -> None:
    """Map a child's status view onto its program record, in place and without I/O.

    Keyed on ``view['status']``, the child's saved status: a completed child is
    COMPLETE, a reopened one (RUNNING again) is no longer complete, a child at a
    question or plan approval is WAITING, any other stop is PAUSED, and MERGED is
    never downgraded. The additive ``needs`` and ``progress`` (the view's one-line
    progress) say what the child waits for. ``needs`` keeps ``kind`` and ``token``,
    plus, for a question, ``request_kind``, the AutoResolver ``resolver_scope`` and
    ``resolver_request_id``, and each question's id and text, so the program can tell
    a child asking to change what it inherited from an ordinary question.
    """
    status = view.get("status") if isinstance(view, dict) else None
    if not isinstance(status, str) or not status:
        _unreadable(record, "Child status view has no status")
        return
    record["run_status"] = status
    needs = view.get("needs")  # None once the child is complete
    record["needs"] = _needs(needs) if isinstance(needs, dict) else None
    line = view["progress"].get("line") if isinstance(view.get("progress"), dict) else None
    if line:
        record["progress"] = line
    else:
        record.pop("progress", None)
    if record["status"] == "MERGED":
        return
    if status in TERMINAL_CODE:
        if record["status"] not in ("COMPLETE", "CONFLICT"):
            record.update(status="COMPLETE", finished_at=util.now())
    elif status == "RUNNING" and record["status"] in ("COMPLETE", "CONFLICT"):
        record["status"] = "WAITING"
        record.pop("finished_at", None)
    elif status in WAITING_CODE:
        record["status"] = "WAITING"
    elif status != "RUNNING":
        record["status"] = "PAUSED"


def _needs(needs):
    kept = {key: needs[key] for key in ("kind", "token", "request_kind", "resolver_scope", "resolver_request_id")
            if needs.get(key) is not None}
    if isinstance(needs.get("questions"), list):
        kept["questions"] = [{key: question.get(key) for key in ("id", "question")}
                             for question in needs["questions"] if isinstance(question, dict)]
    return kept


def forget_view(record) -> None:
    """Drop what the child's last status view said it needs: after a failure it is unknown."""
    for key in VIEW_FIELDS:
        record.pop(key, None)


def detach(record) -> None:
    """Forget the record's run, so the next ``start`` creates a fresh one; the old run stays on disk."""
    for key in RUN_FIELDS:
        record.pop(key, None)


def _unreadable(record, error) -> None:
    record.update(status="FAILED", run_status=None, error=error)
    forget_view(record)


def read(record, *, lock=NO_LOCK) -> dict | None:
    """Read the child's status view onto ``record``; return the view, or None.

    None when the record has no run yet, or when the view cannot be read: the
    record is then FAILED with run_status, needs and progress unknown (the
    MULTIPLE error when its worktree holds several new runs). A removed worktree
    is such a failure, not an exception.
    """
    try:
        run = handle(record, lock=lock)
    except taskrun.TaskRunError:
        with lock:
            _unreadable(record, MULTIPLE)
        return None
    if run is None:
        return None
    try:
        view = run.status()
    except taskrun.TaskRunError as error:
        with lock:
            _unreadable(record, f"Cannot read child status: {error}")
        return None
    with lock:
        apply_view(record, view)
    return view


def refresh(record, *, lock=NO_LOCK) -> dict | None:
    """Bring ``record`` up to date with its child run (see ``read``)."""
    return read(record, lock=lock)


def start(record, workspace, brief, start_options=(), *, log_dir, lock=NO_LOCK) -> dict | None:
    """Start a new run for the workstream in ``workspace`` (a record with a run is advanced instead).

    Only the run this start creates becomes the workstream's: a run already in
    the worktree is never adopted. ``start_options`` (engine and pass-through
    flags) are given to the new run only; a later ``advance`` relaunches the
    saved run without them.
    """
    if record.get("run_dir"):
        return advance(record, log_dir=log_dir, lock=lock)
    try:
        run = taskrun.TaskRun.start(workspace, brief, start_options=tuple(start_options),
                                    command=tuple(CHILD_COMMAND), cwd=workspace)
    except taskrun.TaskRunError as error:
        if error.run_dir is not None:  # the start saved its run before it failed: read that run below
            with lock:
                record["run_dir"] = str(error.run_dir)
        return _settle(record, error.process, None, log_dir, lock, failure=str(error))
    with lock:
        record["run_dir"] = str(run.run_dir)
    return _settle(record, run.last_advance, None, log_dir, lock)


def advance(record, *, log_dir, lock=NO_LOCK) -> dict | None:
    """Relaunch the workstream's saved run exactly once."""
    run = _run(record["workspace"], record["run_dir"])
    try:
        view = run.advance()
    except taskrun.TaskRunError as error:
        # The relaunch was refused or could not run, or its status could not be read: read it once below.
        return _settle(record, run.last_advance, None, log_dir, lock, failure=str(error))
    return _settle(record, run.last_advance, view, log_dir, lock)


def feedback(record, text) -> dict:
    """Send the program's feedback on the run's displayed plan (``--feedback``); return its status view.

    The child re-plans when it is next advanced. Raises TaskRunError when the
    child refuses the feedback.
    """
    return _run(record["workspace"], record["run_dir"]).feedback(text)


def _settle(record, process, view, log_dir, lock, failure=None) -> dict | None:
    """Keep the invocation's output, read the child's status, and settle a record still RUNNING.

    An exit code alone never completes a workstream. When the view leaves the
    record RUNNING, it becomes WAITING if the child exited 0 or 2 and has a run
    (so a relaunch the child refused with exit 2, for example at its workspace
    lock, waits), and FAILED otherwise, with ``failure`` (the invocation's
    error, if any) as its error.
    """
    log_dir = Path(log_dir)
    if process is not None:
        (log_dir / "stdout.log").write_text(process.stdout or "")
        (log_dir / "stderr.log").write_text(process.stderr or "")
    with lock:
        if process is not None:
            record.update(exit_code=process.returncode, command=[str(part) for part in process.args])
        record["last_invocation_at"] = util.now()
    if view is None:
        view = read(record, lock=lock)
    else:
        with lock:
            apply_view(record, view)
    with lock:
        if record["status"] == "RUNNING":
            failed = process is None or process.returncode not in (0, 2) or not record.get("run_dir")
            record["status"] = "FAILED" if failed else "WAITING"
            if failed and failure:
                record.setdefault("error", failure)
    return view

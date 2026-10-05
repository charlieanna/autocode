"""Drive one AutoCode task run through its CLI: the write side of the task-run interface.

Code that coordinates several task runs (the planned architecture and
multi-component layer) uses this instead of importing runner internals. Each
call is one CLI invocation and the run's state lives on disk, so a caller that
crashes can reattach with ``TaskRun(workspace, run_dir)``. Reads return the
status view from autocode_run_view. See docs/task-run.md.

    run = TaskRun.start(workspace, brief, options=("--engine", "codex"))
    view = run.advance_until_input()
    if view["needs"]["kind"] == "approve_plan":
        view = run.approve_plan(view["needs"]["token"])
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

AUTOCODE = (sys.executable, str(Path(__file__).resolve().parent / "autocode.py"))


class TaskRunError(RuntimeError):
    """AutoCode rejected a command, or could not run it.

    ``process`` is the CompletedProcess of the CLI call that failed, so a caller
    can keep its output and exit code. It is None when the error is not a failed
    call: a call that could not run or did not finish (a missing working
    directory, a timeout), ``attach`` finding several runs, or a guard such as
    ``advance_until_input``'s no-progress check, raised after its calls
    finished (``TaskRun.last_advance`` still holds the latest advancing call).
    ``run_dir`` is the run a failed ``start`` created, when it created exactly one.
    """

    def __init__(self, message: str, process: subprocess.CompletedProcess | None = None,
                 run_dir: Path | None = None):
        super().__init__(message)
        self.process = process
        self.run_dir = run_dir


@dataclass
class TaskRun:
    workspace: Path
    run_dir: Path
    command: tuple[str, ...] = AUTOCODE
    options: tuple[str, ...] = ()  # engine and model flags, passed whenever the run starts or advances
    env: dict | None = None
    timeout: float | None = None
    # The CLI's working directory; None keeps the caller's. Relative paths in options resolve against it.
    cwd: Path | None = None
    # The CompletedProcess of the latest call that starts or advances the run (also when it was
    # rejected), for a caller that keeps the run's output or exit code. Status reads never replace it.
    last_advance: subprocess.CompletedProcess | None = field(default=None, compare=False, repr=False)

    @classmethod
    def start(cls, workspace, brief: str, *, options=(), start_options=(), command=AUTOCODE, env=None, timeout=None,
              cwd=None) -> "TaskRun":
        """Create a run that works directly in ``workspace`` and advance it to its first stop.

        The caller owns the workspace (for example a worktree it created), so
        AutoCode does not create another one. Start one run per workspace at a time.
        ``start_options`` supplies inputs such as --ui-run only on this invocation;
        unlike ``options``, they are not repeated when advancing the saved run.
        """
        workspace = Path(workspace).resolve()
        before = set(_runs(workspace))
        run = cls(workspace, Path(), tuple(command), tuple(options), env, timeout, _path(cwd))
        try:
            proc = run._invoke("start", brief, "--in-place", "--no-chat", *run.options, *start_options,
                               advancing=True, with_run_dir=False)
        except TaskRunError as error:
            created = set(_runs(workspace)) - before
            if len(created) == 1:  # the start saved its run before it failed
                error.run_dir = created.pop()
            raise
        created = set(_runs(workspace)) - before
        if not created:
            detail = (proc.stderr or proc.stdout).strip()[-800:]
            raise TaskRunError(f"start exited {proc.returncode} without creating a run: {detail}", proc)
        if len(created) != 1:
            raise TaskRunError(f"expected one new run in {workspace}, found {sorted(map(str, created))}", proc)
        run.run_dir = created.pop()
        return run

    @classmethod
    def attach(cls, workspace, *, options=(), command=AUTOCODE, env=None, timeout=None, cwd=None,
               exclude=()) -> "TaskRun | None":
        """Reattach to the one run in ``workspace``, or None if it has none yet.

        For a caller that lost its record of ``run_dir``, for example because it
        crashed while ``start`` was still advancing the new run. ``exclude`` lists
        runs that are not the caller's: in a workspace someone else may also run
        AutoCode in, pass what ``runs_in`` returned before the start.
        """
        workspace = Path(workspace).resolve()
        skip = {Path(path).resolve() for path in exclude}
        runs = [run for run in _runs(workspace) if run.resolve() not in skip]
        if len(runs) > 1:
            raise TaskRunError(f"expected at most one run in {workspace}, found {sorted(map(str, runs))}")
        return cls(workspace, runs[0], tuple(command), tuple(options), env, timeout, _path(cwd)) if runs else None

    @staticmethod
    def runs_in(workspace) -> list[Path]:
        """The runs already saved in ``workspace``; see ``attach``'s ``exclude``."""
        return sorted(_runs(Path(workspace).resolve()))

    def status(self) -> dict:
        proc = self._invoke("status", "--status")
        try:
            return json.loads(proc.stdout)["view"]
        except (ValueError, KeyError) as error:
            raise TaskRunError(f"--status did not return a status view: {error}", proc) from None

    def revise_design(self, manifest: Path, expected_hash: str, reason: str) -> dict:
        """Propose a stopped run's design input correction; never approve or dispatch."""
        self._act("revise design", "--revise-figma-manifest", str(manifest),
                  "--expected-design-hash", expected_hash, "--design-change-reason", reason)
        return self.status()

    def show_goal(self) -> str:
        """Return the displayed brief a person must read before approving its token."""
        return self._invoke("show goal", "--show-goal").stdout

    def advance(self) -> dict:
        """Relaunch the run; it works until it completes or stops for input."""
        self._invoke("advance", "--no-chat", *self.options, advancing=True)
        return self.status()

    def advance_until_input(self, max_calls: int = 20) -> dict:
        """Advance while the run needs nothing from the caller."""
        view = self.status()
        for _ in range(max_calls):
            if view["done"] or view["needs"]["kind"] != "continue":
                return view
            before = (view["status"], view["next_stage"], view["iteration"], view["phase"])
            view = self.advance()
            if (view["status"], view["next_stage"], view["iteration"], view["phase"]) == before:
                raise TaskRunError(f"no progress at {view['status']} (next stage {view['next_stage']})")
        raise TaskRunError(f"still not stopped after {max_calls} relaunches")

    def resume_paused(self) -> dict:
        """Acknowledge a pause after its cause is resolved, and continue."""
        self._invoke("resume", "--resume-paused", "--no-chat", *self.options, advancing=True)
        return self.status()

    def abandon_stage(self, attempt_id: str) -> dict:
        """Set aside the inspected uncertain attempt (retains edits and evidence)."""
        self._act("abandon stage", "--abandon-stage", attempt_id)
        return self.status()

    def retry_job(self, token: str) -> dict:
        """Retry exactly the inspected failed workflow job, retaining route and limits."""
        self._invoke('retry job', '--resume-paused', '--retry-failed-stage', '--job-retry-token', token,
                     '--no-chat', *self.options, advancing=True)
        return self.status()

    def grant_recovery(self, amount: int) -> dict:
        """Grant exactly N new recoveries after the operator resolves the pause cause."""
        if type(amount) is not int or amount < 1:
            raise ValueError('recovery allowance must be a positive integer')
        self._invoke('grant recovery', '--resume-paused', '--grant-recovery', str(amount),
                     '--no-chat', *self.options, advancing=True)
        return self.status()

    def compare_checkpoint(self, checkpoint_id: str) -> dict:
        """Read a source-bound comparison; no execution, approval or file rewrite."""
        return json.loads(self._invoke("compare checkpoint", "checkpoint", "--compare", checkpoint_id).stdout)

    def restore_checkpoint(self, checkpoint_id: str, expected_token: str, request_id: str) -> "TaskRun":
        """Create a paused continuation on a new branch; keep this run untouched."""
        result = json.loads(self._invoke("restore checkpoint", "checkpoint", "--restore", checkpoint_id,
            "--expected-token", expected_token, "--request-id", request_id).stdout)
        return TaskRun(Path(result["workspace"]), Path(result["run_dir"]), self.command,
                       self.options, self.env, self.timeout, self.cwd)

    def accept_transport_change(self) -> dict:
        """Explicitly accept a validated OpenCode transport change and continue."""
        self._invoke("accept transport change", "--resume-paused", "--accept-transport-change",
                     "--no-chat", *self.options, advancing=True)
        return self.status()

    def retry_report(self, attempt_id: str) -> dict:
        """Request one fresh report for the exact inspected rejected attempt."""
        self._invoke("retry report", "--resume-paused", "--retry-report", attempt_id,
                     "--no-chat", advancing=True)
        return self.status()

    def retry_failed_stage(self) -> dict:
        """Authorize one inspected retry of a repeated failed stage."""
        self._invoke("retry failed stage", "--resume-paused", "--retry-failed-stage",
                     "--no-chat", advancing=True)
        return self.status()

    def bind_dependency(self, specification: Path) -> dict:
        return self._act("bind dependency", "--bind-dependency", str(specification))

    def receive_dependency(self, manifest: Path) -> dict:
        return self._act("receive dependency", "--receive-dependency", str(manifest))

    def answer(self, question_id: str, text: str, *, resolver_token: str | None = None) -> dict:
        """Answer one question of the run's current AutoResolver request.

        Each answer consumes that request, and the questions left return under a
        new token. Pass ``resolver_token`` from the view the answer was chosen from;
        a stale one is rejected, never refreshed. Without it, the current request's
        token is used after checking that the request lists ``question_id``.
        """
        if resolver_token is None:
            need = self.status()["needs"] or {}
            listed = [question["id"] for question in need.get("questions") or ()]
            if need.get("kind") != "answer" or question_id not in listed:
                raise TaskRunError(f"the run is not waiting for an answer to {question_id} "
                                   f"(needs {need.get('kind')}, questions {listed})")
            resolver_token = need.get("resolver_token")
            if not resolver_token:
                raise TaskRunError(f"no current AutoResolver request carries {question_id}; "
                                   "advance the run to publish one, then answer")
        return self._act("answer", "--answer", f"{question_id}={text}", "--resolver-token", resolver_token)

    def assign_model(self, role: str, model: str, *, resolver_token: str | None = None) -> dict:
        """Name the model a role stopped on quota or a refusal continues on (``needs.route``), then resume_paused().

        The same answer as ``answer(f"route-{role}", model)``: AutoCode refuses a model the launch
        would refuse (engine, format, availability, cross-model) and leaves the run paused. A
        ``--<role>-model`` in ``options`` is updated, so advancing never passes the old model back.
        """
        view = self.answer(f"route-{role}", model, resolver_token=resolver_token)
        flag = "--" + role.replace("_", "-") + "-model"
        options = list(self.options)
        if flag in options[:-1]:
            options[options.index(flag) + 1] = model
            self.options = tuple(options)
        return view

    def respond_operational(self, request_id: str, request_token: str, text: str) -> dict:
        """Send corrective information to the published AutoResolver request."""
        return self._act("resolver response", "--resolver-request", request_id,
                         "--resolver-token", request_token, "--resolver-response",
                         "provide_information", "--resolver-message", text)

    def approve_plan(self, token: str) -> dict:
        return self._act("approve plan", "--approve-goal", token)

    def approve_review(self, criterion: str, token: str) -> dict:
        return self._act("approve review", "--approve-review", criterion, "--review-token", token)

    def feedback(self, text: str) -> dict:
        return self._act("feedback", "--feedback", text)

    def follow_up(self, text: str) -> dict:
        """Say the next thing to a finished run; continue it afterwards."""
        return self._act("follow-up", "--follow-up", text)

    def _act(self, name: str, *args: str) -> dict:
        """User actions exit 0 once saved; anything else means AutoCode rejected them."""
        self._invoke(name, *args)
        return self.status()

    def _invoke(self, name: str, *args: str, advancing: bool = False, with_run_dir: bool = True):
        cmd = [*self.command, *args, "--workspace", str(self.workspace)]
        if with_run_dir:
            cmd += ["--run-dir", str(self.run_dir)]
        where = {"cwd": self.cwd} if self.cwd is not None else {}
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout,
                                  env={**os.environ, **(self.env or {})}, **where)
        except subprocess.TimeoutExpired:
            raise TaskRunError(f"{name} did not finish within {self.timeout} s") from None
        except OSError as error:  # e.g. a working directory or workspace that was removed
            raise TaskRunError(f"{name} could not run: {error}") from error
        if advancing:
            self.last_advance = proc
        # Advancing exits 0 when complete and 2 when stopped for input. Usage errors
        # also exit 2, so recognize argparse's message rather than trusting the code.
        usage_error = proc.returncode == 2 and proc.stderr.startswith("usage:")
        rejected_input = proc.returncode == 2 and any(
            line.startswith(("Input rejected:", "autocode:"))
            for message in (proc.stdout, proc.stderr) for line in message.splitlines())
        accepted = proc.returncode in (0, 2) if advancing else proc.returncode == 0
        if usage_error or rejected_input or not accepted:
            detail = (proc.stderr or proc.stdout).strip()[-800:]
            raise TaskRunError(f"{name} exited {proc.returncode}: {detail}", proc)
        return proc


def _path(value) -> Path | None:
    return None if value is None else Path(value)


def _runs(workspace: Path) -> list[Path]:
    root = workspace / ".autocode" / "runs"
    return [path.parent for path in root.glob("*/state.json")] if root.is_dir() else []

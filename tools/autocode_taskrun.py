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
from dataclasses import dataclass
from pathlib import Path

AUTOCODE = (sys.executable, str(Path(__file__).resolve().parent / "autocode.py"))


class TaskRunError(RuntimeError):
    """AutoCode rejected a command, or could not run it."""


@dataclass
class TaskRun:
    workspace: Path
    run_dir: Path
    command: tuple[str, ...] = AUTOCODE
    options: tuple[str, ...] = ()  # engine and model flags, passed whenever the run starts or advances
    env: dict | None = None
    timeout: float | None = None

    @classmethod
    def start(cls, workspace, brief: str, *, options=(), command=AUTOCODE, env=None, timeout=None) -> "TaskRun":
        """Create a run that works directly in ``workspace`` and advance it to its first stop.

        The caller owns the workspace (for example a worktree it created), so
        AutoCode does not create another one. Start one run per workspace at a time.
        """
        workspace = Path(workspace).resolve()
        before = set(_runs(workspace))
        run = cls(workspace, Path(), tuple(command), tuple(options), env, timeout)
        proc = run._invoke("start", brief, "--in-place", "--no-chat", *run.options, advancing=True, with_run_dir=False)
        created = set(_runs(workspace)) - before
        if not created:
            detail = (proc.stderr or proc.stdout).strip()[-800:]
            raise TaskRunError(f"start exited {proc.returncode} without creating a run: {detail}")
        if len(created) != 1:
            raise TaskRunError(f"expected one new run in {workspace}, found {sorted(map(str, created))}")
        run.run_dir = created.pop()
        return run

    @classmethod
    def attach(cls, workspace, *, options=(), command=AUTOCODE, env=None, timeout=None) -> "TaskRun | None":
        """Reattach to the one run in ``workspace``, or None if it has none yet.

        For a caller that lost its record of ``run_dir``, for example because it
        crashed while ``start`` was still advancing the new run.
        """
        workspace = Path(workspace).resolve()
        runs = _runs(workspace)
        if len(runs) > 1:
            raise TaskRunError(f"expected at most one run in {workspace}, found {sorted(map(str, runs))}")
        return cls(workspace, runs[0], tuple(command), tuple(options), env, timeout) if runs else None

    def status(self) -> dict:
        proc = self._invoke("status", "--status")
        try:
            return json.loads(proc.stdout)["view"]
        except (ValueError, KeyError) as error:
            raise TaskRunError(f"--status did not return a status view: {error}") from None

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

    def grant_recovery(self, amount: int) -> dict:
        """Grant exactly N new recoveries after the operator resolves the pause cause."""
        if type(amount) is not int or amount < 1:
            raise ValueError('recovery allowance must be a positive integer')
        self._invoke('grant recovery', '--resume-paused', '--grant-recovery', str(amount),
                     '--no-chat', *self.options, advancing=True)
        return self.status()

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
        """Answer the displayed question, retaining its resolver token when present."""
        token_args = ("--resolver-token", resolver_token) if resolver_token is not None else ()
        return self._act("answer", "--answer", f"{question_id}={text}", *token_args)

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
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout,
                                  env={**os.environ, **(self.env or {})})
        except subprocess.TimeoutExpired:
            raise TaskRunError(f"{name} did not finish within {self.timeout} s") from None
        # Advancing exits 0 when complete and 2 when stopped for input. Usage errors
        # also exit 2, so recognize argparse's message rather than trusting the code.
        usage_error = proc.returncode == 2 and proc.stderr.startswith("usage:")
        rejected_input = proc.returncode == 2 and any(
            line.startswith("Input rejected:")
            for message in (proc.stdout, proc.stderr) for line in message.splitlines())
        accepted = proc.returncode in (0, 2) if advancing else proc.returncode == 0
        if usage_error or rejected_input or not accepted:
            detail = (proc.stderr or proc.stdout).strip()[-800:]
            raise TaskRunError(f"{name} exited {proc.returncode}: {detail}")
        return proc


def _runs(workspace: Path) -> list[Path]:
    root = workspace / ".autocode" / "runs"
    return [path.parent for path in root.glob("*/state.json")] if root.is_dir() else []

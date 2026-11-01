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
from typing import Any

try:
    from . import autocode_conversation_session as conversation_session
    from . import autocode_operational_change as operational_change
    from . import autocode_route_fit as route_fit
    from . import autocode_taskrun_worker as worker_driver
    from .autocode_captured_process import ProcessError
    from .autocode_captured_process import run as run_captured
    from .autocode_control_tokens import private_command
except ImportError:
    import autocode_conversation_session as conversation_session
    import autocode_operational_change as operational_change
    import autocode_route_fit as route_fit
    import autocode_taskrun_worker as worker_driver
    from autocode_captured_process import ProcessError
    from autocode_captured_process import run as run_captured
    from autocode_control_tokens import private_command

AUTOCODE = (sys.executable, str(Path(__file__).resolve().parent / "autocode.py"))


def run_process(argv, *, env=None, cwd=None, timeout=None, advancing=False) -> subprocess.CompletedProcess:
    """The one CLI-process launch (#704). The default is today's behavior exactly:
    advancing calls go through the captured-process supervisor (verified timeout
    cleanup), everything else is a plain captured run. Tests replace this one
    point with an in-process runner — e.g. one that calls the CLI's main()
    directly — so the driver stops paying interpreter startup per call. The
    supervision it triggers (keeper admission, receipts) still runs for real
    inside the CLI, whichever process runs it."""
    if advancing:
        return run_captured(argv, timeout=timeout, env=env, cwd=cwd)
    return subprocess.run(argv, capture_output=True, text=True, env=env, cwd=cwd, timeout=timeout)


class TaskRunError(RuntimeError):
    """AutoCode rejected a command, or could not run it.

    ``process`` is the CompletedProcess of the CLI call that failed, so a caller
    can keep its output and exit code. It is None when the error is not a failed
    call: a call that could not run or did not finish (a missing working
    directory, a timeout), ``attach`` finding several runs, or a guard such as
    ``advance_until_input``'s no-progress check, raised after its calls
    finished (``TaskRun.last_advance`` still holds the latest advancing call).
    ``run_dir`` is the run a failed ``start`` created, when it created exactly one.
    ``partial_output`` is the ``(stdout, stderr)`` that reached the caller before a call's
    time limit, whole, when it timed out (the message keeps only their ends); otherwise None.
    Output the CLI still held in its own buffer when it was stopped is not in it (#992).
    """

    def __init__(
        self,
        message: str,
        process: subprocess.CompletedProcess | None = None,
        run_dir: Path | None = None,
        partial_output: tuple[str, str] | None = None,
    ):
        super().__init__(message)
        self.process = process
        self.run_dir = run_dir
        self.partial_output = partial_output


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
    def start_durable(cls, workspace, run_dir, brief, *, options=(), command=AUTOCODE, env=None):
        """Return (handle, launch receipt) without waiting for the first human gate.

        Only an available core-named persistent Linux mode may be selected. Inspect
        the receipt until it binds (including effective bootstrap paths), then use
        ordinary status reads and dispatch_durable for the exact displayed action.
        Existing start()/advance() keep their synchronous owned-worker semantics.
        """
        workspace, run_dir = Path(workspace).resolve(), Path(run_dir).absolute()
        environment = {**os.environ, **(env or {})}
        reply = run_process([*command, "modes", "list", "--json"], env=environment)
        try:
            modes = json.loads(reply.stdout)["execution"]["modes"]
            execution = next(row for row in modes if row["id"] == "durable_local")
            receipt = worker_driver.dispatch(
                workspace,
                run_dir,
                ["--brief", brief, "--in-place", *options],
                execution=execution,
                fresh=True,
                command=command,
                env=environment,
                process=run_process,
            )
        except (ValueError, KeyError, TypeError, StopIteration, RuntimeError) as error:
            raise TaskRunError(str(error)) from error
        run = cls(workspace, run_dir, tuple(command), tuple(options), env)
        return run, receipt

    def dispatch_durable(self, command, *, view):
        """Dispatch the exact caller-inspected NamedCommand through this view's transport.

        Returns a launch receipt immediately; it does not claim completion or wait
        for a model. The core independently fences the checkpoint under its lock.
        A lost reply grants no repeat, recovery, approval or model substitution.
        """
        try:
            from . import autocode_client_actions as actions
        except ImportError:
            import autocode_client_actions as actions
        if not isinstance(command, actions.NamedCommand):
            raise TaskRunError("dispatch_durable requires a public NamedCommand")
        if actions.own(view, self.workspace, self.run_dir) != (self.workspace, self.run_dir):
            raise TaskRunError("Durable view belongs to another task handle")
        try:
            return worker_driver.dispatch(
                self.workspace,
                self.run_dir,
                command.argv,
                execution=view.get("execution") or {},
                command=self.command,
                env={**os.environ, **(self.env or {}), **command.env},
                process=run_process,
            )
        except (ValueError, RuntimeError) as error:
            raise TaskRunError(str(error)) from error

    def inspect_launch(self, receipt):
        """Read the receipt's named inspection action; adopts only its bound paths."""
        expected = ["autocode", "worker", "inspect", receipt.get("launch_id")]
        if receipt.get("inspect_command") != expected or not isinstance(expected[-1], str):
            raise TaskRunError("Unsupported worker inspection action")
        reply = run_process([*self.command, *expected[1:]], env={**os.environ, **(self.env or {})})
        try:
            observed = json.loads(reply.stdout)
            if reply.returncode or observed["launch_id"] != receipt["launch_id"]:
                raise ValueError("unknown launch")
            bound = observed.get("binding")
            if observed.get("ready") and bound:
                self.workspace, self.run_dir = Path(bound["workspace"]), Path(bound["run_dir"])
            return observed
        except (ValueError, KeyError, TypeError):
            raise TaskRunError("Worker inspection unavailable; no replay is authorized", reply) from None

    @classmethod
    def start(
        cls,
        workspace,
        brief: str,
        *,
        options=(),
        start_options=(),
        command=AUTOCODE,
        env=None,
        timeout=None,
        cwd=None,
        run_dir=None,
    ) -> TaskRun:
        """Create a run that works directly in ``workspace`` and advance it to its first stop.

        The caller owns the workspace (for example a worktree it created), so
        AutoCode does not create another one. Start one run per workspace at a time.
        ``start_options`` supplies inputs such as --ui-run only on this invocation;
        unlike ``options``, they are not repeated when advancing the saved run.
        ``run_dir`` optionally selects the exact fresh destination, an immediate
        child of ``workspace/.autocode/runs``. Existing destinations are refused
        before a provider launch; stage identities are never rebound by renaming.
        """
        workspace = Path(workspace).resolve()
        before = set(_runs(workspace))
        run = cls(workspace, Path(), tuple(command), tuple(options), env, timeout, _path(cwd))
        try:
            proc = run._invoke(
                "start",
                brief,
                "--in-place",
                "--no-chat",
                *run.options,
                *start_options,
                *(("--new-run-dir", str(Path(run_dir).absolute())) if run_dir is not None else ()),
                advancing=True,
                with_run_dir=False,
            )
        except TaskRunError as error:
            created = set(_runs(workspace)) - before
            if len(created) == 1:  # the start saved its run before it failed
                error.run_dir = created.pop()
            raise
        created = set(_runs(workspace)) - before
        if not created:
            raise TaskRunError(f"start exited {proc.returncode} without creating a run: {_detail(proc)}", proc)
        if len(created) != 1:
            raise TaskRunError(f"expected one new run in {workspace}, found {sorted(map(str, created))}", proc)
        run.run_dir = created.pop()
        if run_dir is not None and run.run_dir != Path(run_dir).absolute():
            raise TaskRunError("start did not create the requested fresh run destination", proc, run.run_dir)
        return run

    @classmethod
    def attach(
        cls, workspace, *, options=(), command=AUTOCODE, env=None, timeout=None, cwd=None, exclude=()
    ) -> TaskRun | None:
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

    @classmethod
    def start_conversation(
        cls,
        workspace,
        text: str,
        *,
        request_id: str,
        title: str | None = None,
        options=(),
        start_options=(),
        command=AUTOCODE,
        env=None,
        timeout=None,
        cwd=None,
    ) -> TaskRun:
        """Start a core-owned Requirements conversation and its one ordinary run."""
        return conversation_session.start(
            cls,
            workspace,
            text,
            request_id=request_id,
            title=title,
            options=options,
            start_options=start_options,
            command=command,
            env=env,
            timeout=timeout,
            cwd=cwd,
        )

    @classmethod
    def attach_conversation(
        cls,
        workspace,
        *,
        run_dir=None,
        options=(),
        command=AUTOCODE,
        env=None,
        timeout=None,
        cwd=None,
        exclude=(),
    ) -> TaskRun | None:
        """Reattach to a validated conversation without advancing or saving it."""
        if run_dir is None:
            run = cls.attach(
                workspace,
                options=options,
                command=command,
                env=env,
                timeout=timeout,
                cwd=cwd,
                exclude=exclude,
            )
        else:
            run = cls(
                Path(workspace).resolve(),
                Path(run_dir).resolve(),
                tuple(command),
                tuple(options),
                env,
                timeout,
                _path(cwd),
            )
        if run is not None:
            conversation_session.attach(run)
        return run

    def read_conversation(self) -> dict:
        """Read the retained conversation and current task-run view only."""
        return conversation_session.read(self)

    def send_conversation(
        self, text: str, *, request_id: str, expected_revision: int, supersedes_request_id: str | None = None
    ) -> dict:
        """Queue a human message at the inspected revision; do not dispatch a model."""
        return conversation_session.send(
            self,
            text,
            request_id=request_id,
            expected_revision=expected_revision,
            supersedes_request_id=supersedes_request_id,
        )

    def refresh_conversation(
        self,
        *,
        request_id: str,
        expected_revision: int,
        question_id: str | None = None,
        resolver_token: str | None = None,
    ) -> dict:
        """Apply the queued human batch and advance to the next ordinary input gate."""
        return conversation_session.refresh(
            self,
            request_id=request_id,
            expected_revision=expected_revision,
            question_id=question_id,
            resolver_token=resolver_token,
        )

    def handoff_conversation(self, *, expected_revision: int, expected_plan_token: str) -> dict:
        """Record the inspected handoff on the same run; never approve a plan."""
        return conversation_session.handoff(
            self,
            expected_revision=expected_revision,
            expected_plan_token=expected_plan_token,
        )

    def apply_conversation_request(
        self,
        request_path: Path,
        text: str,
        *,
        question_id: str | None = None,
        resolver_token: str | None = None,
    ) -> dict:
        """Save one sealed conversation action through the ordinary CLI boundary."""
        request_options = ("--conversation-request", str(request_path))
        if question_id is not None:
            return self._answer_action(
                question_id,
                text,
                resolver_token=resolver_token,
                request_options=request_options,
            )
        if resolver_token is not None:
            raise TaskRunError("A conversation answer token requires its question ID")
        return self._act("conversation feedback", *request_options, "--feedback", text)

    @staticmethod
    def runs_in(workspace) -> list[Path]:
        """The runs already saved in ``workspace``; see ``attach``'s ``exclude``."""
        return sorted(_runs(Path(workspace).resolve()))

    def status(self, *, inspect_evidence=False) -> dict:
        proc = self._invoke("status", "--status", *(("--inspect-evidence",) if inspect_evidence else ()))
        try:
            return json.loads(proc.stdout)["view"]
        except (ValueError, KeyError) as error:
            raise TaskRunError(f"--status did not return a status view: {error}", proc) from None

    def _saved_stage_deadline(self):
        """The run's saved stage deadline, from the status document's settings."""
        proc = self._invoke("status", "--status")
        try:
            return (json.loads(proc.stdout).get("settings") or {}).get("limits", {}).get("stage_timeout_seconds")
        except ValueError as error:
            raise TaskRunError(f"--status did not return a status document: {error}", proc) from None

    def acknowledge_attention(self, view: dict) -> dict:
        """Acknowledge the displayed alert; never answer, approve or advance a task."""
        try:
            from . import autocode_client_actions as actions
        except ImportError:
            import autocode_client_actions as actions
        try:
            action = actions.attention_acknowledgement(view.get("attention") or {}, self.workspace, self.run_dir)
            proc = run_process(
                [*self.command, *action.argv],
                env={**os.environ, **(self.env or {})},
                cwd=self.cwd,
                timeout=self.timeout or 5,
                advancing=False,
            )
            result = json.loads(proc.stdout)
            receipt = result.get("receipt") if isinstance(result, dict) else None
            item = view["attention"]["item"]
            if (
                proc.returncode
                or not isinstance(result, dict)
                or result.get("acknowledged") is not True
                or not isinstance(receipt, dict)
                or any(
                    receipt.get(field) != item.get("id" if field == "item_id" else field)
                    for field in ("item_id", "workspace", "run_dir")
                )
            ):
                raise TaskRunError("Attention acknowledgement was refused or its receipt is unconfirmed", proc)
            return result
        except (actions.ClientGap, OSError, ValueError, subprocess.TimeoutExpired) as error:
            raise TaskRunError(f"Attention acknowledgement outcome needs inspection: {error}") from error

    def evidence_report(self, *, require_current=True) -> dict:
        """Read the authenticated canonical pair through the public CLI only.

        Saved reports are historical; current delivery also requires the ordinary
        completion gate and read-only source/evidence inspection to remain valid.
        """
        view = self.status(inspect_evidence=True)
        report = view.get("evidence_report") or {}
        allowed = ("current",) if require_current else ("current", "recorded", "stale")
        if report.get("availability") not in allowed or not report.get("markdown") or not report.get("document"):
            raise TaskRunError(
                "Canonical evidence report is unavailable or not current: "
                + "; ".join(report.get("reasons") or ["Complete/revalidate the owned run first"])
            )
        return report

    def revise_design(
        self,
        manifest: Path,
        expected_hash: str,
        reason: str,
        *,
        approved_scope: Path | None = None,
        approved_scope_sha256: str | None = None,
    ) -> dict:
        """Propose a stopped run's design input correction; never approve or dispatch."""
        scope_flags: list[str] = []
        if approved_scope is not None:
            scope_flags.extend(("--figma-approved-scope", str(approved_scope)))
        if approved_scope_sha256 is not None:
            scope_flags.extend(("--figma-approved-scope-sha256", approved_scope_sha256))
        self._act(
            "revise design",
            "--revise-figma-manifest",
            str(manifest),
            "--expected-design-hash",
            expected_hash,
            "--design-change-reason",
            reason,
            *scope_flags,
        )
        return self.status()

    def show_goal(self) -> str:
        """Return the displayed brief a person must read before approving its token."""
        return self._invoke("show goal", "--show-goal").stdout

    def export_plan(self, path) -> str:
        """Write the run's current plan (shown for approval, or approved) to ``path`` as a plan file.

        `autocode plan --export`; returns the line it printed. It reads the run and changes nothing in it.
        """
        return self._invoke("export plan", "plan", "--export", str(Path(path).absolute())).stdout.strip()

    def advance(self) -> dict:
        """Relaunch the run; it works until it completes or stops for input."""
        return self._advance(self.status())

    def _advance(self, view: dict) -> dict:
        """Take this view's named continue action, or the ordinary ``--no-chat`` relaunch."""
        need = view.get("needs") or {}
        if need.get("kind") == "continue" and need.get("action") and view.get("next_command"):
            try:
                from . import autocode_client_actions as actions
            except ImportError:
                import autocode_client_actions as actions
            # A named recovery (--abandon-stage, --reconcile-only) is exactly the command the view
            # names; launch settings belong to a relaunch, and the CLI refuses them on these actions.
            named = actions.named(view, self.workspace, self.run_dir)
            self._invoke("advance", *named.argv, advancing=True, secret_env=named.env)
        else:
            self._invoke("advance", "--no-chat", *self.options, advancing=True, secret_env={})
        return self.status()

    def advance_until_input(self, max_calls: int = 20) -> dict:
        """Advance while the run needs nothing from the caller."""
        view = self.status()
        for _ in range(max_calls):
            if view["done"] or view["needs"]["kind"] != "continue":
                return view
            before = (view["status"], view["next_stage"], view["iteration"], view["phase"])
            view = self._advance(view)
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

    def save_stage_deadline(self, token: str, seconds: int) -> dict:
        """The first step of a stopped job's two-step deadline recovery: save a larger deadline alone.

        The CLI refuses a stage deadline beside a retry ("Save the stopped job's stage
        deadline separately, then retry with its fresh token"), and only an increase is
        admitted. The job stays paused and issues the fresh token its retry then takes.
        """
        self._invoke(
            "save stage deadline",
            "--job-retry-token",
            "-",
            "--max-stage-seconds",
            str(seconds),
            "--no-chat",
            secret_env={"AUTOCODE_JOB_RETRY_TOKEN": token},
        )
        fresh = (self.status().get("needs") or {}).get("job_retry_token")
        if not fresh:
            raise TaskRunError("save stage deadline: the run issued no fresh job retry token")
        return {"token": fresh, "view": self.status()}

    def retry_job(self, token: str) -> dict:
        """Retry exactly the inspected failed workflow job, retaining route and limits.

        Retaining them means this never re-asserts a stage deadline: the CLI refuses one
        beside a retry, so the saved limit stands and a differing one is rejected like any
        other change beside a retry. Raise it first with ``save_stage_deadline``.
        """
        options, deadline = _split_flag(self.options, "--max-stage-seconds")
        if deadline is not None and deadline != str(self._saved_stage_deadline()):
            raise TaskRunError(
                "retry job keeps the stopped job's configuration and limits; save a stage deadline on its own first"
            )
        self._invoke(
            "retry job",
            "--resume-paused",
            "--retry-failed-stage",
            "--job-retry-token",
            "-",
            "--no-chat",
            *options,
            advancing=True,
            secret_env={"AUTOCODE_JOB_RETRY_TOKEN": token},
        )
        return self.status()

    def recover_job_report(self, token: str) -> dict:
        """Adopt an inspected owner-lost Investigator report; never launch a model."""
        self._invoke(
            "recover job report",
            "--recover-job-report",
            "-",
            *self.options,
            secret_env={"AUTOCODE_RECOVER_JOB_REPORT": token},
        )
        return self.status()

    def grant_recovery(self, amount: int) -> dict:
        """Grant exactly N new recoveries after the operator resolves the pause cause."""
        if type(amount) is not int or amount < 1:
            raise ValueError("recovery allowance must be a positive integer")
        self._invoke(
            "grant recovery",
            "--resume-paused",
            "--grant-recovery",
            str(amount),
            "--no-chat",
            *self.options,
            advancing=True,
        )
        return self.status()

    def compare_checkpoint(self, checkpoint_id: str) -> dict:
        """Read a source-bound comparison; no execution, approval or file rewrite."""
        return json.loads(self._invoke("compare checkpoint", "checkpoint", "--compare", checkpoint_id).stdout)

    def restore_checkpoint(self, checkpoint_id: str, expected_token: str, request_id: str) -> TaskRun:
        """Create a paused continuation on a new branch; keep this run untouched."""
        result = json.loads(
            self._invoke(
                "restore checkpoint",
                "checkpoint",
                "--restore",
                checkpoint_id,
                "--expected-token",
                expected_token,
                "--request-id",
                request_id,
            ).stdout
        )
        return TaskRun(
            Path(result["workspace"]),
            Path(result["run_dir"]),
            self.command,
            self.options,
            self.env,
            self.timeout,
            self.cwd,
        )

    def accept_transport_change(self) -> dict:
        """Explicitly accept a validated OpenCode transport change and continue."""
        self._invoke(
            "accept transport change",
            "--resume-paused",
            "--accept-transport-change",
            "--no-chat",
            *self.options,
            advancing=True,
        )
        return self.status()

    def accept_source_edit(self) -> dict:
        """Hand a paused repair the source edited while it was stopped, and continue."""
        self._invoke(
            "accept source edit", "--resume-paused", "--accept-source-edit", "--no-chat", *self.options, advancing=True
        )
        return self.status()

    def retry_report(self, attempt_id: str) -> dict:
        """Request one fresh report for the exact inspected rejected attempt."""
        self._invoke("retry report", "--resume-paused", "--retry-report", attempt_id, "--no-chat", advancing=True)
        return self.status()

    def retry_failed_stage(self) -> dict:
        """Authorize one inspected retry of a repeated failed stage."""
        self._invoke("retry failed stage", "--resume-paused", "--retry-failed-stage", "--no-chat", advancing=True)
        return self.status()

    def bind_dependency(self, specification: Path) -> dict:
        return self._act("bind dependency", "--bind-dependency", str(specification))

    def assign_linked_scope(self, identity: str) -> dict:
        """Accept the core's exact repository assignment, never a plan approval."""
        return self._act("assign linked scope", "--assign-linked-scope", identity)

    def receive_dependency(self, manifest: Path) -> dict:
        return self._act("receive dependency", "--receive-dependency", str(manifest))

    def answer(self, question_id: str, text: str, *, resolver_token: str | None = None) -> dict:
        """Answer one question of the run's current AutoResolver request.

        Each answer consumes that request, and the questions left return under a
        new token. Pass ``resolver_token`` from the view the answer was chosen from;
        a stale one is rejected, never refreshed. Without it, the current request's
        token is used after checking that the request lists ``question_id``.
        """
        return self._answer_action(question_id, text, resolver_token=resolver_token)

    def _answer_action(
        self,
        question_id: str,
        text: str,
        *,
        resolver_token: str | None = None,
        request_options=(),
    ) -> dict:
        has_timeout_override = any(
            item.split("=", 1)[0] in ("--max-tool-seconds", "--max-stage-seconds") for item in self.options
        )
        need = self.status()["needs"] or {} if resolver_token is None or has_timeout_override else {}
        if resolver_token is None:
            listed = [question["id"] for question in need.get("questions") or ()]
            if need.get("kind") != "answer" or question_id not in listed:
                raise TaskRunError(
                    f"the run is not waiting for an answer to {question_id} "
                    f"(needs {need.get('kind')}, questions {listed})"
                )
            resolver_token = need.get("resolver_token")
            if not resolver_token:
                route = need.get("route") or {}
                if (
                    not request_options
                    and route.get("cause") == "cross_model"
                    and route.get("question_id") == question_id
                ):
                    # Derived from current saved routes: the core rechecks independence
                    # and availability; no AutoResolver request or token exists.
                    return self._act("answer", "--answer", f"{question_id}={text}")
                raise TaskRunError(
                    f"no current AutoResolver request carries {question_id}; "
                    "advance the run to publish one, then answer"
                )
        if (
            has_timeout_override
            and need
            and need.get("operational_options")
            and (resolver_token != need.get("resolver_token"))
        ):
            raise TaskRunError("The operational timeout decision token is stale; use the current displayed request")
        view = self._act(
            "answer",
            *request_options,
            "--answer",
            f"{question_id}={text}",
            "--resolver-token",
            "-",
            secret_env={"AUTOCODE_RESOLVER_TOKEN": resolver_token},
        )
        if (
            need
            and need.get("resolver_token") == resolver_token
            and any(question.get("id") == question_id for question in need.get("questions") or ())
        ):
            selected = next((row for row in need.get("operational_options", []) if row["option"] == text), None)
            if selected is not None:
                self.options = operational_change.update_overrides(self.options, selected["changes"])
        return view

    def assign_model(
        self, role: str, model: str, *, resolver_token: str | None = None, job_retry_token: str | None = None
    ) -> dict:
        """Name the model a role stopped on quota or a refusal continues on (``needs.route``), then resume_paused().

        The same answer as ``answer(f"route-{role}", model)``: AutoCode refuses a model the launch
        would refuse (engine, format, availability, cross-model) and leaves the run paused. A
        ``--<role>-model`` in ``options`` is updated in split or equals form, including repeats,
        so advancing never passes the old model back.

        At a stopped workflow job (``needs.kind`` ``retry_job`` with ``route``) the answer carries
        the job retry token instead (``job_retry_token``, or the current view's) and issues a new
        one: continue with ``retry_job(view["needs"]["job_retry_token"])``.
        """
        if job_retry_token is None and resolver_token is None:
            need = self.status()["needs"] or {}
            if need.get("kind") == "retry_job" and (need.get("route") or {}).get("role") == role:
                job_retry_token = need.get("job_retry_token")
        if job_retry_token is not None:
            view = self._act(
                "assign model",
                "--answer",
                f"route-{role}={model}",
                "--job-retry-token",
                "-",
                secret_env={"AUTOCODE_JOB_RETRY_TOKEN": job_retry_token},
            )
        else:
            view = self.answer(f"route-{role}", model, resolver_token=resolver_token)
        flag = "--investigator-model" if role == "stuck_investigator" else "--" + role.replace("_", "-") + "-model"
        options = list(self.options)
        # --investigator-model pins the stuck-stage Investigator, not the bug Investigator's route.
        if role != "investigator":
            for index, option in enumerate(options):
                if option == flag and index + 1 < len(options):
                    options[index + 1] = model
                elif option.startswith(flag + "="):
                    options[index] = flag + "=" + model
            self.options = tuple(options)
        return view

    def assign_effort(self, role: str, effort: str) -> dict:
        """Choose a level the model declares for a role whose named effort it does not (``needs.effort``, #2057).

        The same answer as ``--answer effort-ROLE=EFFORT``: AutoCode refuses a level the model does not
        declare and leaves the run paused. It saves without launching; continue with resume_paused().
        A ``--<role>-reasoning-effort`` in ``options`` is updated, so advancing never passes the old level back.
        A global ``--reasoning-effort`` would set every role again on resume, so the role's own flag, which
        wins over it, is added beside it.
        """
        view = self._act("assign effort", "--answer", f"effort-{role}={effort}")
        flag = route_fit.flag(role)
        if flag == "--reasoning-effort":
            return view  # a role without its own flag: the global one is the person's to change
        self.options = tuple(
            effort
            if index and self.options[index - 1] == flag
            else f"{flag}={effort}"
            if option.startswith(flag + "=")
            else option
            for index, option in enumerate(self.options)
        )
        named = any(option == flag or option.startswith(flag + "=") for option in self.options)
        if not named and any(o == "--reasoning-effort" or o.startswith("--reasoning-effort=") for o in self.options):
            self.options = (*self.options, f"{flag}={effort}")
        return view

    def respond_operational(self, request_id: str, request_token: str, text: str) -> dict:
        """Send corrective information to the published AutoResolver request."""
        return self._act(
            "resolver response",
            "--resolver-request",
            request_id,
            "--resolver-token",
            "-",
            "--resolver-response",
            "provide_information",
            "--resolver-message",
            text,
            secret_env={"AUTOCODE_RESOLVER_TOKEN": request_token},
        )

    def approve_plan(self, token: str, *, changeset_token: str | None = None) -> dict:
        """Approve this plan; a set preparation keeps it held until every member acknowledges."""
        options = ("--changeset-token", "-") if changeset_token else ()
        environment = {"AUTOCODE_APPROVE_GOAL_TOKEN": token}
        if changeset_token:
            environment["AUTOCODE_CHANGESET_TOKEN"] = changeset_token
        return self._act("approve plan", "--approve-goal", "-", *options, secret_env=environment)

    def approve_review(self, criterion: str, token: str) -> dict:
        return self._act(
            "approve review",
            "--approve-review",
            criterion,
            "--review-token",
            "-",
            secret_env={"AUTOCODE_REVIEW_TOKEN": token},
        )

    def reject_review(self, criterion: str, token: str, note: str) -> dict:
        """Save a correction on the displayed artifact; a refreshed plan needs approval."""
        return self._act(
            "reject review",
            "--reject-review",
            criterion,
            "--review-token",
            "-",
            "--review-note",
            note,
            secret_env={"AUTOCODE_REVIEW_TOKEN": token},
        )

    def feedback(self, text: str) -> dict:
        return self._act("feedback", "--feedback", text)

    def follow_up(self, text: str) -> dict:
        """Say the next thing to a finished run; continue it afterwards."""
        return self._act("follow-up", "--follow-up", text)

    def _act(self, name: str, *args: str, secret_env: dict[str, str] | None = None) -> dict:
        """User actions exit 0 once saved; anything else means AutoCode rejected them."""
        self._invoke(name, *args, secret_env=secret_env)
        return self.status()

    def _invoke(
        self,
        name: str,
        *args: str,
        advancing: bool = False,
        with_run_dir: bool = True,
        secret_env: dict[str, str] | None = None,
    ):
        try:
            arguments, private_env = private_command([*args, "--workspace", str(self.workspace)])
        except ValueError as error:
            raise TaskRunError(f"{name}: {error}") from error
        cmd = [*self.command, *arguments]
        if with_run_dir:
            cmd += ["--run-dir", str(self.run_dir)]
        where = {"cwd": self.cwd} if self.cwd is not None else {}
        options: dict[str, Any] = {**where}
        if self.timeout is not None:
            options["timeout"] = self.timeout
        try:
            # secret_env carries authorization tokens in the child's environment
            # instead of argv: /proc/<pid>/cmdline is world-readable, environ is not.
            environment = {**os.environ, **(self.env or {}), **(secret_env or {}), **private_env}
            proc = run_process(cmd, env=environment, advancing=advancing, **options)
        except subprocess.TimeoutExpired as error:
            # What reached us before the limit is the only evidence of how far the call got (#992).
            partial = (_text(error.stdout), _text(error.stderr))
            raise TaskRunError(
                f"{name} did not finish within {self.timeout} s; {_partial_detail(*partial)}", partial_output=partial
            ) from None
        except ProcessError as error:
            raise TaskRunError(f"{name} could not supervise its processes: {error}") from error
        except OSError as error:  # e.g. a working directory or workspace that was removed
            raise TaskRunError(f"{name} could not run: {error}") from error
        if advancing:
            self.last_advance = proc
        # Advancing exits 0 when complete and 2 when stopped for input. Usage errors
        # also exit 2, so recognize argparse's message rather than trusting the code.
        usage_error = proc.returncode == 2 and proc.stderr.startswith("usage:")
        rejected_input = proc.returncode == 2 and any(
            line.startswith(REFUSALS) for message in (proc.stdout, proc.stderr) for line in message.splitlines()
        )
        accepted = proc.returncode in (0, 2) if advancing else proc.returncode == 0
        if usage_error or rejected_input or not accepted:
            raise TaskRunError(f"{name} exited {proc.returncode}: {_detail(proc)}", proc)
        return proc


def _split_flag(options, flag):
    """``options`` without ``flag``, and the value it carried (None when it is absent)."""
    kept: list[str] = []
    value = None
    pending = False
    for item in options:
        if pending:
            value = item
            pending = False
            continue
        name, sep, inline = item.partition("=")
        if name != flag:
            kept.append(item)
            continue
        if sep:
            value = inline
        else:
            pending = True
    return tuple(kept), value


# The lines a refusal starts with: AutoCode's own, and argparse's usage error (PROG: error: ...).
REFUSALS = ("Input rejected:", "autocode:")
DETAIL_CHARACTERS = 800


def _detail(proc) -> str:
    """The process's output for an error message, at most DETAIL_CHARACTERS long: its end, as before, but
    a refusal keeps its reason line first, since a long recovery command after it would push it out (#1032).
    The whole output stays on the error's ``process``."""
    text = (proc.stderr or proc.stdout or "").strip()
    reason = next(
        (
            line.strip()
            for stream in (proc.stderr, proc.stdout)
            for line in (stream or "").splitlines()
            if line.startswith(REFUSALS)
        ),
        None,
    )
    if reason is None or (len(text) <= DETAIL_CHARACTERS and reason in text):
        return text[-DETAIL_CHARACTERS:]
    reason = reason[:DETAIL_CHARACTERS]
    rest = text.replace(reason, "", 1).strip()
    room = DETAIL_CHARACTERS - len(reason) - len(" … ")
    return reason + (" … " + rest[-room:] if rest and room > 0 else "")


def _text(output) -> str:
    """A timed-out call's partial stream: bytes from a plain captured run, text from the supervisor."""
    return output.decode(errors="replace") if isinstance(output, bytes) else output or ""


def _partial_detail(stdout: str, stderr: str) -> str:
    """The end of each stream that reached the caller from a timed-out call, at most DETAIL_CHARACTERS
    in all, for its error message; when none did, it says so. It does not claim the call printed
    nothing: a Python CLI's stdout to a pipe is block-buffered, and a killed call can lose that buffer (#992)."""
    streams = [(label, text.strip()) for label, text in (("stdout", stdout), ("stderr", stderr)) if text.strip()]
    if not streams:
        return "no output reached the caller before the limit"
    room = DETAIL_CHARACTERS // len(streams)
    return "; ".join(f"{label} before the limit ended: {text[-room:]}" for label, text in streams)


def _path(value) -> Path | None:
    return None if value is None else Path(value)


def _runs(workspace: Path) -> list[Path]:
    root = workspace / ".autocode" / "runs"
    return [path.parent for path in root.glob("*/state.json")] if root.is_dir() else []

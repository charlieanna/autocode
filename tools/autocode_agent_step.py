"""AutoCode as one step inside a coding agent the person already runs (#693).

A Claude Code, Codex or Cursor session starts an AutoCode run in its repository,
reads where the run stands, passes on the person's plan approval or answer, and
reports the evidence. The Claude Code skill (examples/agent-step) runs this
module's command line; the MCP server (autocode_mcp) calls the same four
operations: ``start``, ``status``, ``respond`` and ``evidence``. Both reach a run
only through the task-run interface: ``TaskRun`` and the status view it returns
(docs/task-run.md). Nothing here imports the runner or reads a run's saved state
(AGENTS.md rule 5), and nothing outside the standard library is needed.

Every operation returns one summary: the run directory, the status view's own
fields that say what happens next (``SUMMARY_FIELDS``, copied unchanged), the
progress line and, while a plan waits for approval, the plan a person reads
before approving it. Decisions stay with the person. ``respond`` approves only
the plan token and answers only the question token it is given, which the agent
copies from the view the person saw; it refuses every other kind of stop (a
review, a recovery, a budget, an operational request) and names the command
the person runs instead. Continuing a run that needs nothing from anyone is the
only step it takes without a decision.

    python -m autocode_cli.autocode_agent_step start "Fix the off-by-one in pager.py" -- --engine claude
    python -m autocode_cli.autocode_agent_step status
    AUTOCODE_APPROVE_GOAL_TOKEN=... python -m autocode_cli.autocode_agent_step approve -
    python -m autocode_cli.autocode_agent_step evidence --markdown
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .autocode_taskrun import TaskRun, TaskRunError
except ImportError:
    from autocode_taskrun import TaskRun, TaskRunError

# The job kinds --workflow names (autocode_workflows.WORKFLOWS; a test keeps the two equal).
WORKFLOWS = ("build", "bugfix", "review", "design", "discuss")
# The status view's fields an agent acts on, copied unchanged (docs/task-run.md#status-view).
SUMMARY_FIELDS = (
    "status",
    "done",
    "pause_category",
    "pause_category_label",
    "next_command",
    "needs",
    "workflow",
    "stop_reason",
)
# What respond() can do: each is the needs.kind it answers.
ACTIONS = ("approve_plan", "answer", "continue")
# AutoResolver requests about recovering the run, not questions about the work: the person's (docs/task-run.md).
OPERATIONAL_SCOPES = ("blocker", "operational_exhaustion")
# The variables a "-" token argument is read from: the same ones the view's next_command uses.
APPROVE_TOKEN_VARIABLE = "AUTOCODE_APPROVE_GOAL_TOKEN"
RESOLVER_TOKEN_VARIABLE = "AUTOCODE_RESOLVER_TOKEN"


class StepError(RuntimeError):
    """The step refused a call, or AutoCode rejected it. The message says what to do instead."""


@dataclass
class AgentStep:
    """One agent's access to the AutoCode runs in one workspace."""

    workspace: Path
    # Engine, model and budget flags for a new run. Later calls use the run's saved settings.
    options: tuple[str, ...] = ()
    # Recorded as each call's caller in the run's activity log (autocode_activity_log).
    caller: str = "agent-step"
    env: dict[str, str] | None = None
    timeout: float | None = None
    # The run this agent started or last named; the default for the calls after it.
    run_dir: Path | None = None

    def __post_init__(self):
        self.workspace = Path(self.workspace).expanduser().resolve()
        self.options = tuple(self.options)

    def start(self, brief: str, *, workflow: str | None = None) -> dict:
        """Start a run on ``brief`` and work until it needs a person or completes."""
        if not isinstance(brief, str) or not brief.strip():
            raise StepError("start needs the request: what to build, fix, review, design or discuss")
        if workflow is not None and workflow not in WORKFLOWS:
            raise StepError(f"workflow must be one of {', '.join(WORKFLOWS)}, not {workflow!r}")
        try:
            run = TaskRun.start(
                self.workspace,
                brief,
                options=self.options,
                start_options=("--workflow", workflow) if workflow else (),
                env=self._env(),
                timeout=self.timeout,
            )
        except TaskRunError as error:
            if error.run_dir is not None:
                self.run_dir = error.run_dir
                raise StepError(f"{error} (run: {error.run_dir})") from None
            raise StepError(str(error)) from None
        self.run_dir = run.run_dir
        return self._summary(run, _call(run.advance_until_input))

    def status(self, run_dir: str | Path | None = None, *, full: bool = False) -> dict:
        """Where the run stands. Reads only; ``full`` adds the whole status view."""
        run = self._run(run_dir)
        return self._summary(run, _call(run.status), full=full)

    def respond(
        self,
        action: str,
        *,
        token: str | None = None,
        question_id: str | None = None,
        text: str | None = None,
        run_dir: str | Path | None = None,
    ) -> dict:
        """Pass on the person's decision, or continue a run that needs none, then work until the next stop.

        ``approve_plan`` takes ``needs.token`` and ``answer`` takes ``needs.resolver_token`` from the
        view the person decided on; AutoCode refuses a token for anything but that exact plan or request.
        """
        if action not in ACTIONS:
            raise StepError(f"action must be one of {', '.join(ACTIONS)}, not {action!r}")
        run = self._run(run_dir)
        view = _call(run.status)
        refusal = _not_now(view, action)
        if refusal:
            raise StepError(refusal)
        if action == "approve_plan":
            if not token:
                raise StepError("approve_plan needs needs.token from the view whose plan the person approved")
            _call(lambda: run.approve_plan(token))
        elif action == "answer":
            if not question_id or not token or not isinstance(text, str) or not text.strip():
                raise StepError(
                    "answer needs question_id, the person's answer as text, and needs.resolver_token "
                    "from the view the person answered"
                )
            answered = _call(lambda: run.answer(question_id, text, resolver_token=token))
            if (answered.get("needs") or {}).get("kind") != "continue":
                return self._summary(run, answered)  # the questions left, under a new token, or another stop
        return self._summary(run, _call(run.advance_until_input))

    def evidence(self, run_dir: str | Path | None = None) -> dict:
        """The completed run's canonical evidence report.

        A build's report must still match the workspace (``current``). A job that only reports, such as a
        review (the report's ``workflow_result``), delivers no build to keep current: its authenticated
        report as recorded is its evidence, as ``autocode review-pr`` reads it. A stale report is refused.
        """
        run = self._run(run_dir)
        report = _call(lambda: run.evidence_report(require_current=False))
        reported = bool((report.get("document") or {}).get("workflow_result"))
        if report.get("availability") not in (("current", "recorded") if reported else ("current",)):
            raise StepError(
                "Canonical evidence report is unavailable or not current: "
                + "; ".join(report.get("reasons") or ["Complete/revalidate the owned run first"])
            )
        return {
            "run_dir": str(run.run_dir),
            "availability": report.get("availability"),
            "markdown": report.get("markdown"),
            "markdown_path": report.get("markdown_path"),
            "json_path": report.get("json_path"),
        }

    def _run(self, run_dir: str | Path | None) -> TaskRun:
        runs = TaskRun.runs_in(self.workspace)
        chosen = run_dir or self.run_dir
        if chosen is None:
            if not runs:
                raise StepError(f"{self.workspace} has no AutoCode run yet; start one")
            if len(runs) > 1:
                raise StepError(f"name the run: {self.workspace} has {len(runs)} runs ({', '.join(map(str, runs))})")
            named = runs[0]
        else:
            named = Path(chosen).expanduser().resolve()
            if named not in {path.resolve() for path in runs}:
                raise StepError(f"{named} is not a run in {self.workspace}")
        self.run_dir = named
        return TaskRun(self.workspace, named, env=self._env(), timeout=self.timeout)

    def _env(self) -> dict[str, str]:
        return {**(self.env or {}), "AUTOCODE_CALLER": self.caller}

    def _summary(self, run: TaskRun, view: dict, *, full: bool = False) -> dict:
        summary: dict[str, Any] = {"run_dir": str(run.run_dir)}
        summary.update((name, view.get(name)) for name in SUMMARY_FIELDS)
        summary["progress"] = (view.get("progress") or {}).get("line")
        summary["explanation"] = (view.get("explanation") or {}).get("explanation")
        if (view.get("needs") or {}).get("kind") == "approve_plan":
            summary["plan"] = _call(run.show_goal)
        if full:
            summary["view"] = view
        return summary


def _not_now(view: dict, action: str) -> str | None:
    """Why ``action`` is not this stop's, or None when it is."""
    need = view.get("needs") or {}
    kind = need.get("kind")
    if view.get("done"):
        return f"The run has finished ({view.get('status')}); read its evidence."
    if kind == action and not (kind == "answer" and need.get("resolver_scope") in OPERATIONAL_SCOPES):
        return None
    if kind == "continue":
        return "The run needs nothing from anyone: continue it."
    shown = f"{view.get('pause_category_label') or 'a stop'} ({view.get('status')}, needs {kind})"
    command = view.get("next_command")
    return f"The run is waiting on {shown}, which is the person's to decide. Show them the status" + (
        f" and the command they run: {command}" if command else "."
    )


def _call(function: Callable[[], Any]) -> Any:
    try:
        return function()
    except TaskRunError as error:
        raise StepError(str(error)) from None


def _token(value: str, variable: str) -> str:
    if value != "-":
        return value
    token = os.environ.get(variable, "")
    if not token:
        raise StepError(f"'-' reads the token from {variable}, which is not set")
    return token


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        prog="python -m autocode_cli.autocode_agent_step",
        description="Start, read, answer and report on an AutoCode run from another coding agent. "
        "Prints one JSON summary (docs/agent-step.md).",
        allow_abbrev=False,
    )
    command.add_argument("--workspace", default=".", help="the repository the run works in (default: here)")
    command.add_argument("--run-dir", help="the run to act on (default: the workspace's only run)")
    steps = command.add_subparsers(dest="step", required=True)
    start = steps.add_parser("start", help="start a run; engine, model and budget flags follow --")
    start.add_argument("brief", help="the request; '-' reads it from standard input")
    start.add_argument("--workflow", choices=WORKFLOWS, help="name the kind of job instead of recognizing it")
    status = steps.add_parser("status", help="where the run stands (reads only)")
    status.add_argument("--full", action="store_true", help="add the whole status view")
    approve = steps.add_parser("approve", help="approve the displayed plan, then continue")
    approve.add_argument(
        "token", help=f"needs.token from the view the person approved; '-' reads {APPROVE_TOKEN_VARIABLE}"
    )
    answer = steps.add_parser("answer", help="answer one question, then continue once none are left")
    answer.add_argument("question_id")
    answer.add_argument("text", help="the person's answer")
    answer.add_argument(
        "--token", required=True, help=f"needs.resolver_token from the view; '-' reads {RESOLVER_TOKEN_VARIABLE}"
    )
    steps.add_parser("continue", help="continue a run that needs nothing from anyone")
    evidence = steps.add_parser("evidence", help="the completed run's evidence report")
    evidence.add_argument("--markdown", action="store_true", help="print the report itself instead of JSON")
    return command


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    options: list[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, options = argv[:at], argv[at + 1 :]
    arguments = parser().parse_args(argv)
    if options and arguments.step != "start":
        print("autocode-agent-step: engine options follow -- only on start", file=sys.stderr)
        return 2
    run_dir = Path(arguments.run_dir) if arguments.run_dir else None
    step = AgentStep(Path(arguments.workspace), tuple(options), run_dir=run_dir)
    try:
        if arguments.step == "start":
            brief = sys.stdin.read() if arguments.brief == "-" else arguments.brief
            result = step.start(brief, workflow=arguments.workflow)
        elif arguments.step == "status":
            result = step.status(full=arguments.full)
        elif arguments.step == "approve":
            result = step.respond("approve_plan", token=_token(arguments.token, APPROVE_TOKEN_VARIABLE))
        elif arguments.step == "answer":
            token = _token(arguments.token, RESOLVER_TOKEN_VARIABLE)
            result = step.respond("answer", token=token, question_id=arguments.question_id, text=arguments.text)
        elif arguments.step == "continue":
            result = step.respond("continue")
        else:
            result = step.evidence()
            if arguments.markdown:
                print(result["markdown"])
                return 0
    except StepError as error:
        print(f"autocode-agent-step: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

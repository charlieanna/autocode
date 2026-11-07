"""Starting durable runs for issues, and taking a channel reply's action on one.

The bridge's host-side hands: it provisions the issue worktree and records exactly
as ``autocode issue start`` does (so ``autocode issue pr`` and ``status`` keep
working for a person), but launches the run through the durable worker envelope
instead of an owned foreground process. Every decision is taken against a fresh
status view through ``TaskRun``'s public methods, with each token read from that
view; nothing here approves, answers or resumes on its own. An advancing command
on a durable run goes back through the same envelope, so the systemd user service
owns the controller, not the bridge.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

try:
    from . import autocode_bridge_store as store
    from . import autocode_client_actions as actions
    from . import autocode_github as github
    from . import autocode_issue as issue_tool
    from .autocode_bridge_relay import ReplyAction
    from .autocode_taskrun import AUTOCODE, TaskRun, TaskRunError, run_process
except ImportError:
    import autocode_bridge_store as store
    import autocode_client_actions as actions
    import autocode_github as github
    import autocode_issue as issue_tool
    from autocode_bridge_relay import ReplyAction
    from autocode_taskrun import AUTOCODE, TaskRun, TaskRunError, run_process


class BridgeError(RuntimeError):
    """A refusal with a message for the channel; the run is unchanged."""


# A human or run action may never ride in as a launch option and be replayed by a label
# or a continuation (the same list examples/github-issue/issue_action.py enforces).
FORBIDDEN_OPTIONS = {
    "--answer",
    "--delegate",
    "--delegate-all",
    "--reject-assumption",
    "--feedback",
    "--follow-up",
    "--edit-goal",
    "--goal-file",
    "--reconcile-review",
    "--review-token",
    "--close-finding",
    "--close-reason",
    "--accept-completion",
    "--resume-paused",
    "--resolver-response",
    "--resolver-request",
    "--resolver-token",
    "--resolver-message",
    "--retry-failed-stage",
    "--retry-builder",
    "--diagnose-failed-stage",
    "--retry-report",
    "--grant-recovery",
    "--accept-source-edit",
    "--accept-transport-change",
    "--abandon-stage",
    "--recover-job-report",
    "--request-milestone-checkpoints",
    "--planning-review-call-limit",
    "--bind-dependency",
    "--receive-dependency",
    "--show-goal",
    "--status",
    "--dry-run",
    "--migrate-only",
    "--no-requirements",
    "--workspace",
    "--run-dir",
    "--in-place",
    "--ui-run",
    "--adaptive-planning",
}


def provider_options(words: list[str]) -> list[str]:
    """Launch options for a bridge-started run, refusing every human or run action."""
    for word in words:
        option = word.split("=", 1)[0]
        if option.startswith("--approve-") or option in FORBIDDEN_OPTIONS:
            raise BridgeError(f"{option} is a human or run action, not a bridge launch option")
    return list(words)


def durable_availability(command=AUTOCODE) -> dict:
    """The ``durable_local`` row from ``modes list --json``, for admission checks."""
    import json

    reply = run_process([*command, "modes", "list", "--json"])
    try:
        modes = json.loads(reply.stdout)["execution"]["modes"]
        return next(row for row in modes if row["id"] == "durable_local")
    except (ValueError, KeyError, TypeError, StopIteration):
        return {"id": "durable_local", "available": False, "reason": "modes list --json is unreadable"}


def start_issue_run(
    project: Path,
    ref: github.IssueRef,
    issue: dict,
    options: list[str],
    *,
    launch=None,
    run_name: str | None = None,
    owned: bool = False,
) -> dict:
    """Provision the issue worktree and start a durable run on it; the bridge record fields."""
    project = Path(project).resolve()
    options = provider_options(list(options))
    if issue_tool.record_path(project, ref).exists():
        raise BridgeError(f"{ref} already has an issue record; finish or remove the earlier attempt first")
    base = issue_tool.git(project, "rev-parse", "--verify", "HEAD^{commit}")
    pr_base = issue_tool.git(project, "symbolic-ref", "--short", "-q", "HEAD", check=False) or None
    title = (issue.get("title") or f"Issue {ref.number}").strip()
    words = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].rstrip("-")
    branch = f"autocode/issue-{ref.number}" + (f"-{words}" if words else "")
    worktree = project / issue_tool.STORE / ref.slug
    if issue_tool.git(project, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False):
        raise BridgeError(f"branch {branch} already exists; delete it or finish the earlier attempt")
    issue_tool.git(project, "worktree", "add", "-q", "-b", branch, str(worktree), base)
    run_dir = worktree / ".autocode" / "runs" / (run_name or f"issue-{ref.number}-{time.strftime('%Y%m%d-%H%M%S')}")
    record = {
        "version": 1,
        "slug": ref.slug,
        "owner": ref.owner,
        "repo": ref.repo,
        "number": ref.number,
        "title": title,
        "url": issue.get("html_url"),
        "project": str(project),
        "worktree": str(worktree),
        "branch": branch,
        "base_commit": base,
        "pr_base": pr_base,
        "remote": "origin",
        "options": options,
        "run_dir": str(run_dir),
        "pr_url": None,
    }
    issue_tool.save(project, record)  # before the launch, so a failed start leaves a trace
    try:
        starter = launch or (_launch_owned if owned else _launch_durable)
        run, receipt = starter(worktree, run_dir, issue_tool.brief(ref, issue), options)
    except (TaskRunError, RuntimeError, OSError) as error:
        raise BridgeError(f"the {'owned' if owned else 'durable'} launch was refused: {error}") from error
    issue_tool.save(project, record)
    bridge_record = {
        "owner": ref.owner,
        "repo": ref.repo,
        "number": ref.number,
        "title": title,
        "url": issue.get("html_url"),
        "project": str(project),
        "worktree": str(worktree),
        "branch": branch,
        "run_dir": str(run.run_dir or run_dir),
        "options": options,
        "receipt": receipt,
        "ready": bool(receipt.get("ready")),
        "owned": bool(owned),
        "gate": {"key": "starting"},
        "delivered": False,
        "pr_url": None,
    }
    store.save(bridge_record)  # the provisioning persists, restart or not
    return bridge_record


def _launch_durable(worktree, run_dir, brief, options):
    return TaskRun.start_durable(Path(worktree), Path(run_dir), brief, options=tuple(options))


def _launch_owned(worktree, run_dir, brief, options):
    """The explicit no-systemd mode: the run lives with the daemon process, as the GitHub Action's runs do."""
    return TaskRun.start(Path(worktree), brief, options=tuple(options), run_dir=Path(run_dir)), {}


def adopt(record: dict, command=AUTOCODE) -> TaskRun:
    """The saved run as a TaskRun handle, durable or owned alike."""
    return TaskRun(
        Path(record["worktree"]), Path(record["run_dir"]), command=command, options=tuple(record.get("options") or ())
    )


def inspect_launch(record: dict, run: TaskRun) -> dict:
    """Re-read the launch receipt until it binds; adoption happens in inspect_launch itself."""
    receipt = record.get("receipt") or {}
    if not receipt.get("launch_id"):
        raise BridgeError("the bridge record carries no launch receipt; inspect it on the host")
    return run.inspect_launch(receipt)


def advancing(view: dict) -> bool:
    """Whether this view's named command continues the run (and so needs the worker envelope on a durable run)."""
    return ((view.get("needs") or {}).get("kind") in ("continue", "resume", "retry_job")) or bool(
        view.get("next_command")
    )


def dispatch(run: TaskRun, view: dict, command: actions.NamedCommand) -> None:
    """One named command on the run: through the worker envelope when the run is durable."""
    if (view.get("execution") or {}).get("mode") == "durable_local":
        run.dispatch_durable(command, view=view)
    else:  # an owned run (a test or a host without the durable service): the typed call advances it
        run.advance() if command.argv == ["--no-chat"] else run.resume_paused()


def answer_all(run: TaskRun, answers: dict[str, str]) -> str:
    """Answer each open question in its own invocation; every answer reissues the request's token."""
    answered = []
    for _ in range(len(answers)):
        view = run.status()
        need = view.get("needs") or {}
        if need.get("kind") != "answer":
            break
        open_ids = [str(row.get("id")) for row in need.get("questions") or ()]
        wanted = next((key for key in answers if key in open_ids), None)
        if wanted is None:
            break  # the remaining answers name questions this request no longer carries
        run.answer(wanted, answers[wanted], resolver_token=need.get("resolver_token"))
        answered.append(wanted)
    if not answered:
        raise BridgeError("the run no longer carries those questions; the current gate is being relayed")
    missing = [key for key in answers if key not in answered]
    note = "Answered " + ", ".join(answered) + "."
    return note + (f" Still open, answer again: {', '.join(missing)}." if missing else "")


CHANGESET_TIMEOUT = 300  # changeset status and approve read, and approve, every run of the set


def _changeset_cli(run: TaskRun, argv: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, **run.env} if run.env else None
    try:
        return run_process([*run.command, *argv], env=env, timeout=CHANGESET_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise BridgeError("the change set did not answer in time; it is relayed again on the next poll") from None


def changeset_summary(run: TaskRun, view: dict) -> dict | None:
    """``changeset status --json`` for a plan gate of a run in a change set, else None; a set that
    cannot be read is relayed as this run's plan alone."""
    if (view.get("needs") or {}).get("kind") != "approve_plan" or not (view.get("scope") or {}).get("linked"):
        return None
    argv = actions.changeset_status(view, run.workspace, run.run_dir)
    if argv is None:
        return None
    try:
        reply = _changeset_cli(run, argv)
        summary = json.loads(reply.stdout)
    except (BridgeError, OSError, ValueError):
        return None
    return summary if reply.returncode == 0 and isinstance(summary, dict) and "error" not in summary else None


def approve_set(run: TaskRun, view: dict, shown_token: str) -> str:
    """The change set's named set approval, only for the set token this issue was shown."""
    summary = changeset_summary(run, view)
    token = ((summary or {}).get("approval") or {}).get("set_token")
    if not summary or not token or not shown_token or token != shown_token:
        raise BridgeError(
            "that set approval matches no change set shown here; the current plans are relayed for a fresh decision"
        )
    try:
        argv = actions.changeset_action(summary, "approval")
    except actions.ClientGap as error:
        raise BridgeError(str(error)) from None
    reply = _changeset_cli(run, argv)
    if reply.returncode != 0:
        try:
            message = json.loads(reply.stdout).get("error")
        except (ValueError, AttributeError):
            message = None
        raise BridgeError(message or (reply.stderr or reply.stdout or "").strip()[-500:] or "the set approval failed")
    return "Every plan of the change set is approved; each run builds."


def take(run: TaskRun, view: dict, action: ReplyAction) -> str:
    """Execute the one action a reply named, against the fresh view the caller just read."""
    need = view.get("needs") or {}
    if action.kind == "answers":
        return answer_all(run, action.answers)
    if action.kind == "approve":
        token = actions.approval_token(view)
        if not token or not action.shown_token or token != action.shown_token:
            raise BridgeError(
                "that approval matches no complete plan shown here; the current plan is relayed for a fresh decision"
            )
        run.approve_plan(token)
        return "Plan approved; the build is starting."
    if action.kind == "approve_set":
        return approve_set(run, view, action.shown_token)
    if action.kind == "review":
        token = actions.approval_token(view)
        if not token or not action.shown_token or token != action.shown_token:
            raise BridgeError("the review changed since it was shown here; the current criteria are being relayed")
        for criterion in need.get("criteria") or []:
            run.approve_review(str(criterion), token)
        return "Review accepted."
    if action.kind == "feedback":
        run.feedback(action.text)
        return "Sent to the Requirements Gatherer as feedback."
    if action.kind == "follow_up":
        run.follow_up(action.text)
        return "Sent as a follow-up; the run continues."
    if action.kind == "operational":
        if not (need.get("resolver_request_id") and need.get("resolver_token")):
            raise BridgeError("no published Resolver request to answer; the current gate is being relayed")
        run.respond_operational(need["resolver_request_id"], need["resolver_token"], action.text)
        return "Sent to the Resolver."
    if action.kind == "model":
        route = need.get("route") or {}
        if not route.get("role"):
            raise BridgeError("the run no longer asks for a model choice; the current gate is being relayed")
        if need.get("kind") == "retry_job":
            run.assign_model(route["role"], action.model, job_retry_token=need.get("job_retry_token"))
        else:
            run.assign_model(route["role"], action.model, resolver_token=need.get("resolver_token"))
        return f"Continuing {route['role']} on {action.model}."
    if action.kind == "resume":
        command = actions.named(view, *actions.own(view, run.workspace, run.run_dir))
        dispatch(run, view, command)
        return "Resumed."
    if action.kind == "retry":
        if need.get("kind") == "retry_job" and need.get("job_retry_token"):
            dispatch(run, view, actions.named(view, *actions.own(view, run.workspace, run.run_dir)))
            return "Retrying the stopped job."
        raise BridgeError("this stop names no retry to take; it is being relayed for a person")
    raise BridgeError(f"the bridge takes no {action.kind} action; this is a gap to report")


def publish_questions(run: TaskRun, view: dict) -> None:
    """Advance a run whose questions are not published yet, so the next view carries the request."""
    if (view.get("needs") or {}).get("kind") == "answer" and not (view.get("needs") or {}).get("resolver_token"):
        dispatch(run, view, actions.NamedCommand(["--no-chat"], {}))


def prepare_pull_request(record: dict, *, open_: bool, command=AUTOCODE) -> str:
    """Commit the finished run's change and write or open the PR, exactly as ``autocode issue pr`` does."""
    ref = f"{record['owner']}/{record['repo']}#{record['number']}"
    argv = [*command, "issue", "pr", ref, "--project", record["project"]] + (["--open"] if open_ else [])
    reply = run_process(argv, advancing=False)
    if reply.returncode:
        raise BridgeError(f"preparing the pull request failed: {(reply.stderr or reply.stdout).strip()[-500:]}")
    return (reply.stdout or "").strip()

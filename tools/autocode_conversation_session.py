"""Core conversation facade over one caller-supplied public TaskRun driver.

No subprocess or provider controller lives here. The initial source is a human-
only canonical envelope, and all planning runs through the ordinary task loop.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path

try:
    from . import autocode_controller_isolation as isolation
    from . import autocode_conversation as protocol
    from . import autocode_conversation_policy as policy
    from . import autocode_util as util
    from . import autocode_workspaces as workspaces
except ImportError:
    import autocode_controller_isolation as isolation
    import autocode_conversation as protocol
    import autocode_conversation_policy as policy
    import autocode_util as util
    import autocode_workspaces as workspaces


def _option(arguments, flag, default=None):
    for index, value in reversed(list(enumerate(arguments))):
        if value == flag and index + 1 < len(arguments):
            return arguments[index + 1]
        if value.startswith(flag + "="):
            return value.partition("=")[2]
    return default


@contextmanager
def _creation_lock(workspace):
    # A file lock would make an empty non-Git folder populated before bootstrap.
    # Lock its existing directory instead, without creating any project files.
    fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        util.lock_file(fd, blocking=True)
        yield
    finally:
        os.close(fd)


def _saved_workspace(selected, saved):
    workspace = Path(saved.get("workspace", str(selected)))
    if (
        not workspace.is_absolute()
        or workspace != workspace.resolve()
        or (workspace != selected and workspace.parent != selected / "autocode-projects")
    ):
        raise ValueError("Saved conversation workspace is not the selected project or its bootstrap child")
    return workspace


def _refused_project(driver_class, selected, markers, task):
    """A project an earlier refused start of this task bootstrapped and left untouched (#1957).

    Bootstrapping is the only side effect that precedes the launch's own validation,
    so a retry reuses that project instead of minting another one per attempt. A
    project that gained a run, a commit or a change is the person's, not a leftover.
    """
    if not markers.is_dir():
        return None
    for marker in sorted(markers.glob("*.json")):
        try:
            record = policy.read_record(marker, markers)
            if not record.get("refused") or record.get("task") != task:
                continue
            workspace = _saved_workspace(selected, record)
        except (OSError, ValueError, AttributeError):
            continue
        if workspace == selected or not workspace.is_dir() or driver_class.runs_in(workspace):
            continue
        try:
            if (
                workspaces.git(workspace, "rev-list", "--count", "HEAD") == "1"
                # AutoCode's own .autocode (the staged handoff) is not a change the person made.
                and not workspaces.git(
                    workspace, "status", "--porcelain", "--untracked-files=all", "--", ".", ":(exclude).autocode"
                )
                and Path(workspaces.git(workspace, "rev-parse", "--show-toplevel")).resolve() == workspace
            ):
                return workspace
        except (OSError, ValueError):
            continue
    return None


def _creation_workspace(driver_class, selected, text, saved, markers):
    if saved is None:
        isolation.require_isolated(selected, Path(__file__))
        workspace = (
            selected
            if (selected / ".git").exists()
            else _refused_project(driver_class, selected, markers, protocol.digest(text))
            or workspaces.bootstrap(selected, text)
        )
    else:
        workspace = _saved_workspace(selected, saved)
        protocol._safe_file(selected, workspace)
        handoff = protocol.load_handoff(Path(saved["source"]), workspace=workspace)
        if handoff["conversation_id"] != saved["conversation_id"]:
            raise ValueError("Saved conversation workspace differs from its creation source")
    if Path(workspaces.git(workspace, "rev-parse", "--show-toplevel")).resolve() != workspace:
        raise ValueError("Select the root of a Git checkout")
    return workspace


def start(
    driver_class,
    workspace,
    text,
    *,
    request_id,
    title=None,
    options=(),
    start_options=(),
    command,
    env=None,
    timeout=None,
    cwd=None,
):
    policy.identifier(request_id)
    policy.text_value(text)
    options = policy.creation_options(options)
    start_options = policy.creation_options(start_options)
    arguments = (*options, *start_options)
    # A creation declaration is context, not a claim about an accepted launch.
    # Missing model/effort selections stay explicitly unknown until the ordinary
    # controller resolves its defaults and saves the actual stage route.
    declared = {
        "model": _option(arguments, "--requirements-model", _option(arguments, "--glm-model", "runner-default")),
        "reasoning_effort": _option(arguments, "--requirements-reasoning-effort"),
        "engine": _option(arguments, "--engine", "opencode"),
        "provider": _option(arguments, "--provider"),
    }
    selected = Path(workspace).resolve()
    if not selected.is_dir():
        raise ValueError(f"workspace does not exist: {selected}")
    binding = protocol.digest(
        {
            "text": text,
            "title": title,
            "options": list(options),
            "start_options": list(start_options),
            "declared_route": declared,
        }
    )
    marker = selected / ".autocode" / "conversation-starts" / (protocol.digest(request_id) + ".json")
    protocol._safe_file(selected, marker)
    protocol._safe_file(selected, selected / "autocode-projects")
    with _creation_lock(selected):
        saved = policy.read_record(marker, marker.parent) if marker.exists() else None
        if saved is not None and saved["binding"] != binding:
            raise ValueError("Conversation start request ID was already used for different content or configuration")
        workspace = _creation_workspace(driver_class, selected, text, saved, marker.parent)
        if saved is not None:
            candidates = (
                [Path(saved["run_dir"])]
                if saved.get("run_dir")
                else [path for path in driver_class.runs_in(workspace) if str(path) not in saved["prior_runs"]]
            )
            for path in candidates:
                run = driver_class(workspace, path, tuple(command), tuple(options), env, timeout, cwd)
                current = run.status().get("conversation") or {}
                if current.get("availability") == "available" and current.get("id") == saved["conversation_id"]:
                    return run
            if not saved.get("refused") or candidates:
                raise ValueError("Prior conversation creation is unresolved; inspect/attach it instead of replaying start")
            # The launch refused this request before it saved a run, so retrying it is a fresh launch.
            return _launch(driver_class, workspace, selected, marker, saved, options, start_options, command, env, timeout, cwd)
        conversation_id = uuid.uuid4().hex
        handoff = protocol.handoff_from_document(
            {
                "id": conversation_id,
                "title": (title or text.strip().splitlines()[0])[:120],
                "created_at": protocol.now(),
                "messages": [
                    {
                        "id": "initial-human",
                        "role": "user",
                        "speaker": "User",
                        "text": text,
                        "created_at": protocol.now(),
                        "status": "saved",
                        "client_request_id": request_id,
                        "logical_turn_id": "initial-human",
                        "in_reply_to": None,
                    }
                ],
                "configured_routes": {"requirements_gatherer": declared},
            }
        )
        source = protocol.stage_handoff(workspace, handoff)
        saved = {
            "binding": binding,
            "task": protocol.digest(text),
            "request_id": request_id,
            "conversation_id": conversation_id,
            "workspace": str(workspace),
            "source": str(source),
            "prior_runs": [str(path) for path in driver_class.runs_in(workspace)],
        }
        protocol.write_private_json(marker, saved)
        return _launch(driver_class, workspace, selected, marker, saved, options, start_options, command, env, timeout, cwd)


def _launch(driver_class, workspace, selected, marker, saved, options, start_options, command, env, timeout, cwd):
    source = Path(saved["source"])
    handoff = protocol.load_handoff(source, workspace=workspace)
    saved = {key: value for key, value in saved.items() if key != "refused"}
    protocol.write_private_json(marker, saved)
    try:
        run = driver_class.start(
            workspace,
            protocol.task_text(handoff),
            options=options,
            start_options=(
                *start_options,
                "--joint-planning",
                "--workflow",
                "build",
                "--conversation-handoff",
                str(source),
                "--conversation-session",
            ),
            command=command,
            env=env,
            timeout=timeout,
            cwd=cwd,
        )
    except Exception as error:
        if (
            getattr(error, "process", None) is not None
            and getattr(error, "run_dir", None) is None
            and not [path for path in driver_class.runs_in(workspace) if str(path) not in saved["prior_runs"]]
        ):
            # The CLI finished and refused before it saved a run (not a timeout that may
            # still be launching): a retry may launch again, and a retry of the same task
            # reuses this project instead of bootstrapping another one.
            protocol.write_private_json(marker, {**saved, "refused": True})
            if workspace != selected and error.args and isinstance(error.args[0], str):
                error.args = (
                    f"{error.args[0]}\nAutoCode created the project {workspace} for this conversation before the "
                    "start was refused; starting the same task again reuses it.",
                    *error.args[1:],
                )
        raise
    protocol.write_private_json(marker, {**saved, "run_dir": str(run.run_dir)})
    return run


def read(run):
    value = run.status().get("conversation")
    if not isinstance(value, dict) or value.get("availability") != "available":
        reason = value.get("reason") if isinstance(value, dict) else None
        raise ValueError(reason or "This run has no core conversation")
    return value


def attach(run):
    return read(run)


def _lock(run):
    read(run)  # Authenticate run/workspace/journal through the ordinary public status first.
    return protocol._file_lock(Path(run.run_dir) / ".conversation-session-operation.lock")


def _planning_only(view):
    if view.get("done") or view.get("approved_contract"):
        raise ValueError(
            "Conversation planning cannot replace an approved or completed task; use its ordinary task interface"
        )


def send(run, text, *, request_id, expected_revision, supersedes_request_id=None):
    with _lock(run):
        view = run.status()
        _planning_only(view)
        current = view["conversation"]
        policy.reconcile(run.run_dir, current["actions"])
        need = view.get("needs") or {}
        route = need.get("route")
        stopped_route = bool(
            need.get("kind") == "answer"
            and need.get("resolver_scope") == "operational_exhaustion"
            and need.get("resolver_token")
            and isinstance(route, dict)
            and route.get("question_id") in [row.get("id") for row in need.get("questions", [])]
        )
        policy.queue(
            run.run_dir,
            text,
            request_id,
            expected_revision,
            supersedes_request_id=supersedes_request_id,
            actions=current["actions"],
            # Model refusals retain their stopped attempt. Correcting a sealed
            # rejection changes only its journal, without resuming that attempt.
            active=current["active"] and not stopped_route,
        )
        return read(run)


def refresh(run, *, request_id, expected_revision, question_id=None, resolver_token=None):
    with _lock(run):
        view = run.status()
        _planning_only(view)
        current = view.get("conversation") or {}
        if current.get("availability") != "available":
            raise ValueError(current.get("reason") or "This run has no core conversation")
        policy.reconcile(run.run_dir, current["actions"])
        prior = policy.session(protocol.read_journal(run.run_dir))["refreshes"].get(request_id)
        if prior:
            if prior["revision"] != expected_revision or prior.get("question_id") != question_id:
                raise ValueError("Refresh request ID was already used for a different revision or answer")
            if resolver_token is not None and resolver_token != prior.get("resolver_token"):
                raise ValueError("Refresh request ID was already used with a different answer token")
            resolver_token = prior.get("resolver_token")
        elif question_id is not None:
            need = view.get("needs") or {}
            if need.get("kind") != "answer" or question_id not in [row.get("id") for row in need.get("questions", [])]:
                raise ValueError("Conversation answer must address the current public question")
            current_token = need.get("resolver_token")
            if not current_token or resolver_token is not None and resolver_token != current_token:
                raise ValueError("The conversation answer token is stale; read the current public question")
            resolver_token = current_token
        record = policy.prepare_refresh(
            run.run_dir, request_id, expected_revision, question_id=question_id, resolver_token=resolver_token
        )
        if record.get("noop"):
            return {**read(run), "refresh_noop": True}
        if record["delivery"] == "rejected":
            raise ValueError("The runner rejected this request; send a correction with supersedes_request_id")
        if record["delivery"] == "prepared":
            run.apply_conversation_request(
                policy.request_path(run.run_dir, request_id),
                record["text"],
                question_id=question_id,
                resolver_token=resolver_token,
            )
            policy.reconcile(run.run_dir, read(run)["actions"])
            record = policy.session(protocol.read_journal(run.run_dir))["refreshes"][request_id]
        if record["delivery"] == "finished":
            return {**read(run), "refresh_noop": True}
        if record["delivery"] == "advancing":
            retained = run.status()
            if (retained.get("conversation") or {}).get("active") or (retained.get("needs") or {}).get("kind") not in (
                "answer",
                "approve_plan",
            ):
                raise ValueError(
                    "Prior planning delivery is unresolved; inspect/recover the existing task, without replaying refresh"
                )
            policy.mark(run.run_dir, request_id, "finished")
            return read(run)
        policy.mark(run.run_dir, request_id, "advancing")
        run.advance()
        policy.mark(run.run_dir, request_id, "finished")
        return read(run)


def handoff(run, *, expected_revision, expected_plan_token):
    with _lock(run):
        view = run.status()
        _planning_only(view)
        current = view.get("conversation") or {}
        if current.get("availability") != "available":
            raise ValueError("This run has no core conversation")
        if current["revision"] != expected_revision or not current.get("handoff_ready"):
            raise ValueError("Conversation draft changed or has not completed its independent plan review")
        draft = current["current_draft"]
        if draft.get("token") != expected_plan_token:
            raise ValueError("Plan changed; read its current draft and approval token before handoff")
        policy.reconcile(run.run_dir, current["actions"])
        policy.handoff(run.run_dir, expected_revision, expected_plan_token, draft["hash"])
        final = read(run)
        if (
            final["revision"] != expected_revision
            or not final.get("handoff_ready")
            or (final.get("current_draft") or {}).get("token") != expected_plan_token
        ):
            raise ValueError("The task changed during handoff; inspect its current draft before acting")
        return {
            **final,
            "handoff": {
                "run_dir": str(run.run_dir),
                "revision": expected_revision,
                "plan_revision": draft["revision"],
                "plan_hash": draft["hash"],
                "approval_token": expected_plan_token,
                "approved": False,
            },
        }

"""Core conversation facade over one caller-supplied public TaskRun driver.

No subprocess or provider controller lives here. The initial source is a human-
only canonical envelope, and all planning runs through the ordinary task loop.
"""

from __future__ import annotations

import uuid
from pathlib import Path

try:
    from . import autocode_conversation as protocol
    from . import autocode_conversation_policy as policy
except ImportError:
    import autocode_conversation as protocol
    import autocode_conversation_policy as policy


def _option(arguments, flag, default=None):
    for index, value in reversed(list(enumerate(arguments))):
        if value == flag and index + 1 < len(arguments):
            return arguments[index + 1]
        if value.startswith(flag + "="):
            return value.partition("=")[2]
    return default


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
    workspace = Path(workspace).resolve()
    binding = protocol.digest(
        {
            "text": text,
            "title": title,
            "options": list(options),
            "start_options": list(start_options),
            "declared_route": declared,
        }
    )
    marker = workspace / ".autocode" / "conversation-starts" / (protocol.digest(request_id) + ".json")
    with protocol._file_lock(workspace / ".autocode" / ".conversation-start.lock"):
        if marker.exists():
            saved = policy.read_record(marker, marker.parent)
            if saved["binding"] != binding:
                raise ValueError(
                    "Conversation start request ID was already used for different content or configuration"
                )
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
            raise ValueError("Prior conversation creation is unresolved; inspect/attach it instead of replaying start")
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
            "request_id": request_id,
            "conversation_id": conversation_id,
            "source": str(source),
            "prior_runs": [str(path) for path in driver_class.runs_in(workspace)],
        }
        protocol._atomic_json(marker, saved)
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
        protocol._atomic_json(marker, {**saved, "run_dir": str(run.run_dir)})
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
        policy.queue(
            run.run_dir,
            text,
            request_id,
            expected_revision,
            supersedes_request_id=supersedes_request_id,
            actions=current["actions"],
            active=current["active"],
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

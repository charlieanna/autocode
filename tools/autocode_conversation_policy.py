"""Runner-owned conversation revisions and idempotent saved user requests.

The optional journal ``session`` record is written only here. It describes the
core CLI session; legacy dashboard journals are not upgraded implicitly. This
module owns no provider, controller or task state-file access. The caller supplies
the already locked state when recording an ordinary saved-action acknowledgement.
"""

from __future__ import annotations

import copy
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path

try:
    from . import autocode_conversation as protocol
except ImportError:
    import autocode_conversation as protocol

ACK = "conversation_action"
REQUEST_KIND = "autocode.conversation-action"
CREATION_VALUES = frozenset(
    {
        "--engine",
        "--provider",
        "--astra-model",
        "--glm-model",
        "--plan-reviewer-model",
        "--terra-model",
        "--sol-model",
        "--completion-model",
        "--requirements-model",
        "--resolver-model",
        "--investigator-model",
        "--builder-strong-model",
        "--reasoning-effort",
        "--astra-reasoning-effort",
        "--glm-reasoning-effort",
        "--plan-reviewer-reasoning-effort",
        "--terra-reasoning-effort",
        "--sol-reasoning-effort",
        "--completion-reasoning-effort",
        "--requirements-reasoning-effort",
        "--resolver-reasoning-effort",
        "--investigator-reasoning-effort",
        "--max-seconds",
        "--max-stage-seconds",
        "--max-idle-seconds",
        "--max-tool-seconds",
        "--max-iterations",
        "--evidence-provenance",
    }
)
CREATION_SWITCHES = frozenset(
    {
        "--joint-planning",
        "--adaptive-planning",
        "--no-adaptive-planning",
        "--planning-v2",
        "--no-chat",
        "--no-verbose",
        "--verbose",
    }
)


def creation_options(values):
    """One allowlist for Python and CLI creation, with no action/controller flags."""
    result = []
    remaining = list(values)
    while remaining:
        word = remaining.pop(0)
        if not isinstance(word, str):
            raise ValueError("Conversation creation options must be strings")
        flag, separator, value = word.partition("=")
        if flag in CREATION_SWITCHES and not separator:
            result.append(flag)
            continue
        if flag not in CREATION_VALUES:
            raise ValueError(f"Unsupported conversation creation option: {flag}")
        if not separator:
            if not remaining or not isinstance(remaining[0], str) or remaining[0].startswith("--"):
                raise ValueError(f"Conversation creation option needs a value: {flag}")
            value = remaining.pop(0)
        if not value or value.startswith("--"):
            raise ValueError(f"Conversation creation option needs a nonempty value: {flag}")
        result.extend((flag, value))
    return tuple(result)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", value):
        raise ValueError("A bounded conversation request ID is required")
    return value


def text_value(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 40_000:
        raise ValueError("Conversation text must contain 1–40000 characters")
    return value


def session(journal):
    value = journal.get("session")
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("This run has no core conversation session; its legacy handoff is unchanged")
    revision, applied = value.get("revision"), value.get("applied_revision")
    if type(revision) is not int or type(applied) is not int or not 1 <= applied <= revision:
        raise ValueError("Conversation revision is invalid")
    for key in ("messages", "refreshes", "handoffs"):
        if not isinstance(value.get(key), dict):
            raise ValueError("Conversation request journal is invalid")
    return value


def initialize(run_dir):
    def apply(journal):
        if journal.get("session") is not None:
            session(journal)
            return journal
        messages = [row for row in journal["conversation"]["messages"] if row["role"] == "user"]
        journal["session"] = {
            "schema_version": 1,
            "revision": 1,
            "applied_revision": 1,
            "messages": {
                row["client_request_id"]: {"revision": 1, "message_id": row["id"], "text": row["text"], "applied": True}
                for row in messages
                if row.get("client_request_id")
            },
            "refreshes": {},
            "rejections": {},
            "handoffs": {},
        }
        return journal

    return protocol.update_journal(run_dir, apply)


def _revision(core, expected):
    if type(expected) is not int or expected != core["revision"]:
        raise ValueError("Conversation changed; read its current revision before acting")


def queue(run_dir, text, request_id, expected_revision, *, supersedes_request_id=None, actions=(), active=False):
    identifier(request_id)
    text_value(text)
    if supersedes_request_id is not None:
        identifier(supersedes_request_id)

    def apply(journal):
        core = session(journal)
        previous = core["messages"].get(request_id)
        if previous:
            if (previous["text"], previous["revision"], previous.get("supersedes_request_id")) != (
                text,
                expected_revision + 1,
                supersedes_request_id,
            ):
                raise ValueError("Conversation request ID was already used for different content or revision")
            return journal
        _revision(core, expected_revision)
        if supersedes_request_id is not None:
            old = core["messages"].get(supersedes_request_id)
            acknowledged = {message for action in actions for message in action.get("message_ids", [])}
            if not old or old["applied"] or old.get("superseded_by") or old["message_id"] in acknowledged or active:
                raise ValueError("Only a rejected, unapplied human message can be corrected")
            related = [row for row in core["refreshes"].values() if old["message_id"] in row["message_ids"]]
            rejected = core.get("rejections", {})
            if not related or any(
                row["delivery"] != "rejected"
                or rejected.get(row["request_id"], {}).get("action_digest") != row["digest"]
                or rejected[row["request_id"]].get("kind") != "runner_rejected_input"
                or rejected[row["request_id"]].get("revision") != row["revision"]
                for row in related
            ):
                raise ValueError("The old human request has no definitive runner rejection; inspect it instead")
        pending_texts = [
            row["text"]
            for ident, row in sorted(core["messages"].items(), key=lambda item: item[1]["revision"])
            if not row["applied"] and not row.get("superseded_by") and ident != supersedes_request_id
        ]
        text_value("\n\n".join([*pending_texts, text]))
        if supersedes_request_id is not None:
            old["superseded_by"] = request_id
        revision = core["revision"] + 1
        message_id = (
            "human-" + protocol.digest({"request_id": request_id, "conversation": journal["conversation_id"]})[:24]
        )
        journal["conversation"]["messages"].append(
            {
                "id": message_id,
                "role": "user",
                "text": text,
                "created_at": protocol.now(),
                "speaker": "User",
                "status": "saved",
                "client_request_id": request_id,
                "logical_turn_id": message_id,
                "in_reply_to": None,
            }
        )
        core["messages"][request_id] = {
            "revision": revision,
            "message_id": message_id,
            "text": text,
            "applied": False,
            "supersedes_request_id": supersedes_request_id,
        }
        core["revision"] = revision
        return journal

    return protocol.update_journal(run_dir, apply)


def acknowledgements(state, conversation_id):
    return [
        copy.deepcopy(row)
        for row in state.get("user_events", [])
        if isinstance(row, dict) and row.get("kind") == ACK and row.get("conversation_id") == conversation_id
    ]


def request_path(run_dir, request_id):
    identifier(request_id)
    return Path(run_dir) / "conversation-actions" / (protocol.digest(request_id) + ".json")


def read_record(path, parent):
    path = Path(path)
    root = Path(parent).resolve()
    if path.parent.is_symlink() or path.parent.resolve() != root or path.is_symlink():
        raise ValueError("Conversation action must be in the owned action directory")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > protocol.MAX_HANDOFF_BYTES:
        raise ValueError("Conversation action must be a bounded regular file")
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_mode) != (before.st_dev, before.st_ino, before.st_mode):
            raise ValueError("Conversation action changed while opening")
        data = stream.read(protocol.MAX_HANDOFF_BYTES + 1)
        after = os.fstat(stream.fileno())
    if len(data) > protocol.MAX_HANDOFF_BYTES or (after.st_size, after.st_mtime_ns) != (
        before.st_size,
        before.st_mtime_ns,
    ):
        raise ValueError("Conversation action changed while reading")
    try:
        return json.loads(data)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Conversation record is not valid JSON") from error


def _read_action(path, run_dir):
    return read_record(path, Path(run_dir) / "conversation-actions")


def prepare_refresh(run_dir, request_id, expected_revision, *, question_id=None, resolver_token=None):
    identifier(request_id)
    result = {}

    def apply(journal):
        core = session(journal)
        previous = core["refreshes"].get(request_id)
        if previous:
            if (previous["revision"], previous.get("question_id"), previous.get("resolver_token")) != (
                expected_revision,
                question_id,
                resolver_token,
            ):
                raise ValueError("Refresh request ID was already used for a different revision or answer")
            result.update(previous)
            return journal
        _revision(core, expected_revision)
        pending = sorted(
            (row for row in core["messages"].values() if not row["applied"] and not row.get("superseded_by")),
            key=lambda row: row["revision"],
        )
        if not pending:
            result["noop"] = True
            return journal
        if question_id is not None and len(pending) != 1:
            raise ValueError("A question answer requires exactly one pending human message")
        action = {
            "kind": REQUEST_KIND,
            "schema_version": 1,
            "conversation_id": journal["conversation_id"],
            "handoff_digest": journal["handoff_digest"],
            "request_id": request_id,
            "revision": expected_revision,
            "message_ids": [row["message_id"] for row in pending],
            "text": pending[0]["text"] if question_id is not None else "\n\n".join(row["text"] for row in pending),
            "question_id": question_id,
            "resolver_token": resolver_token,
        }
        text_value(action["text"])
        action["digest"] = protocol.digest(action)
        record = {**action, "delivery": "prepared"}
        core["refreshes"][request_id] = record
        result.update(record)
        return journal

    protocol.update_journal(run_dir, apply)
    if result.get("noop"):
        return result
    action = {key: value for key, value in result.items() if key != "delivery"}
    path = request_path(run_dir, request_id)
    with protocol._file_lock(Path(run_dir) / ".conversation-action-file.lock"):
        if path.exists():
            if _read_action(path, run_dir) != action:
                raise ValueError("Saved conversation action conflicts with its sealed request")
        else:
            protocol._atomic_json(path, action)
    return result


def load_action(state, run_dir, args):
    source = getattr(args, "conversation_request", None)
    if source is None:
        return None
    path = Path(source)
    value = _read_action(path, run_dir)
    if not isinstance(value, dict) or value.get("kind") != REQUEST_KIND or value.get("schema_version") != 1:
        raise ValueError("Conversation action is invalid")
    if path.resolve() != request_path(run_dir, value.get("request_id")).resolve() or path.is_symlink():
        raise ValueError("Conversation action must be the owned sealed request")
    if value.get("digest") != protocol.digest(value):
        raise ValueError("Conversation action digest does not match")
    pointer = state.get("conversation_handoff") or {}
    journal = protocol.read_journal(run_dir)
    core = session(journal)
    sealed = core["refreshes"].get(value["request_id"])
    if not sealed or {key: item for key, item in sealed.items() if key != "delivery"} != value:
        raise ValueError("Conversation action is not sealed by this journal")
    if (value["conversation_id"], value["handoff_digest"]) != (pointer.get("conversation_id"), pointer.get("digest")):
        raise ValueError("Conversation action does not belong to this task")
    question = value.get("question_id")
    if question is None:
        matches = getattr(args, "feedback", None) == value["text"] and not getattr(args, "answer", [])
    else:
        matches = (
            getattr(args, "answer", []) == [question + "=" + value["text"]]
            and getattr(args, "resolver_token", None) == value.get("resolver_token")
            and getattr(args, "feedback", None) is None
        )
    forbidden = (
        "approve_goal",
        "edit_goal",
        "approve_review",
        "delegate",
        "delegate_all",
        "follow_up",
        "accept_completion",
        "resume_paused",
        "reject_assumption",
        "close_finding",
        "planning_review_call_limit",
    )
    if not matches or any(getattr(args, key, None) for key in forbidden):
        raise ValueError("Conversation request authorizes only its exact human feedback or question answer")
    prior = next(
        (
            row
            for row in acknowledgements(state, value["conversation_id"])
            if row.get("request_id") == value["request_id"]
        ),
        None,
    )
    if prior:
        if prior.get("action_digest") != value["digest"]:
            raise ValueError("Saved conversation acknowledgement conflicts with this request")
        return {**value, "acknowledged": True}
    if any(
        set(row.get("message_ids", [])) & set(value["message_ids"])
        for row in acknowledgements(state, value["conversation_id"])
    ):
        raise ValueError("This human batch was already applied under another conversation request")
    if sealed["delivery"] != "prepared":
        raise ValueError("This conversation request was rejected or its delivery is unresolved; inspect/correct it")
    _revision(core, value["revision"])
    return value


@contextmanager
def saved_action(state, run_dir, args, action):
    """Hold source revision through the existing run-locked saved-action commit."""
    if action is None:
        yield
        return
    with protocol._file_lock(Path(run_dir) / ".conversation-journal.lock"):
        current = load_action(state, run_dir, args)
        if current != action:
            raise ValueError("Conversation request changed before its saved action")
        yield


def reject(run_dir, action, error):
    """Only the runner calls this after refusing input, before candidate save."""
    if action is None:
        return

    def apply(journal):
        core = session(journal)
        record = core["refreshes"].get(action["request_id"])
        if not record or record["digest"] != action["digest"] or record["delivery"] != "prepared":
            raise ValueError("Rejected conversation input no longer matches its sealed request")
        record["delivery"] = "rejected"
        core.setdefault("rejections", {})[action["request_id"]] = {
            "kind": "runner_rejected_input",
            "action_digest": action["digest"],
            "revision": action["revision"],
            "reason": str(error),
            "at": protocol.now(),
        }
        return journal

    return protocol.update_journal(run_dir, apply)


def acknowledge(before, candidate, action):
    if action is None:
        return
    old_count = len(before.get("user_events", []))
    sources = [
        row
        for row in candidate.get("user_events", [])[old_count:]
        if isinstance(row, dict) and row.get("kind") in ("brief_feedback", "answer")
    ]
    if not sources:
        raise ValueError("Conversation action created no saved human source event")
    candidate.setdefault("user_events", []).append(
        {
            "kind": ACK,
            "actor": "user_cli",
            "id": "conversation-" + action["digest"][:24],
            "at": protocol.now(),
            "conversation_id": action["conversation_id"],
            "handoff_digest": action["handoff_digest"],
            "request_id": action["request_id"],
            "revision": action["revision"],
            "action_digest": action["digest"],
            "message_ids": action["message_ids"],
            "source_events": [{key: row.get(key) for key in ("kind", "id", "question_id", "text")} for row in sources],
        }
    )


def reconcile(run_dir, actions):
    def apply(journal):
        core = session(journal)
        for ack in actions:
            record = core["refreshes"].get(ack.get("request_id"))
            if (
                not record
                or record["digest"] != ack.get("action_digest")
                or record["message_ids"] != ack.get("message_ids")
            ):
                raise ValueError("Runner acknowledgement does not match the sealed conversation request")
            for row in core["messages"].values():
                if row["message_id"] in record["message_ids"]:
                    row["applied"] = True
            core["applied_revision"] = max(core["applied_revision"], record["revision"])
            if record["delivery"] == "prepared":
                record["delivery"] = "applied"
        return journal

    return protocol.update_journal(run_dir, apply)


def mark(run_dir, request_id, status):
    if status not in ("advancing", "finished"):
        raise ValueError("Unsupported conversation delivery state")

    def apply(journal):
        record = session(journal)["refreshes"][request_id]
        allowed = {"advancing": ("applied",), "finished": ("advancing",)}
        if record["delivery"] != status and record["delivery"] not in allowed[status]:
            raise ValueError("Conversation delivery cannot move backwards")
        record["delivery"] = status
        return journal

    return protocol.update_journal(run_dir, apply)


def handoff(run_dir, expected_revision, token, plan_hash):
    def apply(journal):
        core = session(journal)
        _revision(core, expected_revision)
        if core["applied_revision"] != core["revision"]:
            raise ValueError("Refresh the pending human messages before handing off a plan")
        core["handoffs"][token] = {"revision": expected_revision, "plan_hash": plan_hash, "at": protocol.now()}
        return journal

    return protocol.update_journal(run_dir, apply)

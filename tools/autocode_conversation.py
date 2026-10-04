"""Durable Requirements Conversation handoff and dispatch records.

This module is deliberately independent of the dashboard HTTP server and the
runner.  Both sides use the same canonical schema so attaching a project is a
transfer of authority, rather than a copy of a transcript into a task string.

This worktree's port keeps the snapshot's HANDOFF_VERSION=1 shapes and digests
byte-compatible while additively extending plan-draft records in the
authoritative ``normalize_document``/``_plan_drafts`` path: records may carry
requirements-revision and logical-turn binding, role/model/reasoning
attribution, ``pending``/``failed`` status and freshness values, and bounded
freshness error evidence.  Records without those extension fields normalize to
exactly the snapshot shape, so snapshot-shaped documents and fixtures still
validate unchanged.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid


HANDOFF_VERSION = 1
HANDOFF_KIND = "autocode.requirements-conversation-handoff"
JOURNAL_KIND = "autocode.requirements-conversation-journal"
MAX_HANDOFF_BYTES = 1_048_576
_IDENT = re.compile(r"[A-Za-z0-9_.:-]{1,160}$")
_CONVERSATION_ID = re.compile(r"[a-f0-9]{32}$")

# These capabilities are intentionally conservative.  A local session id,
# elapsed time, or a missing child process is never authoritative evidence that
# a provider did not execute a logical turn.
CAPABILITY_MATRIX = {
    "native_codex": {
        "transport": "native_codex",
        "provider_idempotency": False,
        "authoritative_not_dispatched": False,
        "live_execution_identity": "thread_id_and_owned_process",
        "completed_result": "validated_durable_terminal_record",
    },
    "opencode": {
        "transport": "opencode",
        "provider_idempotency": False,
        "authoritative_not_dispatched": False,
        "live_execution_identity": "session_id_and_owned_process",
        "completed_result": "validated_session_events",
    },
    "command_opencode_events": {
        "transport": "command_opencode_events",
        "provider_idempotency": False,
        "authoritative_not_dispatched": False,
        "live_execution_identity": "configured_session_and_owned_process",
        "completed_result": "validated_opencode_events",
    },
    "command_report_file": {
        "transport": "command_report_file",
        "provider_idempotency": False,
        "authoritative_not_dispatched": False,
        "live_execution_identity": None,
        "completed_result": "validated_durable_report_file",
    },
}

DISPATCH_STATES = {
    "SAVED",
    "DISPATCH_PREPARED",
    "PROCESS_STARTING",
    "PROCESS_STARTED",
    "PROVIDER_IDENTIFIED",
    "RESULT_CAPTURED",
    "REPLY_COMMITTED",
    "SAFE_NOT_DISPATCHED",
    "ACTIVE",
    "UNCERTAIN",
}

# Additive extension over the snapshot: plan-draft records may be ``pending``
# (a Planner dispatch is in flight for the bound revision/turn) or ``failed``
# (a dispatched result was rejected or errored; the previous usable draft is
# preserved elsewhere in the history).  Snapshot records keep using only
# ``current``/``superseded``.
PLAN_DRAFT_STATUSES = ("current", "superseded", "pending", "failed")
# ``pending`` marks a structured result awaited for the current requirements
# revision; ``failed`` records retained error evidence.  The snapshot values
# (``fresh``/``refreshing``/``stale``) remain valid unchanged.
PLAN_DRAFT_FRESHNESS_STATES = ("fresh", "refreshing", "stale", "pending", "failed")


class ConversationProtocolError(ValueError):
    """A saved handoff or journal cannot be trusted."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def canonical_json(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ConversationProtocolError("Conversation protocol data must be JSON-safe.") from error


def digest(value) -> str:
    payload = deepcopy(value)
    if isinstance(payload, dict):
        payload.pop("digest", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def capabilities() -> dict:
    return deepcopy(CAPABILITY_MATRIX)


def _identifier(value, label: str, *, conversation=False, optional=False):
    if value is None and optional:
        return None
    matcher = _CONVERSATION_ID if conversation else _IDENT
    if not isinstance(value, str) or not matcher.fullmatch(value):
        raise ConversationProtocolError(f"{label} is invalid.")
    return value


def _text(value, label: str, *, allow_empty=False, limit=40_000):
    if not isinstance(value, str) or len(value) > limit or (not allow_empty and not value.strip()):
        raise ConversationProtocolError(f"{label} is invalid.")
    return value


def _route(value):
    if not isinstance(value, dict):
        raise ConversationProtocolError("Configured routes must be objects.")
    result = {}
    for name, route in value.items():
        _identifier(name, "Configured route name")
        if not isinstance(route, dict):
            raise ConversationProtocolError("Configured route is invalid.")
        model = _text(route.get("model"), "Configured model", limit=240)
        engine = _text(route.get("engine"), "Configured engine", limit=80)
        provider = route.get("provider")
        if provider is not None:
            _text(provider, "Configured provider", limit=80)
        effort = route.get("reasoning_effort")
        if effort is not None:
            _text(effort, "Configured reasoning effort", limit=80)
        result[name] = {"engine": engine, "provider": provider, "model": model,
                        "reasoning_effort": effort}
    if "requirements_gatherer" not in result:
        raise ConversationProtocolError("The configured Requirements Gatherer route is required.")
    return result


def _message(value):
    if not isinstance(value, dict):
        raise ConversationProtocolError("Conversation message is invalid.")
    result = {
        "id": _identifier(value.get("id"), "Message id"),
        "role": value.get("role"),
        "speaker": _text(value.get("speaker"), "Message speaker", limit=160),
        "text": _text(value.get("text"), "Message text"),
        "created_at": _text(value.get("created_at"), "Message timestamp", limit=80),
        "status": _text(value.get("status"), "Message status", limit=80),
        "client_request_id": _identifier(value.get("client_request_id"), "Client request id", optional=True),
        "logical_turn_id": _identifier(value.get("logical_turn_id"), "Logical turn id"),
        "in_reply_to": _identifier(value.get("in_reply_to"), "Reply turn id", optional=True),
    }
    if result["role"] not in ("user", "assistant"):
        raise ConversationProtocolError("Conversation message role is invalid.")
    if result["role"] == "user" and result["in_reply_to"] is not None:
        raise ConversationProtocolError("Human turns cannot reply to another logical turn.")
    if result["role"] == "assistant" and result["in_reply_to"] is None:
        raise ConversationProtocolError("Gatherer replies must identify their human turn.")
    if "execution" in value:
        execution = value["execution"]
        fields = {"role", "engine", "provider", "model", "reasoning_effort"}
        if not isinstance(execution, dict) or set(execution) - fields:
            raise ConversationProtocolError("Message execution attribution is invalid.")
        for key in ("role", "model"):
            _text(execution.get(key), f"Message execution {key}", limit=240 if key == "model" else 80)
        for key in ("engine", "provider", "reasoning_effort"):
            if execution.get(key) is not None:
                _text(execution[key], f"Message execution {key}", limit=80)
        result["execution"] = deepcopy(execution)
    if "event_refs" in value:
        refs = value["event_refs"]
        if not isinstance(refs, list) or len(refs) > 200:
            raise ConversationProtocolError("Message event references are invalid.")
        result["event_refs"] = [_text(ref, "Message event reference", limit=4096) for ref in refs]
    return result


def _draft(value):
    if not isinstance(value, dict):
        raise ConversationProtocolError("Draft record is invalid.")
    revision = value.get("revision", 0)
    if type(revision) is not int or revision < 0:
        raise ConversationProtocolError("Draft revision is invalid.")
    return {
        "id": _identifier(value.get("id"), "Draft id"),
        "tab_id": _identifier(value.get("tab_id"), "Draft tab id"),
        "text": _text(value.get("text"), "Draft text", allow_empty=True, limit=20_000),
        "revision": revision,
        "updated_at": _text(value.get("updated_at"), "Draft timestamp", limit=80),
    }


def _requirements(value):
    if not isinstance(value, dict):
        raise ConversationProtocolError("Requirements state is invalid.")
    revisions = value.get("revisions", [])
    provenance = value.get("provenance", [])
    if not isinstance(revisions, list) or not isinstance(provenance, list):
        raise ConversationProtocolError("Requirements revisions and provenance must be arrays.")
    # The schema intentionally preserves richer later-M3 requirement records.
    # Only JSON validity and bounded size are established at this transport layer.
    canonical_json({"revisions": revisions, "provenance": provenance})
    return {"revisions": deepcopy(revisions), "provenance": deepcopy(provenance)}


def _bounded_rows(value, label, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise ConversationProtocolError(f"{label} is invalid.")
    for row in value:
        if isinstance(row, str):
            if len(row) > 8000:
                raise ConversationProtocolError(f"{label} entry is too large.")
        elif isinstance(row, dict):
            canonical_json(row)
        else:
            raise ConversationProtocolError(f"{label} entries must be strings or objects.")
    return deepcopy(value)


def _attribution(value):
    """Validate plan-draft role/model/reasoning attribution (additive field).

    Required role/model/reasoning-effort text is type-checked, then the whole
    attribution object is preserved verbatim so richer producer evidence
    (engine, provider, reasoning summaries) survives round-trips.
    """
    if not isinstance(value, dict):
        raise ConversationProtocolError("Plan draft attribution is invalid.")
    _text(value.get("role"), "Plan draft attribution role", limit=80)
    _text(value.get("model"), "Plan draft attribution model", limit=240)
    _text(value.get("reasoning_effort"), "Plan draft attribution reasoning effort", limit=80)
    canonical_json(value)
    return deepcopy(value)


def _plan_drafts(value):
    """Validate the revisioned Planner-draft pipeline records (quiet preview).

    Each record is one draft revision: goal, requirements, milestones,
    parallelism, outstanding questions, and freshness bound to the human turn
    that prompted it.  Only shape, bounds and revision identity are enforced
    here; richer structured content is preserved verbatim for later milestones.

    Additively extended over the snapshot: records may bind to the exact
    requirements revision and logical turn they answer, carry role/model/
    reasoning attribution, and hold ``pending``/``failed`` status and freshness
    values with bounded error evidence.  Absent extension fields stay absent
    so snapshot-shaped records normalize to the identical snapshot shape.
    """
    if not isinstance(value, list) or len(value) > 240:
        raise ConversationProtocolError("Plan drafts are invalid.")
    result = []
    revisions = set()
    current = 0
    for row in value:
        if not isinstance(row, dict):
            raise ConversationProtocolError("Plan draft record is invalid.")
        revision = row.get("revision")
        if type(revision) is not int or revision < 1 or revision in revisions:
            raise ConversationProtocolError("Plan draft revision is invalid.")
        revisions.add(revision)
        status = row.get("status")
        if status not in PLAN_DRAFT_STATUSES:
            raise ConversationProtocolError("Plan draft status is invalid.")
        current += status == "current"
        freshness = row.get("freshness")
        if (not isinstance(freshness, dict)
                or freshness.get("state") not in PLAN_DRAFT_FRESHNESS_STATES
                or not isinstance(freshness.get("updated_at"), str) or len(freshness.get("updated_at", "")) > 80):
            raise ConversationProtocolError("Plan draft freshness is invalid.")
        if status in ("pending", "failed") and freshness["state"] != status:
            raise ConversationProtocolError("Plan draft status and freshness disagree.")
        if status in ("current", "superseded") and freshness["state"] in ("pending", "failed"):
            raise ConversationProtocolError("Plan draft status and freshness disagree.")
        error = freshness.get("error") if isinstance(freshness, dict) else None
        if error is not None and (not isinstance(error, (str, dict)) or isinstance(error, bool)
                                  or (isinstance(error, str) and len(error) > 8000)):
            raise ConversationProtocolError("Plan draft freshness error evidence is invalid.")
        if isinstance(error, dict) and len(canonical_json(error)) > 8000:
            raise ConversationProtocolError("Plan draft freshness error evidence is too large.")
        if status == "failed" and not error:
            raise ConversationProtocolError("Failed plan drafts require error evidence.")
        record = {
            "id": _identifier(row.get("id"), "Plan draft id"),
            "revision": revision,
            "created_at": _text(row.get("created_at"), "Plan draft timestamp", limit=80),
            "status": status,
            "goal": _text(row.get("goal"), "Plan draft goal", allow_empty=True, limit=4000),
            "requirements": _bounded_rows(row.get("requirements", []), "Plan draft requirements", 200),
            "milestones": _bounded_rows(row.get("milestones", []), "Plan draft milestones", 60),
            "parallelism": _bounded_rows(row.get("parallelism", []), "Plan draft parallelism", 60),
            "outstanding_questions": _bounded_rows(row.get("outstanding_questions", []),
                                                   "Plan draft outstanding questions", 60),
            "reply_preview": _text(row.get("reply_preview") or "", "Plan draft preview",
                                  allow_empty=True, limit=4000) or None,
            "freshness": deepcopy(freshness),
        }
        if (status in ("pending", "failed") or "requirements_revision" in row
                or "logical_turn_id" in row):
            binding_revision = row.get("requirements_revision")
            if type(binding_revision) is not int or binding_revision < 1:
                raise ConversationProtocolError("Plan draft requirements revision binding is invalid.")
            record["requirements_revision"] = binding_revision
            record["logical_turn_id"] = _identifier(row.get("logical_turn_id"), "Plan draft logical turn id")
            # A bound current revision represents an accepted structured Planner
            # result. Legacy snapshot records lack binding and must retain their
            # original shape, but a new accepted record cannot lose its actual
            # producer attribution during normalization or handoff.
            if status == "current" and "attribution" not in row:
                raise ConversationProtocolError("Accepted plan drafts require Planner attribution.")
        if "attribution" in row:
            record["attribution"] = _attribution(row["attribution"])
        canonical_json(record)
        result.append(record)
    if current > 1:
        raise ConversationProtocolError("Only one plan draft may be current.")
    return result


def _dispatch_records(records, *, turns, planner=False):
    """Validate additive transport receipts without granting replay authority."""
    if not isinstance(records, dict):
        raise ConversationProtocolError("Conversation dispatch records are invalid.")
    result = deepcopy(records)
    seen = set()
    for turn, row in result.items():
        _identifier(turn, "Dispatch turn")
        if turn not in turns or not isinstance(row, dict) or row.get("logical_turn_id") != turn:
            raise ConversationProtocolError("Dispatch references an unknown or mismatched human turn.")
        ident = _identifier(row.get("id"), "Dispatch id")
        if ident in seen:
            raise ConversationProtocolError("Dispatch identities must be unique.")
        seen.add(ident)
        if row.get("state") not in DISPATCH_STATES:
            raise ConversationProtocolError("Dispatch state is invalid.")
        _identifier(row.get("client_request_id"), "Dispatch request id", optional=True)
        _route({"requirements_gatherer": row.get("route")})
        history = row.get("history")
        if (not isinstance(history, list) or not history
                or any(not isinstance(item, dict) or item.get("state") not in DISPATCH_STATES for item in history)
                or history[-1]["state"] != row["state"]):
            raise ConversationProtocolError("Dispatch history does not match its current state.")
        if planner and (row.get("role") != "planner"
                        or type(row.get("requirements_revision")) is not int
                        or row["requirements_revision"] < 1):
            raise ConversationProtocolError("Planner dispatch needs its exact requirements revision and role.")
    canonical_json(result)
    return result


def recovery_action(dispatch, *, process_alive=False, captured_valid=False):
    """Classify retained delivery; process disappearance never permits replay.

    The caller supplies verified liveness and result validation. Only a saved
    pre-launch intent or authoritative not-dispatched receipt permits retry.
    A validated captured result can be committed without another provider call.
    """
    if not isinstance(dispatch, dict) or dispatch.get("state") not in DISPATCH_STATES:
        raise ConversationProtocolError("Dispatch recovery requires a valid receipt.")
    state = dispatch["state"]
    if state == "REPLY_COMMITTED":
        return "committed"
    if state == "RESULT_CAPTURED":
        return "commit_captured" if captured_valid else "failed"
    if process_alive:
        return "wait"
    if state in ("SAVED", "DISPATCH_PREPARED", "SAFE_NOT_DISPATCHED"):
        return "retry"
    return "uncertain"


def normalize_document(document: dict, *, persist_legacy=False) -> dict:
    """Return a v1 document without inventing unavailable legacy attribution.

    ``persist_legacy`` is accepted for callers that need to record a migration
    marker; it never changes an unknown saved request id into a guessed one.
    """
    if not isinstance(document, dict):
        raise ConversationProtocolError("Conversation document is invalid.")
    value = deepcopy(document)
    conversation_id = value.get("id")
    _identifier(conversation_id, "Conversation id", conversation=True)
    requests = value.get("_requests") if isinstance(value.get("_requests"), dict) else {}
    request_by_message = {message_id: request_id for request_id, message_id in requests.items()
                          if isinstance(request_id, str) and isinstance(message_id, str)}
    messages = value.get("messages")
    if not isinstance(messages, list):
        raise ConversationProtocolError("Conversation messages are invalid.")
    normalized_messages = []
    last_user_turn = None
    for index, row in enumerate(messages):
        if not isinstance(row, dict):
            raise ConversationProtocolError("Conversation message is invalid.")
        item = deepcopy(row)
        message_id = item.get("id")
        _identifier(message_id, "Message id")
        role = item.get("role")
        if role == "user":
            item.setdefault("client_request_id", request_by_message.get(message_id))
            item.setdefault("logical_turn_id", "legacy-" + message_id)
            item["in_reply_to"] = None
            last_user_turn = item["logical_turn_id"]
        elif role == "assistant":
            item.setdefault("client_request_id", None)
            item.setdefault("logical_turn_id", "legacy-" + message_id)
            # Old documents did not retain a logical reply binding.  Preserve
            # its legacy nature while binding it to the preceding known human
            # turn for the handoff schema.
            item.setdefault("in_reply_to", last_user_turn)
        normalized_messages.append(item)
    value["messages"] = normalized_messages
    value.setdefault("schema_version", HANDOFF_VERSION)
    value.setdefault("drafts", [])
    value.setdefault("requirements", {"revisions": [], "provenance": []})
    # The revisioned Planner-draft pipeline (quiet in-chat preview) rides the
    # same document; absent records stay absent so legacy documents and
    # handoff digests remain byte-compatible.
    value["plan_drafts"] = _plan_drafts(value.get("plan_drafts", []))
    if not isinstance(value.get("configured_routes"), dict):
        models = value.get("models") if isinstance(value.get("models"), dict) else {}
        model = models.get("requirements_model") or models.get("glm_model")
        if not isinstance(model, str) or not model:
            raise ConversationProtocolError("Conversation has no configured Requirements Gatherer model.")
        value["configured_routes"] = {
            "requirements_gatherer": {
                "engine": "opencode", "provider": "opencode", "model": model,
                "reasoning_effort": models.get("requirements_reasoning_effort") or models.get("glm_reasoning_effort"),
            }
        }
    value.setdefault("provider_capabilities", capabilities())
    value.setdefault("_dispatches", {})
    if persist_legacy and value.get("schema_version") != HANDOFF_VERSION:
        value["schema_version"] = HANDOFF_VERSION
    return value


def handoff_from_document(document: dict) -> dict:
    source = normalize_document(document)
    messages = [_message(row) for row in source["messages"]]
    if not messages or not any(row["role"] == "user" for row in messages):
        raise ConversationProtocolError("A handoff needs at least one human message.")
    message_ids = set()
    request_ids = set()
    turns = set()
    replies = set()
    for row in messages:
        if row["id"] in message_ids or row["logical_turn_id"] in turns:
            raise ConversationProtocolError("Message and logical-turn identities must be unique.")
        message_ids.add(row["id"])
        turns.add(row["logical_turn_id"])
        if row["client_request_id"] is not None:
            if row["client_request_id"] in request_ids:
                raise ConversationProtocolError("Client request identities must be unique.")
            request_ids.add(row["client_request_id"])
        if row["in_reply_to"] is not None:
            replies.add(row["in_reply_to"])
    human_turns = {row["logical_turn_id"] for row in messages if row["role"] == "user"}
    if not replies.issubset(human_turns):
        raise ConversationProtocolError("A Gatherer reply references an unknown human turn.")
    payload = {
        "schema_version": HANDOFF_VERSION,
        "kind": HANDOFF_KIND,
        "conversation_id": source["id"],
        "title": _text(source.get("title"), "Conversation title", limit=120),
        "created_at": _text(source.get("created_at"), "Conversation timestamp", limit=80),
        "messages": messages,
        "drafts": [_draft(row) for row in source.get("drafts", [])],
        "requirements": _requirements(source.get("requirements")),
        "configured_routes": _route(source.get("configured_routes")),
        "request_logical_turns": [
            {"client_request_id": row["client_request_id"], "logical_turn_id": row["logical_turn_id"],
             "message_id": row["id"]}
            for row in messages if row["role"] == "user"
        ],
        "provider_capabilities": capabilities(),
    }
    if source.get("plan_drafts"):
        payload["plan_drafts"] = deepcopy(source["plan_drafts"])
    for field, saved, planner in (("dispatches", "_dispatches", False),
                                  ("planner_dispatches", "_planner_dispatches", True)):
        records = source.get(saved, source.get(field, {}))
        if records:
            payload[field] = _dispatch_records(records, turns=human_turns, planner=planner)
    payload["digest"] = digest(payload)
    return payload


def validate_handoff(value) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != HANDOFF_VERSION or value.get("kind") != HANDOFF_KIND:
        raise ConversationProtocolError("Unsupported Requirements Conversation handoff schema.")
    payload = deepcopy(value)
    received = payload.get("digest")
    if not isinstance(received, str) or not re.fullmatch(r"[a-f0-9]{64}", received):
        raise ConversationProtocolError("Conversation handoff digest is invalid.")
    if digest(payload) != received:
        raise ConversationProtocolError("Conversation handoff digest does not match its contents.")
    _identifier(payload.get("conversation_id"), "Conversation id", conversation=True)
    _text(payload.get("title"), "Conversation title", limit=120)
    _text(payload.get("created_at"), "Conversation timestamp", limit=80)
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise ConversationProtocolError("Conversation handoff messages are invalid.")
    rebuilt = handoff_from_document({
        "id": payload["conversation_id"], "title": payload["title"], "created_at": payload["created_at"],
        "messages": messages, "drafts": payload.get("drafts", []),
        "requirements": payload.get("requirements"), "configured_routes": payload.get("configured_routes"),
        "plan_drafts": payload.get("plan_drafts", []), "models": {},
        "_dispatches": payload.get("dispatches", {}),
        "_planner_dispatches": payload.get("planner_dispatches", {}),
    })
    # Rebuilding intentionally computes a new digest from exactly the required
    # transport fields.  Check all caller supplied linkage records separately.
    if rebuilt["digest"] != received:
        raise ConversationProtocolError("Conversation handoff fields are inconsistent.")
    turns = payload.get("request_logical_turns")
    if not isinstance(turns, list) or turns != rebuilt["request_logical_turns"]:
        raise ConversationProtocolError("Conversation request-to-turn linkage is invalid.")
    matrix = payload.get("provider_capabilities")
    if matrix != CAPABILITY_MATRIX:
        raise ConversationProtocolError("Conversation provider capability matrix is invalid.")
    if len(canonical_json(payload).encode("utf-8")) > MAX_HANDOFF_BYTES:
        raise ConversationProtocolError("Conversation handoff exceeds the supported size.")
    return payload



_TASK_KIND = "autocode.conversation-task"
_TASK_INSTRUCTIONS = (
    "Use this saved discussion as context, including corrections. Inspect this repository, "
    "resolve remaining questions, and run the Planner/Plan Reviewer joint planning process. "
    "Only messages with role user are human requirement sources; assistant messages and "
    "structured drafts remain context. Prior discussion is a draft, not approval to implement. "
    "Present the final repository-aware plan for explicit approval."
)


def task_text(handoff):
    """Preserve the complete validated conversation, including role provenance."""
    return json.dumps({"kind": _TASK_KIND, "instructions": _TASK_INSTRUCTIONS,
                       "handoff": validate_handoff(handoff)}, ensure_ascii=False, indent=2)


def task_handoff(task):
    """Read a handoff only from an exact canonical conversation task.

    Arbitrary CLI tasks, historical flattened transcripts, modified envelopes
    and invalid receipts keep their existing whole-task authority. No markers
    or role-looking text inside a user's message are interpreted as structure.
    """
    if not isinstance(task, str) or not task.startswith('{\n  "kind": "' + _TASK_KIND + '",\n'):
        return None
    try:
        handoff = json.loads(task)["handoff"]
        if task != task_text(handoff):
            return None
    except (ValueError, TypeError, KeyError, RecursionError):
        return None
    return handoff


def task_user_texts(task):
    """Project human requirement sources without reinterpreting message contents."""
    handoff = task_handoff(task)
    return None if handoff is None else [row["text"] for row in handoff["messages"] if row["role"] == "user"]

def _safe_file(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise ConversationProtocolError("Conversation protocol path escapes its allowed root.") from error
    if candidate.is_symlink():
        raise ConversationProtocolError("Conversation protocol files cannot be symbolic links.")
    return candidate


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = canonical_json(value) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".", delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def stage_handoff(workspace: Path, handoff: dict) -> Path:
    """Save a producer receipt under the selected project before runner launch."""
    workspace = Path(workspace).resolve()
    validated = validate_handoff(handoff)
    root = workspace / ".autocode" / "conversation-handoffs"
    path = root / (validated["digest"] + ".json")
    _safe_file(workspace, path)
    with _file_lock(root / ".handoffs.lock"):
        if path.exists():
            try:
                existing = validate_handoff(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise ConversationProtocolError("Existing conversation handoff is unreadable.") from error
            if existing["digest"] != validated["digest"]:
                raise ConversationProtocolError("Conversation handoff path conflicts with different data.")
        else:
            _atomic_json(path, validated)
    return path


def load_handoff(path: Path, *, workspace: Path | None = None) -> dict:
    path = Path(path)
    if workspace is not None:
        _safe_file(Path(workspace), path)
    try:
        return validate_handoff(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise ConversationProtocolError("Conversation handoff could not be read.") from error


def journal_file(run_dir: Path) -> Path:
    return Path(run_dir) / "conversation-journal.json"


def _journal_from_handoff(handoff: dict, source_path: Path | None) -> dict:
    source = {"kind": "producer_handoff", "digest": handoff["digest"]}
    if source_path is not None:
        source["path"] = str(source_path)
    return {
        "schema_version": HANDOFF_VERSION,
        "kind": JOURNAL_KIND,
        "authority": "runner",
        "conversation_id": handoff["conversation_id"],
        "handoff_digest": handoff["digest"],
        "source": source,
        "linked_at": now(),
        "conversation": {
            "id": handoff["conversation_id"], "title": handoff["title"], "created_at": handoff["created_at"],
            "status": "ready", "error": None, "messages": deepcopy(handoff["messages"]),
            "drafts": deepcopy(handoff["drafts"]), "requirements": deepcopy(handoff["requirements"]),
            "plan_drafts": deepcopy(handoff.get("plan_drafts", [])),
            "configured_routes": deepcopy(handoff["configured_routes"]),
            "request_logical_turns": deepcopy(handoff["request_logical_turns"]),
            "dispatches": deepcopy(handoff.get("dispatches", {})),
            "planner_dispatches": deepcopy(handoff.get("planner_dispatches", {})),
        },
        "provider_capabilities": capabilities(),
    }


def validate_journal(value) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != HANDOFF_VERSION or value.get("kind") != JOURNAL_KIND:
        raise ConversationProtocolError("Unsupported Requirements Conversation journal schema.")
    if value.get("authority") != "runner":
        raise ConversationProtocolError("Requirements Conversation journal authority is invalid.")
    _identifier(value.get("conversation_id"), "Conversation id", conversation=True)
    handoff_digest = value.get("handoff_digest")
    if not isinstance(handoff_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", handoff_digest):
        raise ConversationProtocolError("Journal handoff digest is invalid.")
    source = value.get("source")
    if not isinstance(source, dict) or source.get("kind") != "producer_handoff" or source.get("digest") != handoff_digest:
        raise ConversationProtocolError("Journal source pointer is invalid.")
    conversation = value.get("conversation")
    if not isinstance(conversation, dict) or conversation.get("id") != value["conversation_id"]:
        raise ConversationProtocolError("Journal conversation is invalid.")
    candidate = {
        "id": conversation["id"], "title": conversation.get("title"), "created_at": conversation.get("created_at"),
        "messages": conversation.get("messages"), "drafts": conversation.get("drafts", []),
        "requirements": conversation.get("requirements"), "configured_routes": conversation.get("configured_routes"),
        "plan_drafts": conversation.get("plan_drafts", []), "models": {},
    }
    # Validate the mutable runner conversation without requiring it to retain
    # the original handoff digest after attached turns are appended.
    handoff_from_document(candidate)
    if conversation.get("status") not in ("thinking", "ready", "error", "uncertain"):
        raise ConversationProtocolError("Journal conversation status is invalid.")
    turns = {row["logical_turn_id"] for row in conversation["messages"] if row["role"] == "user"}
    _dispatch_records(conversation.get("dispatches"), turns=turns)
    _dispatch_records(conversation.get("planner_dispatches", {}), turns=turns, planner=True)
    if value.get("provider_capabilities") != CAPABILITY_MATRIX:
        raise ConversationProtocolError("Journal capability matrix is invalid.")
    return deepcopy(value)


def ingest_handoff(run_dir: Path, handoff: dict, *, source_path: Path | None = None) -> dict:
    """Atomically transfer a validated producer handoff to one runner journal."""
    run_dir = Path(run_dir)
    validated = validate_handoff(handoff)
    path = journal_file(run_dir)
    with _file_lock(run_dir / ".conversation-journal.lock"):
        if path.exists():
            try:
                existing = validate_journal(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise ConversationProtocolError("Existing runner conversation journal is unreadable.") from error
            if (existing["conversation_id"] != validated["conversation_id"]
                    or existing["handoff_digest"] != validated["digest"]):
                raise ConversationProtocolError("A different conversation already owns this runner journal.")
            return existing
        journal = _journal_from_handoff(validated, source_path)
        _atomic_json(path, journal)
        return journal


def read_journal(run_dir: Path) -> dict:
    path = journal_file(run_dir)
    try:
        return validate_journal(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise ConversationProtocolError("Runner conversation journal could not be read.") from error


def update_journal(run_dir: Path, callback) -> dict:
    """Atomically mutate a journal and return its fully validated replacement."""
    run_dir = Path(run_dir)
    path = journal_file(run_dir)
    with _file_lock(run_dir / ".conversation-journal.lock"):
        try:
            journal = validate_journal(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as error:
            raise ConversationProtocolError("Runner conversation journal could not be read.") from error
        proposed = callback(deepcopy(journal))
        if proposed is None:
            proposed = journal
        proposed = validate_journal(proposed)
        _atomic_json(path, proposed)
        return proposed


def new_dispatch(*, logical_turn_id: str, client_request_id: str | None, route: dict) -> dict:
    _identifier(logical_turn_id, "Logical turn id")
    _identifier(client_request_id, "Client request id", optional=True)
    return {
        "id": uuid.uuid4().hex,
        "logical_turn_id": logical_turn_id,
        "client_request_id": client_request_id,
        "route": _route({"requirements_gatherer": route})["requirements_gatherer"],
        "state": "SAVED",
        "created_at": now(),
        "updated_at": now(),
        "history": [{"state": "SAVED", "at": now()}],
        "process": None,
        "provider_identity": None,
        "raw_result": None,
        "result": None,
        "reply_commit_id": None,
    }


def transition(dispatch: dict, state: str, **fields) -> dict:
    """Persist a monotonic dispatch transition without fabricating evidence."""
    if not isinstance(dispatch, dict) or dispatch.get("state") not in DISPATCH_STATES or state not in DISPATCH_STATES:
        raise ConversationProtocolError("Dispatch transition is invalid.")
    current = dispatch["state"]
    allowed = {
        "SAVED": {"DISPATCH_PREPARED", "SAFE_NOT_DISPATCHED"},
        # DISPATCH_PREPARED may capture a result directly when the configured
        # provider is an in-process adapter with no external dispatch boundary;
        # recording that result before commit is what prevents duplicates.
        "DISPATCH_PREPARED": {"PROCESS_STARTING", "SAFE_NOT_DISPATCHED", "UNCERTAIN", "RESULT_CAPTURED"},
        "PROCESS_STARTING": {"PROCESS_STARTED", "SAFE_NOT_DISPATCHED", "UNCERTAIN"},
        "PROCESS_STARTED": {"PROVIDER_IDENTIFIED", "RESULT_CAPTURED", "ACTIVE", "UNCERTAIN"},
        "PROVIDER_IDENTIFIED": {"RESULT_CAPTURED", "ACTIVE", "UNCERTAIN"},
        "ACTIVE": {"RESULT_CAPTURED", "UNCERTAIN"},
        "RESULT_CAPTURED": {"REPLY_COMMITTED"},
        "SAFE_NOT_DISPATCHED": {"DISPATCH_PREPARED"},
        "UNCERTAIN": {"ACTIVE", "RESULT_CAPTURED"},
        "REPLY_COMMITTED": set(),
    }
    if state != current and state not in allowed[current]:
        raise ConversationProtocolError(f"Dispatch cannot transition from {current} to {state}.")
    updated = deepcopy(dispatch)
    updated.update(deepcopy(fields))
    updated["state"] = state
    updated["updated_at"] = now()
    history = list(updated.get("history", []))
    if not history or history[-1].get("state") != state or fields:
        history.append({"state": state, "at": updated["updated_at"],
                        **({"details": deepcopy(fields)} if fields else {})})
    updated["history"] = history
    return updated


def public_delivery(dispatch: dict | None) -> dict | None:
    if not isinstance(dispatch, dict):
        return None
    state = dispatch.get("state")
    if state not in DISPATCH_STATES:
        return None
    status = ("uncertain" if state == "UNCERTAIN" else "active" if state == "ACTIVE" else
              "ready" if state == "REPLY_COMMITTED" else "retryable" if state == "SAFE_NOT_DISPATCHED" else
              "pending")
    return {
        "status": status,
        "state": state,
        "logical_turn_id": dispatch.get("logical_turn_id"),
        "client_request_id": dispatch.get("client_request_id"),
        "updated_at": dispatch.get("updated_at"),
    }

"""Durable intervention submissions and runner-owned inbox consumption."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
from pathlib import Path
import time
from typing import Any

try:
    from . import autocode_goals as goals, autocode_support as support
except ImportError:
    import autocode_goals as goals
    import autocode_support as support


INBOX_VERSION = 1
LOCK_TIMEOUT_SECONDS = 1.0
INBOX_NAME = "interventions.json"
LOCK_NAME = "interventions.lock"


class InterventionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _target(workspace: Path, run_dir: Path) -> tuple[Path, Path, dict[str, Any]]:
    try:
        workspace = Path(workspace).resolve(strict=True)
        run_dir = Path(run_dir).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise InterventionError("target_unavailable", f"Target is unavailable: {error}") from error
    runs_dir = (workspace / ".autocode" / "runs").resolve(strict=False)
    if not workspace.is_dir() or not (workspace / ".git").exists():
        raise InterventionError("invalid_workspace", f"Workspace is not a Git repository: {workspace}")
    if not runs_dir.is_dir() or not runs_dir.is_relative_to(workspace):
        raise InterventionError("invalid_runs_directory", "Workspace .autocode/runs escapes its canonical workspace")
    if not run_dir.is_dir() or not run_dir.is_relative_to(runs_dir):
        raise InterventionError("invalid_run_directory", "Run directory must be contained by workspace .autocode/runs")
    state_path = run_dir / "state.json"
    if state_path.is_symlink() or not state_path.is_file() or state_path.resolve().parent != run_dir:
        raise InterventionError("invalid_checkpoint", "Run state.json must be a regular file directly inside the run directory")
    try:
        state = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise InterventionError("invalid_checkpoint", f"Run state.json cannot be read: {error}") from error
    if not isinstance(state, dict) or state.get("workspace") != str(workspace):
        raise InterventionError("checkpoint_workspace_mismatch", "Checkpoint workspace does not match the canonical workspace")
    return workspace, run_dir, state


def _paths(run_dir: Path) -> tuple[Path, Path]:
    inbox = run_dir / INBOX_NAME
    lock = run_dir / LOCK_NAME
    if inbox.is_symlink() or lock.is_symlink():
        raise InterventionError("inbox_escape", "Intervention inbox or lock must not be a symlink")
    return inbox, lock


def _empty() -> dict[str, Any]:
    return {"version": INBOX_VERSION, "requests": []}


def _read_inbox(inbox: Path) -> dict[str, Any]:
    if not inbox.exists():
        return _empty()
    try:
        value = json.loads(inbox.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise InterventionError("corrupt_inbox", f"Intervention inbox cannot be read: {error}") from error
    if not isinstance(value, dict) or value.get("version") != INBOX_VERSION or not isinstance(value.get("requests"), list):
        raise InterventionError("unsupported_inbox", "Intervention inbox has an unsupported version or shape")
    seen_ids = set()
    for request in value["requests"]:
        if (not isinstance(request, dict) or not isinstance(request.get("id"), str) or not request["id"]
                or request.get("kind") not in {"feedback", "pause"} or not isinstance(request.get("text"), str)
                or not isinstance(request.get("order"), int) or isinstance(request["order"], bool) or request["order"] < 1
                or not isinstance(request.get("submitted_at"), str)
                or request.get("observed_goal_token") is not None and not isinstance(request["observed_goal_token"], str)
                or request.get("boundary_pause_requested") is not True or request["id"] in seen_ids):
            raise InterventionError("corrupt_inbox", "Intervention inbox has an invalid or duplicate request record")
        seen_ids.add(request["id"])
    return value


@contextlib.contextmanager
def _locked_inbox(lock: Path) -> Any:
    try:
        handle = lock.open("a+")
    except OSError as error:
        raise InterventionError("inbox_unavailable", f"Intervention inbox lock is unavailable: {error}") from error
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise InterventionError("lock_timeout", "Intervention inbox is busy; retry after a short wait")
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


def _payload(request_id: str, kind: str, text: str) -> dict[str, str]:
    if not isinstance(request_id, str) or not request_id:
        raise InterventionError("invalid_request_id", "Request ID must be a nonempty string")
    if kind not in {"feedback", "pause"}:
        raise InterventionError("invalid_kind", "Intervention kind must be feedback or pause")
    if not isinstance(text, str) or (kind == "feedback" and not text):
        raise InterventionError("invalid_text", "Feedback text must be a nonempty string")
    if kind == "pause" and text:
        raise InterventionError("invalid_text", "Pause requests do not accept feedback text")
    return {"id": request_id, "kind": kind, "text": text}


def _original_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Exclude runner-owned application metadata from the submitter's receipt."""
    return {key: value for key, value in receipt.items() if key not in {"applied_at", "resumed_at"}}


def submit(workspace: Path, run_dir: Path, *, request_id: str, kind: str, text: str) -> dict[str, Any]:
    """Persist one request, or return the original receipt for an identical retry."""
    workspace, run_dir, state = _target(workspace, run_dir)
    payload = _payload(request_id, kind, text)
    inbox, lock = _paths(run_dir)
    with _locked_inbox(lock):
        document = _read_inbox(inbox)
        existing = next((item for item in document["requests"] if item.get("id") == request_id), None)
        if existing is not None:
            if {key: existing.get(key) for key in payload} != payload:
                raise InterventionError("request_conflict", "Request ID is already used by a different payload")
            return {"version": INBOX_VERSION, "operation": "submit", "accepted": True,
                    "idempotent": True, "consumer": "pending_only", "receipt": existing}
        # The owner may have consumed this ID while this submitter waited for the
        # inbox lock, so inspect its just-committed authoritative ledger here.
        _, _, current_state = _target(workspace, run_dir)
        applied = next((item for item in current_state.get("applied_interventions", [])
                        if isinstance(item, dict) and item.get("id") == request_id), None)
        if applied is not None:
            if {key: applied.get(key) for key in payload} != payload:
                raise InterventionError("request_conflict", "Request ID is already used by a different payload")
            return {"version": INBOX_VERSION, "operation": "submit", "accepted": True,
                    "idempotent": True, "consumer": "already_applied", "receipt": _original_receipt(applied)}
        contract = current_state.get("goal_contract")
        try:
            token = goals.token(contract) if isinstance(contract, dict) else None
        except (KeyError, TypeError):
            # A malformed optional contract must not turn a safe submission failure
            # into an uncaught traceback or claim an authorization token.
            token = None
        history = document["requests"] + current_state.get("applied_interventions", [])
        order = 1 + max((item.get("order", 0) for item in history
                         if isinstance(item, dict) and type(item.get("order")) is int), default=0)
        receipt = {**payload, "order": order, "submitted_at": support.now(), "observed_goal_token": token,
                   "boundary_pause_requested": True}
        document["requests"].append(receipt)
        try:
            support.atomic_json(inbox, document)
        except OSError as error:
            raise InterventionError("write_failed", f"Intervention inbox update failed: {error}") from error
    return {"version": INBOX_VERSION, "operation": "submit", "accepted": True,
            "idempotent": False, "consumer": "pending_only", "receipt": receipt}


def inspect(workspace: Path, run_dir: Path) -> dict[str, Any]:
    """Read the inbox without creating storage, locks, or task state."""
    _, run_dir, _ = _target(workspace, run_dir)
    inbox, _ = _paths(run_dir)
    document = _read_inbox(inbox)
    return {"version": INBOX_VERSION, "operation": "inspect", "run_dir": str(run_dir),
            "consumer": "pending_only", "pending_count": len(document["requests"]),
            "requests": document["requests"]}


@contextlib.contextmanager
def serialized(run_dir: Path):
    """Order submission against short runner commits, never provider execution."""
    _, lock = _paths(run_dir)
    with _locked_inbox(lock):
        yield


def pending(run_dir: Path):
    """Read pending requests for an already validated runner-owned target."""
    inbox, _ = _paths(run_dir)
    return _read_inbox(inbox)["requests"]


@contextlib.contextmanager
def admission(run_dir: Path):
    """Authorize one launch/commit only if no intervention was accepted first."""
    try:
        with serialized(run_dir):
            inbox, _ = _paths(run_dir)
            if _read_inbox(inbox)["requests"]:
                raise support.Paused("PAUSED_INTERVENTION_PENDING",
                                     "Queued intervention must be applied before this action; explicitly continue.")
            yield
    except InterventionError as error:
        raise support.Paused("PAUSED_INTERVENTION_PENDING", str(error)) from error


def consume(run_dir: Path, state: dict[str, Any], *, write_state: Any, apply_feedback: Any,
            before_commit: Any = None, lock_held: bool = False) -> list[dict[str, Any]]:
    """Apply requests through the workspace-lock owner, then acknowledge them.

    The authoritative state ledger is written while holding the short inbox lock before
    requests are removed. A crash before that write leaves the inbox intact; a crash
    after it is recovered by recognizing the ledger and retrying only acknowledgement.
    """
    inbox, lock = _paths(run_dir)
    with contextlib.nullcontext() if lock_held else _locked_inbox(lock):
        document = _read_inbox(inbox)
        requests = sorted(document["requests"], key=lambda item: item["order"])
        applied = state.setdefault("applied_interventions", [])
        applied_ids = {item.get("id") for item in applied if isinstance(item, dict)}
        new = [item for item in requests if item["id"] not in applied_ids]
        if new:
            for receipt in new:
                applied_receipt = {**receipt, "applied_at": support.now()}
                if receipt["kind"] == "feedback":
                    apply_feedback(receipt, applied_receipt)
                applied.append(applied_receipt)
            if before_commit is not None:
                before_commit(new)
            state["intervention_ack_pending"] = [item["id"] for item in requests]
            write_state()
        if requests:
            document["requests"] = []
            try:
                support.atomic_json(inbox, document)
            except OSError as error:
                raise InterventionError("acknowledgement_failed", f"Intervention acknowledgement failed: {error}") from error
            state.pop("intervention_ack_pending", None)
            write_state()
        elif (ack_pending := state.get("intervention_ack_pending")) and isinstance(ack_pending, list):
            # A crash after clearing the inbox but before this final state write leaves
            # only an obsolete acknowledgement marker. Clear it during owner recovery.
            if all(request_id in applied_ids for request_id in ack_pending):
                state.pop("intervention_ack_pending", None)
                write_state()
        return new


def cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Submit or inspect pending Autocode interventions")
    commands = parser.add_subparsers(dest="operation", required=True)
    submit_parser = commands.add_parser("submit")
    submit_parser.add_argument("--workspace", required=True, type=Path)
    submit_parser.add_argument("--run-dir", required=True, type=Path)
    submit_parser.add_argument("--request-id", required=True)
    submit_parser.add_argument("--kind", required=True, choices=("feedback", "pause"))
    submit_parser.add_argument("--text", default="")
    submit_parser.add_argument("--json", action="store_true")
    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("--workspace", required=True, type=Path)
    inspect_parser.add_argument("--run-dir", required=True, type=Path)
    inspect_parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.operation == "submit":
            result = submit(args.workspace, args.run_dir, request_id=args.request_id, kind=args.kind, text=args.text)
        else:
            result = inspect(args.workspace, args.run_dir)
    except InterventionError as error:
        result = {"version": INBOX_VERSION, "operation": args.operation,
                  "error": {"code": error.code, "message": str(error)}}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0

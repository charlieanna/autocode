"""Exact-obligation scheduling policy and durable runner receipts.

This is not a cross-stage proof cache. A receipt belongs to one accepted check
obligation; Builder feedback, prescribed executions and independent phases are
never interchangeable. The caller supplies the runtime and source identity.
Only this module writes scheduling metadata in check-replay receipts; replay
and the public evidence/efficiency projections read it.
"""
from __future__ import annotations

import os
from pathlib import Path
import uuid

try:
    from . import autocode_util as util, autocode_command_receipt as command_receipt
    from . import autocode_python_tests as python_tests
except ImportError:
    import autocode_util as util
    import autocode_command_receipt as command_receipt
    import autocode_python_tests as python_tests


def tree_identity(root, *, excluded=()):
    """Hash dependency contents, including symlink targets, not just lock files."""
    root = Path(root)
    if not root.exists():
        return None
    if root.is_file():
        return {"path": str(root.resolve()), "sha256": util.file_hash(root),
                "mode": root.stat().st_mode & 0o777}
    rows, visited = {}, set()
    for directory, dirs, files in os.walk(root, followlinks=True):
        dirs[:] = [name for name in dirs if name not in excluded]
        files = [name for name in files if name not in excluded]
        resolved = str(Path(directory).resolve())
        if resolved in visited:
            dirs[:] = []
            continue
        visited.add(resolved)
        for name in sorted(dirs + files):
            path = Path(directory) / name
            relative = str(path.relative_to(root))
            if path.is_symlink():
                rows[relative + "/link"] = str(path.resolve())
            if path.is_file():
                rows[relative] = [util.file_hash(path), path.stat().st_mode & 0o777]
    return {"path": str(root.resolve()), "sha256": util.digest(rows)}


def collection_kind(command):
    """A known exact Python collector, not shell text resembling a test suite."""
    invocation = python_tests.parse(command)
    return invocation.kind if invocation else None


def complete_results(receipt):
    """Only actual, fully attributed runner results establish test coverage."""
    result = receipt.get("results")
    if not isinstance(result, dict) or result.get("complete") is not True:
        return False
    groups = [result.get(name) for name in ("passed", "failed", "skipped")]
    if any(not isinstance(group, list) or any(not isinstance(x, str) or not x for x in group)
           for group in groups):
        return False
    ids = [item for group in groups for item in group]
    # Only entries that never imported, collected or built break attribution; a failed
    # hook or fixture executed its test. Results from parsers that predate ``uncollected``
    # (saved receipts) fall back to collection_errors, so they fail closed.
    uncollected = result.get("uncollected", result.get("collection_errors"))
    return (type(result.get("total")) is int and result["total"] > 0
            and len(ids) == len(set(ids)) == result["total"]
            and not uncollected)


def intact(receipt, *, root=None):
    """Output must still be the original complete capture, never a model claim."""
    try:
        output = Path(receipt["output"])
        if root is not None:
            root = Path(root)
            if not output.resolve().is_relative_to(root.resolve()):
                return False
            if any(parent.is_symlink() for parent in output.parents
                   if parent != root and parent.is_relative_to(root)):
                return False
        return (command_receipt.completed(receipt, root=root)
                and not output.is_symlink() and output.is_file()
                and util.file_hash(output) == receipt["output_sha256"])
    except (OSError, KeyError, TypeError):
        return False


def reusable(receipt, *, root=None):
    return (type(receipt.get("exit_code")) is int and receipt["exit_code"] == 0
            and not receipt.get("timed_out") and not receipt.get("error")
            and complete_results(receipt) and bool(receipt["results"]["passed"])
            and not receipt["results"]["failed"] and intact(receipt, root=root))


def _completed(directory):
    pointer = directory / "completed.json"
    try:
        if directory.is_symlink() or pointer.is_symlink():
            return None
        reference = util.read(pointer)
        relative = Path(reference["name"])
        if len(relative.parts) != 2 or relative.name != "receipt.json" or len(relative.parts[0]) != 32:
            return None
        receipt_path = directory / reference["name"]
        if (not receipt_path.resolve().is_relative_to(directory.resolve())
                or any(p.is_symlink() for p in (receipt_path, receipt_path.parent))
                or util.file_hash(receipt_path) != reference["sha256"]):
            return None
        saved = util.read(receipt_path)
        if (util.digest(saved["identity"]) != directory.name or saved.get("runner_owned") is not True
                or not isinstance(saved.get("result"), dict)
                or any(not isinstance(saved.get(key), str) for key in ("attempt_id", "started_at", "finished_at"))):
            return None
        return saved, receipt_path, reference
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _reconcile_pending(root):
    pending = root / "pending.json"
    if pending.is_symlink():
        raise util.Paused("PAUSED_VERIFICATION_UNCERTAIN", "Verification receipt pointer is symlinked")
    if not pending.exists():
        return
    try:
        active = util.read(pending)
        completed = _completed(root / util.digest(active["identity"]))
        finished = completed and completed[0].get("attempt_id") == active["attempt_id"]
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        finished = False
    if not finished:
        raise util.Paused("PAUSED_VERIFICATION_UNCERTAIN",
                          "A clean verification launch has no completed receipt; reconcile its owned "
                          f"processes before retrying. Evidence: {pending}")
    pending.unlink()


def guard(root):
    """No provider, regression or protected check may bypass an uncertain replay."""
    root = Path(root)
    if not root.exists():
        return
    if any(path.is_symlink() for path in (root, root.parent, root / "writer.lock")):
        raise util.Paused("PAUSED_VERIFICATION_UNCERTAIN", "Verification receipt directory is symlinked")
    with util.run_lock(root):
        _reconcile_pending(root)


def _finish(pending, directory, out, pointer, completed):
    """Make one attempt's receipt durable, then clear the pending launch."""
    receipt_path = out / "receipt.json"
    util.atomic_json(receipt_path, completed)
    sha = util.file_hash(receipt_path)
    # The pointer names a nested attempt, never a caller-supplied path.
    util.atomic_json(pointer, {"name": str(receipt_path.relative_to(directory)), "sha256": sha})
    pending.unlink()
    return receipt_path, sha


def run(directory, identity, execute, *, reuse_allowed, reason, current_identity):
    """Execute or resume exactly one obligation, preserving every attempt.

    An attempt that ends in an exception is known-failed state, not uncertainty:
    it is recorded as a failed receipt (never reused) and its pending launch is
    cleared, so the next attempt runs a fresh check instead of pausing forever
    (#414). A hard crash or uncertain command ownership leaves the pending launch for
    a person to reconcile; an exception cannot authorize another unsafe launch. Completed receipts
    survive controller restart without a second launch.
    """
    root = Path(directory)
    directory = root / util.digest(identity)
    if any(path.is_symlink() for path in (directory, directory.parent, directory.parent.parent, root / "writer.lock")):
        raise util.Paused("PAUSED_VERIFICATION_UNCERTAIN", "Verification receipt directory is symlinked")
    started_at = util.now()
    directory.mkdir(parents=True, exist_ok=True)
    # Linked test dependencies can be mutable. One admission lock and pending
    # launch cover ALL identities in this run, including changed-source retries.
    with util.run_lock(root):
        pending = root / "pending.json"
        pointer = directory / "completed.json"
        if pending.is_symlink() or pointer.is_symlink():
            raise util.Paused("PAUSED_VERIFICATION_UNCERTAIN", "Verification receipt pointer is symlinked")
        # Completion is durable before clearing pending. This closes the
        # crash-after-completion window without launching a second command.
        _reconcile_pending(root)
        completed = _completed(directory)
        saved, receipt_path, reference = completed if completed else (None, None, None)
        if pointer.exists() and not saved:
            reason = "missing_or_tampered_receipt"
        if reuse_allowed and saved and reusable(saved["result"], root=directory):
            if current_identity() == identity:
                result = saved["result"]
                return {**result, "scheduling": {"action": "reuse", "reason": "same_obligation_complete_proof",
                        "receipt": str(receipt_path), "receipt_sha256": reference["sha256"],
                        "identity": util.digest(identity), "attempt_id": saved["attempt_id"],
                        "started_at": started_at, "finished_at": util.now(),
                        "original_started_at": saved["started_at"], "original_finished_at": saved["finished_at"],
                        "original_reason": saved.get("reason", "unknown"),
                        "original_duration_seconds": result.get("duration_seconds")}}
            reason = "execution_context_changed"
        elif saved and reuse_allowed:
            reason = "failed_partial_or_missing_output"
        attempt = uuid.uuid4().hex
        out = directory / attempt
        util.atomic_json(pending, {"attempt_id": attempt, "identity": identity, "started_at": started_at})
        result = None
        try:
            result = execute(out)
            after = current_identity()
        except command_receipt.OwnershipUncertain:
            # In-process unwind cannot establish that owned commands stopped.
            # Retain pending so guard blocks every identity until reconciliation.
            raise
        except BaseException as error:
            failed = dict(result) if isinstance(result, dict) else {}
            failed["error"] = f"Verification attempt did not complete ({type(error).__name__}): {error}"
            _finish(pending, directory, out, pointer, {"runner_owned": True, "attempt_id": attempt,
                     "identity": identity, "reason": reason, "started_at": started_at,
                     "finished_at": util.now(), "result": failed})
            raise
        if after != identity:
            result = {**result, "error": "Source, runtime, dependencies or environment changed during verification",
                      "observed_after_identity": after}
        completed = {"runner_owned": True, "attempt_id": attempt, "identity": identity, "reason": reason,
                     "started_at": started_at, "finished_at": util.now(), "result": result}
        receipt_path, sha = _finish(pending, directory, out, pointer, completed)
        return {**result, "scheduling": {"action": "execute", "reason": reason,
                "receipt": str(receipt_path), "receipt_sha256": sha, "identity": util.digest(identity),
                "attempt_id": attempt, "started_at": started_at, "finished_at": completed["finished_at"],
                "original_duration_seconds": result.get("duration_seconds")}}

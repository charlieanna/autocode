"""Exact-obligation scheduling policy and durable runner receipts.

This is not a cross-stage proof cache. A receipt belongs to one accepted check
obligation; Builder feedback, prescribed executions and independent phases are
never interchangeable. The caller supplies the runtime and source identity.
Only this module writes scheduling metadata in check-replay receipts; replay
and the public evidence/efficiency projections read it.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_command_supervision as command_supervision
    from . import autocode_python_tests as python_tests
    from . import autocode_util as util
    from . import autocode_verification_preparation as preparation
    from . import autocode_verification_recovery as recovery
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_command_supervision as command_supervision
    import autocode_python_tests as python_tests
    import autocode_util as util
    import autocode_verification_preparation as preparation
    import autocode_verification_recovery as recovery


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


def recover_interrupted(root, check):
    """Explicitly retire exactly one authenticated, stopped pending obligation.

    Uncollected exits retain interrupted evidence. An authenticated original
    receipt is preserved at either publication boundary. Normal guard/dispatch
    cannot call this reconciliation implicitly.
    """
    root = Path(root)
    if not root.exists():
        return None
    recovery.owned_path(root, root, directory=True)
    lock = root / 'writer.lock'
    if lock.exists() or lock.is_symlink():
        recovery.owned_path(lock, root)
    with util.run_lock(root):
        pending = root / 'pending.json'
        if not pending.exists() and not pending.is_symlink():
            return None
        try:
            active, pending_sha = recovery.read_regular(pending, root)
            authenticated = (recovery.authenticate_publication(root, active)
                if active.get('preparation_admission') and active.get('phase') == 'post_execution_identity'
                else recovery.authenticate(root, active, check))
            out = authenticated['out']
            directory, pointer = out.parent, out.parent / 'completed.json'
            saved = _recovery_completed(directory, root)
            if saved and saved[0]['attempt_id'] == active['attempt_id']:
                # A crash after durable publication only needs the pointer retired.
                if saved[1] != out / 'receipt.json' or saved[0]['identity'] != active['identity']:
                    recovery.hold('completed receipt does not bind the admitted attempt')
                if (util.file_hash(pending) != pending_sha
                        or util.file_hash(pointer) != saved[3]
                        or util.file_hash(saved[1]) != saved[2]['sha256']
                        or any(util.file_hash(name) != digest for name, digest in authenticated['pins'].items())):
                    recovery.hold('completed admission changed before retirement')
                pending.unlink()
                return {'receipt': str(saved[1]), 'receipt_sha256': saved[2]['sha256'],
                        'attempt_id': active['attempt_id'], 'pending_sha256': pending_sha,
                        'disposition': 'existing_completed_receipt'}
            path = out / 'receipt.json'
            completed = {'runner_owned': True, 'attempt_id': active['attempt_id'],
                         'identity': active['identity'], 'pending_admission': active,
                         'reason': 'explicit_interrupted_recovery', 'started_at': active['started_at'],
                         'finished_at': util.now(), 'ownership_pins': authenticated['pins'],
                         'result': authenticated['result']}
            disposition = 'interrupted_receipt'
            receipt_sha = None
            if path.exists() or path.is_symlink():
                existing, receipt_sha = recovery.read_regular(path, root)
                # Resume a crash after failed receipt publication without rewriting
                # its original time, context or evidence. Conflicting bytes stay held.
                expected = {k: v for k, v in completed.items() if k != 'finished_at'}
                actual = {k: v for k, v in existing.items() if k != 'finished_at'}
                if actual == expected and isinstance(existing.get('finished_at'), str):
                    pass
                elif _unpublished_completed(existing, active, authenticated):
                    disposition = 'published_existing_completed_receipt'
                else:
                    recovery.hold('an existing attempt receipt conflicts with this recovery')
            for filename, digest in authenticated['pins'].items():
                if util.file_hash(filename) != digest:
                    recovery.hold('authenticated admission changed before publication')
            if util.file_hash(pending) != pending_sha:
                recovery.hold('pending admission changed before publication')
            if receipt_sha is not None and util.file_hash(path) != receipt_sha:
                recovery.hold('existing attempt receipt changed before publication')
            if (disposition == 'published_existing_completed_receipt'
                    and util.file_hash(authenticated['result']['output']) != authenticated['result']['output_sha256']):
                recovery.hold('original command output changed before publication')
            if not path.exists():
                util.atomic_json(path, completed)
            digest = util.file_hash(path)
            util.atomic_json(pointer, {'name': str(path.relative_to(directory)), 'sha256': digest})
            if (util.file_hash(path) != digest or util.file_hash(pending) != pending_sha
                    or any(util.file_hash(name) != pin for name, pin in authenticated['pins'].items())
                    or (disposition == 'published_existing_completed_receipt'
                        and util.file_hash(authenticated['result']['output']) != authenticated['result']['output_sha256'])):
                recovery.hold('authenticated admission or receipt changed before retirement')
            pending.unlink()
            return {'receipt': str(path), 'receipt_sha256': digest,
                    'attempt_id': active['attempt_id'], 'pending_sha256': pending_sha,
                    'disposition': disposition}
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as error:
            recovery.hold(str(error))


def _unpublished_completed(saved, active, authenticated):
    """Authenticate an original collected result whose pointer was not saved.

    This does not fill any result field or infer an exit from stopped ownership.
    Only a complete original command receipt with the exact admitted context can
    be published. Its immutable bytes remain the scheduler's original evidence.
    """
    result, bound = saved.get('result'), authenticated['result']
    return (saved.get('runner_owned') is True
            and all(saved.get(key) == active[key] for key in ('attempt_id', 'identity', 'started_at'))
            and all(isinstance(saved.get(key), str) and saved[key] for key in ('finished_at', 'reason'))
            and isinstance(result, dict)
            and type(result.get('exit_code')) is int
            and all(result.get(key) == bound[key] for key in
                    ('command', 'output', 'output_sha256', 'supervision', 'supervision_sha256'))
            and (command_receipt.completed(result, root=authenticated['out'])
                 or authenticated.get('collected_failed_result') is True))


def _recovery_completed(directory, root):
    """Read a prior publication with bounded regular-file authentication."""
    pointer = directory / 'completed.json'
    if not pointer.exists() and not pointer.is_symlink():
        return None
    reference, pointer_sha = recovery.read_regular(pointer, root)
    relative = Path(reference['name'])
    if (len(relative.parts) != 2 or relative.name != 'receipt.json'
            or len(relative.parts[0]) != 32
            or any(char not in '0123456789abcdef' for char in relative.parts[0])):
        recovery.hold('completed receipt pointer is corrupt or unbound')
    path = recovery.owned_path(directory / relative, root)
    saved, digest = recovery.read_regular(path, root)
    if (digest != reference['sha256'] or util.digest(saved['identity']) != directory.name
            or saved.get('runner_owned') is not True or not isinstance(saved.get('result'), dict)
            or saved.get('attempt_id') != relative.parts[0]
            or any(not isinstance(saved.get(key), str) for key in ('started_at', 'finished_at'))):
        recovery.hold('completed receipt pointer is corrupt or unbound')
    return saved, path, reference, pointer_sha


def run(directory, identity, execute, *, reuse_allowed, reason, current_identity, owned_preparation=False):
    """Execute or resume exactly one obligation, preserving every attempt.

    An attempt that ends in an exception is known-failed state, not uncertainty:
    it is recorded as a failed receipt (never reused) and its pending launch is
    cleared, so the next attempt runs a fresh check instead of pausing forever
    (#414). A hard crash or uncertain command ownership leaves the pending launch for
    a person to reconcile; an exception cannot authorize another unsafe launch. Completed receipts
    survive controller restart without a second launch.
    """
    root = Path(directory)
    if any(path.is_symlink() for path in (root, root.parent, root.parent.parent, root / 'writer.lock')):
        raise util.Paused('PAUSED_VERIFICATION_UNCERTAIN', 'Verification receipt directory is symlinked')
    # The platform's temporary-directory alias is not an owned child symlink.
    # Canonicalize this trusted root once, before publishing its admission.
    root = root.resolve()
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
        active = {"attempt_id": attempt, "identity": identity, "started_at": started_at}
        reference = preparation.allocate(out, identity, attempt, started_at) if owned_preparation else None
        if reference:
            active.update(preparation_admission=reference, phase='admitted')
        util.atomic_json(pending, active)
        result = None
        try:
            if reference:
                previous = command_supervision.CHECKPOINT.get()
                def custody(command, output, metadata):
                    # The keeper's bootstrap is not released until both this
                    # exact pending frontier and the caller's checkpoint persist.
                    path = recovery.owned_path(output, out)
                    if path.parent != out:
                        recovery.hold('scheduled command output is outside its attempt')
                    phase = preparation.PHASE.get() or {'phase': 'executing'}
                    active.update(phase=phase['phase'], current_check={**phase, 'command': command,
                        'output': str(path), 'supervision': metadata, 'processes': [metadata['owner']]})
                    util.atomic_json(pending, active)
                    if previous is not None:
                        previous(command, output, metadata)
                token = command_supervision.CHECKPOINT.set(custody)
                try:
                    with preparation.admitted(reference):
                        result = execute(out)
                    if 'current_check' not in active:
                        recovery.hold('scheduled verification never admitted its native preparation')
                    # Context measurement remains the caller's operation. A
                    # crash here must not borrow custody from a finished command.
                    active.update(phase='post_execution_identity', completed_check=active['current_check'], current_check=None)
                    util.atomic_json(pending, active)
                    after = current_identity()
                finally:
                    command_supervision.CHECKPOINT.reset(token)
            else:
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
            result = {**result, "error": recovery.CONTEXT_CHANGED_ERROR,
                      "observed_after_identity": after}
        completed = {"runner_owned": True, "attempt_id": attempt, "identity": identity, "reason": reason,
                     "started_at": started_at, "finished_at": util.now(), "result": result}
        receipt_path, sha = _finish(pending, directory, out, pointer, completed)
        return {**result, "scheduling": {"action": "execute", "reason": reason,
                "receipt": str(receipt_path), "receipt_sha256": sha, "identity": util.digest(identity),
                "attempt_id": attempt, "started_at": started_at, "finished_at": completed["finished_at"],
                "original_duration_seconds": result.get("duration_seconds")}}

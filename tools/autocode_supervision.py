"""A provider stays bounded when its controller disappears.

The controller keeps its actual Popen child. A blocked exec bootstrap closes the
launch-before-ownership gap; a separate-session sibling keeper owns no provider
streams or checkout lock. This module writes only its caller-supplied receipt,
never run state. The caller persists metadata before provider release and closes
this context after its existing process supervisor has cleaned the provider.
"""

from __future__ import annotations

import io
import json
import math
import os
import select
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager, suppress
from pathlib import Path

try:
    from . import autocode_command_receipt as receipts
    from . import autocode_elapsed_timing as elapsed_timing
    from . import autocode_process as processes
    from . import autocode_session_boundary as boundary
    from . import autocode_supervision_handoff as handoff
    from . import autocode_util as util
except ImportError:
    import autocode_command_receipt as receipts
    import autocode_elapsed_timing as elapsed_timing
    import autocode_process as processes
    import autocode_session_boundary as boundary
    import autocode_supervision_handoff as handoff
    import autocode_util as util


class SupervisionError(processes.ProcessError):
    pass


class LaunchRefused(SupervisionError):
    """The host refused the launch before any process started (#2296).

    Nothing ran: no keeper, no provider, no receipt. The caller may treat the
    attempt as not started instead of as an uncertain stage.
    """


class LaunchLimitExpired(SupervisionError):
    """The launch limit passed before the keeper acknowledged (#989)."""


class VerifiedStop(SupervisionError):
    """The provider stopped abnormally, but its keeper verified complete cleanup."""

    def __init__(self, cause):
        super().__init__(f"Provider supervision interrupted: {cause}")
        self.cause = cause


def launch_options_identity(options):
    """Nonsecret identity of the actual inherited launch descriptors/options.

    Capture before Popen closes any parent transport descriptors. An envelope
    retains this original value; a reconstructed dictionary grants no authority.
    """

    def descriptor(value, inherited=None):
        fd = inherited if value is None else value if isinstance(value, int) else value.fileno()
        if fd < 0:
            return {"special": fd}
        saved = os.fstat(fd)
        return {"fd": fd, "device": saved.st_dev, "inode": saved.st_ino, "mode": saved.st_mode}

    return {
        "environment_sha256": util.digest(options.get("env")),
        "cwd": str(Path(options.get("cwd") or os.getcwd()).absolute()),
        "start_new_session": options.get("start_new_session"),
        "close_fds": options.get("close_fds", True),
        "stdio": [descriptor(options.get(key), index) for index, key in enumerate(("stdin", "stdout", "stderr"))],
        "pass_fds": [descriptor(fd) for fd in options.get("pass_fds", ())],
    }


class _LaunchEnvelope:
    """Original process-local outer exec authority; never restored from facts."""

    def __init__(self, child, keeper, command, bootstrap, options, metadata, release_fd):
        self._child, self._keeper, self._pid = child, keeper, os.getpid()
        self._command, self._bootstrap = tuple(command), tuple(bootstrap)
        self._metadata = json.dumps(metadata, sort_keys=True)
        self._options = json.dumps(launch_options_identity(options), sort_keys=True)
        self._pins = {
            str(Path(__file__).resolve()): util.file_hash(Path(__file__)),
            str(Path(__file__).with_name("autocode_supervision_bootstrap.py").resolve()): util.file_hash(
                Path(__file__).with_name("autocode_supervision_bootstrap.py")
            ),
        }
        saved = os.fstat(release_fd)
        self._release = {"fd": release_fd, "device": saved.st_dev, "inode": saved.st_ino}
        self._active, self._released = True, False

    def authenticate(self, child, *, command, environment_sha256, options_identity=None, tree=None):
        def require(value, message):
            if not value:
                raise SupervisionError("Held launch envelope: " + message)

        require(
            self._pid == os.getpid()
            and self._active
            and self._released
            and child is self._child
            and isinstance(child, subprocess.Popen)
            and getattr(child, "_autocode_launch_envelope", None) is self,
            "missing original active Popen handle",
        )
        metadata, options = json.loads(self._metadata), json.loads(self._options)
        require(tuple(child.args) == self._bootstrap and tuple(command) == self._command, "outer or inner argv changed")
        require(
            environment_sha256 == options["environment_sha256"]
            and (options_identity is None or options_identity == options),
            "environment or inherited descriptors changed",
        )
        require(
            child.supervision == metadata,
            "original keeper admission changed: supervision metadata differs",
        )
        require(
            self._keeper.pid == metadata["keeper"]["pid"],
            "original keeper admission changed: keeper PID differs",
        )
        require(
            self._keeper.poll() is None,
            "original keeper admission changed: keeper process stopped",
        )
        require(
            not child.supervision_errors,
            "original keeper admission changed: supervision errors reported",
        )
        require(
            all(util.file_hash(Path(path)) == pin for path, pin in self._pins.items()), "outer bootstrap source changed"
        )
        current = receipt(metadata)
        require(
            current is not None
            and current.get("phase") == "armed"
            and bool(processes.live_processes([metadata["keeper"]]))
            and bool(processes.live_processes([metadata["provider"]])),
            "keeper or root birth is no longer live",
        )
        if tree is not None:
            require(
                type(tree) is processes.ProcessTree
                and tree.pid == child.pid
                # The admitted identity also records SID; the tree root carries only birth fields.
                and processes.matches(metadata["provider"], tree.capture_root())
                and tree.capture_root().get("group") == metadata["provider"].get("group"),
                "actual supervisor root birth differs",
            )
            # Epoch/display birth times can move with the host clock (#1117).
            # Keep original kernel birth, group and SID as independent checks.
            actual = _identity(child.pid)
            require(
                processes.matches(metadata["provider"], actual)
                and actual.get("group") == metadata["provider"].get("group")
                and actual.get("session") == metadata["provider"].get("session"),
                "actual supervisor root birth or session differs",
            )
        return {
            "version": 1,
            "outer_argv": list(self._bootstrap),
            "inner_argv": list(self._command),
            "options": options,
            "supervision": metadata,
            "release_descriptor": dict(self._release),
            "bootstrap_pins": dict(self._pins),
        }


def launch_envelope(child):
    """Return only the same supervisor-issued handle, never saved metadata."""
    value = getattr(child, "_autocode_launch_envelope", None)
    if type(value) is not _LaunchEnvelope or value._child is not child:
        raise SupervisionError("Missing original supervision launch envelope")
    return value


_RECEIPT_READ_PHASES = frozenset({"metadata", "open", "stat", "read", "parse", "validate"})
_RECEIPT_READ_REASONS = frozenset(
    {
        "metadata_invalid",
        "not_found",
        "access_denied",
        "io_error",
        "size_limit",
        "text_decode_invalid",
        "json_invalid",
        "value_invalid",
        "object_invalid",
        "schema_invalid",
        "nonce_mismatch",
        "owner_mismatch",
        "keeper_mismatch",
        "provider_mismatch",
    }
)


def _open_receipt(path):
    """Allow 100 ms of initial-open visibility lag during atomic replacement."""
    for attempt in range(5):
        try:
            return path.open("rb")
        except FileNotFoundError:
            if attempt == 4:
                raise
            # A Mac Docker bind can briefly hide the replaced directory entry.
            # Never cache old evidence or retry failures after a descriptor opens.
            time.sleep(0.025)


def _read_receipt(metadata):
    """Size-check and read one opened receipt; only initial absence is retried."""
    phase = "metadata"
    try:
        path = Path(metadata["receipt"])
        phase = "open"
        with _open_receipt(path) as stream:
            phase = "stat"
            if os.fstat(stream.fileno()).st_size > receipts.MAX_RECEIPT_BYTES:
                return None, (phase, "size_limit")
            phase = "read"
            data = stream.read(receipts.MAX_RECEIPT_BYTES + 1)
        if len(data) > receipts.MAX_RECEIPT_BYTES:
            return None, (phase, "size_limit")
        # Preserve Path.read_text's default encoding, newline and decoder semantics.
        with io.TextIOWrapper(io.BytesIO(data)) as text_stream:
            text = text_stream.read()
        phase = "parse"
        value = json.loads(text)
        phase = "validate"
        if not isinstance(value, dict):
            return None, (phase, "object_invalid")
        if value.get("schema") != 1:
            return None, (phase, "schema_invalid")
        for key in ("nonce", "owner", "keeper", "provider"):
            if value.get(key) != metadata.get(key):
                return None, (phase, key + "_mismatch")
        return value, None
    except (OSError, ValueError, KeyError, TypeError) as error:
        if phase == "metadata":
            reason = "metadata_invalid"
        elif isinstance(error, FileNotFoundError):
            reason = "not_found"
        elif isinstance(error, PermissionError):
            reason = "access_denied"
        elif isinstance(error, OSError):
            reason = "io_error"
        elif phase == "read" and isinstance(error, UnicodeError):
            reason = "text_decode_invalid"
        elif phase == "parse" and isinstance(error, json.JSONDecodeError):
            reason = "json_invalid"
        else:
            reason = "value_invalid"
        return None, (phase, reason)


def receipt(metadata):
    """Read a bounded receipt bound to one launch; missing/invalid is unknown."""
    return _read_receipt(metadata)[0]


def observe(metadata):
    """Fresh, read-only owner/keeper/provider liveness; denied access is unknown."""
    result = {"receipt": receipt(metadata), "owner": None, "keeper": None, "provider": None}
    for key in ("owner", "keeper", "provider"):
        saved = metadata.get(key) if isinstance(metadata, dict) else None
        if not isinstance(saved, dict) or "birth_identity" not in saved:
            continue
        try:
            result[key] = {"checked": True, "alive": bool(processes.live_processes([saved]))}
        except (processes.ProcessError, OSError, ValueError, KeyError, TypeError):
            result[key] = {"checked": False, "alive": None}
    return result


def _identity(pid):
    row = processes.process_table({pid}).get(pid)
    if not row or row["state"].startswith("Z"):
        raise SupervisionError("Cannot capture supervision process birth identity")
    # Persist SID only for the few admitted launch identities, not every
    # sampled descendant in the bounded process-receipt inventory.
    return {**processes.identity(row), "session": row["session"]}


def _read_line(fd, deadline):
    data = bytearray()
    while len(data) <= 65536:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise LaunchLimitExpired("Keeper did not arm within the launch limit")
        chunk = os.read(fd, 1)
        if not chunk:
            raise SupervisionError("Keeper exited before acknowledging ownership")
        if chunk == b"\n":
            return json.loads(data)
        data.extend(chunk)
    raise SupervisionError("Keeper acknowledgement exceeded its limit")


def _keeper_stderr_tail(path, limit=512):
    """The last bytes of the keeper's captured stderr (#815): the missing-import
    class of death wrote its cause to stderr that DEVNULL used to discard."""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _annotated(error, stderr_path):
    tail = _keeper_stderr_tail(stderr_path)
    if not tail:
        return error
    return SupervisionError(f"{error}; keeper stderr tail: {tail!r}")


def _unread_bytes(fd):
    """Bytes still waiting in a pipe, read through its write end; None when unknown.

    Linux answers FIONREAD on either end of a pipe. Other hosts count only the
    end that was asked, so a write end there would falsely look fully read.
    """
    if not sys.platform.startswith("linux"):
        return None
    import fcntl
    import termios

    try:
        return int.from_bytes(fcntl.ioctl(fd, termios.FIONREAD, bytes(4)), sys.byteorder, signed=True)
    except OSError:
        return None


def _unarmed(error, *, limit, started, marks, keeper, metadata, control_fd):
    """#989: say where a launch limit went when the keeper never acknowledged.

    Read-only and bounded, after the limit has already been enforced: the
    parent's own launch steps, one exit poll and one process row for the
    keeper, the unread bytes of its ownership frame, and its receipt. It never
    extends the limit, retries, or releases the provider; a probe that fails
    reports unknown rather than replacing the original error.
    """
    seen = [f"limit {limit:g}s", *(f"{label} +{moment - started:.2f}s" for label, moment in marks)]
    try:
        code = keeper.poll()
        if code is None:
            row = processes.process_table({keeper.pid}).get(keeper.pid)
            seen.append(f"keeper alive ({row['state']})" if row else "keeper gone")
        else:
            seen.append(f"keeper exited {code}")
    except Exception:  # A diagnostic must never replace the launch-limit error.
        seen.append("keeper unknown")
    unread = _unread_bytes(control_fd)
    if unread is None:
        seen.append("ownership frame unknown")
    else:
        seen.append("ownership frame read" if unread == 0 else f"ownership frame unread ({unread} bytes)")
    try:
        current = receipt(metadata)
        seen.append("no receipt" if current is None else f"receipt {current.get('phase')}")
    except Exception:  # receipt() already maps unreadable to None; stay defensive.
        seen.append("receipt unknown")
    return LaunchLimitExpired(f"{error} ({'; '.join(seen)})")


def _send(fd, value):
    data = (json.dumps(value, separators=(",", ":")) + "\n").encode()
    while data:
        data = data[os.write(fd, data) :]


def _terminal_clean(value, *, allow_deadline=False):
    return bool(
        value
        and value.get("phase") == "stopped"
        and value.get("cleanup_error") is None
        and (
            value.get("cause") in receipts.NORMAL_CAUSES or (allow_deadline and value.get("cause") == "stage_deadline")
        )
    )


def session_handoff_reply(metadata, raw, deadline):
    """Answer a keeper's bound notice; cancelled callers never gain a grant.

    A terminal clean receipt makes an old queued notice obsolete. Otherwise
    the keeper must validate a cancellation's kernel cookie before accepting
    it; this reply does not assert that a syscall ran or succeeded.
    """
    notice = json.loads(raw)
    if (
        len(raw) > 65536
        or not isinstance(notice, dict)
        or set(notice) != {"session_admission", "request", "guard"}
        or notice["guard"] != metadata["nonce"]
        or type(notice["request"]) is not int
        or not 0 <= notice["request"] < 1 << 64
    ):
        raise SupervisionError("Invalid session ownership handoff")
    record = notice["session_admission"]
    creator = record.get("creator") if isinstance(record, dict) else None
    if (
        not isinstance(creator, dict)
        or record.get("kind") != "setsid_pending"
        or type(creator.get("pid")) is not int
        or creator["pid"] <= 0
        or type(creator.get("birth_identity")) not in (int, float)
        or not math.isfinite(creator["birth_identity"])
        or creator["birth_identity"] <= 0
        or type(record.get("requested_session")) is not int
        or record["requested_session"] != creator["pid"]
    ):
        raise SupervisionError("Invalid session ownership creator")
    current, diagnostic = _read_receipt(metadata)
    if current is None:
        error = SupervisionError("Session ownership receipt is unavailable")
        error.__dict__["_receipt_diagnostic"] = diagnostic
        raise error
    if _terminal_clean(current):
        return None
    if current.get("phase") != "armed" or current.get("cleanup_error") is not None:
        raise SupervisionError("Session ownership is no longer armed")
    cancelled = {"session_cancelled": notice["request"], "guard": metadata["nonce"]}
    if record not in current.get("session_admissions", []):
        return cancelled
    fresh = processes.process_table({creator["pid"]}).get(creator["pid"])
    if not processes.matches(creator, fresh):
        return cancelled
    if not handoff.admit_session(metadata, record, deadline):
        return cancelled
    return {"session_admitted": notice["request"], "guard": metadata["nonce"]}


# Fixed diagnostics only: exception payloads may contain paths, provider output or secrets.
_SESSION_HANDOFF_REASONS = {
    "Invalid session ownership handoff": "invalid_notice",
    "Invalid session ownership creator": "invalid_creator",
    "Session ownership receipt is unavailable": "receipt_unavailable",
    "Session ownership is no longer armed": "receipt_not_armed",
    "Enclosing keeper is unavailable": "enclosing_keeper_unavailable",
    "Enclosing keeper did not retain session ownership": "enclosing_ownership_unverified",
    "Keeper exited before acknowledging ownership": "acknowledgement_unavailable",
    "Keeper acknowledgement exceeded its limit": "acknowledgement_too_large",
    "Keeper did not arm within the launch limit": "acknowledgement_deadline",
}


def _session_handoff_failure(error, phase):
    known = (
        (SupervisionError, "SupervisionError"),
        (LaunchLimitExpired, "LaunchLimitExpired"),
        (processes.ProcessError, "ProcessError"),
        (OSError, "OSError"),
        (BrokenPipeError, "BrokenPipeError"),
        (PermissionError, "PermissionError"),
        (ConnectionResetError, "ConnectionResetError"),
        (ValueError, "ValueError"),
        (json.JSONDecodeError, "JSONDecodeError"),
    )
    name = next((name for kind, name in known if type(error) is kind), "Exception")
    message = None
    if (type(error) is SupervisionError or type(error) is LaunchLimitExpired) and len(error.args) == 1:
        message = error.args[0] if type(error.args[0]) is str else None
    reason = _SESSION_HANDOFF_REASONS.get(message, "unclassified")
    detail = ""
    if type(error) is SupervisionError and reason == "receipt_unavailable":
        diagnostic = error.__dict__.get("_receipt_diagnostic")
        if (
            type(diagnostic) is tuple
            and len(diagnostic) == 2
            and all(type(label) is str and len(label) <= 32 for label in diagnostic)
            and diagnostic[0] in _RECEIPT_READ_PHASES
            and diagnostic[1] in _RECEIPT_READ_REASONS
        ):
            detail = f"; receipt_phase={diagnostic[0]}; receipt_reason={diagnostic[1]}"
    return f"Session ownership handoff failed: {name}; phase={phase}; reason={reason}" + detail


def _stop_direct(child):
    if child is None:
        return
    if child.poll() is None:
        with suppress(ProcessLookupError):
            child.kill()
    child.wait(timeout=5)


_portable_noted = False


def _note_portable_host():
    global _portable_noted
    if not _portable_noted:
        _portable_noted = True
        print(
            "Provider session boundary: unavailable on macOS; providers run without kernel session ownership",
            file=sys.stderr,
            flush=True,
        )


def launch(command, *, receipt_path, timeout=None, checkpoint=None, cleanup_policy="freeze_tree", **options):
    """Launch a provider only with a qualified inherited kernel boundary, except on macOS (#1195)."""
    return _launch(
        command,
        receipt_path=receipt_path,
        timeout=timeout,
        checkpoint=checkpoint,
        cleanup_policy=cleanup_policy,
        provider_boundary=True,
        **options,
    )


def launch_command(command, *, receipt_path, timeout=None, checkpoint=None, **options):
    """Retain the existing runner-command lifeline and sampled SID ownership.

    Trusted verification workers launch further supervised commands. They must
    not install a provider listener that prohibits their nested command guards.
    This path confers no kernel family-extinction proof and is never used by
    provider adapters. Any inherited provider filter remains in force.
    """
    return _launch(
        command,
        receipt_path=receipt_path,
        timeout=timeout,
        checkpoint=checkpoint,
        cleanup_policy="freeze_tree",
        provider_boundary=False,
        **options,
    )


@contextmanager
def _launch(command, *, receipt_path, timeout, checkpoint, cleanup_policy, provider_boundary, **options):
    """Yield the actual provider Popen child after durable keeper admission.

    ``checkpoint(child.supervision)`` runs before exec. It must durably attach
    these identities to the already admitted attempt. It may raise: the provider
    is then never released. The receipt carries observed cleanup, not an exit
    code; the existing caller remains the child's sole waitpid authority.
    """
    if not isinstance(command, (list, tuple)) or not command or any(not isinstance(s, str) for s in command):
        raise SupervisionError("Supervision requires a nonempty argv")
    if options.get("shell") or options.get("executable") or options.get("preexec_fn"):
        raise SupervisionError("Supervision requires a direct, thread-safe argv launch")
    if options.get("start_new_session") is not True:
        raise SupervisionError("Supervised provider must have its own session")
    if timeout is not None and (
        isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0
    ):
        raise SupervisionError("Supervision timeout must be positive or disabled")
    if cleanup_policy not in ("freeze_tree", "interrupt_root"):
        raise SupervisionError("Unknown supervision cleanup policy")
    if provider_boundary:
        try:
            boundary.require_supported()
        except boundary.PortableHost:
            # #1195: macOS has no seccomp. Supervise as runner commands are: lifeline and
            # sampled ownership, no kernel family-exit proof, no session_boundary in the receipt.
            provider_boundary = False
            _note_portable_host()
        except boundary.BoundaryError as error:
            raise LaunchRefused(f"Provider session boundary unavailable; no provider launched: {error}") from error
    # The held envelope authenticates the exact values passed to this Popen,
    # not caller-owned containers that can change during its checkpoint.
    command = list(command)
    options = {**options, "pass_fds": tuple(options.get("pass_fds", ()))}
    if isinstance(options.get("env"), dict):
        options["env"] = dict(options["env"])
    path = Path(receipt_path).resolve()
    if path.exists():
        raise SupervisionError("Supervision receipt already exists; retain the previous attempt")
    path.parent.mkdir(parents=True, exist_ok=True)
    owner = _identity(os.getpid())
    control_r, control_w = os.pipe()
    ack_r, ack_w = os.pipe()
    release_r, release_w = os.pipe()
    socket_type = socket.SOCK_SEQPACKET if provider_boundary else socket.SOCK_STREAM
    bootstrap_parent, bootstrap_child = socket.socketpair(socket.AF_UNIX, socket_type)
    callback_parent, callback_child = socket.socketpair(socket.AF_UNIX, socket_type)
    callback_child_fd = callback_child.fileno()
    launch_nonce = uuid.uuid4().hex
    owned_fds = {control_r, control_w, ack_r, ack_w, release_r, release_w}
    owned_fds_lock = threading.Lock()
    keeper = child = envelope = None
    metadata = None
    stopped = threading.Event()
    errors = []
    monitor = None
    started = time.monotonic()
    timing_admission = elapsed_timing.admission(time.time(), started, timeout)
    if timing_admission is None:
        raise SupervisionError("Supervision timing origin is invalid")
    marks = []  # (step, monotonic): where a launch limit went if the keeper never arms (#989)
    completed = False
    keeper_stderr = None
    # #815: the keeper's stderr carries its death cause (e.g. the -E stripped
    # PYTHONPATH making psutil unimportable); capture it beside the receipt so
    # SupervisionError can name the cause. Unlinked on success, retained on
    # failure, and only its bounded tail ever reaches an error message.
    keeper_stderr_path = Path(path).parent / f"keeper-stderr-{uuid.uuid4().hex[:12]}.log"

    def close_fd(fd):
        with owned_fds_lock:
            if fd in owned_fds:
                owned_fds.remove(fd)
                os.close(fd)

    def send_owned(fd, value):
        # The watchdog may revoke the writer while the caller reaps its provider.
        # Its number can already belong to another file: checking ownership and
        # writing must be atomic with close, rather than merely ignoring EBADF.
        with owned_fds_lock:
            if fd in owned_fds:
                _send(fd, value)

    try:
        # The parent's handle closes right after the spawn (Popen dups it for
        # the child); reads go through _keeper_stderr_tail by path.
        keeper_stderr = open(keeper_stderr_path, "wb")  # noqa: SIM115 - handed to Popen
        keeper = subprocess.Popen(  # noqa: SIM115 - guarded by the finally below
            [
                sys.executable,
                "-E",
                str(Path(__file__).with_name("autocode_supervision_keeper.py")),
                str(control_r),
                str(ack_w),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=keeper_stderr,
            start_new_session=True,
            close_fds=True,
            pass_fds=(
                control_r,
                ack_w,
                *([bootstrap_parent.fileno(), callback_child.fileno()] if provider_boundary else []),
            ),
        )
        marks.append(("keeper spawned", time.monotonic()))
        keeper_stderr.close()
        close_fd(control_r)
        close_fd(ack_w)
        callback_child.close()
        bootstrap = [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("autocode_supervision_bootstrap.py")),
            str(release_r),
            json.dumps(list(command)),
            *([str(bootstrap_child.fileno()), launch_nonce] if provider_boundary else []),
        ]
        child_options = {
            **options,
            "close_fds": True,
            "pass_fds": tuple(
                dict.fromkeys(
                    (
                        *options.get("pass_fds", ()),
                        release_r,
                        *([bootstrap_child.fileno()] if provider_boundary else []),
                    )
                )
            ),
        }
        child = subprocess.Popen(bootstrap, **child_options)
        marks.append(("provider spawned", time.monotonic()))
        bootstrap_child.close()
        try:
            keeper_identity = _identity(keeper.pid)
            metadata = {
                "schema": 1,
                "nonce": launch_nonce,
                "owner": owner,
                "keeper": keeper_identity,
                "provider": _identity(child.pid),
                "receipt": str(path),
                "started_at": util.now(),
            }
            child.supervision = metadata
            child.supervision_errors = errors
            envelope = _LaunchEnvelope(child, keeper, command, bootstrap, options, metadata, release_r)
            child._autocode_launch_envelope = envelope
            close_fd(release_r)
            _send(
                control_w,
                {
                    "metadata": metadata,
                    "deadline": started + timeout if timeout else None,
                    "timing": timing_admission,
                    "cleanup_policy": cleanup_policy,
                    **(
                        {"session_socket": bootstrap_parent.fileno(), "session_callback": callback_child_fd}
                        if provider_boundary
                        else {}
                    ),
                },
            )
            marks.append(("ownership sent", time.monotonic()))
        except (processes.ProcessError, OSError) as error:
            # A fast keeper failure can precede identity sampling, metadata
            # delivery or the ack read. Name it only after the direct child
            # confirms its own exit; alive or uncertain inspection preserves
            # the original error and never releases the provider.
            with suppress(OSError):
                if keeper.poll() is not None:
                    raise _annotated(
                        SupervisionError("Keeper exited before acknowledging ownership"), keeper_stderr_path
                    ) from error
            raise
        bootstrap_parent.close()
        limit = min(15, timeout) if timeout else 15
        try:
            ack = _read_line(ack_r, started + limit)
        except LaunchLimitExpired as error:
            unarmed = _unarmed(
                error,
                limit=limit,
                started=started,
                marks=marks,
                keeper=keeper,
                metadata=metadata,
                control_fd=control_w,
            )
            raise _annotated(unarmed, keeper_stderr_path) from error
        except SupervisionError as error:
            raise _annotated(error, keeper_stderr_path) from error
        if ack != {"armed": metadata["nonce"]} or keeper.poll() is not None:
            raise _annotated(SupervisionError("Keeper refused provider ownership"), keeper_stderr_path)
        current = receipt(metadata)
        if (
            current is None
            or current.get("phase") != "armed"
            or (provider_boundary and current.get("session_boundary") != "linux-setsid-notify-v1")
        ):
            raise SupervisionError("Keeper ownership receipt is missing or invalid")
        close_fd(ack_r)

        failure_cleanup_lock = threading.Lock()
        failure_cleaned = False

        def keeper_loss_cleanup(value):
            nonlocal failure_cleaned
            # The synchronous exit check may race the watchdog. Both must wait
            # for one complete cleanup, rather than running competing trees.
            if not failure_cleanup_lock.acquire(timeout=5):
                errors.append("Keeper-loss cleanup did not settle within its bound")
                return
            try:
                if failure_cleaned:
                    return
                failure_cleaned = True
                tail = _keeper_stderr_tail(keeper_stderr_path)
                errors.append(
                    "Independent keeper stopped without verified cleanup"
                    + (f"; keeper stderr tail: {tail!r}" if tail else "")
                )
                try:
                    try:
                        from .autocode_supervision_keeper import Observer
                    except ImportError:
                        from autocode_supervision_keeper import Observer
                    # launch() admitted this exact dedicated session before
                    # exec. Preserve its group after the provider is reaped,
                    # even when the keeper has no descendant receipt.
                    tree = processes.ProcessTree(
                        child.pid,
                        lambda rows: None,
                        groups=(metadata["provider"],),
                        sessions=(metadata["provider"],),
                        session_admissions=(value or {}).get("session_admissions", ()),
                    )
                    tree.known[child.pid] = metadata["provider"]
                    if value:
                        tree.known.update({row["pid"]: row for row in value.get("processes", [])})
                    tree.stop(Observer(metadata["provider"]))
                except BaseException as error:
                    errors.append(f"Keeper-loss cleanup uncertain: {type(error).__name__}")
            finally:
                failure_cleanup_lock.release()

        # A dead keeper must not strand a provider even while the controller's
        # checkpoint callback is blocked. No runner state is touched here.
        def watch_keeper():
            callback_open = True
            while not stopped.is_set():
                phase = "wait_callback"
                try:
                    if callback_open and select.select([callback_parent], [], [], 0.05)[0]:
                        phase = "read_callback"
                        raw = callback_parent.recv(65537)
                        if not raw:
                            callback_open = False
                        else:
                            phase = "check_ownership"
                            answer = session_handoff_reply(metadata, raw, time.monotonic() + 15)
                            if answer is not None:
                                phase = "send_acknowledgement"
                                callback_parent.send(json.dumps(answer).encode())
                    elif not callback_open:
                        stopped.wait(0.05)  # EOF remains readable; do not spin on it.
                except BaseException as error:
                    # Clean publication can race the reply socket's closure.
                    # A clean deadline closes this socket too. Let final receipt
                    # and keeper-exit validation raise VerifiedStop; do not turn
                    # an already verified timeout into unknown cleanup. This
                    # does not widen normal command or ownership-grant success.
                    if stopped.is_set() or (
                        isinstance(error, OSError) and _terminal_clean(receipt(metadata), allow_deadline=True)
                    ):
                        return
                    try:
                        errors.append(_session_handoff_failure(error, phase))
                    finally:
                        with suppress(OSError):
                            close_fd(control_w)
                    return
                code = keeper.poll()
                if code is not None:
                    value = receipt(metadata)
                    if code != 0 or value is None or value.get("phase") != "stopped":
                        keeper_loss_cleanup(value)
                    return

        monitor = threading.Thread(target=watch_keeper, daemon=True)
        monitor.start()
        # Transfer nested ownership before exec while the bootstrap is blocked.
        handoff.admit(metadata, min(started + 15, started + timeout) if timeout else started + 15)
        if checkpoint is not None:
            checkpoint(dict(metadata))
        if (
            keeper.poll() is not None
            or child.poll() is not None
            or not processes.live_processes([metadata["keeper"]])
            or (receipt(metadata) or {}).get("phase") != "armed"
        ):
            raise _annotated(
                SupervisionError("Supervision stopped during the admission checkpoint"), keeper_stderr_path
            )
        os.write(release_w, b"G")
        close_fd(release_w)
        envelope._released = True
        yield child
        # The caller has now reaped and cleaned its provider tree. Discharge
        # only on that boundary; abnormal context exits use EOF cleanup instead.
        with suppress(BrokenPipeError):
            # A fast normal provider may already have been cleaned and its
            # keeper exited. The final receipt and exit status below decide.
            send_owned(control_w, {"finished": metadata["nonce"]})
        close_fd(control_w)
        try:
            keeper.wait(timeout=8)
        except subprocess.TimeoutExpired as error:
            raise SupervisionError("Keeper cleanup did not finish within its bound") from error
        final = receipt(metadata)
        child.supervision_result = final
        if (
            errors
            or keeper.returncode != 0
            or final is None
            or final.get("phase") != "stopped"
            or final.get("cleanup_error")
        ):
            # A keeper can die after the provider was reaped but before the
            # watchdog's next poll. Finishing the context must not disable that
            # watchdog without reaching the same scoped fallback cleanup.
            keeper_loss_cleanup(final)
            raise SupervisionError("; ".join(errors) or "Keeper cleanup receipt is incomplete")
        if final.get("cause") not in ("provider_stopped", "controller_finished"):
            if final.get("cause") in ("stage_deadline", "owner_lost", "lifeline_failure", "invalid_owner_message"):
                raise VerifiedStop(final["cause"])
            raise SupervisionError(f"Provider supervision interrupted: {final.get('cause')}")
        completed = True
    finally:
        if envelope is not None:
            envelope._active = False
        # Closing the only writer is authoritative owner loss. The bootstrap
        # also sees EOF if setup failed before provider release.
        with owned_fds_lock:
            remaining_fds = tuple(owned_fds)
        for fd in remaining_fds:
            close_fd(fd)
        if keeper is not None:
            try:
                keeper.wait(timeout=8)
            except subprocess.TimeoutExpired:
                _stop_direct(keeper)
            if monitor is not None:
                final = receipt(metadata)
                if (
                    keeper.returncode != 0
                    or final is None
                    or final.get("phase") != "stopped"
                    or final.get("cleanup_error")
                ):
                    # Abnormal caller exits skip the discharge/validation path
                    # above. Keep their original exception while reaching the
                    # same once-only cleanup before disabling its watchdog.
                    keeper_loss_cleanup(final)
        stopped.set()
        if monitor is not None:
            monitor.join(timeout=5)
        for channel in (bootstrap_parent, bootstrap_child, callback_parent, callback_child):
            channel.close()
        if keeper_stderr is not None and not keeper_stderr.closed:
            keeper_stderr.close()
        if completed:
            with suppress(OSError):
                keeper_stderr_path.unlink()
        if child is not None and child.poll() is None:
            # A keeper launch/setup failure still owns an unreaped direct child.
            _stop_direct(child)


# The keeper's window between its SIGTERM and SIGKILL after owner loss or a deadline.
DEFAULT_KILL_AFTER = 2


def _bounded(moment, owner_deadline):
    """The earlier of a wait's own deadline and the owner's, when the owner has one."""
    return moment if owner_deadline is None else min(moment, owner_deadline)


@contextmanager
def protect_owner(
    lifeline_fd,
    *,
    receipt_path,
    timeout,
    owner_identity,
    nonce,
    checkpoint=None,
    deadline=None,
    kill_after=DEFAULT_KILL_AFTER,
    checkout_lease_fd=None,
):
    """The public CLI guards itself against an inherited harness-only lifeline.

    The CLI has already consumed the bounded launch declaration. Its keeper
    receives the inherited read end; neither CLI nor keeper owns a writer.
    A normal exit discharges this guard after provider contexts have closed.
    ``deadline`` is the declaration's absolute monotonic deadline. The earlier
    of it and entry + ``timeout`` is fixed on entry; setup cannot extend it
    (#622). The original 15-second admission budget also starts on entry.
    ``timeout`` and ``deadline`` both None is the one
    unbounded guard, a Builder worker's lifeline (#454): the keeper then enforces no deadline of its
    own, and each nested launch keeps its own stage limit.

    ``kill_after`` is how long the keeper allows between its SIGTERM and SIGKILL after owner loss;
    a worker declares a window that covers a stage teardown (autocode_worker_lifeline).
    An optional parent checkout lease stays exclusively in the worker's keeper
    until cleanup settles; ordinary provider keepers never inherit that lease.
    """
    entered = time.monotonic()
    wall_entered = time.time()
    unbounded = timeout is None and deadline is None
    if not unbounded:
        for bound in (timeout,) + (() if deadline is None else (deadline,)):
            if isinstance(bound, bool) or not isinstance(bound, (int, float)) or not math.isfinite(bound):
                raise SupervisionError("Harness launch deadline is invalid")
    if isinstance(kill_after, bool) or not isinstance(kill_after, (int, float)) or not 0 < kill_after < math.inf:
        raise SupervisionError("Harness kill window is invalid")
    owner_deadline = (
        None if unbounded else (min(entered + timeout, deadline) if deadline is not None else entered + timeout)
    )
    if not unbounded and (timeout <= 0 or owner_deadline <= entered):
        raise SupervisionError("Harness launch deadline has expired")
    admission_deadline = _bounded(entered + 15, owner_deadline)
    if not isinstance(owner_identity, dict) or "birth_identity" not in owner_identity:
        raise SupervisionError("Harness owner has no stable birth identity")
    if not processes.live_processes([owner_identity]):
        raise SupervisionError("Harness owner is gone before CLI admission")
    if not isinstance(nonce, str) or len(nonce) != 32:
        raise SupervisionError("Harness launch nonce is invalid")
    timing_admission = elapsed_timing.admission(
        wall_entered, entered, None if owner_deadline is None else owner_deadline - entered
    )
    if timing_admission is None:
        raise SupervisionError("Harness timing origin is invalid")
    path = Path(receipt_path).resolve()
    if path.exists():
        raise SupervisionError("Harness supervision receipt already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    if time.monotonic() >= admission_deadline:
        raise SupervisionError("Harness launch deadline has expired")
    control_r, control_w = os.pipe()
    ack_r, ack_w = os.pipe()
    fds = {control_r, control_w, ack_r, ack_w}
    keeper = None
    metadata = None
    stopped = threading.Event()
    watcher = None
    errors = []

    def close(fd):
        if fd in fds:
            fds.remove(fd)
            os.close(fd)

    try:
        if time.monotonic() >= admission_deadline:
            raise SupervisionError("Harness launch deadline has expired")
        keeper = subprocess.Popen(
            [
                sys.executable,
                "-E",
                str(Path(__file__).with_name("autocode_supervision_keeper.py")),
                str(control_r),
                str(ack_w),
                str(lifeline_fd),
                *([str(checkout_lease_fd)] if checkout_lease_fd is not None else []),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            pass_fds=(control_r, ack_w, lifeline_fd, *([checkout_lease_fd] if checkout_lease_fd is not None else [])),
        )
        close(control_r)
        close(ack_w)
        metadata = {
            "schema": 1,
            "nonce": nonce,
            "owner": dict(owner_identity),
            "keeper": _identity(keeper.pid),
            "provider": _identity(os.getpid()),
            "receipt": str(path),
            "started_at": util.now(),
        }
        _send(
            control_w,
            {
                "metadata": metadata,
                "deadline": owner_deadline,
                "timing": timing_admission,
                "cleanup_policy": "interrupt_root",
                "kill_after_seconds": kill_after,
            },
        )
        ack = _read_line(ack_r, admission_deadline)
        if ack != {"armed": nonce} or keeper.poll() is not None or receipt(metadata) is None:
            raise SupervisionError("Harness keeper failed admission")
        if select.select([lifeline_fd], [], [], 0)[0]:
            raise SupervisionError("Harness lifeline closed before CLI admission")

        def watch():
            while not stopped.wait(0.05):
                code = keeper.poll()
                if code is not None:
                    current = receipt(metadata)
                    if code != 0 or current is None or current.get("phase") not in ("stopped", "discharged"):
                        errors.append("Harness keeper stopped without verified cleanup")
                        os.kill(os.getpid(), signal.SIGTERM)
                    return

        import signal

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        if checkpoint is not None:
            checkpoint(dict(metadata))
        channel_lock = threading.Lock()

        def admit_nested(nested, deadline):
            # Serialize framed requests and acknowledgements across launch threads.
            with channel_lock:
                if keeper.poll() is not None or stopped.is_set():
                    raise SupervisionError("Enclosing keeper is unavailable")
                transition = isinstance(nested, dict) and set(nested) == {"session_admission", "supervision"}
                if transition:
                    _send(
                        control_w,
                        {"admit_session": nested["session_admission"], "nested": nested["supervision"], "guard": nonce},
                    )
                else:
                    _send(control_w, {"admit": nested, "guard": nonce})
                answer = _read_line(ack_r, _bounded(deadline, owner_deadline))
                current = receipt(metadata)
                inventory = (current or {}).get("processes", [])
                if transition:
                    record = nested["session_admission"]
                    expected = {
                        "session_admitted": nested["supervision"]["nonce"],
                        "creator": record["requested_session"],
                    }
                    cancelled = {
                        "session_cancelled": nested["supervision"]["nonce"],
                        "creator": record["requested_session"],
                    }
                    if current is None or current.get("phase") != "armed" or current.get("cleanup_error") is not None:
                        raise SupervisionError("Enclosing keeper did not retain session ownership")
                    if answer == cancelled:
                        return False
                    if answer != expected or record not in current.get("session_admissions", []):
                        raise SupervisionError("Enclosing keeper did not retain session ownership")
                    return True
                if (
                    answer != {"admitted": nested["nonce"]}
                    or current is None
                    or current.get("phase") != "armed"
                    or any(
                        not any(processes.matches(nested[key], row) for row in inventory)
                        for key in ("provider", "keeper")
                    )
                ):
                    raise SupervisionError("Enclosing keeper did not retain nested ownership")
                return True

        try:
            with handoff.enclosing_guard(admit_nested):
                yield metadata
        finally:
            current = receipt(metadata)
            # EOF cleanup must not wait for itself: the CLI has to return from
            # this context before its keeper can observe/reap its disappearance.
            readable = select.select([lifeline_fd], [], [], 0)[0]
            interrupted = bool(readable) or (
                current is not None and current.get("cause") not in (None, "provider_stopped", "controller_finished")
            )
            if not interrupted:
                try:
                    _send(control_w, {"finished": nonce})
                except BrokenPipeError as error:
                    raise SupervisionError("Harness keeper exited before discharge") from error
                finished_sent = time.monotonic()
                close(control_w)
                try:
                    keeper.wait(timeout=8)
                except subprocess.TimeoutExpired as error:
                    # The admitted keeper retains cleanup authority and still publishes its terminal
                    # receipt, with its stop_timing phases, after this CLI exits; an unverified wait
                    # cannot become a discharge, so no fresh receipt read is taken here. Name what this
                    # side last saw instead of escaping as an untyped traceback (#1143).
                    phase = (current or {}).get("phase") or "unreadable"
                    raise SupervisionError(
                        "Harness keeper discharge did not finish within its bound: receipt "
                        f"{phase} {time.monotonic() - finished_sent:.3f} s after the finished message"
                    ) from error
                current = receipt(metadata)
                if errors or keeper.returncode != 0 or current is None or current.get("phase") != "discharged":
                    raise SupervisionError("; ".join(errors) or "Harness keeper cleanup was uncertain")
    finally:
        for fd in tuple(fds):
            close(fd)
        stopped.set()
        if watcher is not None:
            watcher.join(timeout=1)
        # An armed keeper owns the surviving read end and remains responsible
        # after this CLI exits. Waiting here would deadlock interrupted teardown.
        if keeper is not None and metadata is None:
            try:
                keeper.wait(timeout=8)
            except subprocess.TimeoutExpired:
                _stop_direct(keeper)

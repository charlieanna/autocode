"""Block a trusted provider root until its original supervisor seals admission.

This transport does not authenticate a visual admission. Its CODE-owned caller
must seal the actual child, exact target argv/environment and native containment
through the retained admission transaction before invoking release_after_seal.
The original wait_for_stage remains the sole owner of deadlines and cleanup.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_PAYLOAD = 1024 * 1024
MAX_CONTROL = 512


class BarrierError(RuntimeError):
    pass


def _require(condition, reason):
    if not condition:
        raise BarrierError(reason)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _supervision():
    # Parent-only import: the isolated child bootstrap imports no AutoCode.
    try:
        from . import autocode_supervision
    except ImportError:
        import autocode_supervision
    return autocode_supervision


def _target(command, environment):
    _require(
        isinstance(command, (list, tuple))
        and command
        and command[0]
        and all(isinstance(value, str) and "\x00" not in value for value in command),
        "Writer target needs an exact nonempty argument list",
    )
    _require(
        isinstance(environment, dict)
        and all(
            isinstance(key, str)
            and key
            and "=" not in key
            and "\x00" not in key
            and isinstance(value, str)
            and "\x00" not in value
            for key, value in environment.items()
        ),
        "Writer target needs an exact environment map",
    )
    return list(command), dict(environment)


def _private_directory(value):
    path = Path(value).absolute()
    _require(path == path.resolve() and path.is_dir(), "Writer payload requires an existing unaliased owned directory")
    return path


def _private_fd(fd):
    """Take a private endpoint above stdio; preserve its inode and close low alias.

    Caller retains ownership of fd on failure. A successfully returned endpoint
    is noninheritable until the original Popen explicitly passes it. Never let a
    closed parent standard slot become a private payload/release descriptor.
    """
    import fcntl

    _require(type(fd) is int and fd >= 0, "Writer private descriptor is invalid")
    if fd >= 3:
        os.set_inheritable(fd, False)
        return fd
    replacement = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 3)
    try:
        _require(
            replacement >= 3 and not os.get_inheritable(replacement),
            "Writer private descriptor normalization is unsupported",
        )
        os.close(fd)
    except BaseException:
        os.close(replacement)
        raise
    return replacement


class WriterBarrier:
    """Single-use process-local transport, never a saved approval or admission.

    The readonly, anonymous payload descriptor holds the exact original target
    environment, including credentials. No raw environment is returned, logged
    or written into a receipt. It is closed before provider exec. The release
    pipe is inherited only at its read end; abort/parent crash yields EOF and no
    target execution. The checkout writer descriptor is preserved separately.
    """

    def __init__(self, command, environment, *, payload_directory):
        command, environment = _target(command, environment)
        directory = _private_directory(payload_directory)
        self._nonce = secrets.token_hex(32)
        self._payload = _json({"version": 1, "nonce": self._nonce, "command": command, "environment": environment})
        _require(len(self._payload) <= MAX_PAYLOAD, "Writer launch payload is unsupported")
        self.payload_sha256 = _sha(self._payload)
        self.command_sha256 = _sha(_json(command))
        self.environment_sha256 = _sha(_json(environment))
        self._module_sha256 = _sha(Path(__file__).read_bytes())
        self._creator_pid = os.getpid()
        self._released = False
        self._spawned = False
        self._child = None
        self._envelope = None
        self._launch_identity = None
        self._payload_fd = None
        self._read_fd = None
        self._write_fd = None
        # Reopen readonly and unlink before launch. The child cannot replace
        # this anonymous inode or obtain a writable descriptor to the payload.
        handle, name = tempfile.mkstemp(prefix="writer-payload-", dir=directory)
        try:
            with os.fdopen(handle, "wb", closefd=True) as output:
                output.write(self._payload)
                output.flush()
                os.fsync(output.fileno())
            self._payload_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW)
            self._payload_fd = _private_fd(self._payload_fd)
            saved = os.fstat(self._payload_fd)
            _require(
                stat.S_ISREG(saved.st_mode) and saved.st_size == len(self._payload) and saved.st_nlink == 1,
                "Writer payload file identity is uncertain",
            )
            os.unlink(name)
            self._payload_identity = {"device": saved.st_dev, "inode": saved.st_ino, "bytes": saved.st_size}
            self._read_fd, self._write_fd = os.pipe()
            self._read_fd = _private_fd(self._read_fd)
            self._write_fd = _private_fd(self._write_fd)
            os.set_blocking(self._write_fd, False)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name)
            self.close()
            raise
        finally:
            # The authoritative bytes remain solely in the anonymous readonly
            # descriptor. Avoid retaining a second credential-bearing buffer.
            self._payload = None
        self._bootstrap = [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(Path(__file__).resolve()),
            "--writer-barrier-child",
            str(self._read_fd),
            str(self._payload_fd),
            self._nonce,
            self.payload_sha256,
            str(self._payload_identity["bytes"]),
        ]

    def launch(self, options):
        """Return exact bootstrap argv/options for the caller's original Popen."""
        _require(
            os.getpid() == self._creator_pid
            and not self._spawned
            and self._read_fd is not None
            and self._payload_fd is not None,
            "Writer barrier launch is absent or already consumed",
        )
        _require(
            isinstance(options, dict) and options.get("shell", False) is False,
            "Writer barrier refuses a shell-interpreted root",
        )
        _require(
            options.get("preexec_fn") is None and options.get("executable") is None,
            "Writer barrier refuses alternate pre-exec or executable authority",
        )
        _require(
            isinstance(options.get("env"), dict) and _sha(_json(options["env"])) == self.environment_sha256,
            "Writer bootstrap environment differs from the prepared target",
        )
        _require(
            self._write_fd not in options.get("pass_fds", ()),
            "Writer release authority cannot be inherited by the child",
        )
        _require(
            all(key in options for key in ("stdin", "stdout", "stderr")),
            "Writer barrier requires the exact final standard descriptors",
        )
        controls = {
            tuple(getattr(os.fstat(fd), name) for name in ("st_dev", "st_ino"))
            for fd in (self._read_fd, self._write_fd, self._payload_fd)
        }

        def descriptor(value, inherited):
            if value is None:
                return inherited
            return value if isinstance(value, int) else value.fileno()

        standard = [descriptor(options[key], index) for index, key in enumerate(("stdin", "stdout", "stderr"))]
        # STDOUT (-2) aliases stdout; other negative Popen constants create
        # distinct descriptors or /dev/null. Compare inode aliases, not numbers.
        for fd in [*standard, *options.get("pass_fds", ())]:
            if fd < 0:
                continue
            saved = os.fstat(fd)
            _require(
                (saved.st_dev, saved.st_ino) not in controls,
                "Writer stdio/pass_fds cannot alias its release or payload descriptors",
            )
        _require(
            _sha(Path(__file__).read_bytes()) == self._module_sha256, "Writer bootstrap source changed before launch"
        )
        _require(options.get("start_new_session") is True, "Writer barrier requires the original owned process session")
        actual = {
            **options,
            "close_fds": True,
            "pass_fds": tuple(dict.fromkeys((*options.get("pass_fds", ()), self._read_fd, self._payload_fd))),
        }
        self._launch_identity = _supervision().launch_options_identity(actual)
        return list(self._bootstrap), actual

    def spawned(self, child):
        """Close parent copies after the actual original Popen succeeds."""
        _require(
            os.getpid() == self._creator_pid
            and not self._spawned
            and self._launch_identity is not None
            and isinstance(child, subprocess.Popen)
            and type(child.pid) is int
            and child.pid > 0,
            "Writer root differs from the prepared bootstrap",
        )
        if hasattr(child, "supervision") or hasattr(child, "_autocode_launch_envelope"):
            envelope = _supervision().launch_envelope(child)
            envelope.authenticate(
                child,
                command=self._bootstrap,
                environment_sha256=self.environment_sha256,
                options_identity=self._launch_identity,
            )
            self._envelope = envelope
        else:
            _require(list(child.args) == self._bootstrap, "Writer root differs from the prepared bootstrap")
        self._child = child
        self._spawned = True
        for name in ("_read_fd", "_payload_fd"):
            fd = getattr(self, name)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)

    def facts(self):
        """Nonsecret exact execution facts for the native admission seal."""
        return {
            "version": 1,
            "bootstrap": list(self._bootstrap),
            "module_sha256": self._module_sha256,
            "payload_sha256": self.payload_sha256,
            "payload_identity": dict(self._payload_identity),
            "target_command_sha256": self.command_sha256,
            "target_environment_sha256": self.environment_sha256,
        }

    def execution_facts(self, child, tree):
        """Authenticate the real held child, including any original outer exec."""
        _require(
            os.getpid() == self._creator_pid
            and self._spawned
            and not self._released
            and child is self._child
            and isinstance(child, subprocess.Popen)
            and tree.pid == child.pid
            and child.pid in tree.known,
            "Writer execution lacks its original held root",
        )
        facts = self.facts()
        if self._envelope is not None:
            facts["supervision_envelope"] = self._envelope.authenticate(
                child,
                command=self._bootstrap,
                environment_sha256=self.environment_sha256,
                options_identity=self._launch_identity,
                tree=tree,
            )
        else:
            _require(
                not hasattr(child, "supervision") and list(child.args) == self._bootstrap,
                "Writer outer supervision cannot be reconstructed or omitted",
            )
        return facts

    def release_after_seal(self, child, tree, seal, release_token):
        """Invoked from the original supervisor before any ordinary child poll.

        seal(child, tree, facts) is supplied by the CODE-owned native admission
        transaction. It must authenticate and fsync actual immutable admission
        and launch facts, under the existing run/intervention/checkout locks.
        This transport cannot replace that work with a saved boolean/hash.
        If it raises, the original supervisor cleans this exact blocked root.
        """
        _require(
            os.getpid() == self._creator_pid
            and self._spawned
            and not self._released
            and child is self._child
            and self._write_fd is not None,
            "Writer release handle is missing, foreign or already consumed",
        )
        facts = self.execution_facts(child, tree)
        root = tree.known[child.pid]
        _require(
            root.get("pid") == child.pid
            and type(root.get("birth_identity")) in (int, float)
            and root["birth_identity"] > 0
            and root.get("group") == child.pid,
            "Writer root birth or owned session is uncertain",
        )
        _require(
            _sha(Path(__file__).read_bytes()) == self._module_sha256, "Writer bootstrap source changed before release"
        )
        _require(callable(seal), "Missing CODE-owned durable admission transaction")
        _require(callable(release_token), "Missing original supervisor release guard")
        seal(child, tree, facts)
        # Durable sealing may block. Recheck the exact original outer identity
        # before the existing supervisor arbitrates its final release write.
        self.execution_facts(child, tree)
        # A single small write is atomic and nonblocking. If the child died or
        # the pipe is unavailable, raise into original supervisor cleanup. No
        # retry, new timer, signal, secondary ProcessTree or controller is added.
        control = _json({"version": 1, "nonce": self._nonce, "payload_sha256": self.payload_sha256})
        _require(
            len(control) <= MAX_CONTROL and len(control) <= os.fpathconf(self._write_fd, "PC_PIPE_BUF"),
            "Writer release token is unsupported",
        )
        try:
            written = release_token(self._write_fd, control)
            _require(written == len(control), "Writer release token was incomplete")
        finally:
            os.close(self._write_fd)
            self._write_fd = None
        self._released = True

    def close(self):
        """Drop parent descriptors; a blocked child sees EOF, never permission."""
        for name in ("_read_fd", "_payload_fd", "_write_fd"):
            fd = getattr(self, name, None)
            if fd is not None:
                try:
                    os.close(fd)
                finally:
                    setattr(self, name, None)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _read_bounded(fd, limit):
    data = bytearray()
    while True:
        block = os.read(fd, min(65536, limit + 1 - len(data)))
        if not block:
            return bytes(data)
        data.extend(block)
        _require(len(data) <= limit, "Writer bootstrap input exceeds its bound")


def child_main(arguments):
    """Standalone trusted bootstrap; imports no AutoCode or workspace modules."""
    import fcntl

    try:
        _require(len(arguments) == 5, "Invalid writer bootstrap arguments")
        read_fd, payload_fd = int(arguments[0]), int(arguments[1])
        nonce, expected_sha, size = arguments[2], arguments[3], int(arguments[4])
        _require(
            read_fd >= 3
            and payload_fd >= 3
            and read_fd != payload_fd
            and len(nonce) == 64
            and len(expected_sha) == 64
            and 0 < size <= MAX_PAYLOAD,
            "Invalid writer bootstrap identity",
        )
        _require(stat.S_ISFIFO(os.fstat(read_fd).st_mode), "Writer release is not a private pipe")
        payload_stat = os.fstat(payload_fd)
        _require(
            stat.S_ISREG(payload_stat.st_mode)
            and payload_stat.st_nlink == 0
            and payload_stat.st_size == size
            and fcntl.fcntl(payload_fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY,
            "Writer payload is not a readonly anonymous file",
        )
        control_raw = _read_bounded(read_fd, MAX_CONTROL)
        os.close(read_fd)
        _require(bool(control_raw), "Writer admission was not released")
        control = json.loads(control_raw)
        _require(
            control == {"version": 1, "nonce": nonce, "payload_sha256": expected_sha}, "Writer release token differs"
        )
        payload_raw = _read_bounded(payload_fd, size)
        os.close(payload_fd)
        _require(len(payload_raw) == size and _sha(payload_raw) == expected_sha, "Writer target payload differs")
        payload = json.loads(payload_raw)
        _require(
            isinstance(payload, dict)
            and set(payload) == {"version", "nonce", "command", "environment"}
            and payload["version"] == 1
            and payload["nonce"] == nonce,
            "Writer target payload is unbound",
        )
        command, environment = _target(payload["command"], payload["environment"])
        os.execvpe(command[0], command, environment)
    except (BarrierError, OSError, ValueError, TypeError, KeyError):
        # Never print the target environment, payload or command on failure.
        return 126
    return 126


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--writer-barrier-child":
        raise SystemExit(child_main(sys.argv[2:]))
    raise SystemExit(126)

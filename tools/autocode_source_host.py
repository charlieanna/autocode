"""A long-lived local helper that answers source snapshots and read-only CLI views (#1707, #3618).

Every AutoCode process used to cold-import the full CLI (~80 modules) and fingerprint the workspace
from scratch. With ``AUTOCODE_SOURCE_HOST=1`` (set by the VS Code client and terminal chat unless the
environment already sets it), eligible read-only CLI queries and workspace snapshots talk to a
per-user helper over a private ``0700`` Unix socket instead.

A host serves one code version (a digest of ``tools/`` and ``pyproject.toml``) and one Git environment,
keeps ``ContentHashes`` in memory, never writes snapshot state to disk, and exits after ``IDLE_SECONDS``
(or on ``autocode serve --stop`` / ``SIGINT`` / ``SIGTERM``). When no host is running yet, the caller
starts one in the background and runs in-process transparently.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

try:
    from . import autocode_capture_family as capture_family
except ImportError:
    import autocode_capture_family as capture_family

SETTING = "AUTOCODE_SOURCE_HOST"
IDLE_SECONDS = 30 * 60
CONNECT_SECONDS = 0.5
ANSWER_SECONDS = 120.0
# Environment that can change what `git ls-files`, `git rev-parse` and backend/config lookups report.
GIT_ENV = ("HOME", "PATH", "XDG_CONFIG_HOME")
# Same set as autocode_util.GIT_REPOSITORY_VARIABLES; duplicated here so autocode_entry's fast path stays stdlib-only.
GIT_REPOSITORY_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_GRAFT_FILE",
    "GIT_SHALLOW_FILE",
    "GIT_PREFIX",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
)


def drop_git_repository_variables(environ=None) -> list[str]:
    """Remove inherited repository-location variables in place; return the names removed."""
    target = os.environ if environ is None else environ
    return [name for name in GIT_REPOSITORY_VARIABLES if target.pop(name, None) is not None]


_CODE_SUFFIXES = (".py", ".json", ".toml", ".md")
_CAP_SETPCAP = 1 << 8
_CAP_MASK = (1 << 19) | (1 << 21)  # Linux CAP_SYS_PTRACE | CAP_SYS_ADMIN
_STATUS_BOOL_FLAGS = frozenset({"--status", "--explain", "--json", "--summary", "--inspect-evidence"})
_STATUS_VALUE_FLAGS = ("--workspace", "--run-dir")
_STAGES_VALUE_FLAGS = ("--backend", "--workspace", "--run-dir")
_state = {"failed": False, "started": False}
_local = threading.local()
_UTIL = None


def _in_request() -> bool:
    return bool(getattr(_local, "in_request", False))


def _util():
    global _UTIL
    if _UTIL is None:
        try:
            from . import autocode_util as util
        except ImportError:
            import autocode_util as util
        _UTIL = util
    return _UTIL


def _capture_owned(env=None) -> bool:
    # A retained host is outside this invocation's kernel-owned family. Keep
    # execution local; the capture owner still authenticates any advertised key.
    return capture_family.ENVIRONMENT_KEY in (os.environ if env is None else env)


def enabled(env=None) -> bool:
    environment = os.environ if env is None else env
    return os.name != "nt" and environment.get(SETTING) == "1" and not _capture_owned(environment)


def install() -> None:
    """Route this process's snapshots through the host, when the setting asks for it."""
    if not _in_request() and enabled():
        _util().use_snapshot_service(snapshot)


def code_digest(tools_dir: Path | None = None) -> bytes:
    """Fast metadata digest across ``tools/`` and ``pyproject.toml`` so code edits invalidate the host."""
    root = str(Path(tools_dir).resolve() if tools_dir is not None else Path(__file__).resolve().parent)
    prefix = len(root) + 1
    entries: list[tuple[str, int, int, int, int]] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            scanner = os.scandir(current)
        except OSError:
            continue
        with scanner:
            for entry in scanner:
                name = entry.name
                if name.startswith(".") or name in ("__pycache__", "tests"):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif name.endswith(_CODE_SUFFIXES):
                        st = entry.stat(follow_symlinks=False)
                        entries.append((entry.path[prefix:], st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino))
                except OSError:
                    continue
    pyproject = Path(root).parent / "pyproject.toml"
    with contextlib.suppress(OSError):
        st = pyproject.stat()
        entries.append(("../pyproject.toml", st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino))
    entries.sort()
    digest = hashlib.sha256(f"{sys.executable}\0{sys.version}\n".encode())
    for rel, mtime_ns, ctime_ns, size, ino in entries:
        digest.update(f"{rel}\0{mtime_ns}\0{ctime_ns}\0{size}\0{ino}\n".encode())
    return digest.digest()


def socket_path(env=None, *, tools_dir: Path | None = None) -> Path:
    env = os.environ if env is None else env
    code = code_digest(tools_dir=tools_dir)
    git_env = sorted((key, value) for key, value in env.items() if key.startswith("GIT_") or key in GIT_ENV)
    name = hashlib.sha256(code + json.dumps(git_env).encode()).hexdigest()[:20]
    base_tmp = next((env[key] for key in ("TMPDIR", "TEMP", "TMP") if env.get(key)), None) or tempfile.gettempdir()
    uid = getattr(os, "getuid", lambda: 0)()
    return Path(base_tmp) / f"autocode-{uid}" / f"source-{name}.sock"


def _consume_valued_flag(
    arg: str, args: list[str] | tuple[str, ...], index: int, allowed: tuple[str, ...]
) -> int | None:
    if arg in allowed:
        return index + 2 if index + 1 < len(args) and args[index + 1] != "" else -1
    for flag in allowed:
        if arg.startswith(flag + "="):
            return index + 1 if len(arg) > len(flag) + 1 else -1
    return None


def eligible_cli(argv: list[str] | tuple[str, ...]) -> bool:
    """Return True only for strictly read-only, non-interactive CLI queries safe for warm hosting."""
    if os.name == "nt" or not isinstance(argv, (list, tuple)) or not argv:
        return False
    if any(not isinstance(item, str) for item in argv):
        return False
    if any(item in ("-h", "--help", "--owner-lifeline-fd", "--worker-receipt") for item in argv):
        return False
    if list(argv) == ["--version"]:
        return True

    first = argv[0]
    if first == "modes":
        return len(argv) == 3 and set(argv[1:]) == {"list", "--json"}
    if first == "connections":
        return (
            len(argv) >= 3
            and argv[1] == "list"
            and "--json" in argv[2:]
            and set(argv[2:])
            <= {
                "--json",
                "--routes",
                "--new-runs",
            }
        )
    if first == "registry":
        if argv[1:2] == ["list"]:
            return set(argv[2:]) <= {"--json", "--include-archived"}
        if argv[1:2] == ["location"]:
            return set(argv[2:]) <= {"--json"}
        return False
    if first == "worktrees":
        has_json = False
        saw_list = False
        i = 1
        while i < len(argv):
            token = argv[i]
            if token == "--json":
                has_json = True
                i += 1
                continue
            if token == "list" and not saw_list:
                saw_list = True
                i += 1
                continue
            step = _consume_valued_flag(token, argv, i, ("--workspace",))
            if step is None or step < 0:
                return False
            i = step
        return has_json
    if first == "runs":
        has_json = False
        i = 1
        while i < len(argv):
            token = argv[i]
            if token == "--json":
                has_json = True
                i += 1
                continue
            if token in ("--all", "--needs"):
                i += 1
                continue
            step = _consume_valued_flag(token, argv, i, ("--workspace",))
            if step is None or step < 0:
                return False
            i = step
        return has_json
    if first == "stages":
        head = list(argv[1 : argv.index("--")]) if "--" in argv else list(argv[1:])
        has_json = False
        saw_list = False
        i = 0
        while i < len(head):
            token = head[i]
            if token == "--json":
                has_json = True
                i += 1
                continue
            if token == "list" and not saw_list:
                saw_list = True
                i += 1
                continue
            if token == "--chat-stage":
                if i + 3 >= len(head):
                    return False
                i += 4
                continue
            step = _consume_valued_flag(token, head, i, _STAGES_VALUE_FLAGS)
            if step is None or step < 0:
                return False
            i = step
        return has_json

    has_read_action = False
    i = 0
    if first in ("status", "explain"):
        has_read_action = True
        i = 1
    while i < len(argv):
        token = argv[i]
        if token in _STATUS_BOOL_FLAGS:
            if token in ("--status", "--explain"):
                has_read_action = True
            i += 1
            continue
        step = _consume_valued_flag(token, argv, i, _STATUS_VALUE_FLAGS)
        if step is None or step < 0:
            return False
        i = step
    return has_read_action


def _needs_capability_reexec() -> bool:
    if sys.platform != "linux":
        return False
    if os.environ.get("_AUTOCODE_CAPABILITY_REEXEC"):
        return True
    if os.environ.get("AUTOCODE_NO_CAPABILITY_DROP") == "1":
        return False
    try:
        with open("/proc/self/status", encoding="ascii") as status_file:
            lines = status_file.read(65537).splitlines()
        sets = {}
        for field in ("CapBnd", "CapPrm", "CapEff"):
            values = [line.partition(":")[2].strip() for line in lines if line.startswith(field + ":")]
            if len(values) != 1:
                return False
            sets[field] = int(values[0], 16)
    except (OSError, ValueError):
        return False
    if (sets["CapEff"] | sets["CapPrm"]) & _CAP_MASK:
        return True
    return bool((sets["CapBnd"] & _CAP_MASK) and (sets["CapEff"] & _CAP_SETPCAP))


def cli_fast_path(argv: list[str] | tuple[str, ...] | None = None) -> int | None:
    """Serve an eligible read-only CLI query over the warm host socket, or return None to run cold."""
    drop_git_repository_variables()
    args = sys.argv[1:] if argv is None else argv
    if _in_request() or not enabled() or _state["failed"] or not eligible_cli(args) or _needs_capability_reexec():
        return None
    try:
        cwd = os.getcwd()
    except OSError:
        return None
    path = socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(CONNECT_SECONDS)
            connection.connect(str(path))
            connection.settimeout(ANSWER_SECONDS)
            request = {"kind": "cli", "argv": list(args), "cwd": cwd, "env": dict(os.environ)}
            connection.sendall(json.dumps(request).encode() + b"\n")
            answer = json.loads(_read_line(connection))
    except (FileNotFoundError, ConnectionRefusedError):
        _start(path)
        return None
    except (OSError, ValueError):
        _state["failed"] = True
        return None
    if not isinstance(answer, dict) or answer.get("fallback") or not isinstance(answer.get("exit_code"), int):
        return None
    with contextlib.suppress(BrokenPipeError):
        if answer.get("stdout"):
            sys.stdout.write(answer["stdout"])
            sys.stdout.flush()
        if answer.get("stderr"):
            sys.stderr.write(answer["stderr"])
            sys.stderr.flush()
    return int(answer["exit_code"])


def exit_if_served(argv: list[str] | None = None) -> None:
    """Fast exit hook for ``tools/autocode.py`` before heavy top-level imports."""
    code = cli_fast_path(sys.argv[1:] if argv is None else argv)
    if code is not None:
        raise SystemExit(code)


def snapshot(workspace, nested):
    """The host's snapshot of ``workspace``, or None when this process should take its own."""
    if _in_request() or _state["failed"] or _capture_owned():
        return None
    path = socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(CONNECT_SECONDS)
            connection.connect(str(path))
            connection.settimeout(ANSWER_SECONDS)
            request = {"kind": "snapshot", "root": os.path.abspath(workspace), "nested": bool(nested)}
            connection.sendall(json.dumps(request).encode() + b"\n")
            answer = json.loads(_read_line(connection))
    except (FileNotFoundError, ConnectionRefusedError):
        _start(path)
        return None
    except (OSError, ValueError):
        _state["failed"] = True  # slow or broken: this process stops asking
        return None
    if "paused" in answer:
        raise _util().Paused(answer["paused"]["status"], answer["paused"]["reason"])
    return answer.get("snapshot")


def host_status(path: Path | None = None) -> dict:
    """Inspect whether a warm host is currently answering on ``path`` without starting one."""
    if os.name == "nt":
        return {"running": False, "pid": None, "socket": None}
    target = Path(path) if path is not None else socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(CONNECT_SECONDS)
            connection.connect(str(target))
            connection.settimeout(5.0)
            connection.sendall(b'{"kind": "status"}\n')
            answer = json.loads(_read_line(connection))
    except (OSError, ValueError):
        return {"running": False, "pid": None, "socket": str(target)}
    if isinstance(answer, dict) and answer.get("running"):
        return answer
    return {"running": False, "pid": None, "socket": str(target)}


def stop_host(path: Path | None = None, *, timeout: float = 5.0) -> dict:
    """Stop the warm host answering on ``path`` (if any) and wait for it to exit."""
    if os.name == "nt":
        return {"stopped": False, "running": False, "pid": None, "socket": None}
    target = Path(path) if path is not None else socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(CONNECT_SECONDS)
            connection.connect(str(target))
            connection.settimeout(min(5.0, max(0.5, timeout)))
            connection.sendall(b'{"kind": "stop"}\n')
            answer = json.loads(_read_line(connection))
    except (FileNotFoundError, ConnectionRefusedError, OSError, ValueError):
        return {"stopped": False, "running": False, "pid": None, "socket": str(target)}
    pid = answer.get("pid") if isinstance(answer, dict) else None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        socket_gone = not target.exists()
        pid_alive = False
        if isinstance(pid, int) and pid > 0:
            try:
                os.kill(pid, 0)
                pid_alive = True
            except ProcessLookupError:
                pid_alive = False
            except PermissionError:
                pid_alive = True
        if socket_gone and not pid_alive:
            break
        time.sleep(0.02)
    return {"stopped": True, "running": False, "pid": pid, "socket": str(target)}


def _read_line(connection) -> bytes:
    chunks: list[bytes] = []
    while not chunks or not chunks[-1].endswith(b"\n"):
        chunk = connection.recv(1 << 20)
        if not chunk:
            raise OSError("the source host closed the connection")
        chunks.append(chunk)
    return b"".join(chunks)


def _ensure_private_parent(path: Path) -> bool:
    path.parent.mkdir(mode=0o700, exist_ok=True)
    parent_stat = path.parent.stat()
    uid = getattr(os, "getuid", lambda: 0)()
    return parent_stat.st_uid == uid and (parent_stat.st_mode & 0o077) == 0


def _start(path: Path) -> None:
    if _state["started"]:
        return
    _state["started"] = True
    with contextlib.suppress(OSError):
        if not _ensure_private_parent(path):
            return  # never serve through a folder another user could reach
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), str(path)],
            cwd=str(path.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )


def _execute_cli(cli_runner, request: dict, safe_cwd: str) -> dict:
    argv = request.get("argv")
    cwd = request.get("cwd")
    env = request.get("env")
    if not isinstance(argv, list) or not eligible_cli(argv) or not isinstance(cwd, str) or not isinstance(env, dict):
        return {"fallback": True}
    saved_env = dict(os.environ)
    saved_argv = list(sys.argv)
    saved_stdout, saved_stderr, saved_stdin = sys.stdout, sys.stderr, sys.stdin
    try:
        saved_cwd = os.getcwd()
    except OSError:
        saved_cwd = safe_cwd
    try:
        os.chdir(cwd)
    except OSError:
        return {"fallback": True}
    out, err = io.StringIO(), io.StringIO()
    try:
        os.environ.clear()
        os.environ.update({str(key): str(value) for key, value in env.items()})
        drop_git_repository_variables()
        sys.argv = ["autocode", *[str(item) for item in argv]]
        sys.stdin = io.StringIO()
        sys.stdout = out
        sys.stderr = err
        _util().use_snapshot_service(None)
        try:
            code = cli_runner()
        except SystemExit as exc:
            if exc.code is None:
                code = 0
            elif isinstance(exc.code, bool):
                code = int(exc.code)
            elif isinstance(exc.code, int):
                code = exc.code
            else:
                print(exc.code, file=err)
                code = 1
        return {
            "exit_code": 0 if code is None else int(code),
            "stdout": out.getvalue(),
            "stderr": err.getvalue(),
            "pid": os.getpid(),
        }
    except Exception:  # noqa: BLE001 - unexpected host failure falls back to cold in-process execution
        return {"fallback": True}
    finally:
        sys.stdout, sys.stderr, sys.stdin = saved_stdout, saved_stderr, saved_stdin
        sys.argv = saved_argv
        os.environ.clear()
        os.environ.update(saved_env)
        with contextlib.suppress(OSError):
            os.chdir(saved_cwd)
        _util().use_snapshot_service(None)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server: Any = self.server
        server.touched = time.monotonic()
        stop_after_reply = False
        try:
            request = json.loads(self.rfile.readline())
            kind = request.get("kind") if isinstance(request, dict) else None
            if kind == "status":
                answer = {
                    "running": True,
                    "pid": os.getpid(),
                    "socket": str(server.socket_file),
                    "idle_seconds": server.idle_seconds,
                    "idle_for_seconds": round(max(0.0, time.monotonic() - server.touched), 3),
                }
            elif kind == "stop":
                answer = {"stopped": True, "pid": os.getpid(), "socket": str(server.socket_file)}
                stop_after_reply = True
            elif kind == "cli":
                if server.cli_runner is None:
                    answer = {"fallback": True}
                else:
                    with server.lock:
                        _local.in_request = True
                        try:
                            answer = _execute_cli(server.cli_runner, request, server.safe_cwd)
                        finally:
                            _local.in_request = False
            else:
                with server.lock:
                    _local.in_request = True
                    try:
                        answer = {"snapshot": _util().snapshot(request["root"], request["nested"])}
                    finally:
                        _local.in_request = False
        except Exception as error:  # noqa: BLE001
            paused_type = getattr(_UTIL, "Paused", ())
            if paused_type and isinstance(error, paused_type):
                answer = {"paused": {"status": error.status, "reason": str(error)}}
            else:
                answer = {"error": type(error).__name__}
        with contextlib.suppress(OSError):
            self.wfile.write(json.dumps(answer).encode() + b"\n")
            self.wfile.flush()
        server.touched = time.monotonic()
        if stop_after_reply:
            server.stop_event.set()


if sys.platform != "win32":
    _UnixStreamServer = socketserver.ThreadingUnixStreamServer
else:  # pragma: no cover - Windows
    _UnixStreamServer = socketserver.ThreadingTCPServer


class _Server(_UnixStreamServer):
    daemon_threads = True
    request_queue_size = 64
    touched = 0.0
    idle_seconds = float(IDLE_SECONDS)
    cli_runner = None
    socket_file = Path()
    safe_cwd = "/"

    def __init__(self, server_address: Any, handler_class: Any, *, cli_runner=None, idle_seconds: float = IDLE_SECONDS):
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.cli_runner = cli_runner
        self.idle_seconds = float(idle_seconds)
        self.socket_file = Path(server_address)
        self.safe_cwd = str(self.socket_file.parent)
        super().__init__(server_address, handler_class)


@contextlib.contextmanager
def _serve_lock(path: Path):
    if os.name == "nt":
        yield True
        return
    import fcntl

    lock_path = path.with_suffix(".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                lock_path.unlink()
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def serve(path: Path, *, cli_runner=None, warmup=None, idle_seconds: float = IDLE_SECONDS) -> None:
    """Answer snapshots and read-only CLI queries on ``path`` until idle for ``idle_seconds`` or stopped."""
    drop_git_repository_variables()
    path = Path(path)
    if not _ensure_private_parent(path):
        raise RuntimeError(f"Refusing to serve warm host through non-private directory {path.parent}")
    with _serve_lock(path) as acquired:
        if not acquired:
            return
        with contextlib.suppress(OSError), socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(CONNECT_SECONDS)
            probe.connect(str(path))
            return  # another host already answers here
        if callable(warmup):
            warmup()
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        previous_umask = os.umask(0o077)
        try:
            server = _Server(str(path), _Handler, cli_runner=cli_runner, idle_seconds=idle_seconds)
        finally:
            os.umask(previous_umask)
        server.touched = time.monotonic()

        previous_signals: dict[signal.Signals, Any] = {}
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                previous_signals[sig] = signal.signal(sig, lambda *_: server.stop_event.set())

        def watch():
            while not server.stop_event.is_set():
                remaining = server.idle_seconds - (time.monotonic() - server.touched)
                if remaining <= 0:
                    break
                server.stop_event.wait(min(1.0, max(0.02, remaining)))
            server.shutdown()

        threading.Thread(target=watch, daemon=True).start()
        poll = min(0.5, max(0.02, float(idle_seconds) / 2.0))
        try:
            server.serve_forever(poll_interval=poll)
        finally:
            server.stop_event.set()
            for sig, handler in previous_signals.items():
                signal.signal(sig, handler)
            server.server_close()
            with contextlib.suppress(OSError):
                path.unlink()


if __name__ == "__main__":
    try:
        from . import autocode_entry as _entry
    except ImportError:
        import autocode_entry as _entry

    serve(Path(sys.argv[1]), cli_runner=_entry.run_in_host, warmup=_entry._autocode_module)

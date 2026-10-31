"""Standard-library helpers every layer of AutoCode shares: the clock, hashing,
atomic JSON files, workspace and run locks, source snapshots and the small JSON
Schema subset used by reports.

The bottom layer (AGENTS.md): it imports nothing from AutoCode, so any module can
use it without creating an import cycle.
"""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import errno
import hashlib
import importlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from pathlib import Path

# Native Windows (#698, docs/windows.md). Read at call time, so a test can take either
# branch on any host.
_WINDOWS = sys.platform == "win32"

# The reasoning-effort ladder, lowest first (docs/models.md). One definition for the
# role/effort machinery: the planning unit steps a Plan Reviewer down it and
# units/common caps read-only routes along it, so no layer invents a second order.
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def now():
    return dt.datetime.now(dt.UTC).isoformat()


def monotonic() -> float:
    """The one monotonic clock (#704). Every wait and deadline in tools/ reads time
    through here so a test can install a fake that advances on demand instead of
    sleeping; `sleep(seconds)` below is its matching wait."""
    return time.monotonic()


def sleep(seconds: float) -> None:
    """The one wait (#704). Pair with `monotonic()`: a fake clock advances its
    reading here, so watchdog loops run in milliseconds under tests."""
    time.sleep(seconds)


_RUN_PROCESS_HANDLERS: dict = {}


def run_process(cmd, *, input=None, env=None, cwd=None):
    """The one process spawn (#704 slice 3).

    Delegates to subprocess.run. When AUTOCODE_RUN_PROCESS names an importable
    module, that module's ``run_process(cmd, *, input=None, env=None)`` executes
    the command in-process instead — tests point it at a handler so a whole
    battery of grand-child interpreter startups collapses into imports. The
    handler must swallow SystemExit from scripted mains and return an object
    with ``returncode``, ``stdout`` and ``stderr``. When ``cwd`` is supplied,
    the handler must accept it and execute relative to that directory; omitted
    ``cwd`` retains the original handler call shape.
    """
    handler = os.environ.get("AUTOCODE_RUN_PROCESS")
    if handler:
        module = _RUN_PROCESS_HANDLERS.get(handler)
        if module is None:
            if os.sep in handler or handler.endswith(".py"):
                spec = importlib.util.spec_from_file_location("autocode_run_process_handler", handler)
                loaded = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(loaded)
            else:
                loaded = importlib.import_module(handler)
            _RUN_PROCESS_HANDLERS[handler] = module = loaded
        if cwd is not None:
            return module.run_process(cmd, input=input, env=env, cwd=cwd)
        return module.run_process(cmd, input=input, env=env)
    if cwd is not None:
        return subprocess.run(cmd, input=input, env=env, cwd=cwd)
    return subprocess.run(cmd, input=input, env=env)


def source_commit() -> str | None:
    """The AutoCode checkout's commit, with ``+modified`` when its tools/ has uncommitted changes.

    A commit is reported only when this module sits in a Git work tree whose top
    level is this project's root (AutoCode's ``pyproject.toml`` with the
    autocode-supervisor name and a version). An installed package inside some
    other repository (a venv under a user project) must report None, never that
    project's HEAD (#339). Moved from autocode_subcommands so the bottom-layer
    util can stamp run states (#980) without importing upward.
    """
    import subprocess as _sp

    def git(*args):
        try:
            return _sp.run(
                ["git", "-C", str(Path(__file__).resolve().parent), *args], capture_output=True, text=True, timeout=10
            )
        except (OSError, _sp.TimeoutExpired):
            return None

    top = git("rev-parse", "--show-toplevel")
    if top is None or top.returncode != 0 or not top.stdout.strip():
        return None
    try:
        top_path = Path(top.stdout.strip()).resolve()
    except OSError:
        return None
    tools_dir = Path(__file__).resolve().parent
    if top_path != tools_dir.parent.resolve() or not _is_autocode_checkout(top_path):
        return None
    head = git("rev-parse", "--short", "HEAD")
    if head is None or head.returncode != 0 or not head.stdout.strip():
        return None
    dirty = git("status", "--porcelain", "--", str(tools_dir))
    return head.stdout.strip() + ("+modified" if dirty and dirty.stdout.strip() else "")


def _is_autocode_checkout(root: Path) -> bool:
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text()).get("project") or {}
    except (OSError, ValueError):
        return False
    return project.get("name") == "autocode-supervisor" and bool(project.get("version"))


_IDENTITY_CACHE: dict | None = None


def runner_identity(commit_source=None) -> dict:
    """Which autocode code is executing (#980): the runner's path and source commit.

    The commit is source_commit() above — the #339 guard applies (an installed
    package inside a foreign repository reports None, and ``+modified`` marks
    uncommitted changes). A run state stamped with this identity can be triaged
    when the architecture moves underneath it, instead of requiring two-tree
    forensics. Cached per process: the runner cannot change inside one process,
    and tests that patch subprocess must not see git probes on every persist."""
    if commit_source is not None:
        return {"path": str(Path(__file__).resolve().parent), "commit": commit_source()}
    global _IDENTITY_CACHE
    if _IDENTITY_CACHE is None:
        _IDENTITY_CACHE = {"path": str(Path(__file__).resolve().parent), "commit": source_commit()}
    return dict(_IDENTITY_CACHE)


def runner_changed(old, new) -> str | None:
    """The resume warning when the runner identity moved since the last save, or None."""
    if not old or not new:
        return None
    if old.get("commit") == new.get("commit"):
        return None
    old_commit = old.get("commit") or "unknown"
    new_commit = new.get("commit") or "unknown"
    return f"runner changed since the last save: {old_commit} -> {new_commit}; resuming under different autocode code"


def slug(task: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")
    return (value or "task")[:48]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# Token shapes that must never leave the run directory in an export (#712).
# Raw captures stay untouched; this pass is applied where text is copied out.
# A word that starts `risk-` is not an `sk-` key: AutoCode's own `risk-<sha256>` declaration ids (#720).
# Only that word: a key after a letter is still masked, as after the `n` of a JSON-escaped newline.
_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?<!\bri)sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]{12,}"),
    re.compile(r'(?i)\b(?:api[_-]?key|secret|token|password|passwd)\b\s*[=:]\s*[\'"]?[^\s\'"]{8,}'),
)
_REDACTED = "[REDACTED]"


def redact(text: str, secret_values=()) -> str:
    """Replace credential-shaped runs and known secret values with a marker.

    Applied where text leaves the run directory (pull request bodies, exported
    reports, bundles). The raw capture in the run directory is never rewritten;
    evidence hashes keep covering the raw bytes (#712).
    """
    if not isinstance(text, str) or not text:
        return text
    for value in secret_values:
        if isinstance(value, str) and len(value) >= 8:
            text = text.replace(value, _REDACTED)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class JSONCommitUncertain(OSError):
    """An observed parent write reached replacement; ordinary rollback is unsafe.

    ``candidate`` is the exact normalized value offered to the original sink,
    not a read of a saved run or an assertion that directory fsync succeeded.
    Only callers opting into before_commit receive this failure disposition.
    """

    def __init__(self, error, *, candidate, replaced, durable):
        super().__init__(str(error))
        self.candidate = candidate
        self.replaced = replaced
        self.durable = durable


def atomic_json(path, value, *, before_commit=None, after_commit=None):
    """Save atomically; an opt-in observer sees the exact durable sink only.

    The observer runs after file fsync, atomic replacement and directory
    fsync. Its receipt is not a transaction or application approval; the
    caller must finish its own boundary before publishing any authority.
    Observer failure occurs after persistence and must not imply rollback.
    """
    for name, callback in (("before_commit", before_commit), ("after_commit", after_commit)):
        if callback is not None and not callable(callback):
            raise TypeError(name + " must be callable")
    observed = before_commit is not None or after_commit is not None
    serialized = (
        (json.dumps(value, indent=2, sort_keys=True, allow_nan=before_commit is None) + "\n").encode("utf-8")
        if observed
        else None
    )
    if before_commit is not None:
        before_commit(value=json.loads(serialized), serialized_bytes=serialized)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    replace_attempted = replaced = durable = False
    try:
        with os.fdopen(fd, "w") as stream:
            if serialized is None:
                json.dump(value, stream, indent=2, sort_keys=True)
                stream.write("\n")
            else:
                stream.write(serialized.decode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
            written = os.fstat(stream.fileno()) if after_commit is not None else None
        replace_attempted = True
        os.replace(tmp, path)
        replaced = True
        # Make the rename durable where the platform allows it. Windows cannot open a directory
        # with os.open (PermissionError), so every JSON write failed there (#698); the file's own
        # data is already synced above.
        if not _WINDOWS:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
                durable = True
            finally:
                os.close(directory)
        if after_commit is not None:
            committed = os.stat(path, follow_symlinks=False)
            if (
                (committed.st_dev, committed.st_ino) != (written.st_dev, written.st_ino)
                or committed.st_size != len(serialized)
                or committed.st_nlink != 1
            ):
                raise OSError("Committed JSON sink identity changed")
            after_commit(
                value=json.loads(serialized),
                serialized_bytes=serialized,
                sink_identity={
                    "path": str(path.absolute()),
                    "device": committed.st_dev,
                    "inode": committed.st_ino,
                    "bytes": committed.st_size,
                    "mode": committed.st_mode & 0o7777,
                },
            )
    except Exception as error:
        if before_commit is not None and replace_attempted:
            raise JSONCommitUncertain(
                error, candidate=json.loads(serialized), replaced=replaced, durable=durable
            ) from error
        raise
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read(path):
    return json.loads(Path(path).read_text())


def read_object(path):
    """A stage's JSON report file, which must hold one object."""
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Agent did not produce valid JSON at {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected an object in {path}")
    return value


class Paused(RuntimeError):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status


def boundary_pause(error) -> bool:
    """A pause after a report was accepted, not a rejected output.

    The completion gate runs once the report passed its own checks; it says the run
    still lacks evidence, so recording that report as a failed output would hide the
    gate's reason behind an Investigator (#1851). The runner sets the attempt aside
    under the gate's own status instead (autocode_run_records.set_aside_at_completion_gate).
    Output-shaped pauses such as PAUSED_ASSIGNMENT_SCOPE stay ordinary rejections.
    """
    return isinstance(error, Paused) and error.status == "PAUSED_COMPLETION_GATE"


class MissingModule:
    """Stands in for a dependency that failed to import, so a module that needs it only
    at call time still loads: `autocode --version` and `autocode doctor` work without
    psutil (#67). Using it raises ModuleNotFoundError naming the fix."""

    def __init__(self, name, error):
        self._name, self._error = name, error

    def __getattr__(self, attribute):
        if attribute.startswith("__"):  # hasattr, copy and pickle probe dunders; keep those ordinary
            raise AttributeError(attribute)
        raise ModuleNotFoundError(
            f"{self._name} cannot be imported ({self._error}); reinstall AutoCode "
            "(docs/install.md) and run `autocode doctor`",
            name=self._name,
        )


# Native Windows has no fcntl (#698): the file locks below go through lock_file/unlock_file,
# which use msvcrt there.
# msvcrt locks byte ranges, and a Windows byte-range lock is mandatory: other processes cannot
# read a locked byte. Lock one byte far past any content instead of byte 0, so a lock file that
# also records its holder stays readable. Locking past the end of a file is allowed on Windows.
_WINDOWS_LOCK_OFFSET = 1 << 30
# Errors msvcrt.locking raises when another handle holds the range.
_WINDOWS_LOCK_BUSY = frozenset({errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK)})
# How often a blocking lock retries on Windows, where msvcrt has no wait-forever mode.
WINDOWS_LOCK_RETRY_SECONDS = 0.05


def _fileno(handle):
    return handle if isinstance(handle, int) else handle.fileno()


def _windows_locking(fd, mode):
    """Apply one msvcrt.locking call to the lock byte, keeping the caller's file position."""
    msvcrt = importlib.import_module("msvcrt")
    position = os.lseek(fd, 0, os.SEEK_CUR)
    os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
    try:
        msvcrt.locking(fd, getattr(msvcrt, mode), 1)
    finally:
        os.lseek(fd, position, os.SEEK_SET)


def lock_file(handle, *, blocking=False):
    """Take an exclusive advisory lock on an open file (or file descriptor).

    POSIX uses ``fcntl.flock``; native Windows uses ``msvcrt.locking`` on one byte (#698).
    Without ``blocking``, a lock another handle holds raises BlockingIOError on both, which
    callers turn into their own busy pause. With ``blocking`` it waits for the holder.
    """
    if not _WINDOWS:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    fd = _fileno(handle)
    while True:
        try:
            _windows_locking(fd, "LK_NBLCK")
            return
        except OSError as error:
            if error.errno not in _WINDOWS_LOCK_BUSY:
                raise
            if not blocking:
                raise BlockingIOError(errno.EAGAIN, "Another handle holds this file lock") from error
        sleep(WINDOWS_LOCK_RETRY_SECONDS)


def unlock_file(handle):
    """Release a lock taken with lock_file."""
    if not _WINDOWS:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_UN)
        return
    _windows_locking(_fileno(handle), "LK_UNLCK")


@contextlib.contextmanager
def workspace_lock(workspace):
    path = Path(workspace) / ".autocode" / "writer.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            lock_file(handle)
        except BlockingIOError:
            raise Paused("PAUSED_WORKSPACE_BUSY", "Another autocode runner holds this workspace lock") from None
        try:
            yield
        finally:
            unlock_file(handle)


@contextlib.contextmanager
def run_lock(run_dir):
    """Serialize mutations for one run without blocking independent runs."""
    path = Path(run_dir) / "writer.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            lock_file(handle)
        except BlockingIOError:
            raise Paused("PAUSED_RUN_BUSY", "Another autocode runner holds this run lock") from None
        try:
            yield handle
        finally:
            unlock_file(handle)


GIT_TIMEOUT = 600  # seconds; a checkout of a large repository can take minutes, a hung hook never ends


def git(root, *args, timeout=None, env=None, input=None, check=True):
    """Run ``git -C root args`` and return its stripped stdout.

    The user's own hooks are off (``core.hooksPath``), so a post-checkout or pre-commit hook cannot run inside
    AutoCode's internal Git operations, and the call is bounded by ``timeout`` (``GIT_TIMEOUT`` when None).
    A failure raises ValueError with Git's message; with ``check`` False it returns whatever stdout there was.
    """
    seconds = GIT_TIMEOUT if timeout is None else timeout
    command = ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(root), *map(str, args)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=seconds, env=env, input=input)
    except subprocess.TimeoutExpired:
        raise ValueError(f"git {' '.join(map(str, args[:2]))} did not finish in {seconds} seconds") from None
    if check and result.returncode:
        raise ValueError(result.stderr.strip() or "Git command failed")
    return result.stdout.strip()


def git_names(output):
    """File names in NUL-separated Git output, decoded the way the file system does (surrogateescape).

    A name that is not UTF-8 stays one usable name; a strict decode raised on it and stopped every snapshot,
    so the run could not resume until a person renamed the file (#1116).
    """
    return [os.fsdecode(name) for name in output.split(b"\0") if name]


class ContentHashes:
    """A source file's sha256, remembered while the file system's identity for it holds (#711).

    Every snapshot read every file: seconds for 100,000 files, and a run takes
    dozens of snapshots. The identity is device, inode, size, modification time and
    status-change time. Any write sets the status-change time to the current time,
    and no ordinary process can set it back, so an unchanged identity means
    unchanged bytes, with two exceptions this class refuses, as Git does for its index:

    - Two writes in one timestamp tick leave equal times. A hash is kept only for a
      file whose times are at least ``SETTLE_NS`` older than the snapshot's start,
      so a later write cannot share its tick.
    - A wall clock that jumps can stamp a later write with an earlier time. When the
      wall clock drifts from the monotonic clock by ``CLOCK_TOLERANCE_NS``, every
      hash is forgotten, including those of a snapshot still in progress (a
      workspace's own, while a nested repository's snapshot finds the jump).

    On Windows ``st_ctime`` is the creation time and any writer can set a file's
    times back, so nothing there proves the bytes unchanged: ``REUSE`` is off and
    every snapshot reads every file.

    Hashes stay in this process and only for the last ``ROOTS`` workspaces
    snapshotted; a file on disk would let any process that can write the workspace
    change what a snapshot reports. Hash values are unchanged: sha256 of the bytes.
    """

    SETTLE_NS = 10 * 10**9
    CLOCK_TOLERANCE_NS = 10**9
    ROOTS = 8
    REUSE = os.name != "nt"

    def __init__(self, wall_ns=time.time_ns, monotonic_ns=time.monotonic_ns):
        self._wall_ns, self._monotonic_ns = wall_ns, monotonic_ns
        self._lock = threading.Lock()
        self._anchor = None
        self._epoch = 0  # counts the times every hash was forgotten
        self._roots = {}  # workspace -> {identity: sha256}, least recently snapshotted first

    def begin(self, root):
        """The hashes kept from ``root``'s last snapshot, the time before which a file is settled, and the epoch."""
        wall, monotonic = self._wall_ns(), self._monotonic_ns()
        with self._lock:
            anchor = self._anchor
            if anchor is None or abs((wall - anchor[0]) - (monotonic - anchor[1])) > self.CLOCK_TOLERANCE_NS:
                self._roots.clear()
                self._anchor = (wall, monotonic)
                self._epoch += 1
            if not self.REUSE:
                return {}, float("-inf"), self._epoch  # no file is ever settled
            return self._roots.pop(root, {}), wall - self.SETTLE_NS, self._epoch

    def finish(self, root, kept, epoch):
        """Keep the hashes this snapshot of ``root`` used or settled, and nothing older."""
        with self._lock:
            self._roots.pop(root, None)
            if epoch != self._epoch:
                return  # the clock jumped after this snapshot began: keep nothing from it
            self._roots[root] = kept
            while len(self._roots) > self.ROOTS:
                del self._roots[next(iter(self._roots))]

    @staticmethod
    def content(path, info, known, kept, settled_before):
        """sha256 of the regular file at ``path``, whose ``os.lstat`` is ``info``."""
        identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        value = known.get(identity)
        if value is None:
            h = hashlib.sha256()
            with open(path, "rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
                after = os.fstat(stream.fileno())
            value = h.hexdigest()
            read = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            if read != identity or max(info.st_mtime_ns, info.st_ctime_ns) >= settled_before:
                return value  # changed while it was read, or too recent to trust its times
        kept[identity] = value
        return value


_CONTENT_HASHES = ContentHashes()


def _other_entry(path, unborn_ok=True):
    """A listed name that is neither a file nor a link: a nested repository, gone, or left out."""
    if path.is_dir():
        return (
            "submodule:" + snapshot(path, nested=True, unborn_ok=unborn_ok)["revision"]
            if (path / ".git").exists()
            else "uninitialized-submodule"
        )
    if not path.exists():
        return "deleted"
    return None


def _snapshot_git(root, *args, text=False):
    try:
        return subprocess.check_output(["git", *args], cwd=root, text=text, stderr=subprocess.PIPE)
    except (subprocess.CalledProcessError, OSError) as error:
        detail = getattr(error, "stderr", None) or str(error)
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise Paused(
            "PAUSED_SOURCE_UNAVAILABLE",
            f"Cannot snapshot source in {root}: git {' '.join(args)} failed: {detail.strip()[:2000]}. "
            "Fix repository access or ownership, then resume. No source snapshot was accepted.",
        ) from error


def _unborn(root):
    """Whether ``root`` is a Git repository with no commit yet (``git init`` and nothing more)."""
    try:
        found = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "HEAD"], cwd=root, capture_output=True)
    except OSError:
        return False
    return found.returncode == 1 and not found.stdout and not found.stderr


_SNAPSHOT_SERVICE = None


def use_snapshot_service(service) -> None:
    """Ask ``service(workspace, nested)`` first; None from it means take the snapshot here (#1707)."""
    global _SNAPSHOT_SERVICE
    _SNAPSHOT_SERVICE = service


def inventory_excluded(name: str) -> bool:
    """Whether a workspace name is outside the checked source inventory: run state and Python bytecode caches."""
    return name.startswith((".autocode/", ".autocode-ui/")) or "/__pycache__/" in f"/{name}" or name.endswith(".pyc")


def snapshot(workspace, nested=False, unborn_ok=True):
    """Hash current source content, executable modes and nested Git worktrees.

    A nested repository with no commit yet (a scratch ``git init`` inside the project) has
    head "unborn" rather than stopping the run; the workspace itself still needs a HEAD.
    ``unborn_ok=False`` keeps the refusal for callers that bind an identity to the whole tree.
    """
    if _SNAPSHOT_SERVICE is not None and unborn_ok and (served := _SNAPSHOT_SERVICE(workspace, nested)) is not None:
        return served
    root = Path(workspace)
    names = git_names(_snapshot_git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"))
    hashes = _CONTENT_HASHES
    key = os.path.abspath(root)
    known, settled_before, epoch = hashes.begin(key)
    kept = {}
    files = {}
    for name in sorted(set(names)):
        if inventory_excluded(name):
            continue
        # One lstat on a plain string: building a Path and stating it three times cost more than hashing.
        # Git lists an embedded repository as "name/"; Path dropped that slash, so lstat must not follow it.
        full = os.path.join(root, name.rstrip("/"))
        try:
            info = os.lstat(full)
        except OSError:
            info = None  # missing or unreadable: _other_entry decides, as the path checks always have
        if info is not None and stat.S_ISLNK(info.st_mode):
            files[name] = "symlink:" + os.readlink(full)
        elif info is not None and stat.S_ISREG(info.st_mode):
            content = hashes.content(full, info, known, kept, settled_before)
            files[name] = ("executable:" if info.st_mode & 0o111 else "") + content
        elif (entry := _other_entry(root / name, unborn_ok)) is not None:
            files[name] = entry
    head = (
        "unborn"
        if nested and unborn_ok and _unborn(root)
        else _snapshot_git(root, "rev-parse", "HEAD", text=True).strip()
    )
    hashes.finish(key, kept, epoch)
    return {"head": head, "files": files, "revision": digest({"head": head, "files": files})}


def changed_paths(before, after):
    return sorted(
        p for p in before["files"].keys() | after["files"].keys() if before["files"].get(p) != after["files"].get(p)
    )


# OpenAI Structured Outputs (and LiteLLM fronts that mirror it, including gocode)
# reject ``uniqueItems`` on the wire. Keep it on Autocode's saved / validation
# schemas; strip only the copy sent as --output-schema. Uniqueness is enforced
# after the reply (brief_acceptance.normalized_proposal, risk_acceptance._proposal).
# Do not strip minItems/maxItems/pattern: those stay in the wire schema and in
# Autocode validation of the same file (record["schema"]).
_MODEL_OUTPUT_UNSUPPORTED = frozenset({"uniqueItems"})


def model_output_schema(schema):
    """Strict generation schema; retain permissive schemas for saved reports.

    Codex structured output requires every object property to be required.
    Requiring fields in new responses must not invalidate sealed old contracts
    or mutate shared schema constants (e.g. legacy optional milestone ownership).
    Providers that forward the schema to OpenAI-compatible structured outputs also
    reject ``uniqueItems``; strip that keyword here and keep enforcing uniqueness
    in Autocode validators after the model replies.
    """
    result = copy.deepcopy(schema)

    def visit(node):
        if isinstance(node, dict):
            for key in _MODEL_OUTPUT_UNSUPPORTED:
                node.pop(key, None)
            if node.get("type") == "object":
                node["required"] = list(node.get("properties", {}))
                node["additionalProperties"] = False
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(result)
    return result


def validate_schema(value, schema, where="$"):
    """The small, strict JSON Schema subset used by our checked-in verdicts."""
    kind = schema.get("type")
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    kinds = kind if isinstance(kind, list) else [kind] if kind else []  # ["object", "null"]: an object or null
    if kinds and not any(
        isinstance(value, types[k]) and not (k in ("integer", "number") and isinstance(value, bool)) for k in kinds
    ):
        raise ValueError(f"{where}: expected {' or '.join(kinds)}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{where}: invalid enum")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{where}: missing {key}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and value.keys() - props.keys():
            raise ValueError(f"{where}: unexpected fields: {', '.join(sorted(value.keys() - props.keys()))}")
        for key, child in value.items():
            if key in props:
                validate_schema(child, props[key], f"{where}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{where}: too few items")
        if len(value) > schema.get("maxItems", len(value)):
            raise ValueError(f"{where}: too many items")
        for index, child in enumerate(value):
            validate_schema(child, schema.get("items", {}), f"{where}[{index}]")


def criteria_definition(criteria):
    """The id and wording of each acceptance criterion, as stages are shown them."""
    return [{"id": c["id"], "criterion": c["criterion"]} for c in criteria]

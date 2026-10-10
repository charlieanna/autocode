"""Standard-library helpers every layer of AutoCode shares: the clock, hashing,
atomic JSON files, workspace and run locks, source snapshots and the small JSON
Schema subset used by reports.

The bottom layer (AGENTS.md): it imports nothing from AutoCode, so any module can
use it without joining the import cycle through autocode.py.
"""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import fcntl
import hashlib
import importlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path


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


def run_process(cmd, *, input=None, env=None):
    """The one process spawn (#704 slice 3).

    Delegates to subprocess.run. When AUTOCODE_RUN_PROCESS names an importable
    module, that module's ``run_process(cmd, *, input=None, env=None)`` executes
    the command in-process instead — tests point it at a handler so a whole
    battery of grand-child interpreter startups collapses into imports. The
    handler must swallow SystemExit from scripted mains and return an object
    with ``returncode``, ``stdout`` and ``stderr``.
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
        return module.run_process(cmd, input=input, env=env)
    return subprocess.run(cmd, input=input, env=env)


def slug(task: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")
    return (value or "task")[:48]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# Token shapes that must never leave the run directory in an export (#712).
# Raw captures stay untouched; this pass is applied where text is copied out.
_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
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


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
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


@contextlib.contextmanager
def workspace_lock(workspace):
    path = Path(workspace) / ".autocode" / "writer.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Paused("PAUSED_WORKSPACE_BUSY", "Another autocode runner holds this workspace lock") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextlib.contextmanager
def run_lock(run_dir):
    """Serialize mutations for one run without blocking independent runs."""
    path = Path(run_dir) / "writer.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Paused("PAUSED_RUN_BUSY", "Another autocode runner holds this run lock") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def snapshot(workspace):
    """Hash current source content, executable modes and nested Git worktrees."""
    root = Path(workspace)
    names = (
        subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root)
        .decode()
        .split("\0")
    )
    files = {}
    for name in sorted(set(filter(None, names))):
        if name.startswith((".autocode/", ".autocode-ui/")) or "/__pycache__/" in f"/{name}" or name.endswith(".pyc"):
            continue
        path = root / name
        if path.is_symlink():
            files[name] = "symlink:" + os.readlink(path)
        elif path.is_file():
            files[name] = ("executable:" if path.stat().st_mode & 0o111 else "") + file_hash(path)
        elif path.is_dir():
            files[name] = (
                "submodule:" + snapshot(path)["revision"] if (path / ".git").exists() else "uninitialized-submodule"
            )
        elif not path.exists():
            files[name] = "deleted"
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    return {"head": head, "files": files, "revision": digest({"head": head, "files": files})}


def changed_paths(before, after):
    return sorted(
        p for p in before["files"].keys() | after["files"].keys() if before["files"].get(p) != after["files"].get(p)
    )


def model_output_schema(schema):
    """Strict generation schema; retain permissive schemas for saved reports.

    Codex structured output requires every object property to be required.
    Requiring fields in new responses must not invalidate sealed old contracts
    or mutate shared schema constants (e.g. legacy optional milestone ownership).
    """
    result = copy.deepcopy(schema)

    def visit(node):
        if isinstance(node, dict):
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

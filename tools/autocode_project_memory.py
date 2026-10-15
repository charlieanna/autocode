"""Bounded advisory project preferences, separate from task or approval authority.

Only current-run facts enter ``write``. Loading a snapshot does not make it an
update: callers must not send its old conventions back after a person forgets
memory. Unsafe strings are omitted whole, never redacted into false quotations.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import shlex
import stat
import uuid
from contextlib import contextmanager, suppress
from pathlib import Path

try:
    from . import autocode_agent_env as agent_env
    from . import autocode_util as util
except ImportError:
    import autocode_agent_env as agent_env
    import autocode_util as util

VERSION = 1
MAX_BYTES = 65536
MAX_CONTEXT_BYTES = 16384
MAX_CONVENTIONS = 32
MAX_ROUTES = 32
MAX_PAUSE_STATES = 32
MAX_QUESTION = 512
MAX_ANSWER = 2048
MAX_COMMAND = 2048
MAX_FRAMEWORK = 80
MAX_ROUTE_VALUE = 128
MAX_PAUSE_STATE = 96
FILENAME = "project.json"
LOCKFILE = "project-memory.lock"
FIELDS = frozenset({"version", "framework", "test_command", "conventions", "routes", "human_pause_states"})
PAUSE_PATTERN = r"^(?:WAITING_FOR_USER|AWAITING_GOAL_APPROVAL|PAUSED_[A-Z0-9_]+)$"
# Paths are local facts, not portable conventions. Relative project paths remain usable.
ABSOLUTE_PATH = re.compile(
    r"(?:^|[\s\"'=:(`\[])(?:/(?!/)[^\s,;\"')]*|~[/\\]|[A-Za-z]:[/\\]|\\\\[^\s]+)|\bfile://", re.I
)
PRIVATE_PATH = re.compile(
    r"(?:^|[\s/\\\"'`\[])\.(?:autocode|ssh|aws|azure|config)(?:[/\\]|$)|(?:^|[/\\\s])\.env(?:\b|$)"
)
ASSIGNMENT = re.compile(r"\b([A-Za-z][A-Za-z0-9_-]*)\s*[=:]\s*([^\s]+)")
FLAG_VALUE = re.compile(r"--([A-Za-z][A-Za-z0-9_-]*)(?:=|\s+)([^\s]+)")
VARIABLE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
CREDENTIAL_PROSE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:bearer|basic)\s+[A-Za-z0-9+/_.=~-]+"
    r"|\b(?:authorization|proxy-authorization)\s*:|\b(?:password|passwd|secret|api[ _-]?key|access[ _-]?token)\s+(?:is|equals)\s+\S+",
    re.I,
)


def _error():
    return ValueError("Project memory is invalid or unsafe; its existing file was preserved.")


def _string(value, limit, *, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise _error()
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise _error() from error
    if any(ord(char) < 32 and char not in "\n\t" or ord(char) == 127 for char in value):
        raise _error()


def valid_pause_state(value):
    return isinstance(value, str) and len(value) <= MAX_PAUSE_STATE and re.fullmatch(PAUSE_PATTERN, value) is not None


def _structure(record):
    if (
        not isinstance(record, dict)
        or set(record) != FIELDS
        or type(record.get("version")) is not int
        or record["version"] != VERSION
    ):
        raise _error()
    _string(record["framework"], MAX_FRAMEWORK, nullable=True)
    _string(record["test_command"], MAX_COMMAND, nullable=True)
    for key, limit in (
        ("conventions", MAX_CONVENTIONS),
        ("routes", MAX_ROUTES),
        ("human_pause_states", MAX_PAUSE_STATES),
    ):
        if not isinstance(record[key], list) or len(record[key]) > limit:
            raise _error()
    questions, roles, pauses = set(), set(), set()
    for row in record["conventions"]:
        if not isinstance(row, dict) or set(row) != {"question", "answer"}:
            raise _error()
        _string(row["question"], MAX_QUESTION)
        _string(row["answer"], MAX_ANSWER)
        if row["question"] in questions:
            raise _error()
        questions.add(row["question"])
    for row in record["routes"]:
        if not isinstance(row, dict) or set(row) != {"role", "engine", "provider", "model"}:
            raise _error()
        _string(row["role"], MAX_ROUTE_VALUE)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", row["role"]) or row["role"] in roles:
            raise _error()
        roles.add(row["role"])
        for key in ("engine", "provider", "model"):
            _string(row[key], MAX_ROUTE_VALUE, nullable=True)
    for value in record["human_pause_states"]:
        if not valid_pause_state(value) or value in pauses:
            raise _error()
        pauses.add(value)
    if len(_encoded(record)) > MAX_BYTES:
        raise _error()


def _secret_name(name, value):
    # The environment policy keeps ordinary AUTOCODE settings and proxy names;
    # that passthrough permission is not authority to commit their secret values.
    name = name.replace("-", "_")
    if name.upper().startswith("AUTOCODE_"):
        name = name[len("AUTOCODE_") :]
    return agent_env.is_secret(name, value)


def _safe(value, *, command=False):
    if value is None:
        return True
    known_secrets = [secret for name, secret in os.environ.items() if _secret_name(name, secret)]
    if (
        any(secret and secret in value for secret in known_secrets)
        or util.redact(value, known_secrets) != value
        or agent_env.URL_WITH_PASSWORD.search(value)
        or ABSOLUTE_PATH.search(value)
        or PRIVATE_PATH.search(value)
        or CREDENTIAL_PROSE.search(value)
    ):
        return False
    if any(
        _secret_name(name, data.strip("\"'")) for name, data in [*ASSIGNMENT.findall(value), *FLAG_VALUE.findall(value)]
    ):
        return False
    if any(_secret_name(name, "") for name in VARIABLE.findall(value)):
        return False
    if command:
        if "\n" in value or "\r" in value:
            return False
        try:
            return bool(shlex.split(value))
        except ValueError:
            return False
    return True


def sanitize_record(record):
    """Validate the exact v1 shape, then drop entire unsafe optional facts/rows."""
    _structure(record)
    return {
        "version": VERSION,
        "framework": record["framework"] if _safe(record["framework"]) else None,
        "test_command": record["test_command"] if _safe(record["test_command"], command=True) else None,
        "conventions": [
            copy.deepcopy(row)
            for row in record["conventions"]
            if all(_safe(row[key]) for key in ("question", "answer"))
        ],
        "routes": [copy.deepcopy(row) for row in record["routes"] if all(_safe(value) for value in row.values())],
        "human_pause_states": list(record["human_pause_states"]),
    }


def _encoded(record):
    return (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _pairs(pairs):
    found = {}
    for key, value in pairs:
        if key in found:
            raise _error()
        found[key] = value
    return found


def _signature(value):
    if value is None:
        return None
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _regular(value):
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise _error()


def _entry(directory, name):
    try:
        return os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _read(directory):
    before = _entry(directory, FILENAME)
    if before is None:
        return None, None, None
    _regular(before)
    if before.st_size > MAX_BYTES:
        raise _error()
    leaf = os.open(FILENAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        opened = os.fstat(leaf)
        _regular(opened)
        if _signature(before) != _signature(opened):
            raise _error()
        with os.fdopen(leaf, "rb", closefd=False) as stream:
            data = stream.read(MAX_BYTES + 1)
        if (
            len(data) > MAX_BYTES
            or _signature(opened) != _signature(os.fstat(leaf))
            or _signature(opened) != _signature(_entry(directory, FILENAME))
        ):
            raise _error()
    finally:
        os.close(leaf)
    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(_error())
        )
        record = sanitize_record(value)
    except (ValueError, UnicodeError, TypeError, RecursionError, OverflowError) as error:
        raise _error() from error
    return record, data, before


@contextmanager
def _directory(workspace, *, create):
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        path = Path(os.path.abspath(os.fspath(workspace)))
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        parent = descriptor
        try:
            directory = os.open(".autocode", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            if not create:
                yield None
                return
            os.mkdir(".autocode", mode=0o700, dir_fd=parent)
            directory = os.open(".autocode", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            yield directory
            current = os.stat(".autocode", dir_fd=parent, follow_symlinks=False)
            opened = os.fstat(directory)
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                raise _error()
        finally:
            os.close(directory)
    except (OSError, AttributeError) as error:
        raise _error() from error
    finally:
        os.close(descriptor)


@contextmanager
def _locked(directory):
    existing = _entry(directory, LOCKFILE)
    if existing is not None:
        _regular(existing)
    descriptor = os.open(LOCKFILE, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
    try:
        before = os.fstat(descriptor)
        _regular(before)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = _entry(directory, LOCKFILE)
        if current is None or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise _error()
        yield
    except OSError as error:
        raise _error() from error
    finally:
        os.close(descriptor)


def _snapshot(workspace):
    with _directory(workspace, create=False) as directory:
        if directory is None or _entry(directory, FILENAME) is None:
            return None, None
        record, data, _ = _read(directory)
        return record, hashlib.sha256(data).hexdigest() if data is not None else None


def read(workspace):
    """Return one validated safe record, or None; invalid input is never overwritten."""
    return _snapshot(workspace)[0]


def load(workspace):
    """Read advisory memory, reporting invalid inputs without echoing their contents."""
    try:
        record, pin = _snapshot(workspace)
    except (ValueError, OSError):
        return {
            "status": "invalid",
            "record": None,
            "sha256": None,
            "note": "Project memory could not be safely loaded; it was ignored.",
        }
    return {
        "status": "loaded" if record is not None else "absent",
        "record": record,
        "sha256": pin,
        "note": "Saved project preferences are advisory." if record is not None else "No project memory is saved.",
    }


def _loaded(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("status") != "loaded":
        return None
    try:
        return sanitize_record(snapshot.get("record"))
    except ValueError:
        return None


def defaults(snapshot):
    record = _loaded(snapshot)
    return {"test-command": record["test_command"]} if record and record["test_command"] else {}


def context(snapshot):
    """Exact historical question/answer data for Requirements only; never answers or criteria."""
    record = _loaded(snapshot)
    if record is None:
        return {}
    result = {"framework": record["framework"], "test_command": record["test_command"], "conventions": []}
    for row in record["conventions"]:
        candidate = {**result, "conventions": [*result["conventions"], row]}
        if len(_encoded(candidate)) <= MAX_CONTEXT_BYTES:
            result = candidate
    return result


def _merge(previous, current):
    if previous is None:
        return current
    conventions = {row["question"]: row for row in previous["conventions"]}
    for row in current["conventions"]:
        conventions.pop(row["question"], None)
        conventions[row["question"]] = row
    routes = {row["role"]: row for row in previous["routes"]}
    for row in current["routes"]:
        old = routes.pop(row["role"], {})
        routes[row["role"]] = {key: value if value is not None else old.get(key) for key, value in row.items()}
    pauses = dict.fromkeys([*previous["human_pause_states"], *current["human_pause_states"]])
    return {
        "version": VERSION,
        "framework": current["framework"] or previous["framework"],
        "test_command": current["test_command"] or previous["test_command"],
        "conventions": list(conventions.values())[-MAX_CONVENTIONS:],
        "routes": list(routes.values())[-MAX_ROUTES:],
        "human_pause_states": list(pauses)[-MAX_PAUSE_STATES:],
    }


def write(workspace, record):
    """Atomically merge current-run safe facts under the same lock as forget."""
    current = sanitize_record(record)
    with _directory(workspace, create=True) as directory, _locked(directory):
        previous, _, before = _read(directory)
        merged = sanitize_record(_merge(previous, current))
        data = _encoded(merged)
        temporary = ".project-memory-" + uuid.uuid4().hex
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            latest = _entry(directory, FILENAME)
            if (before is None) != (latest is None) or before is not None and _signature(before) != _signature(latest):
                raise _error()
            os.replace(temporary, FILENAME, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory)
    return merged


def forget(workspace):
    """Remove only project.json, refusing links and special files without reading them."""
    with _directory(workspace, create=False) as directory:
        if directory is None or _entry(directory, FILENAME) is None:
            return False
        with _locked(directory):
            before = _entry(directory, FILENAME)
            if before is None:
                return False
            _regular(before)
            os.unlink(FILENAME, dir_fd=directory)
            os.fsync(directory)
            return True

"""Bounded, retained Python seed evidence for risk-proof target eligibility.

No project module is imported or executed. Verification rederives targets from
retained source bytes, never from the current candidate. The caller authenticates
this artifact's complete content pin and captures it before Builder changes.
"""

from __future__ import annotations

import ast
import hashlib
import keyword
import os
import re
import stat
from pathlib import Path

VERSION = 1
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_FILES = 2000
MAX_SCAN_ENTRIES = 20000
_EXCLUDED = frozenset(
    {
        "node_modules",
        "vendor",
        "venv",
        "env",
        "__pycache__",
        "build",
        "dist",
        "target",
        "coverage",
        "htmlcov",
        "outputs",
    }
)
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
_PATH_PART = re.compile(r"[A-Za-z0-9_.-]+\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = frozenset({"initial_public_import", "initial_public_definition"})
MAX_MODULE_PARTS = 64


def _excluded(name):
    return name.startswith(".") or name in _EXCLUDED


def _path(value):
    if not isinstance(value, str) or not value or len(value) > 1024 or not value.endswith(".py"):
        raise ValueError("Retained Python path must be a bounded relative .py path")
    parts = value.split("/")
    if any(not _PATH_PART.fullmatch(part) or part in (".", "..") or _excluded(part) for part in parts):
        raise ValueError("Retained Python path is unsafe or outside the source inventory")
    return value


def _identifier(value):
    return bool(_IDENTIFIER.fullmatch(value)) and not keyword.iskeyword(value)


def _module(path):
    parts = path.split("/")
    if parts[0] == "src":
        parts = parts[1:]
    parts[-1:] = [parts[-1][:-3]] if parts else []
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return (
        ".".join(parts)
        if parts and len(parts) <= MAX_MODULE_PARTS and all(_identifier(part) for part in parts)
        else None
    )


def _test(path):
    parts = path.split("/")
    stem = parts[-1][:-3].lower()
    return (
        any(part.lower() in ("test", "tests") for part in parts[:-1])
        or stem in ("test", "tests", "conftest")
        or stem.startswith("test_")
        or stem.endswith("_test")
    )


def _public_module(path):
    return None if _test(path) else _module(path)


def _derive(files):
    modules, trees = {}, {}
    for record in files:
        path = record["path"]
        module = _public_module(path)
        if module:
            if module in modules:
                raise ValueError("Initial public module has ambiguous root/src paths: " + module)
            modules[module] = record
        try:
            trees[path] = ast.parse(record["text"], filename=path)
        except (SyntaxError, ValueError, RecursionError) as error:
            raise ValueError("Retained Python source cannot be parsed: " + path) from error
    selected = {}

    def imported(module):
        if (
            not module
            or len(module) > 1024
            or len(module.split(".")) > MAX_MODULE_PARTS
            or not all(_identifier(part) for part in module.split("."))
        ):
            return
        parts = module.split(".")
        for count in range(1, len(parts) + 1):
            candidate = ".".join(parts[:count])
            if candidate in modules:
                selected.setdefault(candidate, "initial_public_import")

    for record in files:
        path, tree = record["path"], trees[record["path"]]
        module = _public_module(path)
        if module and any(isinstance(node, ast.ClassDef) and _identifier(node.name) for node in tree.body):
            selected[module] = "initial_public_definition"
        if not module and not _test(path):
            continue
        context = _module(path)
        package = (context if path.endswith("/__init__.py") else context.rpartition(".")[0]) if context else ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported(alias.name)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".") if package else []
                    if node.level > len(parts):
                        continue
                    base = ".".join(parts[: len(parts) - node.level + 1] + ([base] if base else []))
                imported(base)
                for alias in node.names:
                    if alias.name != "*":
                        imported(".".join(part for part in (base, alias.name) if part))
    return [
        {"module": module, "path": modules[module]["path"], "kind": kind, "sha256": modules[module]["sha256"]}
        for module, kind in sorted(selected.items())
    ]


def _read_file(directory, name):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
            raise ValueError("Initial Python source must be a bounded regular file")
        data = bytearray()
        while len(data) <= MAX_FILE_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_FILE_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("Initial Python source exceeds the per-file byte limit")
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("Initial Python source changed while being captured")
        return bytes(data)
    finally:
        os.close(descriptor)


def capture(workspace):
    """Read a bounded initial public-module/import inventory without execution."""
    if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")):
        raise ValueError("Host lacks the no-follow file/directory capture primitives")
    files, total, scanned = [], 0, 0

    def walk(directory, prefix=""):
        nonlocal total, scanned
        entries = []
        with os.scandir(directory) as iterator:
            for entry in iterator:
                scanned += 1
                if scanned > MAX_SCAN_ENTRIES:
                    raise ValueError("Initial source scan exceeds its entry limit")
                entries.append(entry)
        for entry in sorted(entries, key=lambda item: item.name):
            if _excluded(entry.name):
                continue
            path = prefix + entry.name
            if entry.is_symlink():
                raise ValueError("Initial source inventory cannot capture symlinks: " + path)
            if entry.is_dir(follow_symlinks=False):
                descriptor = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    walk(descriptor, path + "/")
                finally:
                    os.close(descriptor)
            elif path.endswith(".py"):
                if not entry.is_file(follow_symlinks=False):
                    raise ValueError("Initial Python source must be a regular file: " + path)
                _path(path)
                if len(files) >= MAX_FILES:
                    raise ValueError("Initial Python inventory exceeds its file limit")
                data = _read_file(directory, entry.name)
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("Initial Python inventory exceeds its total byte limit")
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ValueError("Initial Python source must be UTF-8: " + path) from error
                files.append({"path": path, "sha256": hashlib.sha256(data).hexdigest(), "text": text})

    descriptor = None
    try:
        descriptor = os.open(Path(workspace), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        walk(descriptor)
    except (OSError, RecursionError) as error:
        raise ValueError("Initial Python inventory could not be captured safely") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    files.sort(key=lambda record: record["path"])
    return {"version": VERSION, "targets": _derive(files), "files": files}


def verify(artifact):
    """Canonicalize and verify retained evidence; the caller checks its content pin."""
    if (
        not isinstance(artifact, dict)
        or set(artifact) != {"version", "targets", "files"}
        or type(artifact["version"]) is not int
        or artifact["version"] != VERSION
    ):
        raise ValueError("Initial target artifact has an unsupported shape/version")
    if not isinstance(artifact["files"], list) or len(artifact["files"]) > MAX_FILES:
        raise ValueError("Retained Python inventory exceeds its file limit")
    files, seen, total = [], set(), 0
    for record in artifact["files"]:
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "text"}:
            raise ValueError("Retained Python file has an unsupported shape")
        path = _path(record["path"])
        if path in seen:
            raise ValueError("Retained Python path is duplicated")
        seen.add(path)
        if not isinstance(record["text"], str):
            raise ValueError("Retained Python text must be a string")
        try:
            data = record["text"].encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("Retained Python text must be UTF-8") from error
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("Retained Python evidence exceeds its byte limit")
        if (
            not isinstance(record["sha256"], str)
            or not _SHA256.fullmatch(record["sha256"])
            or hashlib.sha256(data).hexdigest() != record["sha256"]
        ):
            raise ValueError("Retained Python source hash does not match its bytes")
        files.append(dict(record))
    files.sort(key=lambda record: record["path"])
    expected = _derive(files)
    if not isinstance(artifact["targets"], list) or len(artifact["targets"]) > MAX_FILES:
        raise ValueError("Initial public target list exceeds its limit")
    targets, seen = [], set()
    for record in artifact["targets"]:
        if not isinstance(record, dict) or set(record) != {"module", "path", "kind", "sha256"}:
            raise ValueError("Initial public target has an unsupported shape")
        module = record["module"]
        if (
            not isinstance(module, str)
            or not module
            or len(module) > 1024
            or len(module.split(".")) > MAX_MODULE_PARTS
            or not all(_identifier(part) for part in module.split("."))
            or module in seen
        ):
            raise ValueError("Initial target module is unsafe or duplicated")
        seen.add(module)
        _path(record["path"])
        if not isinstance(record["kind"], str) or record["kind"] not in _KINDS:
            raise ValueError("Initial public target has an unsupported kind")
        if not isinstance(record["sha256"], str) or not _SHA256.fullmatch(record["sha256"]):
            raise ValueError("Initial target hash must be a SHA-256 content pin")
        targets.append(dict(record))
    targets.sort(key=lambda record: record["module"])
    if targets != expected:
        raise ValueError("Initial public targets do not match retained import/definition evidence")
    return {"version": VERSION, "targets": expected, "files": files}

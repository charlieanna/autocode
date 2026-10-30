"""Shared, explicitly authorized source roots for cross-repository discovery.

This catalog contains repository pointers only. It does not clone, edit or run
code in discovered repositories. The owning coordination service pins selected
identities and revisions before it can create task worktrees.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

CATALOG_NAME = "source-catalog.json"
CATALOG_VERSION = 1
MAX_DEPTH = 8
DIRECTORY_BUDGET = 20_000
EXCLUDED_DIRECTORIES = {".git", ".autocode", ".autocode-ui", "node_modules", "vendor", ".venv", "venv"}


class CatalogError(ValueError):
    """Invalid or unavailable source catalog input."""


def storage_root() -> Path:
    """Resolve the selected shared source catalog, independent of cwd."""
    selected = os.environ.get("AUTOCODE_SOURCE_CATALOG_HOME") or os.environ.get("AUTOCODE_HOME")
    if selected is None:
        selected = str(Path.home() / ".autocode")
    return Path(selected).expanduser().resolve()


def catalog_path() -> Path:
    return storage_root() / CATALOG_NAME


def _empty() -> dict[str, Any]:
    return {"version": CATALOG_VERSION, "roots": [], "repositories": []}


def _read() -> dict[str, Any]:
    path = catalog_path()
    if not path.exists():
        return _empty()
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CatalogError(f"Source catalog cannot be read: {error}") from error
    if (
        not isinstance(value, dict)
        or value.get("version") != CATALOG_VERSION
        or not isinstance(value.get("roots"), list)
        or not isinstance(value.get("repositories"), list)
    ):
        raise CatalogError("Source catalog has an unsupported version or shape")
    return value


@contextlib.contextmanager
def _locked():
    root = storage_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        handle = (root / "source-catalog.lock").open("a+")
    except OSError as error:
        raise CatalogError(f"Source catalog storage is unavailable: {error}") from error
    try:
        deadline = time.monotonic() + 2
        while True:
            try:
                util.lock_file(handle)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CatalogError("Source catalog is busy; inspect it and retry") from None
                time.sleep(0.05)
        try:
            yield catalog_path()
        finally:
            util.unlock_file(handle)
    finally:
        handle.close()


def _save(value: dict[str, Any]) -> None:
    util.atomic_json(catalog_path(), value)


def _canonical_directory(path: str | Path) -> Path:
    try:
        result = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise CatalogError(f"Source location is unavailable: {error}") from error
    if not result.is_dir():
        raise CatalogError(f"Source location is not a directory: {result}")
    return result


def add_root(path: str | Path) -> dict[str, Any]:
    """Authorize recursive, bounded local discovery beneath one canonical root."""
    root = _canonical_directory(path)
    with _locked():
        document = _read()
        paths = {row.get("path") for row in document["roots"] if isinstance(row, dict)}
        if str(root) not in paths:
            document["roots"].append({"path": str(root)})
            document["roots"].sort(key=lambda row: row["path"])
            _save(document)
    return {"operation": "roots-add", "path": str(root), "already_registered": str(root) in paths}


def remove_root(path: str | Path) -> dict[str, Any]:
    """Remove one previously authorized recursive source root."""
    # Removal must remain possible after a configured volume or directory has
    # disappeared. Resolve non-strictly so existing parent symlinks still
    # identify the same canonical entry recorded by add_root.
    try:
        root = Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise CatalogError(f"Source location cannot be resolved: {error}") from error
    with _locked():
        document = _read()
        before = len(document["roots"])
        document["roots"] = [row for row in document["roots"] if row.get("path") != str(root)]
        if len(document["roots"]) != before:
            _save(document)
    return {"operation": "roots-remove", "path": str(root), "removed": before != len(document["roots"])}


def list_roots() -> dict[str, Any]:
    """List authorized roots and standalone repository entries."""
    document = _read()
    return {
        "operation": "roots-list",
        "catalog": str(catalog_path()),
        "roots": list(document["roots"]),
        "repositories": list(document["repositories"]),
    }


def _git(path: Path, *args: str) -> str:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        ["git", "-C", str(path), *args], env=environment, capture_output=True, text=True, timeout=30
    )
    if result.returncode:
        raise CatalogError(result.stderr.strip()[-1000:] or "Git repository inspection failed")
    return result.stdout.strip()


def add_repository(path: str | Path) -> dict[str, Any]:
    """Authorize one committed repository outside recursive source roots."""
    root = _canonical_directory(path)
    try:
        git_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
        head = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
        common = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    except (OSError, subprocess.SubprocessError, CatalogError) as error:
        raise CatalogError(f"Standalone source must be a committed Git repository: {error}") from error
    if root != git_root:
        raise CatalogError(f"Standalone source must be the Git repository root: {git_root}")
    row = {"path": str(root), "git_common_dir": str(common), "head": head}
    with _locked():
        document = _read()
        known = {item.get("git_common_dir") for item in document["repositories"] if isinstance(item, dict)}
        if str(common) not in known:
            document["repositories"].append(row)
            document["repositories"].sort(key=lambda item: item["path"])
            _save(document)
    return {"operation": "repository-add", **row, "already_registered": str(common) in known}


def remove_repository(path: str | Path) -> dict[str, Any]:
    """Remove one explicitly registered standalone repository."""
    root = _canonical_directory(path)
    try:
        common = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    except (OSError, subprocess.SubprocessError, CatalogError) as error:
        raise CatalogError(f"Standalone source is not a readable Git repository: {error}") from error
    with _locked():
        document = _read()
        before = len(document["repositories"])
        document["repositories"] = [
            row for row in document["repositories"] if row.get("git_common_dir") != str(common)
        ]
        removed = before != len(document["repositories"])
        if removed:
            _save(document)
    return {"operation": "repository-remove", "git_common_dir": str(common), "removed": removed}


def _remote(path: Path) -> str | None:
    try:
        value = _git(path, "remote", "get-url", "origin")
    except (OSError, subprocess.SubprocessError, CatalogError):
        return None
    return _normalize_remote(value)


def _normalize_remote(value: str) -> str | None:
    """Normalize remote URL host case while preserving its transport and path."""
    value = value.strip()
    if not value:
        return None
    if "://" in value:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.hostname:
            return value.removesuffix(".git") or None
        try:
            port = f":{parsed.port}" if parsed.port else ""
        except ValueError:
            return value.removesuffix(".git") or None
        auth = f"{parsed.username}@" if parsed.username else ""
        netloc = auth + parsed.hostname.casefold() + port
        path = parsed.path.removesuffix(".git").rstrip("/")
        return urlunsplit((parsed.scheme.casefold(), netloc, path, "", "")) or None
    # Git's scp-like SSH form: [user@]host:path
    if "@" in value and ":" in value and not value.startswith(("/", "./", "../")):
        user, rest = value.split("@", 1)
        host, path = rest.split(":", 1)
        return f"{user}@{host.casefold()}:{path.removesuffix('.git').strip('/')}"
    return value.removesuffix(".git") or None


def _remote_identity(value: str | None) -> str | None:
    """Identify equivalent HTTPS and SSH remotes without folding path case."""
    if not value:
        return None
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.hostname:
            try:
                port = f":{parsed.port}" if parsed.port else ""
            except ValueError:
                return value.casefold()
            return parsed.hostname.casefold() + port + parsed.path
    if "@" in value and ":" in value and not value.startswith(("/", "./", "../")):
        _, rest = value.split("@", 1)
        host, path = rest.split(":", 1)
        return host.casefold() + "/" + path.strip("/")
    return value


def _candidate(path: Path) -> dict[str, Any]:
    top = Path(_git(path, "rev-parse", "--show-toplevel")).resolve()
    common = Path(_git(top, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    head = _git(top, "rev-parse", "--verify", "HEAD^{commit}")
    tree = _git(top, "rev-parse", "HEAD^{tree}")
    remote = _remote(top)
    return {
        "path": str(top),
        "git_common_dir": str(common),
        "head": head,
        "tree": tree,
        "remote": remote,
        "remote_identity": _remote_identity(remote),
    }


def discover(*, max_depth: int = MAX_DEPTH, directory_budget: int = DIRECTORY_BUDGET) -> dict[str, Any]:
    """Find repositories below configured roots without following links or changing cwd."""
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0:
        raise CatalogError("Maximum discovery depth must be a nonnegative integer")
    if isinstance(directory_budget, bool) or not isinstance(directory_budget, int) or directory_budget < 1:
        raise CatalogError("Discovery directory budget must be a positive integer")
    document = _read()
    catalog_identity = _catalog_identity(document)
    roots = [Path(row["path"]) for row in document["roots"]]
    standalone = [Path(row["path"]) for row in document["repositories"]]
    result: dict[str, Any] = {
        "catalog": str(catalog_path()), "catalog_identity": catalog_identity,
        "catalog_roots": list(document["roots"]), "registered_repositories": list(document["repositories"]),
        "complete": True, "directories_inspected": 0,
        "repositories": [], "diagnostics": [],
    }
    found: dict[str, dict[str, Any]] = {}
    seen: set[Path] = set()
    queue: list[tuple[Path, Path, int]] = []
    for root in roots:
        try:
            canonical = root.resolve(strict=True)
            if not canonical.is_dir():
                raise OSError("not a directory")
            queue.append((canonical, canonical, 0))
        except (OSError, RuntimeError) as error:
            result["complete"] = False
            result["diagnostics"].append({"code": "root_unavailable", "path": str(root), "message": str(error)})
    while queue:
        directory, boundary, depth = queue.pop(0)
        if directory in seen:
            continue
        if result["directories_inspected"] >= directory_budget:
            result["complete"] = False
            result["diagnostics"].append({"code": "budget_exhausted", "path": str(directory)})
            break
        seen.add(directory)
        result["directories_inspected"] += 1
        marker = directory / ".git"
        if marker.exists():
            try:
                row = _candidate(directory)
                found[row["git_common_dir"] + "\0" + (row["remote_identity"] or "") + "\0" + row["tree"]] = row
            except (OSError, subprocess.SubprocessError, CatalogError) as error:
                result["complete"] = False
                result["diagnostics"].append({"code": "repository_unreadable", "path": str(directory), "message": str(error)})
            continue
        if depth >= max_depth:
            try:
                has_unvisited_directory = any(
                    child.name not in EXCLUDED_DIRECTORIES and (child.is_symlink() or child.is_dir())
                    for child in directory.iterdir()
                )
            except OSError as error:
                result["complete"] = False
                result["diagnostics"].append({"code": "inaccessible", "path": str(directory), "message": str(error)})
                continue
            if has_unvisited_directory:
                result["complete"] = False
                result["diagnostics"].append({"code": "depth_limit", "path": str(directory), "depth": depth})
            continue
        try:
            children = sorted(directory.iterdir(), key=lambda child: child.name.casefold())
        except OSError as error:
            result["complete"] = False
            result["diagnostics"].append({"code": "inaccessible", "path": str(directory), "message": str(error)})
            continue
        for child in children:
            if child.name in EXCLUDED_DIRECTORIES:
                continue
            try:
                if child.is_symlink():
                    target = child.resolve(strict=True)
                    if not target.is_relative_to(boundary):
                        result["complete"] = False
                        result["diagnostics"].append({"code": "symlink_escape", "path": str(child)})
                    continue
                if child.is_dir():
                    queue.append((child.resolve(strict=True), boundary, depth + 1))
            except (OSError, RuntimeError) as error:
                result["complete"] = False
                result["diagnostics"].append({"code": "inaccessible", "path": str(child), "message": str(error)})
    for path in standalone:
        try:
            row = _candidate(path)
            found[row["git_common_dir"] + "\0" + (row["remote_identity"] or "") + "\0" + row["tree"]] = row
        except (OSError, subprocess.SubprocessError, CatalogError) as error:
            result["complete"] = False
            result["diagnostics"].append({"code": "repository_unreadable", "path": str(path), "message": str(error)})

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in found.values():
        # Linked worktrees share an identity. Separate clones with one remote
        # stay together so callers can detect incompatible baselines.
        identity = "common:" + row["git_common_dir"] if row["remote_identity"] is None else "remote:" + row["remote_identity"]
        groups.setdefault(identity, []).append(row)
    for identity, candidates in sorted(groups.items()):
        by_tree: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            by_tree.setdefault(candidate["tree"], candidate)
        versions = sorted(by_tree.values(), key=lambda row: (row["path"], row["head"]))
        result["repositories"].append({
            "identity": identity,
            "remote": versions[0]["remote"],
            "remote_identity": versions[0]["remote_identity"],
            "path": versions[0]["path"],
            "ambiguous_baseline": len(versions) > 1,
            "candidates": versions,
        })
    result["repositories"].sort(key=lambda row: (row["remote"] or "", row["path"]))
    result["source_snapshot"] = _digest({
        "catalog_identity": catalog_identity,
        "complete": result["complete"],
        "repositories": result["repositories"],
        "diagnostics": result["diagnostics"],
    })
    return result


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    import hashlib

    return hashlib.sha256(encoded).hexdigest()


def _catalog_identity(document: dict[str, Any]) -> str:
    return _digest({"catalog": str(catalog_path()), "document": document})


def cli(argv: list[str]) -> int:
    import argparse

    if argv and argv[0] == "repositories":
        return repository_cli(argv[1:])
    parser = argparse.ArgumentParser(prog="autocode registry roots")
    commands = parser.add_subparsers(dest="operation", required=True)
    add = commands.add_parser("add")
    add.add_argument("path", type=Path)
    add.add_argument("--json", action="store_true")
    remove = commands.add_parser("remove")
    remove.add_argument("path", type=Path)
    remove.add_argument("--json", action="store_true")
    listing = commands.add_parser("list")
    listing.add_argument("--json", action="store_true")
    find = commands.add_parser("discover")
    find.add_argument("--max-depth", type=int, default=MAX_DEPTH)
    find.add_argument("--directory-budget", type=int, default=DIRECTORY_BUDGET)
    find.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.operation == "add":
            value = add_root(args.path)
        elif args.operation == "remove":
            value = remove_root(args.path)
        elif args.operation == "discover":
            value = discover(max_depth=args.max_depth, directory_budget=args.directory_budget)
        else:
            value = list_roots()
    except CatalogError as error:
        print(json.dumps({"error": {"code": "source_catalog_error", "message": str(error)}}, indent=2))
        return 2
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0 if value.get("complete", True) else 1


def repository_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="autocode registry repositories")
    commands = parser.add_subparsers(dest="operation", required=True)
    for operation in ("add", "remove"):
        command = commands.add_parser(operation)
        command.add_argument("path", type=Path)
        command.add_argument("--json", action="store_true")
    listing = commands.add_parser("list")
    listing.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.operation == "add":
            value = add_repository(args.path)
        elif args.operation == "remove":
            value = remove_repository(args.path)
        else:
            value = list_roots()
    except CatalogError as error:
        print(json.dumps({"error": {"code": "source_catalog_error", "message": str(error)}}, indent=2))
        return 2
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0

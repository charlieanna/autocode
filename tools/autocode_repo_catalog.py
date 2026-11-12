"""Shared, explicitly authorized source roots for cross-repository discovery.

This catalog contains repository pointers only. It does not edit or run code in
discovered repositories. The person-confirmed `registry remotes clone` command
brings one operator-allowlisted GitHub repository into the managed `clones/`
folder beneath the catalog storage root. The owning coordination service pins
selected identities and revisions before it can create task worktrees.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
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
# Managed clones of operator-allowlisted GitHub remotes live under this folder
# in the catalog storage root; only `registry remotes clone` creates one.
MANAGED_CLONES = "clones"
# A clone is a bounded network operation, unlike the local inspection below.
CLONE_TIMEOUT = 600
_OWNER_NAME_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")
DIRECTORY_BUDGET = 20_000
EXCLUDED_DIRECTORIES = {".git", ".autocode", ".autocode-ui", "node_modules", "vendor", ".venv", "venv"}
# A checkout or directory below a configured root that cannot be read holds
# nothing a stage could inspect or cite: read authority covers only identified
# checkouts. Intake skips such an entry and records it. A missing configured
# root, the directory budget or a link out of a root can hide readable sources,
# and stays blocking.
UNREADABLE = frozenset({"repository_unreadable", "inaccessible"})
# A directory at the depth bound that still has subdirectories was searched to
# the bound and no further. Everything shallower was inventoried, so intake uses
# the inventory and records what it did not search, rather than asking the
# person to restructure their folders: a checkout nested more than MAX_DEPTH
# levels below a root is registered on its own (`registry repositories add`).
NOT_SEARCHED = frozenset({"depth_limit"})


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
    return {"version": CATALOG_VERSION, "roots": [], "repositories": [], "remotes": []}


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
        or ("remotes" in value and not isinstance(value.get("remotes"), list))
    ):
        raise CatalogError("Source catalog has an unsupported version or shape")
    # A catalog saved before the remote allowlist existed has no `remotes` key;
    # it loads as an empty allowlist and keeps every existing command working.
    value.setdefault("remotes", [])
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


def _git_environment() -> dict[str, str]:
    """Git's own environment: no inherited GIT_* redirection, no terminal prompts.

    Credentials come from Git's own credential helper or gh; this catalog never
    stores or passes a token.
    """
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return environment


def _git(path: Path, *args: str, timeout: float = 30) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args], env=_git_environment(), capture_output=True, text=True, timeout=timeout
    )
    if result.returncode:
        raise CatalogError(result.stderr.strip()[-1000:] or "Git repository inspection failed")
    return result.stdout.strip()


def _repository_row(path: str | Path) -> dict[str, Any]:
    """The standalone-repository record for one committed Git repository root."""
    root = _canonical_directory(path)
    try:
        git_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
        head = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
        common = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    except (OSError, subprocess.SubprocessError, CatalogError) as error:
        raise CatalogError(f"Standalone source must be a committed Git repository: {error}") from error
    if root != git_root:
        raise CatalogError(f"Standalone source must be the Git repository root: {git_root}")
    return {"path": str(root), "git_common_dir": str(common), "head": head}


def _insert_repository(document: dict[str, Any], row: dict[str, Any]) -> bool:
    known = {item.get("git_common_dir") for item in document["repositories"] if isinstance(item, dict)}
    if row["git_common_dir"] in known:
        return False
    document["repositories"].append(row)
    document["repositories"].sort(key=lambda item: item["path"])
    return True


def add_repository(path: str | Path) -> dict[str, Any]:
    """Authorize one committed repository outside recursive source roots."""
    row = _repository_row(path)
    with _locked():
        document = _read()
        inserted = _insert_repository(document, row)
        if inserted:
            _save(document)
    return {"operation": "repository-add", **row, "already_registered": not inserted}


def remove_repository(path: str | Path) -> dict[str, Any]:
    """Remove one explicitly registered standalone repository."""
    # As with remove_root, removal must remain possible after the checkout has disappeared: a dead entry
    # leaves discovery incomplete for every chat (#3266). A checkout Git can still read is matched by its
    # Git identity; otherwise by the path add_repository recorded.
    try:
        root = Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise CatalogError(f"Source location cannot be resolved: {error}") from error
    common, unreadable = None, None
    if root.is_dir():
        try:
            common = str(Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve())
        except (OSError, subprocess.SubprocessError, CatalogError) as error:
            unreadable = error
    with _locked():
        document = _read()
        key, wanted = ("git_common_dir", common) if common is not None else ("path", str(root))
        dropped = [row for row in document["repositories"] if row.get(key) == wanted]
        if dropped:
            document["repositories"] = [row for row in document["repositories"] if row.get(key) != wanted]
            _save(document)
    if not dropped and unreadable is not None:
        raise CatalogError(f"Standalone source is not a readable Git repository: {unreadable}") from unreadable
    if common is None and dropped:
        common = dropped[0].get("git_common_dir")
    return {"operation": "repository-remove", "git_common_dir": common, "removed": bool(dropped)}


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
        "catalog": str(catalog_path()),
        "catalog_identity": catalog_identity,
        "catalog_roots": list(document["roots"]),
        "registered_repositories": list(document["repositories"]),
        "complete": True,
        "directories_inspected": 0,
        "repositories": [],
        "diagnostics": [],
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
                result["diagnostics"].append(
                    {"code": "repository_unreadable", "path": str(directory), "message": str(error)}
                )
            # A configured workspace can itself have Git metadata while holding
            # independent child checkouts. Inventory its children as well; ordinary
            # discovered repositories remain leaves of the bounded search.
            if directory != boundary:
                continue
        elif (directory / "pyvenv.cfg").is_file():
            # Identify generated environments by their marker, regardless of name.
            continue
        if depth >= max_depth:
            try:
                has_unvisited_directory = any(
                    child.name not in EXCLUDED_DIRECTORIES and not child.name.startswith(".") and child.is_dir()
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
            # Hidden directories below a root hold tool state (run copies,
            # caches, editor settings), not the person's checkouts.
            if child.name in EXCLUDED_DIRECTORIES or child.name.startswith("."):
                continue
            try:
                if child.is_symlink():
                    if not child.is_dir():
                        continue
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
        identity = (
            "common:" + row["git_common_dir"] if row["remote_identity"] is None else "remote:" + row["remote_identity"]
        )
        groups.setdefault(identity, []).append(row)
    for identity, candidates in sorted(groups.items()):
        by_tree: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            by_tree.setdefault(candidate["tree"], candidate)
        versions = sorted(by_tree.values(), key=lambda row: (row["path"], row["head"]))
        result["repositories"].append(
            {
                "identity": identity,
                "remote": versions[0]["remote"],
                "remote_identity": versions[0]["remote_identity"],
                "path": versions[0]["path"],
                "ambiguous_baseline": len(versions) > 1,
                "candidates": versions,
            }
        )
    result["repositories"].sort(key=lambda row: (row["remote"] or "", row["path"]))
    result["source_snapshot"] = _digest(
        {
            "catalog_identity": catalog_identity,
            "complete": result["complete"],
            "repositories": result["repositories"],
            "diagnostics": result["diagnostics"],
        }
    )
    return result


def blocking(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    """Diagnostics that leave readable sources unseen; [] when intake can use the inventory."""
    if discovery["complete"]:
        return []
    rows = [row for row in discovery["diagnostics"] if row.get("code") not in UNREADABLE | NOT_SEARCHED]
    return rows or ([] if discovery["diagnostics"] else [{"code": "incomplete"}])


def unreadable(discovery: dict[str, Any]) -> list[dict[str, str]]:
    """The entries discovery skipped because they cannot be read, by code and path."""
    return [
        {"code": row["code"], "path": row["path"]} for row in discovery["diagnostics"] if row.get("code") in UNREADABLE
    ]


def not_searched(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    """The directories discovery reached at its depth bound without searching below them."""
    return [
        {"code": row["code"], "path": row["path"], "depth": row["depth"]}
        for row in discovery["diagnostics"]
        if row.get("code") in NOT_SEARCHED
    ]


def _owner_name(value: str) -> str:
    """One GitHub OWNER/NAME pair; nothing else may name a remote or a managed clone."""
    parts = value.split("/") if isinstance(value, str) else []
    if len(parts) != 2 or any(part in ("", ".", "..") or not _OWNER_NAME_SEGMENT.fullmatch(part) for part in parts):
        raise CatalogError(f"Remote must be one OWNER/NAME pair of letters, digits, '.', '_' and '-': {value!r}")
    return value


def _github_url(name: str) -> str:
    return f"https://github.com/{name}.git"


def _remote_entry(document: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((row for row in document["remotes"] if isinstance(row, dict) and row.get("name") == name), None)


def _remote_row(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    path = entry.get("path") or None
    pinned = entry.get("pinned_commit") or None
    return {
        "name": name,
        "url": _github_url(name),
        "ref": entry.get("ref"),
        "cloned": bool(path and pinned),
        "path": path,
        "pinned_commit": pinned,
    }


def add_remote(name: str, ref: str) -> dict[str, Any]:
    """List one GitHub repository on the operator-approved remote allowlist.

    Re-adding an already-listed OWNER/NAME replaces its stored ref; only these
    `registry remotes` commands change the allowlist.
    """
    selected = _owner_name(name)
    if not isinstance(ref, str) or not ref.strip():
        raise CatalogError("Remote ref is required")
    with _locked():
        document = _read()
        entries = document["remotes"]
        row = _remote_entry(document, selected)
        already_registered = row is not None
        if row is None:
            row = {"name": selected}
            entries.append(row)
        row["ref"] = ref
        entries.sort(key=lambda item: str(item.get("name")) if isinstance(item, dict) else "")
        _save(document)
    return {
        "operation": "remotes-add",
        "name": selected,
        "url": _github_url(selected),
        "ref": ref,
        "already_registered": already_registered,
    }


def remove_remote(name: str) -> dict[str, Any]:
    """Drop only the allowlist entry; a managed clone stays on disk and registered."""
    with _locked():
        document = _read()
        before = len(document["remotes"])
        document["remotes"] = [
            row for row in document["remotes"] if not isinstance(row, dict) or row.get("name") != name
        ]
        removed = len(document["remotes"]) != before
        if removed:
            _save(document)
    return {"operation": "remotes-remove", "name": name, "removed": removed}


def list_remotes() -> dict[str, Any]:
    """List the operator-approved remote allowlist and what is already cloned."""
    document = _read()
    # A row without a name names no repository: leave that row out, never the whole list.
    entries: list[tuple[str, dict[str, Any]]] = []
    for row in document["remotes"]:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if not isinstance(name, str):
            continue
        entries.append((name, row))
    entries.sort(key=lambda named: named[0])
    return {
        "operation": "remotes-list",
        "catalog": str(catalog_path()),
        "remotes": [_remote_row(name, row) for name, row in entries],
    }


def _verifiable_managed_clone(path: Path, url: str) -> bool:
    """A managed folder the person already made: a Git repository whose origin is the allowlisted URL."""
    try:
        top = Path(_git(path, "rev-parse", "--show-toplevel")).resolve()
    except (OSError, subprocess.SubprocessError, CatalogError):
        return False
    return top == path.resolve() and _remote(top) == _normalize_remote(url)


def _git_clone(url: str, destination: Path, ref: str) -> None:
    try:
        result = subprocess.run(
            ["git", "clone", "--branch", ref, "--single-branch", "--", url, str(destination)],
            env=_git_environment(),
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CatalogError(f"Git clone failed: {error}") from error
    if result.returncode:
        raise CatalogError(result.stderr.strip()[-1000:] or "Git clone failed")


def _record_managed_clone(selected: str, row: dict[str, Any], ref: Any) -> dict[str, Any]:
    """Register the managed clone as a standalone repository and pin its resolved commit."""
    with _locked():
        document = _read()
        entry = _remote_entry(document, selected)
        if entry is None:
            raise CatalogError(f"Remote {selected} is not on the operator-approved allowlist")
        _insert_repository(document, row)
        entry["path"] = row["path"]
        entry["pinned_commit"] = row["head"]
        _save(document)
    return {
        "operation": "remotes-clone",
        "name": selected,
        "url": _github_url(selected),
        "ref": ref,
        "path": row["path"],
        "pinned_commit": row["head"],
    }


def clone_remote(name: str) -> dict[str, Any]:
    """The person's confirmation to bring one allowlisted GitHub repository onto disk."""
    selected = _owner_name(name)
    url = _github_url(selected)
    entry = _remote_entry(_read(), selected)
    if entry is None:
        raise CatalogError(f"Remote {selected} is not on the operator-approved allowlist")
    ref = entry.get("ref")
    # Nothing to clone with: say so rather than hand `git clone --branch` a non-branch.
    if not isinstance(ref, str) or not ref.strip():
        raise CatalogError(f"Remote {selected} has no stored ref to clone")
    target = storage_root() / MANAGED_CLONES / Path(*selected.split("/"))
    if target.is_dir():
        if entry.get("path") and entry.get("pinned_commit"):
            return {
                "operation": "remotes-clone",
                "name": selected,
                "url": url,
                "ref": ref,
                "already_cloned": True,
                "path": entry.get("path"),
                "pinned_commit": entry.get("pinned_commit"),
            }
        if not _verifiable_managed_clone(target, url):
            raise CatalogError(f"Managed folder {target} already exists and is not a verifiable clone of {url}")
        return {"already_cloned": True, **_record_managed_clone(selected, _repository_row(target), ref)}
    root = storage_root()
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="remote-clone-", dir=root))
    moved = False
    try:
        try:
            _git_clone(url, staging, ref)
            target.parent.mkdir(parents=True, exist_ok=True)
            # A rename, never a move: a managed folder another clone finished meanwhile is refused
            # here, where a move would nest this clone inside it and the cleanup below would delete it.
            os.rename(staging, target)
        except (OSError, subprocess.SubprocessError) as error:
            raise CatalogError(f"Git clone failed: {error}") from error
        moved = True
        report = _record_managed_clone(selected, _repository_row(target), ref)
    except BaseException:
        # Cleanup removes only directories this attempt created, never a
        # managed folder that already existed.
        shutil.rmtree(target if moved else staging, ignore_errors=True)
        raise
    return {"already_cloned": False, **report}


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
    if argv and argv[0] == "remotes":
        return remote_cli(argv[1:])
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


def remote_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="autocode registry remotes")
    commands = parser.add_subparsers(dest="operation", required=True)
    add = commands.add_parser("add", help="List one GitHub repository on the operator-approved allowlist")
    add.add_argument("name", help="The GitHub OWNER/NAME pair")
    add.add_argument("--ref", required=True, help="The branch or ref a later clone checks out")
    remove = commands.add_parser("remove", help="Drop only the allowlist entry; a managed clone stays on disk")
    remove.add_argument("name", help="The GitHub OWNER/NAME pair")
    listing = commands.add_parser("list", help="List the allowlist and what is already cloned")
    listing.add_argument("--json", action="store_true")
    clone = commands.add_parser("clone", help="The person's confirmation to clone one allowlisted repository")
    clone.add_argument("name", help="The GitHub OWNER/NAME pair")
    args = parser.parse_args(argv)
    try:
        if args.operation == "add":
            value = add_remote(args.name, args.ref)
        elif args.operation == "remove":
            value = remove_remote(args.name)
        elif args.operation == "clone":
            value = clone_remote(args.name)
        else:
            value = list_remotes()
    except CatalogError as error:
        print(json.dumps({"error": {"code": "source_catalog_error", "message": str(error)}}, indent=2))
        return 2
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0

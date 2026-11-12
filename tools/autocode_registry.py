"""Durable, minimal cross-workspace registry for Autocode runs.

The registry is an index of canonical pointers, never a second copy of run state.
Registry locking is independent of workspace ownership: callers acquire the registry
lock only while updating this file and never while a provider is running.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

try:
    from . import autocode_repo_catalog as source_catalog
    from . import autocode_util as util
except ImportError:
    import autocode_repo_catalog as source_catalog
    import autocode_util as util


REGISTRY_VERSION = 1
LOCK_TIMEOUT_SECONDS = 1.0
DEFAULT_IMPORT_MAX_DEPTH = 3
DEFAULT_IMPORT_DIRECTORY_BUDGET = 10_000
TITLE_LIMIT = 80  # a listed run's title: the first line of its task
UPDATED_AT = "%Y-%m-%dT%H:%M:%SZ"  # a listed run's last checkpoint write, in UTC


class RegistryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _registry_project(workspace: Path) -> Path:
    """The project that owns registry storage for ``workspace``.

    A default task worktree (``.autocode/task-workspace.json``) registers under its
    project so ``registry list`` and ``runs --all`` from that project see the lane
    (#2279). Invalid or foreign metadata keeps the workspace itself.
    """
    meta = workspace / ".autocode" / "task-workspace.json"
    if not meta.is_file() or meta.is_symlink():
        return workspace
    try:
        data = json.loads(meta.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return workspace
    if not isinstance(data, dict) or not isinstance(data.get("project_workspace"), str):
        return workspace
    if data.get("workspace") != str(workspace):
        return workspace
    project = Path(data["project_workspace"]).resolve()
    try:
        if not workspace.is_relative_to(project / ".autocode" / "worktrees"):
            return workspace
    except (OSError, ValueError):
        return workspace
    return project


def configure_workspace_storage(workspace: str | Path) -> None:
    """Use project-local registry storage unless a shared home was chosen.

    Task worktrees register through their ``project_workspace`` (#2279). An explicit
    ``AUTOCODE_HOME`` still wins.
    """
    root = _registry_project(Path(workspace).resolve())
    if os.environ.get("AUTOCODE_HOME") is None:
        os.environ["AUTOCODE_HOME"] = str(root / ".autocode" / "registry")
        remember_project(root)


PROJECT_INDEX = "projects.json"
PROJECT_INDEX_VERSION = 1


def project_index_path() -> Path:
    """The per-user list of projects whose runs index in their own registry (#3265).

    A run without ``AUTOCODE_HOME`` indexes in its project, so a listing from another folder could not
    find it. The index names those projects; it holds no run. ``AUTOCODE_PROJECT_INDEX_HOME`` selects its
    home for isolated installations and tests; otherwise it is ``~/.autocode``, whatever home a run uses.
    """
    selected = os.environ.get("AUTOCODE_PROJECT_INDEX_HOME")
    return Path(selected if selected else Path.home() / ".autocode").expanduser().resolve() / PROJECT_INDEX


def _read_project_index(path: Path) -> list[str]:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    rows = value.get("projects") if isinstance(value, dict) and value.get("version") == PROJECT_INDEX_VERSION else None
    return [row for row in rows if isinstance(row, str)] if isinstance(rows, list) else []


def _has_registry(project: str) -> bool:
    return (Path(project) / ".autocode" / "registry" / "registry.json").is_file()


def remember_project(root: Path) -> None:
    """Record a project whose runs index in its own registry, so a listing from anywhere finds them.

    Best effort: the index is how other folders find this project's chats, never a reason to stop a run.
    Projects whose registry is gone (a deleted folder) are dropped as the index is rewritten.
    """
    path = project_index_path()
    try:
        if str(root) in _read_project_index(path):
            return
        with _locked_registry_at(path.parent):
            kept = [row for row in _read_project_index(path) if row != str(root) and _has_registry(row)]
            util.atomic_json(path, {"version": PROJECT_INDEX_VERSION, "projects": sorted([*kept, str(root)])})
    except (OSError, RegistryError):
        return


def other_project_storage() -> list[Path]:
    """The registries of every project a task ran in, for a listing of all chats (#3265).

    None with an explicit ``AUTOCODE_HOME``, which selects exactly one registry, as in ``project_storage``.
    """
    if os.environ.get("AUTOCODE_HOME") is not None:
        return []
    roots = [Path(row) / ".autocode" / "registry" for row in _read_project_index(project_index_path())]
    return [root for root in roots if (root / "registry.json").is_file() and root != storage_root()]


def project_storage(start: str | Path) -> Path | None:
    """The project registry to read beside the shared home from ``start`` (#2279).

    Without ``AUTOCODE_HOME`` a run indexes in its project's registry while the read
    commands default to the per-user home, so a listing from inside a project reads
    both. An explicit ``AUTOCODE_HOME`` selects exactly one registry: None.
    """
    if os.environ.get("AUTOCODE_HOME") is not None:
        return None
    try:
        here = Path(start).resolve()
    except (OSError, RuntimeError):
        return None
    for directory in (here, *here.parents):
        if (directory / ".git").exists():
            root = _registry_project(directory) / ".autocode" / "registry"
            return None if root == storage_root() else root
    return None


def storage_root() -> Path:
    selected = os.environ.get("AUTOCODE_HOME")
    return Path(selected if selected is not None else Path.home() / ".autocode").expanduser().resolve()


def registry_path() -> Path:
    return storage_root() / "registry.json"


def _identity(prefix: str, path: Path) -> str:
    return f"{prefix}-{hashlib.sha256(str(path).encode()).hexdigest()[:24]}"


def _empty() -> dict[str, Any]:
    return {"version": REGISTRY_VERSION, "workspaces": {}, "runs": {}}


def _read_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty()
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryError("corrupt_registry", f"Registry cannot be read: {error}") from error
    if not isinstance(value, dict) or value.get("version") != REGISTRY_VERSION:
        raise RegistryError("unsupported_registry", "Registry has an unsupported version or shape")
    if not isinstance(value.get("workspaces"), dict) or not isinstance(value.get("runs"), dict):
        raise RegistryError("corrupt_registry", "Registry workspaces and runs must be objects")
    return value


@contextlib.contextmanager
def _locked_registry_at(root: Path) -> Any:
    root = Path(root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        handle = (root / "registry.lock").open("a+")
    except OSError as error:
        raise RegistryError("storage_unavailable", f"Registry storage is unavailable: {error}") from error
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                util.lock_file(handle)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RegistryError(
                        "lock_timeout", "Registry is busy; retry the same run after a short wait"
                    ) from None
                time.sleep(0.05)
        # Unlock only a lock this handle took: Windows refuses to unlock one it never held.
        try:
            yield root / "registry.json"
        finally:
            util.unlock_file(handle)
    finally:
        handle.close()


@contextlib.contextmanager
def _locked_registry() -> Any:
    with _locked_registry_at(storage_root()) as path:
        yield path


ACTIVE_STATUS = re.compile(r"^(RUNNING|BUILDING|WORKING|DISPATCHING|EXECUTING)", re.I)


def _pointer(workspace: str | Path, run_dir: str | Path) -> tuple[Path, Path]:
    """The canonical direct run a person named; never a link or a directory outside the store."""
    workspace, run_dir = Path(workspace), Path(run_dir)
    if not workspace.is_absolute() or not run_dir.is_absolute():
        raise RegistryError("invalid_run", "Use the canonical absolute workspace and run directories")
    try:
        canonical_workspace = workspace.resolve()
        canonical_run = run_dir.resolve()
    except (OSError, RuntimeError) as error:
        raise RegistryError("invalid_run", f"Run pointers cannot be resolved: {error}") from error
    if str(canonical_workspace) != str(workspace) or str(canonical_run) != str(run_dir):
        raise RegistryError("invalid_run", "Use the canonical workspace and run directories")
    if run_dir.parent != workspace / ".autocode" / "runs":
        raise RegistryError("invalid_run", "The run must be a direct child of the workspace .autocode/runs")
    return workspace, run_dir


def _registry_roots(workspace: Path) -> list[Path]:
    """The shared registry and, without an explicit home, the project's own (#2279)."""
    roots = [storage_root()]
    project = project_storage(workspace)
    if project is not None:
        roots.append(Path(project).resolve())
    unique: list[Path] = []
    for root in sorted(roots, key=str):
        if root not in unique:
            unique.append(root)
    return unique


def _matching_keys(document: dict[str, Any], workspace: Path, run_dir: Path) -> list[str]:
    return [
        key
        for key, value in document["runs"].items()
        if isinstance(value, dict) and value.get("workspace") == str(workspace) and value.get("run_dir") == str(run_dir)
    ]


def _update_run_records(workspace: Path, run_dir: Path, mutate) -> list[str]:
    """Apply one change to every registry that indexes this run; both are updated, never one side."""
    updated: list[str] = []
    for root in _registry_roots(workspace):
        path = root / "registry.json"
        if not path.is_file():
            continue
        with _locked_registry_at(root) as locked_path:
            document = _read_registry(locked_path)
            keys = _matching_keys(document, workspace, run_dir)
            if not keys:
                continue
            mutate(document, keys)
            try:
                util.atomic_json(locked_path, document)
            except OSError as error:
                raise RegistryError("write_failed", f"Registry update failed: {error}") from error
            updated.extend(keys)
    return updated


def _run_actions(record: dict[str, Any], availability: str, diagnostic: Any) -> list[dict[str, Any]]:
    """The named cleanup actions for one row; an active chat offers none.

    A stale pointer can be archived (it disappears from the list) but not deleted:
    deleting files is only offered where the canonical run directory is available.
    """
    workspace, run_dir = record.get("workspace"), record.get("run_dir")
    if availability in ("malformed_record", "inaccessible"):
        return []
    if not isinstance(workspace, str) or not isinstance(run_dir, str):
        return []
    status = diagnostic.get("status") if isinstance(diagnostic, dict) else None
    if isinstance(status, str) and ACTIVE_STATUS.match(status):
        return []
    archived = record.get("archived") is True
    actions = [
        {
            "id": "unarchive" if archived else "archive",
            "label": "Unarchive" if archived else "Archive",
            "next_command": [
                "registry",
                "unarchive" if archived else "archive",
                "--workspace",
                workspace,
                "--run-dir",
                run_dir,
            ],
        }
    ]
    if availability == "available":
        actions.append(
            {
                "id": "delete",
                "label": "Delete",
                "next_command": [
                    "registry",
                    "delete",
                    "--workspace",
                    workspace,
                    "--run-dir",
                    run_dir,
                    "--yes",
                ],
            }
        )
    return actions


def _validate_registration(workspace: Path, run_dir: Path, state: RunState) -> tuple[Path, Path]:
    workspace = Path(workspace).resolve()
    run_dir = Path(run_dir).resolve()
    runs_dir = (workspace / ".autocode" / "runs").resolve()
    if not workspace.is_dir() or not (workspace / ".git").exists():
        raise RegistryError("invalid_workspace", f"Workspace is not a Git repository: {workspace}")
    if not runs_dir.is_dir() or not runs_dir.is_relative_to(workspace):
        raise RegistryError("invalid_runs_directory", "Workspace .autocode/runs escapes its canonical workspace")
    if not run_dir.is_dir() or not run_dir.is_relative_to(runs_dir):
        raise RegistryError("invalid_run_directory", "Run directory must be contained by workspace .autocode/runs")
    state_path = run_dir / "state.json"
    if state_path.is_symlink() or not state_path.is_file() or state_path.resolve().parent != run_dir:
        raise RegistryError(
            "invalid_checkpoint", "Run state.json must be a regular file directly inside the run directory"
        )
    if state.get("workspace") != str(workspace):
        raise RegistryError(
            "checkpoint_workspace_mismatch", "Checkpoint workspace does not match the canonical workspace"
        )
    return workspace, run_dir


def _run_task_id(state: RunState) -> str | None:
    """The run-level ID is stable across implementation task assignments."""
    value = state.get("task_id")
    return value if isinstance(value, str) else None


def register_run(workspace: Path, run_dir: Path, state: RunState) -> dict[str, str | None]:
    workspace, run_dir = _validate_registration(workspace, run_dir, state)
    workspace_id = _identity("workspace", workspace)
    run_id = _identity("run", run_dir)
    task_id = _run_task_id(state)
    with _locked_registry() as path:
        document = _read_registry(path)
        document["workspaces"][workspace_id] = {"id": workspace_id, "workspace": str(workspace)}
        document["runs"][run_id] = {
            "id": run_id,
            "workspace_id": workspace_id,
            "workspace": str(workspace),
            "run_dir": str(run_dir),
            "task_id": task_id,
        }
        try:
            util.atomic_json(path, document)
        except OSError as error:
            raise RegistryError("write_failed", f"Registry update failed: {error}") from error
    return {"workspace_id": workspace_id, "run_id": run_id, "task_id": task_id}


def move_run(workspace: Path, run_dir: Path, new_workspace: Path, new_run_dir: Path, state: RunState) -> None:
    """Point the registry at a run carried to its own task worktree (autocode_build_worktree.move).

    The record left in the checkout is not a run of its own any more, so its pointer is dropped and
    the new run's is added in one write; a listing never shows the same chat twice."""
    new_workspace, new_run_dir = _validate_registration(new_workspace, new_run_dir, state)
    old_run = str(Path(run_dir).resolve())
    with _locked_registry() as path:
        document = _read_registry(path)
        for key in [key for key, value in document["runs"].items() if value.get("run_dir") == old_run]:
            del document["runs"][key]
        workspace_id, run_id = _identity("workspace", new_workspace), _identity("run", new_run_dir)
        document["workspaces"][workspace_id] = {"id": workspace_id, "workspace": str(new_workspace)}
        document["runs"][run_id] = {
            "id": run_id,
            "workspace_id": workspace_id,
            "workspace": str(new_workspace),
            "run_dir": str(new_run_dir),
            "task_id": _run_task_id(state),
        }
        try:
            util.atomic_json(path, document)
        except OSError as error:
            raise RegistryError("write_failed", f"Registry update failed: {error}") from error


def add_project(selected: Path) -> dict[str, Any]:
    """Register an operator-selected committed Git root without creating a task."""
    try:
        if not selected.expanduser().exists():
            raise ValueError("no such directory")
        workspace = selected.expanduser().resolve(strict=True)
        if not workspace.is_dir() or not (workspace / ".git").exists():
            raise ValueError("select the root of an existing Git checkout or worktree")
        # Ambient Git overrides must not make a different repository qualify this path.
        environ = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        top = util.git(workspace, "rev-parse", "--show-toplevel", env=environ, timeout=10)
        if Path(top).resolve() != workspace:
            raise ValueError("select the Git repository root, not a subdirectory")
        try:
            util.git(workspace, "rev-parse", "--verify", "HEAD^{commit}", env=environ, timeout=10)
        except ValueError as error:
            # Git's own "fatal: Needed a single revision" names no remedy.
            raise ValueError("the repository has no commit yet; make a first commit, then register it") from error
    except (OSError, RuntimeError, ValueError) as error:
        raise RegistryError("invalid_workspace", f"Cannot register {selected}: {error}") from error
    workspace_id = _identity("workspace", workspace)
    record = {"id": workspace_id, "workspace": str(workspace)}
    with _locked_registry() as path:
        document = _read_registry(path)
        existing = document["workspaces"].get(workspace_id)
        if existing is not None and existing != record:
            raise RegistryError("record_conflict", "Registry has a conflicting record for this canonical project")
        if existing is None:
            document["workspaces"][workspace_id] = record
            try:
                util.atomic_json(path, document)
            except OSError as error:
                raise RegistryError("write_failed", f"Registry update failed: {error}") from error
    return {
        "registry_version": REGISTRY_VERSION,
        "operation": "add",
        "workspace_id": workspace_id,
        "workspace": str(workspace),
        "already_registered": existing is not None,
    }


def _register_imported_run(workspace: Path, run_dir: Path, state: RunState) -> tuple[dict[str, str | None], bool]:
    """Register an import candidate without silently repairing a conflicting record."""
    workspace, run_dir = _validate_registration(workspace, run_dir, state)
    workspace_id = _identity("workspace", workspace)
    run_id = _identity("run", run_dir)
    record = {
        "id": run_id,
        "workspace_id": workspace_id,
        "workspace": str(workspace),
        "run_dir": str(run_dir),
        "task_id": _run_task_id(state),
    }
    with _locked_registry() as path:
        document = _read_registry(path)
        existing = document["runs"].get(run_id)
        if existing is not None:
            expected = {key: record[key] for key in ("id", "workspace_id", "workspace", "run_dir")}
            if not isinstance(existing, dict) or any(existing.get(key) != value for key, value in expected.items()):
                raise RegistryError("record_conflict", "Registry has a conflicting record for this canonical run")
            return {"workspace_id": workspace_id, "run_id": run_id, "task_id": _run_task_id(state)}, False
        document["workspaces"][workspace_id] = {"id": workspace_id, "workspace": str(workspace)}
        document["runs"][run_id] = record
        try:
            util.atomic_json(path, document)
        except OSError as error:
            raise RegistryError("write_failed", f"Registry update failed: {error}") from error
    return {"workspace_id": workspace_id, "run_id": run_id, "task_id": _run_task_id(state)}, True


def _canonical_path(value: str) -> Path:
    try:
        return Path(value).resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise RegistryError("path_unavailable", str(error)) from error


def title(task: Any) -> str | None:
    """The first line of what the run was asked to do, bounded: how a client names the chat."""
    first = next((line.strip().lstrip("#").strip() for line in str(task or "").splitlines() if line.strip()), "")
    return (first[: TITLE_LIMIT - 1] + "…" if len(first) > TITLE_LIMIT else first) or None


def _checkpoint_summary(state: Any, workspace: Path) -> tuple[str, dict[str, Any]]:
    if not isinstance(state, dict):
        return "checkpoint_malformed", {"message": "Checkpoint must be an object"}
    version = state.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return "checkpoint_malformed", {"message": "Checkpoint version must be a positive integer"}
    if version > 3:
        return "checkpoint_unsupported", {"message": f"Checkpoint version {version} is unsupported"}
    required = ("workspace", "task", "status")
    if any(not isinstance(state.get(key), str) for key in required):
        return "checkpoint_malformed", {"message": "Checkpoint lacks required workspace, task, or status fields"}
    if state["workspace"] != str(workspace):
        return "checkpoint_malformed", {"message": "Checkpoint workspace does not match its registered workspace"}
    return "available", {
        "status": state["status"],
        "phase": state.get("phase"),
        "next_stage": state.get("next_stage"),
        "task_id": _run_task_id(cast("RunState", state)),
        "checkpoint_version": version,
        "title": title(state["task"]),
    }


def read_checkpoint(state_path: Path, workspace: Path) -> tuple[str, dict[str, Any]]:
    """Read and summarize one run checkpoint, or name why it cannot be read.

    Only a regular ``state.json`` sitting directly inside its run directory
    counts: a symlink, a missing file or unparsable JSON returns an
    availability code instead of a partially trusted record. On success the
    full state travels under ``state`` for callers that need more than the
    summary; callers that list runs drop it and keep only the summary fields.
    """
    run_dir = state_path.parent
    if state_path.is_symlink() or not state_path.is_file() or state_path.resolve().parent != run_dir:
        return "checkpoint_missing", {"message": "Run state.json is unavailable"}
    try:
        state = json.loads(state_path.read_text())
    except PermissionError as error:
        return "inaccessible", {"message": str(error)}
    except (OSError, json.JSONDecodeError) as error:
        return "checkpoint_malformed", {"message": str(error)}
    availability, summary = _checkpoint_summary(state, workspace)
    if availability != "available":
        return availability, summary
    try:
        # When the run last wrote its checkpoint: how a client finds the chats used most recently.
        summary["updated_at"] = time.strftime(UPDATED_AT, time.gmtime(state_path.stat().st_mtime))
    except OSError:
        summary["updated_at"] = None
    return availability, {**summary, "state": state}


def _availability(
    record_key: str, record: dict[str, Any], workspaces: dict[str, Any], visit_checkpoint=None
) -> tuple[str, dict[str, Any]]:
    workspace = record.get("workspace")
    run_dir = record.get("run_dir")
    if not isinstance(workspace, str) or not isinstance(run_dir, str):
        return "malformed_record", {"message": "Record lacks string workspace/run_dir pointers"}
    try:
        canonical_workspace = _canonical_path(workspace)
        canonical_run = _canonical_path(run_dir)
    except RegistryError as error:
        return "inaccessible", {"message": str(error)}
    if str(canonical_workspace) != workspace or str(canonical_run) != run_dir:
        return "malformed_record", {"message": "Stored pointers are not canonical"}
    workspace_id = _identity("workspace", canonical_workspace)
    run_id = _identity("run", canonical_run)
    if record_key != run_id or record.get("id") != run_id or record.get("workspace_id") != workspace_id:
        return "malformed_record", {"message": "Record IDs do not match canonical pointers"}
    workspace_record = workspaces.get(workspace_id)
    if (
        not isinstance(workspace_record, dict)
        or workspace_record.get("id") != workspace_id
        or workspace_record.get("workspace") != workspace
    ):
        return "malformed_record", {"message": "Record does not match its workspace registry entry"}
    if "task_id" in record and record["task_id"] is not None and not isinstance(record["task_id"], str):
        return "malformed_record", {"message": "Record task_id must be a string or null"}
    if not canonical_workspace.is_dir():
        return "workspace_missing", {"message": "Workspace no longer exists"}
    if not (canonical_workspace / ".git").exists():
        return "workspace_invalid", {"message": "Workspace is no longer a Git repository"}
    runs_dir = (canonical_workspace / ".autocode" / "runs").resolve()
    if not runs_dir.is_relative_to(canonical_workspace) or not canonical_run.is_relative_to(runs_dir):
        return "containment_invalid", {"message": "Run is no longer contained by workspace .autocode/runs"}
    availability, payload = read_checkpoint(canonical_run / "state.json", canonical_workspace)
    if visit_checkpoint is not None:
        visit_checkpoint(availability, payload)
    return availability, {key: value for key, value in payload.items() if key != "state"}


def _workspace_listing(key: str, record: Any) -> dict[str, Any]:
    if not isinstance(record, dict) or not isinstance(record.get("workspace"), str):
        return {
            "id": key,
            "availability": "malformed_record",
            "diagnostic": {"message": "Workspace record lacks a string workspace pointer"},
        }
    try:
        workspace = _canonical_path(record["workspace"])
    except RegistryError as error:
        return {**record, "availability": "inaccessible", "diagnostic": {"message": str(error)}}
    if record.get("id") != key or _identity("workspace", workspace) != key or str(workspace) != record["workspace"]:
        return {
            **record,
            "availability": "malformed_record",
            "diagnostic": {"message": "Workspace record ID or pointer is invalid"},
        }
    if not workspace.is_dir():
        return {**record, "availability": "workspace_missing", "diagnostic": {"message": "Workspace no longer exists"}}
    if not (workspace / ".git").exists():
        return {
            **record,
            "availability": "workspace_invalid",
            "diagnostic": {"message": "Workspace is no longer a Git repository"},
        }
    return {**record, "availability": "available", "diagnostic": {}}


def location() -> dict[str, Any]:
    path = registry_path()
    return {
        "registry_version": REGISTRY_VERSION,
        "operation": "location",
        "storage_root": str(storage_root()),
        "registry_path": str(path),
        "exists": path.is_file(),
    }


def listing(also: Path | None = None, *, include_archived: bool = False, others=()) -> dict[str, Any]:
    """The registry's workspaces and runs; ``also`` adds a project registry (``project_storage``).

    ``others`` adds further project registries (``other_project_storage``), for a listing of every
    project's chats. The shared registry's records win on a shared key. An unreadable project registry
    is a diagnostic, never a reason to hide the shared registry. Archived chats are hidden unless asked for.
    """
    return _listing(also, include_archived=include_archived, others=others)


def _listing(also: Path | None = None, *, include_archived: bool = False, visit_run=None, others=()) -> dict[str, Any]:
    """Internal streaming projection: consume each validated checkpoint before the next read.

    The visitor receives (pointer record, checkpoint result), or None for a pointer
    refused before checkpoint inspection. Full state never enters the returned
    registry JSON and is retained for only one row, never for action execution.
    """
    documents, diagnostics = [], []
    path = registry_path()
    if path.exists():
        documents.append(_read_registry(path))
    extras: list[Path] = []
    for root in (*((also,) if also is not None else ()), *others):
        extra = Path(root) / "registry.json"
        if extra not in extras and extra != path:
            extras.append(extra)
    for extra in extras:
        if not extra.exists():
            continue
        try:
            documents.append(_read_registry(extra))
        except RegistryError as error:
            diagnostics.append({"code": "project_registry_unreadable", "message": f"{extra}: {error}"})
    if not documents:
        return {
            "registry_version": REGISTRY_VERSION,
            "operation": "list",
            "registry_exists": False,
            "workspaces": [],
            "runs": [],
            "diagnostics": [{"code": "registry_absent", "message": "No registry exists yet"}, *diagnostics],
        }
    document = _empty()
    for source in documents:
        for kind in ("workspaces", "runs"):
            for key, value in source[kind].items():
                document[kind].setdefault(key, value)
    workspaces = [_workspace_listing(key, document["workspaces"][key]) for key in sorted(document["workspaces"])]
    runs = []
    for key in sorted(document["runs"]):
        record = document["runs"][key]
        if not isinstance(record, dict):
            runs.append(
                {"id": key, "availability": "malformed_record", "diagnostic": {"message": "Record is not an object"}}
            )
            continue
        if record.get("archived") is True and not include_archived:
            continue
        visited = False

        def visit_checkpoint(availability, payload):
            nonlocal visited
            visited = True
            visit_run(record, (availability, payload))

        availability, diagnostic = _availability(
            key, record, document["workspaces"], visit_checkpoint if visit_run is not None else None
        )
        if visit_run is not None and not visited:
            visit_run(record, None)
        # The central pointer record may predate migration. Surface the run's
        # authoritative task_id without writing either registry or checkpoint.
        task_id = diagnostic.pop("task_id", record.get("task_id"))
        runs.append(
            {
                **record,
                "task_id": task_id,
                "availability": availability,
                "diagnostic": diagnostic,
                "archived": record.get("archived") is True,
                "actions": _run_actions(record, availability, diagnostic),
            }
        )
    return {
        "registry_version": REGISTRY_VERSION,
        "operation": "list",
        "registry_exists": True,
        "workspaces": workspaces,
        "runs": runs,
        "diagnostics": diagnostics,
    }


def _import_diagnostic(code: str, path: Path, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "path": str(path), "message": message, **details}


def _import_candidate(workspace: Path, run_dir: Path, result: dict[str, Any]) -> bool:
    state_path = run_dir / "state.json"
    if state_path.is_symlink() or not state_path.is_file() or state_path.resolve().parent != run_dir:
        result["diagnostics"].append(
            _import_diagnostic(
                "checkpoint_missing", run_dir, "Run state.json must be a regular file directly inside the run directory"
            )
        )
        return True
    try:
        state = json.loads(state_path.read_text())
    except PermissionError as error:
        result["diagnostics"].append(_import_diagnostic("inaccessible", state_path, str(error)))
        return True
    except (OSError, json.JSONDecodeError) as error:
        result["diagnostics"].append(_import_diagnostic("checkpoint_malformed", state_path, str(error)))
        return True
    availability, diagnostic = _checkpoint_summary(state, workspace)
    if availability != "available":
        result["diagnostics"].append(_import_diagnostic(availability, state_path, diagnostic["message"]))
        return True
    try:
        identity, created = _register_imported_run(workspace, run_dir, state)
    except RegistryError as error:
        result["diagnostics"].append(_import_diagnostic(error.code, run_dir, str(error)))
        result["registry_error"] = {"code": error.code, "message": str(error)}
        return False
    key = "imported" if created else "already_registered"
    result[key].append(identity)
    return True


def registry_import(
    selected_root: Path,
    *,
    max_depth: int = DEFAULT_IMPORT_MAX_DEPTH,
    directory_budget: int = DEFAULT_IMPORT_DIRECTORY_BUDGET,
) -> dict[str, Any]:
    """Import valid existing pointers below a bounded, explicitly selected root.

    The traversal budget counts every unique canonical directory inspected,
    including the selected root at depth zero and direct run candidates. Aliases
    to an already inspected directory do not consume the budget a second time.
    """
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0:
        raise RegistryError("invalid_depth", "Import max depth must be a nonnegative integer")
    if isinstance(directory_budget, bool) or not isinstance(directory_budget, int) or directory_budget < 1:
        raise RegistryError("invalid_budget", "Import directory budget must be a positive integer")
    if not Path(selected_root).expanduser().exists():
        raise RegistryError("invalid_root", f"Selected root is unavailable: no such directory: {selected_root}")
    try:
        root = Path(selected_root).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise RegistryError("invalid_root", f"Selected root is unavailable: {error}") from error
    if not root.is_dir():
        raise RegistryError("invalid_root", f"Selected root is not a directory: {root}")
    result: dict[str, Any] = {
        "registry_version": REGISTRY_VERSION,
        "operation": "import",
        "selected_root": str(root),
        "max_depth": max_depth,
        "directory_budget": directory_budget,
        "directories_inspected": 0,
        "complete": True,
        "imported": [],
        "already_registered": [],
        "diagnostics": [],
    }
    queue: list[tuple[Path, int]] = [(root, 0)]
    seen: set[Path] = set()

    def inspect_directory(directory: Path, *, depth: int | None = None) -> bool:
        if directory in seen:
            return True
        if result["directories_inspected"] >= directory_budget:
            result["complete"] = False
            details = {} if depth is None else {"depth": depth}
            result["diagnostics"].append(
                _import_diagnostic(
                    "budget_exhausted",
                    directory,
                    "Directory traversal budget exhausted before this directory",
                    **details,
                )
            )
            return False
        seen.add(directory)
        result["directories_inspected"] += 1
        return True

    while queue:
        directory, depth = queue.pop(0)
        if directory in seen:
            continue
        if not inspect_directory(directory, depth=depth):
            break
        runs_dir = directory / ".autocode" / "runs"
        try:
            canonical_runs = runs_dir.resolve(strict=False)
        except (OSError, RuntimeError) as error:
            result["complete"] = False
            result["diagnostics"].append(_import_diagnostic("inaccessible", runs_dir, str(error)))
            canonical_runs = None
        if canonical_runs is not None and runs_dir.is_dir():
            if not canonical_runs.is_relative_to(directory):
                result["diagnostics"].append(
                    _import_diagnostic(
                        "containment_invalid", runs_dir, "Workspace .autocode/runs escapes its canonical workspace"
                    )
                )
            else:
                try:
                    candidates = canonical_runs.iterdir()
                except OSError as error:
                    result["complete"] = False
                    result["diagnostics"].append(_import_diagnostic("inaccessible", canonical_runs, str(error)))
                else:
                    try:
                        for candidate in candidates:
                            try:
                                run_dir = candidate.resolve(strict=True)
                            except (OSError, RuntimeError) as error:
                                result["diagnostics"].append(_import_diagnostic("inaccessible", candidate, str(error)))
                                continue
                            if not run_dir.is_dir() or not run_dir.is_relative_to(canonical_runs):
                                result["diagnostics"].append(
                                    _import_diagnostic(
                                        "containment_invalid",
                                        candidate,
                                        "Run directory escapes workspace .autocode/runs",
                                    )
                                )
                                continue
                            if run_dir in seen:
                                result["diagnostics"].append(
                                    _import_diagnostic(
                                        "duplicate_directory", candidate, "Canonical directory was already discovered"
                                    )
                                )
                                continue
                            if not inspect_directory(run_dir):
                                return result
                            if not _import_candidate(directory, run_dir, result):
                                result["complete"] = False
                                return result
                    except OSError as error:
                        result["complete"] = False
                        result["diagnostics"].append(_import_diagnostic("inaccessible", canonical_runs, str(error)))
        if depth == max_depth:
            continue
        try:
            children = list(directory.iterdir())
        except OSError as error:
            result["complete"] = False
            result["diagnostics"].append(_import_diagnostic("inaccessible", directory, str(error)))
            continue
        for child in children:
            # Repository internals and run contents are candidates, never workspace roots.
            if child.name in {".git", ".autocode"}:
                continue
            try:
                canonical_child = child.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                result["complete"] = False
                result["diagnostics"].append(_import_diagnostic("inaccessible", child, str(error)))
                continue
            if not canonical_child.is_relative_to(root):
                result["complete"] = False
                result["diagnostics"].append(
                    _import_diagnostic("symlink_escape", child, "Directory alias resolves outside the selected root")
                )
                continue
            if not canonical_child.is_dir():
                continue
            if canonical_child in seen or any(path == canonical_child for path, _ in queue):
                result["diagnostics"].append(
                    _import_diagnostic("duplicate_directory", child, "Canonical directory was already discovered")
                )
                continue
            queue.append((canonical_child, depth + 1))
    return result


def forget_deleted(workspace: Path, run_dir: Path) -> dict[str, Any]:
    """Remove only an absent run's exact discovery pointer; never delete files."""
    workspace, run_dir = Path(workspace), Path(run_dir)
    if (
        not workspace.is_absolute()
        or not run_dir.is_absolute()
        or str(workspace.resolve()) != str(workspace)
        or str(run_dir.resolve()) != str(run_dir)
        or run_dir.parent != workspace / ".autocode/runs"
        or os.path.lexists(run_dir)
    ):
        raise RegistryError("invalid_deleted_run", "Only an absent canonical direct run may be forgotten")
    with _locked_registry() as path:
        # The run or an ancestor may have been recreated while this cleanup
        # waited for the registry lock. Never forget that new discovery entry.
        if (
            str(workspace.resolve()) != str(workspace)
            or str(run_dir.resolve()) != str(run_dir)
            or os.path.lexists(run_dir)
        ):
            raise RegistryError("invalid_deleted_run", "Only an absent canonical direct run may be forgotten")
        document = _read_registry(path)
        removed = [
            key
            for key, value in document["runs"].items()
            if value.get("workspace") == str(workspace) and value.get("run_dir") == str(run_dir)
        ]
        for key in removed:
            del document["runs"][key]
        if not any(value.get("workspace") == str(workspace) for value in document["runs"].values()):
            document["workspaces"] = {
                key: value for key, value in document["workspaces"].items() if value.get("workspace") != str(workspace)
            }
        util.atomic_json(path, document)
    return {"registry_version": REGISTRY_VERSION, "operation": "forget-deleted", "removed": removed}


def archived_run_dirs(also: Path | None = None) -> set[str]:
    """The run directories of the chats a person archived, read from the registries without their checkpoints.

    ``also`` adds a project registry (``project_storage``). An unreadable registry hides nothing."""
    archived: set[str] = set()
    for path in (registry_path(), *((also / "registry.json",) if also is not None else ())):
        try:
            document = _read_registry(path)
        except RegistryError:
            continue
        for record in document["runs"].values():
            if isinstance(record, dict) and record.get("archived") is True and isinstance(record.get("run_dir"), str):
                archived.add(record["run_dir"])
    return archived


def archive_run(workspace: str | Path, run_dir: str | Path, archived: bool = True) -> dict[str, Any]:
    """Hide or restore a chat without moving or deleting its run directory."""
    workspace, run_dir = _pointer(workspace, run_dir)

    def mutate(document: dict[str, Any], keys: list[str]) -> None:
        for key in keys:
            record = document["runs"][key]
            if archived:
                record["archived"] = True
            else:
                record.pop("archived", None)

    updated = _update_run_records(workspace, run_dir, mutate)
    if not updated:
        raise RegistryError("run_not_registered", f"No registry entry names {run_dir}")
    return {
        "registry_version": REGISTRY_VERSION,
        "operation": "archive" if archived else "unarchive",
        "workspace": str(workspace),
        "run_dir": str(run_dir),
        "archived": archived,
        "updated": len(updated),
    }


def delete_run(workspace: str | Path, run_dir: str | Path, confirmed: bool = False) -> dict[str, Any]:
    """Delete one chat's run record after refusing a live run; never a branch or worktree."""
    workspace, run_dir = _pointer(workspace, run_dir)
    if not confirmed:
        raise RegistryError("confirmation_required", "Deleting a chat requires --yes")
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise RegistryError("invalid_run", "Only an existing run directory can be deleted")
    if not workspace.is_dir():
        raise RegistryError("invalid_workspace", f"Workspace no longer exists: {workspace}")
    state_path = run_dir / "state.json"
    if state_path.is_symlink() or not state_path.is_file() or state_path.resolve().parent != run_dir:
        raise RegistryError("invalid_run", "Run state.json must be a regular file directly inside the run directory")
    try:
        state = json.loads(state_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RegistryError("invalid_run", f"Run state.json cannot be read: {error}") from error
    if not isinstance(state, dict) or state.get("workspace") != str(workspace):
        raise RegistryError("invalid_run", "Run state.json does not name this canonical workspace")
    registered = False
    for root in _registry_roots(workspace):
        path = root / "registry.json"
        if path.is_file() and _matching_keys(_read_registry(path), workspace, run_dir):
            registered = True
            break
    if not registered:
        raise RegistryError("run_not_registered", f"No registry entry names {run_dir}")
    try:
        # The workspace writer lock is the one a run's agents hold; the run lock catches a save.
        with util.workspace_lock(workspace):
            try:
                with util.run_lock(run_dir):
                    pass
            except util.Paused as error:
                raise RegistryError("run_busy", "The chat is still running; stop it before deleting it") from error
            kept_branch = state.get("task_branch") if isinstance(state.get("task_branch"), str) else None
            try:
                shutil.rmtree(run_dir)
            except OSError as error:
                raise RegistryError("delete_failed", f"Run directory could not be deleted: {error}") from error
    except util.Paused as error:
        raise RegistryError("run_busy", "The chat is still running; stop it before deleting it") from error

    def mutate(document: dict[str, Any], keys: list[str]) -> None:
        for key in keys:
            document["runs"].pop(key, None)
        if not any(value.get("workspace") == str(workspace) for value in document["runs"].values()):
            document["workspaces"] = {
                key: value for key, value in document["workspaces"].items() if value.get("workspace") != str(workspace)
            }

    removed = _update_run_records(workspace, run_dir, mutate)
    return {
        "registry_version": REGISTRY_VERSION,
        "operation": "delete",
        "workspace": str(workspace),
        "run_dir": str(run_dir),
        "removed": len(removed),
        "kept_branch": kept_branch,
    }


def cli(argv: list[str]) -> int:
    if argv and argv[0] in ("roots", "repositories"):
        family, *tail = argv
        if family == "roots":
            return source_catalog.cli(tail)
        return source_catalog.cli(["repositories", *tail])
    parser = argparse.ArgumentParser(description="Read the per-user Autocode run registry")
    subcommands = parser.add_subparsers(dest="operation", required=True)
    for operation in ("location", "list"):
        command = subcommands.add_parser(operation)
        if operation == "list":
            command.add_argument(
                "--include-archived", action="store_true", help="Include chats hidden with registry archive"
            )
        command.add_argument(
            "--json", action="store_true", help="Accepted for argument-array compatibility; JSON is always emitted"
        )
    command = subcommands.add_parser("add", help="Register a committed local Git project without starting a task")
    command.add_argument("workspace", type=Path, help="The project directory to register")
    command.add_argument(
        "--json", action="store_true", help="Accepted for argument-array compatibility; JSON is always emitted"
    )
    command = subcommands.add_parser("import", help="Register every committed Git project found under a directory")
    command.add_argument("selected_root", type=Path, help="The directory to search")
    command.add_argument(
        "--max-depth",
        type=int,
        default=DEFAULT_IMPORT_MAX_DEPTH,
        help=f"How deep to search (default: {DEFAULT_IMPORT_MAX_DEPTH})",
    )
    command.add_argument(
        "--directory-budget",
        type=int,
        default=DEFAULT_IMPORT_DIRECTORY_BUDGET,
        help=f"Most directories to visit (default: {DEFAULT_IMPORT_DIRECTORY_BUDGET})",
    )
    command.add_argument(
        "--json", action="store_true", help="Accepted for argument-array compatibility; JSON is always emitted"
    )
    command = subcommands.add_parser("forget-deleted", help="Drop the entry of a run whose directory no longer exists")
    command.add_argument("--workspace", type=Path, required=True, help="The project the run belonged to")
    command.add_argument("--run-dir", type=Path, required=True, help="The run directory that no longer exists")
    command.add_argument(
        "--json", action="store_true", help="Accepted for argument-array compatibility; JSON is always emitted"
    )
    for operation, help_text in (
        ("archive", "Hide a chat from the runs list without deleting it"),
        ("unarchive", "Show a hidden chat again"),
        ("delete", "Delete a chat's run directory and registry entry; branches and worktrees are kept"),
    ):
        command = subcommands.add_parser(operation, help=help_text)
        command.add_argument("--workspace", type=Path, required=True, help="The project the run belongs to")
        command.add_argument("--run-dir", type=Path, required=True, help="The direct run directory")
        if operation == "delete":
            command.add_argument("--yes", action="store_true", help="Confirm the irreversible deletion")
        command.add_argument(
            "--json", action="store_true", help="Accepted for argument-array compatibility; JSON is always emitted"
        )
    args = parser.parse_args(argv)
    try:
        if args.operation == "location":
            result = location()
        elif args.operation == "list":
            result = listing(
                also=project_storage(Path.cwd()),
                include_archived=getattr(args, "include_archived", False),
                others=other_project_storage(),
            )
        elif args.operation == "add":
            result = add_project(args.workspace)
        elif args.operation == "forget-deleted":
            result = forget_deleted(args.workspace, args.run_dir)
        elif args.operation == "archive":
            result = archive_run(args.workspace, args.run_dir, True)
        elif args.operation == "unarchive":
            result = archive_run(args.workspace, args.run_dir, False)
        elif args.operation == "delete":
            result = delete_run(args.workspace, args.run_dir, args.yes)
        else:
            result = registry_import(
                args.selected_root, max_depth=args.max_depth, directory_budget=args.directory_budget
            )
    except RegistryError as error:
        result = {
            "registry_version": REGISTRY_VERSION,
            "operation": args.operation,
            "error": {"code": error.code, "message": str(error)},
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 2 if result.get("registry_error") else 1 if not result.get("complete", True) else 0

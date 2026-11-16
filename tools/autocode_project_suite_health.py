"""Durable, evidence-cited per-project baseline and flaky-test quarantine store (#2942).

Stores authenticated base-suite results, confirmed deterministic pre-existing base failures, and
proven flaky-test quarantine entries under ``.autocode/suite-health.json`` and
``.autocode/suite-health/baselines/<id>/`` so separate runs and worktrees of the same project share
them safely:

- Every entry carries the source commit and runtime/command provenance that produced it.
- Baselines are never cached when broken, empty, timed out, patched with a transient base patch, or
  driven by unpinned local inputs (never reintroducing greenfield dead-ends like #2259).
- Quarantine entries are invalidated across base commits whenever Git history edits the test's own
  module/conftest or after ``MAX_QUARANTINE_BASES`` commits without a fresh observation, and
  deterministic candidate failures or changed/protected tests are never waived.
- All file reads and replacements follow ``autocode_project_memory``'s ``O_NOFOLLOW`` lock and atomic
  replace guarantees, and ``autocode_workspaces.keep_out_of_git`` keeps ``.autocode/`` untracked.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from . import autocode_package_scope as package_scope
    from . import autocode_project_memory as memory
    from . import autocode_project_memory_interface as memory_interface
    from . import autocode_util as util
    from . import autocode_verification_schedule as schedule
    from . import autocode_workspaces as workspaces
except ImportError:
    import autocode_package_scope as package_scope
    import autocode_project_memory as memory
    import autocode_project_memory_interface as memory_interface
    import autocode_util as util
    import autocode_verification_schedule as schedule
    import autocode_workspaces as workspaces

VERSION = 1
FILENAME = "suite-health.json"
STORE_DIR = "suite-health"
BASELINES_SUBDIR = "baselines"
MAX_BYTES = 262144
MAX_BASELINES = 8
MAX_PRE_EXISTING = 64
MAX_QUARANTINED = 64
MAX_QUARANTINE_BASES = 10
MAX_FAILED_LIST = 256
MAX_TEXT = 2048
MAX_SHORT = 160
MAX_TEST_ID = 400
_HEX64 = re.compile(r"[0-9a-f]{64}")
_BASELINE_ID = re.compile(r"baseline-[0-9a-f]{16}")
_RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
_OUTCOMES = frozenset({"pass", "fail", "missing"})


def _error(message="Project suite health record is invalid or unsafe; existing data preserved."):
    return ValueError(message)


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _text(value: Any, limit: int, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise _error()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise _error()


def _string_list(value: Any, *, max_items: int, item_limit: int, allowed: frozenset[str] | None = None) -> None:
    if not isinstance(value, list) or len(value) > max_items:
        raise _error()
    for item in value:
        _text(item, item_limit)
        if allowed is not None and item not in allowed:
            raise _error()


def _empty() -> dict:
    return {"version": VERSION, "baselines": [], "pre_existing": [], "quarantine": []}


def sanitize(document: Any) -> dict:
    """Validate strict version-1 schema of ``.autocode/suite-health.json``."""
    if (
        not isinstance(document, dict)
        or set(document) != {"version", "baselines", "pre_existing", "quarantine"}
        or type(document.get("version")) is not int
        or document["version"] != VERSION
    ):
        raise _error()
    baselines = document["baselines"]
    pre_existing = document["pre_existing"]
    quarantine = document["quarantine"]
    if (
        not isinstance(baselines, list)
        or len(baselines) > MAX_BASELINES
        or not isinstance(pre_existing, list)
        or len(pre_existing) > MAX_PRE_EXISTING
        or not isinstance(quarantine, list)
        or len(quarantine) > MAX_QUARANTINED
    ):
        raise _error()

    seen_baselines: set[str] = set()
    for row in baselines:
        expected = {
            "id",
            "binding_digest",
            "base",
            "command",
            "framework",
            "health",
            "failed_tests",
            "passed_count",
            "recorded_at",
            "run",
            "baseline_sha256",
        }
        if not isinstance(row, dict) or set(row) != expected:
            raise _error()
        if not isinstance(row["id"], str) or _BASELINE_ID.fullmatch(row["id"]) is None:
            raise _error()
        if row["id"] in seen_baselines:
            raise _error()
        seen_baselines.add(row["id"])
        for digest_key in ("binding_digest", "baseline_sha256"):
            if not isinstance(row[digest_key], str) or _HEX64.fullmatch(row[digest_key]) is None:
                raise _error()
        _text(row["base"], MAX_SHORT)
        _text(row["command"], MAX_TEXT)
        _text(row["framework"], MAX_SHORT, nullable=True)
        if row["health"] not in ("passing", "failing_tests"):
            raise _error()
        _string_list(row["failed_tests"], max_items=MAX_FAILED_LIST, item_limit=MAX_TEST_ID)
        if type(row["passed_count"]) is not int or row["passed_count"] < 0:
            raise _error()
        _text(row["recorded_at"], MAX_SHORT)
        if row["run"] is not None and (not isinstance(row["run"], str) or _RUN_NAME.fullmatch(row["run"]) is None):
            raise _error()

    seen_pre: set[tuple[str, str, str | None, str]] = set()
    for row in pre_existing:
        expected = {"base", "command", "framework", "test", "base_runs", "recorded_at", "run"}
        if not isinstance(row, dict) or set(row) != expected:
            raise _error()
        _text(row["base"], MAX_SHORT)
        _text(row["command"], MAX_TEXT)
        _text(row["framework"], MAX_SHORT, nullable=True)
        _text(row["test"], MAX_TEST_ID)
        _string_list(row["base_runs"], max_items=16, item_limit=16, allowed=_OUTCOMES)
        _text(row["recorded_at"], MAX_SHORT)
        if row["run"] is not None and (not isinstance(row["run"], str) or _RUN_NAME.fullmatch(row["run"]) is None):
            raise _error()
        key = (row["base"], row["command"], row["framework"], row["test"])
        if key in seen_pre:
            raise _error()
        seen_pre.add(key)

    seen_quarantine: set[tuple[str | None, str | None, str]] = set()
    for row in quarantine:
        expected = {
            "test",
            "framework",
            "package",
            "command",
            "reason",
            "base_commit",
            "base_runs",
            "candidate_runs",
            "recorded_at",
            "run",
            "seen_bases",
        }
        if not isinstance(row, dict) or set(row) != expected:
            raise _error()
        _text(row["test"], MAX_TEST_ID)
        _text(row["framework"], MAX_SHORT, nullable=True)
        _text(row["package"], MAX_SHORT, nullable=True)
        _text(row["command"], MAX_TEXT)
        _text(row["reason"], MAX_TEXT)
        _text(row["base_commit"], MAX_SHORT)
        _string_list(row["base_runs"], max_items=16, item_limit=16, allowed=_OUTCOMES)
        _string_list(row["candidate_runs"], max_items=16, item_limit=16, allowed=_OUTCOMES)
        _text(row["recorded_at"], MAX_SHORT)
        if row["run"] is not None and (not isinstance(row["run"], str) or _RUN_NAME.fullmatch(row["run"]) is None):
            raise _error()
        _string_list(row["seen_bases"], max_items=MAX_QUARANTINE_BASES + 1, item_limit=MAX_SHORT)
        q_key = (row["framework"], row["package"], row["test"])
        if q_key in seen_quarantine:
            raise _error()
        seen_quarantine.add(q_key)

    return copy.deepcopy(document)


def project_root(workspace_or_deps: str | Path) -> Path:
    """Resolve the canonical owning project root (shared across task worktrees)."""
    return memory_interface.selected(workspace_or_deps).resolve()


def run_name_from(run_dir: str | Path | None) -> str | None:
    if not run_dir:
        return None
    name = Path(run_dir).name
    return name if _RUN_NAME.fullmatch(name) else None


def load(project: Path | str) -> dict:
    """Safely load ``.autocode/suite-health.json``; absent or invalid records fail cleanly."""
    try:
        resolved = Path(project).resolve()
        doc, pin = memory.read_document(resolved, FILENAME, sanitize, limit=MAX_BYTES)
    except (OSError, ValueError):
        return {
            "status": "invalid",
            "document": _empty(),
            "sha256": None,
        }
    if doc is None:
        return {"status": "absent", "document": _empty(), "sha256": None}
    return {"status": "loaded", "document": doc, "sha256": pin}


def project_binding(
    project: Path,
    *,
    base: str,
    suite: str,
    framework: Any,
    timeout: int | float | None,
    baseline_identity: dict,
) -> dict:
    """Deterministic project-wide cache binding matching across all task worktrees of ``project``."""
    _ = project
    return {
        "base": base,
        "command": suite,
        "framework": framework.to_dict() if framework else None,
        "base_patch": None,
        "timeout": timeout,
        "runtime": baseline_identity,
    }


def binding_digest(binding: dict) -> str:
    return util.digest(binding)


def baseline_eligible(
    *,
    base_patch: Any,
    ignored_inputs: Any,
    framework: Any,
    runtime_identity: dict | None,
) -> bool:
    """Whether a run's baseline may be restored from or stored in the shared project cache."""
    if base_patch is not None:
        return False
    if ignored_inputs is not None and (
        getattr(ignored_inputs, "generated", None)
        or getattr(ignored_inputs, "vendor", None)
        or getattr(ignored_inputs, "unverified", None)
    ):
        return False
    if framework is not None and getattr(framework, "name", None) == "junit":
        return False
    return bool(isinstance(runtime_identity, dict) and runtime_identity.get("reuse_supported") is True)


def _baseline_usable(result: Any, source_dir: Path) -> bool:
    """Only completed, green or partially-passing baselines with intact captures may be shared (#2259)."""
    if not isinstance(result, dict) or result.get("health") not in ("passing", "failing_tests"):
        return False
    receipt = result.get("receipt")
    if not isinstance(receipt, dict) or receipt.get("timed_out"):
        return False
    if not schedule.intact(receipt, root=source_dir):
        return False
    if receipt.get("results_expected"):
        results = receipt.get("results") or {}
        return schedule.complete_results(receipt) and bool(results.get("passed"))
    return receipt.get("exit_code") == 0


def _ensure_store_dir(project: Path, entry_id: str) -> Path:
    workspaces.keep_out_of_git(project)
    base_dir = project / ".autocode" / STORE_DIR / BASELINES_SUBDIR
    base_dir.mkdir(parents=True, exist_ok=True)
    entry_dir = base_dir / entry_id
    if entry_dir.exists() or entry_dir.is_symlink():
        if entry_dir.is_symlink():
            raise _error("Project suite-health baseline directory is a symlink")
        shutil.rmtree(entry_dir)
    entry_dir.mkdir(parents=True, exist_ok=True)
    return entry_dir


def _copy_regular_bytes(src: Path, dst: Path) -> None:
    if src.is_symlink():
        raise _error("Baseline capture is a symlink")
    resolved = src.resolve()
    if not resolved.is_file():
        raise _error("Baseline capture is not an ordinary regular file")
    info = resolved.stat()
    if not stat.S_ISREG(info.st_mode):
        raise _error("Baseline capture is not a regular file")
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(resolved.read_bytes())


def _materialize_copy(result: dict, dest_root: Path) -> dict:
    """Copy a verified baseline result and its output/supervision captures into ``dest_root``."""
    dest_root = dest_root.resolve()
    copied = copy.deepcopy(result)
    receipt = copied["receipt"]
    src_output = Path(receipt["output"])
    dst_output = dest_root / "baseline" / src_output.name
    _copy_regular_bytes(src_output, dst_output)
    receipt["output"] = str(dst_output)
    supervision = receipt.get("supervision")
    if isinstance(supervision, dict) and isinstance(supervision.get("receipt"), str):
        src_sup = Path(supervision["receipt"])
        dst_sup = dest_root / "baseline" / "supervision" / src_sup.name
        _copy_regular_bytes(src_sup, dst_sup)
        receipt["supervision"] = {**supervision, "receipt": str(dst_sup)}
    if not schedule.intact(receipt, root=dest_root):
        raise _error("Materialized baseline capture failed integrity verification")
    return copied


def save_baseline(
    project: Path | str,
    digest: str,
    result: dict,
    source_directory: Path | str,
    *,
    framework_name: str | None,
    run_name: str | None = None,
) -> dict | None:
    """Persist an authenticated baseline into ``.autocode/suite-health`` for future runs."""
    project = Path(project).resolve()
    source_directory = Path(source_directory).resolve()
    if not _baseline_usable(result, source_directory):
        return None
    workspaces.keep_out_of_git(project)
    entry_id = "baseline-" + hashlib.sha256(f"{digest}:{uuid.uuid4().hex}".encode()).hexdigest()[:16]
    entry_dir = _ensure_store_dir(project, entry_id)
    try:
        archived = _materialize_copy(result, entry_dir)
        archived_json = entry_dir / "baseline.json"
        util.atomic_json(archived_json, archived)
        baseline_sha = util.file_hash(archived_json)
    except (OSError, ValueError, TypeError, KeyError):
        shutil.rmtree(entry_dir, ignore_errors=True)
        return None

    results_map = (result.get("receipt") or {}).get("results") or {}
    failed_list = sorted(str(t) for t in (results_map.get("failed") or []) if isinstance(t, str))[:MAX_FAILED_LIST]
    passed_count = len(results_map.get("passed") or []) if isinstance(results_map.get("passed"), list) else 0
    row = {
        "id": entry_id,
        "binding_digest": digest,
        "base": str(result["base"]),
        "command": str(result["command"]),
        "framework": framework_name,
        "health": str(result["health"]),
        "failed_tests": failed_list,
        "passed_count": passed_count,
        "recorded_at": _now(),
        "run": run_name,
        "baseline_sha256": baseline_sha,
    }
    evicted: list[str] = []

    def change(previous: dict | None) -> dict:
        doc = previous or _empty()
        kept = []
        for item in doc["baselines"]:
            if item["binding_digest"] == digest:
                evicted.append(item["id"])
            else:
                kept.append(item)
        kept.append(row)
        while len(kept) > MAX_BASELINES:
            dropped = kept.pop(0)
            evicted.append(dropped["id"])
        doc["baselines"] = kept
        return doc

    memory.update_document(project, FILENAME, sanitize, change, limit=MAX_BYTES)
    baselines_root = project / ".autocode" / STORE_DIR / BASELINES_SUBDIR
    for old_id in evicted:
        target = baselines_root / old_id
        if target.exists() and not target.is_symlink():
            shutil.rmtree(target, ignore_errors=True)
    return row


def restore_baseline(project: Path | str, digest: str, target_directory: Path | str) -> tuple[dict, Path] | None:
    """Restore a matching verified baseline from ``.autocode/suite-health`` into ``target_directory``."""
    project = Path(project).resolve()
    target_directory = Path(target_directory).resolve()
    snapshot = load(project)
    if snapshot["status"] != "loaded":
        return None
    entry = next(
        (row for row in reversed(snapshot["document"]["baselines"]) if row["binding_digest"] == digest),
        None,
    )
    if entry is None:
        return None
    entry_dir = (project / ".autocode" / STORE_DIR / BASELINES_SUBDIR / entry["id"]).resolve()
    archived_json = entry_dir / "baseline.json"
    try:
        if any(p.is_symlink() for p in (entry_dir, archived_json)):
            return None
        if not archived_json.is_file() or util.file_hash(archived_json) != entry["baseline_sha256"]:
            return None
        archived = json.loads(archived_json.read_text(encoding="utf-8"))
        if not _baseline_usable(archived, entry_dir):
            return None
        if archived.get("base") != entry["base"] or archived.get("command") != entry["command"]:
            return None
        target_directory.mkdir(parents=True, exist_ok=True)
        restored = _materialize_copy(archived, target_directory)
        out_json = target_directory / "baseline.json"
        util.atomic_json(out_json, restored)
        return restored, out_json
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _test_affected_by_paths(test_id: str, changed_paths: set[str], framework_name: str | None) -> bool:
    """True when any file changed between commits could define or configure ``test_id``."""
    if not changed_paths:
        return False
    # Project-wide conftest / root test package hooks can alter any test run
    for path in changed_paths:
        name = PurePosixPath(path).name
        if framework_name == "pytest" and name == "conftest.py":
            return True
        if name in ("pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini"):
            return True
    if "::" in test_id and "/" in test_id.split("::", 1)[0]:
        file_part = test_id.split("::", 1)[0]
        if file_part in changed_paths:
            return True
        if any(p.endswith("/" + file_part) for p in changed_paths):
            return True
    cleaned = re.sub(r"\s+\(.*\)$", "", test_id)
    cleaned = re.sub(r"\[.*\]$", "", cleaned)
    parts = cleaned.replace("::", ".").split(".")
    # Match module prefixes (e.g. tests.test_flaky.FlakyTests.test_a -> tests/test_flaky.py)
    candidates: set[str] = set()
    for k in range(len(parts), 0, -1):
        sub = "/".join(parts[:k])
        candidates.add(sub + ".py")
        candidates.add(sub + "/__init__.py")
    # Also match package __init__.py files along ancestor dirs
    for cand in list(candidates):
        parent = PurePosixPath(cand).parent
        while str(parent) not in (".", ""):
            candidates.add(f"{parent.as_posix()}/__init__.py")
            parent = parent.parent
    return any(path in candidates or any(path.endswith("/" + cand) for cand in candidates) for path in changed_paths)


def _entry_package(command: str | None) -> str | None:
    if not command:
        return None
    entered = package_scope.entered(command)
    return entered[0] if entered else None


def active_quarantine(
    project: Path | str,
    framework_name: str | None,
    command: str | None,
    current_base: str,
    *,
    git_changed_files_fn=None,
) -> dict[str, dict]:
    """Active cross-run flaky-test quarantine entries applicable to ``(framework_name, package, current_base)``."""
    if not framework_name or not current_base:
        return {}
    snapshot = load(project)
    if snapshot["status"] != "loaded":
        return {}
    package = _entry_package(command)
    diff_cache: dict[str, set[str] | None] = {}
    active: dict[str, dict] = {}
    for row in snapshot["document"]["quarantine"]:
        if row["framework"] != framework_name or row["package"] != package:
            continue
        origin = row["base_commit"]
        if origin != current_base:
            if len(row.get("seen_bases") or []) >= MAX_QUARANTINE_BASES:
                continue
            if git_changed_files_fn is None:
                continue
            if origin not in diff_cache:
                diff_cache[origin] = git_changed_files_fn(origin, current_base)
            changed = diff_cache[origin]
            if changed is None or _test_affected_by_paths(row["test"], changed, framework_name):
                continue
        active[row["test"]] = copy.deepcopy(row)
    return active


def confirmed_pre_existing(
    project: Path | str,
    *,
    base: str,
    command: str | None,
    framework_name: str | None,
) -> dict[str, dict]:
    """Deterministic pre-existing failures confirmed on the exact ``(base, command, framework)``."""
    if not base or not command:
        return {}
    snapshot = load(project)
    if snapshot["status"] != "loaded":
        return {}
    return {
        row["test"]: copy.deepcopy(row)
        for row in snapshot["document"]["pre_existing"]
        if row["base"] == base and row["command"] == command and row["framework"] == framework_name
    }


def record_observations(
    project: Path | str,
    *,
    base: str,
    command: str,
    framework_name: str | None,
    quarantine_rows: Iterable[dict] = (),
    pre_existing_rows: Iterable[dict] = (),
    clean_passed_tests: Iterable[str] = (),
    run_name: str | None = None,
) -> None:
    """Atomically persist newly confirmed flaky/pre-existing facts and advance clean base horizons."""
    project = Path(project).resolve()
    q_rows = list(quarantine_rows)
    p_rows = list(pre_existing_rows)
    clean_tests = set(clean_passed_tests)
    if not q_rows and not p_rows and not clean_tests:
        return
    if not base or not command:
        return
    if run_name is not None and _RUN_NAME.fullmatch(run_name) is None:
        run_name = None
    package = _entry_package(command)
    now = _now()
    workspaces.keep_out_of_git(project)

    def change(previous: dict | None) -> dict:
        doc = previous or _empty()
        if p_rows:
            by_key = {(r["base"], r["command"], r["framework"], r["test"]): r for r in doc["pre_existing"]}
            for row in p_rows:
                test = row.get("test")
                runs = list(row.get("base_runs") or [])
                if not isinstance(test, str) or not runs:
                    continue
                key = (base, command, framework_name, test)
                by_key.pop(key, None)
                by_key[key] = {
                    "base": base,
                    "command": command,
                    "framework": framework_name,
                    "test": test,
                    "base_runs": runs,
                    "recorded_at": now,
                    "run": run_name,
                }
            doc["pre_existing"] = list(by_key.values())[-MAX_PRE_EXISTING:]

        if q_rows or clean_tests:
            q_by_key: dict[tuple[str | None, str | None, str], dict[str, Any]] = {
                (r["framework"], r["package"], r["test"]): r for r in doc["quarantine"]
            }
            if clean_tests:
                for q_key, existing in list(q_by_key.items()):
                    if (
                        q_key[0] == framework_name
                        and q_key[1] == package
                        and existing["test"] in clean_tests
                        and base != existing["base_commit"]
                        and base not in existing["seen_bases"]
                        and len(existing["seen_bases"]) < MAX_QUARANTINE_BASES
                    ):
                        existing["seen_bases"].append(base)
            for row in q_rows:
                test = row.get("test")
                base_runs = list(row.get("base_runs") or [])
                cand_runs = list(row.get("candidate_runs") or [])
                reason = row.get("reason")
                if not isinstance(test, str) or not isinstance(reason, str) or not base_runs or not cand_runs:
                    continue
                q_key = (framework_name, package, test)
                q_by_key.pop(q_key, None)
                q_by_key[q_key] = {
                    "test": test,
                    "framework": framework_name,
                    "package": package,
                    "command": command,
                    "reason": reason[:MAX_TEXT],
                    "base_commit": base,
                    "base_runs": base_runs,
                    "candidate_runs": cand_runs,
                    "recorded_at": now,
                    "run": run_name,
                    "seen_bases": [base],
                }
            doc["quarantine"] = list(q_by_key.values())[-MAX_QUARANTINED:]
        return doc

    memory.update_document(project, FILENAME, sanitize, change, limit=MAX_BYTES)

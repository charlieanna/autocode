"""Reject transient reviewer writes recorded by OpenCode's workspace snapshots.

This complements the runner's before/after check. It is detection, not a shell
sandbox: missing native snapshots and writes outside the workspace are not
covered. Never interpret model text or a tool's output as filesystem evidence.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

try:
    from . import autocode_opencode_snapshots as native_snapshots
    from . import autocode_review_job as review_job
    from . import autocode_stage_access as stage_access
    from .autocode_util import Paused, digest
except ImportError:
    import autocode_opencode_snapshots as native_snapshots
    import autocode_review_job as review_job
    import autocode_stage_access as stage_access
    from autocode_util import Paused, digest


def assert_unchanged_review(record, *, workspace=None):
    """Refuse a read-only native OpenCode report after any recorded source drift."""
    if (
        record.get("engine") != "opencode"
        or record.get("output_mode", "opencode_events") != "opencode_events"
        or (record.get("role") == "terra" and not record.get("report_only"))
    ):
        return
    baselines, changes = {}, set()
    events = Path(record["events"])
    if not events.is_file():
        return  # The provider's report parser still requires its terminal evidence.
    with events.open() as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            part, session = row.get("part"), row.get("sessionID")
            if (
                row.get("type") not in ("step_start", "step_finish")
                or not isinstance(part, dict)
                or part.get("type") != row["type"].replace("_", "-")
                or not isinstance(session, str)
                or not session
            ):
                continue
            snapshot = part.get("snapshot")
            if not isinstance(snapshot, str) or not snapshot:
                continue
            baseline = baselines.setdefault(session, snapshot)
            if snapshot != baseline:
                changes.add((baseline, snapshot))
    if not changes:
        return
    protected = _protected_paths(record)
    if workspace is not None and native_snapshots.workspace(record) != Path(workspace).resolve():
        protected = None
    if protected is not None and native_snapshots.only_new_artifacts(
        record, sorted(changes), protected, stage_access.opencode_additions(review_job.STAGE)
    ):
        return
    raise Paused(
        "PAUSED_STALE_VALIDATION",
        "Repository changed during read-only review in OpenCode's recorded snapshots, "
        "even if restored before the final report; inspect saved events and revalidate",
    )


def _protected_paths(record):
    """Only a normal Code Review may add evidence; its source capture binds existing paths."""
    if record.get("stage") != review_job.STAGE or record.get("report_only"):
        return None
    try:
        capture = record["job_source"]
        raw = Path(capture["capture"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != capture["capture_hash"]:
            return None
        original = json.loads(raw)["original"]
        files = original["files"]
        if (
            not isinstance(files, dict)
            or not all(isinstance(name, str) for name in files)
            or digest(original) != capture["before_identity"]
            or sorted(files) != capture["paths"]
        ):
            return None
        return set(files)
    except (KeyError, TypeError, ValueError, OSError):
        return None


def prepare_opencode_snapshots(workspace, *, record=None, env=None):
    """Keep native Git snapshots aligned with the runner's runtime exclusions.

    Append only local Git exclusions, preserving existing user rules and source
    files. This runs before provider admission, including for a Git worktree.
    """
    # Written only here, before provider admission; the native-tree reader uses
    # this stage-record metadata on immediate parsing and saved-report recovery.
    if record is not None and record.get("stage") == review_job.STAGE and not record.get("report_only"):
        record["opencode_snapshot_context"] = native_snapshots.context(workspace, env)
    try:
        name = subprocess.check_output(
            ["git", "rev-parse", "--path-format=absolute", "--git-path", "info/exclude"],
            cwd=workspace,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        path = Path(name)
        existing = path.read_text(errors="surrogateescape") if path.is_file() else ""
        missing = [
            rule
            for rule in ("/.autocode/", "/.autocode-ui/", "__pycache__/", "*.pyc")
            if rule not in existing.splitlines()
        ]
        if missing:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", errors="surrogateescape") as sink:
                sink.write(("\n" if existing and not existing.endswith("\n") else "") + "\n".join(missing) + "\n")
    except (OSError, subprocess.CalledProcessError) as error:
        raise Paused(
            "PAUSED_OPENCODE_SNAPSHOT",
            "Cannot align native OpenCode snapshots with runtime exclusions; no provider was launched",
        ) from error

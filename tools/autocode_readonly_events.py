"""Reject transient reviewer writes recorded by OpenCode's workspace snapshots.

This complements the runner's before/after check. It is detection, not a shell
sandbox: missing native snapshots and writes outside the workspace are not
covered. Never interpret model text or a tool's output as filesystem evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

try:
    from . import autocode_agent_env as agent_env
    from . import autocode_opencode_snapshots as native_snapshots
    from . import autocode_review_job as review_job
    from . import autocode_stage_access as stage_access
    from .autocode_util import Paused, digest
except ImportError:
    import autocode_agent_env as agent_env
    import autocode_opencode_snapshots as native_snapshots
    import autocode_review_job as review_job
    import autocode_stage_access as stage_access
    from autocode_util import Paused, digest


records_native_snapshots = native_snapshots.records_native_snapshots  # the launch site and the guard share it


def assert_unchanged_review(record, *, workspace=None):
    """Refuse a read-only native OpenCode report after any recorded source drift."""
    if (
        not records_native_snapshots(record.get("engine"), record.get("output_mode", "opencode_events"))
        or (record.get("role") == "terra" and not record.get("report_only"))
        or (record.get("stage") in stage_access.WRITING_JOBS and not record.get("report_only"))
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


def launch_excludes(workspace, engine, adapter, environment=None, *, dry_run=False):
    """The snapshot excludes environment for a launch, or {} when it records no native snapshots.

    Decided by the same predicate as the guard, from the engine and the adapter's output mode, before the
    provider environment is built: an isolated launch's base ``environment`` gains the entries in place, so its
    allowlist (which passes the GIT_CONFIG_* set through whole) carries them; a plain launch merges the returned
    entries at prepare_opencode_snapshots."""
    if dry_run or not records_native_snapshots(engine, getattr(adapter, "OUTPUT", "opencode_events")):
        return {}
    base = environment if environment is not None else agent_env.scrubbed(os.environ)
    entries = snapshot_excludes(workspace, base)
    if environment is not None:
        environment.update(entries)
    return entries


def prepare_opencode_snapshots(workspace, *, record=None, env=None, excludes=None):
    """Keep the project's own Git view aligned with the runner's runtime exclusions.

    Append only local Git exclusions, preserving existing user rules and source
    files. This runs before provider admission, including for a Git worktree.
    The provider's snapshot repository does not read this file (it has its own
    git dir): snapshot_excludes supplies what that repository reads.
    """
    if record is not None and not records_native_snapshots(
        record.get("engine"), record.get("output_mode", "opencode_events")
    ):
        return  # Codex and report_file tools record no native snapshots; nothing to align.
    # Written only here, before provider admission; the native-tree reader uses
    # this stage-record metadata on immediate parsing and saved-report recovery.
    if record is not None and record.get("stage") == review_job.STAGE and not record.get("report_only"):
        record["opencode_snapshot_context"] = native_snapshots.context(workspace, env, native_snapshots.app(record))
    try:
        name = subprocess.check_output(
            ["git", "rev-parse", "--path-format=absolute", "--git-path", "info/exclude"],
            cwd=workspace,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        path = Path(name)
        existing = path.read_text(errors="surrogateescape") if path.is_file() else ""
        missing = [rule for rule in native_snapshots.EXCLUDES if rule not in existing.splitlines()]
        if missing:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", errors="surrogateescape") as sink:
                sink.write(("\n" if existing and not existing.endswith("\n") else "") + "\n".join(missing) + "\n")
    except (OSError, subprocess.CalledProcessError) as error:
        raise Paused(
            "PAUSED_OPENCODE_SNAPSHOT",
            "Cannot align native OpenCode snapshots with runtime exclusions; no provider was launched",
        ) from error
    if excludes and env is not None:
        env.update(excludes)  # the entries launch_excludes computed for this launch (#1630)


def _global_ignore(workspace, env):
    """The user's global ignore file as git would find it in ``env``: core.excludesFile, else the XDG default."""
    try:
        configured = subprocess.run(
            ["git", "config", "--get", "core.excludesFile"],
            cwd=workspace,
            env={key: env[key] for key in ("PATH", "HOME", "XDG_CONFIG_HOME") if key in env},
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        configured = ""
    home = Path(env.get("HOME") or Path.home())
    config = env.get("XDG_CONFIG_HOME")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.append((Path(config) if config else home / ".config") / "git" / "ignore")
    return next((path for path in candidates if path.is_file()), None)


def snapshot_excludes(workspace, env):
    """Write the excludes file a provider's snapshot repository reads, and return the environment that names it.

    The snapshot repository is a separate git dir with core.worktree set, so it never reads the project's
    info/exclude written above; until now a read-only stage that compiled Python in the workspace changed its own
    snapshot and was paused as a source edit (#1630). core.excludesFile replaces git's default global ignore, so
    the user's rules come first. The file lives under .autocode, which ignores itself.
    """
    directory = Path(workspace).resolve() / ".autocode"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "snapshot-excludes"
    lines = []
    user_rules = _global_ignore(workspace, env)
    if user_rules is not None:
        lines += user_rules.read_text(errors="surrogateescape").splitlines()
    lines += [rule for rule in native_snapshots.EXCLUDES if rule not in lines]
    content = "".join(line + "\n" for line in lines)
    if not path.is_file() or path.read_text(errors="surrogateescape") != content:
        staging = path.with_name(path.name + ".tmp")
        staging.write_text(content, errors="surrogateescape")
        staging.replace(path)
    return native_snapshots.excludes_env(path, env)

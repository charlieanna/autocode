"""Reject transient reviewer writes recorded by OpenCode's workspace snapshots.

This complements the runner's before/after check. It is detection, not a shell
sandbox: missing native snapshots and writes outside the workspace are not
covered. Never interpret model text or a tool's output as filesystem evidence.

A review may *create* permitted evidence under ``review/`` (the Reviewer's
delivered tests). Those additions change OpenCode's snapshot id and must not be
mistaken for source drift (#332). When two recorded snapshot ids differ, the
transition is accepted only when Git reports the delta as additions under the
permitted prefixes and nothing else. Edits, deletions and restorations of
pre-existing files — including transient writes — still pause.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

try:
    from .autocode_util import Paused
except ImportError:
    from autocode_util import Paused

# Matches autocode_review_job.ALLOWED_PREFIXES / TESTS_PREFIX without importing
# the job (this module is used by the runner before the job table).
REVIEW_PERMITTED_ADDITIONS = ("review/",)


def _permitted_additions_for(record) -> tuple[str, ...]:
    stage = str(record.get("stage") or "").removesuffix("_report_repair")
    return REVIEW_PERMITTED_ADDITIONS if stage == "review_change" else ()


def _snapshot_delta_is_permitted(workspace, baseline, snapshot, prefixes) -> bool:
    """True when Git can prove the snapshot change is only new files under prefixes."""
    if not prefixes or not workspace:
        return False
    try:
        raw = subprocess.check_output(
            ["git", "diff", "--name-status", "-z", "--no-renames", baseline, snapshot],
            cwd=workspace, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return False
    # -z name-status: STATUS\0path\0 repeated; --no-renames keeps every row two fields.
    fields = raw.decode("utf-8", "replace").split("\0")
    rows = [(fields[i], fields[i + 1]) for i in range(0, len(fields) - 1, 2) if fields[i]]
    if not rows:
        return False
    return all(status == "A" and path.startswith(prefixes) for status, path in rows)


def assert_unchanged_review(record, *, workspace=None):
    """Refuse a read-only native OpenCode report after any recorded source drift."""
    if (record.get("engine") != "opencode"
            or record.get("output_mode", "opencode_events") != "opencode_events"
            or (record.get("role") == "terra" and not record.get("report_only"))):
        return
    permitted = _permitted_additions_for(record)
    baselines = {}
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
            if (row.get("type") not in ("step_start", "step_finish")
                    or not isinstance(part, dict)
                    or part.get("type") != row["type"].replace("_", "-")
                    or not isinstance(session, str) or not session):
                continue
            snapshot = part.get("snapshot")
            if not isinstance(snapshot, str) or not snapshot:
                continue
            baseline = baselines.setdefault(session, snapshot)
            if snapshot == baseline:
                continue
            if _snapshot_delta_is_permitted(workspace, baseline, snapshot, permitted):
                # A delivered review test is now the session's baseline.
                baselines[session] = snapshot
                continue
            raise Paused("PAUSED_STALE_VALIDATION",
                         "Repository changed during read-only review in OpenCode's recorded snapshots, "
                         "even if restored before the final report; inspect saved events and revalidate")


def prepare_opencode_snapshots(workspace):
    """Keep native Git snapshots aligned with the runner's runtime exclusions.

    Append only local Git exclusions, preserving existing user rules and source
    files. This runs before provider admission, including for a Git worktree.
    """
    try:
        name = subprocess.check_output(["git", "rev-parse", "--path-format=absolute", "--git-path", "info/exclude"],
                                       cwd=workspace, text=True, stderr=subprocess.DEVNULL).strip()
        path = Path(name)
        existing = path.read_text(errors="surrogateescape") if path.is_file() else ""
        missing = [rule for rule in ("/.autocode/", "/.autocode-ui/", "__pycache__/", "*.pyc")
                   if rule not in existing.splitlines()]
        if missing:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", errors="surrogateescape") as sink:
                sink.write(("\n" if existing and not existing.endswith("\n") else "")
                           + "\n".join(missing) + "\n")
    except (OSError, subprocess.CalledProcessError) as error:
        raise Paused("PAUSED_OPENCODE_SNAPSHOT",
                     "Cannot align native OpenCode snapshots with runtime exclusions; no provider was launched") from error

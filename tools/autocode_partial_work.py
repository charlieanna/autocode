"""Carry a stopped Builder's exact source delta into its recovery context (#3290).

Git trees capture source bytes through a separate index; neither HEAD nor working
files are restored or changed. The inline patch is bounded, the complete retained
tree is pinned, and none of this grants verification or completion authority.
"""

import hashlib
import subprocess
import tempfile

try:
    from . import autocode_code_checkpoints as checkpoints
    from . import autocode_recovery_context as recovery_context
    from . import autocode_util as util
except ImportError:
    import autocode_code_checkpoints as checkpoints
    import autocode_recovery_context as recovery_context
    import autocode_util as util

MAX_PATCH_BYTES = 32 * 1024


def _milestones(task):
    return sorted(task.get("milestone_ids") or ([task["milestone_id"]] if task.get("milestone_id") else []))


def _pin(workspace, kind, commit):
    # Pin the generated Git object without putting filesystem names in the ref.
    ref = f"refs/autocode/partial-work/{kind}/{commit}"
    checkpoints.git(workspace, "update-ref", ref, commit)


def pending(state, revision):
    """Keep an unfinished Builder delta across intervening read-only reviews.

    A later Builder attempt consumes it. Another timeout publishes that attempt's
    cumulative replacement; a changed source or contract never borrows it.
    """
    latest = next(
        (
            row
            for row in reversed(state.get("stages") or [])
            if isinstance(row, dict)
            and (row.get("original_stage") or row.get("stage")) == "builder"
            and not row.get("report_only")
            and not row.get("dry_run")
        ),
        None,
    )
    if latest is None or type(latest.get("iteration")) is not int or not latest.get("output"):
        return None
    for row in reversed(state.get("automatic_timeout_recoveries") or []):
        packet = row.get("partial_work") or {}
        if (
            packet.get("available")
            and packet.get("source_revision") == revision
            and (packet.get("contract_hash") == (state.get("goal_contract") or {}).get("hash"))
        ):
            task = state.get("current_task") or {}
            members = packet.get("milestone_ids") or []
            if (
                members != _milestones(task)
                or not members
                and (not task.get("id") or packet.get("task_id") != task.get("id"))
            ):
                return None
            if any(
                progress.get("accepted")
                and progress.get("contract_hash") == packet["contract_hash"]
                and set(members) <= set(progress.get("milestone_ids") or [progress.get("id")])
                for progress in (state.get("milestone_progress") or {}).values()
            ):
                return None
            if recovery_context._attempt_id(latest) != row.get("attempt_id"):
                return None
            return packet
    return None


def capture_start(record, before, workspace, run_dir, *, state=None):
    """Save the exact launch tree before a Builder starts, including dirty source."""
    try:
        commit = checkpoints.capture_tree(workspace, before, run_dir)
        _pin(workspace, "start", commit)
        inherited = pending(state, before["revision"]) if state is not None else None
        task = (state or {}).get("current_task") or {}
        base = inherited["before_commit"] if inherited else commit
        record["partial_work_start"] = {
            "commit": commit,
            "source_revision": before["revision"],
            "repair_base_commit": base,
            "milestone_ids": _milestones(task),
            "repair_base_reason": "Preserve the unfinished Builder's baseline"
            if inherited
            else "No matching unfinished Builder delta; capture this launch's exact tree",
        }
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        record["partial_work_start"] = {"unavailable_reason": str(error)}


def recovery(record, before, after, workspace, run_dir):
    """Materialize the stopped attempt's delta for the existing recovery prompt.

    Old attempts without a captured tree keep their evidence gap explicit. Only
    original source bytes may supply the old side of a patch; HEAD is not a
    substitute for an attempt that launched with dirty files.
    """
    try:
        start = record.get("partial_work_start") or {}
        commit = start.get("commit")
        if not commit or start.get("source_revision") != before["revision"]:
            return {"available": False, "reason": start.get("unavailable_reason") or "No launch tree was captured"}
        if checkpoints.tree_files(workspace, commit) != checkpoints.content_files(before):
            return {"available": False, "reason": "Launch tree does not match the original source snapshot"}
        retained = checkpoints.capture_tree(workspace, after, run_dir)
        _pin(workspace, "retained", retained)
        base = start.get("repair_base_commit", commit)
        changed = util.changed_paths({"files": checkpoints.tree_files(workspace, base)}, after)
        with tempfile.TemporaryFile(dir=run_dir) as output:
            result = subprocess.run(
                checkpoints._git_argv(
                    workspace,
                    "--literal-pathspecs",
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--no-color",
                    "--no-renames",
                    base,
                    retained,
                    "--",
                    *changed,
                ),
                stdout=output,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            if result.returncode:
                raise ValueError(result.stderr.decode(errors="replace")[:1000])
            size = output.tell()
            output.seek(0)
            digest = hashlib.file_digest(output, "sha256").hexdigest()
            output.seek(0)
            patch = output.read(MAX_PATCH_BYTES).decode(errors="replace")
        return {
            "available": True,
            "verification": "unverified",
            "source_revision": after["revision"],
            "before_commit": base,
            "base_reason": start.get("repair_base_reason"),
            "retained_commit": retained,
            "contract_hash": record.get("contract_hash"),
            "task_id": record.get("task_id"),
            "milestone_ids": start.get("milestone_ids", []),
            "changed_files": changed,
            "deleted_files": [name for name in changed if after["files"].get(name, "deleted") == "deleted"],
            "patch": patch,
            "patch_bytes": size,
            "patch_sha256": digest,
            "patch_truncated": size > MAX_PATCH_BYTES,
            "instruction": "Continue from this retained source delta rather than restart. Review deletions; "
            "the source is unverified and the complete retained tree remains available if the patch is truncated.",
        }
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        return {"available": False, "reason": str(error)}

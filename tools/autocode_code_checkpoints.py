"""Immutable, source-bound code checkpoints owned by the runner.

Writes only ``code_checkpoints`` (an append-only source receipt list, read by the
status view and checkpoint CLI) and ``kept_code`` (the run's newest kept code beyond
its saved steps, where ``checkpoint --all`` ends). A checkpoint is a Git object, not a claim of
acceptance. The real index, HEAD and working files are never changed. Metadata
from older runs cannot be upgraded into missing source snapshots.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_source_scope as source_scope
    from . import autocode_source_snapshot as source_snapshot
except ImportError:
    import autocode_source_scope as source_scope
    import autocode_source_snapshot as source_snapshot


import contextlib
import hashlib
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_contract_identity as contract
    from . import autocode_repair_provenance as repair_provenance
    from . import autocode_util as util
except ImportError:
    import autocode_contract_identity as contract
    import autocode_repair_provenance as repair_provenance
    import autocode_util as util


def approved(state: RunState):
    try:
        return contract.approved(state)
    except (KeyError, TypeError, ValueError):
        return False


def _git_argv(workspace, *args):
    return ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-C", str(workspace), *args]


def git(workspace, *args, env=None, data=None):
    proc = subprocess.run(_git_argv(workspace, *args), input=data, capture_output=True, env=util.git_environment(env))
    if proc.returncode:
        raise ValueError(proc.stderr.decode(errors="replace").strip() or "Git checkpoint operation failed")
    return proc.stdout


def content_files(snapshot):
    return {name: value for name, value in snapshot["files"].items() if value != "deleted"}


def _read_blob_identity(output, oid, mode):
    """Read one length-framed batch reply without retaining a whole regular blob."""
    header = output.readline(256)
    fields = header[:-1].split(b" ")
    if (
        not header.endswith(b"\n")
        or len(fields) != 3
        or fields[0] != oid.encode()
        or fields[1] != b"blob"
        or not fields[2].isdigit()
    ):
        raise ValueError("Git checkpoint batch returned an invalid blob header")
    remaining = int(fields[2])
    digest = hashlib.sha256()
    target = bytearray() if mode == "120000" else None
    while remaining:
        data = output.read(min(remaining, 64 * 1024))
        if not data:
            raise ValueError("Git checkpoint batch returned a truncated blob")
        remaining -= len(data)
        if target is None:
            digest.update(data)
        else:
            target.extend(data)
    if output.read(1) != b"\n":
        raise ValueError("Git checkpoint batch returned an invalid blob terminator")
    return (
        "symlink:" + target.decode()
        if target is not None
        else ("executable:" if mode == "100755" else "") + digest.hexdigest()
    )


def tree_files(workspace, revision):
    """Compare actual blobs, modes and links through one streamed Git batch."""
    entries = []
    for entry in git(workspace, "ls-tree", "-rz", revision).split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        if kind != "blob":
            raise ValueError("Checkpoint restoration requires ordinary files; nested Git repositories are unsupported")
        entries.append((name.decode(), mode, oid))
    result = {}
    if not entries:
        return result
    # One request and response at a time avoids full-tree buffering and pipe
    # backpressure. Stderr uses a file so it cannot block a large stdout reply.
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(
            _git_argv(workspace, "cat-file", "--batch"), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors
        )
        try:
            for name, mode, oid in entries:
                proc.stdin.write(oid.encode() + b"\n")
                proc.stdin.flush()
                result[name] = _read_blob_identity(proc.stdout, oid, mode)
            proc.stdin.close()
            if proc.stdout.read(1):
                raise ValueError("Git checkpoint batch returned unexpected trailing output")
            if proc.wait():
                errors.seek(0)
                raise ValueError(errors.read().decode(errors="replace").strip() or "Git checkpoint operation failed")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            for pipe in (proc.stdin, proc.stdout):
                with contextlib.suppress(OSError):
                    pipe.close()
    return result


def capture_tree(workspace, source, directory):
    """Build from a separate index and verify its blobs against the captured source."""
    files = content_files(source)
    if any(value.startswith(("submodule:", "uninitialized-submodule")) for value in files.values()):
        raise ValueError("Nested Git repositories cannot yet be restored as a code checkpoint")
    with tempfile.TemporaryDirectory(prefix="code-checkpoint-", dir=directory) as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git(workspace, "read-tree", "--empty", env=env)
        if files:
            # NUL-delimited literal paths support spaces, newlines and leading dashes.
            git(
                workspace,
                "--literal-pathspecs",
                "add",
                "-f",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
                env=env,
                data=b"\0".join(name.encode() for name in files) + b"\0",
            )
        tree = git(workspace, "write-tree", env=env).decode().strip()
        if (
            tree_files(workspace, tree) != files
            or source_snapshot.snapshot(workspace, paths=source.get("source_paths", ())) != source
        ):
            raise ValueError("Source changed during checkpoint capture, or Git filters changed the captured content")
        commit = (
            git(
                workspace,
                "-c",
                "user.name=AutoCode",
                "-c",
                "user.email=autocode@localhost",
                "commit-tree",
                tree,
                "-p",
                source["head"],
                data=b"AutoCode saved code checkpoint\n",
            )
            .decode()
            .strip()
        )
    return commit


def _saved_file(path):
    return type(path) is str and bool(path) and Path(path).is_file()


def _saved_original_report(original):
    output = original.get("output")
    if _saved_file(output) or _saved_file(original.get("response_text")):
        return True
    # Older OpenCode attempts saved the terminal response under this exact stem
    # before the repair handler recorded response_text on the pending copy.
    return (
        original.get("engine") == "opencode"
        and type(output) is str
        and bool(output)
        and _saved_file(str(Path(output).with_suffix(".response.txt")))
    )


def builder_execution(state: RunState, accepted, implementation):
    """Resolve a repaired handoff to its runner-observed implementation step.

    A report-only turn has no source changes of its own. Its accepted, hash-pinned
    repair receipt identifies the original command; model-declared paths are never
    used. Missing or ambiguous provenance cannot create a checkpoint.
    """
    if not accepted.get("report_only") and accepted.get("stage") in ("builder", "orchestrator"):
        return accepted
    stage = accepted.get("original_stage")
    receipts = [
        row
        for row in state.get("report_repair_history", [])
        if row.get("result") == "accepted" and row.get("repair") == accepted
    ]
    if (
        not accepted.get("report_only")
        or stage not in ("builder", "orchestrator")
        or accepted.get("stage") != stage + "_report_repair"
        or type(accepted.get("exit_code")) is not int
        or accepted["exit_code"] != 0
        or accepted.get("rejected")
        or accepted.get("abandoned")
        or accepted.get("timed_out")
        or accepted.get("changed_files") != []
        or len(receipts) != 1
        or type(accepted.get("applied_original_events")) is not str
        or not accepted["applied_original_events"]
    ):
        raise ValueError("Accepted Builder repair lacks unique read-only provenance")
    receipt = repair_provenance.verify_accepted_repair(state, accepted)
    originals = [
        row
        for row in state.get("stages", [])
        if row.get("output") == receipt.get("original_output")
        and row.get("events") == accepted["applied_original_events"]
    ]
    if len(originals) != 1:
        raise ValueError("Accepted Builder repair lacks a unique original execution")
    original = originals[0]
    if (
        original.get("stage") != stage
        or original.get("report_only")
        or type(original.get("exit_code")) is not int
        or original["exit_code"] != 0
        or original.get("abandoned")
        or original.get("timed_out")
        or any(
            type(accepted.get(key)) is not str or not accepted[key] or accepted[key] != implementation.get(key)
            for key in ("task_id", "contract_hash", "source_revision")
        )
        or any(
            original.get(key) != accepted.get(key)
            for key in (
                "task_id",
                "contract_hash",
                "source_revision",
                "role",
                "iteration",
                "schema",
                "criteria_revision",
            )
        )
        or not original.get("role")
        or not original.get("schema")
        or type(original.get("iteration")) is not int
        or not _saved_file(original.get("events"))
        or not _saved_original_report(original)
        or type(original.get("changed_files")) is not list
        or any(type(path) is not str or not path for path in original["changed_files"])
    ):
        raise ValueError("Accepted Builder repair has mismatched original execution provenance")
    return original


def _integrated_batch(state: RunState, task, implementation):
    """The parallel batch whose verified integration is this implementation, with its built members.

    The orchestrator applies the members' runner-observed patch as one source change, so no
    Builder stage row carries the integrated revision; the saved batch record is its provenance.
    """
    for batch in reversed(state.get("orchestration_history") or []):
        if (
            batch.get("status") != "INTEGRATED"
            or "task-" + str(batch.get("id")) != task["id"]
            or batch.get("contract_hash") != implementation["contract_hash"]
            or (batch.get("expected") or {}).get("revision") != implementation["source_revision"]
        ):
            continue
        members = [
            {"milestone_id": row.get("milestone_id"), "changed_files": deepcopy(row.get("changed_files") or [])}
            for row in batch.get("workers") or []
            if row.get("status") == "BUILT"
        ]
        return (batch, members) if members else None
    return None


def update(state: RunState, run_dir):
    """Called from the runner's durable writer at every save (``autocode_status.persist``).

    It records an applied Builder step or batch integration as a saved step, then the run's newest
    kept code beyond it (``_record_kept``), whichever stage kept it."""
    _record_step(state, run_dir)
    _record_kept(state, run_dir)


def _record_step(state: RunState, run_dir):
    """A saved step: an applied Builder step or batch integration, one row per implementation revision."""
    implementation = state.get("implementation") or {}
    task = state.get("current_task") or {}
    if (
        not implementation.get("source_revision")
        or not task.get("id")
        or implementation.get("task_id") != task["id"]
        or implementation.get("contract_hash") != (state.get("goal_contract") or {}).get("hash")
        or state.get("active_stage")
        or not approved(state)
    ):
        return
    rows = state.setdefault("code_checkpoints", [])
    ident = (
        "code-"
        + util.digest({key: implementation.get(key) for key in ("task_id", "contract_hash", "source_revision")})[:24]
    )
    existing = next((row for row in rows if row["id"] == ident), None)
    if existing is None:
        stage: dict = next(
            (
                row
                for row in reversed(state.get("stages") or [])
                if row.get("task_id") == task["id"]
                and row.get("source_revision") == implementation["source_revision"]
                and row.get("stage", "").removesuffix("_report_repair") in ("builder", "orchestrator")
                and not row.get("rejected")
                and row.get("exit_code") == 0
            ),
            {},
        )
        integrated = None if stage else _integrated_batch(state, task, implementation)
        if not stage and not integrated:
            return
        base = {
            "id": ident,
            "task_id": task["id"],
            "contract_hash": implementation["contract_hash"],
            "source_revision": implementation["source_revision"],
            "iteration": state.get("iteration"),
            "at": (integrated[0] if integrated else stage).get("finished_at") or util.now(),
            "label": task.get("objective") or "Saved code change",
            # A batch served several milestones; its members name each one with its own files.
            "milestone_id": None if integrated else task.get("milestone_id"),
            "changed_files": [],
            "finding_ids": [row["id"] for row in state.get("findings_ledger", []) if row.get("id")],
            "recorded_check": None,
        }
        if integrated:
            base["members"] = integrated[1]
        try:
            changed = (
                integrated[0].get("changed_files")
                if integrated
                else builder_execution(state, stage, implementation).get("changed_files")
            )
            if not changed:
                return
            base["changed_files"] = deepcopy(changed)
            source = source_scope.snapshot(state["workspace"], state)
            if source["revision"] != implementation["source_revision"]:
                return
            directory = Path(run_dir) / "code-checkpoints"
            if directory.is_symlink():
                raise ValueError("Checkpoint storage must not be a symlink")
            directory.mkdir(parents=True, exist_ok=True)
            commit = capture_tree(state["workspace"], source, directory)
            manifest = {
                **base,
                "commit": commit,
                "source": source,
                "findings_ledger": deepcopy(state.get("findings_ledger", [])),
                "run_dir": str(Path(run_dir).resolve()),
                "workspace": state["workspace"],
            }
            path = directory / (ident + ".json")
            # The immutable manifest is published before its reference in state.
            if path.exists():
                saved = util.read(path)
                if saved["source"] != source or saved["task_id"] != task["id"]:
                    raise ValueError("An existing checkpoint receipt conflicts with this step")
                manifest, commit = saved, saved["commit"]
            else:
                util.atomic_json(path, manifest)
            ref = "refs/autocode/code-checkpoints/" + util.digest(str(Path(run_dir).resolve()))[:20] + "/" + ident
            git(state["workspace"], "update-ref", ref, commit)
            base.update(commit=commit, receipt=str(path), receipt_sha256=util.file_hash(path), available=True)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            # Saving work must not fail just because optional rollback storage is unavailable.
            # A failed receipt is visible, retained, and never presented as restorable.
            base.update(available=False, reason=str(error))
        rows.append(base)
        existing = base
    validation = state.get("validation") or {}
    if (
        validation.get("task_id") == task["id"]
        and validation.get("contract_hash") == existing["contract_hash"]
        and validation.get("source_revision") == existing["source_revision"]
    ):
        existing["recorded_check"] = {
            "verdict": validation.get("verdict"),
            "at": next(
                (
                    r.get("finished_at")
                    for r in reversed(state.get("stages", []))
                    if r.get("stage") in ("tester", "review_checkpoint")
                    and r.get("source_revision") == existing["source_revision"]
                ),
                None,
            ),
            "scope": "Historical independent verdict; fresh checks are required after restoration.",
        }


COMPLETE_STATUSES = ("TASK_COMPLETE", "COMPLETE")


def _receipt_source(record, run_dir):
    """The saved source of a step or kept capture, read from its hash-pinned receipt in this run."""
    path = Path(record["receipt"])
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(Path(run_dir).resolve()):
        raise ValueError("Saved code receipt must be a regular file inside its run directory")
    if util.file_hash(path) != record["receipt_sha256"]:
        raise ValueError("The saved code checkpoint receipt changed")
    return util.read(path)["source"]


def kept_end(state: RunState):
    """Where the run's saved code ends: a kept capture made after the newest available step, else that step.

    Returns (kind, record) with kind "kept", "step" or "start" (no saved code yet, record None). A marker
    names a step, so the step itself is the end. The checkpoint CLI reads the same answer."""
    rows = [row for row in state.get("code_checkpoints") or [] if row.get("available")]
    newest = rows[-1]["id"] if rows else None
    kept = state.get("kept_code") or {}
    if kept.get("available") and not kept.get("marker") and kept.get("after") == newest:
        return "kept", kept
    return ("step", rows[-1]) if rows else ("start", None)


def _record_kept(state: RunState, run_dir):
    """Capture the code the run keeps beyond its newest saved step (#3945, #3933).

    Any stage can keep work without saving a step: a Builder retry, Tester-retained tests, a combined
    review, the acceptance-test writer, an edit accepted at a pause. Every durable save calls this, so
    the whole-run diff (``checkpoint --all``) reflects what the run keeps, whichever stage kept it. Only
    a settled run is captured: never while a stage, runner check or batch is active, or before the plan
    is approved. Paths the kept work removed are carried forward while they stay absent, so a later end
    still lists them. A completed run fixes its delivery at its first complete save; a save outside a
    complete status releases it, so a follow-up records its own. A failed capture is recorded with its
    reason and never fails the save.
    """
    kept = state.get("kept_code")
    complete = state.get("status") in COMPLETE_STATUSES
    if kept and kept.get("final"):
        if complete:
            return  # The delivered code stays as the run completed it; later edits are not part of it.
        kept["final"] = False
    if any(state.get(key) for key in ("active_stage", "active_runner_check", "orchestration_batch")) or not approved(
        state
    ):
        return
    rows = [row for row in state.get("code_checkpoints") or [] if row.get("available")]
    if not state.get("workspace") or (not rows and not (state.get("implementation") or {}).get("source_revision")):
        return
    newest = rows[-1] if rows else None
    run_dir = Path(run_dir).resolve()
    workspace = state["workspace"]
    revision = None
    try:
        source = source_scope.snapshot(workspace, state)
        revision = source["revision"]
        kind, end = kept_end(state)
        if rows:
            start = _receipt_source(rows[0], run_dir)["head"]
        else:
            start = (kept or {}).get("start") or source["head"]
        if kind == "start":
            # Only the run's source inventory: tracked names it leaves out (.vscode/) are never a change.
            end_files = {
                name: value for name, value in tree_files(workspace, start).items() if not util.inventory_excluded(name)
            }
        else:
            end_files = content_files(_receipt_source(end, run_dir))
        files = content_files(source)
        removed = sorted(
            {name for name in (kept or {}).get("removed") or [] if name not in files}
            | {name for name, value in source["files"].items() if value == "deleted"}
            | {name for name in end_files if name not in files}
        )
        common = {
            "start": start,
            "after": newest["id"] if newest else None,
            "removed": removed,
            "final": complete,
            "at": util.now(),
            "reason": None,
        }
        if files != end_files:
            state["kept_code"] = {**common, **_capture_kept(state, run_dir, source, common)}
        elif kind == "kept":
            if complete:
                end["final"] = True
        elif (
            kind == "step"
            and newest is not None
            and (complete or (kept and (not kept.get("marker") or kept.get("id") != newest["id"])))
        ):
            # The source is the newest step itself: name it, so the carried removals and a completed
            # delivery end there rather than at an older capture.
            state["kept_code"] = {
                **common,
                "id": newest["id"],
                "source_revision": newest.get("source_revision"),
                "commit": newest.get("commit"),
                "available": True,
                "marker": True,
            }
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        # Saving the run must not fail because kept code cannot be read or stored (an unreadable
        # source pauses through its own check); the gap is recorded and `checkpoint --all` names it.
        state["kept_code"] = {
            "id": "kept-" + util.digest([str(run_dir), revision])[:24],
            "source_revision": revision,
            "commit": None,
            "at": util.now(),
            "available": False,
            "reason": str(error),
            "start": (kept or {}).get("start"),
            "after": newest["id"] if newest else None,
            "removed": list((kept or {}).get("removed") or []),
            "final": False,
        }


def _capture_kept(state: RunState, run_dir, source, common):
    """Save the kept source as a Git commit with a hash-pinned receipt and a ref, like a saved step."""
    ident = "kept-" + util.digest([str(run_dir), source["revision"]])[:24]
    directory = run_dir / "code-checkpoints"
    if directory.is_symlink():
        raise ValueError("Checkpoint storage must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (ident + ".json")
    if path.exists():
        saved = util.read(path)
        if saved.get("id") != ident or saved.get("source") != source:
            raise ValueError("An existing kept code receipt conflicts with this source")
        commit = saved["commit"]
    else:
        commit = capture_tree(state["workspace"], source, directory)
        util.atomic_json(
            path,
            {
                "id": ident,
                "kind": "kept",
                "commit": commit,
                "source": source,
                "start": common["start"],
                "after": common["after"],
                "removed": common["removed"],
                "run_dir": str(run_dir),
                "workspace": state["workspace"],
                "at": common["at"],
            },
        )
    ref = "refs/autocode/code-checkpoints/" + util.digest(str(run_dir))[:20] + "/" + ident
    git(state["workspace"], "update-ref", ref, commit)
    return {
        "id": ident,
        "source_revision": source["revision"],
        "commit": commit,
        "available": True,
        "receipt": str(path),
        "receipt_sha256": util.file_hash(path),
    }


def blocked_reason(state: RunState):
    if state.get("status") == "TASK_COMPLETE":
        # Nothing runs, so "pause first" would be advice the person cannot follow (#1643).
        return "A finished run cannot go back to a saved step. Its steps can still be compared; ask for a follow-up to change the code."
    if state.get("status") not in ("PAUSED_REQUESTED", "PAUSED_INTERVENTION"):
        return "Pause after the current step before restoring a checkpoint."
    if not approved(state):
        return "Checkpoint restoration requires the same approved plan."
    if any(
        state.get(key)
        for key in (
            "active_stage",
            "active_runner_check",
            "uncertain_artifacts",
            "pending_report_repair",
            "orchestration_batch",
            "pending_questions",
            "user_request",
            "resolver_human_request",
            "resolver_pending_human",
        )
    ):
        return "Reconcile the active step, batch or human request before restoring."
    if (
        state.get("progressive")
        or state.get("parent_run")
        or (state.get("workflow") or {}).get("kind") not in (None, "build")
    ):
        return "This checkpoint action currently supports ordinary build runs; this run uses a different execution contract."
    if any(row.get("kind") == "stop" for row in state.get("applied_interventions", [])):
        return "A stopped run is terminal. Its saved work remains available."
    return None


def row_actions(row, *, restorable, run_dir=None, workspace=None):
    """The actions a client may run on this row, as argv after `autocode`.

    The shape follows the connections contract (docs/task-run.md): clients
    execute only offered actions. The restore's token travels through
    AUTOCODE_CHECKPOINT_EXPECTED_TOKEN (the `-` selector), and only a fresh
    compare of the same checkpoint returns it; `REQUEST_ID` is the caller's
    stable confirmation id, which makes replay idempotent.
    """
    if not row.get("available"):
        return []
    prefix = [
        "checkpoint",
        "--workspace",
        str(workspace) if workspace else "WORKSPACE",
        "--run-dir",
        str(run_dir) if run_dir else "RUN_DIR",
    ]
    actions = [
        {
            "id": "compare",
            "label": "Show what changed since here",
            "next_command": [*prefix, "--compare", row["id"]],
        },
        # Display only: what this step itself changed, which a compare of the newest step cannot show.
        {"id": "step", "label": "Show what this step changed", "next_command": [*prefix, "--step", row["id"]]},
    ]
    if restorable:
        actions.append(
            {
                "id": "restore",
                "label": "Go back to here",
                "next_command": [
                    *prefix,
                    "--restore",
                    row["id"],
                    "--expected-token",
                    "-",
                    "--request-id",
                    "REQUEST_ID",
                ],
            }
        )
    return actions


def working_action(state: RunState, *, run_dir=None, workspace=None):
    """The display-only `checkpoint --working` action, named while a Builder step is running."""
    active = state.get("active_stage")
    if not isinstance(active, dict):
        return None
    if active.get("stage") not in ("builder", "orchestrator") or not approved(state):
        return None
    return {
        "id": "working",
        "label": "Show what the Builder has changed so far",
        "next_command": [
            "checkpoint",
            "--workspace",
            str(workspace) if workspace else "WORKSPACE",
            "--run-dir",
            str(run_dir) if run_dir else "RUN_DIR",
            "--working",
        ],
    }


def all_changes_action(state: RunState, *, run_dir=None, workspace=None):
    """The display-only `checkpoint --all` action: the run's whole change, named once a step or kept code
    is saved.

    This is what a person reads: the result, not each step (owner decision 2026-11-13)."""
    kept = state.get("kept_code") or {}
    if not any(row.get("available") for row in state.get("code_checkpoints") or []) and not kept.get("available"):
        return None
    return {
        "id": "all_changes",
        "label": "Show all changes",
        "next_command": [
            "checkpoint",
            "--workspace",
            str(workspace) if workspace else "WORKSPACE",
            "--run-dir",
            str(run_dir) if run_dir else "RUN_DIR",
            "--all",
        ],
    }


def project(state: RunState, *, run_dir=None, workspace=None):
    reason = blocked_reason(state)
    rows = []
    for row in state.get("code_checkpoints", []):
        projected = {
            key: deepcopy(row.get(key))
            for key in (
                "id",
                "task_id",
                "iteration",
                "at",
                "label",
                "milestone_id",
                "members",
                "changed_files",
                "source_revision",
                "commit",
                "available",
                "reason",
                "recorded_check",
            )
        }
        # Only a row with verified source objects is actionable; a restore is
        # named only where the pause reconciles (blocked_reason is None).
        projected["actions"] = row_actions(row, restorable=reason is None, run_dir=run_dir, workspace=workspace)
        rows.append(projected)
    return {
        "version": 1,
        "blocked_reason": reason,
        "rows": rows,
        "working": working_action(state, run_dir=run_dir, workspace=workspace),
        "all_changes": all_changes_action(state, run_dir=run_dir, workspace=workspace),
        # The newest kept code the whole-run diff ends at, or why it could not be saved (#3945).
        "kept": (
            {
                key: deepcopy((state.get("kept_code") or {}).get(key))
                for key in ("id", "source_revision", "commit", "at", "available", "reason")
            }
            if state.get("kept_code")
            else None
        ),
        "continuation": deepcopy(state.get("checkpoint_continuation")),
        "restores": deepcopy(state.get("checkpoint_restores", [])),
    }

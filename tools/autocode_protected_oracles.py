"""Original test gates retained before a new code run dispatches a model.

reconcile alone writes settings.protected_tests. Replay and completion read it;
user_events and content-addressed bundles preserve explicit revisions. The
caller supplies test classification, framework discovery and scratch execution.
A run scoped to one package (settings.package) retains that package's tests only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


try:
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_source_scope as source_scope

import copy
import json
import uuid
from pathlib import Path

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_package_scope as package_scope
    from . import autocode_protected_commands as commands
    from . import autocode_protected_paths as paths
    from . import autocode_protected_store as store
    from . import autocode_util as util
    from .autocode_protected_paths import identity, path_in
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_package_scope as package_scope
    import autocode_protected_commands as commands
    import autocode_protected_paths as paths
    import autocode_protected_store as store
    import autocode_util as util
    from autocode_protected_paths import identity, path_in


def body(record):
    return {key: record[key] for key in ("version", "files", "command")}


def verify_binding(record):
    if record.get("version") not in (1, 2) or record.get("binding_hash") != util.digest(body(record)):
        raise ValueError("Protected test binding changed; a model cannot revise the original gate")
    if record.get("inventory_path") and util.read_object(record["inventory_path"]) != body(record):
        raise ValueError("Retained protected-test inventory changed")
    archive = store.archive_path(record)
    if archive is not None:
        store.read(record, archive)
    else:
        for name, expected in record["files"].items():
            if identity(path_in(record["root"], name, allow_link=record["version"] == 2)) != expected:
                raise ValueError(f"Original protected test bundle changed: {name}")
        if record["version"] == 2:
            paths.verify_links(record["root"], record["files"])
    return record


def retain(workspace, run_dir, files, command):
    workspace = Path(workspace).resolve()
    version = 2 if any("symlink" in value for value in files.values()) else 1
    record = {"version": version, "files": copy.deepcopy(files), "command": command}
    record["binding_hash"] = util.digest(body(record))
    root = Path(run_dir) / "protected-tests" / (record["binding_hash"] + ".zip")
    record["root"] = str(root)
    store.capture(record, workspace, root)
    inventory_path = root.parent / (record["binding_hash"] + ".json")
    if not inventory_path.exists():
        util.atomic_json(inventory_path, body(record))
    record["inventory_path"] = str(inventory_path)
    return verify_binding(record)


def inventory(workspace, is_test_path, *, package=None):
    """Every eligible test, or only those inside ``package``; a test's link targets come with it."""
    snapshot = util.snapshot(workspace)["files"]
    names = [name for name, value in snapshot.items() if is_test_path(name) and value != "deleted"]
    if package:
        names = [name for name in names if package_scope.contains(package, name)]
    return paths.collect(workspace, names, snapshot)


def reconcile(state: RunState, settings, args, workspace, run_dir, *, is_test_path, discover_command):
    """New runs bind all eligible existing tests; saved runs never silently rebind.

    A CLI revision declares the exact old binding, new inventory, command and
    rationale. It is admitted only at a reconciled paused validation boundary.
    Neither a Builder report nor a repaired verification command is authority.
    """
    revision = getattr(args, "revise_protected_tests", None)
    previous = settings.get("protected_tests")
    if revision:
        command_only = revision == commands.SELECTOR
        if not getattr(args, "run_dir", None) or not getattr(args, "resume_paused", False):
            raise ValueError("--revise-protected-tests requires a saved pause and --resume-paused")
        if (
            not str(state.get("status", "")).startswith("PAUSED_")
            or state.get("next_stage") not in ("sol", "astra_checkpoint")
            or any(
                state.get(key)
                for key in (
                    "active_stage",
                    "uncertain_artifacts",
                    "active_runner_check",
                    "runner_check",
                )
            )
            or state.get("pending_report_repair")
            and not (command_only and commands.completed_gate_repair(state))
        ):
            raise ValueError("Reconcile the attempt and stop before independent validation to revise protected tests")
        if not previous:
            raise ValueError("This saved run has no original protected-test binding; start a new run")
        verify_binding(previous)
        proposal = commands.proposal(state, previous) if command_only else util.read_object(revision)
        if proposal is None:
            raise ValueError("No approved command-only protected-test correction is available")
        if (
            set(proposal) != {"previous_hash", "files", "command", "reason"}
            or proposal["previous_hash"] != previous["binding_hash"]
            or not isinstance(proposal["reason"], str)
            or not proposal["reason"].strip()
            or not isinstance(proposal["command"], str)
            or not proposal["command"].strip()
        ):
            raise ValueError("A protected-test revision needs the exact previous hash, command and user rationale")
        files = (
            previous["files"]
            if command_only
            else inventory(workspace, is_test_path, package=package_scope.of(settings))
        )
        if proposal["files"] != files or not files:
            raise ValueError("Protected-test revision must identify every current eligible test by hash, size and mode")
        if command_only:
            with store.opened(previous, workspace) as original_root:
                current = retain(original_root, run_dir, files, proposal["command"])
        else:
            current = retain(workspace, run_dir, files, proposal["command"])
        state.setdefault("user_events", []).append(
            {
                "kind": "protected_tests_revised",
                "actor": "user_cli",
                "at": util.now(),
                "previous": previous,
                "current": current,
                "reason": proposal["reason"],
                "proposal_sha256": util.digest(proposal) if command_only else util.file_hash(revision),
            }
        )
        settings["protected_tests"] = current
        if command_only and state.get("pending_report_repair"):
            state.setdefault("report_repair_archive", []).append(
                {
                    "reason": "Explicit original-suite command correction requires fresh validation, not report formatting",
                    "repair": state.pop("pending_report_repair"),
                }
            )
    elif not getattr(args, "run_dir", None):
        files = inventory(workspace, is_test_path, package=package_scope.of(settings))
        explicit = (settings.get("regression") or {}).get("test_command")
        command = explicit or commands.initial_command(files, discover_command() if files else None)
        settings["protected_tests"] = retain(workspace, run_dir, files, command)
    if getattr(args, "run_dir", None) and not any(state.get(key) for key in ("active_stage", "active_runner_check")):
        retained = [settings.get("protected_tests")]
        for event in state.get("user_events", []):
            if event.get("kind") == "protected_tests_revised":
                retained.extend((event.get("previous"), event.get("current")))
        for record in retained:
            if record:
                verify_binding(record)
                store.compact(record, run_dir)
    return settings


# A stage handoff lists the protected tests in its affected paths, but never more than this: an affected
# directory can hold tens of thousands of them, and copying them all once made a 7.8 MB prompt (#1140). The
# full inventory stays at inventory_path, and the binding and the original-test replay cover every file.
MAX_ASSIGNED_TESTS = 200
MAX_ASSIGNED_BYTES = 32_000


def context(settings, affected_paths=()):
    record = settings.get("protected_tests")
    if not record or not record["files"]:
        return None
    verify_binding(record)
    matching = [
        name
        for name in record["files"]
        if any(name == path.rstrip("/") or name.startswith(path.rstrip("/") + "/") for path in affected_paths)
    ]
    assigned, size = {}, 0
    for name in matching[:MAX_ASSIGNED_TESTS]:
        size += len(json.dumps({name: record["files"][name]})) + 2
        if size > MAX_ASSIGNED_BYTES:
            break
        assigned[name] = copy.deepcopy(record["files"][name])
    result = {key: copy.deepcopy(record.get(key)) for key in ("binding_hash", "root", "inventory_path", "command")}
    result |= {"total_files": len(record["files"]), "assigned_tests": assigned}
    instruction = prompts.get("fragments/protected-oracles/context.md")
    if len(assigned) < len(matching):
        result |= {"assigned_total": len(matching), "assigned_omitted": len(matching) - len(assigned)}
        instruction += "\n\n" + prompts.get("fragments/protected-oracles/omitted.md")
    return result | {"instruction": instruction}


def replay(state: RunState, workspace, out, scratch_run, *, timeout):
    record = state.get("settings", {}).get("protected_tests")
    if not record:
        return None  # compatibility: do not invent an original binding for old runs
    verify_binding(record)
    revision = source_scope.snapshot(workspace, state)["revision"]
    changed = []
    for name, expected in record["files"].items():
        try:
            actual = identity(path_in(workspace, name, allow_link=record["version"] == 2))
        except (OSError, ValueError):
            actual = None
        if actual != expected:
            changed.append(name)
    result = {
        "binding_hash": record["binding_hash"],
        "source_revision": revision,
        "changed_tests": changed,
        "verdict": "PASS",
        "candidate": None,
        "original": None,
    }
    if not changed:
        result["meaning"] = "Original test files unchanged; ordinary independent checks remain required."
        return result
    directory = Path(out) / "protected-tests" / uuid.uuid4().hex
    if not record["command"]:
        result.update(
            verdict="NOT_VERIFIED", error="No original suite command was available; explicit user revision required"
        )
    else:
        result["candidate"] = scratch_run(
            workspace, directory / "candidate", command=record["command"], timeout=timeout
        )
        with store.opened(record, workspace) as original_root:
            overlays = {
                "files": {
                    name: str(path_in(original_root, name))
                    for name, value in record["files"].items()
                    if "symlink" not in value
                }
            }
            links = {name: value["symlink"] for name, value in record["files"].items() if "symlink" in value}
            if links:
                overlays["links"] = links
            result["original"] = scratch_run(
                workspace, directory / "original", command=record["command"], timeout=timeout, **overlays
            )
        receipts = (result["candidate"], result["original"])
        if any(
            row.get("exit_code") != 0 or row.get("error") or row.get("timed_out") or not command_receipt.completed(row)
            for row in receipts
        ):
            result["verdict"] = "FAIL"
    verify_binding(record)
    if source_scope.snapshot(workspace, state)["revision"] != revision:
        result.update(verdict="NOT_VERIFIED", error="Candidate changed during protected-test replay")
    directory.mkdir(parents=True, exist_ok=True)
    receipt = directory / "receipt.json"
    util.atomic_json(receipt, result)
    result["receipt"] = str(receipt)
    result["receipt_sha256"] = util.file_hash(receipt)
    if result["verdict"] != "PASS":
        tail = (result.get("original") or {}).get("tail", "")[-500:]
        raise commands.OriginalGateFailure(
            "Protected tests changed and the original gate did not pass on this implementation. "
            "Repair the implementation; a modified assertion is not proof. "
            + result.get("error", "")
            + f" Receipt: {receipt}. {tail}"
        )
    return result


def ready(state: RunState, current_revision):
    record = state.get("settings", {}).get("protected_tests")
    if not record:
        return True
    try:
        verify_binding(record)
        proof = (state.get("validation") or {}).get("check_replay") or {}
        if proof.get("verdict") != "PASS" or proof.get("source_revision") != current_revision:
            return False
        result = proof.get("protected_tests") or {}
        if result.get("receipt"):
            receipt = Path(result["receipt"])
            if (
                not receipt.is_file()
                or util.file_hash(receipt) != result.get("receipt_sha256")
                or util.read_object(receipt)
                != {key: value for key, value in result.items() if key not in ("receipt", "receipt_sha256")}
            ):
                return False
        if result.get("changed_tests") or any(result.get(name) is not None for name in ("candidate", "original")):
            for name in ("candidate", "original"):
                row = result.get(name)
                if (
                    not isinstance(row, dict)
                    or type(row.get("exit_code")) is not int
                    or row["exit_code"] != 0
                    or row.get("timed_out")
                    or row.get("error")
                    or not command_receipt.completed(row)
                ):
                    return False
        return (
            result.get("verdict") == "PASS"
            and result.get("binding_hash") == record["binding_hash"]
            and result.get("source_revision") == current_revision
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False

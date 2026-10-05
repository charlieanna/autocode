"""Workspace diagnostics and incident identity for external-directory denials.

Only fields inside the existing automatic_permission_recoveries/recovery_context
records are added. The launch guard (autocode_recovery_limits) reads them through
``holds``; fresh stage prompts receive denied_operation and diagnostic_directory
unchanged. No permission, retry allowance or completion evidence is granted here.

A denial retry never consumes the shared timeout-recovery budget or the Builder's
unchanged-batch count: it has its own repeat guard per incident and its own ceiling
(MAX_PERMISSION_RECOVERIES denial recoveries since the last accepted stage of any
kind other than a read-only diagnosis).
"""
from pathlib import Path
import re

try:
    from .autocode_util import digest
except ImportError:
    from autocode_util import digest


MAX_PERMISSION_RECOVERIES = 3
DENIAL_MARKER = "OpenCode denied external_directory"
# Read-only diagnosticians (autocode_stuck_job's Investigator, --diagnose-failed-stage): an accepted
# diagnosis explains a stall but is not forward progress, so it ends no run of denials.
DIAGNOSTIC_STAGES = frozenset({"investigate_stuck", "astra_diagnose"})


def operation(raw):
    match = re.search(r"permission requested: external_directory\s*\(([^\r\n)]*)\)", raw)
    path = match.group(1).strip() if match else "unknown external path"
    temporary = path.startswith(("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/"))
    return {"capability": "external_directory", "path": path,
            "classification": "external_temporary_directory" if temporary else "external_directory"}


def _denied_since_accepted(stages):
    """Denial recoveries since the last accepted stage of any kind but a diagnosis.

    An accepted stage row proves forward progress, so fresh denials after it
    are new incidents rather than a continuation of the same loop.
    """
    count = 0
    for row in reversed(stages):
        if str(row.get("rejection_reason") or "").startswith(DENIAL_MARKER):
            count += 1
            continue
        if (row.get("stage") and row["stage"] not in DIAGNOSTIC_STAGES and not any(row.get(key) for key in
                ("abandoned", "rejected", "runner_owned", "failure_attempt"))):
            break
    return count


def prepare(workspace, stage, denied, recoveries, stages, *, run_dir):
    """Provision a safe path and count recurrence since this owner's last success."""
    identity = {"stage": stage, "capability": denied["capability"],
                "path": denied["classification"] if denied["classification"] == "external_temporary_directory"
                        else denied["path"]}
    incident = digest(identity)
    previous = next((row for row in reversed(recoveries) if row.get("incident_id") == incident), None)
    count = 1
    if previous:
        since = []
        for row in reversed(stages):
            if row.get("events") == previous.get("events"):
                break
            since.append(row)
        accepted = any(row.get("stage") == stage and not any(row.get(key) for key in
                       ("abandoned", "rejected", "runner_owned")) for row in since)
        if not accepted:
            count = previous["repeat_count"] + 1
    root = Path(workspace).resolve()
    directory = root
    scope = digest(str(Path(run_dir).resolve()))
    for part in (".autocode", "recovery-evidence", scope, incident, "scratch"):
        directory = directory / part
        # Never resolve or mkdir through a workspace-owned symlink.
        if directory.is_symlink():
            raise ValueError(f"Recovery diagnostic path is a symlink: {directory}")
        directory.mkdir(exist_ok=True)
    return {"incident_id": incident, "repeat_count": count,
            "denied_operation": denied, "diagnostic_directory": str(directory),
            "denied_since_accepted": _denied_since_accepted(stages)}


def _accepted_after(stages, recovery):
    """Whether an attempt of ``recovery``'s stage was accepted after its denied attempt.

    Unknown when the denied attempt's row is missing: then False, so the hold stays.
    """
    for index in range(len(stages) - 1, -1, -1):
        if recovery.get("events") and stages[index].get("events") == recovery["events"]:
            return any((row.get("original_stage") or row.get("stage")) == recovery.get("stage")
                       and not any(row.get(key) for key in ("abandoned", "rejected", "runner_owned"))
                       for row in stages[index + 1:])
    return False


def holds(recovery, stages, revision):
    """Whether ``recovery`` still holds every launch (the launch guard, autocode_recovery_limits).

    It does while its denial repeated after a workspace-only retry or reached the ceiling, no attempt
    of its stage has been accepted since, and ``revision()`` (read last) is the source it stopped at.
    """
    if not (recovery.get("denied_operation") and (recovery.get("repeat_count", 0) >= 2
            or recovery.get("denied_since_accepted", 0) >= MAX_PERMISSION_RECOVERIES)):
        return False
    return not _accepted_after(stages, recovery) and revision() == recovery.get("source_revision")


def stop_message(recovery):
    """Why ``recovery`` stops the run, or None while automatic denial retries remain."""
    if recovery.get("repeat_count", 0) >= 2:
        return hold_message(recovery)
    if recovery.get("denied_since_accepted", 0) >= MAX_PERMISSION_RECOVERIES:
        return ceiling_message(recovery)
    return None


def hold_message(recovery):
    denied = recovery["denied_operation"]
    return (f"Repeated {denied['capability']} denial for {denied['path']} after a workspace-only recovery. "
            f"Retained scratch directory: {recovery['diagnostic_directory']}. "
            "No further automatic retry will launch. Inspect the saved denied operation and change its "
            "cause before resuming; existing permissions and recovery accounting remain in force.")


def ceiling_message(recovery):
    return (f"External-directory denials were recovered {recovery['denied_since_accepted']} times "
            f"without an accepted stage; the permission-recovery ceiling of {MAX_PERMISSION_RECOVERIES} "
            "is reached. Retained scratch directory: "
            f"{recovery['diagnostic_directory']}. No further automatic retry will launch. "
            "Inspect the saved denied operations and change their cause before resuming; "
            "timeout-recovery allowances are unaffected and remain available.")

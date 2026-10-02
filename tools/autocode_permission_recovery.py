"""Workspace diagnostics and incident identity for external-directory denials.

Only fields inside the existing automatic_permission_recoveries/recovery_context
records are added. The launch guard reads repeat_count and source_revision;
fresh stage prompts receive denied_operation and diagnostic_directory unchanged.
No permission, retry allowance or completion evidence is granted here.
"""
from pathlib import Path
import re

try:
    from .autocode_util import digest
except ImportError:
    from autocode_util import digest


def operation(raw):
    match = re.search(r"permission requested: external_directory\s*\(([^\r\n)]*)\)", raw)
    path = match.group(1).strip() if match else "unknown external path"
    temporary = path.startswith(("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/"))
    return {"capability": "external_directory", "path": path,
            "classification": "external_temporary_directory" if temporary else "external_directory"}


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
            "denied_operation": denied, "diagnostic_directory": str(directory)}


def hold_message(recovery):
    denied = recovery["denied_operation"]
    return (f"Repeated {denied['capability']} denial for {denied['path']} after a workspace-only recovery. "
            f"Retained scratch directory: {recovery['diagnostic_directory']}. "
            "No further automatic retry will launch. Inspect the saved denied operation and change its "
            "cause before resuming; existing permissions and recovery accounting remain in force.")

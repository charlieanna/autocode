"""Classify pinned failure facts without inspecting files or requesting a model.

The caller verifies receipts and source/task/contract binding before setting
checks_verified or checkpoint_verified. An Investigator can settle an ambiguous
cause only for the current failure and its supplied evidence references. Free
text, a Builder's claims and an empty diff never authorize a stronger route.
"""

CLASSES = frozenset({"plan", "execution", "operational", "unknown"})
_OPERATIONAL = (
    "PAUSED_PROVIDER_", "PAUSED_AUTH", "PAUSED_QUOTA", "PAUSED_TOOL",
    "PAUSED_PROCESS_", "PAUSED_VERIFICATION_", "PAUSED_INTERRUPTED",
    "PAUSED_UNCERTAIN_STAGE", "PAUSED_INVALID_OUTPUT", "PAUSED_OUTPUT_CAP",
    "PAUSED_STARTUP_", "PAUSED_OPERATIONAL_", "PAUSED_PERMISSION",
    "PAUSED_STALE_HANDOFF", "PAUSED_STALE_VALIDATION",
)


def classify(evidence):
    """Return plan, execution, operational or unknown from runner-pinned facts."""
    if not isinstance(evidence, dict):
        return "unknown"
    record = evidence.get("record") or {}
    if not isinstance(record, dict):
        record = {}
    statuses = (evidence.get("error_class"), record.get("error_class"),
                evidence.get("provider_error_class"))
    if (any(isinstance(status, str) and status.startswith(_OPERATIONAL) for status in statuses)
            or record.get("timed_out") is True or record.get("interrupted") is True
            or (type(record.get("exit_code")) is int and record["exit_code"] < 0)
            or record.get("cleanup_error") or record.get("supervision_errors")):
        return "operational"
    probe = evidence.get("output_probe") or {}
    if isinstance(probe, dict):
        probe = probe.get("result", probe)
        final = probe.get("final_output") if isinstance(probe, dict) else None
        if isinstance(final, dict) and final.get("parse") in ("invalid_or_unreadable", "json_other"):
            return "operational"
    checks = evidence.get("checks") or []
    if evidence.get("checks_verified") is True and isinstance(checks, (list, tuple)):
        for check in checks:
            if not isinstance(check, dict):
                continue
            if (check.get("timed_out") is True or check.get("interrupted") is True
                    or check.get("error") or check.get("supervision_errors")
                    or (type(check.get("exit_code")) is int and check["exit_code"] < 0)):
                return "operational"
    checkpoint = evidence.get("checkpoint") or {}
    if (evidence.get("checkpoint_verified") is True and isinstance(checkpoint, dict)
            and checkpoint.get("decision") == "needs_replan"):
        return "plan"
    diagnosis = evidence.get("diagnosis") or {}
    failure_id = evidence.get("failure_id")
    allowed = evidence.get("evidence_refs") or []
    if isinstance(diagnosis, dict):
        refs = diagnosis.get("evidence_refs")
        if (isinstance(failure_id, str) and failure_id
                and diagnosis.get("failure_id") == failure_id
                and isinstance(diagnosis.get("failure_class"), str)
                and diagnosis["failure_class"] in CLASSES
                and isinstance(allowed, (list, tuple))
                and all(isinstance(ref, str) and ref for ref in allowed)
                and isinstance(refs, list) and refs
                and all(isinstance(ref, str) and ref in allowed for ref in refs)):
            return diagnosis["failure_class"]
    if (evidence.get("checks_verified") is True and isinstance(checks, (list, tuple))
        and any(isinstance(check, dict) and type(check.get("exit_code")) is int
                and check["exit_code"] > 0 for check in checks)):
        return "execution"
    return "unknown"

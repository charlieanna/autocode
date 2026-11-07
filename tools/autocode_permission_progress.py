"""Bound permission handoffs that have no source, contract or validation progress.

A paraphrased permission question is not proof of progress. Three material decisions
may be consumed at one work frontier; a fourth requires corrective feedback or actual
work progress. This bound grants no permission and leaves every receipt intact.
"""

try:
    from .autocode_util import digest
except ImportError:
    from autocode_util import digest

LIMIT = 3
STATUS = "PAUSED_PERMISSION_LOOP"
FIELDS = ("task_id", "task", "current_task", "contract", "observed_source", "validation")


def frontier(binding):
    return {key: binding.get(key) for key in FIELDS}


def exhausted(resolver, binding):
    """Return public stop details only for authenticated consumed material decisions."""
    expected = frontier(binding)
    count = 0
    for key, entry in (resolver.get("human_escalations") or {}).items():
        if not isinstance(entry, dict) or entry.get("status") != "consumed":
            continue
        identity = entry.get("identity") or {}
        proposal = identity.get("proposal") or {}
        previous = identity.get("binding") or {}
        response = entry.get("response") or {}
        if (
            identity.get("issuer") == "resolver"
            and identity.get("action") == "escalate"
            and proposal.get("scope") == "permission"
            and digest(identity) == key
            and entry.get("receipt_hash") == key
            and response.get("actor") == "user_cli"
            and response.get("action") == "material_decision"
            and response.get("request_id") == key
            and response.get("request_token") == digest({"request_id": key, "binding": previous})
            and frontier(previous) == expected
        ):
            count += 1
    if count < LIMIT:
        return None
    return {
        "count": count,
        "limit": LIMIT,
        "reason": f"{count} permission decisions were answered without source, contract or validation progress. "
        "Another question would repeat unchanged work. Inspect the saved blocker and repair it; "
        "use --feedback to explain the correction or new scope before continuing. "
        "A plain resume does not authorize another model call.",
    }

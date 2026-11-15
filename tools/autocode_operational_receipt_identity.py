"""Pure ownership identities for the runner's shared operational receipt ledger.

Identity ownership is separate from presentation or recovery authority. Domain
readers still validate their own evidence before granting any result or retry.
"""

from __future__ import annotations

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

DIAGNOSTIC = "operational_diagnostic"
_PREFIXES = {
    "metadata_custody": "operational-block",
    "metadata_admission": "operational-block",
    "worker_evidence": "operational-block",
    "model_listing": "model-listing",
    "verification_preparation": "verification-preparation",
    "repaired_acceptance_no_progress": "no-progress-proof-recovery",
    "no_progress_exhaustion": "no-progress-exhausted",
}


def ordinal(scope, number):
    """Keep each domain's existing append-only spelling in one contract."""
    if scope not in _PREFIXES or type(number) is not int or number < 1:
        raise ValueError("Invalid operational receipt identity")
    return f"{_PREFIXES[scope]}-{number}"


def diagnostic(action, summary, evidence, binding):
    # This is the original generic diagnostic identity algorithm, unchanged.
    return util.digest(
        {"scope": DIAGNOSTIC, "action": action, "detail": summary, "evidence": evidence, "binding": binding}
    )


def scope(row, identity):
    """Authenticate the ledger key, row identity and producer's namespace."""
    if not isinstance(row, dict) or row.get("runner_owned") is not True or row.get("engine") != "runner":
        return None
    receipt = row.get("receipt")
    if not isinstance(receipt, dict) or not isinstance(identity, str) or receipt.get("id") != identity:
        return None
    owner = receipt.get("scope")
    if owner == DIAGNOSTIC:
        decision = row.get("decision")
        if (
            row.get("stage") == "resolver"
            and isinstance(decision, dict)
            and isinstance(row.get("summary"), str)
            and row["summary"] == decision.get("rationale")
            and isinstance(receipt.get("evidence"), dict)
            and isinstance(receipt.get("evaluation_binding"), dict)
            and diagnostic(decision.get("action"), row["summary"], receipt["evidence"], receipt["evaluation_binding"])
            == identity
        ):
            return owner
        return None
    prefix = _PREFIXES.get(owner) if isinstance(owner, str) else None
    if prefix and identity.startswith(prefix + "-"):
        number = identity[len(prefix) + 1 :]
        if number.isascii() and number.isdecimal() and number and number[0] != "0":
            return owner
    return None

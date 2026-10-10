"""The user closes reviewer findings by name, as an audited decision (#300).

Reviewer findings close only through their reviewer's fresh, evidenced report (autocode_findings),
and nothing closes a finding because its text or evidence matches another. A live run still held a
duplicate of a problem the user had already settled, and no reviewer report could close it. The
user may close named open findings with ``--close-finding ID --close-reason TEXT``: each row records
that the user closed it and why, and one ``findings_closed`` user event records the decision.

When any of them is a finding an AutoResolver validation-only stop asked about
(autocode_validation_rounds puts their ids in the request), that request is answered by the decision:
it is withdrawn and the run returns to RUNNING. The next resume reaches the same Validator gate, which
asks again, without a model call, about any finding still stalled, or dispatches when none is.
Callers pass the resolver services, so this module imports nothing from the runner.
"""

from __future__ import annotations

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def close(state, ids, reason, *, current, supersede):
    """Close each named open finding as the user's decision. Raises ValueError and changes nothing on bad input."""
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("--close-finding needs --close-reason TEXT: why each finding no longer applies")
    rows = {row.get("id"): row for row in state.get("findings_ledger", [])}
    named = list(dict.fromkeys(ids))
    unknown = [ident for ident in named if ident not in rows]
    closed = [ident for ident in named if ident in rows and rows[ident].get("status") != "open"]
    if unknown or closed:
        raise ValueError(
            "Only open findings can be closed: "
            + "; ".join(
                ([f"unknown {', '.join(unknown)}"] if unknown else [])
                + ([f"already {rows[i]['status']} {i}" for i in closed])
            )
        )
    # Read before changing anything: the request is bound to the run's user events.
    published = current(state)
    at = util.now()
    for ident in named:
        row = rows[ident]
        row.update(
            status="resolved", resolved_at=at, resolved_in="user_cli", resolved_by="user", resolution_evidence=reason
        )
        row.pop("pending_resolution", None)
        row.pop("not_rechecked_in", None)
    state.setdefault("user_events", []).append(
        {"kind": "findings_closed", "actor": "user_cli", "at": at, "ids": named, "reason": reason}
    )
    asked = set(((published or {}).get("request") or {}).get("finding_ids") or [])
    if asked & set(named) and supersede(state, "The user closed findings this request asked about"):
        state.update(status="RUNNING", phase="EXECUTING")
        state.pop("stop_reason", None)
    return named

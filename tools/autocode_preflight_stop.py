"""The model access probe a run stopped at, and the one sentence that says why (#3888).

A blocking access probe (autocode_model_preflight) stops the run before the stage it checked
launches: its account ran out of credit or quota, its login was refused, or its billing route
cannot serve the model. That stop is not a step failure, and PAUSED_BUDGET is also the status of
a spending limit, so the person is told it was the model access check, for which model and role,
and the fixed cause of its saved status.

``blocking_probe`` is the one lookup of that probe; autocode_quota_route asks its route question
from it. ``compose`` builds the sentence from the probe's saved record alone (its model,
``launch_route`` provider, ``preflight_roles`` and ``preflight_outcome``), never from a message:
the provider's own words stay in the record and its evidence directory. The probe raises its
stop with this sentence, and the stop formatter, the operational hold and the recovery view read
the same one through ``sentence``.

Imports only autocode_roles and autocode_provider_auth.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

try:
    from . import autocode_provider_auth as provider_auth
    from . import autocode_roles as roles
except ImportError:
    import autocode_provider_auth as provider_auth
    import autocode_roles as roles

STAGE = "model_preflight"  # autocode_preflight_attempts.STAGE
INVESTIGATOR = "investigate_stuck"  # autocode_stuck_job.STAGE
# A fixed cause per blocking probe status (autocode_model_preflight.BLOCKING_STATUSES). The budget
# cause holds for every refusal autocode_provider_funds classifies: credit, balance, funds,
# spending or billing limit, quota and usage limit.
CAUSES = {
    "PAUSED_BUDGET": "the provider reported the account credit, quota, usage limit or spending limit used up",
    "PAUSED_AUTH": "the provider refused the account login",
    "PAUSED_BILLING_ROUTE": "the account billing route cannot serve this model",
}
_REFUSED = "the provider refused access to this model"
# The diagnostics AutoCode itself writes for a refused login (autocode_model_preflight._classify),
# followed only by the names-only withheld-variable hint: kept, so the person keeps that advice.
_OWN_AUTH_DIAGNOSTICS = (provider_auth.AUTH_FAILED, provider_auth.AUTH_ACCESS_DENIED)


def latest_attempt(state: Mapping[str, Any]) -> dict | None:
    """The newest stage attempt of a role: not runner-owned, not a batch worker's, not the Investigator."""
    return next(
        (
            row
            for row in reversed(state.get("stages") or [])
            if isinstance(row, dict)
            and not row.get("runner_owned")
            and not row.get("worker_attempt")
            # The Investigator that looked at this stop (#3349) is not a later attempt of the role.
            and (row.get("original_stage") or row.get("stage")) != INVESTIGATOR
        ),
        None,
    )


def blocking_probe(state: Mapping[str, Any], latest: dict | None = None) -> dict | None:
    """The blocking access probe the run stopped at, when no stage attempt has run since it.

    The probe is runner-owned, so the newest role attempt (``latest``, read here when not given) is
    never it; the probe must come after that attempt, and the run must still be paused with the
    probe's own status or waiting on the request that stop published.
    """
    if latest is None:
        latest = latest_attempt(state)
    rows = [row for row in state.get("stages") or [] if isinstance(row, dict)]
    after = rows[rows.index(latest) + 1 :] if latest in rows else rows
    probe = next((row for row in reversed(after) if row.get("stage") == STAGE), None)
    outcome = (probe or {}).get("preflight_outcome") or {}
    if (
        not probe
        or not outcome.get("blocking")
        or state.get("status") not in (outcome.get("status"), "WAITING_FOR_USER", "RESOLVER_PENDING")
    ):
        return None
    return probe


def _jobs(names: list[str]) -> str:
    shown = list(dict.fromkeys(roles.route_name(name, {}) for name in names))
    if not shown:
        return "the next step"
    if len(shown) == 1:
        return f"the {shown[0]} step"
    return f"the {', '.join(shown[:-1])} and {shown[-1]} steps"


def compose(record: Mapping[str, Any]) -> str:
    """One sentence for a blocking probe, from its saved record alone; never the provider's words."""
    outcome = record.get("preflight_outcome") or {}
    status = outcome.get("status")
    model = str(record.get("model") or outcome.get("model") or "the selected model")
    provider = (record.get("launch_route") or {}).get("provider")
    where = f" on {provider}" if isinstance(provider, str) and provider and provider != "opencode" else ""
    checked = [str(name) for name in record.get("preflight_roles") or [] if isinstance(name, str) and name]
    affected = f" (role: {checked[0]})" if len(checked) == 1 else f" (roles: {', '.join(checked)})" if checked else ""
    text = (
        f"The model access check for {model}{where}{affected} was refused before {_jobs(checked)} started: "
        f"{CAUSES.get(str(status), _REFUSED)}"
    )
    detail = outcome.get("detail")
    if status == "PAUSED_AUTH" and isinstance(detail, str) and detail.startswith(_OWN_AUTH_DIAGNOSTICS):
        text += f": {detail.strip()}"
    return text if text.endswith(".") else text + "."


def sentence(state: Mapping[str, Any] | None, status) -> str | None:
    """The stop sentence of the blocking probe the run stopped at with ``status``, else None."""
    probe = blocking_probe(state or {})
    if probe is None or (probe.get("preflight_outcome") or {}).get("status") != status:
        return None
    return compose(probe)

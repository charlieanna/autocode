"""Read-only admission for an exact retry of a failed workflow job."""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_job_source as source
    from . import autocode_quota_route as quota_route
except ImportError:
    import autocode_job_source as source
    import autocode_quota_route as quota_route


def configuration(state: RunState):
    settings = state.get("settings") or {}
    configured: dict = copy.deepcopy(
        {
            k: settings.get(k)
            for k in (
                "engine",
                "provider",
                "roles",
                "limits",
                "transport_identity",
                "transport_identities",
                "headroom",
                "output_transport",
                "test_command",
                "regression_command",
            )
        }
    )

    # Saved verification commands live under regression. Missing nested keys
    # retain legacy flat settings; an explicit None still means auto-detection.
    regression = settings.get("regression") or {}
    for key in ("test_command", "regression_command"):
        configured[key] = copy.deepcopy(regression.get(key, settings.get(key)))

    # Only the physical stopped Investigator is prepared from this saved pin.
    # Other jobs and retained classification (bc:) stops keep their old binding.
    failure = state.get("job_failure") or {}
    authorization = state.get("job_retry_authorization") or {}
    physical_retry = quota_route.job_pause_current(state) or (
        state.get("status") == "RUNNING"
        and authorization.get("stage") == "investigate_stuck"
        and authorization.get("token") == failure.get("job_retry_token")
    )
    if failure.get("stage") == "investigate_stuck" and physical_retry:
        pin = (settings.get("stuck_investigation") or {}).get("route")
        if isinstance(pin, dict) and isinstance((configured.get("roles") or {}).get("stuck_investigator"), dict):
            configured["roles"]["stuck_investigator"] = copy.deepcopy(pin)
    return configured


def retry_configuration_matches(current, saved):
    """Compare retry authority, ignoring only a complete content-addressed config's location.

    Package reinstallations relocate bundled TOML files. Their path is diagnostic;
    bytes, executable, version and all remaining settings still bind the retry.
    Live transport admission independently checks the currently installed provider.
    Legacy/incomplete identities keep strict equality.
    """

    def normalized(value):
        result = copy.deepcopy(value)
        if not isinstance(result, dict):
            return result
        identities = [result.get("transport_identity")]
        multiple = result.get("transport_identities")
        if isinstance(multiple, dict):
            identities.extend(multiple.values())
        fields = {"engine", "identity_version", "executable", "version", "config_path", "config_sha256"}
        for identity in identities:
            if (
                isinstance(identity, dict)
                and set(identity) == fields
                and type(identity.get("identity_version")) is int
                and identity["identity_version"] == 1
                and all(
                    isinstance(identity.get(key), str) and identity[key]
                    for key in ("engine", "executable", "config_path")
                )
                and (identity["version"] is None or isinstance(identity["version"], str))
                and isinstance(identity["config_sha256"], str)
                and re.fullmatch(r"[0-9a-f]{64}", identity["config_sha256"])
            ):
                identity.pop("config_path")
        return result

    return normalized(current) == normalized(saved)


def validate_retry(state: RunState, workspace, token):
    failure = state.get("job_failure") or {}
    if not quota_route.job_pause_current(state):
        raise ValueError("Exact job retry requires a paused failed workflow job")
    if not token or token != failure.get("job_retry_token"):
        raise ValueError("Job retry token does not match the current failed attempt")
    if not failure.get("source_identity"):
        raise ValueError(
            "Exact retry is unavailable: this attempt has no saved original source identity. "
            "Inspect the archived attempt and current changes before starting a new run."
        )
    # The restoration diagnosis describes the failed attempt. Missing capture
    # files or a later exact manual restoration must not make it a permanent
    # veto: recheck the saved full identity, then check it again at admission.
    if not source.matches_original(workspace, failure["source_identity"], source_paths=failure.get("source_paths", ())):
        if failure.get("unrestored"):
            raise ValueError("Unrestored source blocks job retry: " + ", ".join(failure["unrestored"]))
        raise ValueError("Source changed since the failed attempt; retry is stale")
    if not retry_configuration_matches(configuration(state), failure["configuration"]):
        raise ValueError("Provider configuration or limits changed; retry is stale")
    return failure

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


class RetryConfigurationChanged(ValueError):
    """The source and token are current, but the saved retry configuration differs."""


def route_configuration_matches(state: RunState, failure, assignment):
    """The concrete route assignment may change only its bound model and named effort."""
    expected = copy.deepcopy(failure["configuration"])
    bound = (expected.get("roles") or {}).get(assignment.get("role"))
    if not isinstance(bound, dict):
        return False
    bound["model"] = assignment.get("to")
    if isinstance(assignment.get("effort"), dict):
        bound["reasoning_effort"] = assignment["effort"].get("to")
    return configuration(state) == expected


def answer_effort(state: RunState, failure, role, effort):
    """Keep an explicitly named bound effort when it restores a drifted current route."""
    bound = ((failure.get("configuration") or {}).get("roles") or {}).get(role) or {}
    current = (((state.get("settings") or {}).get("roles") or {}).get(role)) or {}
    if not isinstance(bound, dict) or not isinstance(current, dict):
        raise ValueError("The stopped job's saved route is malformed")
    if effort == bound.get("reasoning_effort") and effort == current.get("reasoning_effort"):
        return None
    return effort


def effort_drift(state: RunState, failure, role):
    """A recognized saved and current effort differ for this route."""
    bound = ((failure.get("configuration") or {}).get("roles") or {}).get(role) or {}
    current = (((state.get("settings") or {}).get("roles") or {}).get(role)) or {}
    if not isinstance(bound, dict) or not isinstance(current, dict):
        return False
    saved_effort, current_effort = bound.get("reasoning_effort"), current.get("reasoning_effort")
    return (
        isinstance(saved_effort, str)
        and saved_effort in quota_route.route_fit.SCALE
        and isinstance(current_effort, str)
        and current_effort in quota_route.route_fit.SCALE
        and saved_effort != current_effort
    )


def route_is_repairable(state: RunState, *, cross_check, failure_status, configured_tool=False):
    """Try saved offered models against the same concrete assignment configuration gate."""
    question = quota_route.job_question(state)
    failure = state.get("job_failure") or {}
    if not isinstance(question, dict):
        return False
    role = question.get("route_role")
    question_id = question.get("id")
    if (
        not isinstance(question_id, str)
        or quota_route.asked_route([question], question_id) is None
        or quota_route.job_stop_status(failure) not in quota_route.MODEL_STOPS
    ):
        return False
    attempt = quota_route.stopped_attempt(state, failure_status=failure_status)
    if (
        not isinstance(role, str)
        or not attempt
        or attempt.get("kind") != "job"
        or attempt.get("role") != role
        or attempt.get("attempt_id") != failure.get("attempt_id")
    ):
        return False
    expected = copy.deepcopy(failure.get("configuration"))
    current = configuration(state)
    if isinstance(expected, dict):
        expected_role = (expected.get("roles") or {}).get(role)
        current_role = (current.get("roles") or {}).get(role)
        if isinstance(expected_role, dict) and isinstance(current_role, dict):
            saved_model, current_model = expected_role.get("model"), current_role.get("model")
            if isinstance(saved_model, str) and saved_model and isinstance(current_model, str) and current_model:
                current_role["model"] = saved_model
                if saved_model != current_model and current == expected:
                    # A route question is free form. This observes only model drift;
                    # its concrete answer still passes every normal locked gate.
                    return True
    saved_candidates = question.get("candidates")
    candidates = list(saved_candidates) if isinstance(saved_candidates, list) else []
    bound = ((failure.get("configuration") or {}).get("roles") or {}).get(role) or {}
    if not isinstance(bound, dict):
        return False
    saved_model, saved_effort = bound.get("model"), bound.get("reasoning_effort")
    if isinstance(saved_model, str) and isinstance(saved_effort, str) and effort_drift(state, failure, role):
        candidates.append(f"{saved_model}@{saved_effort}")
        routes = (state.get("settings") or {}).get("roles") or {}
        if isinstance(routes, dict) and len(routes) <= 32:
            candidates.extend(
                f"{route['model']}@{saved_effort}"
                for route in routes.values()
                if isinstance(route, dict) and isinstance(route.get("model"), str)
            )
    for answer in candidates:
        if not isinstance(answer, str):
            continue
        model, effort = quota_route.split_effort(answer)
        effort = answer_effort(state, failure, role, effort)
        if failure.get("pause_status") in (
            quota_route.REFUSAL_STATUS,
            quota_route.OUTPUT_STATUS,
            quota_route.CONTEXT_STATUS,
        ) and model == question.get("stopped_model"):
            continue
        candidate = copy.deepcopy(state)
        try:
            quota_route.validate(
                candidate,
                role,
                model,
                configured_tool=configured_tool,
                cross_check=cross_check,
                job=question.get("job"),
                allow_unchanged=effort is not None
                or quota_route.job_stop_status(failure) == quota_route.TIMEOUT_STATUS,
            )
            if effort is not None:
                quota_route.check_effort(candidate, role, model, effort, job=question.get("job"))
            assignment = quota_route.assign(
                candidate,
                role,
                model,
                at="",
                via="answer",
                effort=effort,
                attempt=attempt,
            )
            if route_configuration_matches(candidate, failure, assignment):
                return True
        except (ValueError, KeyError, TypeError):
            continue
    return False


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
        raise RetryConfigurationChanged("Provider configuration or limits changed; retry is stale")
    return failure

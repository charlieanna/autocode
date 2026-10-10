"""One-use planning-review route fallback for proven provider-stream silence.

This module only reserves and admits a route which is already present in saved
settings.  It does not launch providers, change settings, replenish accounting,
or diagnose why a provider stream was silent.
"""

from __future__ import annotations

try:
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_source_scope as source_scope


import copy
from pathlib import Path

try:
    from . import autocode_support as support
except ImportError:
    import autocode_support as support


REVIEW_STAGES = frozenset({"astra_challenge", "astra_finalize", "plan_review", "plan_finalize"})
SCOPE = "planning_reviewer_provider_stream_silence"


def _stop(message):
    raise support.Paused("PAUSED_REVIEWER_FALLBACK", message)


def _family(model):
    name = model.rsplit("/", 1)[-1].lower()
    if model.startswith("zai-coding-plan/") or name.startswith("glm-"):
        return "glm"
    if model.startswith("xiaomi-token-plan-sgp/") or name.startswith("mimo-"):
        return "mimo"
    return model


def _route(config):
    return {key: config.get(key) for key in ("engine", "provider", "model", "reasoning_effort")}


def _reviewer_role(state):
    roles = state.get("settings", {}).get("roles", {})
    return "plan_reviewer" if "plan_reviewer" in roles else "astra"


def _producer_role(state, stage):
    roles = state.get("settings", {}).get("roles", {})
    preferred = (
        ("technical_planner", "glm") if stage in ("plan_review", "plan_finalize") else ("glm", "technical_planner")
    )
    return next((role for role in preferred if (roles.get(role) or {}).get("model")), None)


def _explicitly_pinned(settings, reviewer_role, current):
    return bool(
        current.get("model_pinned")
        or current.get("route_pinned")
        or settings.get("reviewer_route_pinned")
        or settings.get("plan_reviewer_route_pinned")
        or reviewer_role in settings.get("pinned_routes", [])
    )


def _input_pins(state):
    paths = {}
    planning = state.get("planning") or {}
    entries = list((planning.get("reports") or {}).values())
    if state.get("requirements_handoff"):
        entries.append(state["requirements_handoff"])
    for entry in entries:
        path = entry.get("output") if isinstance(entry, dict) else None
        if path:
            file = Path(path)
            if not file.is_file():
                raise ValueError("missing planning input")
            paths[str(file)] = support.file_hash(file)
    return paths


def _cycle(state, stage):
    planning = state.get("planning") or {}
    reports = planning.get("reports") or {}
    discovery = reports.get("astra_discovery") or reports.get("plan") or {}
    contract = state.get("goal_contract") or {}
    return support.digest(
        {
            "discovery": discovery.get("output"),
            "planning_history": len(state.get("planning_history", [])),
            "task_id": contract.get("task_id", state.get("task_id")),
        }
    )


def _attempt_route(record):
    saved = record.get("launch_route")
    if isinstance(saved, dict):
        return _route(saved)
    # Current runner records contain the exact model in the launch command.  A
    # future caller should save launch_route so provider=None is also explicit.
    command = record.get("command") or []
    if "--model" not in command:
        return None
    model = command[command.index("--model") + 1]
    provider = None
    for index, value in enumerate(command[:-1]):
        if value == "-c" and str(command[index + 1]).startswith('model_provider="'):
            provider = command[index + 1][len('model_provider="') : -1]
    return {
        "engine": record.get("engine"),
        "provider": provider,
        "model": model,
        "reasoning_effort": record.get("reasoning_effort"),
    }


def _silent_attempt(record, expected, source_revision, *, focused):
    if (
        record.get("stage") not in REVIEW_STAGES
        or record.get("timed_out") is not True
        or record.get("timeout_kind") != "idle"
        or record.get("report_only")
        or not all(record.get(key) for key in ("accounted", "automatic_recovery", "abandoned", "rejected"))
        or record.get("changed_files") != []
        or record.get("source_revision") != source_revision
        or (focused and not (record.get("planning_recovery_grant") or record.get("focused_same_route_recovery")))
    ):
        return None
    if _attempt_route(record) != expected:
        return None
    evidence = {}
    for key in ("events", "before_ref", "after_ref"):
        path = Path(record.get(key) or "")
        if not path.is_file():
            return None
        evidence[str(path)] = support.file_hash(path)
    if any(event.get("type") == "turn.completed" for event in support.events(record["events"])):
        return None
    return evidence


def _evidence(state, stage, current_route, source_revision):
    attempts = [row for row in state.get("stages", []) if not row.get("runner_owned") and row.get("stage") == stage]
    if len(attempts) < 2:
        return None
    ordinary, focused = attempts[-2:]
    first = _silent_attempt(ordinary, current_route, source_revision, focused=False)
    second = _silent_attempt(focused, current_route, source_revision, focused=True)
    if not first or not second:
        return None
    return {
        "attempts": [ordinary.get("output"), focused.get("output")],
        "pins": {**first, **second},
        "records_hash": support.digest([ordinary, focused]),
        "observation": "repeated nonterminal planning-review provider-stream silence",
    }


def _candidate(state, stage, current_role, current):
    settings = state["settings"]
    roles = settings.get("roles") or {}
    producer_role = _producer_role(state, stage)
    if not producer_role:
        return None
    producer_model = roles[producer_role]["model"]
    choices = []
    for role, config in roles.items():
        if role == current_role or not isinstance(config, dict):
            continue
        route = _route(config)
        if (
            not isinstance(route["model"], str)
            or not route["model"]
            or route["model"] == current.get("model")
            or route["engine"] != "opencode"
            or route["engine"] != current.get("engine")
            or route["provider"] != current.get("provider")
            or config.get("available") is False
            or _family(route["model"]) == _family(producer_model)
        ):
            continue
        choices.append((role, route))
    if len(choices) != 1:
        # Ambiguity is not authority to pick a billing or permission route.
        return None
    role, route = choices[0]
    return {"role": role, **route, "producer_role": producer_role, "producer_model": producer_model}


def _binding(state, stage, workspace, current, selected, evidence):
    inputs = _input_pins(state)
    contract = state.get("goal_contract") or {}
    return {
        "source_revision": source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)["revision"],
        "contract_hash": contract.get("hash"),
        "contract_digest": support.digest(contract),
        "cycle": _cycle(state, stage),
        "stage": stage,
        "settings_hash": support.digest(state["settings"]),
        "inputs": inputs,
        "inputs_hash": support.digest(inputs),
        "evidence": evidence,
        "evidence_hash": support.digest(evidence),
        "current_route": current,
        "selected_route": selected,
    }


def _existing(state, cycle):
    grants = (state.get("planning") or {}).get("reviewer_route_fallbacks", [])
    if not isinstance(grants, list) or any(not isinstance(grant, dict) for grant in grants):
        _stop("Saved reviewer fallback state is malformed; no provider will launch")
    return next((grant for grant in grants if grant.get("binding", {}).get("cycle") == cycle), None)


def reserve(state, run_dir, workspace, stage=None):
    """Reserve one fallback after an ordinary attempt and same-route recovery stall.

    Returns the saved grant, or None when policy is ineligible.  Ineligibility is
    intentionally non-mutating so the resolver-owned human request remains.
    """
    stage = stage or state.get("next_stage")
    if stage not in REVIEW_STAGES:
        return None
    settings = state.get("settings") or {}
    roles = settings.get("roles") or {}
    reviewer_role = _reviewer_role(state)
    reviewer = roles.get(reviewer_role) or {}
    current = _route(reviewer)
    if (
        not isinstance(current["model"], str)
        or not current["model"]
        or current["engine"] != "opencode"
        or _explicitly_pinned(settings, reviewer_role, reviewer)
    ):
        return None
    cycle = _cycle(state, stage)
    old = _existing(state, cycle)
    if old:
        return old if not old.get("consumed") else None
    source_revision = source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)["revision"]
    evidence = _evidence(state, stage, current, source_revision)
    selected = _candidate(state, stage, reviewer_role, reviewer)
    if not evidence or not selected:
        return None
    binding = _binding(state, stage, workspace, current, selected, evidence)
    identity = support.digest({"scope": SCOPE, "binding": binding})
    path = Path(run_dir) / "resolver" / f"{identity}.json"
    receipt = {
        "stage": "resolver",
        "role": "resolver",
        "engine": "runner",
        "runner_owned": True,
        "iteration": state.get("iteration"),
        "finished_at": support.now(),
        "exit_code": 0,
        "runner_calls": 0,
        "output": str(path),
        "metrics": {"provider_tokens": {"input_tokens": 0, "output_tokens": 0}},
        "decision": {
            "action": "route_fallback",
            "rationale": "Observed repeated nonterminal planning-review provider-stream silence; selecting one already configured independent reviewer route.",
        },
        "receipt": {"id": identity, "scope": SCOPE, "callbacks_used": [], "binding": binding},
    }
    support.atomic_json(path, receipt)
    grant = {
        "id": identity,
        "binding": binding,
        "receipt_output": str(path),
        "receipt_hash": support.file_hash(path),
        "consumed": False,
    }
    state.setdefault("planning", {}).setdefault("reviewer_route_fallbacks", []).append(grant)
    state.setdefault("stages", []).append(receipt)
    state.setdefault("history", []).append(copy.deepcopy(receipt))
    return grant


def admit(state, run_dir, workspace, stage=None):
    """Revalidate and consume the sole fallback at the provider admission lock."""
    stage = stage or state.get("next_stage")
    cycle = _cycle(state, stage)
    grant = _existing(state, cycle)
    if not grant or grant.get("consumed"):
        _stop("Reviewer fallback is absent or already consumed; no provider will launch")
    try:
        binding = grant["binding"]
        current = binding["current_route"]
        selected = binding["selected_route"]
        reviewer = state["settings"]["roles"][_reviewer_role(state)]
        evidence = _evidence(state, stage, current, binding["source_revision"])
        if (
            _explicitly_pinned(state["settings"], _reviewer_role(state), reviewer)
            or _route(reviewer) != current
            or _candidate(state, stage, _reviewer_role(state), reviewer) != selected
            or evidence != binding["evidence"]
            or _binding(state, stage, workspace, current, selected, evidence) != binding
        ):
            _stop("Reviewer fallback bindings changed; no provider will launch")
        receipt_path = Path(grant["receipt_output"])
        if not receipt_path.is_file() or support.file_hash(receipt_path) != grant["receipt_hash"]:
            _stop("Reviewer fallback receipt changed; no provider will launch")
        saved = support.read(receipt_path)
        if (
            saved.get("receipt", {}).get("id") != grant["id"]
            or saved["receipt"].get("binding") != binding
            or saved["receipt"].get("scope") != SCOPE
        ):
            _stop("Reviewer fallback differs from its receipt; no provider will launch")
    except support.Paused:
        raise
    except (KeyError, TypeError, ValueError, OSError) as error:
        _stop(f"Reviewer fallback cannot be validated: {error}")
    grant.update(consumed=True, consumed_at=support.now(), admission_stage=stage)
    return copy.deepcopy(selected)


def pending_route(state, stage=None):
    """Return the already reserved route for command preparation, without authority."""
    stage = stage or state.get("next_stage")
    if stage not in REVIEW_STAGES:
        return None
    grant = _existing(state, _cycle(state, stage))
    if not grant or grant.get("consumed"):
        return None
    selected = grant.get("binding", {}).get("selected_route")
    return copy.deepcopy(selected) if isinstance(selected, dict) else None


def record_failed(state, grant_id, record=None):
    """Record exhaustion without creating another route switch or changing counts."""
    grants = (state.get("planning") or {}).get("reviewer_route_fallbacks", [])
    grant = next((item for item in grants if item.get("id") == grant_id), None)
    if not grant or not grant.get("consumed"):
        _stop("Only an admitted reviewer fallback can be marked failed")
    if "failed_at" not in grant:
        grant.update(
            failed_at=support.now(),
            outcome="observed provider-stream silence",
            failed_record_hash=support.digest(record) if record is not None else None,
        )
    return grant

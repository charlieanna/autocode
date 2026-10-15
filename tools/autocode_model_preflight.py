"""Probe each billing-routed role model once per run, before any stage spend.

A roster check (``check_models``) and a login listing (``check_subscription_routes``)
cannot see a gateway whose balance is gone: a Kilo Gateway route passed both and
failed with HTTP 402 "Add credits to continue" only after ten minutes of GLM work,
when the first MiMo stage launched. One tiny call per distinct role model at build
start turns that class of failure into an immediate, named stop.

Only providers whose config declares ``[auth]`` billing routes are probed; scripted
test providers and native engine paths keep their zero-call launches. An
access-class failure (budget, auth, billing route) refuses the build before any
stage dispatch. Anything transient (capacity, a slow answer, a garbled reply) is
reported and the run continues: the probe proves access, not model obedience.

Writes ``run_dir/model-preflight.json`` and one evidence directory per probe, so a
later invocation under the same saved transport re-probes only models that have
not yet passed. Imports nothing from the controller; the runner is passed in.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from .autocode_util import atomic_json, digest
except ImportError:
    from autocode_util import atomic_json, digest

try:
    from . import autocode_cost as cost
    from . import autocode_preflight_attempts as attempts
    from . import autocode_process as processes
    from . import autocode_support as support
    from . import provider_conformance_transport as transport
except ImportError:
    import autocode_cost as cost
    import autocode_preflight_attempts as attempts
    import autocode_process as processes
    import autocode_support as support
    import provider_conformance_transport as transport

PROMPT = 'Do not read or edit files. Return only this JSON object: {"ok": true}.'
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
# Persistent until the operator changes money or login: a launch that ignores this
# wastes real spend on stages that cannot complete. Everything else is transient.
BLOCKING_STATUSES = ("PAUSED_BUDGET", "PAUSED_AUTH", "PAUSED_BILLING_ROUTE")
TIMEOUT_SECONDS = 90


def billing_aware(adapter) -> bool:
    """Whether ``adapter`` routes models through declared billing logins (a TOML [auth])."""
    config = getattr(adapter, "_config", None)
    return isinstance(config, dict) and isinstance(config.get("auth"), dict)


def _cache_identity(adapter, settings) -> dict | None:
    identities = settings.get("transport_identities") or {}
    transport = identities.get("opencode", settings.get("transport_identity"))
    if not isinstance(transport, dict):
        return None
    return {
        "provider": getattr(adapter, "NAME", None) or transport.get("engine") or "opencode",
        "transport_sha256": digest(transport),
    }


def distinct_models(settings) -> list[str]:
    """Role models in declaration order, without duplicates."""
    roles = (settings or {}).get("roles") or {}
    models: list[str] = []
    for route in roles.values():
        model = (route or {}).get("model") if isinstance(route, dict) else None
        if isinstance(model, str) and model.strip() and model not in models:
            models.append(model)
    return models


def _classify(events_path: Path) -> tuple[str | None, str]:
    rows = support.events(events_path) if events_path.is_file() else []
    failures = [row for row in rows if row.get("type") in ("error", "turn.failed")]
    if not failures and not rows:
        return "PAUSED_PROVIDER_UNCERTAIN", "the provider produced no events"
    if not failures:
        return None, ""
    status = support.failure_status(events_path, record=None)
    message = ""
    error = failures[-1].get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("data", {}).get("message") or "").strip()
    return status, message or status


def probe(
    adapter, model, workspace, run_dir, *, timeout=TIMEOUT_SECONDS, directory=None, checkpoint=None, deadline=None
) -> dict:
    """One tiny read-only call for ``model``; returns its recorded outcome."""
    directory = directory or run_dir / "model-preflight" / model.replace("/", "__") / f"attempt-{uuid.uuid4().hex}"
    started = time.monotonic()
    try:
        result = transport.execute(
            adapter=adapter,
            workspace=workspace,
            directory=directory,
            model=model,
            effort="low",
            session=None,
            allow_write=False,
            prompt=PROMPT,
            schema=SCHEMA,
            env=dict(os.environ),
            timeout=timeout,
            supervised=True,
            checkpoint=checkpoint,
            deadline=deadline,
        )
    except (processes.ProcessError, OSError) as error:
        raise support.Paused(
            "PAUSED_PROCESS_CLEANUP",
            f"Model access preflight could not verify provider ownership for {model}: {error}. "
            f"Probe evidence: {directory}",
        ) from error
    events_path = directory / "events.jsonl"
    status: str | None
    if result.get("timed_out"):
        status, message = "PAUSED_PROVIDER_UNCERTAIN", f"probe timed out after {timeout} seconds"
    else:
        status, message = _classify(events_path)
        if status is None and result.get("exit_code"):
            status, message = "PAUSED_PROVIDER_UNCERTAIN", f"provider exited {result['exit_code']}"
    return {
        "model": model,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seconds": round(time.monotonic() - started, 2),
        "status": status or "ok",
        "detail": message,
        "blocking": status in BLOCKING_STATUSES,
        "evidence": str(directory),
        "metrics": result.get("metrics") or {},
        "exit_code": result.get("exit_code"),
        "timed_out": bool(result.get("timed_out")),
    }


def _allowance(runner, state: RunState, run_dir, workspace, timeout):
    reached = cost.exceeded(state)
    if reached:
        raise support.Paused(
            "PAUSED_COST_LIMIT",
            f"Spend so far {cost.usd(reached[0])} reached the {cost.usd(reached[1])} cap before model access.",
        )
    limits = (state.get("settings") or {}).get("limits") or {}
    maximum = limits.get("max_seconds")
    if maximum and state.get("active_seconds", 0) >= maximum:
        recover = getattr(runner, "recover_default_budget", None)
        if recover is None or not recover(state, run_dir, workspace, "max_seconds"):
            raise support.Paused("PAUSED_TIME_LIMIT", "Saved active-time limit reached before model access")
        maximum = ((state.get("settings") or {}).get("limits") or {}).get("max_seconds")
    remaining = maximum - state.get("active_seconds", 0) if maximum else None
    return min(timeout, remaining) if timeout is not None and remaining is not None else remaining or timeout


def check(
    runner, state: RunState, run_dir, workspace, *, timeout=TIMEOUT_SECONDS, checkpoint=None, before_probe=None
) -> bool:
    """Run admitted, charged probes; unbound legacy passes are probed again.

    checkpoint durably saves the caller's state. before_probe consumes queued
    human input under the caller's existing lock, including after the last call.
    """
    if getattr(runner, "native_calls", None) is not None:
        # Native calls require their original held, accounted launch authority.
        # Standalone access probes cannot borrow that authority or its budget.
        return False
    adapter = getattr(runner, "opencode", None)
    if adapter is None or not billing_aware(adapter):
        return False
    checkpoint = checkpoint or (lambda: None)
    before_probe = before_probe or (lambda: None)
    run_dir = Path(run_dir)
    attempts.reconcile(runner, state, run_dir, checkpoint)
    marker = run_dir / "model-preflight.json"
    identity = _cache_identity(adapter, state.get("settings") or {})
    probed: dict[str, dict] = {}
    if marker.is_file():
        try:
            saved = json.loads(marker.read_text())
            if saved.get("version") == 2 and saved.get("identity") == identity and identity is not None:
                cached = saved.get("probed")
                probed = cached if isinstance(cached, dict) else {}
        except (OSError, ValueError, AttributeError):
            probed = {}
    # The charged checkpoint is authoritative if marker publication was interrupted.
    for record in attempts.records(state, run_dir):
        if (
            identity is not None
            and record.get("preflight_identity") == identity
            and record.get("preflight_complete")
            and record.get("accounted")
            and record.get("preflight_outcome")
        ):
            probed[record["model"]] = record["preflight_outcome"]
    wanted = [
        model for model in distinct_models(state.get("settings")) if (probed.get(model) or {}).get("status") != "ok"
    ]
    if not wanted:
        return False
    before_probe()
    _allowance(runner, state, run_dir, workspace, timeout)
    settings = state.get("settings") or {}
    try:
        adapter.check_subscription_routes(settings.get("roles") or {}, workspace)
    except RuntimeError as error:
        raise support.Paused(
            "PAUSED_BILLING_ROUTE",
            f"Model access preflight could not verify billing routes: {error}",
        ) from error
    for model in wanted:
        before_probe()
        allowed = _allowance(runner, state, run_dir, workspace, timeout)
        attempt = attempts.Attempt(
            runner,
            state,
            run_dir,
            adapter,
            model,
            checkpoint,
            time.monotonic,
            preflight_identity=identity,
        )
        finite_time = ((state.get("settings") or {}).get("limits") or {}).get("max_seconds")
        try:
            outcome = probe(
                adapter,
                model,
                workspace,
                run_dir,
                timeout=allowed,
                directory=attempt.directory,
                checkpoint=attempt.admit,
                deadline=attempt.started + allowed if finite_time and allowed is not None else None,
            )
        except BaseException as error:
            attempt.finish(error=error)
            # A Stop may be acknowledged only after native cleanup is verified.
            if attempt.record.get("preflight_complete"):
                before_probe()
            if isinstance(error, KeyboardInterrupt):
                raise support.Paused(
                    "PAUSED_INTERRUPTED", "Model access probe interrupted; evidence retained"
                ) from error
            raise
        outcome = attempt.finish(outcome)
        probed[model] = outcome
        atomic_json(marker, {"version": 2, "identity": identity, "probed": probed})
        # Stop wins over budget and must also apply after the final probe.
        before_probe()
        if outcome["blocking"]:
            raise support.Paused(
                outcome["status"],
                f"Model access preflight failed for {model}: {outcome['detail'] or outcome['status']}. "
                "Billing or login for this route cannot serve the model; fix it before any stage spend. "
                f"Probe evidence: {outcome['evidence']}",
            )
        _allowance(runner, state, run_dir, workspace, timeout)
        if outcome["status"] == "ok":
            print(f"Model preflight: {model} ok ({outcome['seconds']}s)", flush=True)
        else:
            print(
                f"Model preflight: {model} uncertain ({outcome['status']}: {outcome['detail'] or 'no detail'}); "
                "continuing within the remaining run budget",
                flush=True,
            )
    return True

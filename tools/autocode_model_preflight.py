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
later invocation re-probes only models that have not yet passed. Imports nothing
from the controller; the runner is passed in.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

try:
    from . import autocode_process as processes
    from . import autocode_support as support
    from . import provider_conformance_transport as transport
except ImportError:
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


def probe(adapter, model, workspace, run_dir, *, timeout=TIMEOUT_SECONDS) -> dict:
    """One tiny read-only call for ``model``; returns its recorded outcome."""
    directory = run_dir / "model-preflight" / model.replace("/", "__") / f"attempt-{uuid.uuid4().hex}"
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
    }


def check(runner, state, run_dir, workspace, *, timeout=TIMEOUT_SECONDS) -> bool:
    """Probe every billing-routed role model this run has not probed yet.

    True when at least one probe ran. Raises ``support.Paused`` on an access-class
    failure so the run stops before any stage spend, with the model named.
    """
    adapter = getattr(runner, "opencode", None)
    if adapter is None or not billing_aware(adapter):
        return False
    marker = Path(run_dir) / "model-preflight.json"
    probed: dict[str, dict] = {}
    if marker.is_file():
        try:
            saved = json.loads(marker.read_text())
            probed = saved.get("probed") or {}
        except (OSError, ValueError):
            probed = {}
    wanted = [
        model for model in distinct_models(state.get("settings")) if (probed.get(model) or {}).get("status") != "ok"
    ]
    if not wanted:
        return False
    settings = state.get("settings") or {}
    try:
        # The implicit call matches the stage gate exactly: the auth listing child gets the
        # scrubbed launch projection, and a failure names the withheld variables itself.
        adapter.check_subscription_routes(settings.get("roles") or {}, workspace)
    except RuntimeError as error:
        # A failed login listing must stop before any model call, the same
        # property the stage's own admission gate enforces (tests/test_provider_credentials).
        raise support.Paused(
            "PAUSED_BILLING_ROUTE",
            f"Model access preflight could not verify billing routes: {error}",
        ) from error
    ran = False
    for model in wanted:
        ran = True
        outcome = probe(adapter, model, workspace, Path(run_dir), timeout=timeout)
        probed[model] = outcome
        marker.write_text(json.dumps({"version": 1, "probed": probed}, indent=2) + "\n")
        if outcome["status"] == "ok":
            print(f"Model preflight: {model} ok ({outcome['seconds']}s)", flush=True)
            continue
        if outcome["blocking"]:
            raise support.Paused(
                outcome["status"],
                f"Model access preflight failed for {model}: {outcome['detail'] or outcome['status']}. "
                "Billing or login for this route cannot serve the model; fix it before any stage spend. "
                f"Probe evidence: {outcome['evidence']}",
            )
        print(
            f"Model preflight: {model} uncertain ({outcome['status']}: {outcome['detail'] or 'no detail'}); "
            "continuing, the stage budget still applies",
            flush=True,
        )
    return ran

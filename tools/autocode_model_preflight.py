"""Probe each billing-routed role model once per run, before any stage spend.

A roster check (``check_models``) and a login listing (``check_subscription_routes``)
cannot see a gateway whose balance is gone: a Kilo Gateway route passed both and
failed with HTTP 402 "Add credits to continue" only after ten minutes of GLM work,
when the first MiMo stage launched. One tiny call per distinct role model at build
start turns that class of failure into an immediate, named stop.

Only providers whose config declares ``[auth]`` billing routes are probed; scripted
test providers and native engine paths keep their zero-call launches. Each role
is probed through the tool that launches it: a role bound with ``--role-transport``
uses its own saved tool (``autocode_role_transport.groups``), every other role the
run's tool. Every relevant login is checked before the first request. An
access-class failure (budget, auth, billing route) refuses the build before any
stage dispatch. Anything transient (capacity, a slow answer, a garbled reply) is
reported and the run continues: the probe proves access, not model obedience.

Writes ``run_dir/model-preflight.json`` and one evidence directory per probe. The
marker keeps one receipt set per tool identity (provider name plus saved transport
digest), with the route's reasoning effort included when it names one, so a later
invocation re-probes only model/level pairs that have not yet passed on that same
tool and a passed level is certified for later fits (#2057). Imports nothing from
the controller; the runner is passed in.

The automatic selector's access check (#1426 step 8) is the same machinery with a
different source of routes: pass ``check(..., selection=...)`` the selector's result
and the actual saved routes are probed at each role's own reasoning level, through
the tool that will run it — including built-in OpenCode routes. Initial selection
metadata is used only by standalone callers without saved routes.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from .autocode_util import atomic_json, digest
except ImportError:
    from autocode_util import atomic_json, digest

try:
    from . import autocode_access_evidence as access_evidence
    from . import autocode_access_identity as access_identity
    from . import autocode_agent_env as agent_env
    from . import autocode_cost as cost
    from . import autocode_preflight_attempts as attempts
    from . import autocode_process as processes
    from . import autocode_provider_auth as provider_auth
    from . import autocode_provider_env as provider_env
    from . import autocode_role_transport as role_transport
    from . import autocode_route_fit as route_fit
    from . import autocode_support as support
    from . import provider_conformance_transport as transport
except ImportError:
    import autocode_access_evidence as access_evidence
    import autocode_access_identity as access_identity
    import autocode_agent_env as agent_env
    import autocode_cost as cost
    import autocode_preflight_attempts as attempts
    import autocode_process as processes
    import autocode_provider_auth as provider_auth
    import autocode_provider_env as provider_env
    import autocode_role_transport as role_transport
    import autocode_route_fit as route_fit
    import autocode_support as support
    import provider_conformance_transport as transport

PROMPT = 'Do not read or edit files. Return only this JSON object: {"ok": true}.'
# The probe proves access, not tool use (#1744): OpenCode and Kilo wrapped the one-line prompt in their coding
# agent's system prompt and tool list, ~17-19k input tokens a call ($0.19 on GPT-6 Astra). This agent has no
# tools and a one-line system prompt: ~200-600 input tokens.
PROBE_AGENT = "autocode_access_probe"
PROBE_SYSTEM = "Answer with exactly the JSON object you are asked for."
CONFIG_VARIABLES = ("OPENCODE_CONFIG_CONTENT", "KILO_CONFIG_CONTENT")
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
# Persistent until the operator changes money or login: a launch that ignores this
# wastes real spend on stages that cannot complete. Everything else is transient.
BLOCKING_STATUSES = ("PAUSED_BUDGET", "PAUSED_AUTH", "PAUSED_BILLING_ROUTE")
TIMEOUT_SECONDS = 90
# Roles probed only when a stage that runs them is next (#1744). The Resolver (and the per-task Planner on the
# same route) is usually GPT-6 Astra, the costliest model, and runs only after a send-back; its probe cost about
# $0.19 of a small task's $1.31. Its first stage still proves access before that stage's own spend.
DEFERRED = {"astra": ("astra_plan", "astra_resolve"), "resolver": ("astra_resolve",)}
DEFERRED_STAGES = frozenset(stage for stages in DEFERRED.values() for stage in stages)
# Version 2 held one receipt set for the run's tool; it is still read (_saved_receipts).
MARKER_VERSION = 3


def billing_aware(adapter) -> bool:
    """Whether ``adapter`` routes models through declared billing logins (a TOML [auth])."""
    config = getattr(adapter, "_config", None)
    return isinstance(config, dict) and isinstance(config.get("auth"), dict)


def _receipt_identity(adapter, transport) -> dict | None:
    """The tool a receipt was earned on; a pass is reused only under the same identity."""
    if not isinstance(transport, dict):
        return None
    return {
        "provider": getattr(adapter, "NAME", None) or transport.get("engine") or "opencode",
        "transport_sha256": digest(transport),
    }


def _billing_groups(runner, settings, workspace) -> list[tuple]:
    """(facade, routes, receipt identity) for each billing-aware tool that launches saved roles.

    A role bound with --role-transport resolves to its own saved tool, and a changed
    bound tool pauses with PAUSED_TRANSPORT_CHANGED before any request; every other
    OpenCode-engine role uses the run's tool. Groups that share one identity share
    one login check and one probe per model.
    """
    groups: list[tuple] = []
    for facade, routes, transport in role_transport.groups(settings, getattr(runner, "opencode", None), workspace):
        if not billing_aware(facade):
            continue
        identity = _receipt_identity(facade, transport)
        same = next((group for group in groups if identity is not None and group[2] == identity), None)
        if same is None:
            groups.append((facade, dict(routes), identity))
        else:
            same[1].update(routes)
    return groups


def _saved_receipts(marker: Path) -> list[dict]:
    """Saved {identity, probed} sets; a version 2 marker is one set under its own identity."""
    entries: object
    try:
        saved = json.loads(marker.read_text()) if marker.is_file() else {}
        if saved.get("version") == 2:
            entries = [saved]
        elif saved.get("version") == MARKER_VERSION:
            entries = saved.get("receipts")
        else:
            return []
    except (OSError, ValueError, AttributeError):
        return []
    if not isinstance(entries, list):
        return []
    return [
        entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("identity"), dict) and isinstance(entry.get("probed"), dict)
    ]


def distinct_models(settings) -> list[str]:
    """Role models in declaration order, without duplicates."""
    roles = (settings or {}).get("roles") or {}
    models: list[str] = []
    for route in roles.values():
        model = (route or {}).get("model") if isinstance(route, dict) else None
        if isinstance(model, str) and model.strip() and model not in models:
            models.append(model)
    return models


def _classify(events_path: Path, *, withheld_env=()) -> tuple[str | None, str]:
    rows = support.events(events_path) if events_path.is_file() else []
    failures = [row for row in rows if row.get("type") in ("error", "turn.failed")]
    if not failures and not rows:
        return "PAUSED_PROVIDER_UNCERTAIN", "the provider produced no events"
    if not failures:
        return None, ""
    status = support.failure_status(events_path, record=None)
    if auth := provider_auth.authentication_reason(failures):
        hint = provider_env.authentication_hint(rows, withheld_env)
        return status, f"{auth}. {hint}" if hint else auth
    message = ""
    error = failures[-1].get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("data", {}).get("message") or "").strip()
    return status, message or status


def bare(argv: list, env: dict) -> tuple[list, dict]:
    """Run an OpenCode-family probe as ``PROBE_AGENT``: no tools, every permission denied, a one-line system prompt.

    A launch that names no agent through an inline configuration (another tool) is left as it is."""
    if "--agent" not in argv[:-1]:
        return argv, env
    for variable in CONFIG_VARIABLES:
        if variable not in env:
            continue
        config = json.loads(env[variable])
        agents = config.get("agent") if isinstance(config, dict) else None
        if not isinstance(agents, dict):
            return argv, env
        chosen = agents.get(argv[argv.index("--agent") + 1])
        definition: dict[str, Any] = {
            "description": "AutoCode model access probe",
            "mode": "primary",
            "prompt": PROBE_SYSTEM,
        }
        definition.update(tools={"*": False}, permission={"*": "deny"})
        if isinstance(chosen, dict) and chosen.get("model"):
            definition["model"] = chosen["model"]
        config["agent"] = {**agents, PROBE_AGENT: definition}
        argv[argv.index("--agent") + 1] = PROBE_AGENT
        return argv, {**env, variable: json.dumps(config)}
    return argv, env


def probe(
    adapter,
    model,
    workspace,
    run_dir,
    *,
    effort="low",
    timeout=TIMEOUT_SECONDS,
    directory=None,
    checkpoint=None,
    deadline=None,
    role=None,
) -> dict:
    """One tiny read-only call for ``model``; returns its recorded outcome.

    Every route probes at the role's own reasoning level, saved or selected, because
    the level is part of the route: a variant that does not exist fails at the probe,
    not three stages in (#1426 step 8, #2057). A passed probe certifies that level for
    later fits. ``None`` sends no level and the model uses its default. ``role`` carries
    the role whose route is proven, so a tool refusing the level names its flag (#2108).
    """
    directory = directory or run_dir / "model-preflight" / model.replace("/", "__") / f"attempt-{uuid.uuid4().hex}"
    started = time.monotonic()
    launch_env = dict(os.environ)
    withheld_env = agent_env.withheld(launch_env)
    try:
        result = transport.execute(
            adapter=adapter,
            workspace=workspace,
            directory=directory,
            model=model,
            effort=effort,
            session=None,
            allow_write=False,
            prompt=PROMPT,
            schema=SCHEMA,
            env=launch_env,
            timeout=timeout,
            supervised=True,
            checkpoint=checkpoint,
            deadline=deadline,
            prepare=bare,
            role=role,
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
        status, message = _classify(events_path, withheld_env=withheld_env)
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


def _selection_groups(runner, selection, workspace, settings=None) -> list[tuple]:
    """(facade, routes, identity) per tool an automatic selection routes through.

    Unlike saved routes, a selection's groups are not filtered by ``billing_aware``:
    built-in OpenCode routes are probed too, because the selector chose them and a
    login the engine holds is exactly what a probe proves (#1426 step 8). Identity is
    the tool itself — the provider's own configuration digest — so a receipt earned
    on one tool is never reused for another, and a config change re-probes.
    """
    if (settings or {}).get("roles"):
        # Resolve the actual saved routes: explicit bindings, derived roles and
        # recovery can differ from the initial selection. The transport layer
        # verifies immutable bindings before a receipt can be reused.
        return [
            (facade, routes, _receipt_identity(facade, transport))
            for facade, routes, transport in role_transport.groups(
                settings, getattr(runner, "opencode", None), workspace
            )
        ]
    try:
        from providers import command as command_provider
    except ImportError:
        from .providers import command as command_provider
    by_tool: dict[str, dict] = {}
    for role, route in (selection.get("routes") or {}).items():
        tool = route.get("tool") or "opencode"
        by_tool.setdefault(tool, {})[role] = {
            "model": route.get("model"),
            "reasoning_effort": route.get("reasoning_effort"),
        }
    groups: list[tuple] = []
    for tool in sorted(by_tool):
        if tool == "opencode":
            facade = getattr(runner, "opencode", None)
            if facade is None:
                continue
            identity = _receipt_identity(facade, {"engine": "opencode"})
        else:
            try:
                facade = command_provider.load(tool)
            except (RuntimeError, ValueError, OSError):
                continue
            identity = _receipt_identity(facade, facade._config)
        groups.append((facade, by_tool[tool], identity))
    return groups


def due(routes: dict, next_stage) -> dict:
    """The routes to probe before ``next_stage``: every role but a deferred one whose stage is not next.

    With no next stage known (a standalone caller), every route is due."""
    if not next_stage:
        return dict(routes)
    # A standalone review has one execution stage: that request establishes access.
    # A separate paid ping cannot prevent partially built work here and duplicates
    # provider startup. Local transport/auth/model/effort admission still runs;
    # a failed review remains an accounted stage failure, never a successful probe.
    if next_stage == "review_change":
        return {}
    return {role: route for role, route in routes.items() if next_stage in DEFERRED.get(role, (next_stage,))}


def _selected_efforts(routes) -> dict:
    """model -> the reasoning level to probe it at: its first role's own level."""
    efforts: dict[str, str | None] = {}
    for role in sorted(routes):
        route = routes[role] or {}
        model = route.get("model")
        if isinstance(model, str) and model and model not in efforts:
            efforts[model] = route.get("reasoning_effort")
    return efforts


def _selected_roles(routes) -> dict:
    """model -> the role whose route a probe proves: the first role (in role order) that names it."""
    roles: dict[str, str] = {}
    for role in sorted(routes):
        model = (routes[role] or {}).get("model")
        if isinstance(model, str) and model and model not in roles:
            roles[model] = role
    return roles


def fitted_routes(settings, routes) -> dict:
    """Each route at the level the run's launch boundary will pass (#2104, #2057).

    The access preflight is the first launch boundary of a build. Fitting here means a
    person-named level its model does not declare pauses with its named answer before any
    probe spends, and a runner-chosen level is probed at the variant a stage will launch
    (``autocode_route_fit.launch`` fits the saved level the same way at dispatch). The saved
    route is left alone: the stage's launch records the fit in ``effort_fits``.
    """
    fitted = {}
    for role, route in routes.items():
        route = dict(route or {})
        result = route_fit.fit(settings, role, effort=route.get("reasoning_effort"), model=route.get("model"))
        if "reasoning_effort" in route or result.effort is not None:
            route["reasoning_effort"] = result.effort
        fitted[role] = route
    return fitted


def certify(state: RunState, model, effort, outcome, checkpoint) -> None:
    """A probe that passed proves this model serves this level: record it for later fits (#2057).

    The fit reads these levels before a launch; a probe is evidence, never a guess. They are kept
    apart from the tool's listing: one passed level says nothing about the levels no probe tried.
    Nothing is recorded for a level-less route or an uncertain outcome.
    """
    if not effort or not isinstance(model, str) or (outcome or {}).get("status") != "ok":
        return
    settings = state.get("settings")
    if not isinstance(settings, dict):
        return
    recorded = settings.setdefault(route_fit.CERTIFIED, {}).setdefault(model, [])
    if effort in recorded:
        return
    recorded.append(effort)
    recorded.sort(key=lambda level: route_fit.SCALE.index(level) if level in route_fit.SCALE else len(route_fit.SCALE))
    checkpoint()


def check(
    runner,
    state: RunState,
    run_dir,
    workspace,
    *,
    timeout=TIMEOUT_SECONDS,
    checkpoint=None,
    before_probe=None,
    selection=None,
) -> bool:
    """Run admitted, charged probes; unbound legacy passes are probed again.

    checkpoint durably saves the caller's state. before_probe consumes queued
    human input under the caller's existing lock, including after the last call.
    ``selection`` is an automatic selector's result (#1426 step 8): exactly the
    routes it lists as still unknown are probed, each at its role's own reasoning
    level, through the tool that will run it, within the same cost cap and receipts.

    Every role's saved level is fitted before the first probe (#2104): a person-named
    level the model does not declare pauses as ``PAUSED_EFFORT_UNSUPPORTED`` with its
    named answer, and a runner-chosen one is probed at the variant a stage will launch.
    """
    if getattr(runner, "native_calls", None) is not None:
        # Native calls require their original held, accounted launch authority.
        # Standalone access probes cannot borrow that authority or its budget.
        return False
    if (state.get("settings") or {}).get("spec_first") and state.get("next_stage") in (
        "recognize_workflow",
        "requirements_gather",
    ):
        # Recognition and the read-only conversation are themselves the
        # requested model calls. Future execution roles are probed when
        # approved requirements release planning; do not make the user wait.
        return False
    settings = state.get("settings") or {}
    route_fit.guard(settings)
    groups = (
        _selection_groups(runner, selection, workspace, settings)
        if selection is not None
        else _billing_groups(runner, settings, workspace)
    )
    groups = [(facade, fitted_routes(settings, routes), identity) for facade, routes, identity in groups]
    # A successful low-level probe cannot certify another role's higher level. Bind the
    # durable receipt (and charged attempt) to the exact effort too, for a saved route
    # (#2057) as for a selection's: the same model at two levels is two route pairs.
    # A level-less route keeps the older identity, so its saved receipts stay valid.
    split: list[tuple] = []
    for facade, routes, identity in groups:
        by_effort: dict[str | None, dict] = {}
        for role, route in routes.items():
            by_effort.setdefault(route.get("reasoning_effort"), {})[role] = route
        for effort, same_level in by_effort.items():
            bound = (
                {**identity, "reasoning_effort": effort} if identity is not None and effort is not None else identity
            )
            existing = next((group for group in split if bound is not None and group[2] == bound), None)
            if existing is None:
                split.append((facade, dict(same_level), bound))
            else:
                existing[1].update(same_level)
    groups = split
    groups = [(facade, due(routes, state.get("next_stage")), identity) for facade, routes, identity in groups]
    groups = [group for group in groups if group[1]]
    if not groups:
        return False
    # Standalone callers supply unknown routes; saved runs use their actual current
    # routes, since explicit bindings or recovery may have changed the initial selection.
    unknown = (
        set(selection.get("probe") or ())
        if selection is not None and not (state.get("settings") or {}).get("roles")
        else None
    )
    checkpoint = checkpoint or (lambda: None)
    before_probe = before_probe or (lambda: None)
    run_dir = Path(run_dir)
    attempts.reconcile(runner, state, run_dir, checkpoint)
    marker = run_dir / "model-preflight.json"
    saved = _saved_receipts(marker)
    charged = [
        record
        for record in attempts.records(state, run_dir)
        if record.get("preflight_complete") and record.get("accounted") and record.get("preflight_outcome")
    ]
    receipts: list[tuple] = []
    publication: list[dict] = []
    for facade, routes, identity in groups:
        probed: dict[str, dict] = {}
        equivalent = access_identity.equivalents(state, identity)
        current_probed: dict[str, dict] = {}
        if identity is not None:
            # A current marker, including failure, takes precedence over older locations.
            current_probed = next((dict(entry["probed"]) for entry in saved if entry["identity"] == identity), {})
            for alias in reversed(equivalent):
                for entry in saved:
                    if entry["identity"] == alias:
                        probed.update(entry["probed"])
            # Charged attempts are chronological and authoritative after interrupted
            # marker publication. An old location cannot erase a current marker.
            for record in charged:
                bound = record.get("preflight_identity")
                if bound in equivalent and (bound == identity or record["model"] not in current_probed):
                    probed[record["model"]] = record["preflight_outcome"]
                    if bound == identity:
                        current_probed[record["model"]] = record["preflight_outcome"]
        # A real, completed call already demonstrated access on this exact launch
        # identity. Missing legacy bindings never borrow the current configuration.
        # Completed-stage entries are revalidated from their original artifacts,
        # never promoted into permanent probe receipts. Explicit probe failures
        # remain authoritative over earlier successful task calls.
        probed = {model: outcome for model, outcome in probed.items() if outcome.get("source") != "completed_stage"}
        for model, outcome in access_evidence.passed(
            state.get("stages", []), identity, run_dir, equivalent=equivalent
        ).items():
            probed.setdefault(model, outcome)
        # Preserve each relocated receipt's original binding on disk. Only calls
        # actually earned under this location may be published under its identity.
        current_probed = {
            model: outcome for model, outcome in current_probed.items() if outcome.get("source") != "completed_stage"
        }
        for model, outcome in access_evidence.passed(state.get("stages", []), identity, run_dir).items():
            current_probed.setdefault(model, outcome)
        publication.extend(
            entry for entry in saved if entry["identity"] in equivalent and entry["identity"] != identity
        )
        publication.append({"identity": identity, "probed": current_probed})
        wanted = [
            model
            for model in distinct_models({"roles": routes})
            if (probed.get(model) or {}).get("status") != "ok" and (unknown is None or model in unknown)
        ]
        efforts = _selected_efforts(routes)
        # Evidence a prior invocation already paid for still proves the level (#2057).
        for model, outcome in probed.items():
            certify(state, model, efforts.get(model), outcome, checkpoint)
        receipts.append((facade, routes, identity, probed, wanted, efforts, _selected_roles(routes)))
    if not any(wanted for *_front, wanted, _efforts, _roles in receipts):
        return False
    before_probe()
    _allowance(runner, state, run_dir, workspace, timeout)
    # Every tool that will receive a request proves its login before the first one.
    for facade, routes, _identity, _probed, wanted, _efforts, _roles in receipts:
        if not wanted:
            continue
        try:
            facade.check_subscription_routes(routes, workspace)
        except RuntimeError as error:
            raise support.Paused(
                "PAUSED_BILLING_ROUTE",
                f"Model access preflight could not verify billing routes: {error}",
            ) from error

    def publish():
        atomic_json(marker, {"version": MARKER_VERSION, "receipts": publication})

    pending = [
        (adapter, identity, probed, model, efforts.get(model), roles.get(model))
        for adapter, _routes, identity, probed, wanted, efforts, roles in receipts
        for model in wanted
    ]
    for adapter, identity, probed, model, effort, role in pending:
        # A role bound with --role-transport names its tool; the same model may also be on the run's tool.
        where = "" if adapter is getattr(runner, "opencode", None) else f" on {(identity or {}).get('provider')}"
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
                effort=effort,
                role=role,
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
        for entry in publication:
            if entry["identity"] == identity:
                entry["probed"][model] = outcome
        certify(state, model, effort, outcome, checkpoint)
        publish()
        # Stop wins over budget and must also apply after the final probe.
        before_probe()
        if outcome["blocking"]:
            raise support.Paused(
                outcome["status"],
                f"Model access preflight failed for {model}{where}: {outcome['detail'] or outcome['status']}. "
                "Billing or login for this route cannot serve the model; fix it before any stage spend. "
                f"Probe evidence: {outcome['evidence']}",
            )
        _allowance(runner, state, run_dir, workspace, timeout)
        if outcome["status"] == "ok":
            print(f"Model preflight: {model}{where} ok ({outcome['seconds']}s)", flush=True)
        else:
            print(
                f"Model preflight: {model}{where} uncertain ({outcome['status']}: {outcome['detail'] or 'no detail'}); "
                "continuing within the remaining run budget",
                flush=True,
            )
    return True

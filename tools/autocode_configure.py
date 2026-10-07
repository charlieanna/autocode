"""Run configuration: CLI arguments to the settings a run persists.

Extracted from autocode.py (AGENTS.md architecture rules 1-3). This module
never imports the CLI (autocode) or the controller (autopilot); the cycle
members it needs (planning, milestones), the controller, the selected
provider facade (opencode, defaulting to the builtin OpenCode module) and
migrate's state-writing helpers (write_json, now) arrive as call-time
arguments, so the bindings autocode.py holds — including the provider it
selects in main — keep applying.
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from . import autocode_support as support, autocode_goals as goals, autocode_providers
    from . import autocode_opencode, autocode_figma as figma
    from . import autocode_budget_recovery as budget_recovery, autocode_verification_config as verification_config
    from . import autocode_retired_token_budget as retired_token_budget, autocode_design_manifest as design_manifest
    from . import autocode_planner_routes as planner_routes, autocode_adaptive_planning as adaptive
    from . import autocode_task_preflight as task_preflight, autocode_output_policy as output_policy
    from . import autocode_base_patch as operator_patch, autocode_quota_route as quota_route
    from . import autocode_route_ladder as route_ladder
except ImportError:
    import autocode_support as support, autocode_goals as goals, autocode_providers
    import autocode_opencode, autocode_figma as figma
    import autocode_budget_recovery as budget_recovery, autocode_verification_config as verification_config
    import autocode_retired_token_budget as retired_token_budget, autocode_design_manifest as design_manifest
    import autocode_planner_routes as planner_routes, autocode_adaptive_planning as adaptive
    import autocode_task_preflight as task_preflight, autocode_output_policy as output_policy
    import autocode_base_patch as operator_patch, autocode_quota_route as quota_route
    import autocode_route_ladder as route_ladder

DEFAULT_ROLE_MODELS = {
    "astra": "gpt-5.6-sol",
    "terra": "gpt-5.6-terra",
    "sol": "gpt-5.6-sol",
    "completion": "gpt-5.6-sol",
}
# Keep the historical OpenCode default for existing saved/dashboard flows.
DEFAULT_ENGINE = "opencode"

BUDGET_ARGUMENTS = {
    'iteration_ceiling': ('max_iterations', 'legacy_iteration_ceiling', 'unlimited_iterations'),
    'max_seconds': ('max_seconds',), 'stage_timeout_seconds': ('max_stage_seconds',),
    'idle_timeout_seconds': ('max_idle_seconds',), 'tool_timeout_seconds': ('max_tool_seconds',),
    'no_progress_batches': ('no_progress_limit',),
    'milestone_max_seconds': ('max_milestone_seconds',),
}


def budget_origins(args):
    explicit = getattr(args, '_explicit_budget_flags', None)
    if explicit is None:
        explicit = {flag for flags in BUDGET_ARGUMENTS.values() for flag in flags
                    if getattr(args, flag, None) is not None
                    and (flag != 'unlimited_iterations' or getattr(args, flag, False))}
    delegated = getattr(args, 'autoresolver_managed_limits', False)
    return {kind: ('resolver_delegated' if delegated else
                   'user_explicit' if any(flag in explicit for flag in flags) else 'runner_default')
            for kind, flags in BUDGET_ARGUMENTS.items()}


def check_subscription(identity):
    if (identity.get("auth_mode") != "ChatGPT" or identity.get("model_provider") not in (None, "openai")
            or identity.get("openai_base_url") or identity.get("environment_auth_present")
            or identity.get("environment_base_url_present")):
        raise support.Paused("PAUSED_BILLING_ROUTE", "Joint planning requires Codex signed in with ChatGPT, "
                             "the default OpenAI endpoint and no API-key environment overrides; no billing fallback")


def configure(args, state, *, planning, milestones, autopilot, opencode=None):
    if opencode is None:
        opencode = autocode_opencode
    started = bool(state.get("settings") or state.get("sessions") or state.get("history"))
    if started and getattr(args, 'builder_strong_model', None):
        raise ValueError('--builder-strong-model is a new-run policy; existing runs keep their persisted budget and route')
    if started and adaptive.resume_refused(state.get('settings') or {}, getattr(args, 'adaptive_planning', None)):
        raise ValueError('Adaptive planning is a new-run policy; a saved run keeps its planning flow')
    manifest_input = None
    if getattr(args, "figma_manifest", None):
        if started:
            raise ValueError("--figma-manifest is a new-run input; saved references are immutable")
        manifest_input = getattr(args, "_design_manifest_input", None) or design_manifest.load(args.figma_manifest)
    if started and not getattr(args, "status", False):
        design_manifest.context(state.get("settings") or {})
    saved_provider = dict(state.get("settings") or {})
    # Checkpoints created before provider selection shipped were necessarily
    # OpenCode runs.  Treating that as explicit prevents an unsafe transport
    # switch when they are resumed.
    if started and "provider" not in saved_provider:
        saved_provider["provider"] = "opencode"
    saved_engine = state.get("settings", {}).get("engine") or ("codex" if started else None)
    engine = getattr(args, "engine", None) or saved_engine or DEFAULT_ENGINE
    if engine not in ("codex", "opencode"):
        raise ValueError(f"Engine {engine!r} is not bundled in this checkout; providers live in "
                         "~/.config/autocode/providers/ and run with --provider")
    provider_name = autocode_providers.select(getattr(args, "provider", None), saved_provider,
                                              default="opencode" if engine == "codex" else None)
    if engine == "codex" and provider_name != "opencode":
        raise ValueError("--provider requires the OpenCode engine; --engine codex uses its native transport")
    figma_file = getattr(args, "figma_file", None)
    saved_figma = state.get("settings", {}).get("figma_file")
    if manifest_input and figma_file:
        target = urlparse(figma.design_url(figma_file))
        parts = target.path.strip("/").split("/")
        file_key = parts[1] if len(parts) > 1 else None
        file = next((row for row in manifest_input["body"]["files"] if row["key"] == file_key), None)
        if file is None:
            raise ValueError("Native Figma file is not declared in --figma-manifest")
        node_id = parse_qs(target.query).get("node-id", [None])[0]
        if node_id:
            node_id = node_id.replace("-", ":")
            declared = (file["nodes"] if manifest_input["body"]["version"] == 1 else
                        [node for page in file["pages"]
                         for node in design_manifest.inventory._metadata_nodes(page, manifest_input["root"])[0]])
            if node_id not in declared:
                raise ValueError("Native Figma node is not declared in --figma-manifest")
    if (figma_file or saved_figma) and engine != "codex":
        raise ValueError("Figma integration requires the Codex engine")
    if figma_file or saved_figma:
        for role in DEFAULT_ROLE_MODELS:
            model = getattr(args, f"{role}_model", None)
            if model and not re.fullmatch(r"gpt-[a-zA-Z0-9.-]+", model):
                raise ValueError("Figma workflow uses ChatGPT GPT models through Codex")
            if getattr(args, f"{role}_provider", None) not in (None, "openai"):
                raise ValueError("Figma workflow uses the OpenAI provider through Codex")
    if started and figma_file and figma_file != saved_figma:
        raise ValueError("Start a new run to change its Figma reference")
    saved_joint = bool(state.get("settings", {}).get("joint_planning"))
    requested_joint = getattr(args, "joint_planning", False)
    enable_saved_joint = started and requested_joint and not saved_joint
    if started:
        if enable_saved_joint:
            saved = state.get("settings", {})
            if engine != saved_engine or any(
                    planning.engine_for(saved, role) != engine for role in saved.get("roles", {})):
                raise ValueError("Start a new run to enable joint planning across different session engines")
            if any(state.get(key) for key in ("active_stage", "pending_report_repair", "uncertain_artifacts")):
                raise ValueError("Resolve the saved provider attempt before enabling joint planning")
            restarting_discovery = engine == "codex" and state.get("next_stage") == "astra_discovery"
            approved_boundary = goals.approved(state) and state.get("next_stage") in (
                "astra_plan", "orchestrator", "terra", "sol", "astra_review", "astra_checkpoint")
            if (state.get("version", 1) < 3 or not (restarting_discovery or approved_boundary)
                    or state.get("status") != "RUNNING" and not str(state.get("status", "")).startswith("PAUSED_")):
                raise ValueError("Start a new run or reach an approved execution boundary before enabling joint planning")
        joint = saved_joint or enable_saved_joint
    elif engine == "codex":
        joint = requested_joint
    else:
        joint = True
    if joint and engine not in ("codex", "opencode"):
        raise ValueError("--joint-planning requires a supported planning engine")
    if getattr(args, 'planning_v2', False) and not joint:
        raise ValueError('--planning-v2 requires --joint-planning or an engine where joint planning is default')
    if started and getattr(args, 'planning_v2', False) and state.get('settings', {}).get('planning_flow') != 'v2' and not enable_saved_joint:
        raise ValueError('Start a new run, or explicitly enable joint planning at its supported migration boundary, to select planning-v2')
    if (getattr(args, "requirements_model", None) or getattr(args, "requirements_reasoning_effort", None)
            or getattr(args, "glm_reasoning_effort", None) or getattr(args, "plan_reviewer_model", None)
            or getattr(args, "plan_reviewer_reasoning_effort", None)) and not joint:
        raise ValueError("Planner role overrides require --joint-planning")
    if getattr(args, "glm_model", None) and not joint:
        raise ValueError("--glm-model requires --joint-planning")
    if started and engine != saved_engine:
        raise ValueError("Start a new run to change engines; Codex and OpenCode session IDs are not interchangeable")
    if engine == "opencode" and any(getattr(args, f"{r}_provider", None) for r in DEFAULT_ROLE_MODELS):
        raise ValueError("For OpenCode use --<role>-model instead of --<role>-provider")
    if state.get("settings"):
        settings = json.loads(json.dumps(state["settings"]))
        retired_token_budget.retire_settings(settings)
        settings.setdefault("provider", provider_name)
        # v0.5.4 introduced bounded report-only repairs.  Existing runs retain
        # their model, auth and limit settings while gaining the safe default
        # used by every newly-created run.
        settings.setdefault("report_repair", {"max_attempts": 2})
        # Preserve every saved hard limit, including explicit zero. Older runs
        # gain activity supervision at their next configured launch boundary.
        settings.setdefault("limits", {}).setdefault("idle_timeout_seconds", 300)
        settings["limits"].setdefault("tool_timeout_seconds", 1800)
        if "completion" not in settings["roles"]:
            astra_route = settings["roles"]["astra"]
            completion_engine = planning.engine_for(settings, "astra")
            settings["roles"]["completion"] = {
                **astra_route,
                "model": (opencode.DEFAULT_MODELS["completion"] if completion_engine == "opencode"
                          else DEFAULT_ROLE_MODELS["completion"]),
                "reasoning_effort": opencode.DEFAULT_REASONING_EFFORTS["completion"],
            }
        for role in DEFAULT_ROLE_MODELS:
            selected_model = getattr(args, f"{role}_model", None)
            if selected_model:
                settings["roles"][role]["model"] = selected_model
            if getattr(args, f"{role}_provider", None):
                settings["roles"][role]["provider"] = getattr(args, f"{role}_provider")
            role_effort = getattr(args, f"{role}_reasoning_effort", None)
            if role_effort or args.reasoning_effort:
                settings["roles"][role]["reasoning_effort"] = role_effort or args.reasoning_effort
        for role in getattr(args, "pin_model_role", []):
            settings["roles"][role]["model_pinned"] = True
        if args.headroom is not None:
            settings["headroom"]["enabled"] = args.headroom == "on"
        if args.context_soft_tokens is not None:
            settings["context_soft_tokens"] = args.context_soft_tokens
        if args.rotate_after_input_tokens is not None:
            settings["rotation_after_input_tokens"] = args.rotate_after_input_tokens
        for flag, name in (("max_iterations", "iteration_ceiling"), ("legacy_iteration_ceiling", "iteration_ceiling"),
                           ("max_seconds", "max_seconds"), ("max_stage_seconds", "stage_timeout_seconds"),
                           ("max_idle_seconds", "idle_timeout_seconds"), ("max_tool_seconds", "tool_timeout_seconds"),
                           ("no_progress_limit", "no_progress_batches"),
                           ("max_findings_per_task", "max_findings_per_task")):
            selected = getattr(args, flag, None)
            if selected is not None:
                settings.setdefault("limits", {})[name] = selected
                settings.setdefault('budget_origins', {})[name] = 'user_explicit'
        if enable_saved_joint and engine == "opencode":
            settings["joint_planning"] = True
            settings["roles"]["requirements"] = {"engine": "opencode", "provider": None,
                "model": getattr(args, "requirements_model", None) or opencode.DEFAULT_MODELS.get("requirements", opencode.DEFAULT_MODELS["glm"]),
                "reasoning_effort": getattr(args, "requirements_reasoning_effort", None)}
            settings["roles"]["glm"] = {"engine": "opencode", "provider": None,
                "model": getattr(args, "glm_model", None) or opencode.DEFAULT_MODELS["glm"],
                "reasoning_effort": getattr(args, "glm_reasoning_effort", None)}
            settings.setdefault("transport_identities", {})["opencode"] = settings["transport_identity"]
            opencode.check_models(settings["roles"], Path(state["workspace"]))
            opencode.check_subscription_routes(settings["roles"], Path(state["workspace"]))
        if joint:
            configure_joint(settings, args, fresh=False, planning=planning, opencode=opencode)
        if getattr(args, 'planning_v2', False):
            settings['planning_flow'] = 'v2'
        if getattr(args,'unlimited_iterations',False):
            settings.setdefault('limits',{})['iteration_ceiling']=None
            settings.setdefault('budget_origins', {})['iteration_ceiling'] = 'user_explicit'
        if getattr(args, 'max_milestone_seconds', None) is not None:
            if not milestones.enabled({"settings": settings}) and not getattr(args, 'milestone_checkpoints', False):
                raise ValueError("Enable --milestone-checkpoints before setting its budget")
            if milestones.enabled({"settings": settings}):
                settings['milestone_checkpoints']['max_seconds'] = args.max_milestone_seconds
                settings.setdefault('budget_origins', {})['milestone_max_seconds'] = 'user_explicit'
        if getattr(args, 'max_milestone_replans', None) is not None:
            if not milestones.enabled({"settings": settings}) and not getattr(args, 'milestone_checkpoints', False):
                raise ValueError("Enable --milestone-checkpoints before setting the replan limit")
            if milestones.enabled({"settings": settings}):
                settings['milestone_checkpoints']['max_replans'] = (
                    None if args.max_milestone_replans == 0 else args.max_milestone_replans)
        if getattr(args, 'max_milestone_stalled_reviews', None) is not None:
            if not milestones.enabled({"settings": settings}):
                raise ValueError("Enable --milestone-checkpoints before setting the review limit")
            settings['milestone_checkpoints']['stalled_reviews'] = (
                None if args.max_milestone_stalled_reviews == 0 else args.max_milestone_stalled_reviews)
        if getattr(args, "accept_transport_change", False):
            if engine != "opencode" or state.get("status") != "PAUSED_TRANSPORT_CHANGED":
                raise ValueError("--accept-transport-change requires an OpenCode run paused for a transport change")
            if any(state.get(key) for key in ("active_stage", "pending_report_repair", "uncertain_artifacts")):
                raise ValueError("Resolve the saved provider attempt before accepting a transport change")
            workspace = Path(state["workspace"])
            current = opencode.local_settings(workspace)
            routed = {role: config for role, config in settings["roles"].items()
                      if planning.engine_for(settings, role) == "opencode"}
            opencode.check_models(routed, workspace)
            opencode.check_subscription_routes(routed, workspace)
            settings["transport_identity"] = current
            settings.setdefault("transport_identities", {})["opencode"] = current
        if getattr(args, "max_parallel_builders", None) is not None:
            if not settings.get("orchestration", {}).get("enabled"):
                raise ValueError("Start a new run to enable milestone orchestration")
            settings["orchestration"]["max_parallel"] = args.max_parallel_builders
        settings = autopilot.stuck.configure(settings, args)
        settings = verification_config.configure_resume(state, settings, args)
        settings = operator_patch.configure_resume(state, settings, args)
        return task_preflight.configure(state, output_policy.configure(state, settings, args), args)
    if engine == "opencode":
        local = opencode.local_settings(state["workspace"])
    else:
        local = support.local_settings()
    models = {}
    providers = {}
    for record in state.get("history", []):
        command = record.get("command", [])
        if "--model" in command:
            models[record["role"]] = command[command.index("--model")+1]
        for index, item in enumerate(command[:-1]):
            if item == "-c" and command[index+1].startswith('model_provider="'):
                providers[record["role"]] = command[index+1][len('model_provider="'):-1]
    # Custom providers ship their own DEFAULT_MODELS (TOML [roles]); never
    # force the builtin OpenCode catalogue onto fixturetool/kilocode/etc.
    provider_mod = autocode_providers.resolve(provider_name) if provider_name else opencode
    defaults = DEFAULT_ROLE_MODELS.copy()
    if engine == "opencode":
        defaults.update(provider_mod.DEFAULT_MODELS)
    roles = {r: {"model": getattr(args, f"{r}_model", None) or models.get(r) or defaults[r],
                 "reasoning_effort": getattr(args, f"{r}_reasoning_effort", None) or args.reasoning_effort or local.get("model_reasoning_effort") or opencode.DEFAULT_REASONING_EFFORTS[r],
                 "provider": getattr(args, f"{r}_provider", None) or providers.get(r) or local.get("model_provider")}
            for r in DEFAULT_ROLE_MODELS}
    for role in getattr(args, "pin_model_role", []):
        roles[role]["model_pinned"] = True
    settings = {"roles": roles, "transport_identity": local, "engine": engine, "provider": provider_name,
            'budget_origins': budget_origins(args),
            "builder_retry": autopilot.builder_policy.configured(getattr(args, 'builder_strong_model', None), provider_mod),
            # The effort rungs each role may climb, serveability-filtered for a
            # provider that publishes its models (autocode_route_ladder).
            "route_ladders": route_ladder.configure_ladders(getattr(provider_mod, 'LISTED_MODELS', None)),
            "orchestration": {"enabled": joint or getattr(args, "max_parallel_builders", None) is not None,
                              "max_parallel": getattr(args, "max_parallel_builders", None) or 2},
            "report_repair": {"max_attempts": 2},
            "milestone_checkpoints": {**milestones.DEFAULTS,
                "max_seconds": getattr(args, 'max_milestone_seconds', None)
                    if getattr(args, 'max_milestone_seconds', None) is not None else milestones.DEFAULTS['max_seconds'],
                "max_replans": (None if getattr(args, 'max_milestone_replans', None) == 0 else
                    getattr(args, 'max_milestone_replans', None)
                    if getattr(args, 'max_milestone_replans', None) is not None else milestones.DEFAULTS['max_replans'])},
            "headroom": {"enabled": args.headroom == "on", "verified": False},
            "regression": {key: value for key, value in (("test_command", getattr(args, "test_command", None)),
                           ("regression_command", getattr(args, "regression_command", None)),
                           ("base_patch", getattr(args, "base_patch", None) and operator_patch.pin(
                               args.base_patch, state["workspace"], state.get("base_commit")))) if value},
            "context_soft_tokens": args.context_soft_tokens if args.context_soft_tokens is not None else 10000,
            "rotation_after_input_tokens": args.rotate_after_input_tokens if args.rotate_after_input_tokens is not None else 1000000,
            "limits": {"iteration_ceiling": args.legacy_iteration_ceiling if args.legacy_iteration_ceiling is not None
                       else (state.get("iteration", 0) + args.max_iterations
                             if args.max_iterations is not None else None),
                       "max_seconds": args.max_seconds if args.max_seconds is not None else budget_recovery.RUNNER_DEFAULTS["max_seconds"],
                       "stage_timeout_seconds": (getattr(args, "max_stage_seconds", None) if getattr(args, "max_stage_seconds", None)
                                                 is not None else budget_recovery.RUNNER_DEFAULTS["stage_timeout_seconds"]),
                       "idle_timeout_seconds": (getattr(args, "max_idle_seconds", None)
                                                if getattr(args, "max_idle_seconds", None) is not None else 300),
                       "tool_timeout_seconds": (getattr(args, "max_tool_seconds", None)
                                                if getattr(args, "max_tool_seconds", None) is not None else 1800),
                       "no_progress_batches": args.no_progress_limit if args.no_progress_limit is not None else 3,
                       "max_findings_per_task": getattr(args, "max_findings_per_task", None),
                        "automatic_retries": 0}}
    if manifest_input:
        settings["design_manifest"] = (manifest_input if getattr(args, "dry_run", False) or getattr(args, "status", False)
                                       else design_manifest.retain(manifest_input, state["workspace"]))
    if figma_file:
        figma.require_chatgpt(local)
        for config in settings["roles"].values():
            config["provider"] = "openai"
        settings.update(figma_file=figma.design_url(figma_file), figma_review=getattr(args, "figma_review", None) or "automatic",
                        figma_inventory_required=True, figma_references=[figma_file, *getattr(args, "figma_additional_file", [])])
    if figma_file and manifest_input and manifest_input["body"]["version"] == 2:
        try:
            from . import autocode_design_intake as intake
        except ImportError:
            import autocode_design_intake as intake
        intake.require_references(manifest_input, settings["figma_references"], exact=False)
    if joint:
        configure_joint(settings, args, fresh=True, planning=planning, opencode=opencode)
    if getattr(args, 'conversation_handoff', None):
        planner_routes.configure_runner_profile(settings, args)
    if getattr(args, 'planning_v2', False):
        settings['planning_flow'] = 'v2'
    if adaptive.new_run_setting(getattr(args, 'adaptive_planning', None), joint, settings.get('planning_flow') == 'v2'):
        settings['adaptive_planning'] = True
    if getattr(args,'unlimited_iterations',False):
        settings['limits']['iteration_ceiling']=None
    return task_preflight.configure(state, output_policy.configure(state, autopilot.stuck.configure(settings, args), args), args)


def _provider_model(role, requested, mod=None):
    mod = mod or autocode_opencode
    model = requested or mod.DEFAULT_MODELS[role]
    # Bare OpenAI names from older dashboard conversations are aliases,
    # never a reason to use a separate Codex login. Config tools name models
    # themselves, so they keep the configured string.
    if not getattr(mod, "CONFIGURED", False) and "/" not in model:
        model = f"openai/{model}"
    return model


def configure_joint(settings, args, *, fresh, planning, opencode=None):
    if opencode is None:
        opencode = autocode_opencode
    if settings.get("engine") == "codex":
        configure_codex_joint(settings, args, planning=planning)
        return
    mod = autocode_providers.resolve(settings.get("provider") or "opencode")
    if fresh:
        settings["joint_planning"] = True
        settings["roles"]["requirements"] = {"engine": "opencode", "provider": None,
            "model": getattr(args, "requirements_model", None) or mod.DEFAULT_MODELS.get("requirements", mod.DEFAULT_MODELS["glm"]),
            "reasoning_effort": getattr(args, "requirements_reasoning_effort", None)}
        if "completion" not in settings["roles"]:
            settings["roles"]["completion"] = {
                **settings["roles"]["astra"],
                "model": mod.DEFAULT_MODELS["completion"],
                "reasoning_effort": mod.DEFAULT_REASONING_EFFORTS["completion"],
            }
        for role in ("astra", "sol", "completion"):
            settings["roles"][role].update(engine="opencode", provider=None,
                model=_provider_model(role, getattr(args, f"{role}_model", None), mod))
        terra_model = getattr(args, "terra_model", None) or mod.DEFAULT_MODELS["terra"]
        settings["roles"]["terra"].update(engine="opencode", provider=None, model=terra_model)
        for role, effort in mod.DEFAULT_REASONING_EFFORTS.items():
            if role in settings["roles"] and not settings["roles"][role].get("reasoning_effort"):
                settings["roles"][role]["reasoning_effort"] = effort
        glm_model = getattr(args, "glm_model", None) or mod.DEFAULT_MODELS["glm"]
        settings["roles"]["glm"] = {"engine": "opencode", "provider": None,
            "model": glm_model, "reasoning_effort": mod.DEFAULT_REASONING_EFFORTS.get("glm")}
        settings["roles"]["plan_reviewer"] = {"engine": "opencode", "provider": None,
            "model": (getattr(args, "plan_reviewer_model", None)
                      or mod.DEFAULT_MODELS.get("plan_reviewer")
                      or planning.PINNED_REVIEWER_MODEL),
            "reasoning_effort": (getattr(args, "plan_reviewer_reasoning_effort", None)
                                 or mod.DEFAULT_REASONING_EFFORTS.get("plan_reviewer")),
            "model_pinned": True}
        settings["transport_identities"] = {"opencode": settings["transport_identity"]}
    elif getattr(args, "glm_model", None):
        settings["roles"]["glm"]["model"] = args.glm_model
    if getattr(args, "requirements_model", None) or getattr(args, "requirements_reasoning_effort", None):
        if "requirements" not in settings["roles"]:
            raise ValueError("This saved run predates the separate requirements stage; start a new run to select its model")
        if getattr(args, "requirements_model", None):
            settings["roles"]["requirements"]["model"] = args.requirements_model
        if getattr(args, "requirements_reasoning_effort", None):
            settings["roles"]["requirements"]["reasoning_effort"] = args.requirements_reasoning_effort
    if getattr(args, "resolver_model", None) or getattr(args, "resolver_reasoning_effort", None):
        settings["roles"].setdefault("resolver", copy.deepcopy(settings["roles"]["astra"]))
        if getattr(args, "resolver_model", None):
            settings["roles"]["resolver"]["model"] = args.resolver_model
        if getattr(args, "resolver_reasoning_effort", None):
            settings["roles"]["resolver"]["reasoning_effort"] = args.resolver_reasoning_effort
    if getattr(args, "glm_reasoning_effort", None):
        settings["roles"]["glm"]["reasoning_effort"] = args.glm_reasoning_effort
    if getattr(args, "plan_reviewer_model", None):
        settings["roles"]["plan_reviewer"]["model"] = args.plan_reviewer_model
    if getattr(args, "plan_reviewer_reasoning_effort", None):
        settings["roles"]["plan_reviewer"]["reasoning_effort"] = args.plan_reviewer_reasoning_effort
    configured_tool = getattr(opencode, "CONFIGURED", False)
    for role, config in settings["roles"].items():
        # One rule for a launch and for a model named at a quota stop (autocode_quota_route).
        problem = quota_route.model_problem(role, config.get("model"), role_engine=planning.engine_for(settings, role),
                                            configured_tool=configured_tool)
        if problem:
            raise ValueError(problem)


def configure_codex_joint(settings, args, *, planning):
    """Independent planning sessions through the existing native Codex login."""
    check_subscription(settings["transport_identity"])
    roles = settings["roles"]
    for role in ("requirements", "glm", "plan_reviewer"):
        roles.setdefault(role, copy.deepcopy(roles["astra"]))
        if planning.engine_for(settings, role) != "codex":
            raise ValueError("Native Codex joint planning cannot switch a saved role's engine")
        route = roles[role]
        route["engine"] = "codex"
        model = getattr(args, f"{role}_model", None)
        effort = getattr(args, f"{role}_reasoning_effort", None)
        if model:
            route["model"] = model
        if effort:
            route["reasoning_effort"] = effort
    roles["plan_reviewer"]["model_pinned"] = True
    for role, route in roles.items():
        if planning.engine_for(settings, role) != "codex":
            raise ValueError("Native Codex joint planning cannot switch a saved role's engine")
        if not re.fullmatch(r"gpt-[a-zA-Z0-9.-]+", route.get("model") or ""):
            raise ValueError(f"{role.title()} requires a bare GPT Codex model name")
        if route.get("provider") not in (None, "openai"):
            raise ValueError("Native Codex joint planning uses the OpenAI ChatGPT route")
    settings["joint_planning"] = True
    settings.setdefault("transport_identities", {}).setdefault("codex", settings["transport_identity"])


def migrate_opencode_roles(state, run_dir, workspace, *, planning, opencode=None, write_json, now):
    """Move old mixed-CLI runs to OpenCode at a recovered, locked boundary."""
    if opencode is None:
        opencode = autocode_opencode
    settings = state["settings"]
    if settings.get("engine") != "opencode":
        return False  # Explicit legacy --engine codex runs retain their contract.
    roles = [role for role in settings["roles"] if planning.engine_for(settings, role) == "codex"]
    if not roles:
        return False
    if any(state.get(key) for key in ("active_stage", "pending_report_repair", "uncertain_artifacts")):
        raise support.Paused("PAUSED_TRANSPORT_MIGRATION", "Resolve the saved stage before moving its role to OpenCode")
    candidate = copy.deepcopy(state)
    selected = candidate["settings"]
    for role in roles:
        config = selected["roles"][role]
        if config.get("provider") not in (None, "openai") or "/" in config["model"]:
            raise support.Paused("PAUSED_TRANSPORT_MIGRATION",
                                 f"Cannot map the saved {role} provider to OpenCode automatically")
        config.update(engine="opencode", provider=None, model=f"openai/{config['model']}")
    # Check availability and the existing OAuth route before changing a checkpoint.
    opencode.check_models(selected["roles"], workspace)
    opencode.check_subscription_routes(selected["roles"], workspace)
    current = opencode.local_settings(workspace)
    if opencode.transport_drift(current, settings["transport_identity"]):
        raise support.Paused("PAUSED_TRANSPORT_CHANGED", "OpenCode configuration changed before role migration")
    selected["transport_identities"] = {"opencode": settings["transport_identity"]}
    at = now()
    for role in roles:
        old = candidate.setdefault("sessions", {}).pop(role, None)
        if old:
            candidate.setdefault("session_rotations", []).append({"role": role, "old_session": old, "at": at,
                "reason": "Moved from Codex to OpenCode; saved handoffs and evidence retained"})
    backup = Path(run_dir) / f"state.pre-opencode-{uuid.uuid4().hex[:8]}.json"
    candidate.setdefault("configuration_changes", []).append({"at": at, "previous": settings,
        "selected": copy.deepcopy(selected), "backup": str(backup),
        "reason": "Use OpenCode and its current ChatGPT OAuth login for every role"})
    write_json(backup, state)
    write_json(Path(run_dir) / "state.json", candidate)
    state.clear()
    state.update(candidate)
    return True

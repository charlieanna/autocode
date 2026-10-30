"""Run configuration: CLI arguments to the settings a run persists.

Extracted from autocode.py (AGENTS.md architecture rules 1-3). This module
never imports the CLI (autocode) or the controller (autopilot); the
modules it needs (planning, milestones), the controller, the selected
provider facade (opencode, defaulting to the builtin OpenCode module) and
migrate's state-writing helpers (write_json, now) arrive as call-time
arguments, so the bindings autocode.py holds — including the provider it
selects in main — keep applying.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import argparse
import copy
import json
import math
import re
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from . import autocode_acceptance_tests as acceptance_tests
    from . import autocode_adaptive_planning as adaptive
    from . import autocode_base_patch as operator_patch
    from . import autocode_budget_recovery as budget_recovery
    from . import autocode_compact_planning as compact_planning
    from . import autocode_design_manifest as design_manifest
    from . import autocode_figma as figma
    from . import autocode_goals as goals
    from . import autocode_opencode, autocode_providers
    from . import autocode_output_policy as output_policy
    from . import autocode_pause_authority as pause_authority
    from . import autocode_pause_command as pause_command
    from . import autocode_planner_routes as planner_routes
    from . import autocode_quota_route as quota_route
    from . import autocode_requirements_review as requirements_review
    from . import autocode_retired_token_budget as retired_token_budget
    from . import autocode_role_route as role_route
    from . import autocode_role_transport as role_transport
    from . import autocode_roles as role_names
    from . import autocode_route_fit as route_fit
    from . import autocode_route_ladder as route_ladder
    from . import autocode_skip_requirements as skip_requirements
    from . import autocode_support as support
    from . import autocode_task_preflight as task_preflight
    from . import autocode_transport_recovery as transport_recovery
    from . import autocode_utility_route as utility_route
    from . import autocode_verification_config as verification_config
except ImportError:
    import autocode_acceptance_tests as acceptance_tests
    import autocode_adaptive_planning as adaptive
    import autocode_base_patch as operator_patch
    import autocode_budget_recovery as budget_recovery
    import autocode_compact_planning as compact_planning
    import autocode_design_manifest as design_manifest
    import autocode_figma as figma
    import autocode_goals as goals
    import autocode_opencode
    import autocode_output_policy as output_policy
    import autocode_pause_authority as pause_authority
    import autocode_pause_command as pause_command
    import autocode_planner_routes as planner_routes
    import autocode_providers
    import autocode_quota_route as quota_route
    import autocode_requirements_review as requirements_review
    import autocode_retired_token_budget as retired_token_budget
    import autocode_role_route as role_route
    import autocode_role_transport as role_transport
    import autocode_roles as role_names
    import autocode_route_fit as route_fit
    import autocode_route_ladder as route_ladder
    import autocode_skip_requirements as skip_requirements
    import autocode_support as support
    import autocode_task_preflight as task_preflight
    import autocode_transport_recovery as transport_recovery
    import autocode_utility_route as utility_route
    import autocode_verification_config as verification_config

DEFAULT_ROLE_MODELS = {
    "astra": "gpt-5.6-sol",
    "terra": "gpt-5.6-terra",
    "sol": "gpt-5.6-sol",
    "completion": "gpt-5.6-sol",
}
# Keep the historical OpenCode default for existing saved/dashboard flows.
DEFAULT_ENGINE = "opencode"

BUDGET_ARGUMENTS = {
    "iteration_ceiling": ("max_iterations", "legacy_iteration_ceiling", "unlimited_iterations"),
    "max_seconds": ("max_seconds",),
    "max_cost_usd": ("max_cost_usd",),
    "stage_timeout_seconds": ("max_stage_seconds",),
    "idle_timeout_seconds": ("max_idle_seconds",),
    "tool_timeout_seconds": ("max_tool_seconds",),
    "no_progress_batches": ("no_progress_limit",),
    "milestone_max_seconds": ("max_milestone_seconds",),
    "milestone_max_replans": ("max_milestone_replans",),
}


def cost_cap(text):
    """`--max-cost-usd`: a finite nonnegative USD amount (0 means no cap); argparse's float alone takes nan."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid amount: {text!r}") from None
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(f"must be a finite nonnegative amount in USD, not {text!r}")
    return value


def budget_origins(args):
    explicit = getattr(args, "_explicit_budget_flags", None)
    if explicit is None:
        explicit = {
            flag
            for flags in BUDGET_ARGUMENTS.values()
            for flag in flags
            if getattr(args, flag, None) is not None and (flag != "unlimited_iterations" or getattr(args, flag, False))
        }
    delegated = getattr(args, "autoresolver_managed_limits", False)
    return {
        kind: (
            # A money cap is never delegated: AutoResolver may not manage or raise it (#721).
            "resolver_delegated"
            if delegated and kind != "max_cost_usd"
            else "user_explicit"
            if any(flag in explicit for flag in flags)
            else "runner_default"
        )
        for kind, flags in BUDGET_ARGUMENTS.items()
    }


def check_subscription(identity):
    if (
        identity.get("auth_mode") != "ChatGPT"
        or identity.get("model_provider") not in (None, "openai")
        or identity.get("openai_base_url")
        or identity.get("environment_auth_present")
        or identity.get("environment_base_url_present")
    ):
        raise support.Paused(
            "PAUSED_BILLING_ROUTE",
            "Joint planning requires Codex signed in with ChatGPT, "
            "the default OpenAI endpoint and no API-key environment overrides; no billing fallback",
        )


def _declared_levels(provider_mod, workspace, model):
    """The reasoning variants the tool declares for ``model``, from its own listing; None when it has no
    readable listing (the launch check decides then). Never a model request."""
    try:
        from . import autocode_route_discovery as discovery
    except ImportError:
        import autocode_route_discovery as discovery
    try:
        found = discovery.discover(provider_mod, Path(workspace))
        for row in found.get("candidates") or []:
            if row.get("model") == model and isinstance(row.get("levels"), list):
                return list(row["levels"])
    except Exception:  # noqa: BLE001 - an unreadable optional listing never blocks a launch
        return None
    return None


def _auto_select(
    args,
    roles,
    provider_mod,
    single_model,
    history_models,
    history_providers,
    engine,
    workspace,
    version=None,
    joint=False,
    started=False,
):
    """Choose routes from the tool's connected accounts (#1426) — the default for new runs.

    Explicit choices win: a role whose model a flag or this conversation named, or
    whose tool a flag named, keeps that route and only has its independence checked.
    Everything else is the selector's, from discovery's local listings; a selection
    that cannot field an independent checker refuses to start, in account terms.
    The result is stored under ``route_selection`` for the access check and the
    route record; saved runs never re-select.

    ``--no-auto-select`` (``auto_select`` False) keeps the fixed default routes; an
    explicit ``--auto-select`` (True) insists on selection. The default (None) also
    selects, but softly: when the tool's listings cannot be read, or its accounts
    cannot field an independent checker — a scripted test provider, a custom tool,
    an offline tool — the run prints why and falls back to the fixed default routes
    rather than refusing, because a defaulted attempt must never block a launch; the
    usual catalogue and launch checks still guard those routes.

    ``version`` is the installed OpenCode version when the run uses OpenCode itself.
    Selection chooses only on a version with a conformant row in the provider
    matrix (#2060); any other version keeps the fixed routes, or refuses when insisted.
    """
    # The CLI parser supplies None for default selection. Older programmatic callers
    # without this field retain their existing fixed-route behavior.
    requested = getattr(args, "auto_select", False)
    if requested is False or single_model or engine != "opencode":
        if requested is True and single_model:
            raise ValueError("--auto-select cannot be combined with --single-model")
        if requested is True and engine != "opencode":
            raise ValueError(
                "--auto-select chooses among the OpenCode engine's tools; --engine codex keeps its own routes"
            )
        return None
    try:
        from . import autocode_route_discovery as discovery
        from . import autocode_route_selector as route_selector
    except ImportError:
        import autocode_route_discovery as discovery
        import autocode_route_selector as route_selector

    def soft(story):
        # A defaulted attempt falls back to the fixed routes; an insisted one refuses.
        if requested is True:
            raise ValueError(story)
        print(f"Automatic model selection is off for this run: {story} Using the fixed default routes.", flush=True)
        return None

    try:
        from . import provider_matrix
    except ImportError:
        import provider_matrix
    try:
        untested = version and provider_matrix.unqualified("opencode", version)
    except (OSError, ValueError) as error:
        untested = f"the conformance matrix cannot be read ({error})"
    if untested:
        return soft(f"{untested}.")
    try:
        found = discovery.discover(provider_mod, Path(workspace), inventory=getattr(args, "_model_inventory", None))
    except Exception as error:  # noqa: BLE001 - any failure of a defaulted listing is a fallback
        return soft(f"the tool's listings could not be read ({error}).")
    # The run's full role set, not the four DEFAULT_ROLE_MODELS the roles dict
    # holds here: joint planning adds Requirements, Planner and Plan Reviewer.
    pinned = {}
    for role in route_selector.PIPELINE_ROLES:
        named = getattr(args, f"{role}_model", None) or history_models.get(role)
        if role == "requirements":
            named = named or getattr(args, "glm_model", None)
        explicit_tool = getattr(args, f"{role}_provider", None) or history_providers.get(role)
        if named:
            pinned[role] = named
        elif explicit_tool:
            # A role pointed at another tool keeps whatever route it already has.
            pinned[role] = (roles.get(role) or {}).get("model") or provider_mod.DEFAULT_MODELS.get(role)
    if requested is None and set(pinned) == set(route_selector.PIPELINE_ROLES):
        # A fully pinned profile makes no automatic choice. Keep its existing
        # preflight and recovery policy instead of saving an empty permitted set.
        return None
    result = route_selector.select(route_selector.PIPELINE_ROLES, found["candidates"], pinned=pinned)
    if result["stop"]:
        return soft(result["stop"])
    # The permitted set a quota stop may switch within without asking (#1426 step 9):
    # every qualified, connected candidate, all on accounts this launch already used.
    result["candidates"] = route_selector.permitted_set(found["candidates"])
    for role, route in result["routes"].items():
        explicit_effort = getattr(args, f"{role}_reasoning_effort", None) or getattr(args, "reasoning_effort", None)
        if explicit_effort is not None:
            route["reasoning_effort"] = explicit_effort
        if role in pinned or role not in roles:
            continue
        roles[role]["model"] = route["model"]
        # None is the model's own default level; a tool that cannot omit the level
        # argument omits it (providers/command.py, #1426 step 5).
        roles[role]["reasoning_effort"] = route.get("reasoning_effort")
    if not pinned and not started:
        # A new run with no model flag named anything: say what was chosen, per role, before
        # spending (#2057). A saved run keeps the routes it started with and stays quiet.
        try:
            from . import model_catalogue as catalogue
        except ImportError:
            import model_catalogue as catalogue
        shown = set(roles) | ({"requirements", "glm", "plan_reviewer"} if joint else set())
        report = {"routes": result["routes"], "candidates": found["candidates"], "tool": found["tool"]}
        lines = catalogue.route_lines(report, shown)
        if lines:
            print(f"Routes for this run, from {found['tool']}'s local listing:", flush=True)
            for line in lines:
                print("  " + line, flush=True)
    return result


def persisted_review_call_limit():
    """The review allowance a new run starts with from the user's config (#1565).

    ``plan_review_call_limit`` in ~/.config/autocode/config.toml: 0 (unlimited) or an
    integer of at least 2. Absent means the planning machinery's built-in default.
    An explicit CLI flag always wins; a resumed run keeps its saved setting.
    """
    try:
        from . import autocode_providers as providers
    except ImportError:
        import autocode_providers as providers
    import tomllib

    path = providers.user_config_path()
    if not path.is_file():
        return None, "built-in default"
    try:
        config = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{path} is not valid TOML: {error}") from error
    value = config.get("plan_review_call_limit")
    if value is None:
        return None, "built-in default"
    if type(value) is not int or (value != 0 and value < 2):
        raise ValueError(f"plan_review_call_limit in {path} must be 0 (unlimited) or an integer of at least 2")
    return value, f"plan_review_call_limit in {path}"


def configure(args, state: RunState, *, planning, milestones, autopilot, opencode=None):
    role_transport.guard(args, state)
    if getattr(args, "replay_recording", None) or (state.get("settings") or {}).get("replay_recording"):
        try:
            from . import autocode_replay_transport as replay_transport
        except ImportError:
            import autocode_replay_transport as replay_transport
        return replay_transport.configure(args, state)
    try:
        from . import autocode_evidence_provenance as evidence_provenance
    except ImportError:
        import autocode_evidence_provenance as evidence_provenance
    provenance = evidence_provenance.configured(state.get("settings") or {}, getattr(args, "evidence_provenance", None))
    if opencode is None:
        opencode = autocode_opencode
    started = bool(state.get("settings") or state.get("sessions") or state.get("history"))
    single_model = getattr(args, "single_model", None)
    if started and single_model:
        raise ValueError("--single-model is only available when starting a new run")
    if single_model and any(
        getattr(args, f"{role}_model", None)
        for role in ("requirements", "glm", "plan_reviewer", "astra", "terra", "sol", "completion")
    ):
        raise ValueError("--single-model cannot be combined with per-role model flags")
    if single_model and any(
        getattr(args, f"{role}_provider", None) for role in ("astra", "terra", "sol", "completion")
    ):
        raise ValueError("--single-model cannot be combined with per-role provider flags")
    if single_model and getattr(args, "builder_strong_model", None):
        raise ValueError("--single-model cannot be combined with --builder-strong-model")
    if started:
        utility_route.configure({}, args, started=True)  # refuses --utility-model on a saved run
    if started and getattr(args, "auto_select", None) is True:
        raise ValueError("--auto-select is a new-run choice; a saved run keeps the routes it started with")
    if started and getattr(args, "builder_strong_model", None):
        raise ValueError(
            "--builder-strong-model is a new-run policy; existing runs keep their persisted budget and route"
        )
    if started and adaptive.resume_refused(state.get("settings") or {}, getattr(args, "adaptive_planning", None)):
        raise ValueError("Adaptive planning is a new-run policy; a saved run keeps its planning flow")
    if started and compact_planning.resume_refused(
        state.get("settings") or {}, getattr(args, "compact_bugfix_planning", None)
    ):
        raise ValueError(f"{compact_planning.FLAG} is a new-run policy; a saved run keeps its planning path")
    if started and skip_requirements.resume_refused(state.get("settings") or {}, args):
        raise ValueError(
            "--skip-requirements and --skip-to-build are chosen when a run starts; a saved run keeps its choice"
        )
    if started and requirements_review.resume_refused(state.get("settings") or {}, getattr(args, "spec_first", None)):
        raise ValueError("--spec-first is a new-run policy; a saved run keeps its planning flow")
    manifest_input = None
    scope_arguments = {
        "approved_scope": getattr(args, "figma_approved_scope", None),
        "approved_scope_sha256": getattr(args, "figma_approved_scope_sha256", None),
    }
    if bool(scope_arguments["approved_scope"]) != bool(scope_arguments["approved_scope_sha256"]) or (
        scope_arguments["approved_scope"]
        and not (getattr(args, "figma_manifest", None) or getattr(args, "revise_figma_manifest", None))
    ):
        raise ValueError(
            "Approved scope requires external path, trusted pin and --figma-manifest or stopped --revise-figma-manifest"
        )
    if getattr(args, "figma_manifest", None):
        if started:
            raise ValueError("--figma-manifest is a new-run input; saved references are immutable")
        manifest_input = getattr(args, "_design_manifest_input", None) or design_manifest.load(
            args.figma_manifest, **scope_arguments
        )
    if started and not getattr(args, "status", False):
        design_manifest.context(state.get("settings") or {})
    saved_provider = dict(state.get("settings") or {})
    # Checkpoints created before provider selection shipped were necessarily
    # OpenCode runs.  Treating that as explicit prevents an unsafe transport
    # switch when they are resumed.
    if started and "provider" not in saved_provider:
        saved_provider["provider"] = "opencode"
    saved_engine = state.get("settings", {}).get("engine") or ("codex" if started else None)
    # A new run that nobody routed takes the backend this machine has connected when OpenCode is
    # not installed (#2049): the CLI applies that choice to args before it resolves the provider
    # facade (autocode_native_invocation.select_provider), so here it already reads as explicit.
    engine = getattr(args, "engine", None) or saved_engine or DEFAULT_ENGINE
    if engine not in ("codex", "opencode"):
        raise ValueError(
            f"Engine {engine!r} is not bundled in this checkout; providers live in "
            "~/.config/autocode/providers/ and run with --provider"
        )
    provider_name = autocode_providers.select(
        getattr(args, "provider", None), saved_provider, default="opencode" if engine == "codex" else None
    )
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
            declared = (
                file["nodes"]
                if manifest_input["body"]["version"] == 1
                else [
                    node
                    for page in file["pages"]
                    for node in design_manifest.inventory._metadata_nodes(page, manifest_input["root"])[0]
                ]
            )
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
    requested_joint = getattr(args, "joint_planning", False) or bool(single_model)
    enable_saved_joint = started and requested_joint and not saved_joint
    if started:
        if enable_saved_joint:
            saved = state.get("settings", {})
            if engine != saved_engine or any(
                planning.engine_for(saved, role) != engine for role in saved.get("roles", {})
            ):
                raise ValueError("Start a new run to enable joint planning across different session engines")
            if any(state.get(key) for key in ("active_stage", "pending_report_repair", "uncertain_artifacts")):
                raise ValueError("Resolve the saved provider attempt before enabling joint planning")
            restarting_discovery = engine == "codex" and state.get("next_stage") == "astra_discovery"
            approved_boundary = goals.approved(state) and state.get("next_stage") in (
                "astra_plan",
                "orchestrator",
                "terra",
                "sol",
                "astra_review",
                "astra_checkpoint",
            )
            if (
                state.get("version", 1) < 3
                or not (restarting_discovery or approved_boundary)
                or state.get("status") != "RUNNING"
                and not str(state.get("status", "")).startswith("PAUSED_")
            ):
                raise ValueError(
                    "Start a new run or reach an approved execution boundary before enabling joint planning"
                )
        joint = saved_joint or enable_saved_joint
    elif engine == "codex":
        joint = requested_joint
    else:
        joint = True
    if joint and engine not in ("codex", "opencode"):
        raise ValueError("--joint-planning requires a supported planning engine")
    if getattr(args, "planning_v2", False) and not joint:
        raise ValueError("--planning-v2 requires --joint-planning or an engine where joint planning is default")
    if (
        started
        and getattr(args, "planning_v2", False)
        and role_names.planning_flow(state) != "v2"
        and not enable_saved_joint
    ):
        raise ValueError(
            "Start a new run, or explicitly enable joint planning at its supported migration boundary, to select planning-v2"
        )
    if (
        getattr(args, "requirements_model", None)
        or getattr(args, "requirements_reasoning_effort", None)
        or getattr(args, "glm_reasoning_effort", None)
        or getattr(args, "plan_reviewer_model", None)
        or getattr(args, "plan_reviewer_reasoning_effort", None)
    ) and not joint:
        raise ValueError("Planner role overrides require --joint-planning")
    if getattr(args, "glm_model", None) and not joint:
        raise ValueError("--glm-model requires --joint-planning")
    if started and engine != saved_engine:
        raise ValueError("Start a new run to change engines; Codex and OpenCode session IDs are not interchangeable")
    if engine == "opencode" and any(getattr(args, f"{r}_provider", None) for r in DEFAULT_ROLE_MODELS):
        raise ValueError("For OpenCode use --<role>-model instead of --<role>-provider")
    if state.get("settings"):
        settings = json.loads(json.dumps(state["settings"]))
        settings["evidence_provenance"] = provenance
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
                "model": (
                    opencode.DEFAULT_MODELS["completion"]
                    if completion_engine == "opencode"
                    else DEFAULT_ROLE_MODELS["completion"]
                ),
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
        for flag, name in (
            ("max_iterations", "iteration_ceiling"),
            ("legacy_iteration_ceiling", "iteration_ceiling"),
            ("max_seconds", "max_seconds"),
            ("max_cost_usd", "max_cost_usd"),
            ("max_stage_seconds", "stage_timeout_seconds"),
            ("max_idle_seconds", "idle_timeout_seconds"),
            ("max_tool_seconds", "tool_timeout_seconds"),
            ("no_progress_limit", "no_progress_batches"),
            ("max_findings_per_task", "max_findings_per_task"),
        ):
            selected = getattr(args, flag, None)
            if selected is not None:
                settings.setdefault("limits", {})[name] = selected
                settings.setdefault("budget_origins", {})[name] = "user_explicit"
        if enable_saved_joint and engine == "opencode":
            settings["joint_planning"] = True
            settings["roles"].setdefault(
                "requirements",
                {
                    "engine": "opencode",
                    "provider": None,
                    "model": getattr(args, "requirements_model", None)
                    or getattr(args, "glm_model", None)
                    or opencode.DEFAULT_MODELS.get("requirements", opencode.DEFAULT_MODELS["glm"]),
                    "reasoning_effort": getattr(args, "requirements_reasoning_effort", None),
                },
            )
            settings["roles"]["glm"] = {
                "engine": "opencode",
                "provider": None,
                "model": getattr(args, "glm_model", None) or opencode.DEFAULT_MODELS["glm"],
                "reasoning_effort": getattr(args, "glm_reasoning_effort", None),
            }
            settings.setdefault("transport_identities", {})["opencode"] = settings["transport_identity"]
            role_transport.check_models(settings, opencode, Path(state["workspace"]))
            role_transport.check_subscription_routes(settings, opencode, Path(state["workspace"]))
        if joint:
            configure_joint(settings, args, fresh=False, planning=planning, opencode=opencode)
        # A saved run re-records only the routes a flag just moved, as `budget_origins` does below.
        role_route.moved(args, settings)
        role_route.record_efforts(args, settings, fresh=False)
        if getattr(args, "planning_v2", False):
            settings["planning_flow"] = "v2"
        if getattr(args, "unlimited_iterations", False):
            settings.setdefault("limits", {})["iteration_ceiling"] = None
            settings.setdefault("budget_origins", {})["iteration_ceiling"] = "user_explicit"
        if getattr(args, "max_milestone_seconds", None) is not None:
            if not milestones.enabled({"settings": settings}) and not getattr(args, "milestone_checkpoints", False):
                raise ValueError("Enable --milestone-checkpoints before setting its budget")
            if milestones.enabled({"settings": settings}):
                settings["milestone_checkpoints"]["max_seconds"] = args.max_milestone_seconds
                settings.setdefault("budget_origins", {})["milestone_max_seconds"] = "user_explicit"
        if getattr(args, "max_milestone_replans", None) is not None:
            if not milestones.enabled({"settings": settings}) and not getattr(args, "milestone_checkpoints", False):
                raise ValueError("Enable --milestone-checkpoints before setting the replan limit")
            if milestones.enabled({"settings": settings}):
                held_pause = (pause_authority.interrupted(state) or {}).get("status") or pause_authority.held_origin(
                    state
                ).get("pause_status")
                if held_pause == "PAUSED_MILESTONE_STALLED" and args.max_milestone_replans > 0:
                    current = state.get("milestone_progress", {}).get(milestones.key(state), {})
                    if args.max_milestone_replans <= current.get("replans", 0):
                        raise ValueError("--max-milestone-replans must exceed the replans already used, or be 0")
                settings["milestone_checkpoints"]["max_replans"] = (
                    None if args.max_milestone_replans == 0 else args.max_milestone_replans
                )
        if getattr(args, "max_milestone_stalled_reviews", None) is not None:
            if not milestones.enabled({"settings": settings}):
                raise ValueError("Enable --milestone-checkpoints before setting the review limit")
            settings["milestone_checkpoints"]["stalled_reviews"] = (
                None if args.max_milestone_stalled_reviews == 0 else args.max_milestone_stalled_reviews
            )
        if getattr(args, "accept_transport_change", False):
            if not transport_recovery.scope_matches(state, engine):
                # #909: name the accepted command for this state instead of letting
                # the operator guess; the plain resume is the recovery for every
                # other pause family.
                raise ValueError(
                    "--accept-transport-change requires an OpenCode run paused for a transport change"
                    + (
                        pause_command.recovery(state.get("needs"))
                        or f" -- from {state.get('status')}, run the plain resume (no recovery flags)"
                    )
                )
            if transport_recovery.unresolved_attempt(state):
                raise ValueError("Resolve the saved provider attempt before accepting a transport change")
            workspace = Path(state["workspace"])
            current = opencode.local_settings(workspace)
            routed = {
                role: config
                for role, config in settings["roles"].items()
                if planning.engine_for(settings, role) == "opencode"
            }
            opencode.check_models(routed, workspace)
            opencode.check_subscription_routes(routed, workspace)
            settings["transport_identity"] = current
            settings.setdefault("transport_identities", {})["opencode"] = current
        if getattr(args, "max_parallel_builders", None) is not None:
            if not settings.get("orchestration", {}).get("enabled"):
                raise ValueError("Start a new run to enable milestone orchestration")
            settings["orchestration"]["max_parallel"] = args.max_parallel_builders
        if acceptance_tests.resume_refused(settings, getattr(args, "blind_tests", None)):
            raise ValueError("Start a new run to change blind tests setting")
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
            models[record["role"]] = command[command.index("--model") + 1]
        for index, item in enumerate(command[:-1]):
            if item == "-c" and command[index + 1].startswith('model_provider="'):
                providers[record["role"]] = command[index + 1][len('model_provider="') : -1]
    # Custom providers ship their own DEFAULT_MODELS (TOML [roles]); never
    # force the builtin OpenCode catalogue onto fixturetool/kilocode/etc.
    provider_mod = autocode_providers.resolve(provider_name) if provider_name else opencode
    defaults = DEFAULT_ROLE_MODELS.copy()
    if engine == "opencode":
        defaults.update(provider_mod.DEFAULT_MODELS)
    effort_defaults = opencode.DEFAULT_REASONING_EFFORTS
    roles = {
        r: {
            "model": single_model or getattr(args, f"{r}_model", None) or models.get(r) or defaults[r],
            "reasoning_effort": getattr(args, f"{r}_reasoning_effort", None)
            or args.reasoning_effort
            or local.get("model_reasoning_effort")
            or effort_defaults[r],
            "provider": getattr(args, f"{r}_provider", None) or providers.get(r) or local.get("model_provider"),
        }
        for r in DEFAULT_ROLE_MODELS
    }
    for role in getattr(args, "pin_model_role", []):
        roles[role]["model_pinned"] = True
    auto_selection = _auto_select(
        args,
        roles,
        provider_mod,
        single_model,
        models,
        providers,
        engine,
        state["workspace"],
        version=local.get("version") if provider_name in (None, "opencode") else None,
        joint=joint,
        started=started,
    )
    builder_retry = autopilot.builder_policy.configured(
        getattr(args, "builder_strong_model", None), provider_mod, single_model=single_model
    )
    settings = {
        "roles": roles,
        "transport_identity": local,
        "engine": engine,
        "provider": provider_name,
        "single_model_mode": bool(single_model),
        "budget_origins": budget_origins(args),
        "builder_retry": builder_retry,
        # What --auto-select chose, for the access check and the route record (#1426).
        **({"route_selection": auto_selection} if auto_selection is not None else {}),
        # The effort rungs each role may climb, serveability-filtered for a
        # provider that publishes its models (autocode_route_ladder).
        "route_ladders": route_ladder.configure_ladders(getattr(provider_mod, "LISTED_MODELS", None)),
        "orchestration": {
            "enabled": joint or getattr(args, "max_parallel_builders", None) is not None,
            "max_parallel": getattr(args, "max_parallel_builders", None) or 2,
        },
        "report_repair": {"max_attempts": 2},
        "evidence_provenance": provenance,
        "milestone_checkpoints": {
            **milestones.DEFAULTS,
            "max_seconds": getattr(args, "max_milestone_seconds", None)
            if getattr(args, "max_milestone_seconds", None) is not None
            else milestones.DEFAULTS["max_seconds"],
            "max_replans": (
                None
                if getattr(args, "max_milestone_replans", None) == 0
                else getattr(args, "max_milestone_replans", None)
                if getattr(args, "max_milestone_replans", None) is not None
                else milestones.DEFAULTS["max_replans"]
            ),
            "stalled_reviews": (
                None
                if getattr(args, "max_milestone_stalled_reviews", None) == 0
                else getattr(args, "max_milestone_stalled_reviews", None)
                if getattr(args, "max_milestone_stalled_reviews", None) is not None
                else milestones.DEFAULTS["stalled_reviews"]
            ),
        },
        "headroom": {"enabled": args.headroom == "on", "verified": False},
        "regression": {
            key: value
            for key, value in (
                ("test_command", getattr(args, "test_command", None)),
                ("regression_command", getattr(args, "regression_command", None)),
                ("test_report", getattr(args, "test_report", None)),
                # Launch-fixed proof root; component admission corroborates the original task.
                ("test_root", getattr(args, "test_root", None)),
                (
                    "base_patch",
                    getattr(args, "base_patch", None)
                    and operator_patch.pin(args.base_patch, state["workspace"], state.get("base_commit")),
                ),
            )
            if value
        },
        # The monorepo package the run works in (autocode_package_scope); fixed at launch.
        **({"package": args.package} if getattr(args, "package", None) else {}),
        # Who may approve: the project's require-approver, fixed at launch (autocode_required_approver).
        **({"require_approver": args.require_approver} if getattr(args, "require_approver", None) else {}),
        # Which paths a shorter planning path may never touch: the project's sensitive-paths, fixed at launch
        # (autocode_sensitive_paths); an empty list is a declaration, so None alone means "not declared".
        **({"sensitive_paths": args.sensitive_paths} if getattr(args, "sensitive_paths", None) is not None else {}),
        # The command that serves the app: the project's [serve], fixed at launch (autocode_dev_server_config).
        **({"serve": args.serve} if getattr(args, "serve", None) else {}),
        "context_soft_tokens": args.context_soft_tokens if args.context_soft_tokens is not None else 10000,
        "rotation_after_input_tokens": args.rotate_after_input_tokens
        if args.rotate_after_input_tokens is not None
        else 1000000,
        "limits": {
            "iteration_ceiling": args.legacy_iteration_ceiling
            if args.legacy_iteration_ceiling is not None
            else (state.get("iteration", 0) + args.max_iterations if args.max_iterations is not None else None),
            "max_seconds": args.max_seconds
            if args.max_seconds is not None
            else budget_recovery.RUNNER_DEFAULTS["max_seconds"],
            "stage_timeout_seconds": (
                getattr(args, "max_stage_seconds", None)
                if getattr(args, "max_stage_seconds", None) is not None
                else budget_recovery.RUNNER_DEFAULTS["stage_timeout_seconds"]
            ),
            "idle_timeout_seconds": (
                getattr(args, "max_idle_seconds", None) if getattr(args, "max_idle_seconds", None) is not None else 300
            ),
            "tool_timeout_seconds": (
                getattr(args, "max_tool_seconds", None) if getattr(args, "max_tool_seconds", None) is not None else 1800
            ),
            "no_progress_batches": args.no_progress_limit if args.no_progress_limit is not None else 3,
            "max_findings_per_task": getattr(args, "max_findings_per_task", None),
            "automatic_retries": 0,
            # Saved only when set: no cost cap by default, and never a runner default (#721).
            **({"max_cost_usd": args.max_cost_usd} if getattr(args, "max_cost_usd", None) is not None else {}),
        },
    }
    scoped_readonly = bool(
        scope_arguments["approved_scope"] and (getattr(args, "dry_run", False) or getattr(args, "status", False))
    )
    if manifest_input and not scoped_readonly:
        settings["design_manifest"] = (
            manifest_input
            if getattr(args, "dry_run", False) or getattr(args, "status", False)
            else design_manifest.retain(manifest_input, state["workspace"], **scope_arguments)
        )
    if figma_file:
        figma.require_chatgpt(local)
        for config in settings["roles"].values():
            config["provider"] = "openai"
        settings.update(
            figma_file=figma.design_url(figma_file),
            figma_review=getattr(args, "figma_review", None) or "automatic",
            figma_inventory_required=True,
            figma_references=[figma_file, *getattr(args, "figma_additional_file", [])],
        )
    if figma_file and manifest_input and manifest_input["body"]["version"] == 2:
        try:
            from . import autocode_design_intake as intake
        except ImportError:
            import autocode_design_intake as intake
        intake.require_references(manifest_input, settings["figma_references"], exact=False)
    if joint:
        configure_joint(settings, args, fresh=True, planning=planning, opencode=opencode)
    if getattr(args, "conversation_handoff", None):
        planner_routes.configure_runner_profile(settings, args)
    role_transport.configure(settings, args, state["workspace"])
    if getattr(args, "planning_v2", False):
        settings["planning_flow"] = "v2"
    if adaptive.new_run_setting(
        getattr(args, "adaptive_planning", None), joint, role_names.planning_flow(settings) == "v2"
    ):
        settings["adaptive_planning"] = True
    if getattr(args, "plan_review_full", False):
        settings["plan_review_full"] = True
    if compact_planning.new_run_setting(getattr(args, "compact_bugfix_planning", None), joint):
        settings[compact_planning.SETTING] = True
    settings.update(skip_requirements.new_run_settings(args, joint))
    # A persisted review allowance for new runs (#1565): the flag wins, then the
    # user's config.toml, then the machinery's built-in default. A resumed run
    # keeps its saved settings; this applies only where settings are first built.
    if getattr(args, "planning_review_call_limit", None) is None and not started:
        limit, source = persisted_review_call_limit()
        if limit is not None:
            settings["planning_review_call_limit"] = limit
            settings["planning_review_call_limit_origin"] = source
    if requirements_review.new_run_setting(
        getattr(args, "spec_first", None), joint, role_names.planning_flow(settings) == "v2"
    ):
        settings[requirements_review.SETTING] = True
    if acceptance_tests.new_run_setting(getattr(args, "blind_tests", None), joint):
        settings[acceptance_tests.SETTING_BLIND] = True
    if getattr(args, "unlimited_iterations", False):
        settings["limits"]["iteration_ceiling"] = None
    if utility := utility_route.configure(settings, args, started=False):
        problem = quota_route.model_problem(
            utility,
            settings["roles"][utility]["model"],
            role_engine=planning.engine_for(settings, utility),
            configured_tool=getattr(opencode, "CONFIGURED", False),
            label="--utility-model",
        )
        if problem:
            raise ValueError(problem)
    if single_model:
        # One model for every role: each role keeps its profile effort, which the model may not declare
        # (#2006). After every role exists, a runner-chosen effort moves to the nearest declared variant
        # and the move is recorded; an effort a person named refuses here, naming its flag.
        declared = _declared_levels(provider_mod, state["workspace"], single_model)
        if declared is not None:
            # Kept for the efforts derived later in the run (the Plan Reviewer's reduced tier, #2020).
            settings[route_fit.DECLARED] = {single_model: list(declared)}
        explicit = {r for r in settings["roles"] if getattr(args, f"{r}_reasoning_effort", None)}
        if getattr(args, "reasoning_effort", None):
            explicit = set(settings["roles"])
        if fits := route_fit.reconcile(settings["roles"], single_model, declared, explicit=explicit):
            settings["effort_fits"] = fits
        if note := route_fit.notice(fits, single_model, declared):
            print(note, flush=True)
    # Why each role of this new run has the route it does (autocode_role_route). Recorded here,
    # after every role exists; a saved run re-records only the routes it re-chooses (role_route.moved).
    settings["route_origins"] = role_route.origins(args, settings["roles"], models, single_model)
    if auto_selection is not None:
        # The selector's own choices are recorded as such; a pinned role keeps the
        # person's origin that `origins` just wrote.
        for role, route in auto_selection["routes"].items():
            if route.get("origin") == role_route.AUTO_SELECTED:
                settings["route_origins"][role] = role_route.AUTO_SELECTED
    role_route.record_efforts(args, settings, fresh=True)
    verification_config.validate(settings["regression"])
    return task_preflight.configure(
        state, output_policy.configure(state, autopilot.stuck.configure(settings, args), args), args
    )


def _provider_model(role, requested, mod=None):
    mod = mod or autocode_opencode
    model = requested or mod.DEFAULT_MODELS[role]
    # Bare OpenAI names from older dashboard conversations are aliases,
    # never a reason to use a separate Codex login. Config tools name models
    # themselves, so they keep the configured string.
    if not getattr(mod, "CONFIGURED", False) and "/" not in model:
        model = f"openai/{model}"
    return model


def _selected_model(settings, role):
    """The route --auto-select chose for ``role``; None when selection did not (#1426)."""
    route = ((settings.get("route_selection") or {}).get("routes") or {}).get(role) or {}
    return route.get("model") if route.get("origin") == role_route.AUTO_SELECTED else None


def _auto_chosen(settings, role) -> bool:
    """Whether --auto-select (not a flag) decided this role's route."""
    route = ((settings.get("route_selection") or {}).get("routes") or {}).get(role) or {}
    return route.get("origin") == role_route.AUTO_SELECTED


def _selected_effort(settings, role):
    """The level --auto-select chose, None when it left the model's default."""
    route = ((settings.get("route_selection") or {}).get("routes") or {}).get(role) or {}
    return route.get("reasoning_effort") if route.get("origin") == role_route.AUTO_SELECTED else None


def configure_joint(settings, args, *, fresh, planning, opencode=None):
    if opencode is None:
        opencode = autocode_opencode
    if settings.get("engine") == "codex":
        configure_codex_joint(settings, args, planning=planning)
        return
    mod = autocode_providers.resolve(settings.get("provider") or "opencode")
    if fresh:
        single_model = getattr(args, "single_model", None)
        settings["joint_planning"] = True
        settings["roles"]["requirements"] = {
            "engine": "opencode",
            "provider": None,
            "model": single_model
            or getattr(args, "requirements_model", None)
            or getattr(args, "glm_model", None)
            or _selected_model(settings, "requirements")
            or mod.DEFAULT_MODELS.get("requirements", mod.DEFAULT_MODELS["glm"]),
            "reasoning_effort": getattr(args, "requirements_reasoning_effort", None)
            or _selected_effort(settings, "requirements"),
        }
        if "completion" not in settings["roles"]:
            settings["roles"]["completion"] = {
                **settings["roles"]["astra"],
                "model": mod.DEFAULT_MODELS["completion"],
                "reasoning_effort": mod.DEFAULT_REASONING_EFFORTS["completion"],
            }
        for role in ("astra", "sol", "completion"):
            settings["roles"][role].update(
                engine="opencode",
                provider=None,
                model=single_model
                or _selected_model(settings, role)
                or _provider_model(role, getattr(args, f"{role}_model", None), mod),
            )
        terra_model = (
            single_model
            or getattr(args, "terra_model", None)
            or _selected_model(settings, "terra")
            or mod.DEFAULT_MODELS["terra"]
        )
        settings["roles"]["terra"].update(engine="opencode", provider=None, model=terra_model)
        for role, effort in mod.DEFAULT_REASONING_EFFORTS.items():
            # An auto-selected route may deliberately run at the model's own default.
            route = settings["roles"].get(role)
            if route and not route.get("reasoning_effort") and not _auto_chosen(settings, role):
                route["reasoning_effort"] = effort
        glm_model = (
            single_model
            or getattr(args, "glm_model", None)
            or _selected_model(settings, "glm")
            or mod.DEFAULT_MODELS["glm"]
        )
        settings["roles"]["glm"] = {
            "engine": "opencode",
            "provider": None,
            "model": glm_model,
            "reasoning_effort": getattr(args, "glm_reasoning_effort", None)
            or (
                _selected_effort(settings, "glm")
                if _auto_chosen(settings, "glm")
                else mod.DEFAULT_REASONING_EFFORTS.get("glm")
            ),
        }
        plan_reviewer_route = {
            "engine": "opencode",
            "provider": None,
            "model": (
                single_model
                or getattr(args, "plan_reviewer_model", None)
                or _selected_model(settings, "plan_reviewer")
                or mod.DEFAULT_MODELS.get("plan_reviewer")
                or planning.PINNED_REVIEWER_MODEL
            ),
            "reasoning_effort": (
                getattr(args, "plan_reviewer_reasoning_effort", None)
                or (
                    _selected_effort(settings, "plan_reviewer")
                    if _auto_chosen(settings, "plan_reviewer")
                    else mod.DEFAULT_REASONING_EFFORTS.get("plan_reviewer")
                )
            ),
            "model_pinned": True,
        }
        light_effort = getattr(mod, "DEFAULT_LIGHT_EFFORTS", {}).get("plan_reviewer")
        if light_effort is not None:
            plan_reviewer_route["light_effort"] = light_effort
        settings["roles"]["plan_reviewer"] = plan_reviewer_route
        settings["transport_identities"] = {"opencode": settings["transport_identity"]}
    elif getattr(args, "glm_model", None):
        settings["roles"]["glm"]["model"] = args.glm_model
    if getattr(args, "requirements_model", None) or getattr(args, "requirements_reasoning_effort", None):
        if "requirements" not in settings["roles"]:
            raise ValueError(
                "This saved run predates the separate requirements stage; start a new run to select its model"
            )
        if getattr(args, "requirements_model", None):
            settings["roles"]["requirements"]["model"] = args.requirements_model
        if getattr(args, "requirements_reasoning_effort", None):
            settings["roles"]["requirements"]["reasoning_effort"] = args.requirements_reasoning_effort
    _resolver_override(settings, args)
    if getattr(args, "glm_reasoning_effort", None):
        settings["roles"]["glm"]["reasoning_effort"] = args.glm_reasoning_effort
    if getattr(args, "plan_reviewer_model", None):
        settings["roles"]["plan_reviewer"]["model"] = args.plan_reviewer_model
    if getattr(args, "plan_reviewer_reasoning_effort", None):
        settings["roles"]["plan_reviewer"]["reasoning_effort"] = args.plan_reviewer_reasoning_effort
    if fresh:
        role_transport.configure(settings, args, getattr(args, "workspace", None))
    configured_tool = getattr(opencode, "CONFIGURED", False)
    for role, config in settings["roles"].items():
        # One rule for a launch and for a model named at a quota stop (autocode_quota_route).
        problem = quota_route.model_problem(
            role,
            config.get("model"),
            role_engine=planning.engine_for(settings, role),
            configured_tool=role_transport.configured(settings, role, configured_tool),
        )
        if problem:
            raise ValueError(problem)


def _resolver_override(settings, args):
    """The saved Resolver override has identical semantics on both engines."""
    if getattr(args, "resolver_model", None) or getattr(args, "resolver_reasoning_effort", None):
        settings["roles"].setdefault("resolver", copy.deepcopy(settings["roles"]["astra"]))
        if getattr(args, "resolver_model", None):
            settings["roles"]["resolver"]["model"] = args.resolver_model
        if getattr(args, "resolver_reasoning_effort", None):
            settings["roles"]["resolver"]["reasoning_effort"] = args.resolver_reasoning_effort


def configure_codex_joint(settings, args, *, planning):
    """Independent planning sessions through the existing native Codex login."""
    check_subscription(settings["transport_identity"])
    roles = settings["roles"]
    single_model = getattr(args, "single_model", None)
    for role in ("requirements", "glm", "plan_reviewer"):
        creating_requirements = role == "requirements" and role not in roles
        roles.setdefault(role, copy.deepcopy(roles["astra"]))
        if planning.engine_for(settings, role) != "codex":
            raise ValueError("Native Codex joint planning cannot switch a saved role's engine")
        route = roles[role]
        route["engine"] = "codex"
        model = getattr(args, f"{role}_model", None)
        if creating_requirements:
            model = model or getattr(args, "glm_model", None)
        effort = getattr(args, f"{role}_reasoning_effort", None)
        if single_model:
            route["model"] = single_model
        elif model:
            route["model"] = model
        if effort:
            route["reasoning_effort"] = effort
    _resolver_override(settings, args)
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


def migrate_opencode_roles(state: RunState, run_dir, workspace, *, planning, opencode=None, write_json, now):
    """Move old mixed-CLI runs to OpenCode at a recovered, locked boundary."""
    if opencode is None:
        opencode = autocode_opencode
    settings = state["settings"]
    if settings.get("replay_recording") or role_transport.enabled(settings):
        return False
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
            raise support.Paused(
                "PAUSED_TRANSPORT_MIGRATION", f"Cannot map the saved {role} provider to OpenCode automatically"
            )
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
            candidate.setdefault("session_rotations", []).append(
                {
                    "role": role,
                    "old_session": old,
                    "at": at,
                    "reason": "Moved from Codex to OpenCode; saved handoffs and evidence retained",
                }
            )
    backup = Path(run_dir) / f"state.pre-opencode-{uuid.uuid4().hex[:8]}.json"
    candidate.setdefault("configuration_changes", []).append(
        {
            "at": at,
            "previous": settings,
            "selected": copy.deepcopy(selected),
            "backup": str(backup),
            "reason": "Use OpenCode and its current ChatGPT OAuth login for every role",
        }
    )
    write_json(backup, state)
    write_json(Path(run_dir) / "state.json", candidate)
    cast(dict, state).clear()
    state.update(candidate)
    return True

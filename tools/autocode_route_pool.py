"""Candidate route pool and paired-role swap search for automatic model-stop recovery.

When a stage, report repair, workflow job or parallel Builder worker hits a model stop
(``quota_route.STATUSES``), AutoCode searches for a replacement model in three tiers:

1. The run's saved ``route_selection.candidates`` permitted set, when present;
2. The models already configured in ``settings.roles`` on the same backend — first as a
   single-role move, then as an atomic producer/verifier swap when cross-model independence
   requires moving a checker that has not yet judged work produced by its proposed family;
3. Other connected ``subscription`` models on the same backend (``stage_choices`` /
   ``stages.toml``), never unconfigured ``pay per token`` accounts — first single-role, then
   paired swap.

Pure selection helper: writes no files, mutates no run state and stays below the controller.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_model_identity as model_identity
    from . import autocode_quota_route as quota_route
    from . import autocode_role_transport as role_transport
    from . import autocode_route_selector as route_selector
    from . import autocode_source_scope as source_scope
    from . import autocode_stage_choices as stage_choices
    from . import autocode_support as support
    from . import autocode_verifier_independence as independence
    from . import autocode_worker_quota as worker_quota
except ImportError:
    import autocode_model_identity as model_identity
    import autocode_quota_route as quota_route
    import autocode_role_transport as role_transport
    import autocode_route_selector as route_selector
    import autocode_source_scope as source_scope
    import autocode_stage_choices as stage_choices
    import autocode_support as support
    import autocode_verifier_independence as independence
    import autocode_worker_quota as worker_quota

_COUPLED_ROLES = ("planner", "plan_reviewer", "builder", "tester", "completion")
_ROLE_SCAN_ORDER = (
    "builder",
    "planner",
    "plan_reviewer",
    "tester",
    "completion",
    "requirements",
    "task_planner",
    "resolver",
    "stuck_investigator",
    "architect",
    "acceptance_tester",
)
_KNOWN_FAMILIES = ("glm", "mimo", "claude", "openai")


def account_key(model: str | None, role_engine: str, settings: dict) -> str:
    """Normalized billing-account bucket for quota exhaustion checks."""
    if role_engine == "codex":
        return "codex:openai"
    if settings.get("provider") == "claude":
        return "claude:anthropic"
    if not isinstance(model, str) or not model:
        return f"{role_engine}:{settings.get('provider') or 'default'}"
    prefix = model.split("/", 1)[0] if "/" in model else ""
    if prefix in ("xiaomi-token-plan-sgp", "mimo-token-plan"):
        return "xiaomi-token-plan"
    return prefix or f"{role_engine}:{settings.get('provider') or 'default'}"


def vendor_key(model: str | None, role_engine: str, settings: dict) -> str:
    """Upstream model-provider bucket for content-filter refusal separation."""
    if isinstance(model, str):
        parts = model.split("/")
        if len(parts) >= 3:
            return f"{parts[0]}/{parts[1]}"
    return account_key(model, role_engine, settings)


def routes_signature(roles_map: dict) -> list[list[str]]:
    """Canonical ``[[role, model], ...]`` snapshot of coupled roles for swap-cycle prevention."""
    return [
        [role, str((roles_map.get(role) or {}).get("model") or "")]
        for role in _COUPLED_ROLES
        if isinstance(roles_map.get(role), dict) and (roles_map.get(role) or {}).get("model")
    ]


def _seen_signatures(state: RunState) -> list[list[list[str]]]:
    seen = []
    for event in state.get("user_events") or []:
        if isinstance(event, dict) and event.get("kind") == quota_route.KIND:
            prior = event.get("prior_routes")
            if isinstance(prior, list) and prior not in seen:
                seen.append(prior)
    return seen


def _quota_dead_accounts(state: RunState, settings: dict, role: str, dead: str, pause_status: str) -> set[str]:
    exhausted: set[str] = set()
    for event in state.get("user_events") or []:
        if not isinstance(event, dict) or event.get("kind") != quota_route.KIND:
            continue
        if event.get("pause_status") == quota_route.QUOTA_STATUS and not event.get("paired_partner"):
            old_model = event.get("from")
            old_engine = event.get("engine") or quota_route.engine(settings, event.get("role") or role)
            if isinstance(old_model, str) and old_model:
                exhausted.add(account_key(old_model, old_engine, settings))
    if pause_status == quota_route.QUOTA_STATUS:
        exhausted.add(account_key(dead, quota_route.engine(settings, role), settings))
    return exhausted


def _excluded_identities(role: str) -> set[str]:
    entries = (route_selector.table().get("excluded") or {}).get(role) or ()
    return {
        model_identity.identity(entry["model"])
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("model"), str)
    }


def _produced_builder_models(state: RunState, workspace) -> list[str]:
    models: list[str] = []
    for row in state.get("stages") or []:
        if not isinstance(row, dict) or row.get("report_only"):
            continue
        if (row.get("original_stage") or row.get("stage")) != "builder":
            continue
        if row.get("changed_files") or (not row.get("abandoned") and not row.get("rejected")):
            launched = quota_route._launched_model(row)
            if isinstance(launched, str) and launched:
                models.append(launched)
    active = state.get("active_stage") or {}
    if (
        isinstance(active, dict)
        and (active.get("original_stage") or active.get("stage")) == "builder"
        and not active.get("report_only")
    ):
        edited = bool(active.get("changed_files"))
        if not edited and active.get("before_ref") and workspace is not None:
            before_path = Path(active["before_ref"])
            if before_path.is_file():
                try:
                    before = json.loads(before_path.read_text())
                    after = source_scope.snapshot(Path(workspace), state, base_snapshot=support.snapshot)
                    edited = bool(support.changed_paths(before, after))
                except (OSError, ValueError, KeyError, TypeError, support.Paused):
                    edited = True
        if edited:
            launched = quota_route._launched_model(active) or (
                ((state.get("settings") or {}).get("roles") or {}).get("builder") or {}
            ).get("model")
            if isinstance(launched, str) and launched:
                models.append(launched)
    return models


def _produced_planner_models(state: RunState) -> list[str]:
    contract = state.get("goal_contract")
    if not isinstance(contract, dict) or contract.get("approved"):
        return []
    models: list[str] = []
    for row in state.get("stages") or []:
        if not isinstance(row, dict) or row.get("abandoned") or row.get("rejected"):
            continue
        if (row.get("original_stage") or row.get("stage")) in ("draft_plan", "revise_plan", "finalize_plan"):
            launched = quota_route._launched_model(row)
            if isinstance(launched, str) and launched:
                models.append(launched)
    return models


def verifier_history_ok(state: RunState, workspace, proposed_models: dict[str, str], settings: dict) -> bool:
    """Ensure no verifier is moved onto a model family whose work already sits in the run."""
    vendor = independence.same_vendor_engine(settings)
    current_roles = settings.get("roles") or {}
    builder_models = _produced_builder_models(state, workspace)
    for verifier in ("tester", "completion"):
        chosen = proposed_models.get(verifier)
        existing = (current_roles.get(verifier) or {}).get("model")
        if (
            chosen
            and chosen != existing
            and any(independence.shares(built, chosen, vendor_engine=vendor) for built in builder_models)
        ):
            return False
    pr_chosen = proposed_models.get("plan_reviewer")
    pr_existing = (current_roles.get("plan_reviewer") or {}).get("model")
    if pr_chosen and pr_chosen != pr_existing:
        planner_models = _produced_planner_models(state)
        if any(independence.shares(planned, pr_chosen, vendor_engine=vendor) for planned in planner_models):
            return False
    return True


def _candidate_allowed(
    candidate_model: str,
    target_role: str,
    *,
    stopped_role: str,
    dead: str,
    pause_status: str,
    settings: dict,
    failed_identities: set[str],
    dead_accounts: set[str],
    runtime,
    workspace,
    worker: dict | None = None,
) -> bool:
    if not isinstance(candidate_model, str) or not candidate_model:
        return False
    role_engine = quota_route.engine(settings, target_role)
    opencode_mod = getattr(runtime, "opencode", None)
    configured_tool = role_transport.configured(settings, target_role, bool(getattr(opencode_mod, "CONFIGURED", False)))
    if quota_route.model_problem(
        target_role, candidate_model, role_engine=role_engine, configured_tool=configured_tool
    ):
        return False
    cand_id = model_identity.identity(candidate_model)
    if cand_id in _excluded_identities(target_role):
        return False
    if target_role == "plan_reviewer" and model_identity.family(candidate_model) == "mimo":
        return False
    if account_key(candidate_model, role_engine, settings) in dead_accounts:
        return False
    if target_role == stopped_role:
        if candidate_model == dead or cand_id == model_identity.identity(dead):
            return False
        if cand_id in failed_identities or candidate_model in failed_identities:
            return False
        if pause_status == quota_route.QUOTA_STATUS and account_key(
            candidate_model, role_engine, settings
        ) == account_key(dead, role_engine, settings):
            return False
        if pause_status == quota_route.REFUSAL_STATUS and not independence.same_vendor_engine(settings):
            if vendor_key(candidate_model, role_engine, settings) == vendor_key(dead, role_engine, settings):
                return False
            if model_identity.family(dead) in _KNOWN_FAMILIES and model_identity.shared_family(candidate_model, dead):
                return False
        if worker is not None:
            try:
                worker_quota.validate_model(candidate_model, worker, model_identity.family)
            except ValueError:
                return False
    if role_engine == "opencode" and opencode_mod is not None and workspace is not None:
        try:
            facade = role_transport.adapter(settings, target_role, opencode_mod, workspace)
            check = getattr(facade, "check_models", None)
            if callable(check):
                check({target_role: {"model": candidate_model}}, workspace)
        except (RuntimeError, ValueError, support.Paused):
            return False
    return True


def _selection_billing(selection: dict, model: str | None) -> str | None:
    if not model:
        return None
    wanted = model_identity.identity(model)
    for candidates in ((selection or {}).get("candidates") or {}).values():
        for candidate in candidates:
            if model_identity.identity(candidate.get("model")) == wanted and candidate.get("billing"):
                return candidate["billing"]
    return None


def _selection_account(selection: dict, model: str | None) -> str | None:
    if not model:
        return None
    wanted = model_identity.identity(model)
    for candidates in ((selection or {}).get("candidates") or {}).values():
        for candidate in candidates:
            if model_identity.identity(candidate.get("model")) == wanted and candidate.get("account"):
                return candidate["account"]
    return None


def _configured_pool(settings: dict, role: str, selection: dict, *, worker: dict | None = None) -> list[dict]:
    """Distinct models already configured on ``settings.roles`` sharing ``role``'s engine."""
    roles_map = settings.get("roles") or {}
    wanted_engine = quota_route.engine(settings, role)
    ordered_roles: list[str] = []
    if worker is not None and "builder" in roles_map:
        ordered_roles.append("builder")
    for name in (*_ROLE_SCAN_ORDER, *roles_map.keys()):
        if (worker is not None or name != role) and name not in ordered_roles and name in roles_map:
            ordered_roles.append(name)
    if worker is None and role in roles_map and role not in ordered_roles:
        ordered_roles.append(role)
    seen: set[str] = set()
    pool: list[dict] = []
    for other in ordered_roles:
        cfg = roles_map.get(other)
        model = cfg.get("model") if isinstance(cfg, dict) else None
        if not isinstance(model, str) or not model or model in seen:
            continue
        if quota_route.engine(settings, other) != wanted_engine:
            continue
        seen.add(model)
        pool.append(
            {
                "model": model,
                "account": _selection_account(selection, model),
                "billing": _selection_billing(selection, model),
                "levels": None,
                "levels_listed": False,
            }
        )
    return pool


def _qualified_rank(role: str, model: str) -> tuple[int, int, str]:
    wanted = model_identity.identity(model)
    lookup_role = "task_planner" if role in ("resolver", "stuck_investigator") else role
    for index, entry in enumerate(route_selector.qualified(lookup_role)):
        routes = entry.get("routes") or [entry["model"]]
        if wanted in {model_identity.identity(item) for item in routes}:
            return (0, entry.get("preference", index), model)
    return (1, 0, model)


def _subscription_pool(
    runtime,
    settings: dict,
    role: str,
    workspace,
    selection: dict,
    *,
    offers_fn=None,
    stages_file=None,
) -> list[dict]:
    """Connected ``subscription`` models on ``role``'s backend, ordered by saved choice then qualification."""
    role_engine = quota_route.engine(settings, role)
    backend = "codex" if role_engine == "codex" else (settings.get("provider") or "opencode")
    raw_rows: list[dict] = []
    saved_choices: dict = {}
    if offers_fn is not None:
        try:
            discovered = list(offers_fn(backend, workspace) or [])
        except (RuntimeError, ValueError, OSError):
            discovered = []
        for row in discovered:
            if isinstance(row, dict) and row.get("billing") == "subscription" and row.get("model"):
                raw_rows.append(
                    {
                        "model": row["model"],
                        "account": row.get("account"),
                        "billing": "subscription",
                        "levels": list(row["levels"]) if row.get("levels_listed") else None,
                        "levels_listed": bool(row.get("levels_listed")),
                    }
                )
        if stages_file is not None:
            try:
                saved_choices = stage_choices.load(Path(stages_file)).get(backend, {})
            except (OSError, ValueError):
                saved_choices = {}
    elif selection.get("candidates"):
        for rows in selection["candidates"].values():
            for cand in rows:
                if isinstance(cand, dict) and cand.get("billing") == "subscription" and cand.get("model"):
                    raw_rows.append(
                        {
                            "model": cand["model"],
                            "account": cand.get("account"),
                            "billing": "subscription",
                            "levels": list(cand["levels"]) if isinstance(cand.get("levels"), list) else None,
                            "levels_listed": isinstance(cand.get("levels"), list),
                        }
                    )
    elif getattr(runtime, "opencode", None) is not None or stages_file is not None:
        try:
            saved_choices = stage_choices.load(Path(stages_file) if stages_file is not None else None).get(backend, {})
        except (OSError, ValueError):
            saved_choices = {}
        if backend == "codex":
            for saved_cfg in saved_choices.values():
                if isinstance(saved_cfg, dict) and saved_cfg.get("model"):
                    raw_rows.append(
                        {
                            "model": saved_cfg["model"],
                            "account": "ChatGPT subscription",
                            "billing": "subscription",
                            "levels": None,
                            "levels_listed": False,
                        }
                    )
        elif getattr(runtime, "opencode", None) is not None:
            try:
                facade = role_transport.adapter(settings, role, runtime.opencode, workspace)
                discovered = stage_choices.offers(
                    backend,
                    workspace,
                    resolve=lambda name: facade if name == backend else stage_choices.providers.resolve(name),
                )
            except (RuntimeError, ValueError, OSError, support.Paused):
                discovered = []
            for row in discovered:
                if isinstance(row, dict) and row.get("billing") == "subscription" and row.get("model"):
                    raw_rows.append(
                        {
                            "model": row["model"],
                            "account": row.get("account"),
                            "billing": "subscription",
                            "levels": list(row["levels"]) if row.get("levels_listed") else None,
                            "levels_listed": bool(row.get("levels_listed")),
                        }
                    )

    saved_order: list[str] = []
    if isinstance(saved_choices.get(role), dict) and saved_choices[role].get("model"):
        saved_order.append(saved_choices[role]["model"])
    for saved_role, saved_cfg in saved_choices.items():
        if (
            saved_role != role
            and isinstance(saved_cfg, dict)
            and saved_cfg.get("model")
            and saved_cfg["model"] not in saved_order
        ):
            saved_order.append(saved_cfg["model"])

    deduped: dict[str, dict] = {}
    for row in raw_rows:
        model = row.get("model")
        if isinstance(model, str) and model and model not in deduped:
            deduped[model] = row

    def sort_key(item: dict) -> tuple[int, int, int, str]:
        model = item["model"]
        if model in saved_order:
            return (0, saved_order.index(model), 0, model)
        q_tier, q_pref, _ = _qualified_rank(role, model)
        return (1 + q_tier, q_pref, 0, model)

    return sorted(deduped.values(), key=sort_key)


def _valid_assignment(
    state: RunState,
    workspace,
    settings: dict,
    updates: dict[str, str],
    seen_signatures: list[list[list[str]]],
) -> bool:
    sim = copy.deepcopy(settings)
    roles_map = sim.setdefault("roles", {})
    for target_role, model in updates.items():
        roles_map.setdefault(target_role, {})["model"] = model
    if independence.violations(sim):
        return False
    proposed = {name: (cfg.get("model") or "") for name, cfg in roles_map.items() if isinstance(cfg, dict)}
    if not verifier_history_ok(state, workspace, proposed, settings):
        return False
    sig = routes_signature(roles_map)
    return not (sig and sig in seen_signatures)


def _build_plan(
    role: str,
    primary_item: dict,
    partner_items: list[tuple[str, dict]],
    selection: dict,
    tier: str,
    *,
    selector_route: dict | None = None,
) -> dict:
    sel_routes = (selection or {}).get("routes") or {}
    primary = {
        "role": role,
        "model": primary_item["model"],
        "account": primary_item.get("account"),
        "billing": primary_item.get("billing"),
        "effort": selector_route.get("reasoning_effort") if selector_route is not None else None,
        "has_selector_effort": selector_route is not None,
        "levels": primary_item.get("levels") if primary_item.get("levels_listed") else None,
        "update_selection": isinstance(sel_routes, dict) and role in sel_routes,
        "selection_route": (
            {**selector_route, "origin": "quota_assigned"}
            if selector_route is not None
            else {
                **(sel_routes.get(role) or {}),
                "model": primary_item["model"],
                **({"account": primary_item["account"]} if primary_item.get("account") else {}),
                "origin": "quota_assigned",
            }
            if isinstance(sel_routes, dict) and role in sel_routes
            else None
        ),
    }
    partners = []
    for partner_role, item in partner_items:
        partners.append(
            {
                "role": partner_role,
                "model": item["model"],
                "account": item.get("account"),
                "billing": item.get("billing"),
                "levels": item.get("levels") if item.get("levels_listed") else None,
                "update_selection": isinstance(sel_routes, dict) and partner_role in sel_routes,
                "selection_route": (
                    {
                        **(sel_routes.get(partner_role) or {}),
                        "model": item["model"],
                        **({"account": item["account"]} if item.get("account") else {}),
                        "origin": "quota_assigned",
                    }
                    if isinstance(sel_routes, dict) and partner_role in sel_routes
                    else None
                ),
            }
        )
    return {"primary": primary, "partners": partners, "tier": tier}


def _search_single(
    pool: list[dict],
    *,
    state: RunState,
    workspace,
    settings: dict,
    role: str,
    dead: str,
    pause_status: str,
    failed_identities: set[str],
    dead_accounts: set[str],
    seen_signatures: list[list[list[str]]],
    selection: dict,
    runtime,
    worker: dict | None,
    tier: str,
) -> dict | None:
    for item in pool:
        if not _candidate_allowed(
            item["model"],
            role,
            stopped_role=role,
            dead=dead,
            pause_status=pause_status,
            settings=settings,
            failed_identities=failed_identities,
            dead_accounts=dead_accounts,
            runtime=runtime,
            workspace=workspace,
            worker=worker,
        ):
            continue
        if _valid_assignment(state, workspace, settings, {role: item["model"]}, seen_signatures):
            return _build_plan(role, item, [], selection, tier)
    return None


def _search_paired_swap(
    pool: list[dict],
    *,
    state: RunState,
    workspace,
    settings: dict,
    role: str,
    dead: str,
    pause_status: str,
    failed_identities: set[str],
    dead_accounts: set[str],
    seen_signatures: list[list[list[str]]],
    selection: dict,
    runtime,
    tier: str,
) -> dict | None:
    if role not in _COUPLED_ROLES:
        return None
    roles_map = settings.get("roles") or {}
    vendor = independence.same_vendor_engine(settings)

    def allowed_for_partner(partner_role: str, model: str) -> bool:
        return _candidate_allowed(
            model,
            partner_role,
            stopped_role=role,
            dead=dead,
            pause_status=pause_status,
            settings=settings,
            failed_identities=failed_identities,
            dead_accounts=dead_accounts,
            runtime=runtime,
            workspace=workspace,
        )

    for primary_item in pool:
        new_model = primary_item["model"]
        if not _candidate_allowed(
            new_model,
            role,
            stopped_role=role,
            dead=dead,
            pause_status=pause_status,
            settings=settings,
            failed_identities=failed_identities,
            dead_accounts=dead_accounts,
            runtime=runtime,
            workspace=workspace,
        ):
            continue

        if role == "builder":
            clashing = [
                verifier
                for verifier in ("tester", "completion")
                if isinstance(roles_map.get(verifier), dict)
                and (roles_map[verifier].get("model"))
                and independence.shares(new_model, roles_map[verifier]["model"], vendor_engine=vendor)
            ]
            if not clashing:
                continue
            for partner_item in pool:
                p_model = partner_item["model"]
                if independence.shares(new_model, p_model, vendor_engine=vendor):
                    continue
                if not all(allowed_for_partner(v_role, p_model) for v_role in clashing):
                    continue
                updates = {role: new_model, **{v_role: p_model for v_role in clashing}}
                if _valid_assignment(state, workspace, settings, updates, seen_signatures):
                    return _build_plan(
                        role, primary_item, [(v_role, partner_item) for v_role in clashing], selection, tier
                    )

        elif role in ("tester", "completion"):
            builder_model = (roles_map.get("builder") or {}).get("model")
            if not builder_model or not independence.shares(builder_model, new_model, vendor_engine=vendor):
                continue
            other_verifier = "completion" if role == "tester" else "tester"
            other_model = (roles_map.get(other_verifier) or {}).get("model")
            for builder_item in pool:
                b_model = builder_item["model"]
                if independence.shares(b_model, new_model, vendor_engine=vendor):
                    continue
                if not allowed_for_partner("builder", b_model):
                    continue
                partners: list[tuple[str, dict]] = [("builder", builder_item)]
                updates = {role: new_model, "builder": b_model}
                if other_model and independence.shares(b_model, other_model, vendor_engine=vendor):
                    if not allowed_for_partner(other_verifier, new_model):
                        continue
                    partners.append((other_verifier, primary_item))
                    updates[other_verifier] = new_model
                if _valid_assignment(state, workspace, settings, updates, seen_signatures):
                    return _build_plan(role, primary_item, partners, selection, tier)

        elif role in ("planner", "plan_reviewer"):
            partner_role = "plan_reviewer" if role == "planner" else "planner"
            if not isinstance(roles_map.get(partner_role), dict):
                continue
            for partner_item in pool:
                p_model = partner_item["model"]
                producer_m, verifier_m = (new_model, p_model) if role == "planner" else (p_model, new_model)
                if independence.shares(producer_m, verifier_m, vendor_engine=vendor):
                    continue
                if not allowed_for_partner(partner_role, p_model):
                    continue
                updates = {role: new_model, partner_role: p_model}
                if _valid_assignment(state, workspace, settings, updates, seen_signatures):
                    return _build_plan(role, primary_item, [(partner_role, partner_item)], selection, tier)

    return None


def choose(
    runtime,
    state: RunState,
    workspace,
    role: str,
    dead: str,
    pause_status: str,
    *,
    worker: dict | None = None,
    workflow_job: bool = False,
    failed_for_role: list[str] | tuple[str, ...] = (),
    offers_fn=None,
    stages_file=None,
) -> dict | None:
    """Choose the next valid route (and any required paired checker swap) for a stopped model."""
    settings = state.get("settings") or {}
    if settings.get("single_model_mode") or settings.get("auto_select_disabled"):
        return None
    if pause_status not in quota_route.STATUSES or not dead:
        return None

    selection = settings.get("route_selection") or {}
    failed_identities = {
        *(model_identity.identity(model) for model in failed_for_role if isinstance(model, str) and model),
        *(model for model in failed_for_role if isinstance(model, str) and model),
    }
    dead_accounts = _quota_dead_accounts(state, settings, role, dead, pause_status)
    seen_signatures = _seen_signatures(state)

    # Tier 1: the run's saved --auto-select permitted set, when present for this role.
    # next_route flattens saved.values() across every role's candidate list while pinning
    # non-stopped roles, so every candidate in the map must pass _candidate_allowed for role.
    if selection.get("candidates") and worker is None and not workflow_job:
        filtered_candidates = {
            cand_role: [
                cand
                for cand in cands
                if _candidate_allowed(
                    cand.get("model", ""),
                    role,
                    stopped_role=role,
                    dead=dead,
                    pause_status=pause_status,
                    settings=settings,
                    failed_identities=failed_identities,
                    dead_accounts=dead_accounts,
                    runtime=runtime,
                    workspace=workspace,
                )
            ]
            for cand_role, cands in selection["candidates"].items()
        }
        current_sel = route_selector.current({**selection, "candidates": filtered_candidates}, settings.get("roles"))
        switched = route_selector.next_route(current_sel, role, dead, failed_models=failed_for_role)
        if switched is not None and _valid_assignment(
            state, workspace, settings, {role: switched["model"]}, seen_signatures
        ):
            item = {
                "model": switched["model"],
                "account": switched.get("account"),
                "billing": _selection_billing(selection, switched["model"]),
                "levels": None,
                "levels_listed": False,
            }
            return _build_plan(role, item, [], selection, "permitted_set", selector_route=switched)

    # Tier 2: models already configured on this run's stage routes.
    configured = _configured_pool(settings, role, selection, worker=worker)
    single_cfg = _search_single(
        configured,
        state=state,
        workspace=workspace,
        settings=settings,
        role=role,
        dead=dead,
        pause_status=pause_status,
        failed_identities=failed_identities,
        dead_accounts=dead_accounts,
        seen_signatures=seen_signatures,
        selection=selection,
        runtime=runtime,
        worker=worker,
        tier="configured_roles",
    )
    if single_cfg is not None:
        return single_cfg

    if worker is None and not workflow_job:
        swap_cfg = _search_paired_swap(
            configured,
            state=state,
            workspace=workspace,
            settings=settings,
            role=role,
            dead=dead,
            pause_status=pause_status,
            failed_identities=failed_identities,
            dead_accounts=dead_accounts,
            seen_signatures=seen_signatures,
            selection=selection,
            runtime=runtime,
            tier="configured_pair_swap",
        )
        if swap_cfg is not None:
            return swap_cfg

    # Tier 3: other connected subscription models on the same backend.
    subscriptions = _subscription_pool(
        runtime, settings, role, workspace, selection, offers_fn=offers_fn, stages_file=stages_file
    )
    if not subscriptions:
        return None

    single_sub = _search_single(
        subscriptions,
        state=state,
        workspace=workspace,
        settings=settings,
        role=role,
        dead=dead,
        pause_status=pause_status,
        failed_identities=failed_identities,
        dead_accounts=dead_accounts,
        seen_signatures=seen_signatures,
        selection=selection,
        runtime=runtime,
        worker=worker,
        tier="connected_subscription",
    )
    if single_sub is not None:
        return single_sub

    if worker is None and not workflow_job:
        combined_by_model: dict[str, dict] = {}
        for item in (*configured, *subscriptions):
            combined_by_model.setdefault(item["model"], item)
        return _search_paired_swap(
            list(combined_by_model.values()),
            state=state,
            workspace=workspace,
            settings=settings,
            role=role,
            dead=dead,
            pause_status=pause_status,
            failed_identities=failed_identities,
            dead_accounts=dead_accounts,
            seen_signatures=seen_signatures,
            selection=selection,
            runtime=runtime,
            tier="subscription_pair_swap",
        )
    return None

"""Runner-owned retry decisions. Models diagnose; configuration selects routes."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_climb_cap as climb_cap
    from . import autocode_evidence_ledger as evidence_ledger
    from . import autocode_route_ladder as route_ladder
    from . import autocode_route_permission as permission
    from . import autocode_run_status as run_status
    from . import autocode_stop as stop
    from . import autocode_util as s
    from . import autocode_verifier_independence as independence
    from .autocode_failure_classification import CLASSES
except ImportError:
    import autocode_climb_cap as climb_cap
    import autocode_evidence_ledger as evidence_ledger
    import autocode_route_ladder as route_ladder
    import autocode_route_permission as permission
    import autocode_run_status as run_status
    import autocode_stop as stop
    import autocode_util as s
    import autocode_verifier_independence as independence
    from autocode_failure_classification import CLASSES

# The stronger attempt is GPT-6 Sol at xhigh: Astra is too expensive and only for the
# Resolver (user 2026-09-28). The default checkers also run GPT-6 Sol, so for the rest of
# that milestone a checker on the strong model moves to checker_model and a different model
# checks the escalated work (user 2026-09-29).
DEFAULTS = {
    "enabled": True,
    "ordinary_retries": 1,
    "strong_model": "openai/gpt-6-sol",
    "strong_reasoning_effort": "xhigh",
    "checker_model": "zai-coding-plan/glm-5.3",
    # Whose stronger route this is: AutoCode's ("runner") is withdrawn when its access check
    # fails (withdraw_unreachable); a --builder-strong-model a person named keeps its access pause.
    "strong_origin": "runner",
}
# The roles that check the Builder's work, and the rule they follow, are
# autocode_verifier_independence's: the dispatch guard pauses on exactly the clashes
# the stronger attempt moves checkers away from (#612).
CHECKERS = independence.verifiers("builder")
# A parallel Builder cannot move the parent's checkers, so when they run its stronger model
# it stops with this result. The parent integrates the other Builders and makes the stronger
# attempt itself, serially, where swap_checkers applies (user 2026-09-29).
SERIAL = "SERIAL_ESCALATION"
SERIAL_USER_REQUEST = "SERIAL_USER_REQUEST"
# Efforts a replacement checker keeps; a climbed xhigh/max rung belongs to the GPT ladder.
CHECKER_EFFORTS = ("low", "medium", "high")
EXHAUSTED = "Implementation remains blocked after the configured retries and the stronger Builder attempt"
CHECKER_STUCK = (
    "Implementation remains blocked after the ordinary retries; the stronger Builder model {model} "
    "would be checked by its own model: {why}"
)
NO_STRONG_MODEL = (
    "Implementation remains blocked after the ordinary retries; this run's provider offers no stronger "
    "Builder model, so the ordinary retries were all this run had"
)
# A new run whose provider did not list a model the stronger attempt needs has none (route_ladder.serve).
NOT_OFFERED = (
    "Implementation remains blocked after the ordinary retries; this run has no stronger Builder attempt "
    "because its provider does not offer {model}"
)
# The stronger attempt needs a model the person left out of the models AutoCode may use (autocode_allowed_models).
NOT_ALLOWED = (
    "Implementation remains blocked after the ordinary retries; this run has no stronger Builder attempt "
    "because it needs {model}, which is not among the models you allow AutoCode to use"
)
# The stronger route AutoCode chose failed its access check before any Builder request (model preflight).
UNREACHABLE = (
    "Implementation remains blocked after the ordinary retries; this run has no stronger Builder attempt "
    "because its account cannot use {model} ({detail}); probe evidence: {evidence}"
)
# What the evidence ledger (autocode_evidence_ledger) adds to a charge's decision, and to its pause (#611).
NOVELTY_NOTES = {
    "repeat": "no new evidence: the same failing checks, criteria, open findings and changed files as an earlier "
    "failure of this milestone, so the plain retry is skipped",
    "progress": "new evidence: every earlier failure of this milestone is resolved and this one fails only on new "
    "items, so it does not count toward the retry limit",
}
REPEATED = (
    " The last failure repeats an earlier one of this milestone with no new evidence: the same failing checks, "
    "criteria, open findings and changed files."
)

# The stronger attempt is the one route climb this policy spends; before it launches it is checked against the
# person's cost cap, active-time budget and milestone budget (#611), so a run that cannot afford it stops with the
# arithmetic instead of climbing and stopping at the budget afterwards.
CLIMB_OVER_CAP = {"attempt": "The Builder's stronger attempt", "basis": "the last Builder attempt"}


def configured(strong_model=None, provider=None, single_model=None):
    """A new run's retry policy. Its checkers move to checker_model, so the strong model cannot be it.

    A configured tool may name its own stronger Builder and checker model ([builder_retry] in its
    config); every other provider uses DEFAULTS. A provider config that lists its models without
    the strong one gets none: a live Claude-provider run escalated its Builder to the OpenAI
    default, which that provider cannot serve, so the launch failed and the run waited on a
    person (2026-09-29).
    """
    declared = getattr(provider, "BUILDER_RETRY", None) or {}
    config = {
        **DEFAULTS,
        **{
            key: declared[name]
            for key, name in (
                ("strong_model", "strong_model"),
                ("checker_model", "checker_model"),
                ("strong_reasoning_effort", "strong_effort"),
            )
            if name in declared
        },
    }
    config["strong_model"] = strong_model or config["strong_model"]
    if strong_model:
        config["strong_origin"] = "user_explicit"
    if single_model:
        # Single-model mode may escalate reasoning effort, but cannot switch
        # to another model or checker route.
        config["strong_model"] = single_model
        config["checker_model"] = single_model
    offered = getattr(provider, "LISTED_MODELS", None)
    if route_ladder.unserved(config["strong_model"], offered):
        if strong_model:
            raise ValueError(
                f"--builder-strong-model {strong_model} is not a {provider.NAME} model: {', '.join(offered)}"
            )
        config["strong_model"] = None
    if (
        not single_model
        and config["strong_model"]
        and independence.may_share(
            config["strong_model"], config["checker_model"], vendor_engine=getattr(provider, "NAME", None) == "claude"
        )
    ):
        # Refused here, at run creation: the same clash found at escalation would pause mid-run.
        raise ValueError(
            f"--builder-strong-model {config['strong_model']} shares a model family with "
            f"{config['checker_model']}, the model the checkers move to when the Builder escalates, "
            "so the escalated work would be checked by its own model; "
            "choose another model"
            + (", or another checker_model under [builder_retry] in the provider config" if declared else "")
        )
    return config


def enabled(state: RunState):
    return state.get("settings", {}).get("builder_retry", {}).get("enabled") is True and not state.get(
        "settings", {}
    ).get("workflow")


def key(state: RunState, task=None):
    task = task if task is not None else state.get("current_task") or {}
    members = task.get("milestone_ids") or [task.get("milestone_id") or task.get("id")]
    return s.digest([cast(dict, state.get("goal_contract", {})).get("hash"), sorted(members)])


def classification_limit(state: RunState):
    """Builder failures one milestone may have classified (#686): as many as its lane takes, the
    one it pauses on included. That is the ordinary retries, the stronger attempt (or one more
    ordinary retry in its place), then the pause, plus each failure that did not count toward them
    (progress, #611; or non-execution plan/operational routing), so classifying never stops a
    milestone sooner."""
    count = ((state.get("settings") or {}).get("builder_retry") or {}).get("ordinary_retries")
    count = count if type(count) is int and 0 <= count <= 3 else cast(int, DEFAULTS["ordinary_retries"])
    ident = key(state)
    current = (state.get("builder_retries") or {}).get(ident) or {}
    uncounted = sum(1 for row in current.get("ledger") or () if row.get("novelty") == "progress")
    non_execution = sum(
        1
        for row in state.get("stuck_investigations") or ()
        if isinstance(row, dict)
        and row.get("trigger") == "builder_failure"
        and row.get("milestone_key") == ident
        and row.get("outcome") == "classified"
        and row.get("failure_class") in ("plan", "operational")
    )
    return count + 2 + uncounted + min(non_execution, 4)


def same_unretried_builder_round(state: RunState, current: dict | None, evidence) -> bool:
    """True when ``evidence`` re-reviews the same Builder output as ``current['failures'][-1]`` without a new Builder attempt."""
    if not isinstance(current, dict) or not current.get("failures") or not isinstance(evidence, str) or not evidence:
        return False
    last_failure = current["failures"][-1]
    if not isinstance(last_failure, str) or not last_failure or last_failure == evidence:
        return False
    stages = state.get("stages") or []
    last_idx = next(
        (
            idx
            for idx in range(len(stages) - 1, -1, -1)
            if isinstance(stages[idx], dict) and stages[idx].get("output") == last_failure
        ),
        None,
    )
    curr_idx = next(
        (
            idx
            for idx in range(len(stages) - 1, -1, -1)
            if isinstance(stages[idx], dict) and stages[idx].get("output") == evidence
        ),
        None,
    )
    if last_idx is None or curr_idx is None or last_idx > curr_idx:
        return False
    src = stages[last_idx].get("source_revision")
    if not isinstance(src, str) or not src or src != stages[curr_idx].get("source_revision"):
        return False
    return not any(
        isinstance(row, dict) and (row.get("original_stage") or row.get("stage")) in ("builder", "orchestrator")
        for row in stages[last_idx + 1 : curr_idx + 1]
    )


def lane(state: RunState):
    ident = key(state)
    lanes = state.setdefault("builder_retries", {})
    previous = lanes.get(state.get("builder_retry_key"))
    if previous and state.get("builder_retry_key") != ident:
        # An operator may pin a new route while a revised contract is reviewed.
        # A prior milestone's retry baseline must never undo that instruction.
        roles = state["settings"]["roles"]
        for role, route in {"builder": previous["initial_route"], **previous.get("checker_routes", {})}.items():
            if not roles.get(role, {}).get("model_pinned"):
                roles[role] = copy.deepcopy(route)
            state.setdefault("sessions", {}).pop(role, None)
    state["builder_retry_key"] = ident
    return lanes.setdefault(
        ident, {"initial_route": copy.deepcopy(state["settings"]["roles"]["builder"]), "failures": [], "action": None}
    )


def escalation_routes(state: RunState) -> list[dict]:
    """The Builder lane's own routes for this milestone, as ``{role: (model, effort)}`` maps.

    First its routes before it escalated (the Builder's initial route and each checker it moved),
    then the routes each recorded escalation set. An escalation is AutoCode's answer to this
    milestone's own failures, not a new setting, so a binding over the run's settings taken on one
    of them holds on another (``autocode_resolver_recovery._holds``). Empty without a lane.
    """
    current = (state.get("builder_retries") or {}).get(key(state))
    if not isinstance(current, dict) or not isinstance(current.get("initial_route"), dict):
        return []
    checkers = {role: route for role, route in (current.get("checker_routes") or {}).items() if isinstance(route, dict)}

    def pair(route):
        return route.get("model"), route.get("reasoning_effort")

    result = [{"builder": pair(current["initial_route"]), **{role: pair(route) for role, route in checkers.items()}}]
    for decision in state.get("builder_retry_decisions") or ():
        if (
            not isinstance(decision, dict)
            or decision.get("action") != "escalate"
            or decision.get("milestone_key") != key(state)
        ):
            continue
        moved = {"builder": (decision.get("selected_model"), decision.get("selected_effort"))}
        for role, route in checkers.items():
            effort = route.get("reasoning_effort")
            replacement = (decision.get("checker_models") or {}).get(role)
            moved[role] = (replacement, effort if effort in CHECKER_EFFORTS else "high") if replacement else pair(route)
        if moved not in result:
            result.append(moved)
    return result


def withdraw_unreachable(state: RunState, model, *, detail, evidence) -> bool:
    """Undo the Builder's escalation to ``model`` when its access check failed; False leaves it.

    Only AutoCode's own choice of stronger route is undone (``strong_origin`` "runner"): a
    ``--builder-strong-model`` a person named stays theirs, keeping its access pause, and so does a
    route outside the lane. A run saved before ``strong_origin`` existed counts as AutoCode's choice
    only on the default stronger model. The
    Builder and each checker it moved return to their routes before the escalation, and the lane
    decides as for a stronger model the provider does not offer: the ordinary retries were all this
    run had. The decision and the probe evidence are recorded; nothing asks a person about a login
    for a route they never chose.
    """
    if not enabled(state):
        return False
    config = state["settings"].get("builder_retry") or {}
    current = (state.get("builder_retries") or {}).get(key(state))
    route = (state["settings"].get("roles") or {}).get("builder") or {}
    origin = config.get("strong_origin")
    if (
        not isinstance(current, dict)
        or current.get("action") != "escalate"
        or not isinstance(current.get("initial_route"), dict)
        or route.get("model") != model
        or current["initial_route"].get("model") == model
        or origin not in ("runner", None)
        or (
            origin is None
            and route_ladder.normalize_model(config.get("strong_model") or "")
            != route_ladder.normalize_model(DEFAULTS["strong_model"])
        )
    ):
        return False
    initial = current["initial_route"]
    route.update(model=initial.get("model"), reasoning_effort=initial.get("reasoning_effort"))
    for role, saved in (current.get("checker_routes") or {}).items():
        checker = state["settings"]["roles"].get(role)
        if isinstance(checker, dict) and isinstance(saved, dict):
            checker.update(model=saved.get("model"), reasoning_effort=saved.get("reasoning_effort"))
            state.setdefault("sessions", {}).pop(role, None)
    current["strong_unreachable"] = {"model": model, "detail": detail, "evidence": evidence}
    _decide(
        state,
        current,
        "pause",
        current["failures"][-1] if current.get("failures") else None,
        f"The stronger Builder route {model} failed its access check; restored the Builder's route",
        {},
        UNREACHABLE.format(model=model, detail=detail, evidence=evidence),
    )
    return True


def guard(state: RunState):
    if not enabled(state):
        return
    current = lane(state)
    if current["action"] == "defer":
        if state.get("parent_run"):
            raise s.Paused(SERIAL, "This Builder left its stronger attempt to the parent run, which makes it serially")
        _take_deferred(state, current)
    if current["action"] == "pause":
        raise s.Paused(
            "PAUSED_BUILDER_RETRY_LIMIT",
            "Execution failure: Builder retry/escalation exhausted for this approved milestone; "
            "replan or change policy explicitly before another Builder attempt",
        )
    if current["action"] == "escalate":
        _climb_within_cap(state)


def _climb_within_cap(state: RunState) -> None:
    """Pause before the stronger attempt launches when it is projected past the cost cap, the active-time budget
    you set or the milestone budget you set (#611).

    The projections are the last Builder attempt's tokens at the stronger route's price and its duration, the
    latter added to the run's and to the milestone's active time (autocode_climb_cap.within_budgets, as a writer).
    Once an attempt has launched on that route the stage-boundary checks govern it. No cap or budget, no earlier
    attempt, or a model or attempt without a price or duration never pauses here, as an unpriced stage never trips
    the cap. A parallel Builder sees only its own spend and time, not its siblings', so it leaves the check to the
    serial run.
    """
    if state.get("parent_run"):
        return
    attempts = [
        row
        for row in state.get("stages") or ()
        if isinstance(row, dict)
        and row.get("stage") == "builder"
        and not row.get("report_only")
        and not row.get("dry_run")
    ]
    route = state["settings"]["roles"]["builder"]
    launched = (attempts[-1].get("launch_route") or {}) if attempts else {}
    if not attempts or all(launched.get(key) == route.get(key) for key in ("model", "reasoning_effort")):
        return
    climb_cap.within_budgets(
        state,
        attempts[-1],
        **CLIMB_OVER_CAP,
        model=route.get("model"),
        effort=route.get("reasoning_effort"),
        writer=True,
    )


def failed_before(state: RunState, milestone_id):
    """Whether a Builder already failed this milestone; the rest of its attempts are serial."""
    current = state.get("builder_retries", {}).get(key(state, {"milestone_id": milestone_id}))
    return bool(current and current["failures"])


def adopt(state: RunState, worker):
    """Carry a parallel worker's deferred retry lane into the parent run.

    Its failures stay counted, so the parent makes only the stronger attempt the worker
    left to it, and a restart cannot add another. The parent's own lane for the milestone
    has no failures (select() never parallelizes one that has), so it is replaced.
    """
    ident = worker.get("builder_retry_key")
    deferred = worker.get("builder_retries", {}).get(ident) or {}
    if deferred.get("action") != "defer" or ident != key(state, worker.get("current_task")):
        raise s.Paused(
            "PAUSED_ORCHESTRATOR_WORKER", "Builder deferred its stronger attempt without a saved retry decision"
        )
    lanes = state.setdefault("builder_retries", {})
    if not lanes.get(ident, {}).get("failures"):
        lanes[ident] = copy.deepcopy(deferred)
    decisions = state.setdefault("builder_retry_decisions", [])
    for decision in worker.get("builder_retry_decisions", []):
        if decision not in decisions:
            decisions.append(copy.deepcopy(decision))


def _take_deferred(state: RunState, current):
    """Make the stronger attempt a parallel worker deferred, now that this run builds serially."""
    config = state["settings"]["builder_retry"]
    route = state["settings"]["roles"]["builder"]
    # The route to restore after this milestone is the one this run has now, not the worker's copy.
    current["initial_route"] = copy.deepcopy(route)
    action, checkers, stop_reason = _escalate(state, current, config, route)
    _decide(
        state,
        current,
        action,
        current["failures"][-1],
        "Stronger attempt deferred by a parallel Builder; making it serially",
        checkers,
        stop_reason,
    )


def _tool_route(state: RunState, route):
    """A route a configured command tool serves (settings.provider names its config).

    Such a tool spells its models itself (autocode_configure._provider_model): no openai/ alias
    is added, and a bare name is the tool's model, not a native Codex one.
    """
    settings = state["settings"]
    return (
        settings.get("provider") not in (None, "opencode") and route.get("engine", settings.get("engine")) == "opencode"
    )


def _movable(state: RunState, route):
    # One rule with the run-creation check of the checker model (route_ladder.serve).
    return route_ladder.movable(state["settings"], route)


def _tool_swap_problem(state: RunState, config, model):
    """Why a configured tool's checkers cannot all move off the strong model, or None.

    AutoCode cannot ask the tool what it serves here, so a checker moves only when the cross-model
    guard would refuse it beside the strong model (that model in the tool's own spelling, or another
    model of its GLM or MiMo family), is neither pinned nor on another provider, and its replacement
    is a different model this run already routes a role to. A checker that may only name the strong
    model another way (openai/X for X) stops the escalation. The reason names the jobs as the screen
    does (autocode_roles), in prose.
    """
    roles = state["settings"]["roles"]
    vendor = independence.same_vendor_engine(state["settings"])
    replacement = config.get("checker_model") or DEFAULTS["checker_model"]
    routed = {route.get("model") for route in roles.values() if isinstance(route, dict)}
    for role in colliding_checkers(state, model):
        route = roles[role]
        if not independence.shares(model, route.get("model"), vendor_engine=vendor):
            return f"a Tester or Completion Reviewer route names it {route.get('model')}"
        if not _movable(state, route):
            return "a Tester or Completion Reviewer on it is pinned or uses another provider"
        if independence.may_share(model, replacement, vendor_engine=vendor):
            return f"the checkers would move to {replacement}, the stronger model or its family"
        if replacement not in routed:
            return f"the checkers would move to {replacement}, which no role of this run uses"
    return None


def colliding_checkers(state: RunState, model):
    """Checker roles the dispatch guard would refuse with the given Builder model, or that may run it."""
    roles = state["settings"]["roles"]
    vendor = independence.same_vendor_engine(state["settings"])
    return [
        role
        for role in CHECKERS
        if isinstance(roles.get(role), dict)
        and independence.may_share(model, roles[role].get("model"), vendor_engine=vendor)
    ]


def swap_checkers(state: RunState, current, config, model):
    """Move checkers off the escalated Builder's model so it never checks its own work.

    The replaced routes are kept on the lane and restored at the next milestone. Pinned,
    custom-provider and bare-name (Codex engine) checkers are left alone; the dispatch
    cross-model guard still pauses if one of them collides.
    """
    # Runs saved before checker_model existed use the default rather than check their own work.
    replacement = config.get("checker_model") or DEFAULTS["checker_model"]
    swapped = {}
    vendor = independence.same_vendor_engine(state["settings"])
    for role in (
        colliding_checkers(state, model) if not independence.may_share(model, replacement, vendor_engine=vendor) else ()
    ):
        route = state["settings"]["roles"][role]
        if not _movable(state, route):
            continue
        current.setdefault("checker_routes", {}).setdefault(role, copy.deepcopy(route))
        effort = route.get("reasoning_effort")
        route.update(model=replacement, reasoning_effort=effort if effort in CHECKER_EFFORTS else "high")
        state.setdefault("sessions", {}).pop(role, None)
        swapped[role] = replacement
    return swapped


def _escalate(state: RunState, current, config, route):
    """Move the Builder to the strong model, or say why this run cannot: (action, checkers, stop reason)."""
    unreachable = current.get("strong_unreachable") or {}
    if unreachable:
        return "pause", {}, UNREACHABLE.format(**unreachable)
    if not config.get("strong_model"):
        if config.get("not_allowed"):
            return "pause", {}, NOT_ALLOWED.format(model=config["not_allowed"])
        missing = config.get("not_offered")
        return "pause", {}, NOT_OFFERED.format(model=missing) if missing else NO_STRONG_MODEL
    engine = route.get("engine", state["settings"].get("engine"))
    # A configured tool whose Builder has a bare model name spells its models itself; a tool
    # that spells them provider/model keeps the configured spelling, alias included.
    tool = _tool_route(state, route) and "/" not in str(current["initial_route"].get("model"))
    model = (
        config["strong_model"]
        if tool
        else route_ladder.format_model(route_ladder.normalize_model(config["strong_model"]), engine)
    )
    # Never undo explicit pins or silently change provider/transport.
    if route.get("model_pinned") or route.get("provider") not in (None, "openai"):
        return "pause", {}, EXHAUSTED
    if state.get("parent_run") and colliding_checkers(state, model):
        # The parent run checks this batch with checkers this worker cannot move; the
        # strong model would check its own work. The parent makes the attempt serially.
        return (
            "defer",
            {},
            (
                f"Parallel Builder needs the stronger model {model}, which also checks this batch; "
                "the parent run makes that attempt serially after integrating the other Builders"
            ),
        )
    # Such a tool's Builder escalates only when every checker on the strong model can move off it;
    # otherwise nothing changes and the run pauses here, before any route is rewritten.
    problem = tool and _tool_swap_problem(state, config, model)
    if problem:
        return "pause", {}, CHECKER_STUCK.format(model=model, why=problem)
    if not permission.allows(state["settings"], "builder", model, config["strong_reasoning_effort"]):
        return "pause", {}, "The stronger Builder route is outside this run's saved accounts or supported levels"
    replacement = config.get("checker_model") or DEFAULTS["checker_model"]
    for checker in colliding_checkers(state, model):
        checker_route = state["settings"]["roles"][checker]
        effort = checker_route.get("reasoning_effort")
        effort = effort if effort in CHECKER_EFFORTS else "high"
        if not permission.allows(state["settings"], checker, replacement, effort):
            return "pause", {}, "The stronger Builder attempt needs a checker outside this run's saved routes"
    route.update(model=model, reasoning_effort=config["strong_reasoning_effort"])
    return "escalate", swap_checkers(state, current, config, model), None


def _decide(state: RunState, current, action, evidence, reason, checkers, stop_reason, novelty=None):
    route = state["settings"]["roles"]["builder"]
    current["action"] = action
    state.setdefault("sessions", {}).pop("builder", None)
    state.setdefault("builder_retry_decisions", []).append(
        {
            "at": s.now(),
            "owner": "autoresolver",
            "action": action,
            "failure": evidence,
            "failure_class": "execution",
            "reason": reason,
            "milestone_key": key(state),
            "attempt": len(current["failures"]),
            "selected_model": route["model"],
            "selected_effort": route.get("reasoning_effort"),
            **({"checker_models": checkers} if checkers else {}),
            **({"novelty": novelty} if novelty else {}),
        }
    )
    if action == "pause":
        route_ladder.record_outcome(state, "paused")
        row = (current.get("ledger") or [{}])[-1]
        stop.end(
            state,
            stop_reason,
            {
                "milestone_key": key(state),
                "stage": "builder",
                "failure": evidence,
                "attempt": len(current["failures"]),
                **({"novelty": novelty} if novelty else {}),
                "failing_checks": row.get("failing") or [],
                "changed_files": row.get("changed") or [],
            },
            at=s.now(),
        )
    elif action == "defer":
        state.update({"status": SERIAL, "stop_reason": "Execution failure: " + stop_reason})


def failure(state: RunState, evidence, reason, *, classification="execution"):
    """One charge per execution failure; replay observes the lane's current gate.

    The evidence ledger (autocode_evidence_ledger) judges each execution failure against the milestone's
    earlier ones (#611): a repeat goes straight to the stronger attempt, or pauses once it is spent, and
    progress retries without counting toward the limit. Every failure stays in the lane's `failures`.

    Nonexecution actions are routing observations, not authority to replay a stage;
    the controller validates their bound continuation before dispatch.
    """
    if not isinstance(classification, str) or classification not in CLASSES:
        raise ValueError("Unknown Builder failure class")
    if classification != "execution":
        # The controller consumes these decisions before assignment. They are not
        # execution attempts: keep routes, sessions and the existing retry lane intact.
        ident = key(state)
        decisions = state.setdefault("builder_retry_decisions", [])
        previous = next(
            (
                row
                for row in reversed(decisions)
                if row.get("milestone_key") == ident
                and row.get("failure") == evidence
                and row.get("failure_class") == classification
            ),
            None,
        )
        if previous is not None:
            return previous["action"]
        action = {"plan": "replan", "operational": "recover", "unknown": "investigate"}[classification]
        route = state["settings"]["roles"]["builder"]
        current = state.get("builder_retries", {}).get(ident) or {}
        decisions.append(
            {
                "at": s.now(),
                "owner": "runner",
                "action": action,
                "failure": evidence,
                "failure_class": classification,
                "reason": f"{classification.capitalize()} failure: {reason}; next action: {action}",
                "milestone_key": ident,
                "attempt": len(current.get("failures", [])),
                "selected_model": route["model"],
                "selected_effort": route.get("reasoning_effort"),
            }
        )
        return action
    if not enabled(state):
        run_status.pause(state, "PAUSED_NO_PROGRESS", reason)
        return "pause"
    config = state["settings"]["builder_retry"]
    count = config["ordinary_retries"]
    if type(count) is not int or not 0 <= count <= 3:
        raise ValueError("builder_retry.ordinary_retries must be between 0 and 3")
    current = lane(state)
    rows = reconciled_ledger_rows(state, current)
    seen = observe_state(state)
    novelty = evidence_ledger.assess(rows, evidence, seen)
    if novelty == "replay" or (current.get("action") and same_unretried_builder_round(state, current, evidence)):
        return current["action"]
    # A lane saved before the ledger counted every failure it holds.
    position = current.get("position", len(current["failures"]))
    current["failures"].append(evidence)
    current.setdefault("ledger", []).append(
        {
            "evidence": evidence,
            "signature": evidence_ledger.signature(seen),
            "failing": seen["failing"],
            "changed": seen["changed"],
            "novelty": novelty,
        }
    )
    # With no stronger model the escalation slot is spent as one more ordinary retry: a live Claude run
    # paused on its second failure with correct code and two tests left to strengthen (2026-09-30).
    count += 0 if config["strong_model"] else 1
    # A repeat skips the plain retries for the stronger attempt; progress does not move the lane (#611).
    if novelty == "repeat":
        position = max(position + 1, count + 1)
    elif novelty == "new":
        position += 1
    current["position"] = position
    action = "retry" if position <= count else "escalate" if position == count + 1 else "pause"
    if novelty == "progress":
        # Another attempt on the route the lane has now; a pause or a deferred attempt stays as it is.
        action = current["action"] if current["action"] in ("pause", "defer") else "retry"
    checkers, stop_reason = {}, EXHAUSTED
    if action == "escalate":
        action, checkers, stop_reason = _escalate(state, current, config, state["settings"]["roles"]["builder"])
    if novelty == "repeat" and action == "pause":
        stop_reason = stop_reason.rstrip(".") + "." + REPEATED  # two sentences, not one run-on
    if novelty in NOVELTY_NOTES:
        reason = f"{reason}; {NOVELTY_NOTES[novelty]}"
    _decide(state, current, action, evidence, reason, checkers, stop_reason, novelty)
    return action


def _changed_code_for_state(state: RunState, override=None) -> dict | None:
    if isinstance(override, dict):
        return override
    proof = state.get("regression_proof")
    if not isinstance(proof, dict) or proof.get("verdict") != "PASS":
        return None
    validation = state.get("validation")
    if (
        isinstance(validation, dict)
        and validation.get("source_revision")
        and proof.get("source_revision")
        and validation["source_revision"] != proof["source_revision"]
    ):
        return None
    changed_code = proof.get("changed_code_evidence")
    return changed_code if isinstance(changed_code, dict) else None


def observe_state(state: RunState, changed_code_evidence=None) -> dict:
    """Observe the current milestone failure evidence including passing-proof changed-line obligations."""
    return evidence_ledger.observe(
        state.get("validation"),
        state.get("findings_ledger"),
        state.get("changed_files"),
        _changed_code_for_state(state, changed_code_evidence),
    )


def reconciled_ledger_rows(state: RunState, current: dict, *, upto: int | None = None) -> list[dict]:
    """Read earlier ledger rows, restoring changed-line obligations omitted by pre-fix saves."""
    reworks = {
        row.get("review_output"): row.get("assessment")
        for row in state.get("regression_proofs") or ()
        if isinstance(row, dict)
        and row.get("kind") == "changed_code_rework"
        and isinstance(row.get("assessment"), dict)
    }
    recorded = {row.get("evidence"): row for row in current.get("ledger") or () if isinstance(row, dict)}
    failures = list(current.get("failures") or ())
    if upto is not None:
        failures = failures[:upto]
    rows = []
    for earlier in failures:
        saved = recorded.get(earlier, {"evidence": earlier})
        assessment = reworks.get(earlier)
        if isinstance(assessment, dict) and "signature" in saved:
            cc_seen = evidence_ledger.observe(None, None, saved.get("changed"), assessment)
            merged_failing = sorted(set(saved.get("failing") or ()) | set(cc_seen["failing"]))
            if merged_failing != list(saved.get("failing") or ()):
                saved = {
                    **saved,
                    "failing": merged_failing,
                    "signature": evidence_ledger.signature(
                        {"failing": merged_failing, "changed": list(saved.get("changed") or ())}
                    ),
                }
        rows.append(saved)
    return rows


def execution_exhausted(state: RunState, evidence, *, changed_code_evidence=None) -> bool:
    """True when an execution charge for ``evidence`` would immediately pause at the ladder's end."""
    if not enabled(state):
        return True
    config = state["settings"]["builder_retry"]
    count = config["ordinary_retries"]
    if type(count) is not int or not 0 <= count <= 3:
        return False
    current = (state.get("builder_retries") or {}).get(key(state))
    if not isinstance(current, dict) or not current.get("failures"):
        return False
    rows = reconciled_ledger_rows(state, current)
    seen = observe_state(state, changed_code_evidence)
    novelty = evidence_ledger.assess(rows, evidence, seen)
    if novelty in ("replay", "progress"):
        return False
    limit = count + (0 if config["strong_model"] else 1)
    position = current.get("position", len(current["failures"]))
    next_pos = max(position + 1, limit + 1) if novelty == "repeat" else position + 1
    return next_pos > limit + 1


def reconcile_false_progress_stop(state: RunState, failure_output: str) -> bool:
    """Retire a pre-fix runner could_not_finish stop recorded with failing_checks=[] on genuine progress."""
    stops = [row for row in state.get("applied_interventions") or () if stop.is_stop_receipt(row)]
    if len(stops) != 1:
        return False
    receipt = stops[0]
    outcome = receipt.get("outcome") if receipt.get("actor") == stop.RUNNER_ACTOR else None
    ev_meta = outcome.get("evidence") if isinstance(outcome, dict) else None
    milestone_key = key(state)
    current = (state.get("builder_retries") or {}).get(milestone_key)
    if (
        not isinstance(outcome, dict)
        or outcome.get("result") != "could_not_finish"
        or not isinstance(ev_meta, dict)
        or ev_meta.get("stage") != "builder"
        or ev_meta.get("failure") != failure_output
        or ev_meta.get("milestone_key") != milestone_key
        or ev_meta.get("failing_checks") != []
        or ev_meta.get("novelty") != "new"
        or not isinstance(current, dict)
        or current.get("action") != "pause"
        or (current.get("failures") or [])[-1:] != [failure_output]
    ):
        return False
    prior_rows = reconciled_ledger_rows(state, current, upto=len(current["failures"]) - 1)
    seen = observe_state(state)
    if evidence_ledger.assess(prior_rows, failure_output, seen) != "progress":
        return False
    state["applied_interventions"] = [row for row in state.get("applied_interventions") or () if row is not receipt]
    if (state.get("stop_intent") or {}).get("request_ids") == [receipt.get("id")]:
        state.pop("stop_intent", None)
    updated_ledger = [copy.deepcopy(row) for row in prior_rows]
    updated_ledger.append(
        {
            "evidence": failure_output,
            "signature": evidence_ledger.signature(seen),
            "failing": seen["failing"],
            "changed": seen["changed"],
            "novelty": "progress",
        }
    )
    current["ledger"] = updated_ledger
    current["position"] = max(0, current.get("position", len(current["failures"])) - 1)
    current["action"] = "retry"
    decisions = state.get("builder_retry_decisions") or []
    for row in decisions:
        if isinstance(row, dict) and row.get("milestone_key") == milestone_key and row.get("outcome") == "paused":
            row.pop("outcome", None)
    if (
        decisions
        and isinstance(decisions[-1], dict)
        and decisions[-1].get("action") == "pause"
        and decisions[-1].get("failure") == failure_output
        and decisions[-1].get("milestone_key") == milestone_key
    ):
        decisions[-1]["action"] = "retry"
        decisions[-1]["novelty"] = "progress"
        note = NOVELTY_NOTES["progress"]
        if note not in str(decisions[-1].get("reason") or ""):
            decisions[-1]["reason"] = f"{decisions[-1].get('reason', '')}; {note}"
    assigned_at = str((state.get("current_task") or {}).get("assigned_at") or "")
    for event in state.get("reasoning_escalations") or ():
        if (
            isinstance(event, dict)
            and event.get("outcome") == "paused"
            and (not assigned_at or str(event.get("at") or "") >= assigned_at)
        ):
            event.pop("outcome", None)
    return True

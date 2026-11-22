"""Whether a bug fix's milestone checkpoint gives its regression proof anything that can fail first (#3962).

A bug fix owes a fail-first proof at every checkpoint (autocode_regression.required): a test that fails on the
original code and passes with the change. The one exception is coverage (``coverage_allowed``, the bug-fix branch
of autocode_regression.preserve_only): an approved, coordinated member whose due cases are all guards. An approved
bug-fix plan once began with M0, a survey milestone holding one guard criterion. The Builder delivered it, the
guard passed on the original code and with the change, and the proof failed with an empty fail_to_pass after a
Builder round, a proof and a Tester round. The Resolver then asked the person to fold M0 into M1.

The plan check and the gate disagreed about one fact. They now read this module: the gate through
``coverage_allowed`` and the plan check through ``unprovable``, which asks, for each milestone checkpoint, what the
gate's own case selection (autocode_test_cases.proof_cases and in_scope) makes due there, with the milestone's
transitive prerequisites accepted, and whether one of those cases is a ``test:`` case. A checkpoint that owes a
fail-first proof and has none is unprovable whatever its shape: guard-only, a command guard, doc-only. Two
checkpoints are left to the gate, which decides them from the diff:

- the checkpoint at which every criterion is due (a one-milestone plan, or the milestone every other one is a
  prerequisite of): an unloadable-code repair (autocode_first_suite) or an unnamed fail-first test may still pass;
- a plan whose every path is a test file (autocode_test_support.planned): its tests that already fail are the proof.

``correct`` merges each unprovable milestone into the next milestone carrying a ``test:`` criterion (a merged
checkpoint holds a superset of the criteria and paths, so nothing is lost), and refuses with the correction where
that is not safe. autocode_goal_lifecycle applies it to Planner drafts at install and refuses an unprovable plan
in validate_body, so neither the Plan Reviewer nor the person can approve one; autocode_report_check exposes the
same refusal to shell-capable Planners. ``send_back`` repairs a plan approved before this check, before a
non-planning stage launches, with no person question.

Pure apart from ``coordinated`` (it reads the changeset store) and ``send_back`` (it installs a draft through the
``install`` its caller passes, as goal_lifecycle imports this module).
State: ``fail_first_corrections`` (each merge at install and each saved-plan send-back, with its contract revision).
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as contracts
    from . import autocode_progressive_state as progressive_state
    from . import autocode_project_kinds as project_kinds
    from . import autocode_run_status as run_status
    from . import autocode_test_cases as test_cases
    from . import autocode_test_support as test_support
    from . import autocode_util as util
except ImportError:
    import autocode_contract_identity as contracts
    import autocode_progressive_state as progressive_state
    import autocode_project_kinds as project_kinds
    import autocode_run_status as run_status
    import autocode_test_cases as test_cases
    import autocode_test_support as test_support
    import autocode_util as util

# A runner-built draft (autocode_goal_lifecycle.UNAUTHORED_DRAFTS); plan review and approval still follow.
ORIGIN = "fail_first_correction"
KEY = "fail_first_corrections"  # spelled out where the state is typed (autocode_run_state)
PHRASE = "nothing that can fail first"
SETTLED_AS = "fail_first"
SPEC_KEY = "fail_first"  # report-check's spec: {"coordinated": bool} for planning stages
_CHECKPOINT_HASH = "fail-first-checkpoint"


def coordinated(state: RunState) -> bool:
    """Whether the run is a bound automatic member of a coordinated change, whose joint gate proves the bug."""
    try:
        from . import autocode_coordination_context as coordination
    except ImportError:
        import autocode_coordination_context as coordination
    return bool(coordination.context(state))


def coverage_allowed(task_kind, coordinated, due) -> bool:
    """Whether a bug fix's checkpoint may be proven as coverage: its due cases are all guards and the run is a
    coordinated member. ``coordinated`` is a bool or a call that answers it, made only when the cases qualify."""
    if task_kind != "bugfix" or not due or not all(case.get("kind") == "preserve" for case in due):
        return False
    return bool(coordinated() if callable(coordinated) else coordinated)


def owes_fail_first(task_kind, coordinated, due) -> bool:
    """Whether a checkpoint's proof must show a test that fails on the original code and passes with the change."""
    return task_kind == "bugfix" and not coverage_allowed(task_kind, coordinated, due)


def _milestones(body) -> list[dict]:
    rows = body.get("milestones") if isinstance(body, dict) else None
    return [row for row in rows or [] if isinstance(row, dict) and isinstance(row.get("id"), str)]


def _depends(row) -> list[str]:
    return [str(mid) for mid in row.get("depends_on") or [] if isinstance(mid, str)]


def _prerequisites(body, milestone_id) -> set[str]:
    """The milestones ``milestone_id`` depends on, directly or through others (unknown ids and cycles ignored)."""
    graph = {row["id"]: _depends(row) for row in _milestones(body)}
    found: set[str] = set()
    pending = list(graph.get(milestone_id, []))
    while pending:
        mid = pending.pop()
        if mid in found or mid not in graph or mid == milestone_id:
            continue
        found.add(mid)
        pending.extend(graph[mid])
    return found


def _checkpoint(state: RunState, body, milestone_id) -> RunState:
    """The run as the gate sees it at ``milestone_id``'s checkpoint: the plan approved, that milestone current
    and its prerequisites accepted. A shallow view; the gate's case selection only reads it."""
    view: dict = dict(state)
    view["goal_contract"] = {"body": body, "approval_status": "approved", "hash": _CHECKPOINT_HASH}
    view["current_task"] = {"milestone_id": milestone_id}
    view["milestone_progress"] = {
        mid: {"id": mid, "accepted": True, "contract_hash": _CHECKPOINT_HASH}
        for mid in _prerequisites(body, milestone_id)
    }
    return cast("RunState", view)


def checkpoint_cases(state: RunState, body, milestone_id) -> list[dict] | None:
    """The cases the regression proof requires at ``milestone_id``'s checkpoint, or None when every criterion is
    due there (the gate decides that checkpoint from the diff)."""
    view = _checkpoint(state, body, milestone_id)
    if test_cases.in_scope(view) is None:
        return None
    return test_cases.proof_cases(view)


def _is_test(path) -> bool:
    return project_kinds.is_test_path(str(path).removeprefix("./"))


def unprovable(state: RunState, body, *, coordinated_run=None) -> list[str]:
    """The milestones, in plan order, whose checkpoint owes a fail-first proof that its due cases cannot supply.

    ``coordinated_run`` is the run's coordination when the caller already knows it (report-check's spec), else
    it is read from ``state`` only when a checkpoint's cases are all guards."""
    if not isinstance(body, dict) or body.get("task_kind") != "bugfix":
        return []
    milestones = _milestones(body)
    if len(milestones) < 2:
        return []
    if test_support.planned({"approval_status": "approved", "body": body}, _is_test):
        return []
    answer: list[bool] = []

    def member() -> bool:
        if coordinated_run is not None:
            return bool(coordinated_run)
        if not answer:
            answer.append(coordinated(state))
        return answer[0]

    found = []
    for row in milestones:
        due = checkpoint_cases(state, body, row["id"])
        if due is None or not owes_fail_first("bugfix", member, due):
            continue
        if not any(case.get("kind") != "preserve" for case in due):
            found.append(row["id"])
    return found


def refusal(milestone_id, why="") -> str:
    """The correction for an unprovable milestone, as the Planner and report-check read it."""
    return (
        f"Milestone {milestone_id} gives its checkpoint's regression proof {PHRASE}: in a bug fix every milestone "
        "checkpoint must show a test that fails on the original code and passes with the change, and the criteria "
        f"due at {milestone_id} (its own and those of the milestones it depends on) name no test: criterion"
        + (f" ({why})" if why else "")
        + f". Merge {milestone_id} into a milestone that has a test: criterion, or plan the work as one milestone."
    )


def _restoring(body) -> set[str]:
    """The criteria a ``test:`` mark makes the proof show failing first."""
    return {case["id"] for case in test_cases.plan_cases(body) if case.get("kind") != "preserve"}


def _target(body, milestone_id) -> str | None:
    """The milestone to merge ``milestone_id`` into: the first, in plan order, of those that depend on it and
    then of those after it, whose own criteria include a ``test:`` criterion."""
    milestones = _milestones(body)
    restoring = _restoring(body)
    ids = [row["id"] for row in milestones]
    position = ids.index(milestone_id)
    carries = [
        row for row in milestones if row["id"] != milestone_id and set(row.get("acceptance_criteria") or []) & restoring
    ]
    dependents = [row["id"] for row in carries if milestone_id in _depends(row)]
    later = [row["id"] for row in carries if ids.index(row["id"]) > position]
    return (dependents or later or [None])[0]


def _unique(values) -> list:
    return list(dict.fromkeys(values))


def _cyclic(body) -> bool:
    graph = {row["id"]: _depends(row) for row in _milestones(body)}
    visiting, visited = set(), set()

    def visit(mid) -> bool:
        if mid in visiting:
            return True
        if mid in visited or mid not in graph:
            return False
        visiting.add(mid)
        if any(visit(dependency) for dependency in graph[mid]):
            return True
        visiting.remove(mid)
        visited.add(mid)
        return False

    return any(visit(mid) for mid in graph)


def _merge(body, milestone_id, target_id) -> dict:
    """Merge ``milestone_id`` into ``target_id`` in place and return the record of it."""
    milestones = _milestones(body)
    merged = next(row for row in milestones if row["id"] == milestone_id)
    target = next(row for row in milestones if row["id"] == target_id)
    own = list(merged.get("acceptance_criteria") or [])
    target["acceptance_criteria"] = _unique([*own, *(target.get("acceptance_criteria") or [])])
    target["affected_paths"] = _unique([*(merged.get("affected_paths") or []), *(target.get("affected_paths") or [])])
    target["depends_on"] = [
        mid for mid in _unique([*_depends(merged), *_depends(target)]) if mid not in (milestone_id, target_id)
    ]
    target["objective"] = "; ".join(
        text
        for text in (str(merged.get("objective") or "").strip(), str(target.get("objective") or "").strip())
        if text
    )
    body["milestones"] = [row for row in body.get("milestones") or [] if row is not merged]
    for row in _milestones(body):
        if row is not target and milestone_id in _depends(row):
            row["depends_on"] = [
                mid
                for mid in _unique(target_id if mid == milestone_id else mid for mid in _depends(row))
                if mid != row["id"]
            ]
    first = body.get("initial_task")
    if isinstance(first, dict) and first.get("milestone_id") == milestone_id:
        first.update(
            kind="implement",
            milestone_id=target_id,
            objective=target["objective"],
            acceptance_criteria=list(target["acceptance_criteria"]),
            affected_paths=list(target["affected_paths"]),
        )
    return {"milestone": milestone_id, "into": target_id, "criteria": own}


def correct(state: RunState, body, *, coordinated_run=None):
    """``body`` with each unprovable milestone merged into the next milestone with a ``test:`` criterion, and the
    merges made, as (body, merges); ``body`` itself when there is none. A ValueError with the correction when a
    merge is not safe: no milestone to merge into, a dependency cycle, an initial task left on a milestone that
    still has prerequisites, or a progressive plan, whose slices the Planner sizes."""
    found = unprovable(state, body, coordinated_run=coordinated_run)
    if not found or _cyclic(body):
        return body, []  # a cycle the Planner drew is validate_body's own refusal
    if progressive_state.enabled(_checkpoint(state, body, found[0])):
        raise ValueError(refusal(found[0], "a progressive plan is not merged by the runner"))
    corrected, merges = copy.deepcopy(body), []
    while found:
        milestone_id = found[0]
        target_id = _target(corrected, milestone_id)
        if target_id is None:
            raise ValueError(refusal(milestone_id, "no other milestone has a test: criterion to merge it into"))
        merges.append(_merge(corrected, milestone_id, target_id))
        if _cyclic(corrected):
            raise ValueError(refusal(milestone_id, f"merging it into {target_id} would make the dependencies a cycle"))
        first = corrected.get("initial_task")
        graph = {row["id"]: _depends(row) for row in _milestones(corrected)}
        if (
            isinstance(first, dict)
            and first.get("kind") in ("implement", "validate")
            and graph.get(first.get("milestone_id"))
        ):
            raise ValueError(
                refusal(
                    milestone_id,
                    f"after the merge the initial task's milestone {first['milestone_id']} has prerequisites",
                )
            )
        found = unprovable(state, corrected, coordinated_run=coordinated_run)
    return corrected, merges


def refuse(state: RunState, body) -> None:
    """Refuse a plan with an unprovable milestone (validate_body, so approval refuses it too)."""
    found = unprovable(state, body)
    if found:
        raise ValueError(refusal(found[0]))


def report_refusal(contract, *, coordinated_run) -> str | None:
    """What report-check reports for a Planner's contract: the correction install_draft would refuse it with."""
    if not isinstance(contract, dict) or contract.get("task_kind") != "bugfix":
        return None
    try:
        correct(cast("RunState", {}), contract, coordinated_run=coordinated_run)
    except ValueError as error:
        return str(error)
    return None


def _accepted(state: RunState) -> set[str]:
    contract = state.get("goal_contract") or {}
    return {
        mid
        for row in (state.get("milestone_progress") or {}).values()
        if isinstance(row, dict) and row.get("accepted") and row.get("contract_hash") == contract.get("hash")
        for mid in row.get("milestone_ids") or [row.get("id")]
    }


def send_back(state: RunState, install) -> bool:
    """Send a saved approved bug-fix plan with an unprovable, unaccepted milestone back to planning.

    The corrected draft is installed with ``install`` (autocode_goal_lifecycle.install_draft) and goes to plan
    review; when the merge is not safe, AutoCode records the correction as its own technical decision and the
    Planner drafts again. Either way no person question is published. Once per contract revision; False, leaving
    the state as it was, when the plan does not qualify.
    """
    try:
        from . import autocode_planning_cycle as planning_cycle
        from . import autocode_resolver_human as human
        from . import autocode_roles as roles
    except ImportError:
        import autocode_planning_cycle as planning_cycle
        import autocode_resolver_human as human
        import autocode_roles as roles
    contract = state.get("goal_contract") or {}
    body = contract.get("body") or {}
    if not isinstance(body, dict) or body.get("task_kind") != "bugfix":
        return False
    if not contracts.approved(state):
        return False
    revision = contract.get("revision")
    if any(
        isinstance(row, dict) and row.get("contract_revision") == revision
        for row in state.get("fail_first_corrections") or []
    ):
        return False
    accepted = _accepted(state)
    found = [mid for mid in unprovable(state, body) if mid not in accepted]
    if not found:
        return False
    candidate = copy.deepcopy(state)
    try:
        corrected, merges = correct(state, body)
        candidate.setdefault("fail_first_corrections", []).append(
            {
                "contract_revision": revision,
                "milestones": found,
                "merged": merges,
                "outcome": "plan_review",
                "at": util.now(),
            }
        )
        changes = [
            {
                "item": row["into"],
                "change": "reworded",
                "basis": "runner_proof",
                "answer_id": "",
                "replacement": (
                    f"merged {row['milestone']} into {row['into']}: {refusal(row['milestone'])} The merged milestone "
                    "keeps every criterion and path of both."
                ),
            }
            for row in merges
        ]
        install(candidate, corrected, origin=ORIGIN, changes=changes, record=None, queue_human=False)
        planning_cycle.start(candidate)
    except ValueError as error:
        candidate = copy.deepcopy(state)
        text = str(error) if PHRASE in str(error) else refusal(found[0])
        candidate.setdefault("fail_first_corrections", []).append(
            {"contract_revision": revision, "milestones": found, "merged": [], "outcome": "planner", "at": util.now()}
        )
        candidate.setdefault("user_events", []).append(
            {
                "kind": "technical_decision",
                "actor": "autocode",
                "settled_as": SETTLED_AS,
                "at": util.now(),
                "milestones": found,
                "text": text,
                "reason": "The approved bug-fix plan has a milestone the regression proof can never accept, and the "
                "runner cannot merge it safely, so the Planner corrects the plan and the person approves the "
                "corrected plan.",
                "contract_token": contracts.token(contract),
            }
        )
        planning_cycle.internal_answer_restart(candidate)
        if candidate.get("current_task"):
            candidate.setdefault("task_archive", []).append(candidate.pop("current_task"))
        for key in ("next_action", "user_request", human.PRIVATE, human.PUBLIC):
            cast(dict, candidate).pop(key, None)
        run_status.running(
            candidate,
            "DISCOVERING",
            next_stage="requirements" if roles.planning_flow(candidate) == "v2" else "draft_plan",
            pending_questions=[],
        )
    cast(dict, state).clear()
    state.update(candidate)
    return True

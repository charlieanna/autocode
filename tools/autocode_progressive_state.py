"""Sole writer of the run's ``progressive`` record (AGENTS.md rule 4).

One new top-level run-state key, ``state["progressive"]``, holds everything
progressive planning adds: the pending candidate proposal, the sealed
delegation and plan identity, later the active slice, future map, immutable
task/attempt bindings, allowance lineage, cumulative check obligations,
checkpoint evidence and history. This module is the only code that writes it.
The rules themselves are pure (`autocode_progressive_plan`).

Readers (read ``view(state)`` / the keys below; never write):
- planning and approval actions (this module's ``accept_proposal``/``seal``);
- task admission and dispatch, stage context, report application and repair;
- check replay and stagnation classification;
- completion and the additive status projection.

Everything is versioned; absent keys mean the ordinary (non-progressive) path,
and legacy or already-approved runs never acquire a delegation from defaults,
resumes or a model's assertion.
"""
from __future__ import annotations

try:
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_source_scope as source_scope


import copy
import json
import math
from pathlib import Path, PurePosixPath

try:
    from . import autocode_progressive_plan as rules
    from . import autocode_util as util
    from . import autocode_contract_identity as goals
    from . import autocode_progressive_artifacts as artifacts
    from . import autocode_progressive_budget as budget
    from . import autocode_progressive_activation as activation
    from . import autocode_progressive_progress as progress_policy
    from . import autocode_verification_plan as verification
    from . import autocode_repair_provenance as repair_provenance
except ImportError:
    import autocode_progressive_plan as rules
    import autocode_util as util
    import autocode_contract_identity as goals
    import autocode_progressive_artifacts as artifacts
    import autocode_progressive_budget as budget
    import autocode_progressive_activation as activation
    import autocode_progressive_progress as progress_policy
    import autocode_verification_plan as verification
    import autocode_repair_provenance as repair_provenance

KEY = "progressive"
VERSION = 1

_PREFIXES = (rules.DISCLOSURE_DELEGATION, rules.DISCLOSURE_SLICE, rules.DISCLOSURE_OUTSTANDING)
_RETIREMENT_PREFIX = "Progressive check retirement: "


def view(state):
    """The progressive record, or {} on the ordinary path."""
    record = state.get(KEY)
    return record if isinstance(record, dict) else {}


def retained_review_budget_pause(state, status):
    """Keep an inherited review ceiling actionable without inventing more capacity."""
    return status == "PAUSED_PLANNING_BUDGET" and bool(view(state).get("budget"))


def initial_limits(state):
    settings = state.get("settings", {})
    return rules.normalize_limits({"slice_review_calls": settings.get("planning_review_call_limit", 2),
        "slice_stage_seconds": settings.get("milestone_checkpoints", {}).get("max_seconds", 5400),
        "run_max_seconds": settings.get("limits", {}).get("max_seconds", 43200),
        "run_max_seconds_explicit_only": True})


def product_checklist(body, checks):
    rows = copy.deepcopy(checks)
    for row in verification.product_checks(body, rows):
        rules.check_identity(row)
        if any(old["id"] == row["id"] for old in rows):
            raise ValueError("slice check identity collides with a mandatory original product check")
        rows.append(row)
    return rows


def _strip_disclosure(body, generated):
    for field in ("constraints", "technical_approach"):
        body[field] = [line for line in body.get(field, []) if line not in generated[field]]


def planning_revision_state(state):
    """Exclude only authenticated runner-authored draft text from product edits."""
    replacement = view(state).get("draft_replacement")
    contract = state.get("goal_contract") or {}
    if (not replacement or contract.get("approval_status") == "approved"
            or replacement["contract_token"] != goals.token(contract)):
        return state
    revised = {**state, "goal_contract": copy.deepcopy(contract)}
    _strip_disclosure(revised["goal_contract"]["body"], replacement["generated"])
    return revised


def finish_draft(state):
    record = view(state)
    record.pop("draft_replacement", None)
    if record and set(record) == {"version"}:
        state.pop(KEY, None)


def accept_proposal(state, value, *, origin):
    """Validate a planner report's progressive proposal and install its disclosure.

    Called from the report-application boundary before a draft contract is
    installed. Without a proposal the ordinary path is kept (and a report that
    hand-writes disclosure lines without one is rejected: a model's assertion
    is not authority). With one, the disclosure is generated from the
    structured proposal into the contract's ``constraints`` and
    ``technical_approach``, mismatches are rejected, and the candidate is
    recorded for the approval step to seal.
    """
    body = value.get("contract")
    if body is None:
        return
    criteria = [row["id"] for row in body.get("acceptance_criteria") or []]
    proposal = value.get("progressive_proposal")
    previous = view(state).get("candidate")
    old_disclosure = None
    contract = state.get("goal_contract") or {}
    if previous and contract and contract.get("approval_status") != "approved":
        if previous["plan_hash"] != rules.plan_identity(previous["proposal"]):
            raise ValueError("prior progressive draft identity is not authentic")
        old_disclosure = rules.disclosure(previous["proposal"], previous["criteria"], limits=previous["limits"])
        rules.check_disclosure(contract["body"], previous["proposal"], previous["criteria"], limits=previous["limits"])
        _strip_disclosure(body, old_disclosure)
        view(state)["draft_replacement"] = {"contract_token": goals.token(contract), "generated": old_disclosure}
    if not rules.declares(proposal):
        shown = [line for field in ("constraints", "technical_approach")
                 for line in body.get(field) or [] if line.startswith(_PREFIXES)]
        if shown:
            raise ValueError("the plan card carries progressive disclosure without a progressive_proposal; "
                             "only the runner's generated disclosure may delegate")
        clear_candidate(state)
        return
    rules.validate_proposal(proposal, criteria, initial=True)
    rules.refuse_git_status(proposal)
    validate_product_paths(body, proposal)
    product_checklist(body, [check for row in proposal["slices"] for check in row["checks"]])
    limits = initial_limits(state)
    generated = rules.disclosure(proposal, criteria, limits=limits)
    _install(body, generated)
    rules.check_disclosure(body, proposal, criteria, limits=limits)
    record = state.setdefault(KEY, {"version": VERSION})
    record["candidate"] = {"proposal": copy.deepcopy(proposal), "plan_hash": rules.plan_identity(proposal),
                           "origin": origin, "criteria": sorted(criteria), "limits": limits,
                           "prior_disclosure": old_disclosure, "accepted_at": util.now()}
    return record["candidate"]


def _install(body, generated):
    constraints = list(body.get("constraints") or [])
    approach = list(body.get("technical_approach") or [])
    removals = [line for line in body.get("scope_exclusions", []) if line.startswith(_RETIREMENT_PREFIX)]
    if any(line.startswith(_RETIREMENT_PREFIX) and line not in removals for line in constraints):
        raise ValueError("visible progressive retirement differs from its explicit scope exclusion")
    lines = generated["constraints"] + generated["technical_approach"]
    for line in constraints + approach:
        if line.startswith(_PREFIXES) and line not in lines:
            raise ValueError("the plan card carries a disclosure line the proposal does not generate: "
                             + line[:80] + "...")
    # The expanded ordinary plan card displays constraints, not scope_exclusions.
    body["constraints"] = [line for line in constraints if not line.startswith((*_PREFIXES, _RETIREMENT_PREFIX))] \
        + generated["constraints"] + removals
    body["technical_approach"] = generated["technical_approach"] \
        + [line for line in approach if not line.startswith(_PREFIXES)]


def _first_difference(left, right, path="$"):
    """Return the first JSON path whose values differ, or None."""
    if type(left) is not type(right):
        return path
    if isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}"
            if key not in left or key not in right:
                return child
            difference = _first_difference(left[key], right[key], child)
            if difference:
                return difference
        return None
    if isinstance(left, list):
        for index in range(max(len(left), len(right))):
            child = f"{path}[{index}]"
            if index >= len(left) or index >= len(right):
                return child
            difference = _first_difference(left[index], right[index], child)
            if difference:
                return difference
        return None
    return None if left == right else path


def _canonical_contract(body, proposal, criteria, limits, prior_disclosure=None):
    canonical = copy.deepcopy(body)
    if prior_disclosure:
        _strip_disclosure(canonical, prior_disclosure)
    _install(canonical, rules.disclosure(proposal, criteria, limits=limits))
    return canonical


def clear_candidate(state):
    record = state.get(KEY)
    if record:
        record.pop("candidate", None)


def retirement_line(check, removes):
    """Visible declaration signed by the ordinary revised-goal approval."""
    return _RETIREMENT_PREFIX + json.dumps(
        {"check_id": check["id"], "check_hash": rules.check_identity(check), "removes": removes},
        sort_keys=True, separators=(",", ":"))


def _prepare_retirements(state, body, proposal, previous_token):
    """Reconcile exact old obligations without changing the suspended ledger."""
    record = view(state)
    current = state["goal_contract"]
    predecessor = next((old for old in state.get("contract_history", [])
        if old.get("task_id") == current["task_id"] and old.get("revision", 0) < current["revision"]
        and goals.token(old) == previous_token
        and goals.approved({"goal_contract": old, "user_events": state.get("user_events", [])})), None)
    if predecessor is None:
        raise ValueError("product revision requires the authenticated earlier approved same-task contract")
    previous_state = {**state, "goal_contract": predecessor}
    active = require_active(previous_state)
    known = list(active["definition"]["checks"])
    known += verification.product_checks(predecessor["body"], known)
    for entry in record.get("history", []):
        envelope = artifacts.verify(state["run_dir"], entry["artifact"])
        if envelope["kind"] != "checkpoint" or envelope["report"].get("required_checks") != entry["required_checks"]:
            raise ValueError("retirement requires authentic historical check definitions")
        known += entry["required_checks"]
    old_checks = {row["id"]: row for row in record.get("required_checks", [])}
    proposed = {row["id"] for slice_row in proposal["slices"] for row in slice_row["checks"]}
    old_behaviors = set(predecessor["body"]["required_behaviors"])
    old_behaviors.update(row["criterion"] for row in predecessor["body"]["acceptance_criteria"])
    current_behaviors = set(body["required_behaviors"])
    current_behaviors.update(row["criterion"] for row in body["acceptance_criteria"])
    grants, retired = [], []
    for line in body.get("scope_exclusions", []):
        if not line.startswith(_RETIREMENT_PREFIX):
            continue
        try:
            declaration = json.loads(line.removeprefix(_RETIREMENT_PREFIX))
            if type(declaration) is not dict or set(declaration) != {"check_id", "check_hash", "removes"}:
                raise ValueError("unexpected declaration fields")
            check = old_checks.get(declaration["check_id"])
            removes = declaration["removes"]
            if (check is None or type(removes) is not str or not removes.strip() or "\x00" in removes
                    or line != retirement_line(check, removes)
                    or line not in body.get("constraints", [])
                    or declaration["check_id"] in proposed
                    or any(row["check_id"] == check["id"] for row in grants)
                    or not any(row["id"] == check["id"] and rules.check_identity(row) == rules.check_identity(check)
                               for row in known)
                    or removes not in old_behaviors or removes in current_behaviors):
                raise ValueError("unknown, stale, retained or unrelated check/behavior")
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError("invalid explicit progressive check retirement: " + str(error)) from error
        grants.append({"kind": "product_change", **declaration, "visible_removal": line})
        retired.append(copy.deepcopy(check))
    retained = [copy.deepcopy(row) for row in old_checks.values() if row["id"] not in {check["id"] for check in retired}]
    criteria = {row["id"] for row in body["acceptance_criteria"]}
    if any(set(row["criterion_ids"]) - criteria for row in retained):
        raise ValueError("product revision must explicitly reconcile retained criterion/check obligations")
    return retained, retired, grants


def prepare_seal(state, contract_token):
    """Seal the delegation into ordinary approval, or keep the ordinary path.

    Called only from the approval boundary after the user's approval event, on
    the exact displayed contract token. A candidate whose disclosure or product
    criteria no longer match what the user approved refuses sealing: the run
    pauses on that decision instead of continuing on a stale grant.
    """
    reports = state.get("planning", {}).get("reports", {})
    for row in state.get("stages", []):
        if (row.get("report_only") and row.get("output")
                == reports.get(row.get("original_stage"), {}).get("output")):
            repair_provenance.verify_accepted_repair(state, row)
    record = state.get(KEY)
    candidate = (record or {}).get("candidate")
    if not candidate:
        return
    if state.get("active_stage") or state.get("uncertain_artifacts") or state.get("pending_report_repair"):
        raise ValueError("initial progressive approval requires a reconciled boundary without an active stage or uncertain report")
    previous_grant = record.get("delegation") or {}
    renewing = bool(previous_grant and previous_grant.get("contract_token") != contract_token)
    body = (state.get("goal_contract") or {}).get("body") or {}
    criteria = [row["id"] for row in body.get("acceptance_criteria") or []]
    if sorted(criteria) != candidate.get("criteria"):
        raise ValueError("the approved plan's product criteria changed since the progressive proposal; "
                         "re-plan before approval")
    if rules.plan_identity(candidate["proposal"]) != candidate.get("plan_hash"):
        raise ValueError("the recorded progressive proposal does not match its sealed plan identity")
    limits = candidate.get("limits") or initial_limits(state)
    rules.check_disclosure(body, candidate["proposal"], criteria, limits=limits)
    delegation = rules.seal_delegation(candidate["proposal"], contract_token, limits=limits)
    prepared = {"delegation": delegation,
            "plan": {"proposal": copy.deepcopy(candidate["proposal"]),
                      "plan_hash": candidate["plan_hash"], "limits": limits, "sealed_at": util.now()}}
    # Approval consumes the actual final independent review, not a synthetic grant.
    reviewer = "plan_finalize" if state.get("settings", {}).get("planning_flow") == "v2" else "astra_finalize"
    planner = "plan_revise" if reviewer == "plan_finalize" else "glm_revise"
    final = reports.get(reviewer) or {}
    prior = reports.get(planner) or {}
    witness, reviewer_session = repair_provenance.witness(state, reviewer, final.get("output"))
    if (not prior.get("output") or prior["output"] == final.get("output")
            or candidate.get("origin") != reviewer
            or final.get("report", {}).get("progressive_proposal") != candidate["proposal"]
            or any(row.get("resolved") is not True for row in final["report"].get("decisions", []))):
        raise ValueError("initial progressive activation requires the accepted final independent plan review")
    raw = util.read(Path(final["output"]))
    if raw.get("progressive_proposal") != candidate["proposal"]:
        raise ValueError("saved final review differs from its accepted progressive proposal")
    raw_body = _canonical_contract(raw.get("contract") or {}, candidate["proposal"], criteria,
                                   limits, candidate.get("prior_disclosure"))
    reviewed_body = _canonical_contract(final["report"]["contract"], candidate["proposal"], criteria,
                                         limits, candidate.get("prior_disclosure"))
    difference = _first_difference(raw_body, reviewed_body)
    if difference:
        raise ValueError("saved final review differs from the approved progressive contract "
                         f"at {difference}")
    if (body != final["report"]["contract"]
            or state.get("planning", {}).get("final_token") != contract_token):
        raise ValueError("progressive approval requires the exact current independently reviewed displayed contract")
    first_task = body.get("initial_task") or {}
    first_slice = candidate["proposal"]["slices"][0]
    head_commands = {command for row in first_slice["checks"] for command in verification.commands(row["method"])}
    task_commands = {command for method in first_task.get("validation_plan", []) for command in verification.commands(method)}
    if not task_commands or not task_commands <= head_commands:
        raise ValueError("progressive initial_task validation must belong to the concrete first slice, not tentative future work")
    if any(first_task.get("objective") == row["intended_result"] for row in candidate["proposal"]["slices"][1:]):
        raise ValueError("progressive initial_task targets a tentative future slice")
    planner_witness, planner_session = repair_provenance.witness(state, planner, prior["output"])
    if planner_witness.get("role") == witness.get("role") or planner_session == reviewer_session:
        raise ValueError("initial progressive reviewer must be independent of the accepted Planner")
    snapshot = source_scope.snapshot(Path(state["workspace"]), state)
    if any(row.get("source_revision") != snapshot["revision"] for row in (witness, planner_witness)):
        raise ValueError("source changed after initial progressive plan review")
    prepared["required_checks"] = product_checklist(body, rules.cumulative_checks(candidate["proposal"]))
    proposal_report = {"proposal": candidate["proposal"]}
    if renewing:
        retained, retired, grants = _prepare_retirements(state, body, candidate["proposal"], previous_grant["contract_token"])
        prepared["required_checks"] = product_checklist(body, rules.cumulative_checks(candidate["proposal"], retained))
        prepared["retirements"] = grants
        proposal_report.update(contract_body=copy.deepcopy(body), retired_checks=retired,
                               predecessor_contract_token=previous_grant["contract_token"])
    elif any(line.startswith(_RETIREMENT_PREFIX) for line in body.get("scope_exclusions", [])):
        raise ValueError("check retirement requires an earlier approved progressive contract")
    envelope, _ = artifacts.prepare("proposal", contract_token=contract_token,
        predecessor_identity=None, candidate_identity=candidate["plan_hash"],
        plan_identity=candidate["plan_hash"], source_snapshot_identity=util.digest(snapshot),
        report=proposal_report)
    identity = artifacts.persist(state["run_dir"], envelope)
    for grant in prepared.get("retirements", []):
        grant.update(contract_token=contract_token, artifact=copy.deepcopy(identity))
    review_report = {"accepted": True, "candidate_sha256": identity["sha256"],
                    "stage": reviewer, "role": witness.get("role"),
                    "output": final["output"], "output_hash": util.file_hash(Path(final["output"])),
                    "planner_output": prior["output"]}
    envelope, _ = artifacts.prepare("review", contract_token=contract_token,
        predecessor_identity=candidate["plan_hash"], candidate_identity=candidate["plan_hash"],
        plan_identity=candidate["plan_hash"], source_snapshot_identity=util.digest(snapshot), report=review_report)
    reviewed = artifacts.persist(state["run_dir"], envelope)
    prepared["active"] = {"definition": copy.deepcopy(candidate["proposal"]["slices"][0]),
                          "plan_hash": candidate["plan_hash"], "artifact": identity, "review": reviewed}
    prepared["outstanding_criteria"] = sorted(criteria)
    if renewing:
        # Explicitly reviewed new-goal approval renews authority, not capacity.
        # No check retirement is inferred from a changed ID or new token.
        prepared["renewal"] = {key: copy.deepcopy(record.get(key)) for key in
                               ("initial_plan", "plan", "delegation", "active", "completion_proof")}
        prepared["renewal_allowance"] = copy.deepcopy(record.get("pending_allowance") or record["active_allowance"])
    if not record.get("budget"):
        settings = state.get("settings", {})
        planning = state.get("planning", {})
        review_limit = limits["slice_review_calls"] or 0
        local_limit = limits["slice_stage_seconds"] or 0
        run_limit = limits["run_max_seconds"] or 0
        provenance = "default" if (review_limit, local_limit, run_limit) == (2, 5400, 43200) else "configured"
        used = state.get("active_seconds", 0)
        ledger = budget.new_ledger(review_limit=review_limit, local_seconds_limit=local_limit,
                                   run_seconds_limit=run_limit, run_seconds_used=used,
                                   limit_provenance=provenance)
        head = candidate["proposal"]["slices"][0]
        work = "work:" + util.digest({"initial_plan": candidate["plan_hash"], "slice": head})
        pool = "slice:" + work.removeprefix("work:")
        recovery = []
        for grant in planning.get("recovery_review_grants", []):
            path = Path(grant["receipt_output"])
            receipt = util.read(path)
            if (util.file_hash(path) != grant["receipt_hash"]
                    or receipt.get("runner_owned") is not True
                    or receipt.get("receipt", {}).get("id") != grant["id"]
                    or receipt["receipt"].get("binding") != grant["binding"]):
                raise ValueError("initial review recovery allowance lacks its authenticated runner receipt")
            used_by = grant.get("consuming_output") if grant.get("consumed") else None
            if used_by and not any(row.get("output") == used_by and row.get("planning_recovery_grant") == grant["id"]
                                   for row in state.get("stages", [])):
                raise ValueError("initial review recovery usage does not match its admitted attempt")
            recovery.append({"id": grant["id"], "provenance": "resolver_receipt:" + grant["id"], "used_by": used_by})
        prepared["budget"] = budget.allocate(ledger, "initial:" + candidate["plan_hash"], pool,
                                              approved_work=[work], seed_reviews=planning.get("astra_calls", 0),
                                              seed_seconds=used, recovery_grants=recovery)
        # Initial planning was accounted before the ledger existed. Pin those
        # already-included records so a recovered copy cannot add their time again.
        for row in state.get("stages", []):
            identity = row.get("output")
            if not identity or not row.get("accounted"):
                continue
            duration = 0 if row.get("duration_seconds") is None else row["duration_seconds"]
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
                raise ValueError("initial planning has invalid accounted duration")
            prepared["budget"]["attempts"][identity] = {"pool": pool, "review": False,
                "recovery_grant": None, "refunded": False,
                "outcome": {"exit_code": row.get("exit_code"), "timed_out": bool(row.get("timed_out"))}}
            prepared["budget"]["time_receipts"]["time:" + identity] = {
                "attempt": identity, "pool": pool, "seconds": duration}
        prepared["initial_allowance"] = {"slice_id": head["id"], "pool_id": pool, "work_id": work}
    return prepared


def seal(state, contract_token, *, prepared=None):
    """Publish a preflighted seal at the ordinary approval boundary."""
    prepared = prepared if prepared is not None else prepare_seal(state, contract_token)
    if prepared is None:
        return
    if prepared["delegation"]["contract_token"] != contract_token:
        raise ValueError("prepared progressive approval belongs to a different token")
    if not goals.approved(state) or goals.token(state["goal_contract"]) != contract_token:
        raise ValueError("progressive publication requires the actual current user-approved contract")
    record = state[KEY]
    record.setdefault("retirements", []).extend(copy.deepcopy(prepared.get("retirements", [])))
    if prepared.get("renewal"):
        record.setdefault("execution_history", []).append(prepared["renewal"])
        record.pop("completion_proof", None)
        record.pop("transition", None)
        # Retained/replacement work stays in the previously active pool.
        record.pop("pending_allowance", None)
        record["active_allowance"] = copy.deepcopy(prepared["renewal_allowance"])
        record["active_allowance"]["slice_id"] = prepared["active"]["definition"]["id"]
    record.pop("candidate", None)
    record["initial_plan"] = copy.deepcopy(prepared["plan"])
    record["plan"] = copy.deepcopy(prepared["plan"])
    record["delegation"] = copy.deepcopy(prepared["delegation"])
    record["future"] = copy.deepcopy(prepared["plan"]["proposal"]["slices"][1:])
    for key in ("active", "required_checks", "outstanding_criteria"):
        record[key] = copy.deepcopy(prepared[key])
    if "budget" in prepared:
        record["budget"] = copy.deepcopy(prepared["budget"])
        record["initial_allowance"] = copy.deepcopy(prepared["initial_allowance"])
        record["active_allowance"] = copy.deepcopy(prepared["initial_allowance"])
    return record["delegation"]


def enabled(state):
    """Detect a progressive promise even when its execution ledger was lost.

    Detection is not authority: admission separately verifies the approval and
    reviewed active artifact. A missing ledger must not downgrade to ordinary
    unbounded execution.
    """
    body = (state.get("goal_contract") or {}).get("body") or {}
    record = state.get(KEY)
    malformed = KEY in state and (not isinstance(record, dict) or record.get("version") != VERSION)
    return bool(malformed or view(state).get("delegation") or any(
        isinstance(line, str) and line.startswith(rules.DISCLOSURE_DELEGATION)
        for line in body.get("constraints", [])))


def armed(state):
    """Progressive budget and classification authority: enabled under the approved contract.

    ``enabled`` alone is detection. Before approval the ordinary milestone
    budget and stagnation rules still apply; the progressive policy takes over
    only once the delegation is sealed under the current approved contract.
    """
    return enabled(state) and goals.approved(state)


def require_active(state):
    record = view(state)
    contract = state.get("goal_contract") or {}
    rules.require_delegation(record, contract_token=goals.token(contract),
                             contract_body=contract.get("body") or {},
                             contract_approved=goals.approved(state),
                             contract_sealed=goals.sealed(contract))
    active = record.get("active")
    if not isinstance(active, dict) or not active.get("artifact") or not active.get("review"):
        raise ValueError("progressive execution requires a persisted independently reviewed active slice")
    definition = active.get("definition")
    if not isinstance(definition, dict) or definition.get("tentative") is not False:
        raise ValueError("a tentative or undetailed slice cannot authorize execution")
    candidate = artifacts.verify(state.get("run_dir"), active["artifact"])
    review = artifacts.verify(state.get("run_dir"), active["review"])
    proposal = candidate["report"].get("proposal")
    criteria = [row["id"] for row in contract["body"].get("acceptance_criteria", [])]
    if (not isinstance(proposal, dict) or not proposal.get("slices")
            or rules.validate_slice(proposal["slices"][0], criteria) != rules.validate_slice(definition, criteria)
            or rules.plan_identity(proposal) != active.get("plan_hash")
            or candidate["plan_identity"] != active["plan_hash"]):
        raise ValueError("active slice differs from its persisted reviewed plan")
    if (candidate["contract_token"] != goals.token(contract)
            or review["contract_token"] != goals.token(contract)
            or review["kind"] != "review" or review["report"].get("accepted") is not True
            or review["report"].get("candidate_sha256") != active["artifact"]["sha256"]
            or review["candidate_identity"] != candidate["candidate_identity"]
            or review["plan_identity"] != candidate["plan_identity"]
            or review["source_snapshot_identity"] != candidate["source_snapshot_identity"]):
        raise ValueError("active slice review does not bind its exact contract, plan and retained source")
    return active


def context(state):
    """Read the same reviewed slice and checklist used for execution/replay."""
    if (not enabled(state) or (state.get("goal_contract") or {}).get("approval_status") != "approved"
            or not goals.approved(state)):
        return {}
    active = require_active(state)
    record = view(state)
    due = due_checks(state)
    return {"active_slice": copy.deepcopy(active["definition"]),
            "plan_hash": active["plan_hash"],
            "required_checks": due,
            "checkpoint_checks": copy.deepcopy(record.get("required_checks", [])),
            "done_slices": [row["slice_id"] for row in record.get("history", [])],
            "outstanding_criteria": deferred_criteria(state, due)}


def pending_product_criteria(state):
    """Read the reviewed future suffix, not the queue still holding the active head."""
    proposal = view(state).get("plan", {}).get("proposal", {})
    return {cid for row in proposal.get("slices", [])[1:] for cid in row["criterion_ids"]}


def deferred_criteria(state, due):
    targets = {cid for row in due if row["relation"] == "fully_verify" for cid in row["criterion_ids"]}
    pending = pending_product_criteria(state)
    targets -= pending
    return sorted(row["id"] for row in state["goal_contract"]["body"]["acceptance_criteria"] if row["id"] not in targets)


def due_checks(state):
    """Earlier demonstrations remain due; unfinished slice checks wait for its checkpoint.

    The existing bounded task's validation plan defines its local obligations.
    These derived rows are prompt/replay data, not a second mutable checklist.
    """
    ledger = view(state)
    task = state.get("current_task") or {}
    commands = {command for method in task.get("validation_plan", []) for command in verification.commands(method)}
    historical = {row["id"] for entry in ledger.get("history", []) for row in entry.get("required_checks", [])}
    fully_requested = {cid for row in ledger.get("required_checks", []) if row["relation"] == "fully_verify"
                       and set(verification.commands(row["method"])) <= commands for cid in row["criterion_ids"]}
    guards = {row["id"] for row in state["goal_contract"]["body"]["acceptance_criteria"]
              if row.get("verification_method", "").strip().startswith("guard:")}
    rows = [copy.deepcopy(row) for row in ledger.get("required_checks", [])
            if row["id"] in historical or guards.intersection(row["criterion_ids"])
            or ledger.get("proven_obligations", {}).get(row["id"]) == rules.check_identity(row)
            or fully_requested.intersection(row["criterion_ids"])
            or set(verification.commands(row["method"])) <= commands]
    covered = {command for row in rows for command in verification.commands(row["method"])}
    for command in sorted(commands - covered):
        rows.append({"id": "task-check:" + util.digest(command), "method": command,
                     "relation": "contributes_to", "criterion_ids": list(task.get("acceptance_criteria", []))})
    return product_checklist(state["goal_contract"]["body"], rows)


def guard_assignment(state, spec, paths):
    """Require narrow ownership inside the independently reviewed slice."""
    if not enabled(state):
        return
    active = require_active(state)
    definition = active["definition"]
    milestones = (state.get("goal_contract") or {}).get("body", {}).get("milestones", [])
    if spec.get("milestone_ids"):
        raise ValueError("progressive assignments cannot admit a batch or milestone union")
    if spec.get("slice_id") not in (None, definition["id"]):
        raise ValueError("task declares an inactive slice identity")
    if len(milestones) != 1 or spec.get("milestone_id") != milestones[0]["id"]:
        raise ValueError("progressive tasks must use the one whole-product milestone")
    if not set(spec.get("acceptance_criteria", [])) <= set(definition["criterion_ids"]):
        raise ValueError("task criteria exceed the active reviewed slice")
    if not paths:
        raise ValueError("progressive tasks require bounded writable paths")
    owned = [PurePosixPath(path.rstrip("/")) for path in definition["paths"]]
    for path in paths:
        target = PurePosixPath(path.rstrip("/"))
        if (target.is_absolute() or ".." in target.parts or not target.parts
                or any(char in path for char in "*?[]\\")
                or not any(target == root or root in target.parents for root in owned)):
            raise ValueError(f"task path {path!r} exceeds active slice writable ownership")


def validate_product_paths(body, proposal):
    milestones = body.get("milestones", [])
    if len(milestones) != 1:
        raise ValueError("progressive planning requires one whole-product milestone")
    owned = [PurePosixPath(path.rstrip("/")) for path in milestones[0].get("affected_paths", [])]
    if not owned:
        raise ValueError("the whole-product milestone must declare its granted writable ownership")
    for row in proposal["slices"]:
        for path in row["paths"]:
            target = PurePosixPath(path.rstrip("/"))
            if not any(target == root or root in target.parents for root in owned):
                raise ValueError("progressive slice paths exceed the whole-product milestone's granted scope: " + path)


def guard_dispatch(state, stage):
    """Reject direct/saved batch bypasses and absent reviewed slice authority."""
    if not enabled(state):
        return
    if stage == "orchestrator" or (state.get("current_task") or {}).get("milestone_ids"):
        raise util.Paused("PAUSED_PROGRESSIVE_AUTHORITY", "Progressive slice execution must be serial")
    if stage not in ("terra", "sol", "astra_plan", "astra_review", "astra_checkpoint", "astra_resolve"):
        return
    if view(state).get("transition"):
        raise util.Paused("PAUSED_PROGRESSIVE_TRANSITION", "Slice continuation must finish its saved planning/decision boundary before execution")
    try:
        active = require_active(state)
        task = state.get("current_task") or {}
        if stage != "astra_plan" and task.get("slice_id") != active["definition"]["id"]:
            raise ValueError("task is not bound to the active reviewed slice")
        if task:
            guard_assignment(state, task, task.get("affected_paths", []))
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
        raise util.Paused("PAUSED_PROGRESSIVE_AUTHORITY", str(error)) from error


def bind_task(state):
    if not enabled(state):
        return
    active = require_active(state)
    task = state["current_task"]
    task["slice_id"] = active["definition"]["id"]
    binding = {"contract_token": goals.token(state["goal_contract"]),
               "plan_hash": active["plan_hash"], "slice_id": task["slice_id"],
               "task_id": task["id"], "assignment_source": task["source_revision"]}
    view(state).setdefault("tasks", {})[task["id"]] = binding


def attempt_identity(record):
    return str(record["output"])


def admit_attempt(state, record, before):
    try:
        return _admit_attempt(state, record, before)
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
        raise util.Paused("PAUSED_PROGRESSIVE_AUTHORITY", str(error)) from error


def _admit_attempt(state, record, before):
    """Called under the existing admission lock before durable save and launch."""
    if not enabled(state):
        return
    ledger = view(state)
    approved = goals.approved(state)
    if not approved and not ledger.get("budget"):
        return
    stage = record.get("original_stage") or record["stage"]
    if approved:
        guard_dispatch(state, stage)
    elif stage not in ("requirements_gather", "astra_discovery", "astra_challenge", "glm_revise",
                       "astra_finalize", "requirements", "plan", "plan_review", "plan_revise", "plan_finalize"):
        raise ValueError("suspended progressive delegation permits only ordinary new-goal planning")
    pool = (ledger.get("pending_allowance") or ledger.get("active_allowance") or {}).get("pool_id")
    if not pool:
        raise util.Paused("PAUSED_PROGRESSIVE_AUTHORITY", "No reserved progressive allowance")
    identity = attempt_identity(record)
    try:
        if (approved and ledger.get("transition", {}).get("phase") == "detail"
                and stage in ("glm_revise", "plan_revise")):
            status = budget.check_budget(ledger["budget"], pool, review=True)
            if not status["allowed"]:
                raise util.Paused("PAUSED_PLANNING_BUDGET", "No reserved independent review capacity: " + ", ".join(status["exhausted"]))
        ledger["budget"] = budget.admit(ledger["budget"], identity, pool,
            review=stage in ("astra_challenge", "astra_finalize", "plan_review", "plan_finalize")
                    and not record.get("report_only"))
    except budget.BudgetExhausted as error:
        raise util.Paused("PAUSED_PROGRESSIVE_BUDGET", str(error)) from error
    task = state.get("current_task") or {}
    if approved and stage in ("terra", "sol", "astra_plan", "astra_review", "astra_checkpoint", "astra_resolve") and task:
        binding = ledger.get("tasks", {}).get(task["id"])
        if not binding or binding["slice_id"] != task.get("slice_id"):
            raise util.Paused("PAUSED_PROGRESSIVE_AUTHORITY", "Task lacks immutable slice assignment")
        prior = [row for row in ledger.get("attempts", {}).values() if row.get("task_id") == task["id"]]
        if not prior and before["revision"] != binding["assignment_source"]:
            raise ValueError("source changed between reviewed assignment and its first provider launch")
        attempt = {**binding, "attempt": 1 + sum(row.get("task_id") == task["id"]
                   for row in ledger.get("attempts", {}).values()), "launch_source": before["revision"],
                   "stage": stage, "role": record.get("role")}
        previous = ledger.setdefault("attempts", {}).get(identity)
        if previous and any(previous.get(key) != attempt.get(key)
                            for key in (*binding, "launch_source", "stage", "role")):
            raise ValueError("progressive attempt identity cannot be rebound")
        if not previous:
            ledger["attempts"][identity] = attempt


def account_stage(state, record):
    """Use launch-time allowance, including failures and report-only repairs."""
    ledger = view(state)
    identity = record.get("output")
    if not ledger.get("budget") or identity not in ledger["budget"]["attempts"]:
        return True
    first = "time:" + identity not in ledger["budget"]["time_receipts"]
    updated = budget.record_time(ledger["budget"], "time:" + identity, identity,
                                 0 if record.get("duration_seconds") is None else record["duration_seconds"])
    if type(record.get("exit_code")) is int:
        updated = budget.refund_review(updated, identity, exit_code=record["exit_code"],
                                       timed_out=bool(record.get("timed_out")))
    ledger["budget"] = updated
    return first


def charge_review(state, stage, record):
    """Review admission is performed by admit_stage at the launch chokepoint."""
    if not enabled(state):
        return False
    approved = goals.approved(state)
    if not approved and not view(state).get("budget"):
        return False
    if not record or not record.get("output"):
        raise util.Paused("PAUSED_PROGRESSIVE_AUTHORITY", "Review needs a durable attempt identity")
    if record["output"] in view(state)["budget"]["attempts"]:
        return approved
    pool = (view(state).get("pending_allowance") or view(state).get("active_allowance"))["pool_id"]
    status = budget.check_budget(view(state)["budget"], pool, review=True)
    if not status["allowed"]:
        raise util.Paused("PAUSED_PLANNING_BUDGET", ", ".join(status["exhausted"]))
    return approved


def check_local_budget(state):
    if not enabled(state) or not goals.approved(state):
        return
    ledger = view(state)
    pool = (ledger.get("pending_allowance") or ledger.get("active_allowance") or {})["pool_id"]
    status = budget.check_budget(ledger["budget"], pool)
    if not status["allowed"]:
        raise util.Paused("PAUSED_MILESTONE_BUDGET", ", ".join(status["exhausted"]))


def set_explicit_limits(state, *, run_seconds=None, slice_seconds=None, review_calls=None):
    """Called only by the existing authenticated operator limit-change routes."""
    ledger = view(state)
    if not ledger.get("budget"):
        return
    pool = (ledger.get("pending_allowance") or ledger.get("active_allowance") or {}).get("pool_id")
    for kind, limit in (("run_seconds", run_seconds), ("local_seconds", slice_seconds), ("reviews", review_calls)):
        if limit is None:
            continue
        binding = None if kind == "run_seconds" else pool
        change = "user_cli:" + util.digest({"kind": kind, "pool": binding, "limit": limit})
        ledger["budget"] = budget.change_limit(ledger["budget"], change, kind, limit,
            provenance="user_cli_explicit", explicit=True, pool_id=binding)


def apply_recovery_limit(state, extension, receipt_id):
    """Bind an existing finite runner recovery receipt to its local allowance.

    This boundary never infers authority from changed settings, nor extends the
    progressive whole-run ceiling. It is called where the runner issues its
    authenticated operational receipt, under the existing serialized lock.
    """
    ledger = view(state)
    if not ledger.get("budget"):
        return False
    kind = {"milestone_max_seconds": "local_seconds", "planning_review_call_limit": "reviews"}.get(extension.get("kind"))
    if not kind:
        return False
    receipt = (state.get("resolver") or {}).get("operational_receipts", {}).get(receipt_id)
    if (not receipt or receipt.get("runner_owned") is not True
            or receipt.get("decision", {}).get("action") != "extend_default_budget"
            or receipt.get("receipt", {}).get("id") != receipt_id
            or receipt["receipt"].get("evidence") != extension
            or util.read(Path(receipt["output"])) != receipt
            or extension not in state["resolver"].get("budget_extensions", [])):
        raise ValueError("local recovery extension requires the exact issued runner receipt")
    bindings = ledger.setdefault("recovery_bindings", {})
    requested = {"pool": (ledger.get("pending_allowance") or ledger["active_allowance"])["pool_id"],
                 "kind": kind, "from": extension["from"], "to": extension["to"]}
    binding = bindings.setdefault(receipt_id, requested)
    if any(binding[key] != requested[key] for key in ("kind", "from", "to")):
        raise ValueError("local recovery receipt cannot be rebound")
    pool = ledger["budget"]["pools"][binding["pool"]]
    limit_key, used_key = ("review_limit", "reviews_used") if kind == "reviews" else ("seconds_limit", "seconds_used")
    if pool[used_key] < binding["from"]:
        return False
    change = "resolver:" + receipt_id
    if change not in ledger["budget"]["limit_changes"] and pool[limit_key] != binding["from"]:
        return False
    ledger["budget"] = budget.change_limit(ledger["budget"], change, kind, binding["to"],
        provenance="resolver_receipt:" + receipt_id, explicit=True, pool_id=binding["pool"])
    return True


def check_result_binding(state, record, current):
    if not enabled(state) or not goals.approved(state):
        return
    row = view(state).get("attempts", {}).get(record.get("output"))
    task = state.get("current_task") or {}
    active = require_active(state)
    if (not row or row["task_id"] != task.get("id") or row["slice_id"] != task.get("slice_id")
            or row["plan_hash"] != active["plan_hash"]
            or row["contract_token"] != goals.token(state["goal_contract"])
            or row.get("stage") != (record.get("original_stage") or record.get("stage"))
            or row.get("role") != record.get("role")
            or record.get("source_revision") != current["revision"]):
        raise ValueError("result does not belong to its immutable progressive attempt and current source")
    return {key: row[key] for key in ("contract_token", "plan_hash", "slice_id", "task_id",
                                     "assignment_source", "attempt")} | {"validated_source": current["revision"]}


def validation_receipts(state, current, *, checks=None, allow_failed=False):
    """Authenticate only runner replay output, never a model's PASS dictionary."""
    validation = state.get("validation") or {}
    record = next((row for row in reversed(state.get("stages", []) + [state.get("active_stage") or {}])
                   if row.get("output") == validation.get("output")), None)
    if not record or (not allow_failed and validation.get("verdict") != "PASS"):
        raise ValueError("progressive proof needs a current independent Validator PASS")
    binding = check_result_binding(state, record, current)
    replay = validation.get("check_replay") or {}
    if (not allow_failed and replay.get("verdict") != "PASS") or replay.get("source_revision") != current["revision"]:
        raise ValueError("progressive proof requires current cumulative runner replay")
    executions = {}
    for row in replay.get("checks", []):
        path = row.get("output")
        if allow_failed and (type(row.get("exit_code")) is not int or row["exit_code"] != 0
                             or row.get("timed_out") or row.get("error")):
            continue
        if (type(row.get("exit_code")) is not int or row["exit_code"] != 0 or row.get("timed_out")
                or row.get("error") or not path or not Path(path).is_file()
                or util.file_hash(Path(path)) != row.get("output_sha256")):
            raise ValueError("runner replay output is missing, failed or changed")
        executions[row["command"]] = {"command": row["command"], "status": "PASS", "exit_code": 0,
                                      "evidence_hashes": {path: row["output_sha256"]}}
    results = {}
    for check in view(state)["required_checks"] if checks is None else checks:
        commands = verification.commands(check["method"])
        if not commands or any(command not in executions for command in commands):
            if allow_failed:
                results[check["id"]] = None
                continue
            raise ValueError("cumulative progressive check was not independently replayed")
        rows = [executions[command] for command in commands]
        pins = {path: digest for row in rows for path, digest in row["evidence_hashes"].items()}
        results[check["id"]] = {"status": "PASS", "exit_code": 0,
            "check_hash": rules.check_identity(check), "contract_token": binding["contract_token"],
            "source_revision": current["revision"], "evidence_hashes": pins, "executions": rows,
            "replayed": True, "binding": binding,
            "identity": util.digest({"check": check, "binding": binding, "executions": rows})}
    return binding, results


def assert_product_claims(state, current, validation):
    """A model's PASS cannot turn contribution-only or stale receipts into product proof."""
    if not enabled(state):
        return
    claimed = {row["id"] for row in validation.get("criterion_results", []) if row["status"] == "PASS"}
    if not claimed:
        return
    checks = product_checklist(state["goal_contract"]["body"], view(state)["required_checks"])
    binding, results = validation_receipts({**state, "validation": validation}, current, checks=checks, allow_failed=True)
    proof = rules.criterion_proof(checks, results, contract_token=binding["contract_token"],
                                 source_revision=current["revision"], receipts_authenticated=True)
    pending = pending_product_criteria(state)
    missing = sorted(cid for cid in claimed if proof.get(cid) is not True or cid in pending)
    if missing:
        raise ValueError("Product PASS is not established by authenticated cumulative fully_verify proof: " + ", ".join(missing))


def require_reported_checks(state, checks):
    if not enabled(state):
        return
    required = {command for row in context(state)["required_checks"]
                for command in verification.commands(row["method"])}
    reported = {row.get("command") for row in checks}
    if required - reported:
        raise ValueError("Validator omitted cumulative progressive checks: " + ", ".join(sorted(required - reported)))


def classify_validation(state, current):
    if not enabled(state) or not goals.approved(state):
        return None
    ledger = view(state)
    due = due_checks(state)
    try:
        binding, results = validation_receipts(state, current, checks=due)
    except ValueError:
        return {"kind": "failure", "reason": "missing_current_replay", "proof_identity": None}
    gaps = [{"kind": "criterion", "id": row["id"], "status": row["status"]}
            for row in (state.get("validation") or {}).get("criterion_results", []) if row["status"] != "PASS"]
    deferred = deferred_criteria(state, due)
    end_status = (state.get("validation") or {}).get("end_to_end_result", {}).get("status")
    if end_status == "FAIL" or (end_status != "PASS" and not deferred):
        gaps.append({"kind": "task", "id": "product-end-to-end", "status": end_status or "NOT_VERIFIED"})
    findings = copy.deepcopy(state.get("findings_ledger", []))
    proof = util.digest(results)
    result = progress_policy.classify(due_checks=[{"id": row["id"], "check_hash": rules.check_identity(row)}
        for row in due], results=results, current_binding=binding,
        proof_identity=proof, receipts_authenticated=True,
        new_obligation_ids=[row["id"] for row in due
                            if ledger.get("proven_obligations", {}).get(row["id"]) != rules.check_identity(row)],
        seen_proof_identities=ledger.get("seen_proofs", []),
        seen_receipt_identities=ledger.get("seen_receipts", []), gaps=gaps,
        deferred_criteria=deferred, deferred_tasks=[], findings=findings)
    if result["kind"] == "progress":
        ledger.setdefault("seen_proofs", []).append(proof)
        ledger.setdefault("seen_receipts", []).extend(row["identity"] for row in results.values())
        ledger.setdefault("proven_obligations", {}).update({key: row["check_hash"] for key, row in results.items()})
    return result


def checkpoint(state, current, record, *, product_findings):
    """Persist local proof without accepting the product milestone or criteria."""
    active = require_active(state)
    if product_findings:
        raise ValueError("product blockers prevent a progressive checkpoint")
    check_result_binding(state, record, current)
    ledger = view(state)
    required = product_checklist(state["goal_contract"]["body"], ledger["required_checks"])
    _, results = validation_receipts(state, current, checks=required)
    statuses = {row["id"]: row["status"] for row in state["validation"]["criterion_results"]}
    if ("FAIL" in statuses.values() or state["validation"].get("end_to_end_result", {}).get("status") == "FAIL"):
        raise ValueError("real product failures cannot be deferred by a slice checkpoint")
    slice_id = active["definition"]["id"]
    if slice_id in [row["slice_id"] for row in ledger.get("history", [])]:
        raise ValueError("progressive checkpoint already consumed")
    verified = [row["slice_id"] for row in ledger.get("history", [])] + [slice_id]
    criteria = [row["id"] for row in state["goal_contract"]["body"]["acceptance_criteria"]]
    fully = {cid for check in required if check["relation"] == "fully_verify"
             for cid in check["criterion_ids"]}
    fully -= pending_product_criteria(state)
    if any(statuses.get(cid) != "PASS" for cid in fully):
        raise ValueError("fully_verify slice checks still have unverified original product criteria")
    payload = {"version": VERSION, "contract_token": goals.token(state["goal_contract"]),
        "plan_hash": active["plan_hash"], "source_revision": current["revision"], "slice_id": slice_id,
        "required_checks": required, "results": results,
        "criterion_ids": sorted(fully), "verified_slices": verified}
    envelope, _ = artifacts.prepare("checkpoint", contract_token=payload["contract_token"],
        predecessor_identity=active["artifact"]["sha256"], candidate_identity=active["plan_hash"],
        plan_identity=active["plan_hash"], source_snapshot_identity=current["revision"], report=payload)
    artifact = artifacts.persist(state["run_dir"], envelope)
    ledger["required_checks"] = required
    ledger.setdefault("history", []).append({"slice_id": slice_id, "plan_hash": active["plan_hash"],
                                            "artifact": artifact, "required_checks": copy.deepcopy(ledger["required_checks"])})
    ledger["completion_proof"] = {**payload, "artifact": artifact}
    ledger["outstanding_criteria"] = sorted(set(criteria) - fully)
    future = ledger["plan"]["proposal"]["slices"][1:]
    ledger["future"] = copy.deepcopy(future)
    if not future:
        if ledger["outstanding_criteria"]:
            allowance = copy.deepcopy(ledger["active_allowance"])
            ledger["budget"] = budget.allocate(ledger["budget"], "remaining:" + active["plan_hash"],
                                                allowance["pool_id"], inherit_work=[allowance["work_id"]])
            allowance["slice_id"] = None
            ledger["pending_allowance"] = allowance
            ledger["transition"] = {"phase": "detail", "source": copy.deepcopy(current), "slice_id": None,
                "remaining_criteria": copy.deepcopy(ledger["outstanding_criteria"]),
                "reason": "Remaining original product proof under the inherited spent allowance"}
            planner = "plan_revise" if state.get("settings", {}).get("planning_flow") == "v2" else "glm_revise"
            state.update(next_stage=planner, phase="PLANNING", next_action="Detail the remaining original product proof without a new allowance")
        else:
            state.update(next_stage="astra_review", next_action="Evaluate complete product proof")
        return
    # Only approved, previously unallocated future work may receive fresh capacity.
    head = future[0]
    work = "work:" + util.digest({"initial_plan": ledger["initial_plan"]["plan_hash"], "slice": head})
    pool = "slice:" + work.removeprefix("work:")
    original = next((row for row in ledger["initial_plan"]["proposal"]["slices"] if row["id"] == head["id"]), None)
    if original is not None and original == head and new_work_approved(state, head):
        ledger["budget"] = budget.allocate(ledger["budget"], "next:" + work, pool, approved_work=[work])
    else:
        # A renamed/replaced/split future is not evidence of new capacity.
        inherited = ledger["active_allowance"]
        pool = inherited["pool_id"]
        work = inherited["work_id"]
        ledger["budget"] = budget.allocate(ledger["budget"], "inherit:" + active["plan_hash"], pool,
                                           inherit_work=[work])
    ledger["pending_allowance"] = {"slice_id": head["id"], "pool_id": pool, "work_id": work}
    ledger["transition"] = {"phase": "detail", "source": copy.deepcopy(current), "slice_id": head["id"]}
    planner = "plan_revise" if state.get("settings", {}).get("planning_flow") == "v2" else "glm_revise"
    state.update(next_stage=planner, phase="PLANNING", next_action="Detail the next approved delivery slice")


def new_work_approved(state, head):
    """A replacement contract/hash/criterion ID is not evidence of new work."""
    history = view(state).get("execution_history", [])
    if not history:
        return True
    current = state["goal_contract"]["body"]
    texts = {row["criterion"] for row in current["acceptance_criteria"] if row["id"] in head["criterion_ids"]}
    current_behaviors = set(current["required_behaviors"])
    for previous in history:
        old_token = previous["delegation"]["contract_token"]
        contract = next((row for row in state.get("contract_history", []) if goals.token(row) == old_token
                         and goals.approved({"goal_contract": row, "user_events": state.get("user_events", [])})), None)
        if not contract:
            return False
        old = contract["body"]
        behaviors = set(old["required_behaviors"])
        if (not behaviors < current_behaviors
                or texts.intersection(row["criterion"] for row in old["acceptance_criteria"])):
            return False
    return True


def revision_pending(state):
    return bool(enabled(state) and goals.approved(state) and view(state).get("transition"))


def prepare_completion(state, current, record, *, product_findings):
    """A final whole-product decision also commits the final slice's replay proof."""
    if not enabled(state):
        return
    ledger = view(state)
    if ledger.get("completion_proof", {}).get("source_revision") == current["revision"]:
        return
    if len(ledger.get("plan", {}).get("proposal", {}).get("slices", [])) != 1:
        raise ValueError("future delivery slices still remain; a green local slice cannot complete the product")
    checkpoint(state, current, record, product_findings=product_findings)


def apply_revision(state, stage, value, record, *, product_findings):
    """Existing Planner/Reviewer stages, intercepted before ordinary draft installation."""
    if not revision_pending(state):
        return False
    ledger = view(state)
    transition = ledger["transition"]
    current = source_scope.snapshot(Path(state["workspace"]), state)
    if current != transition["source"] or record.get("source_revision") != current["revision"]:
        raise ValueError("source changed during progressive slice review")
    token = goals.token(state["goal_contract"])
    previous = ledger["plan"]
    planner_stage, reviewer_stage = (("plan_revise", "plan_finalize")
        if state.get("settings", {}).get("planning_flow") == "v2" else ("glm_revise", "astra_finalize"))
    if stage == planner_stage and transition["phase"] == "detail":
        proposal = value["progressive_proposal"]
        criteria = [row["id"] for row in state["goal_contract"]["body"]["acceptance_criteria"]]
        done = [row["slice_id"] for row in ledger.get("history", [])]
        proved = ledger["completion_proof"]["criterion_ids"]
        rules.validate_revision(previous["proposal"], proposal, criteria, established=ledger["required_checks"],
                                verified_done=done, verified_criteria=proved)
        rules.refuse_git_status(proposal)  # the new proposal only: the saved one is not checked again
        verification.refuse_git_status(verification.task_rows(value["initial_task"], "initial_task"))
        try:
            validate_product_paths(state["goal_contract"]["body"], proposal)
            transition.pop("scope_issue", None)
        except ValueError as error:
            transition["scope_issue"] = str(error)
        plan_hash = rules.plan_identity(proposal)
        envelope, _ = artifacts.prepare("revision", contract_token=token,
            predecessor_identity=previous["plan_hash"], candidate_identity=plan_hash, plan_identity=plan_hash,
            source_snapshot_identity=util.digest(current),
            report={"proposal": proposal, "initial_task": copy.deepcopy(value["initial_task"])})
        artifact = artifacts.persist(state["run_dir"], envelope)
        transition.update(phase="review", candidate=artifact, planner=copy.deepcopy(record),
                          initial_task=copy.deepcopy(value["initial_task"]))
        state.update(next_stage=reviewer_stage)
        return True
    if stage != reviewer_stage or transition["phase"] != "review":
        raise ValueError("unexpected progressive planning transition")
    candidate = artifacts.verify(state["run_dir"], transition["candidate"])
    planner = transition["planner"]
    flags = ("product_changes", "permission_changes", "unresolved_product_decisions")
    if (record.get("role") == planner.get("role") or record.get("output") == planner.get("output")
            or any(type(value.get(key)) is not bool for key in ("accepted", *flags))):
        raise ValueError("slice continuation requires a typed independent review outcome")
    reviewer_session = record.get("thread_id") or record["output"]
    planner_session = planner.get("thread_id") or planner["output"]
    worker = state.get("active_stage") or {}
    if (reviewer_session == planner_session or record.get("exit_code") != 0
            or (worker and (worker.get("output") != record["output"] or worker.get("exit_code") is None))
            or state.get("uncertain_artifacts") or state.get("pending_questions")
            or state.get("status") in ("WAITING_FOR_USER", "RESOLVER_PENDING")
            or state.get("intervention_ack_pending")):
        raise ValueError("progressive activation requires an independent reconciled safe boundary")
    report = {**value, "stage": stage, "role": record["role"], "session": reviewer_session,
              "candidate_sha256": transition["candidate"]["sha256"]}
    envelope, _ = artifacts.prepare("review", contract_token=token,
        predecessor_identity=previous["plan_hash"], candidate_identity=candidate["plan_identity"],
        plan_identity=candidate["plan_identity"], source_snapshot_identity=util.digest(current), report=report)
    identity = artifacts.persist(state["run_dir"], envelope)
    transition["last_review"] = identity
    if any(value[key] for key in flags) or transition.get("scope_issue"):
        scope = "permission" if value["permission_changes"] or transition.get("scope_issue") else "goal_change"
        decision = transition.get("scope_issue") or value["summary"]
        if not isinstance(decision, str) or not decision.strip():
            raise ValueError("material slice review needs the actual protected decision and impact")
        request = {"kind": scope, "discovered": value["summary"], "decision_needed": decision,
                   "impact": transition.get("scope_issue") or "This slice cannot activate within the currently approved product and permissions.",
                   "options": ["Keep the approved product and permissions", "Provide a reviewed material change"],
                   "proposed_delta": json.dumps(candidate["report"]["proposal"], indent=2)}
        transition.update(phase="detail", feedback=copy.deepcopy(value))
        return {"material_request": request, "next_stage": planner_stage,
                "evidence": {"hashes": {record["output"]: util.file_hash(Path(record["output"]))},
                             "candidate": transition["candidate"], "review": identity}}
    if not value["accepted"]:
        transition.update(phase="detail", feedback=copy.deepcopy(value))
        state.update(next_stage=planner_stage, phase="PLANNING", next_action="Revise the rejected slice in its existing allowance")
        return True
    receipt = {"authenticated": True, "accepted": True, "artifact": identity, "output_hash": util.digest(report),
        "stage": stage, "role": record["role"], "session": reviewer_session,
        "planner_stage": planner_stage, "planner_role": planner["role"], "planner_session": planner_session}
    prepared = activation.prepare_activation(run_dir=state["run_dir"], contract=state["goal_contract"],
        contract_authenticated=goals.approved(state), progressive=ledger, previous_plan=previous,
        candidate_artifact=transition["candidate"], review_artifact=identity, source_snapshot=current,
        review_receipt=receipt, verified_done=[row["slice_id"] for row in ledger["history"]],
        verified_criteria=ledger["completion_proof"]["criterion_ids"], required_checks=ledger["required_checks"],
        product_findings=product_findings, blockers={key: False for key in activation.BLOCKERS})
    prepared["required_checks"] = product_checklist(state["goal_contract"]["body"], prepared["required_checks"])
    for key in ("active", "required_checks", "outstanding_criteria"):
        ledger[key] = prepared[key]
    ledger["plan"] = {"proposal": candidate["report"]["proposal"], "plan_hash": candidate["plan_identity"]}
    ledger["active_allowance"] = ledger.pop("pending_allowance")
    ledger["active_allowance"]["slice_id"] = ledger["active"]["definition"]["id"]
    first = candidate["report"]["initial_task"]
    ledger.pop("transition")
    ledger.pop("completion_proof", None)
    return {"initial_task": first}

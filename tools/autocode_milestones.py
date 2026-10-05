"""Runner-owned milestone gates, evidence progress and bounded recovery.

This module never launches a provider or rewrites the approved product contract.
Old checkpoints opt in at a reconciled boundary; new runs enable it by default.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import datetime as dt
import fcntl
from pathlib import Path
import uuid

try:
    from . import autocode_util as s
    from .autocode_milestone_scope import fresh_validation, key, scope  # noqa: F401 (re-exported)
    from . import autocode_carryforward as carryforward
    from . import autocode_review_gate as review_gate
    from . import autocode_progressive_state as progressive
    from . import autocode_milestone_replan as replan
except ImportError:
    import autocode_util as s
    from autocode_milestone_scope import fresh_validation, key, scope  # noqa: F401 (re-exported)
    import autocode_carryforward as carryforward
    import autocode_review_gate as review_gate
    import autocode_progressive_state as progressive
    import autocode_milestone_replan as replan


DEFAULTS = {"enabled": True, "max_seconds": 5400, "stalled_reviews": 3, "max_replans": 1}
POLICY = """
ENFORCED MILESTONE CHECKPOINTS
Finish one observable outcome within the approved scope before starting another
milestone. Each task needs an objective, affected paths, requirements, criterion IDs
and an executable validation plan. The Builder may implement, test and fix within that task.
Every completed implementation handoff goes to the Validator, then the Plan Reviewer. Writer self-reports
cannot authorize advancement. The Validator's verdict covers the CURRENT milestone's outcome;
provide criterion evidence for all of its acceptance criteria. end_to_end_result
always covers the full approved user flow. For a partial milestone or batch it may
remain NOT_VERIFIED while later milestones are unfinished; explain what remains.
Report other, unbuilt criteria as NOT_VERIFIED without treating them as milestone
defects. Before overall COMPLETE, validate every contract criterion and the complete
approved flow on the current artifact. Never weaken the full-task completion gate.
For that final validation, assign a kind=validate task on the current milestone that lists
every contract criterion: a validate task may recheck accepted milestones' criteria.
The Plan Reviewer may advance only with current independent evidence for the entire milestone,
no blocking findings in its approved scope and any required human reviews. Later-milestone
findings remain open and still block their own milestones and final completion.
milestone_checkpoint.current_evidence_ready
is the freshly evaluated evidence gate, not an acceptance decision. The checkpoint's
blocker and rejected_advances describe historical attempts, not the current gate.
When current evidence is ready, propose the next eligible milestone; the runner
accepts the current milestone as part of that transition. Do not wait for it to be
marked accepted before proposing advancement. After repeated reviews with no
new passing criteria, inspect milestone_checkpoint and choose an evidence-backed
REWORK with a materially different approach or a smaller implementation batch within
the SAME milestone. Do not rename a milestone or drop criteria to reset the budget.
Advance only to a milestone whose depends_on milestones are all accepted under the
current contract; the runner rejects assignments with unaccepted prerequisites.
Carried milestones are scheduling checkpoints with recorded prior-revision
provenance. Select unfinished work or final integration validation instead of
reimplementing them. Their old evidence never satisfies final completion of the
new contract; validate every criterion and the full flow before COMPLETE.
The runner allows one such automatic replan before pausing persistent failure.
Budget exhaustion stops additional writing at a saved boundary; the Validator and Plan Reviewer may
still verify finished work. File edits and reworded reports alone are not progress.
If only a declared human artifact review remains, report the verified findings in
a normal advancement decision. The runner presents its own review control; do
not ask permission to create that control or claim the user already approved.
"""


def route_review_only_request(state, request, origin):
    """Turn a model's review permission into a runner gate or technical retry."""
    if not enabled(state) or not isinstance(origin, dict) or origin.get("stage") != "astra_review":
        return copy.deepcopy(request)
    output = origin.get("output")
    if not output or not Path(output).is_file():
        return copy.deepcopy(request)
    try:
        report = s.read(output)
        from . import autocode_goals as goals
        from . import autocode_findings as findings
    except ImportError:
        import autocode_goals as goals
        import autocode_findings as findings
    except (OSError, ValueError):
        return copy.deepcopy(request)
    required = set(scope(state)["acceptance_criteria"]).intersection(goals.missing_human_reviews(state))
    if not review_gate.review_only_permission(report, request, required) or not goals.approved(state):
        return copy.deepcopy(request)
    current = s.snapshot(Path(state["workspace"]))
    ready = evidence_ready(state, current)
    if not ready:
        fresh = fresh_validation(state, current)
        blockers = findings.blocking_for_milestone(state, scope(state))
        state["milestone_blocker"] = ("Current independent evidence is stale" if not fresh else
                                      "Current milestone still has open findings: " +
                                      ", ".join(row["id"] for row in blockers))
        state.update(status="RUNNING", phase="READY_TO_EXECUTE",
                     next_stage="sol" if not fresh else "astra_review", pending_questions=[])
        state.pop("user_request", None)
        return None
    return {"kind": "human_review", "criteria": sorted(required),
            "decision_needed": "Review the verified current milestone before it advances",
            "impact": "The approved contract requires your review of this validated artifact",
            "options": [], "discovered": "Independent evidence passed; human review remains",
            "proposed_delta": ""}


def recover_review_only_request(state, public, *, ask_user):
    """Retire an authenticated review-only permission and re-enter the real gate.

    ask_user is autocode_goal_lifecycle.wait_for_user, passed in because that module imports this one."""
    if not public or public.get("scope") != "permission":
        return False
    try:
        from . import autocode_resolver_human as human
    except ImportError:
        import autocode_resolver_human as human
    entry = state.get("resolver", {}).get("human_escalations", {}).get(public["request_id"], {})
    origin = entry.get("identity", {}).get("proposal", {}).get("origin", {})
    request = public.get("request", {})
    if route_review_only_request_preview(state, request, origin) is False:
        return False
    if human.current(state) != public:
        return False
    entry.update(status="superseded", superseded_at=s.now(),
                 superseded_reason="Review-only permission replaced by the runner's evidence gate")
    state.pop(human.PUBLIC, None)
    state.pop("user_request", None)
    state["pending_questions"] = []
    ask_user(state, request, origin=origin, next_stage="astra_review")
    state.setdefault("user_events", []).append({"kind": "review_request_rerouted", "actor": "runner",
        "at": s.now(), "old_request_id": public["request_id"], "milestone_id": scope(state)["id"]})
    return True


def route_review_only_request_preview(state, request, origin):
    """Check report identity before retiring a published request."""
    output = origin.get("output")
    if origin.get("stage") != "astra_review" or not output or not Path(output).is_file():
        return False
    try:
        report = s.read(output)
    except (OSError, ValueError):
        return False
    required = {row["id"] for row in state.get("goal_contract", {}).get("body", {}).get("acceptance_criteria", [])
                if row.get("human_review") and row["id"] in scope(state)["acceptance_criteria"]}
    return review_gate.review_only_permission(report, request, required)


def enabled(state):
    return state.get("settings", {}).get("milestone_checkpoints", {}).get("enabled") is True


def settings(state):
    return {**DEFAULTS, **state.get("settings", {}).get("milestone_checkpoints", {})}


def progress(state):
    task = state.get("current_task", {})
    if not task or task.get("contract_hash") != state.get("goal_contract", {}).get("hash"):
        return None
    milestone = scope(state)
    return state.setdefault("milestone_progress", {}).setdefault(key(state), {
        **copy.deepcopy(milestone), "contract_hash": task["contract_hash"],
        "seconds": 0, "seconds_by_role": {}, "best_passed": [], "reviews_without_progress": 0,
        "replans": 0, "reviews": [], "accepted": False,
    })


def account(state, record):
    if not enabled(state):
        return
    row = progress(state)
    if row is None or record.get("task_id") != state.get("current_task", {}).get("id"):
        return
    seconds = record.get("duration_seconds", 0) or 0
    row["seconds"] += seconds
    role = record.get("role", "unknown")
    row["seconds_by_role"][role] = row["seconds_by_role"].get(role, 0) + seconds


def evidence_ready(state, current):
    if not fresh_validation(state, current):
        return False
    val = state["validation"]
    required = set(scope(state)["acceptance_criteria"])
    results = {r["id"]: r for r in val.get("criterion_results", [])}
    flow = val.get("end_to_end_result", {})
    all_criteria = {c["id"] for c in state["goal_contract"]["body"]["acceptance_criteria"]}
    partial_scope = required < all_criteria
    flow_ready = bool(flow.get("status") == "PASS" and flow.get("summary", "").strip()
                      and flow.get("evidence_refs"))
    # Partial acceptance unlocks downstream work, not whole-task completion.
    # A known flow failure still blocks; only unfinished verification may wait.
    if partial_scope and flow.get("status") == "NOT_VERIFIED" and flow.get("summary", "").strip():
        flow_ready = True
    members = state.get("current_task", {}).get("milestone_ids", [])
    if members:
        results_by_milestone = {r["milestone_id"]: r for r in val.get("milestone_results", [])}
        if set(results_by_milestone) != set(members) or any(
                r.get("status") != "PASS" or not r.get("summary", "").strip() or not r.get("evidence_refs")
                for r in results_by_milestone.values()):
            return False
    try:
        from . import autocode_goals as goals
    except ImportError:
        import autocode_goals as goals
    human_ids = [c["id"] for c in state["goal_contract"]["body"]["acceptance_criteria"] if c["human_review"]]
    human_only_gap = (bool(human_ids) and goals.human_only_pending_validation(state, val, human_ids[0])
                      and not goals.missing_human_reviews(state))
    try:
        from . import autocode_findings as findings_ledger
        ledger_blocking = findings_ledger.blocking_for_milestone(state, scope(state))
    except ImportError:
        import autocode_findings as findings_ledger
        ledger_blocking = findings_ledger.blocking_for_milestone(state, scope(state))
    return bool(required and (val.get("verdict") == "PASS" or human_only_gap) and val.get("checks")
        and all(c["exit_code"] == 0 for c in val["checks"])
        and not any(f.get("blocking", True) or f["severity"] in ("critical", "high") for f in val.get("findings", []))
        and not ledger_blocking
        and (not required.intersection(val.get("unverified_criteria", [])) or human_only_gap)
        and all((results.get(cid, {}).get("status") == "PASS" or
                 (human_only_gap and cid == human_ids[0])) and results[cid].get("evidence_refs") for cid in required)
        and (flow_ready or (human_only_gap and flow.get("status") == "NOT_VERIFIED")))


def release_obsolete_gate_request(state, published):
    """Retire a former gate's permission question after the corrected gate passes.

    This does not accept the milestone. The Plan Reviewer must still request
    advancement, which invokes before_assignment and records normal provenance.
    """
    if (not isinstance(published, dict) or published.get("scope") != "permission"
            or state.get("status") != "WAITING_FOR_USER" or state.get("next_stage") != "astra_review"
            or state.get("active_stage") or len(state.get("pending_questions") or []) != 1
            or not str(state.get("milestone_blocker", "")).startswith(
                "Current milestone needs independent passing evidence before advancement")):
        return False
    request = published.get("request") or {}
    current_scope = scope(state)
    milestone_id = current_scope.get("id", "")
    question = state["pending_questions"][0]
    decision = str(request.get("decision_needed", "")).lower()
    delta = str(request.get("proposed_delta", "")).lower()
    if (not milestone_id or milestone_id.startswith("batch:") or request.get("kind") != "permission"
            or question.get("question") != request.get("decision_needed")
            or f"registration of {milestone_id.lower()}" not in decision
            or "operator/runner-owned" not in decision or "accepted" not in decision
            or "runner-owned milestone-acceptance state" not in delta
            or f"record {milestone_id.lower()}" not in delta or "as accepted" not in delta):
        return False
    entry = state.get("resolver", {}).get("human_escalations", {}).get(published.get("request_id"))
    origin = ((entry or {}).get("identity") or {}).get("proposal", {}).get("origin", {})
    task = state.get("current_task") or {}
    validation = state.get("validation") or {}
    if (not entry or entry.get("status") != "pending" or origin.get("stage") != "astra_review"
            or origin.get("task_id") != task.get("id")
            or origin.get("source_revision") != validation.get("source_revision")):
        return False
    try:
        from . import autocode_goals as goals
    except ImportError:
        import autocode_goals as goals
    if not goals.approved(state):
        return False
    try:
        current = s.snapshot(Path(state["workspace"]))
    except (KeyError, OSError, RuntimeError, ValueError):
        return False
    if (not evidence_ready(state, current)
            or set(current_scope["acceptance_criteria"]).intersection(goals.missing_human_reviews(state))):
        return False
    reason = "The current milestone now has fresh independent passing evidence; runner acceptance needs no permission"
    entry.update(status="superseded", superseded_at=s.now(), superseded_reason=reason)
    state.setdefault("user_events", []).append({"kind": "milestone_gate_request_superseded", "actor": "runner",
        "at": s.now(), "request_id": published["request_id"], "milestone_id": milestone_id,
        "contract_hash": state["goal_contract"]["hash"], "source_revision": current["revision"]})
    state.pop("resolver_human_request", None)
    state.pop("user_request", None)
    state.pop("milestone_blocker", None)
    state.pop("stop_reason", None)
    state.update(status="RUNNING", phase="READY_TO_EXECUTE", pending_questions=[])
    return True


def approach(task):
    # Compare substantive task fields, not random task IDs, timestamps or prose
    # evidence references. Semantic adequacy remains the Plan Reviewer's responsibility.
    return s.digest({field: task.get(field) for field in
                     ("objective", "affected_paths", "requirements", "validation_plan")})


def observe_validation(state, current):
    if not enabled(state) or not fresh_validation(state, current):
        return
    row = progress(state)
    if row is None:
        return
    val = state["validation"]
    receipt = s.digest({field: val.get(field) for field in
                        ("output", "source_revision", "task_id", "evidence_hashes")})
    if any(r["receipt"] == receipt for r in row["reviews"]):
        return
    required = set(row["acceptance_criteria"])
    passed = {r["id"] for r in val["criterion_results"]
              if r["id"] in required and r["status"] == "PASS" and r["evidence_refs"]}
    improved = bool(passed - set(row["best_passed"]))
    if progressive.enabled(state):
        # classify_validation disclaims pre-approval states (returns None);
        # an unclassified review must not count as progress.
        verdict = progressive.classify_validation(state, current)
        improved = bool(verdict) and verdict["kind"] == "progress"
    row["best_passed"] = sorted(set(row["best_passed"]) | passed)
    ready = evidence_ready(state, current)
    progresses = improved if progressive.armed(state) else improved or ready
    row["reviews_without_progress"] = 0 if progresses else row["reviews_without_progress"] + 1
    row["last_approach"] = approach(state["current_task"])
    row["reviews"].append({"receipt": receipt, "output": val["output"], "source_revision": current["revision"],
                           "passed": sorted(passed), "remaining": sorted(required - passed), "ready": ready})
    stalled_limit = settings(state)["stalled_reviews"]
    row["needs_replan"] = bool(stalled_limit and row["reviews_without_progress"] >= stalled_limit)


def before_assignment(state, decision, current):
    if not enabled(state):
        return
    spec = decision["next_task"]
    if not spec["milestone_id"].strip() or not decision.get("affected_paths"):
        raise ValueError("Milestone tasks require a named milestone and explicit affected paths")
    row = progress(state)
    if row is None:
        return
    if spec["milestone_id"] not in row.get("milestone_ids", [row["id"]]):
        if not evidence_ready(state, current):
            try:
                from . import autocode_findings as findings_ledger
            except ImportError:
                import autocode_findings as findings_ledger
            blockers = findings_ledger.blocking_for_milestone(state, scope(state))
            labels = []
            for item in blockers:
                saved = item.get('scope')
                saved = saved if isinstance(saved, dict) else {}
                criteria = saved.get('criteria')
                criterion_label = ', '.join(criteria) if isinstance(criteria, list) and all(
                    isinstance(cid, str) for cid in criteria) else 'unspecified criteria'
                labels.append(f"{item.get('id', '?')} ({saved.get('milestone_id') or 'unscoped'}: {criterion_label})")
            detail = "; blocking findings in current scope: " + ", ".join(labels) if labels else ""
            raise s.Paused("PAUSED_MILESTONE_EVIDENCE", "Current milestone needs independent passing evidence before advancement" + detail)
        try:
            from . import autocode_goals as goals
        except ImportError:
            import autocode_goals as goals
        if set(row["acceptance_criteria"]).intersection(goals.missing_human_reviews(state)):
            raise s.Paused("PAUSED_MILESTONE_HUMAN_REVIEW", "Current milestone requires the recorded human artifact reviews")
        accept(state, current)
        return
    allowed = set(row["acceptance_criteria"]) | (recheckable(state) if spec["kind"] == "validate" else set())
    if not set(spec["acceptance_criteria"]) <= allowed:
        raise ValueError("A saved milestone cannot silently expand its criteria")
    if spec['kind'] == 'implement':
        check_budget(state)
    # The Completion Owner's prompt states this same gate: autocode_milestone_replan.constraint.
    gate = replan.pending(row, settings(state))
    if gate == replan.EXHAUSTED:
        raise s.Paused("PAUSED_MILESTONE_STALLED", "Milestone still fails after bounded replanning; inspect the saved failing evidence")
    if gate == replan.REQUIRED:
        proposed = {**spec, "objective": decision["next_objective"], "affected_paths": decision["affected_paths"]}
        if (decision["status"] != "REWORK" or not decision.get("evidence")
                or approach(proposed) == row.get("last_approach")):
            raise s.Paused("PAUSED_MILESTONE_REPLAN", "Repeated failed criteria require an evidence-backed REWORK with a changed approach or smaller batch")
        row["replans"] += 1
        row["reviews_without_progress"] = 0
        row["needs_replan"] = False
        state['no_progress_batches'] = 0


def accepted_ids(state):
    contract_hash = state.get("goal_contract", {}).get("hash")
    accepted = {mid for r in state.get("milestone_progress", {}).values()
            if r.get("accepted") and r.get("contract_hash") == contract_hash
            for mid in r.get("milestone_ids", [r["id"]])}
    return carryforward.current_ids(state, accepted)


def recheckable(state):
    """Criteria of milestones accepted under the current contract. A validate task may check them again:
    final completion needs one validation passing every criterion, and validating changes no code."""
    milestones = {m["id"]: m for m in state.get("goal_contract", {}).get("body", {}).get("milestones", [])}
    return {c for mid in accepted_ids(state) for c in milestones.get(mid, {}).get("acceptance_criteria", [])}


def require_prerequisites(state, milestone_id):
    """Require current acceptance, including explicitly proven carry-forward."""
    if not enabled(state):
        return
    milestones = {m["id"]: m for m in state.get("goal_contract", {}).get("body", {}).get("milestones", [])}
    missing = set(milestones.get(milestone_id, {}).get("depends_on", [])) - accepted_ids(state)
    if missing:
        raise ValueError(f"Milestone {milestone_id} cannot start until its prerequisites are accepted: "
                         + ", ".join(sorted(missing)))


def accept(state, current):
    row = progress(state)
    if row is not None:
        manifest, reason = carryforward.capture(state, row, current) if evidence_ready(state, current) else (None, 'No fresh acceptance evidence')
        row.update(accepted=True, accepted_at=s.now(), accepted_source_revision=current["revision"],
                   accepted_validation=copy.deepcopy(state["validation"]))
        if manifest:
            row['reuse_manifest'] = manifest
            row.pop('carry_forward_unavailable', None)
        else:
            row.pop('reuse_manifest', None)
            row['carry_forward_unavailable'] = reason
        for member in row.get("members", []):
            member_key = f"{row['contract_hash']}:{member['id']}"
            saved = state["milestone_progress"].setdefault(member_key, copy.deepcopy(member))
            saved.update(contract_hash=row["contract_hash"], accepted=True, accepted_at=row["accepted_at"],
                         accepted_source_revision=current["revision"], accepted_batch=row["id"],
                         accepted_validation=copy.deepcopy(state["validation"]))


def check_budget(state):
    # Pre-approval, check_local_budget disclaims the run; the ordinary
    # milestone budget must keep applying until progressive authority exists.
    if progressive.armed(state):
        progressive.check_local_budget(state)
        return
    row = progress(state)
    limit = settings(state)["max_seconds"]
    if row and limit and row["seconds"] >= limit:
        raise s.Paused("PAUSED_MILESTONE_BUDGET", "Milestone active-time budget exhausted; verify existing work or explicitly raise --max-milestone-seconds")


def dispatch_guard(state, stage):
    if not enabled(state):
        return
    if state.get("settings", {}).get("workflow"):
        raise s.Paused("PAUSED_WORKFLOW_CONFLICT", "Milestone checkpoints require Builder → Validator → Plan Reviewer routing")
    if stage in ("terra", "orchestrator"):
        row = progress(state)
        if row is None:
            raise s.Paused("PAUSED_MILESTONE_TASK", "The Plan Reviewer must assign a bounded milestone task before implementation")
        check_budget(state)
        if row.get("needs_replan") and settings(state)["stalled_reviews"]:
            raise s.Paused("PAUSED_MILESTONE_REPLAN", "The Plan Reviewer must reassess repeated failed checks before another writer attempt")


def handle_gate(state, error, current, *, ask_user, origin=None):
    """Keep a rejected advancement in the review loop; never replay the Builder.

    ask_user is autocode_goal_lifecycle.wait_for_user, passed in because that module imports this one."""
    if error.status in ("PAUSED_MILESTONE_STALLED", "PAUSED_MILESTONE_BUDGET"):
        state.update(status=error.status, phase="PAUSED_OR_BLOCKED", next_stage="astra_review", stop_reason=str(error))
        return
    if error.status not in ("PAUSED_MILESTONE_EVIDENCE", "PAUSED_MILESTONE_HUMAN_REVIEW", "PAUSED_MILESTONE_REPLAN"):
        raise error
    state["milestone_blocker"] = str(error)
    if error.status == "PAUSED_MILESTONE_HUMAN_REVIEW":
        try:
            from . import autocode_goals as goals
        except ImportError:
            import autocode_goals as goals
        required = set(scope(state)["acceptance_criteria"])
        ask_user(state,
            {"kind": "human_review", "criteria": sorted(required.intersection(goals.missing_human_reviews(state))),
             "decision_needed": "Review the current milestone artifact before advancing",
             "impact": "Advancement requires the declared human acceptance of this validated milestone",
             "options": [], "discovered": str(error), "proposed_delta": ""},
            origin=origin or {'stage': 'milestone_gate', 'source_revision': current['revision']},
            next_stage='astra_review')
        return
    row = progress(state)
    row["rejected_advances"] = row.get("rejected_advances", 0) + 1
    if settings(state)["stalled_reviews"] and row["rejected_advances"] >= settings(state)["stalled_reviews"]:
        state.update(status="PAUSED_MILESTONE_REPLAN", phase="PAUSED_OR_BLOCKED", stop_reason=str(error))
    # A fresh FAIL whose milestone criteria already passed is the runner's proof, not a
    # review decision. The Validator re-checks. A failed criterion stays with the reviewer.
    fresh = fresh_validation(state, current)
    validation = state.get("validation") or {}
    required = set(scope(state)["acceptance_criteria"])
    results = {row["id"]: row for row in validation.get("criterion_results") or []}
    criteria_passed = bool(required) and all(results.get(cid, {}).get("status") == "PASS" for cid in required)
    proof_disagrees = validation.get("verdict") != "PASS" and criteria_passed
    state["next_stage"] = "astra_review" if fresh and not proof_disagrees else "sol"


def activate(state):
    """Explicit operator opt-in after the runner reconciles and locks the run."""
    if any(state.get(k) for k in ("active_stage", "pending_report_repair", "uncertain_artifacts")):
        raise ValueError("Reconcile the in-flight stage before enabling milestone checkpoints")
    if enabled(state):
        return
    previous = state["settings"].pop("workflow", None)
    state["settings"]["milestone_checkpoints"] = copy.deepcopy(DEFAULTS)
    state.setdefault("user_events", []).append({"kind": "milestone_checkpoints_enabled", "actor": "user_cli", "at": s.now(),
                                               "previous_workflow": previous})
    state.pop("targeted_consultation", None)
    state.pop("final_audit_request", None)
    if state.get("current_task"):
        progress(state)
    if state.get("goal_contract", {}).get("approval_status") == "approved" and state["status"] != "TASK_COMPLETE":
        state["next_stage"] = "sol" if state.get("current_task") else "astra_plan"


@contextmanager
def request_lock(run_dir):
    with (run_dir / 'milestone-request.lock').open('a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another milestone request is being saved; retry')
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def queue_activation(run_dir, max_seconds=None):
    """Opt-in mailbox usable while another process owns the workspace lock."""
    with request_lock(run_dir):
        request_path = run_dir / 'milestone-checkpoints-requested.json'
        if request_path.exists():
            request = s.read(request_path)
            if max_seconds is not None and request['max_seconds'] != max_seconds:
                raise ValueError('A different milestone budget is already queued')
        else:
            request = {'version': 1, 'id': uuid.uuid4().hex, 'actor': 'user_cli', 'requested_at': s.now(),
                       'max_seconds': DEFAULTS['max_seconds'] if max_seconds is None else max_seconds}
            s.atomic_json(request_path, request)
        # Preserve a preexisting operator pause. Only our owned marker is removed
        # after durable activation; a crash between writes is recoverable by retry.
        try:
            with (run_dir / 'pause-requested').open('x') as handle:
                handle.write('milestone-checkpoints:' + request['id'])
        except FileExistsError:
            pass
        return request


def apply_queued_activation(state, run_dir):
    """Caller owns the workspace lock and has reconciled terminal artifacts."""
    path = run_dir / 'milestone-checkpoints-requested.json'
    if not path.exists():
        return False
    if state.get('pending_report_repair'):
        # Complete the bounded, read-only repair under its original role/schema
        # before changing routing. The request and pause marker stay durable.
        return False
    with request_lock(run_dir):
        if not path.exists():
            return False
        request = s.read(path)
        if (request.get('version') != 1 or request.get('actor') != 'user_cli'
                or not isinstance(request.get('id'), str) or not request['id']
                or type(request.get('max_seconds')) is not int or request['max_seconds'] < 0):
            raise ValueError('Invalid queued milestone configuration')
        applied = state.setdefault('milestone_activation_requests', [])
        if request['id'] not in applied:
            activate(state)
            state['settings']['milestone_checkpoints']['max_seconds'] = request['max_seconds']
            applied.append(request['id'])
            # This is already a safe stage boundary: terminal artifacts were
            # reconciled, the new settings are durably checkpointed below, and
            # the caller still owns the single-writer lock.  Preserve a real
            # user/goal pause, but do not manufacture a second manual-resume
            # gate for an otherwise running orchestration loop.
            if state.get('status') == 'RUNNING':
                state['phase'] = 'EXECUTING'
                state.pop('stop_reason', None)
            s.atomic_json(run_dir / 'state.json', state)
        pause = run_dir / 'pause-requested'
        if pause.exists() and pause.read_text() == 'milestone-checkpoints:' + request['id']:
            pause.unlink()
        path.unlink()
        return True


def owns_pause(run_dir):
    request = run_dir / 'milestone-checkpoints-requested.json'
    pause = run_dir / 'pause-requested'
    return bool(request.exists() and pause.exists()
                and pause.read_text() == 'milestone-checkpoints:' + s.read(request).get('id', ''))


def summary(state):
    """Read-only status; durations are provider-stage elapsed time, not billing."""
    roles = {}
    seen = set()
    for record in state.get("stages", []):
        identity = record.get("output")
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        role = record.get("role", "unknown")
        roles[role] = roles.get(role, 0) + (record.get("duration_seconds") or 0)
    active = state.get('active_stage') or {}
    active_seconds = 0
    if active.get('output') not in seen and active.get('started_at'):
        try:
            active_seconds = active.get('duration_seconds')
            if active_seconds is None:
                started = dt.datetime.fromisoformat(active['started_at'])
                active_seconds = max(0, (dt.datetime.now(dt.timezone.utc) - started).total_seconds())
        except (TypeError, ValueError):
            active_seconds = 0
    row = state.get("milestone_progress", {}).get(key(state))
    return {"enabled": enabled(state), "seconds_by_role": roles, "hours_by_role": {r: round(t / 3600, 3) for r, t in roles.items()},
            "current": copy.deepcopy({k: v for k, v in row.items() if k not in ("accepted_validation", "reviews", "reuse_manifest")} if row else None),
            "carry_forward": copy.deepcopy(next((audit for audit in reversed(state.get('milestone_carry_forward', []))
                                                   if audit['to_contract_hash'] == state.get('goal_contract', {}).get('hash')), None)),
            "limits": settings(state) if enabled(state) else None,
            "blocker": state.get("milestone_blocker"),
            "active_stage_role": active.get('role'), "active_stage_elapsed_seconds": active_seconds,
            "accepted_milestones": sorted(accepted_ids(state))}


def status_line(state):
    info = summary(state)
    row = info['current']
    if not info['enabled'] or row is None:
        return ''
    durations = ', '.join(f'{role} {seconds / 3600:.2f}h' for role, seconds in info['seconds_by_role'].items())
    limit = info['limits']['max_seconds']
    budget = f'{limit / 3600:.2f}h' if limit else 'unlimited'
    return (f"Milestone {row['id']}: {row['seconds'] / 3600:.2f}h / {budget}; "
            f"reviews without progress {row['reviews_without_progress']}; replans {row['replans']}. "
            f"Recorded stage time: {durations}")

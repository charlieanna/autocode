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
    from . import autocode_support as s
except ImportError:
    import autocode_support as s


DEFAULTS = {"enabled": True, "max_seconds": 5400, "stalled_reviews": 3, "max_replans": 1}
POLICY = """
ENFORCED MILESTONE CHECKPOINTS
Finish one observable outcome within the approved scope before starting another
milestone. Each task needs an objective, affected paths, requirements, criterion IDs
and an executable validation plan. Terra may implement, test and fix within that task.
Every completed implementation handoff goes to Sol, then Astra. Writer self-reports
cannot authorize advancement. Sol's verdict and end_to_end_result cover the CURRENT
milestone's outcome; provide criterion evidence for all of its acceptance criteria.
Report other, unbuilt criteria as NOT_VERIFIED without treating them as milestone
defects. Before overall COMPLETE, validate every contract criterion and the complete
approved flow on the current artifact. Never weaken the full-task completion gate.
Astra may advance only with current independent evidence for the entire milestone,
no blocking findings and any required human reviews. After repeated reviews with no
new passing criteria, inspect milestone_checkpoint and choose an evidence-backed
REWORK with a materially different approach or a smaller implementation batch within
the SAME milestone. Do not rename a milestone or drop criteria to reset the budget.
Advance only to a milestone whose depends_on milestones are all accepted under the
current contract; the runner rejects assignments with unaccepted prerequisites.
The runner allows one such automatic replan before pausing persistent failure.
Budget exhaustion stops additional writing at a saved boundary; Sol and Astra may
still verify finished work. File edits and reworded reports alone are not progress.
"""


def enabled(state):
    return state.get("settings", {}).get("milestone_checkpoints", {}).get("enabled") is True


def settings(state):
    return {**DEFAULTS, **state.get("settings", {}).get("milestone_checkpoints", {})}


def key(state, task=None):
    task = task if task is not None else state.get("current_task", {})
    if task.get("milestone_ids"):
        return f"{state.get('goal_contract', {}).get('hash', '')}:batch:{s.digest(sorted(task['milestone_ids']))[:16]}"
    return f"{state.get('goal_contract', {}).get('hash', '')}:{task.get('milestone_id', '')}"


def scope(state, task=None):
    task = task if task is not None else state.get("current_task", {})
    if task.get("milestone_ids"):
        members = [m for m in state.get("goal_contract", {}).get("body", {}).get("milestones", [])
                   if m["id"] in task["milestone_ids"]]
        return {"id": "batch:" + s.digest(sorted(task["milestone_ids"]))[:16],
                "milestone_ids": list(task["milestone_ids"]), "members": members,
                "objective": "; ".join(m["objective"] for m in members),
                "acceptance_criteria": list(dict.fromkeys(c for m in members for c in m["acceptance_criteria"]))}
    for milestone in state.get("goal_contract", {}).get("body", {}).get("milestones", []):
        if milestone["id"] == task.get("milestone_id"):
            return milestone
    # Existing sealed briefs may predate milestone definitions. Freeze the scope
    # of their existing bounded task, rather than inventing or approving a brief.
    saved = state.get("milestone_progress", {}).get(key(state, task))
    return {"id": task.get("milestone_id", ""), "objective": task.get("objective", ""),
            "acceptance_criteria": list(saved["acceptance_criteria"] if saved else task.get("acceptance_criteria", []))}


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


def fresh_validation(state, current):
    val = state.get("validation", {})
    contract = state.get("goal_contract", {})
    pins = val.get("evidence_hashes", {})
    return bool(val.get("reviewer_role") == "sol" and pins
        and val.get("contract_hash") == contract.get("hash")
        and val.get("contract_revision") == contract.get("revision")
        and val.get("criteria_revision") == state.get("criteria_revision")
        and val.get("task_id") == state.get("current_task", {}).get("id")
        and val.get("source_revision") == current["revision"]
        and all(Path(p).is_file() and s.file_hash(p) == digest for p, digest in pins.items()))


def evidence_ready(state, current):
    if not fresh_validation(state, current):
        return False
    val = state["validation"]
    required = set(scope(state)["acceptance_criteria"])
    results = {r["id"]: r for r in val.get("criterion_results", [])}
    flow = val.get("end_to_end_result", {})
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
    human_only_gap = (len(human_ids) == 1 and goals.human_only_pending_validation(state, val, human_ids[0])
                      and not goals.missing_human_reviews(state))
    return bool(required and (val.get("verdict") == "PASS" or human_only_gap) and val.get("checks")
        and all(c["exit_code"] == 0 for c in val["checks"])
        and not any(f.get("blocking", True) or f["severity"] in ("critical", "high") for f in val.get("findings", []))
        and (not required.intersection(val.get("unverified_criteria", [])) or human_only_gap)
        and all((results.get(cid, {}).get("status") == "PASS" or
                 (human_only_gap and cid == human_ids[0])) and results[cid].get("evidence_refs") for cid in required)
        and flow.get("status") == "PASS" and flow.get("summary", "").strip() and flow.get("evidence_refs"))


def approach(task):
    # Compare substantive task fields, not random task IDs, timestamps or prose
    # evidence references. Semantic adequacy remains Astra's responsibility.
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
    row["best_passed"] = sorted(set(row["best_passed"]) | passed)
    ready = evidence_ready(state, current)
    row["reviews_without_progress"] = 0 if improved or ready else row["reviews_without_progress"] + 1
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
            raise s.Paused("PAUSED_MILESTONE_EVIDENCE", "Current milestone needs independent passing evidence before advancement")
        try:
            from . import autocode_goals as goals
        except ImportError:
            import autocode_goals as goals
        if set(row["acceptance_criteria"]).intersection(goals.missing_human_reviews(state)):
            raise s.Paused("PAUSED_MILESTONE_HUMAN_REVIEW", "Current milestone requires the recorded human artifact reviews")
        accept(state, current)
        return
    if not set(spec["acceptance_criteria"]) <= set(row["acceptance_criteria"]):
        raise ValueError("A saved milestone cannot silently expand its criteria")
    if spec['kind'] == 'implement':
        check_budget(state)
    if row.get("needs_replan") and settings(state)["stalled_reviews"]:
        max_replans = settings(state)["max_replans"]
        if max_replans is not None and max_replans > 0 and row["replans"] >= max_replans:
            raise s.Paused("PAUSED_MILESTONE_STALLED", "Milestone still fails after bounded replanning; inspect the saved failing evidence")
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
    return {mid for r in state.get("milestone_progress", {}).values()
            if r.get("accepted") and r.get("contract_hash") == contract_hash
            for mid in r.get("milestone_ids", [r["id"]])}


def require_prerequisites(state, milestone_id):
    """Acceptance is pinned to the contract hash, so a revised brief re-earns its prerequisites."""
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
        row.update(accepted=True, accepted_at=s.now(), accepted_source_revision=current["revision"],
                   accepted_validation=copy.deepcopy(state["validation"]))
        for member in row.get("members", []):
            member_key = f"{row['contract_hash']}:{member['id']}"
            saved = state["milestone_progress"].setdefault(member_key, copy.deepcopy(member))
            saved.update(contract_hash=row["contract_hash"], accepted=True, accepted_at=row["accepted_at"],
                         accepted_source_revision=current["revision"], accepted_batch=row["id"],
                         accepted_validation=copy.deepcopy(state["validation"]))


def check_budget(state):
    row = progress(state)
    limit = settings(state)["max_seconds"]
    if row and limit and row["seconds"] >= limit:
        raise s.Paused("PAUSED_MILESTONE_BUDGET", "Milestone active-time budget exhausted; verify existing work or explicitly raise --max-milestone-seconds")


def dispatch_guard(state, stage):
    if not enabled(state):
        return
    if state.get("settings", {}).get("workflow"):
        raise s.Paused("PAUSED_WORKFLOW_CONFLICT", "Milestone checkpoints require Terra → Sol → Astra routing")
    if stage in ("terra", "orchestrator"):
        row = progress(state)
        if row is None:
            raise s.Paused("PAUSED_MILESTONE_TASK", "Astra must assign a bounded milestone task before implementation")
        check_budget(state)
        if row.get("needs_replan") and settings(state)["stalled_reviews"]:
            raise s.Paused("PAUSED_MILESTONE_REPLAN", "Astra must reassess repeated failed checks before another writer attempt")


def handle_gate(state, error, current):
    """Keep a rejected advancement in the review loop; never replay Terra."""
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
        state.update(status="WAITING_FOR_USER", phase="WAITING_FOR_USER", next_stage="astra_review",
                     user_request={"kind": "human_review", "criteria": sorted(required.intersection(goals.missing_human_reviews(state))),
                                   "decision_needed": "Review the current milestone artifact before advancing"})
        return
    row = progress(state)
    row["rejected_advances"] = row.get("rejected_advances", 0) + 1
    if settings(state)["stalled_reviews"] and row["rejected_advances"] >= settings(state)["stalled_reviews"]:
        state.update(status="PAUSED_MILESTONE_REPLAN", phase="PAUSED_OR_BLOCKED", stop_reason=str(error))
    state["next_stage"] = "astra_review" if fresh_validation(state, current) else "sol"


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
            "current": copy.deepcopy({k: v for k, v in row.items() if k not in ("accepted_validation", "reviews")} if row else None),
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

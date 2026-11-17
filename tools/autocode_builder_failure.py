"""Failure-bound Builder evidence and pre-escalation Investigator continuations.

Writes the existing stuck investigation record/history and pending_builder_failure:
queue defers that record during result acceptance; finalize consumes it only after
the matching completed record is saved. No retry grant or budget is created.
Classifications have their own budget per milestone (classified, policy.classification_limit);
they do not count toward the run's stuck investigations (#686).
builder_failure_hold retains non-execution/operator pauses; dispatch_guard reads
it before a writer or diagnosis call, and retires it only on changed bound work.
"""

from __future__ import annotations

import copy
import json
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_builder_policy as policy
    from . import autocode_failure_classification as classification
    from . import autocode_retained_work as retained_work
    from . import autocode_run_status as run_status
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
except ImportError:
    import autocode_builder_policy as policy
    import autocode_failure_classification as classification
    import autocode_retained_work as retained_work
    import autocode_run_status as run_status
    import autocode_source_scope as source_scope
    import autocode_util as util


def evidence(state: RunState, record, *, checks=(), checkpoint=None, error_class=None, diagnosis=None):
    record = copy.deepcopy(record)
    for key in ("output", "events", "diff_ref", "before_ref", "after_ref", "schema"):
        if record.get(key):
            record[key] = str(Path(record[key]).resolve())
    task = state.get("current_task") or {}
    contract = state.get("goal_contract") or {}
    refs = [
        record[key]
        for key in ("output", "events", "diff_ref", "after_ref")
        if record.get(key) and Path(record[key]).is_file()
    ]
    binding = {
        "task_id": task.get("id"),
        "task_digest": util.digest(task),
        "contract_hash": contract.get("hash"),
        "contract_revision": contract.get("revision"),
        "source_revision": record.get("source_revision"),
        "output": record.get("output"),
        "evidence_hashes": {path: util.file_hash(path) for path in refs},
    }
    # attempt_id survives a runner-owned artifact archive; aliases never create
    # a second incident. Pins still bind the concrete files the probe may inspect.
    identity = {key: value for key, value in binding.items() if key not in ("output", "evidence_hashes")}
    identity["attempt"] = record.get("attempt_id") or (
        {key: record.get(key) for key in ("stage", "iteration", "started_at")}
        if record.get("started_at")
        else binding["output"]
    )
    identity["evidence_hashes"] = sorted(binding["evidence_hashes"].values())
    return {
        "record": record,
        "error_class": error_class,
        "checks": copy.deepcopy(list(checks)),
        "checkpoint": copy.deepcopy(checkpoint),
        "output_probe": record.get("output_probe"),
        "failure_id": util.digest(identity),
        "evidence_refs": refs,
        "diagnosis": copy.deepcopy(diagnosis),
        "binding": binding,
    }


def guard(state: RunState, request, workspace):
    bound = request["failure_evidence"]["binding"]
    task, contract = state.get("current_task") or {}, state.get("goal_contract") or {}
    current = source_scope.snapshot(workspace, state)["revision"]
    if (
        not bound.get("task_id")
        or not bound.get("source_revision")
        or bound.get("output") not in bound["evidence_hashes"]
        or bound["task_id"] != task.get("id")
        or bound["task_digest"] != util.digest(task)
        or request["failure_evidence"]["record"].get("task_id", bound["task_id"]) != bound["task_id"]
        or bound["contract_hash"] != contract.get("hash")
        or bound["contract_revision"] != contract.get("revision")
        or bound["source_revision"] != current
        or any(
            not Path(path).is_file() or util.file_hash(path) != digest
            for path, digest in bound["evidence_hashes"].items()
        )
    ):
        raise util.Paused("PAUSED_STALE_HANDOFF", "Builder failure diagnosis identity or evidence changed")


def hold(state: RunState, evidence, status, reason):
    state["builder_failure_hold"] = {
        "binding": copy.deepcopy(evidence["binding"]),
        "failure_id": evidence["failure_id"],
        "status": status,
        "reason": reason,
    }
    run_status.pause(state, status, reason, next_stage="builder")


def dispatch_guard(state: RunState, stage, workspace):
    held = state.get("builder_failure_hold")
    if not held or stage not in ("builder", "orchestrator", "resolve", "investigate_stuck"):
        return
    bound = held["binding"]
    task, contract = state.get("current_task") or {}, state.get("goal_contract") or {}
    if (
        bound["task_digest"] != util.digest(task)
        or bound["contract_hash"] != contract.get("hash")
        or bound["contract_revision"] != contract.get("revision")
        or bound["source_revision"] != source_scope.snapshot(workspace, state)["revision"]
    ):
        state.pop("builder_failure_hold")  # A changed assignment still passes every normal admission gate.
        return
    if (
        stage == "builder"
        and state.get("status") == "RUNNING"
        and held["status"] == "PAUSED_BUILDER_CLASSIFICATION"
        and held["reason"].startswith("Builder completed an implementation attempt without source changes")
        and not held.get("classification_retry")
        and not any(state.get(key) for key in ("active_stage", "uncertain_artifacts", "pending_report_repair"))
    ):
        record = next(
            (row for row in reversed(state.get("stages") or []) if row.get("output") == bound.get("output")), None
        )
        if record and record.get("exit_code") == 0 and not record.get("changed_files") and not record.get("rejected"):
            guard(state, {"failure_evidence": {"binding": bound, "record": record}}, workspace)
            if retained_work.fresh_candidate(state, record, bound["source_revision"]):
                # Explicit resume can retire an obsolete no-change hold after
                # reapproval provenance is reconciled. No attempt or proof credit
                # is granted: normal admission and fresh validation still follow.
                state.pop("builder_failure_hold")
                return
    raise util.Paused(held["status"], held["reason"])


def reconsiderable(state: RunState, record, workspace):
    """An unlaunched, source-bound no-change incident, never a failed check."""
    pending = state.get("stuck_investigation") or {}
    held = state.get("builder_failure_hold") or {}
    if pending:
        if (
            pending.get("mode") != "builder_failure"
            or pending.get("in_force")
            or state.get("next_stage") != "investigate_stuck"
            or any(
                (row.get("original_stage") or row.get("stage")) == "investigate_stuck"
                and row.get("started_at", "") >= pending.get("requested_at", "")
                for row in state.get("stages") or []
            )
        ):
            return None
        request = pending
    elif held and not held.get("classification_retry") and state.get("next_stage") == "builder":
        request = {**held, "failure_evidence": {"binding": held["binding"], "record": record}}
    else:
        return None
    if (
        request.get("status") != "PAUSED_BUILDER_CLASSIFICATION"
        or not request.get("reason", "").startswith(
            "Builder completed an implementation attempt without source changes"
        )
        or not record.get("output")
        or Path(request["failure_evidence"]["binding"]["output"]).resolve() != Path(record["output"]).resolve()
    ):
        return None
    guard(state, request, workspace)
    return request


def retire_reconsidered(state: RunState, request):
    """Keep the obsolete incident in history; no classification call was spent."""
    if state.get("stuck_investigation") == request:
        state.pop("stuck_investigation")
        for row in state.get("stuck_investigations") or []:
            if row.get("identity") == request.get("identity"):
                row.update(outcome="candidate_retained", finished_at=util.now())
        state["phase"] = request.get("phase") or "BUILDING"
    else:
        state.pop("builder_failure_hold", None)


# Stops saved as a classification's failure before #1949, when a run-wide limit or a changed
# provider transport found at the stage boundary abandoned the request before its Investigator
# launched. Since #1949 such a stop keeps the request queued under its own pause, so only older
# runs hold these rows.
BOUNDARY_STOPS = (
    "Saved active-time limit reached at stage boundary",
    "Saved active-time limit reached before model access",
    "Saved iteration ceiling reached",
    "A joint-planning CLI/auth/provider configuration changed",
    "Local model/auth/provider settings differ from checkpoint",
)


def _boundary_stop(state: RunState, row) -> bool:
    """A failed row whose request a boundary limit stopped before any Investigator started."""
    error, started, finished = row.get("error") or "", row.get("requested_at"), row.get("finished_at")
    if (
        row.get("outcome") != "investigation_failed"
        or not isinstance(started, str)
        or not isinstance(finished, str)
        or not (
            error in BOUNDARY_STOPS
            or (error.startswith("Spend so far ") and error.endswith("cost cap at stage boundary"))
        )
    ):
        return False
    return not any(
        (record.get("original_stage") or str(record.get("stage", "")).removesuffix("_report_repair"))
        == "investigate_stuck"
        and started <= record.get("started_at", "") <= finished
        for record in state.get("stages") or []
    )


def classified(state: RunState):
    """This milestone's classification history rows; rows saved before #686 name no milestone.

    A request a boundary limit stopped before its Investigator started classified nothing and
    is not counted (#1949)."""
    milestone = policy.key(state)
    return [
        row
        for row in state.get("stuck_investigations") or []
        if row.get("trigger") == "builder_failure"
        and row.get("milestone_key") == milestone
        and row.get("outcome") != "candidate_retained"
        and not _boundary_stop(state, row)
    ]


def _other_repair(state: RunState, completed) -> bool:
    """A pending report repair other than the one this completed record is being accepted from.

    autocode_repaired_result applies a repaired report under its original record (``report_repaired``,
    the original's ``events``) and retires that repair just after; it does not hold the failure the
    report queues. A live run (ladder-24, claude-tiers, 2026-11-07) paused for operator action there.
    """
    pending = state.get("pending_report_repair")
    if not pending:
        return False
    events = (pending.get("original") or {}).get("events")
    return not (completed.get("report_repaired") and events and completed.get("events") == events)


def _stages(completed):
    """The active stage that completes this record: its own, or its report repair's when repaired."""
    stage = completed.get("stage")
    return (stage, f"{stage}_report_repair") if completed.get("report_repaired") else (stage,)


def queue(state: RunState, value, reason, *, enabled, completed_record=None):
    key = "builder-failure:" + value["failure_id"]
    history = state.get("stuck_investigations") or []
    active = state.get("active_stage")
    completed = completed_record or value["record"]
    if (
        active
        and not state.get("uncertain_artifacts")
        and not _other_repair(state, completed)
        and active.get("output")
        and completed.get("output")
        and Path(active["output"]).resolve() == Path(completed["output"]).resolve()
        and active.get("stage") in _stages(completed)
        and completed.get("exit_code") == 0
        and not completed.get("rejected")
        and Path(completed["output"]).is_file()
    ):
        # This is not admission: the normal completed-stage save must reconcile
        # ownership first. No history identity or paid call is consumed here.
        state["pending_builder_failure"] = {
            "failure_evidence": copy.deepcopy(value),
            "reason": reason,
            "accepting_output": str(Path(completed["output"]).resolve()),
        }
        return False
    if (
        not enabled
        or state.get("active_stage")
        or state.get("uncertain_artifacts")
        or _other_repair(state, completed)
        or not value["evidence_refs"]
        or value["record"].get("output") not in value["binding"]["evidence_hashes"]
        or any(row.get("identity") == key for row in history)
        or len(classified(state)) >= policy.classification_limit(state)
    ):
        hold(
            state,
            value,
            "PAUSED_BUILDER_CLASSIFICATION",
            reason + "; failure classification requires reconciled evidence or operator action",
        )
        return False
    request = {
        "identity": key,
        "mode": "builder_failure",
        "stage": "builder",
        "status": "PAUSED_BUILDER_CLASSIFICATION",
        "reason": reason,
        "phase": state.get("phase"),
        "requested_at": util.now(),
        "failure_evidence": copy.deepcopy(value),
    }
    state["stuck_investigation"] = request
    state.setdefault("stuck_investigations", []).append(
        {
            "identity": key,
            "stage": "builder",
            "status": request["status"],
            "reason": reason,
            "requested_at": request["requested_at"],
            "outcome": "investigating",
            "trigger": "builder_failure",
            "milestone_key": policy.key(state),
        }
    )
    state.update({"status": "RUNNING", "phase": "INVESTIGATING", "next_stage": "investigate_stuck"})
    return True


def finalize(state: RunState, record, *, enabled):
    pending = state.get("pending_builder_failure")
    if not pending:
        return
    if (
        state.get("active_stage")
        or state.get("uncertain_artifacts")
        or not record.get("output")
        or str(Path(record["output"]).resolve()) != pending["accepting_output"]
        or record.get("exit_code") != 0
        or record.get("rejected")
        or not any(row.get("output") == record.get("output") for row in state.get("stages", []))
    ):
        raise util.Paused("PAUSED_BUILDER_CLASSIFICATION", "Completed failure boundary is not reconciled")
    guard(state, pending, state["workspace"])
    state.pop("pending_builder_failure")
    queue(state, pending["failure_evidence"], pending["reason"], enabled=enabled)
    if pending.get("resolver_continuation") and state.get("stuck_investigation"):
        state["stuck_investigation"]["resolver_continuation"] = pending["resolver_continuation"]


def probe_containment_error():
    """None when a classification probe can be held read-only, else why not.

    Platforms without macOS sandbox-exec cannot deny write/read/restore forgery
    during a probe. Callers fail closed there: the Investigator states an
    untestable advisory cause instead of a shown probe (see readonly_probe).
    """
    sandbox = Path("/usr/bin/sandbox-exec")
    if sys.platform != "darwin" or not sandbox.is_file():
        return "Read-only classification probe containment is unavailable on this platform"
    return None


def readonly_probe(command):
    """A classification probe may inspect scratch evidence, never manufacture it.

    A final hash comparison cannot catch write/read/restore forgery. The native
    sandbox denies writes for the probe and its descendants, including replacing
    cited files and chmod/unlink. Platforms without this enforcement fail closed;
    the Investigator may instead state an explicitly untestable advisory cause.
    """
    reason = probe_containment_error()
    if reason:
        raise ValueError(reason)
    profile = "(version 1)(allow default)(deny file-write*)"
    return shlex.join(["/usr/bin/sandbox-exec", "-p", profile, "/bin/sh", "-c", command])


def check_facts(checks, record, workspace, *, read_events):
    """Enrich already verified checks only from their executed event/captured receipt.

    The caller must first verify the exact check command, exit, source binding and
    evidence pins. Model-authored operational fields are never treated as receipts.
    """
    operational = ("timed_out", "interrupted", "error", "supervision_errors")
    facts = []
    events = None
    for check in checks:
        fact = {key: copy.deepcopy(value) for key, value in check.items() if key not in operational}
        ref = check["evidence_ref"]
        if ref.startswith("event:"):
            if events is None:
                events = read_events(record["events"])
            matched = [
                row["item"]
                for row in events
                if row.get("type") == "item.completed"
                and row.get("item", {}).get("type") == "command_execution"
                and row["item"].get("id") == ref.split(":", 1)[1]
            ]
            if len(matched) != 1:
                raise util.Paused("PAUSED_STALE_HANDOFF", "Verified check event changed before failure classification")
            receipt = matched[0]
        else:
            path = Path(ref)
            receipt = util.read(path if path.is_absolute() else Path(workspace) / path)
        fact.update({key: copy.deepcopy(receipt[key]) for key in operational if key in receipt})
        facts.append(fact)
    return facts


def route_in_context(state: RunState, evidence, reason, *, enabled, snapshot, milestones, completed_record=None):
    """Use a fresh accepted milestone checkpoint before interpreting a failure."""
    row = milestones.progress(state)
    if row and row.get("needs_replan") and state.get("workspace"):
        current = source_scope.snapshot(Path(state["workspace"]), state, base_snapshot=snapshot)
        if current["revision"] == evidence["record"].get("source_revision") and milestones.fresh_validation(
            state, current
        ):
            evidence.update(checkpoint={"decision": "needs_replan"}, checkpoint_verified=True)
    return route(
        state,
        evidence,
        reason,
        enabled=enabled(state),
        reassess=milestones.request_builder_reassessment,
        completed_record=completed_record,
    )


_HOLD_SUFFIX = "; failure classification requires reconciled evidence or operator action"


def _prior_same_round_diagnosis(state: RunState, ev: dict) -> dict | None:
    """Reuse an already-accepted execution diagnosis when no Builder ran since that charged round."""
    if ev.get("diagnosis") is not None:
        return None
    milestone = policy.key(state)
    current = (state.get("builder_retries") or {}).get(milestone)
    if not policy.same_unretried_builder_round(state, current, (ev.get("record") or {}).get("output")):
        return None
    entry = next(
        (
            row
            for row in reversed(state.get("stuck_investigations") or [])
            if isinstance(row, dict)
            and row.get("trigger") == "builder_failure"
            and row.get("milestone_key") == milestone
            and row.get("outcome") == "classified"
            and row.get("cause") != "needs_user"
            and row.get("failure_class") == "execution"
        ),
        None,
    )
    if entry is None:
        return None
    saved_report: dict | None = None
    output_path = entry.get("output")
    if isinstance(output_path, str) and Path(output_path).is_file():
        try:
            loaded = json.loads(Path(output_path).read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                saved_report = loaded
        except (OSError, ValueError, TypeError):
            saved_report = None
    source = saved_report if saved_report is not None else entry
    diagnosis = source.get("diagnosis")
    if not isinstance(diagnosis, str) or not diagnosis.strip() or source.get("cause") == "needs_user":
        return None
    guidance_text = str(source.get("guidance") or "")
    refs = list(ev.get("evidence_refs") or ())
    if not refs:
        return None
    return {
        "failure_id": ev.get("failure_id"),
        "reused_from_failure_id": source.get("failure_id") or entry.get("failure_id"),
        "evidence_refs": refs,
        "diagnosis": diagnosis,
        "guidance": guidance_text if guidance_text.strip() else diagnosis,
        "example": str(source.get("example") or ""),
        "recommendation": str(source.get("recommendation") or "retry"),
        "cause": str(source.get("cause") or "other"),
        "user_question": str(source.get("user_question") or ""),
        "failure_class": "execution",
    }


def saved_resolver_continuation(state: RunState, source_revision) -> dict | None:
    """Reconstruct an accepted ``resolve`` continuation whose post-save classification held."""
    req = state.get("resolution_request")
    contract_hash = (state.get("goal_contract") or {}).get("hash")
    last_stage = (state.get("stages") or [None])[-1]
    if (
        not isinstance(req, dict)
        or not isinstance(source_revision, str)
        or not source_revision
        or req.get("source_revision") != source_revision
        or req.get("contract_hash") != contract_hash
        or not isinstance(last_stage, dict)
        or last_stage.get("stage") != "resolve"
        or last_stage.get("exit_code") != 0
        or last_stage.get("rejected")
        or last_stage.get("abandoned")
        or last_stage.get("source_revision") != source_revision
    ):
        return None
    pins = req.get("evidence_hashes")
    if isinstance(pins, dict) and any(
        not Path(path).is_file() or util.file_hash(path) != digest for path, digest in pins.items()
    ):
        return None
    output_path = last_stage.get("output")
    if not isinstance(output_path, str) or not Path(output_path).is_file():
        return None
    try:
        saved_value = json.loads(Path(output_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(saved_value, dict) or (saved_value.get("next_task") or {}).get("kind") != "implement":
        return None
    return {"value": saved_value, "record": copy.deepcopy(last_stage)}


def _reconcile_unqueued_hold(
    state: RunState,
    held: dict,
    bound: dict,
    failure_id: str,
    workspace,
    *,
    enabled: bool,
    route_failure,
    apply_resolver_continuation,
    prior_next_action: str | None,
) -> bool:
    held_reason = str(held.get("reason") or "")
    if held_reason.startswith("Builder completed an implementation attempt without source changes") or (
        "(The Investigator could not finish:" in held_reason
    ):
        return False
    source_record = next(
        (
            row
            for row in reversed(state.get("stages") or [])
            if isinstance(row, dict) and row.get("output") == bound.get("output")
        ),
        None,
    )
    if source_record is None or not isinstance(bound.get("evidence_hashes"), dict):
        return False
    try:
        guard(state, {"failure_evidence": {"binding": bound, "record": source_record}}, workspace)
    except util.Paused:
        return False
    ev = evidence(state, source_record)
    ev["failure_id"] = failure_id
    ev["binding"] = copy.deepcopy(bound)
    ev["evidence_hashes_verified"] = True
    ev["evidence_refs"] = list(bound["evidence_hashes"])
    ev.pop("evidence_hashes_verified", None)
    reason = held_reason.removesuffix(_HOLD_SUFFIX)
    prior_diag = _prior_same_round_diagnosis(state, ev)
    if prior_diag is not None:
        ev["diagnosis"] = prior_diag
        state.pop("builder_failure_hold", None)
        run_status.running(
            state,
            "EXECUTING",
            next_stage="builder",
            next_action=state.get("next_action") or prior_next_action,
        )
        if (
            state.get("resolution_request")
            and callable(apply_resolver_continuation)
            and apply_resolver_continuation(state, prior_diag, bound["source_revision"])
        ):
            return True
        route_failure(state, ev, reason)
        return True
    if not enabled or len(classified(state)) >= policy.classification_limit(state):
        return False
    saved_cont = saved_resolver_continuation(state, bound.get("source_revision"))
    state.pop("builder_failure_hold", None)
    if not queue(state, ev, reason, enabled=True):
        return False
    if saved_cont is not None and isinstance(state.get("stuck_investigation"), dict):
        state["stuck_investigation"]["resolver_continuation"] = saved_cont
    return True


def route(state: RunState, evidence, reason, *, enabled, reassess, completed_record=None):
    """Consume cause-specific policy actions before assignment or stronger routing."""
    if evidence.get("diagnosis") is None:
        prior_diag = _prior_same_round_diagnosis(state, evidence)
        if prior_diag is not None:
            evidence["diagnosis"] = prior_diag
    action = policy.failure(
        state, evidence["record"].get("output"), reason, classification=classification.classify(evidence)
    )
    if action == "investigate":
        queue(state, evidence, reason, enabled=enabled, completed_record=completed_record)
    elif action == "replan":
        if state.get("resolution_request"):
            state.setdefault("resolution_history", []).append(
                {
                    "kind": "approach-reassessment",
                    "request": state.pop("resolution_request"),
                    "failure_id": evidence["failure_id"],
                }
            )
        reassess(state, evidence)
        if state.get("status") != "RUNNING":
            hold(state, evidence, state["status"], state["stop_reason"])
    elif action == "recover":
        # Dispatch owns typed operational recovery. A completed report authorizes
        # neither a replay nor a provider switch or a new recovery allowance.
        status = evidence.get("error_class") or evidence["record"].get("error_class")
        status = status if isinstance(status, str) and status.startswith("PAUSED_") else "PAUSED_BUILDER_OPERATIONAL"
        hold(state, evidence, status, reason)
    elif action not in ("retry", "escalate", "pause", "defer"):
        raise ValueError("Unrecognized Builder failure action: " + str(action))
    return action


def reconcile_routable_hold(
    state: RunState,
    workspace,
    *,
    route_failure,
    diagnose_rework=None,
    apply_resolver_continuation=None,
    prior_next_action: str | None = None,
    enabled: bool = True,
) -> bool:
    """Reconcile a pre-fix PAUSED_BUILDER_CLASSIFICATION hold whose Investigator returned a concrete non-user class."""
    held = state.get("builder_failure_hold")
    if (
        not isinstance(held, dict)
        or held.get("status") != "PAUSED_BUILDER_CLASSIFICATION"
        or held.get("classification_retry")
        or state.get("next_stage") != "builder"
        or any(
            state.get(key)
            for key in (
                "active_stage",
                "uncertain_artifacts",
                "pending_report_repair",
                "pending_builder_failure",
                "stuck_investigation",
            )
        )
    ):
        return False
    failure_id, bound = held.get("failure_id"), held.get("binding")
    if not isinstance(failure_id, str) or not failure_id or not isinstance(bound, dict):
        return False
    entry = next(
        (
            row
            for row in reversed(state.get("stuck_investigations") or [])
            if isinstance(row, dict) and row.get("identity") == "builder-failure:" + failure_id
        ),
        None,
    )
    if entry is None:
        return _reconcile_unqueued_hold(
            state,
            held,
            bound,
            failure_id,
            workspace,
            enabled=enabled,
            route_failure=route_failure,
            apply_resolver_continuation=apply_resolver_continuation,
            prior_next_action=prior_next_action,
        )
    output_path = entry.get("output") if isinstance(entry, dict) else None
    if (
        entry.get("outcome") != "paused"
        or entry.get("cause") == "needs_user"
        or entry.get("failure_class") not in ("plan", "execution", "operational")
        or not isinstance(output_path, str)
        or not Path(output_path).is_file()
    ):
        return False
    try:
        saved_report = json.loads(Path(output_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    refs = saved_report.get("evidence_refs") if isinstance(saved_report, dict) else None
    allowed_hashes = bound.get("evidence_hashes")
    if (
        not isinstance(saved_report, dict)
        or saved_report.get("failure_id") != failure_id
        or saved_report.get("cause") == "needs_user"
        or saved_report.get("failure_class") not in ("plan", "execution", "operational")
        or not isinstance(saved_report.get("diagnosis"), str)
        or not saved_report["diagnosis"].strip()
        or not isinstance(refs, list)
        or not refs
        or not isinstance(allowed_hashes, dict)
        or not set(refs) <= set(allowed_hashes)
    ):
        return False
    source_record = next(
        (
            row
            for row in reversed(state.get("stages") or [])
            if isinstance(row, dict) and row.get("output") == bound.get("output")
        ),
        {
            "output": bound.get("output"),
            "task_id": bound.get("task_id"),
            "source_revision": bound.get("source_revision"),
        },
    )
    try:
        guard(state, {"failure_evidence": {"binding": bound, "record": source_record}}, workspace)
    except util.Paused:
        return False
    effective_class = saved_report["failure_class"]
    guidance_text = str(saved_report.get("guidance") or "")
    diag_payload = {
        **{
            key: saved_report[key]
            for key in (
                "failure_id",
                "evidence_refs",
                "diagnosis",
                "guidance",
                "example",
                "recommendation",
                "cause",
                "user_question",
            )
            if key in saved_report
        },
        "guidance": guidance_text if guidance_text.strip() else saved_report["diagnosis"],
        "failure_class": effective_class,
    }
    ev = evidence(state, source_record)
    ev["failure_id"] = failure_id
    ev["binding"] = copy.deepcopy(bound)
    ev["evidence_refs"] = list(allowed_hashes)
    ev["diagnosis"] = diag_payload
    reason = str(entry.get("reason") or held.get("reason") or "")
    entry["outcome"] = "classified"
    entry["failure_class"] = effective_class
    state.pop("builder_failure_hold", None)
    run_status.running(
        state,
        "EXECUTING",
        next_stage="builder",
        next_action=state.get("next_action") or prior_next_action,
    )
    if (
        classification.classify(ev) == "execution"
        and state.get("resolution_request")
        and callable(apply_resolver_continuation)
        and apply_resolver_continuation(state, diag_payload, bound["source_revision"])
    ):
        return True
    action = route_failure(state, ev, reason)
    if callable(diagnose_rework):
        diagnose_rework(
            state,
            ev,
            diag_payload,
            {"output": output_path, "source_revision": bound["source_revision"]},
            action,
        )
    return True

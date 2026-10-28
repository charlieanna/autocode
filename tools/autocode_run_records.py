"""How a run reads and writes its saved state and stage records: the JSON files, the clock, attempt
ids, stage accounting and archiving, and the checks that a stage has stopped or completed.

write_json keeps the Resolver's human-request records consistent on every save
(normalize_human_boundary), which is why this module reads the goal, planning and milestone state.
It imports nothing from autocode.py; autocode.py re-exports these names.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_goal_lifecycle as lifecycle
    from . import autocode_goals as goals
    from . import autocode_job_failure as job_failure
    from . import autocode_milestones as milestones
    from . import autocode_output_policy as output_policy
    from . import autocode_planning as planning
    from . import autocode_process as processes
    from . import autocode_progressive_state as progressive
    from . import autocode_recovery_accounting as accounting
    from . import autocode_report_repair_queue as report_repair_queue
    from . import autocode_requirements_review as requirements_review
    from . import autocode_resolver_human as resolver_human
    from . import autocode_roles as roles
    from . import autocode_run_status as run_status
    from . import autocode_support as support
    from . import autocode_util as util
    from . import autocode_worker_quota as worker_quota
    from . import autocode_workflow as workflow
    from . import autopilot
except ImportError:
    import autocode_goal_lifecycle as lifecycle
    import autocode_goals as goals
    import autocode_job_failure as job_failure
    import autocode_milestones as milestones
    import autocode_output_policy as output_policy
    import autocode_planning as planning
    import autocode_process as processes
    import autocode_progressive_state as progressive
    import autocode_recovery_accounting as accounting
    import autocode_report_repair_queue as report_repair_queue
    import autocode_requirements_review as requirements_review
    import autocode_resolver_human as resolver_human
    import autocode_roles as roles
    import autocode_run_status as run_status
    import autocode_support as support
    import autocode_util as util
    import autocode_worker_quota as worker_quota
    import autocode_workflow as workflow
    import autopilot

repair_limit = report_repair_queue.repair_limit  # the bound lives with the repair queue


def check_evidence_options(record):
    return {
        "receipt_only": record.get("output_mode") == "report_file",
        "capture_context": record.get("capture_context"),
    }


# Planning restarts allowed per deferral reason since the user's last input; the next deferral pauses.
MAX_DEFERRED_APPROVAL_RESTARTS = 2


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def write_json(path: Path, value: Any, *, before_commit=None, after_commit=None) -> None:
    if Path(path).name == "state.json" and isinstance(value, dict):
        # #980: persisted state names the autocode code that produced it, so version
        # skew is discoverable on resume instead of requiring tree forensics. Restamp
        # only when the identity moved: an unchanged re-save (explain, idempotent
        # flows) must stay byte-identical — installed_cli_explain asserts the run is
        # not mutated by a read-only explanation.
        try:
            current = util.runner_identity()
        except (OSError, ValueError):
            current = None  # advisory stamp: a hostile (mocked) environment keeps the prior value
        if current is not None and value.get("runner") != current:
            value["runner"] = current
        normalize_human_boundary(cast("RunState", value), Path(path).parent)
        try:
            from . import autocode_status
        except ImportError:
            import autocode_status
        if after_commit is None and before_commit is None:
            autocode_status.persist(path, cast("RunState", value))
        else:
            autocode_status.persist(
                path, cast("RunState", value), before_commit=before_commit, after_commit=after_commit
            )
    else:
        if after_commit is None and before_commit is None:
            support.atomic_json(path, value)
        else:
            support.atomic_json(path, value, before_commit=before_commit, after_commit=after_commit)


def normalize_human_boundary(state: RunState, run_dir):
    """Adjudicate private proposals before persisted state can ask a human."""
    if not all(key in state for key in ("task", "workspace", "status")):
        return
    if run_dir is not None:
        state["run_dir"] = str(Path(run_dir).resolve())
    if state.get("parent_run"):
        # Workers report to their parent; they never publish their own requests.
        state.pop(resolver_human.PUBLIC, None)
        state.pop("user_request", None)
        state["pending_questions"] = []
        return
    pause = state.get("pause_intent") or {}
    if pause and not pause.get("acknowledged_at"):
        # A report may leave a proposal for adjudication at the same boundary
        # that consumes a queued Pause. Keep both until explicit resume; an
        # older writer may already have drifted the status back to RUNNING.
        run_status.pause(
            state,
            "PAUSED_INTERVENTION",
            state.get("stop_reason") or "Queued pause was applied; explicitly resume when ready.",
        )
        return
    if (state.get("active_stage") or state.get("uncertain_artifacts")) and (
        cast(dict, state.get(resolver_human.PRIVATE, {})).get("scope") != "operational_exhaustion"
    ):
        return
    public = resolver_human.current(state)
    if public and not (
        milestones.recover_review_only_request(state, public, ask_user=lifecycle.wait_for_user)
        or milestones.release_obsolete_gate_request(state, public)
    ):
        return
    requirements_review.recover_scope(state, resolver_human.queue)
    proposal = state.get(resolver_human.PRIVATE)
    # The batch's last records belong to its members, never the parent. A pending member request
    # left stale by a settings write must stay for its retry/answer instead of becoming a legacy decision.
    member_request = worker_quota.at_checkpoint(state) and resolver_human.pending_issued(state)
    if (
        not proposal
        and not member_request
        and state.get("status") in ("WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL", "PAUSED_GOAL_UNAPPROVED")
    ):
        # Explicit locked reconciliation of legacy decisions. Read-only status
        # and dashboard projections never enter this writer path.
        request = copy.deepcopy(state.get("user_request") or {})
        questions = copy.deepcopy(state.get("pending_questions") or [])
        origin = {"stage": "legacy_decision_reconciliation"}
        if state.get("status") in ("AWAITING_GOAL_APPROVAL", "PAUSED_GOAL_UNAPPROVED"):
            blockers = (state.get("goal_contract") or {}).get("body", {}).get("open_blocking_questions", [])
            if blockers:
                resolver_human.queue(
                    state,
                    "clarification",
                    origin,
                    questions=blockers,
                    phase="DISCOVERING",
                    next_stage=state.get("next_stage"),
                )
            else:
                resolver_human.queue(
                    state, "goal_approval", origin, status="AWAITING_GOAL_APPROVAL", next_stage=state.get("next_stage")
                )
        elif request.get("kind") == "human_review":
            evidence = {
                "review_token": goals.review_token(state),
                "hashes": copy.deepcopy((state.get("validation") or {}).get("evidence_hashes", {})),
            }
            resolver_human.queue(
                state,
                "human_review",
                origin,
                request=request,
                questions=questions,
                evidence=evidence,
                next_stage=state.get("next_stage"),
            )
        elif request:
            scope = request["kind"] if request.get("kind") in ("permission", "goal_change") else "blocker"
            source = next(
                (
                    row
                    for row in reversed(state.get("stages", []))
                    if not row.get("runner_owned") and not row.get("rejected") and row.get("output")
                ),
                None,
            )
            if source:
                origin = {"stage": source.get("original_stage") or source["stage"], "output": source["output"]}
            resolver_human.queue(
                state, scope, origin, request=request, questions=questions, next_stage=state.get("next_stage")
            )
        elif questions:
            resolver_human.queue(
                state,
                "clarification",
                origin,
                questions=questions,
                phase=state.get("phase"),
                next_stage=state.get("next_stage"),
            )
        proposal = state.get(resolver_human.PRIVATE)
    if not proposal:
        return
    # An internal diagnostic is already scheduled; repeated persistence must
    # neither ask the user nor requeue the same provider call.
    if state.get("next_stage") == "astra_resolve" and state.get("resolution_request"):
        return
    disposition = resolver_human.evaluate(state)
    if disposition == "permission_loop":
        return
    if disposition == "escalate":
        milestones.release_obsolete_gate_request(state, resolver_human.current(state))
        return
    if (
        disposition == "defer"
        and proposal["scope"] == "goal_approval"
        and planning.enabled(state)
        and not (state.get("goal_contract") or {}).get("body", {}).get("open_blocking_questions")
    ):
        if not state.get("planning") or not planning.is_planning(state, state.get("next_stage")):
            # A restart begins a cycle with a fresh review allowance, so a deferral that keeps
            # recurring would spend review calls without limit (docs/bugs/2026-09-30-unbounded-
            # planning-restart.md). Restarts are counted per reason since the user's last input.
            reason = state["resolver"]["human_disposition"]["reason"]
            # Only what the user wrote renews the allowance: runner bookkeeping (recovery
            # receipts, review reroutes) also lands in user_events and must not reset
            # this bound. user_intervention is feedback the user queued while the run worked.
            inputs = sum(
                1
                for event in state.get("user_events", [])
                if isinstance(event, dict) and event.get("actor") in ("user", "user_cli", "user_intervention")
            )
            identity = support.digest({"task_id": state.get("task_id"), "reason": reason, "user_inputs": inputs})
            restarts = state["resolver"].setdefault("deferred_approval_restarts", {})
            planning.start(state)
            if restarts.get(identity, 0) >= MAX_DEFERRED_APPROVAL_RESTARTS:
                # The new cycle is ready, so an explicit resume runs exactly one more.
                state.pop(resolver_human.PRIVATE, None)
                run_status.pause(
                    state,
                    "PAUSED_APPROVAL_DEFERRED",
                    f"Plan approval was deferred again: {reason}. Planning already restarted "
                    f"{restarts[identity]} times for this reason since your last input, so it stopped "
                    "instead of starting another cycle. Inspect the run; autocode resume runs one more "
                    "planning cycle, and --feedback restarts from requirements.",
                )
                return
            restarts[identity] = restarts.get(identity, 0) + 1
        state.update({"status": "RUNNING", "phase": "PLANNING"})
        state.pop(resolver_human.PRIVATE, None)
        return
    if disposition == "defer" and proposal["scope"] == "clarification":
        reason = state["resolver"]["human_disposition"]["reason"]
        if reason.startswith("An authenticated answer already exists"):
            identity = support.digest(
                {
                    "task_id": state.get("task_id"),
                    "questions": proposal["questions"],
                    "answers": state.get("answers", {}),
                }
            )
            retries = state["resolver"].setdefault("saved_answer_retries", {})
            if retries.get(identity, 0) < 2:
                retries[identity] = retries.get(identity, 0) + 1
                state["recovery_context"] = {
                    "kind": "saved_answer_reconciliation",
                    "instruction": "Honor the existing authenticated answers. Do not ask the same questions again.",
                    "question_ids": [q["id"] for q in proposal["questions"]],
                }
                state.pop(resolver_human.PRIVATE, None)
                first = (
                    "requirements_gather"
                    if "requirements" in state.get("settings", {}).get("roles", {})
                    else "astra_discovery"
                )
                state.update({"status": "RUNNING", "phase": "DISCOVERING", "next_stage": first})
                return
    if disposition == "defer" and proposal["scope"] == "blocker" and goals.approved(state):
        output = proposal["origin"].get("output")
        source = next(
            (
                row
                for row in reversed(state.get("stages", []))
                if output and row.get("output") == output and not row.get("rejected") and not row.get("runner_owned")
            ),
            None,
        )
        if source and proposal["origin"]["stage"] != "astra_resolve":
            value = read_json(Path(output))
            semantic = source.get("original_stage") or source["stage"]
            autopilot.queue_resolution(
                state, value, source, source_stage=semantic, source_report=not semantic.startswith("astra")
            )
            if state.get("next_stage") == "astra_resolve" and state.get("resolution_request"):
                state.update({"status": "RUNNING", "phase": "RESOLVING"})
                return
            # Requeueing scheduled no diagnosis: this task and source already have one, so it staged
            # the same blocker again. Setting the run RUNNING here repeated the stage that blocked,
            # without limit (11 Completion Reviews on an unchanged source, 2026-10-25). Hold instead.
            state["stop_reason"] = (
                "The Completion Owner still does not accept this unchanged source after the existing "
                "diagnosis, so another automatic review would reach the same result. Change the setting "
                "or source the diagnosis names, or give feedback, then resume."
            )
    # Deferred or rejected proposals stay private for resolver diagnosis.
    state.update({"status": "RESOLVER_PENDING", "phase": "RESOLVING"})


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def account_stage(state: RunState, record):
    """Charge a finished attempt once, including rejected/recovered responses."""
    if not record.get("accounted"):
        output_policy.account(state, record)
        duration = record.get("duration_seconds")
        if duration is None and record.get("started_at"):
            started = dt.datetime.fromisoformat(record["started_at"]).timestamp()
            events_path = Path(record["events"])
            ended = (
                dt.datetime.fromisoformat(record["finished_at"]).timestamp()
                if record.get("finished_at")
                else events_path.stat().st_mtime
                if events_path.exists()
                else started
            )
            duration = max(0, ended - started)
            record["duration_seconds"] = duration
        if not progressive.account_stage(state, record):
            record["accounted"] = True
            return
        state["active_seconds"] = state.get("active_seconds", 0) + (duration or 0)
        milestones.account(state, record)
        record["accounted"] = True


def normalize_plan_challenge_blocking(value, record):
    """Conservatively retain plan findings when only their blocking flag is omitted.

    Missing flags cannot clear a finding. The raw provider report is preserved by
    load_stage_report, and the complete normalized report still faces its schema.
    """
    if record.get("stage") not in ("astra_challenge", "astra_challenge_report_repair") or not isinstance(value, dict):
        return value
    concerns = value.get("concerns")
    if not isinstance(concerns, list) or not any(isinstance(row, dict) and "blocking" not in row for row in concerns):
        return value
    if any(not isinstance(row, dict) for row in concerns):
        return value
    return {**value, "concerns": [{**row, "blocking": True} if "blocking" not in row else row for row in concerns]}


PROVENANCE_LISTS = frozenset(
    {
        "code_refs",
        "source_refs",
        "alternatives",
        "uncertainties",
        "contract_changes",
        "conflict_resolutions",
        "requirement_trace",
        "remediation_records",
        "machine_resolutions",
        "access_blockers",
        "ignored_statements",
        "conflicts",
        "proposed_reframes",
        "ignored_requirements",
        "obligation_decisions",
    }
)


PLANNING_STAGES = roles.ALL_PLANNING_STAGES


def default_missing_provenance(value, record):
    """Fill omitted provenance lists instead of paying for a report-repair model call.

    Two recorded live trials spent their whole remaining budget repairing a planning
    report that only lacked such a list (VALIDATION.md: `$: missing code_refs`).
    """
    stage = str(record.get("stage", "")).removesuffix("_report_repair")
    if not isinstance(value, dict):
        return value
    try:
        properties = read_json(Path(record["schema"])).get("properties", {})
    except (OSError, ValueError, KeyError):
        return value
    optional = {}
    if "progressive_checkpoint" in properties and "progressive_checkpoint" not in value:
        optional["progressive_checkpoint"] = False
    if "progressive_proposal" in properties and "progressive_proposal" not in value:
        optional["progressive_proposal"] = {
            "version": 0,
            "needed_because": "",
            "shared_decisions": [],
            "outstanding_criteria": [],
            "done_slices": [],
            "slices": [],
        }
    if stage not in PLANNING_STAGES:
        if optional:
            record["defaulted_fields"] = sorted(optional)
        return {**value, **optional}
    contract_offer = properties.get("contract", {})
    if "anyOf" in contract_offer:
        # Revision references and legacy full reports have different optional fields.
        # Do not inject full-body defaults into a reference, or lose them for old reports.
        reference = isinstance(value.get("contract"), dict) and "revise_contract" in value["contract"]
        contract_offer = next(
            (
                branch
                for branch in _offers(contract_offer)
                if ("revise_contract" in branch.get("properties", {})) == reference
            ),
            {},
        )
    contract_schema = contract_offer.get("properties", {})
    if isinstance(value.get("contract"), dict):
        # A report-level field written inside the contract is the model's own content, only misplaced:
        # move it up instead of rejecting the report (`$.contract: unexpected fields`, 8 repairs to
        # 2026-10-02, contract_changes for one) or defaulting it to empty.
        misplaced = sorted(
            key for key in value["contract"] if key in properties and key not in contract_schema and not value.get(key)
        )
        if misplaced:
            record["hoisted_fields"] = misplaced
            value = {
                **value,
                **{key: value["contract"][key] for key in misplaced},
                "contract": {k: v for k, v in value["contract"].items() if k not in misplaced},
            }
    missing = sorted(
        key
        for key in PROVENANCE_LISTS
        if key in properties and key not in value and properties[key].get("type") == "array"
    )
    defaults = {**optional, **{key: [] for key in missing}}
    missing.extend(optional)
    # An omitted job type is "build", as for every run before task_kind existed; approval
    # always shows the job type, so a wrong default is visible before any build starts.
    if "task_kind" in properties and "task_kind" not in value:
        defaults["task_kind"] = "build"
    contract = value.get("contract")
    if isinstance(contract, dict) and "task_kind" in contract_schema and "task_kind" not in contract:
        defaults["contract"] = {**contract, "task_kind": "build"}
        missing.append("contract.task_kind")
    elif "task_kind" in defaults:
        missing.append("task_kind")
    if isinstance(contract, dict):
        # An omitted current_behavior box is an empty one (#2133): the strict generation schema
        # requires it on every criterion, but a provider that leaves it out has said nothing about
        # today's behaviour, which the runner fills from the bound diagnosis or leaves empty. It costs
        # no report-repair call.
        rows = contract_schema.get("acceptance_criteria", {})
        boxed = any("current_behavior" in offer.get("items", {}).get("properties", {}) for offer in _offers(rows))
        filled = {**(defaults.get("contract") or contract)}
        criteria = filled.get("acceptance_criteria")
        if boxed and isinstance(criteria, list) and _without_box(criteria):
            filled["acceptance_criteria"] = _boxed(criteria)
            defaults["contract"] = filled
            missing.append("contract.acceptance_criteria.current_behavior")
        elif boxed and isinstance(criteria, dict) and _without_box(criteria.get("upsert")):
            filled["acceptance_criteria"] = {**criteria, "upsert": _boxed(criteria["upsert"])}
            defaults["contract"] = filled
            missing.append("contract.acceptance_criteria.upsert.current_behavior")
    if not defaults:
        return value
    record["defaulted_fields"] = sorted(missing)
    return {**value, **defaults}


def _offers(schema):
    """The criteria list schemas a contract schema offers: the plain list, or each anyOf branch and a
    finalize delta's upsert list (autocode_plan_retention.model_schema)."""
    if not isinstance(schema, dict):
        return []
    branches = schema.get("anyOf") if isinstance(schema.get("anyOf"), list) else [schema]
    offers = []
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        offers.append(branch)
        upsert = branch.get("properties", {}).get("upsert")
        if isinstance(upsert, dict):
            offers.append(upsert)
    return offers


def _without_box(rows):
    return isinstance(rows, list) and any(
        isinstance(row, dict) and "criterion" in row and "current_behavior" not in row for row in rows
    )


def _boxed(rows):
    return [
        {**row, "current_behavior": ""}
        if isinstance(row, dict) and "criterion" in row and "current_behavior" not in row
        else row
        for row in rows
    ]


def stage_supports_sessions(state: RunState, record):
    """Read the launch-time capability, conservatively recognizing old config records."""
    if isinstance(record.get("supports_sessions"), bool):
        return record["supports_sessions"]
    # Before this field existed, absent provider metadata always meant OpenCode.
    # Only an explicitly saved non-OpenCode provider is known to be sessionless.
    return state.get("settings", {}).get("provider", "opencode") == "opencode"


def stage_completed(state: RunState, record):
    if stage_supports_sessions(state, record):
        if not record.get("events"):
            return False
        return any(event.get("type") == "turn.completed" for event in support.events(record["events"]))
    return record.get("exit_code") == 0 and bool(record.get("output")) and Path(record["output"]).is_file()


def archive_rejected_stage(state: RunState, run_dir, record, reason):
    """Set aside a completed request whose output was rejected, so an explicit
    resume starts a fresh numbered attempt instead of re-applying the same output."""
    base = Path(record["output"]).with_suffix("")
    archived = base.parent / f"archived-{base.name}-{uuid.uuid4().hex[:6]}"
    archived.mkdir(parents=True, exist_ok=True)
    originals = []
    archived_paths = {}
    for suffix in (
        ".json",
        ".jsonl",
        ".reported.json",
        ".response.txt",
        ".prompt.md",
        ".before.json",
        ".after.json",
        ".diff",
        ".tools.json",
        ".opencode.json",
        ".source.json",
        ".witness.json",
    ):
        artifact = base.with_name(base.name + suffix)
        if artifact.exists():
            # Keep originals until the caller durably saves the archive pointers.
            # A crash or disk error must leave the previous checkpoint readable.
            shutil.copy2(artifact, archived / artifact.name)
            originals.append(artifact)
            archived_paths[str(artifact)] = str(archived / artifact.name)
    for key in (
        "output",
        "events",
        "reported_output",
        "response_text",
        "prompt",
        "before_ref",
        "after_ref",
        "diff_ref",
        "tool_evidence",
        "permission_config",
    ):
        if record.get(key) and Path(record[key]).parent == base.parent:
            record[key] = str(archived / Path(record[key]).name)
    for key in ("capture", "witness"):
        capture = record.get("job_source") or {}
        if capture.get(key) in archived_paths:
            capture[key] = archived_paths[capture[key]]
    record["archived_paths"] = archived_paths
    record["rejected"] = True
    record["rejection_reason"] = str(reason)
    state.setdefault("stages", []).append(record)
    state.setdefault("reconciliation_notes", []).append(
        {
            "at": now(),
            "stage": record["stage"],
            "iteration": record["iteration"],
            "archived": str(archived),
            "reason": str(reason),
        }
    )
    state.pop("active_stage", None)
    return originals


def set_aside_at_completion_gate(state: RunState, run_dir, record, error, *, persist):
    """A completion-gate pause (util.boundary_pause) sets its attempt aside and is re-raised; any other error returns.

    The report passed its own checks and the completion gate refused the run's claim (#1851).
    Not a rejected output: no failure record, no escalation, no report repair, so the
    Investigator is never involved and the run pauses under the gate's own status and
    reason. The attempt is still set aside like one (archive_rejected_stage), because an
    attempt left active made every resume re-apply the same report against the same gate.
    --resume-paused starts a fresh attempt; the person's review or --accept-completion
    settle the gap the reason names. ``persist`` is the runner's state writer.
    """
    if not util.boundary_pause(error):
        return
    account_stage(state, record)
    originals = archive_rejected_stage(state, run_dir, record, error)
    run_status.pause(state, error.status, str(error), paused_at=now())
    persist(Path(run_dir) / "state.json", state)
    for artifact in originals:
        artifact.unlink(missing_ok=True)
    raise error


def assert_stage_stopped(record):
    # A lost parent may have left a worker alive. Check without exposing args.
    pid = record.get("pid")
    if record.get("processes"):
        if processes.live_processes(record["processes"]):
            raise support.Paused("PAUSED_WORKSPACE_BUSY", "Recorded provider commands are still alive")
    elif pid and record.get("exit_code") is None:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        else:
            # A zombie has exited and only waits to be reaped. Where PID 1 never reaps orphans (a
            # cloud container's process API), it stays a zombie for good and must not hold the
            # workspace (#668). A process that cannot be inspected still counts as alive.
            try:
                row = processes.process_table({pid}).get(pid)
            except processes.ProcessError:
                row = {"state": "unknown"}
            if row is not None and not row["state"].startswith("Z"):
                raise support.Paused("PAUSED_WORKSPACE_BUSY", f"Stage process {pid} still exists; wait for it")


def attempt_id(record):
    return f"{record['iteration']:03d}/{Path(record['output']).stem}"


# The automatic-recovery budget lives in autocode_recovery_accounting; these names are kept for callers.
recovery_count = accounting.spent
count_automatic_recovery = accounting.count


def timeout_recovery_route(state: RunState, record):
    """Return the (next_stage, phase) that continues after an archived timeout.

    Planning and discovery stages run read-only against an unapproved draft, so
    a timed-out attempt returns to its own owner under the existing planning
    caps (``autoplanner.charge`` still applies). Routing them to the execution
    reviewer would fail the next admission with PAUSED_GOAL_UNAPPROVED.
    """
    stage, role = record["stage"], record["role"]
    if job_failure.owner(record):
        return stage, run_status.PAUSE_PHASE
    if planning.is_planning(state, stage):
        return stage, "PLANNING"
    if stage == "astra_discovery":
        return stage, "DISCOVERING"
    if stage == "astra_plan":
        return stage, "READY_TO_EXECUTE"
    # Final-audit-only runs keep the Builder in charge of implementation. Other routing
    # modes retain the established Plan Reviewer recovery review before another writer.
    if workflow.final_only(state) and role in ("terra", "sol"):
        return "terra", "EXECUTING"
    return ("astra_review" if role != "astra" else stage), "EXECUTING"

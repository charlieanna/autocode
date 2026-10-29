"""Recover only an exact runner-only inherited-test correction of an approved plan.

The read-only offer authenticates the old report and source. Explicit Resume
reruns its current tests, restores that exact approved contract and task, and
keeps the discarded draft, review charges and all historical evidence.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_provenance as provenance
    from . import autocode_acceptance_tests_job as job
    from . import autocode_contract_identity as identity
    from . import autocode_conversation_ingress as conversation_ingress
    from . import autocode_limit_events as limit_events
    from . import autocode_planning_cycle as planning_cycle
    from . import autocode_report_source as report_source
    from . import autocode_resolver_human as resolver_human
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
except ImportError:
    import autocode_acceptance_provenance as provenance
    import autocode_acceptance_tests_job as job
    import autocode_contract_identity as identity
    import autocode_conversation_ingress as conversation_ingress
    import autocode_limit_events as limit_events
    import autocode_planning_cycle as planning_cycle
    import autocode_report_source as report_source
    import autocode_resolver_human as resolver_human
    import autocode_source_scope as source_scope
    import autocode_util as util

STAGE = job.STAGE


def _evidence(state: RunState, workspace):
    try:
        if state.get("status") != "PAUSED_PLANNING_BUDGET" or state.get("next_stage") != "astra_challenge":
            return None
        if any(
            state.get(key)
            for key in (
                "active_stage",
                "active_runner_check",
                "pending_report_repair",
                "uncertain_artifacts",
                "current_task",
            )
        ):
            return None
        current = state.get("goal_contract") or {}
        history = state["contract_history"]
        approved = history[-1]
        candidate = copy.copy(state)
        candidate["goal_contract"] = approved
        if (
            current.get("origin") != job.ORIGIN
            or current.get("approval_status") != "draft"
            or current.get("approval_event")
            or not identity.sealed(current)
            or not identity.approved(candidate)
            or current["revision"] != approved["revision"] + 1
            or limit_events.last_decision(state) != approved["approval_event"]  # a limit raise is no decision (#2412)
        ):
            return None
        send_back = state["acceptance_tests"]["send_backs"][-1]
        records = [row for row in state["stages"] if row.get("output") == send_back.get("output")]
        if len(records) != 1:
            return None
        record = records[0]
        if (
            record.get("stage") != job.STAGE
            or record.get("contract_hash") != approved["hash"]
            or send_back.get("contract_hash") != approved["hash"]
            or type(record.get("exit_code")) is not int
            or record["exit_code"] != 0
            or not record.get("accounted")
            or record.get("engine") != "opencode"
            or record.get("output_mode") != "opencode_events"
            or any(record.get(key) for key in ("timed_out", "rejected", "report_only"))
        ):
            return None
        # Native accounting pins the authoritative event file. A modified report
        # cannot be adopted just because its output path still names the attempt.
        pin = record["metrics"]["request_context"]["accounting"]["event_file"]
        if pin["path"] != record["events"] or not pin.get("sha256") or util.file_hash(pin["path"]) != pin["sha256"]:
            return None
        extracted = report_source.original_report_for_repair(record)
        value = extracted["report"]
        if extracted.get("extraction_error") or value != util.read(record["output"]):
            return None
        util.validate_schema(value, job.SCHEMA)
        if value["cannot_write"]:
            return None
        ids = {row["case_id"] for row in value["already_passing"]}
        if not ids or ids != set(send_back["case_ids"]):
            return None
        if not ids <= provenance.inherited_cases(candidate, reports=value["tests"], workspace=workspace):
            return None
        names = {row["case_id"]: row["test_name"] for row in value["tests"] if row["case_id"] in ids}
        changes = [
            {
                "item": cid,
                "change": "reworded",
                "basis": "runner_proof",
                "answer_id": "",
                "replacement": f"guard: {names[cid]} (the runner saw it pass on today's code: {row['evidence']})",
            }
            for cid, row in sorted((row["case_id"], row) for row in value["already_passing"])
        ]
        if current["body"] != job.corrected_body(candidate, names) or current.get("declared_changes") != changes:
            return None
        index = state["stages"].index(record)
        if any(
            row.get("stage") != "investigate_stuck" or row.get("changed_files") for row in state["stages"][index + 1 :]
        ):
            return None
        if source_scope.snapshot(workspace, candidate)["revision"] != record.get("source_revision"):
            return None
        tasks = [row for row in state.get("task_archive", []) if row.get("id") == record.get("task_id")]
        spec = approved["body"].get("initial_task")
        if (
            len(tasks) != 1
            or tasks[0].get("contract_hash") != approved["hash"]
            or tasks[0].get("contract_revision") != approved["revision"]
            or not isinstance(spec, dict)
            or spec.get("kind") != "implement"
            or any(tasks[0].get(key) != value for key, value in spec.items())
        ):
            return None
        planning = state["planning_history"][-1]
        if planning.get("final_token") != identity.token(approved) or not planning.get("review_depth_reduced"):
            return None
        simulated = copy.deepcopy(state)
        simulated["planning"] = copy.deepcopy(planning)
        # Exactly the old send-back transition: no later charge, bound, policy or
        # user authority is inferred away. Restore its original allowance with
        # its unchanged spent count, rather than granting another review call.
        consistent = planning_cycle.new_cycle(simulated)
        legacy = copy.deepcopy(consistent)
        if planning.get("review_call_limit_origin") == "runner_default":
            legacy.pop("review_call_limit", None)
            legacy.pop("review_call_limit_origin", None)
        if state["planning"] not in (consistent, legacy):
            return None
        return {"contract": approved, "record": record, "value": value, "task": tasks[0], "planning": planning}
    except (OSError, AttributeError, IndexError, KeyError, TypeError, ValueError, RuntimeError):
        return None


def can_resume(state: RunState) -> bool:
    """Read-only eligibility for the ordinary named Resume action."""
    evidence = _evidence(state, state["workspace"]) if state.get("workspace") else None
    if evidence is None:
        return False
    candidate = copy.deepcopy(state)
    candidate["goal_contract"] = evidence["contract"]
    candidate["planning"] = evidence["planning"]
    try:
        require_boundary(candidate)
    except ValueError:
        return False
    return True


def require_boundary(state: RunState, expected_goal_token=None) -> None:
    """Check ordinary Build authority on the speculative restored candidate."""
    if (state.get(resolver_human.PUBLIC) or {}).get("scope") == "operational_exhaustion":
        resolver_human.supersede_operational(
            state, "The inherited-test correction was reversed to its exact approved plan"
        )
    if state.get("pending_questions") or state.get("user_request"):
        raise ValueError("Pending human questions or decisions cannot be bypassed by acceptance recovery")
    conversation_ingress.require_expected_goal(
        state, expected_goal_token, token_for=identity.token, is_approved=identity.approved
    )


def reconcile(state: RunState, workspace, *, at, run_tests, evidence_dir=None) -> bool:
    """Recheck under the caller's lock and independent-command ownership boundary."""
    evidence = _evidence(state, workspace)
    if evidence is None:
        return False
    discarded = copy.deepcopy(state["goal_contract"])
    state["contract_history"].append(discarded)
    state["goal_contract"] = copy.deepcopy(evidence["contract"])
    state["planning_history"].append(copy.deepcopy(state["planning"]))
    state["planning"] = copy.deepcopy(evidence["planning"])
    state["current_task"] = copy.deepcopy(evidence["task"])
    state["acceptance_criteria"] = [
        {"id": row["id"], "criterion": row["criterion"], "status": "unverified", "evidence": ""}
        for row in evidence["contract"]["body"]["acceptance_criteria"]
    ]
    state["criteria_revision"] = util.digest(util.criteria_definition(state["acceptance_criteria"]))
    job.apply(
        state,
        evidence["value"],
        evidence["record"],
        workspace,
        run_tests=run_tests,
        evidence_dir=evidence_dir or Path(evidence["record"]["output"]).parent / "acceptance-recovery",
    )
    if state.get("status") != "RUNNING" or state.get("next_stage") not in ("terra", "orchestrator"):
        raise ValueError("The retained acceptance report did not pass fresh runner checks; recovery was not committed")
    state.setdefault("reconciliation_notes", []).append(
        {
            "at": at,
            "stage": job.STAGE,
            "discarded_contract": identity.token(discarded),
            "restored_contract": identity.token(evidence["contract"]),
            "reason": "Restored the exact approved plan after an inherited-test-only runner correction; "
            "fresh tests checked, all review charges, allowances and historical evidence retained",
        }
    )
    return True

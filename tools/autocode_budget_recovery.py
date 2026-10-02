"""Pure, fail-closed recovery of explicitly runner-owned execution budgets.

The caller supplies authoritative runner state, not model-produced reports, and
persists the limit and ledger together before retrying. ``now`` is an aware ISO
timestamp (as returned by support.now). No files, clocks or providers are read.
Existing unmarked settings are protected. Only an absent planning review limit
has known default provenance (2); a present unmarked planning limit is protected.
A run must retain its resolver ledger
across resumes; changing a limit or resetting an iteration is not a new run.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math


HARD_CEILINGS = {
    "iteration_ceiling": 30,
    "stage_timeout_seconds": 7200,
    "max_seconds": 86400,
    "milestone_max_seconds": 10800,
}
# New runs start with these finite limits unless the user sets their own. Each is a
# runner default, so AutoResolver may double it once after verified progress, up to
# HARD_CEILINGS. The iteration ceiling has no default: new runs are unlimited there.
RUNNER_DEFAULTS = {"max_seconds": 43200, "stage_timeout_seconds": 3600}
RECENT_SECONDS = 1800
PLANNING_KIND = "planning_review_call_limit"
_CEILINGS = {**HARD_CEILINGS, PLANNING_KIND: 4}


def _number(value):
    return (type(value) is int and value >= 0) or (
        type(value) is float and math.isfinite(value) and value >= 0)


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
        return parsed.timestamp() if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _fingerprint(value):
    """Ignore whitespace, dictionary ordering and list ordering, not plan content."""
    def normalize(item):
        if isinstance(item, str):
            return " ".join(item.split())
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return sorted((normalize(child) for child in item), key=lambda child: json.dumps(child, sort_keys=True))
        return item

    try:
        return hashlib.sha256(json.dumps(normalize(value), sort_keys=True, allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError, RecursionError):
        return None


def _planning_evidence(state, planning, stages, clock):
    # A completed revision, not an attempted review or a self-reported resolution,
    # is the only additional authority. Independent final review still must run.
    if (state["settings"].get("joint_planning") is not True or state.get("next_stage") != "astra_finalize"
            or planning.get("final_token") or planning.get("recovery_review_grants", []) != []
            or type(planning.get("recovery_review_calls_used", 0)) is not int
            or planning.get("recovery_review_calls_used", 0) != 0
            or any(row.get("count") != 0 for row in state.get("failure_history", {}).values())):
        return None
    for name in ("consecutive_timeouts", "consecutive_timeout_recoveries", "automatic_recoveries_since_resume"):
        if type(state.get(name, 0)) is not int or state.get(name, 0) != 0:
            return None
    attempts = [(index, row) for index, row in enumerate(stages) if not row.get("runner_owned")]
    if not attempts or any(type(row.get("exit_code")) is not int or row["exit_code"] != 0
                           or any(row.get(flag) for flag in (
                               "timed_out", "rejected", "abandoned", "report_only", "dry_run",
                               "automatic_recovery", "planning_recovery_grant", "failure_key", "cleanup_error"))
                           for _, row in attempts):
        return None
    reports = planning.get("reports")
    history = state.get("planning_history", [])
    if not isinstance(reports, dict) or not isinstance(history, list):
        return None
    bound = {}
    for name in ("astra_discovery", "astra_challenge", "glm_revise"):
        entry = reports.get(name)
        if (not isinstance(entry, dict) or not isinstance(entry.get("report"), dict)
                or not isinstance(entry.get("output"), str) or not entry["output"]):
            return None
        matches = [(index, row) for index, row in attempts
                   if row.get("stage") == name and row.get("output") == entry["output"]]
        if len(matches) != 1:
            return None
        bound[name] = matches[0]
        for cycle in history:
            if not isinstance(cycle, dict) or not isinstance(cycle.get("reports"), dict):
                return None
            if (cycle.get("recovery_review_grants", []) != []
                    or type(cycle.get("recovery_review_calls_used", 0)) is not int
                    or cycle.get("recovery_review_calls_used", 0) != 0):
                return None
            for previous in cycle["reports"].values():
                if not isinstance(previous, dict) or previous.get("output") == entry["output"]:
                    return None
    draft_index, draft_row = bound["astra_discovery"]
    challenge_index, challenge_row = bound["astra_challenge"]
    revised_index, revised_row = bound["glm_revise"]
    finished = _timestamp(revised_row.get("finished_at"))
    source = revised_row.get("source_revision")
    if (not draft_index < challenge_index < revised_index or revised_index != attempts[-1][0]
            or finished is None or not 0 <= clock - finished <= RECENT_SECONDS
            or not isinstance(source, str) or not source
            or draft_row.get("source_revision") != source or challenge_row.get("source_revision") != source):
        return None
    draft = reports["astra_discovery"]["report"].get("contract")
    revised = reports["glm_revise"]["report"].get("contract")
    concerns = reports["astra_challenge"]["report"].get("concerns")
    responses = reports["glm_revise"]["report"].get("responses")
    if (not isinstance(draft, dict) or not isinstance(revised, dict)
            or revised.get("open_blocking_questions") != []
            or not isinstance(concerns, list) or not concerns or not isinstance(responses, list)):
        return None
    ids = [row.get("id") for row in concerns if isinstance(row, dict)]
    response_ids = [row.get("concern_id") for row in responses if isinstance(row, dict)]
    if (len(ids) != len(concerns) or len(response_ids) != len(responses)
            or any(not isinstance(value, str) or not value.strip() for value in ids + response_ids)
            or len(set(ids)) != len(ids) or len(set(response_ids)) != len(response_ids)
            or set(ids) != set(response_ids)):
        return None
    for response in responses:
        refs = response.get("evidence_refs")
        if (not isinstance(refs, list) or not refs
                or any(not isinstance(ref, str) or not ref.strip() for ref in refs)):
            return None
    before, after = _fingerprint(draft), _fingerprint(revised)
    # Changes to summaries, revision numbers, audience or rhetorical framing do
    # not constitute a changed execution plan. Preserve all original data.
    fields = ("required_behaviors", "important_failure_cases", "acceptance_criteria",
              "technical_approach", "end_to_end_flow", "milestones")
    prior_plan = {key: draft.get(key) for key in fields}
    next_plan = {key: revised.get(key) for key in fields}
    if (not before or not after or before == after
            or after != _fingerprint(state["goal_contract"].get("body"))
            or _fingerprint(prior_plan) == _fingerprint(next_plan)
            or any(not isinstance(revised.get(key), list) or not revised[key]
                   or any(not isinstance(row, dict) for row in revised[key])
                   for key in ("milestones", "acceptance_criteria"))):
        return None
    return {"source": "accepted_revised_plan", "output": revised_row["output"],
            "discovery_output": draft_row["output"], "challenge_output": challenge_row["output"],
            "stage_index": revised_index, "body_from": before, "body_to": after,
            "concern_ids": ids, "source_revision": source}


def recover(state, *, kind, now) -> bool:
    """Change one settings/planning limit and resolver.budget_extensions on success.

    Accepted progress means an accounted, successful committed ``stages`` row
    with actual changed files, finished within 30 minutes. Runner-owned receipts,
    report repairs, dry runs, rejected and abandoned attempts never qualify.
    Stage timeout may instead use a recent ActivityMonitor explicit-tool snapshot
    from the latest accounted stage timeout; provider text/process inference does
    not qualify. No active/unreconciled attempt may remain at this boundary.

    Every provider attempt must have known input/output tokens and duration,
    including failures. Missing usage is not zero.
    Failure counts >= 3 or two consecutive timeouts prevent recovery. All prior
    records and usage are immutable; the append-only ledger is the retry fence.
    Planning permits only 2 -> 4, from an absent default or marked runner default,
    backed by a current accepted changed plan, never timeout recovery grants.
    """
    if not isinstance(state, dict) or not isinstance(kind, str) or kind not in _CEILINGS:
        return False
    clock = _timestamp(now)
    settings = state.get("settings")
    resolver = state.get("resolver", {})
    if clock is None or not isinstance(settings, dict) or not isinstance(resolver, dict):
        return False
    planning = state.get("planning")
    origins = {}
    if kind == PLANNING_KIND:
        if not isinstance(planning, dict):
            return False
        origin = planning.get("review_call_limit_origin", "runner_default" if "review_call_limit" not in planning else None)
        if origin not in ("runner_default", "resolver_delegated"):
            return False
    else:
        origins = settings.get("budget_origins")
        if not isinstance(origins, dict) or origins.get(kind) not in ("runner_default", "resolver_delegated"):
            return False
    ledger = resolver.get("budget_extensions", [])
    if (not isinstance(ledger, list) or any(not isinstance(row, dict) for row in ledger)
            or any(row.get("kind") == kind for row in ledger)):
        return False
    for row in ledger:
        saved_kind = row.get("kind")
        if (not isinstance(saved_kind, str) or saved_kind not in _CEILINGS
                or row.get("idempotency_key") != "budget-extension:v1:" + saved_kind
                or not _number(row.get("from")) or not _number(row.get("to"))
                or not 0 < row["from"] < row["to"] <= _CEILINGS[saved_kind]
                or _timestamp(row.get("at")) is None or not isinstance(row.get("evidence"), dict)):
            return False
    limits = settings.get("limits")
    container = settings.get("milestone_checkpoints") if kind == "milestone_max_seconds" else limits
    key = "max_seconds" if kind == "milestone_max_seconds" else kind
    if kind == PLANNING_KIND:
        container, key = planning, "review_call_limit"
    if not isinstance(limits, dict) or not isinstance(container, dict):
        return False
    old = container.get(key, 2 if kind == PLANNING_KIND else None)
    if kind == PLANNING_KIND and (type(old) is not int or old != 2):
        return False
    if not _number(old) or old <= 0 or (kind == "iteration_ceiling" and type(old) is not int):
        return False
    new = min(old * 2, _CEILINGS[kind])
    if not _number(new) or new <= old:
        return False
    if any(state.get(name) for name in (
            "active_stage", "pending_questions", "pending_report_repair", "uncertain_artifacts",
            "active_uncertainty", "pause_requested", "billing_restriction", "access_restriction",
            "provider_quota_exhausted", "usage_unknown", "failure_loop")):
        return False
    for request in (state.get("user_request"), state.get("agent_request")):
        if request:
            if not isinstance(request, dict):
                return False
            request = request.get("request", request)
            if not isinstance(request, dict) or request.get("kind") != "none":
                return False
    contract = state.get("goal_contract", {})
    if not isinstance(contract, dict) or not isinstance(contract.get("body", {}), dict):
        return False
    if contract.get("body", {}).get("open_blocking_questions"):
        return False
    status = state.get("status", "RUNNING")
    if not isinstance(status, str) or (status.startswith("PAUSED") and status not in {
            "PAUSED_ITERATION_LIMIT", "PAUSED_TIME_LIMIT", "PAUSED_STAGE_TIMEOUT",
            "PAUSED_MILESTONE_BUDGET", "PAUSED_MILESTONE_TIME_LIMIT"}
            and not (kind == PLANNING_KIND and status == "PAUSED_PLANNING_BUDGET")):
        return False
    if type(state.get("no_progress_batches")) is not int or state["no_progress_batches"] < 0:
        return False
    for name in ("consecutive_timeout_recoveries", "consecutive_timeouts"):
        count = state.get(name, 0)
        if type(count) is not int or count < 0 or count >= 2:
            return False
    failures = state.get("failure_history", {})
    if not isinstance(failures, dict):
        return False
    for failure in failures.values():
        if (not isinstance(failure, dict) or type(failure.get("count")) is not int
                or failure["count"] < 0 or failure["count"] >= 3):
            return False
    stages = state.get("stages")
    if not isinstance(stages, list) or not stages or any(not isinstance(row, dict) for row in stages):
        return False
    total_tokens = 0
    unknown_usage = False
    for row in stages:
        if row.get("runner_owned") is True:
            continue
        metrics = row.get("metrics")
        tokens = metrics.get("provider_tokens") if isinstance(metrics, dict) else None
        if row.get("accounted") is not True or not _number(row.get("duration_seconds")) or not isinstance(tokens, dict):
            return False
        values = [tokens.get(name) for name in ("input_tokens", "output_tokens")]
        if all(type(value) is int and value >= 0 for value in values):
            total_tokens += sum(values)
        elif (kind == 'max_seconds' and origins.get(kind) == 'resolver_delegated'
              and all(value is None for value in values)):
            unknown_usage = True  # Preserved explicitly below; never converted to zero.
        else:
            return False
    used = state.get("iteration") if kind == "iteration_ceiling" else state.get("active_seconds")
    if kind == PLANNING_KIND:
        used = planning.get("astra_calls")
        if type(used) is not int or used != 2:
            return False
    if kind == "milestone_max_seconds":
        task, progress = state.get("current_task"), state.get("milestone_progress")
        if (not isinstance(task, dict) or not isinstance(progress, dict) or not contract.get("hash")
                or task.get("contract_hash") != contract["hash"]):
            return False
        matches = [row for row in progress.values() if isinstance(row, dict)
                   and row.get("contract_hash") == contract["hash"]
                   and ((task.get("milestone_ids") and row.get("milestone_ids") == task["milestone_ids"])
                        or (not task.get("milestone_ids") and task.get("milestone_id")
                            and row.get("id") == task["milestone_id"]))]
        if (len(matches) != 1 or type(matches[0].get("reviews_without_progress", 0)) is not int
                or matches[0].get("reviews_without_progress", 0) != 0):
            return False
        used = matches[0].get("seconds")
    latest = next((row for row in reversed(stages) if not row.get("runner_owned")), None)
    attempts = [row for row in stages if not row.get("runner_owned")]
    if len(attempts) >= 2 and all(any(row.get(flag) for flag in (
            "rejected", "abandoned", "timed_out")) for row in attempts[-2:]):
        return False
    if kind == "stage_timeout_seconds":
        if (not latest or latest.get("timed_out") is not True or latest.get("timeout_kind") != "stage"
                or latest.get("report_only") or latest.get("cleanup_error")):
            return False
        used = latest.get("duration_seconds")
    if (not _number(used)
            or (not old < used <= new if kind == 'iteration_ceiling' else not old <= used < new)
            or (kind == "iteration_ceiling" and type(used) is not int)):
        return False
    evidence = _planning_evidence(state, planning, stages, clock) if kind == PLANNING_KIND else None
    if evidence is None and kind in ('iteration_ceiling', 'max_seconds', 'milestone_max_seconds'):
        validation = state.get('validation') or {}
        if validation.get('verdict') == 'PASS' and isinstance(validation.get('output'), str):
            for index, row in reversed(list(enumerate(stages))):
                finished = _timestamp(row.get('finished_at'))
                if (row.get('stage') == 'sol' and row.get('output') == validation['output']
                        and row.get('source_revision') == validation.get('source_revision')
                        and finished is not None and 0 <= clock - finished <= RECENT_SECONDS
                        and type(row.get('exit_code')) is int and row['exit_code'] == 0
                        and row.get('accounted') is True
                        and not any(row.get(flag) for flag in (
                            'rejected', 'abandoned', 'timed_out', 'report_only', 'runner_owned',
                            'dry_run', 'automatic_recovery', 'planning_recovery_grant', 'cleanup_error'))
                        and (kind != 'milestone_max_seconds' or row.get('task_id') == task.get('id'))):
                    evidence = {'source': 'accepted_independent_validation', 'stage_index': index,
                                'output': row['output'], 'source_revision': row['source_revision']}
                    break
    for index, row in (reversed(list(enumerate(stages))) if kind != PLANNING_KIND else ()):
        finished = _timestamp(row.get("finished_at"))
        changed = row.get("changed_files")
        if (finished is not None and 0 <= clock - finished <= RECENT_SECONDS
                and type(row.get("exit_code")) is int and row["exit_code"] == 0
                and row.get("accounted") is True
                and not any(row.get(flag) for flag in (
                    "rejected", "abandoned", "report_only", "runner_owned", "timed_out", "dry_run"))
                and isinstance(changed, list) and changed
                and (kind != "milestone_max_seconds" or row.get("task_id") == task.get("id"))
                and all(isinstance(path, str) and path.strip() for path in changed)
                and isinstance(row.get("output"), str) and row["output"]):
            evidence = {"source": "accepted_changed_files", "stage_index": index, "output": row["output"]}
            break
    if evidence is None and kind == "stage_timeout_seconds":
        activity = latest.get("activity")
        if isinstance(activity, dict):
            observed = _timestamp(activity.get("observed_at"))
            tool_used, tool_limit = activity.get("tool_elapsed_seconds"), activity.get("tool_limit_seconds")
            if (observed is not None and 0 <= clock - observed <= 60
                    and activity.get("activity") == "running_tool"
                    and activity.get("process_fallback") is False
                    and type(activity.get("active_tool_count")) is int and activity["active_tool_count"] > 0
                    and _number(tool_used) and _number(tool_limit) and 0 <= tool_used < tool_limit
                    and _number(activity.get("stage_limit_seconds")) and activity["stage_limit_seconds"] == old
                    and isinstance(latest.get("output"), str) and latest["output"]):
                evidence = {"source": "activity_monitor_explicit_tool", "output": latest["output"],
                            "observed_at": activity["observed_at"]}
    no_progress = state['no_progress_batches']
    no_progress_limit = limits.get('no_progress_batches', 0)
    validation_progress = evidence and evidence.get('source') == 'accepted_independent_validation'
    if no_progress and (not validation_progress or (type(no_progress_limit) is int and no_progress_limit > 0
                                                     and no_progress >= no_progress_limit)):
        return False
    if unknown_usage and not validation_progress:
        return False
    if evidence is None:
        return False
    evidence = dict(evidence, unknown_usage_preserved=unknown_usage, known_reported_tokens=total_tokens)
    entry = {"kind": kind, "from": old, "to": new, "evidence": evidence, "at": now,
             "cause": "runner_default_exhausted_with_progress", "used": used,
             "idempotency_key": "budget-extension:v1:" + kind}
    container[key] = new
    if "resolver" not in state:
        state["resolver"] = resolver
    resolver["budget_extensions"] = [*ledger, entry]
    return True

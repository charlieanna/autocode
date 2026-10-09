"""Outcome-denominated measurements, not a second acceptance gate.

This module alone writes state.efficiency_observations. Scheduling/recovery
adapters emit immutable runner observations; the public run view and campaign
aggregator read them. Stage/finding/contract records remain their own authority.
No observation, model claim, screenshot, or green test creates visual acceptance.
"""
from __future__ import annotations

from copy import deepcopy
import datetime as dt
import json
import math
import warnings

try:
    from . import autocode_usage as usage
except ImportError:
    import autocode_usage as usage

CATEGORIES = ("planning", "build", "visual_capture_review", "functional_proof",
              "deterministic_setup", "diagnosis", "report_repair", "waiting", "other")
REASONS = ("source_invalidated", "independent_obligation", "environment_changed",
           "new_failure", "duplicate_no_new_information", "unknown")
VISUAL_PREREQUISITE = ("UNKNOWN: no trusted current-bound visual acceptance projection from #250/#251 was supplied. "
                       "Reported PASS, file hashes and captures are not acceptance.")


def _dict(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def record_observation(state, *, event_id, kind, category, reason=None, provenance=None,
                       attempt_id=None, finding_ids=(), started_at=None, finished_at=None):
    """Best-effort idempotent measurement. Conflicts are retained as warnings.

    `reuse`/`suppressed` record decisions, not execution time or invented savings.
    A repeated real check is `repeat`; its reason distinguishes obligations from
    duplicate work. Only actual clock intervals measure wall time.
    """
    try:
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("An observation needs an exact event ID")
        if kind not in ("activity", "repeat", "reuse", "suppressed") or category not in CATEGORIES:
            raise ValueError("Unknown observation kind/category")
        if reason is not None and reason not in REASONS:
            raise ValueError("Unknown repeat reason")
        value = {"event_id": event_id, "kind": kind, "category": category, "reason": reason or "unknown",
                 "provenance": deepcopy(provenance or {}), "attempt_id": attempt_id,
                 "finding_ids": sorted(set(finding_ids)), "started_at": started_at, "finished_at": finished_at}
        json.dumps(value, allow_nan=False)
        ledger = state.setdefault("efficiency_observations", [])
        previous = next((row for row in ledger if row.get("event_id") == event_id), None)
        if previous is not None:
            if previous == value:
                return True
            # Preserve the original immutable event. A separate conflict marker
            # invalidates this ID on projection rather than silently overwriting.
            marker = {"event_id": event_id, "kind": "conflict", "reason": "Conflicting observation replay",
                      "conflicting_observation": value}
            if marker not in ledger:
                ledger.append(marker)
            raise ValueError(f"Conflicting observation replay: {event_id}")
        ledger.append(value)
        return True
    except (TypeError, ValueError, KeyError, AttributeError) as error:
        warnings.warn(f"Efficiency observation was not recorded: {error}", RuntimeWarning, stacklevel=2)
        return False


def observe_replay(state, replay, *, attempt_id):
    """Retain runner replay measurements, including a precommit crash's proof.

    Called with the scheduling module's verified return value, not model JSON.
    Reuse carries the immutable original execution interval. Importing that
    interval by receipt identity keeps it counted once even if the original
    controller died before its state transaction committed.
    """
    for check in _rows(_dict(replay).get("checks")):
        schedule = _dict(check.get("scheduling"))
        if not schedule:
            continue
        receipt, sha = schedule.get("receipt"), schedule.get("receipt_sha256")
        if not receipt or not sha or schedule.get("action") not in ("execute", "reuse"):
            warnings.warn("Replay measurement lacks an exact scheduling receipt", RuntimeWarning, stacklevel=2)
            continue
        reused = schedule["action"] == "reuse"
        original_reason = schedule.get("original_reason") if reused else schedule.get("reason")
        reason = {"execution_context_changed": "environment_changed",
                  "failed_partial_or_missing_output": "new_failure",
                  "mandatory_approved_execution": "independent_obligation",
                  "prescribed_execution": "independent_obligation",
                  "new_obligation": "independent_obligation"}.get(original_reason, "unknown")
        key = f"verification:{sha}:{schedule.get('attempt_id')}"
        provenance = {"receipt": receipt, "receipt_sha256": sha, "identity": schedule.get("identity"),
                      "command": check.get("command"), "purpose": check.get("purpose", "independent_clean_replay"),
                      "results": deepcopy(check.get("results")), "exit_code": check.get("exit_code"),
                      "timed_out": check.get("timed_out"), "original_reason": original_reason or "unknown"}
        record_observation(state, event_id=key + ":execute", kind="activity", category="functional_proof",
            reason=reason, provenance=provenance, attempt_id="runner-check:" + str(schedule.get("attempt_id")),
            started_at=schedule.get("original_started_at") if reused else schedule.get("started_at"),
            finished_at=schedule.get("original_finished_at") if reused else schedule.get("finished_at"))
        if reused:
            record_observation(state, event_id=key + ":reuse:" + str(schedule.get("started_at")), kind="reuse",
                category="functional_proof", reason="duplicate_no_new_information", attempt_id=attempt_id,
                provenance={**provenance, "original_execution": key + ":execute"},
                started_at=schedule.get("started_at"), finished_at=schedule.get("finished_at"))


def _time(value):
    if type(value) in (int, float):
        return float(value) if math.isfinite(value) else None
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _category(row):
    stage = str(row.get("stage") or "")
    if row.get("report_only") or stage.endswith("_report_repair"):
        return "report_repair"
    if row.get("planning") or stage in ("recognize_workflow", "requirements_gather", "astra_discovery",
            "glm_revise", "astra_challenge", "astra_finalize", "astra_plan", "review_design", "check_design"):
        return "planning"
    if stage in ("terra", "builder"):
        return "build"
    if stage in ("astra_resolve", "astra_diagnose", "investigate_stuck", "investigate_bug"):
        return "diagnosis"
    if stage in ("visual_review", "visual_capture"):
        return "visual_capture_review"
    if stage in ("sol", "astra_review", "astra_checkpoint", "review_change", "regression_proof", "check_replay"):
        return "functional_proof"
    if stage in ("task_preflight", "preflight", "setup"):
        return "deterministic_setup"
    return "other"


def _timing(attempts, observations, now):
    intervals, durations, missing = [], [], 0
    conflicts = [row for row in observations if row.get("kind") == "conflict" and row.get("execution_possible")]
    attempt_ids = {row.get(key) for row in attempts for key in ("identity", "attempt_id", "events", "output") if row.get(key)}
    for row in attempts:
        start, end = _time(row.get("started_at")), _time(row.get("finished_at"))
        if row.get("active") and end is None:
            end = _time(now)
        if start is not None and end is not None and end >= start:
            intervals.append((start, end, row["category"]))
        else:
            missing += 1
        duration = row.get("duration_seconds")
        if row.get("active") and start is not None and end is not None and end >= start:
            duration = end - start
        durations.append(duration)
    for row in observations:
        if row.get("kind") not in ("activity", "repeat"):
            continue
        start, end = _time(row.get("started_at")), _time(row.get("finished_at"))
        # An observation on a model attempt is an attribution, not another copy
        # of that attempt's elapsed time. Sub-activities still join wall union.
        if start is not None and end is not None and end >= start:
            intervals.append((start, end, row["category"]))
            if row.get("attempt_id") not in attempt_ids and row["category"] != "waiting":
                durations.append(end - start)
        elif row.get("attempt_id") not in attempt_ids or row.get("started_at") or row.get("finished_at"):
            missing += 1
            if row.get("attempt_id") not in attempt_ids and row["category"] != "waiting":
                durations.append(None)
    missing += len(conflicts)
    durations.extend(None for _ in conflicts)
    boundaries = sorted({time for interval in intervals for time in interval[:2]})
    allocated = dict.fromkeys((*CATEGORIES, "parallel_overlap"), 0.0)
    for start, end in zip(boundaries, boundaries[1:]):
        categories = {kind for left, right, kind in intervals if left < end and right > start}
        if categories:
            allocated[next(iter(categories)) if len(categories) == 1 else "parallel_overlap"] += end - start
    known_sum = sum(value for value in durations if value is not None)
    def union(spans):
        points = sorted({value for span in spans for value in span})
        return sum(right - left for left, right in zip(points, points[1:])
                   if any(start < right and end > left for start, end in spans))
    native, incomplete_native = {}, False
    for row in attempts:
        if not row.get("runner_owned") and not row.get("request_coverage_complete"):
            incomplete_native = True
        for request in row.get("requests") or []:
            start, end = request.get("started_at_ms"), request.get("finished_at_ms")
            if (type(start) in (int, float) and type(end) in (int, float) and
                    math.isfinite(start) and math.isfinite(end) and end >= start):
                native[tuple(request["identity"])] = (start / 1000, end / 1000)
            else:
                incomplete_native = True
    return {"observed_wall_union_seconds": sum(allocated.values()),
            "wall_union_seconds": sum(allocated.values()) if not missing else None,
            "summed_execution_seconds": known_sum if all(value is not None for value in durations) else None,
            "known_summed_execution_seconds": known_sum, "missing_intervals": missing,
            # Unknown activity can overlap any otherwise known interval. Keep
            # the allocation of the known subset separate from complete totals.
            "exclusive_wall_seconds_by_category": dict.fromkeys(allocated) if conflicts else allocated,
            "observed_exclusive_wall_seconds_by_category": dict(allocated),
            "conflicted_observation_ids": [row.get("event_id") for row in conflicts],
            "observed_provider_request_union_seconds": union(list(native.values())),
            "provider_request_union_seconds": None if incomplete_native else union(list(native.values())),
            "scope": "Union of actual observed execution intervals; simultaneous different categories are parallel_overlap, never added twice",
            "intervals": [{"started_at": left, "finished_at": right, "category": kind}
                          for left, right, kind in intervals]}


def _observations(state):
    grouped, observations, issues = {}, [], []
    for row in _rows(state.get("efficiency_observations")):
        grouped.setdefault(row.get("event_id"), []).append(row)
    for event_id, rows in grouped.items():
        candidates, incomplete = [], False
        for row in rows:
            candidate = row.get("conflicting_observation") if row.get("kind") == "conflict" else row
            if not isinstance(candidate, dict):
                # Older conflict markers did not retain the alternative. Even
                # an original reuse decision cannot prove no activity occurred.
                incomplete = True
            elif candidate not in candidates:
                candidates.append(candidate)
        if len(candidates) == 1 and not any(row.get("kind") == "conflict" for row in rows):
            observations.append(candidates[0])
            continue
        incomplete |= any(row.get("kind") not in ("activity", "repeat", "reuse", "suppressed")
                          or row.get("category") not in CATEGORIES for row in candidates)
        activities = [row for row in candidates if row.get("kind") in ("activity", "repeat")]
        observations.append({"event_id": event_id, "kind": "conflict", "candidates": deepcopy(candidates),
            "execution_possible": incomplete or bool(activities),
            "affected_categories": list(CATEGORIES) if incomplete else sorted({row["category"] for row in activities})})
        issues.append(f"Conflicting observation: {event_id}")
    return observations, issues


def _visual(state, acceptance=None, *, now=None, accounting=None):
    manifest = _dict(_dict(state.get("settings")).get("design_manifest"))
    body = _dict(manifest.get("body"))
    cases = _rows(body.get("cases"))
    validation = _dict(state.get("validation"))
    reports = {row.get("id"): row for row in _rows(validation.get("design_results"))}
    rows = []
    for case in cases:
        report = reports.get(case.get("id"), {}) if validation.get("design_manifest_hash") == manifest.get("manifest_hash") else {}
        # A recorded FAIL is a conservative blocker, not an authenticated current
        # failure claim. A saved PASS never fills the absent visual gate.
        failed = report.get("status") == "FAIL"
        rows.append({"id": case.get("id"), "key": [case.get("file_key"), case.get("node_id"),
                    case.get("state"), deepcopy(case.get("viewport"))],
                    "file_key": case.get("file_key"), "frame": case.get("node_id"), "state": case.get("state"),
                    "reported_status": report.get("status"), "criterion_ids": deepcopy(report.get("criterion_ids") or []),
                    "accepted": False if failed else None, "status": "NOT_ACCEPTED" if failed else "UNKNOWN",
                    "first_accepted_at": None, "current_accepted_at": None,
                    "reason": "Reported visual mismatch; no accepted receipt" if failed else VISUAL_PREREQUISITE})
    frames = {(row.get("file_key"), row.get("node_id")) for row in cases}
    frame_rows = [{"key": [file_key, node], "accepted": False if any(
        row["accepted"] is False for row in rows if row["file_key"] == file_key and row["frame"] == node) else None}
        for file_key, node in sorted(frames, key=repr)]
    accepted = 0 if rows and all(row["accepted"] is False for row in rows) else None
    result = {"required": bool(manifest), "manifest_hash": manifest.get("manifest_hash"),
            "requested_frames": len(frames) if frames else None, "requested_states": len(rows) if rows else None,
            "accepted_frames": 0 if accepted == 0 else None, "accepted_states": accepted,
            "known_accepted_frames": 0, "known_accepted_states": 0,
            "current_all_accepted": False if accepted == 0 else None,
            "projection_valid": None, "coverage_complete": False, "issues": [], "unverified_criterion_ids": [],
            "acceptance_coverage_complete": False, "cases": rows, "frames": frame_rows,
            "first_independently_accepted_screen": {"at": None, "elapsed_seconds": None, "usage": None,
                                                     "historical_at": None, "historical_elapsed_seconds": None,
                                                     "reason": VISUAL_PREREQUISITE},
            "gaps": [{"id": row["id"], "status": row["status"], "reason": row["reason"]} for row in rows],
            "reason": VISUAL_PREREQUISITE if manifest else "No requested visual inventory"}
    if acceptance is None:
        return result

    # The runtime has authenticated source, captures, reviewer and actual image
    # delivery. This consumer only checks that its projection describes exactly
    # this approved inventory; model/state fields can never enter this branch.
    errors = []
    criteria = _rows(_dict(_dict(state.get("goal_contract")).get("body")).get("acceptance_criteria")) or _rows(state.get("acceptance_criteria"))
    criteria_ids = [row.get("id") for row in criteria]
    supplied = acceptance.get("cases") if isinstance(acceptance, dict) else None
    supplied_rows = _rows(supplied)
    case_ids = [case.get("id") for case in cases]
    supplied_ids = [row.get("id") for row in supplied_rows]
    text = lambda value: isinstance(value, str) and bool(value.strip())
    if (not cases or not isinstance(body.get("cases"), list) or len(cases) != len(body["cases"])
            or not all(text(cid) for cid in case_ids) or len(set(case_ids)) != len(case_ids)):
        errors.append("Approved visual inventory is missing or has invalid/duplicate case identities")
    elif any(not all(text(case.get(key)) for key in ("file_key", "node_id", "state"))
             or not isinstance(case.get("viewport"), dict) for case in cases):
        errors.append("Approved visual case lacks its file/frame/state/viewport identity")
    else:
        declared = [(file.get("key"), node) for file in _rows(body.get("files"))
                    if isinstance(file.get("nodes"), list) for node in file["nodes"]]
        if (not declared or any(not text(file) or not text(node) for file, node in declared)
                or len(set(declared)) != len(declared) or set(declared) != frames):
            errors.append("Approved file/frame inventory differs from its required cases")
        identities = [json.dumps([case["file_key"], case["node_id"], case["state"], case["viewport"]], sort_keys=True)
                      for case in cases]
        if len(set(identities)) != len(identities):
            errors.append("Approved visual inventory repeats a frame/state/viewport identity")
    if (not criteria_ids or not all(text(cid) for cid in criteria_ids)
            or len(set(criteria_ids)) != len(criteria_ids)):
        errors.append("Approved criterion identities are missing or duplicated")
    if (not isinstance(supplied, list) or len(supplied_rows) != len(supplied)
            or not all(text(cid) for cid in supplied_ids) or len(set(supplied_ids)) != len(supplied_ids)
            or sorted(supplied_ids) != sorted(case_ids, key=str)):
        errors.append("Visual projection must contain every approved case exactly once, without foreign cases")
    if not errors:
        expected = {case["id"]: case for case in cases}
        for row in supplied_rows:
            case = expected[row["id"]]
            mapped = row.get("criterion_ids")
            if (any(row.get(key) != case[key] for key in ("file_key", "node_id"))
                    or any(key in row and row[key] != case.get(key) for key in ("state", "viewport", "route"))):
                errors.append(f"Visual projection has a foreign case identity: {row['id']}")
            if (not isinstance(mapped, list) or not mapped or not all(text(cid) for cid in mapped)
                    or len(set(mapped)) != len(mapped) or not set(mapped) <= set(criteria_ids)):
                errors.append(f"Visual projection has an invalid criterion mapping: {row['id']}")
            verdict = row.get("verdict")
            if verdict not in ("PASS", "FAIL", "NOT_VERIFIED") or row.get("current_accepted") is not {
                    "PASS": True, "FAIL": False, "NOT_VERIFIED": None}.get(verdict):
                errors.append(f"Visual projection has an inconsistent decision: {row['id']}")
    if errors:
        reason = "UNKNOWN: invalid trusted visual projection: " + "; ".join(errors)
        for row in rows:
            row.update(accepted=None, status="UNKNOWN", reason=reason)
        for row in frame_rows:
            row["accepted"] = None
        result.update(projection_valid=False, accepted_frames=None, accepted_states=None,
                      current_all_accepted=None, issues=errors, reason=reason,
                      unverified_criterion_ids=sorted({cid for item in supplied_rows
                          if isinstance(item.get("criterion_ids"), list) for cid in item["criterion_ids"]
                          if text(cid) and cid in criteria_ids}),
                      gaps=[{"id": row["id"], "status": "UNKNOWN", "reason": reason} for row in rows])
        result["first_independently_accepted_screen"]["reason"] = reason
        return result

    supplied_by_id = {row["id"]: row for row in supplied_rows}
    for row in rows:
        receipt = supplied_by_id[row["id"]]
        decision = receipt["current_accepted"]
        def timestamp(key):
            value = receipt.get(key)
            if value is not None and (not isinstance(value, str) or _time(value) is None):
                result["issues"].append(f"Visual {key} timestamp is unavailable for {row['id']}")
                return None
            return value
        historical = timestamp("historical_accepted_at")
        current = timestamp("accepted_at") if decision is True else None
        row.update(criterion_ids=deepcopy(receipt["criterion_ids"]), verdict=receipt["verdict"], accepted=decision,
                   status="ACCEPTED" if decision is True else "NOT_ACCEPTED" if decision is False else "UNKNOWN",
                   first_accepted_at=historical, historical_accepted_at=historical, current_accepted_at=current,
                   reason="Current independent visual acceptance" if decision is True else
                          "Current independent visual failure" if decision is False else "Current visual acceptance is not verified")
    for frame in frame_rows:
        states = [row["accepted"] for row in rows if [row["file_key"], row["frame"]] == frame["key"]]
        frame["accepted"] = False if False in states else True if all(value is True for value in states) else None
    complete = all(row["accepted"] is not None for row in rows)
    known_states = sum(row["accepted"] is True for row in rows)
    known_frames = sum(row["accepted"] is True for row in frame_rows)
    all_accepted = False if any(row["accepted"] is False for row in rows) else True if complete else None
    result.update(projection_valid=True, accepted_states=known_states if complete else None,
                  accepted_frames=known_frames if all(row["accepted"] is not None for row in frame_rows) else None,
                  known_accepted_states=known_states, known_accepted_frames=known_frames,
                  current_all_accepted=all_accepted, coverage_complete=complete, acceptance_coverage_complete=complete,
                  gaps=[{"id": row["id"], "status": row["status"], "reason": row["reason"]} for row in rows if row["accepted"] is not True],
                  reason="Current independently accepted visual inventory" if all_accepted is True else
                         "Current visual inventory contains a failed case" if all_accepted is False else "Current visual coverage is incomplete")
    accepted_rows = [row for row in rows if row["accepted"] is True]
    first = min(accepted_rows, key=lambda row: _time(row["current_accepted_at"])) if accepted_rows and all(
        row["current_accepted_at"] is not None for row in accepted_rows) else None
    at = first["current_accepted_at"] if first else None
    history = [row["historical_accepted_at"] for row in rows if row["historical_accepted_at"] is not None]
    historical_at = min(history, key=_time) if history else None
    start, cutoff, observed_now = _time(state.get("created_at")), _time(at), _time(now)
    elapsed = cutoff - start if start is not None and cutoff is not None and cutoff >= start and (
        observed_now is not None and cutoff <= observed_now) else None
    historical_elapsed = _time(historical_at) - start if start is not None and historical_at is not None and _time(historical_at) >= start else None
    first_usage = _usage_before(accounting, cutoff) if elapsed is not None else None
    result["first_independently_accepted_screen"] = {
        "at": at, "case_id": first["id"] if first else None, "elapsed_seconds": elapsed,
        "historical_at": historical_at, "historical_elapsed_seconds": historical_elapsed,
        "usage": first_usage, "reason": None if elapsed is not None else "Current acceptance/start timestamp coverage is incomplete",
        "usage_reason": None if first_usage is not None else "Native request coverage at the acceptance cutoff is incomplete",
        "basis": "Earliest accepted_at among current accepted cases; historical_at is separate and never a current denominator"}
    return result


def _usage_before(accounting, cutoff):
    """Only finished native requests with a fully observed cutoff, never proration."""
    if not accounting or not accounting.get("complete") or not _dict(accounting.get("provider_requests")).get("complete"):
        return None
    selected, seen = [], set()
    for attempt in accounting.get("attempts") or []:
        if attempt.get("runner_owned"):
            continue
        if not attempt.get("requests") or attempt.get("active"):
            return None
        requests = []
        for request in attempt["requests"]:
            identity = tuple(request["identity"])
            if identity in seen:
                continue
            seen.add(identity)
            start, finish = request.get("started_at_ms"), request.get("finished_at_ms")
            if (type(start) not in (int, float) or type(finish) not in (int, float)
                    or not math.isfinite(start) or not math.isfinite(finish) or finish < start):
                return None
            if start / 1000 <= cutoff < finish / 1000:
                return None
            if finish / 1000 <= cutoff:
                requests.append(request)
        if requests:
            selected.append(dict(attempt, requests=requests, historical_estimated_cost_usd=None))
    if not selected:
        return None
    measured = usage.combine_accounting(selected)
    return {"tokens": measured["tokens"], "reported_usd": measured["cost"]["reported_usd"],
            "provider_requests": measured["provider_requests"],
            "scope": "All observed native requests finished by first current acceptance; no request crosses the cutoff"}


def _ratio(value, denominator, reason):
    return {"value": value / denominator if value is not None and denominator is not None and denominator > 0 else None,
            "denominator": denominator,
            "reason": None if value is not None and denominator is not None and denominator > 0 else reason}


def _category_usage(attempts, observations, now):
    result = {}
    attempt_ids = {row.get(key) for row in attempts for key in ("identity", "attempt_id", "events", "output") if row.get(key)}
    for category in CATEGORIES:
        rows = [row for row in attempts if row["category"] == category]
        activities = [row for row in observations if row.get("category") == category
                      and row.get("kind") in ("activity", "repeat") and row.get("attempt_id") not in attempt_ids]
        conflicts = [row for row in observations if row.get("kind") == "conflict" and row.get("execution_possible")
                     and category in row.get("affected_categories", [])]
        quantities, durations = {}, []
        for row in rows:
            duration = row.get("duration_seconds")
            if row.get("active") and _time(row.get("started_at")) is not None and _time(now) is not None:
                duration = max(0, _time(now) - _time(row["started_at"]))
            durations.append(duration)
        for row in activities:
            start, end = _time(row.get("started_at")), _time(row.get("finished_at"))
            durations.append(end - start if start is not None and end is not None and end >= start else None)
        durations.extend(None for _ in conflicts)
        for key in usage.request_usage.TOKEN_KEYS:
            values = [row["attributed_tokens"][key] for row in rows]
            quantities[key] = {"value": sum(row["known"] for row in values) if all(row["complete"] for row in values) else None,
                               "known": sum(row["known"] for row in values), "complete": all(row["complete"] for row in values)}
        result[category] = {"attempts": len(rows), "activity_observations": len(activities),
            "conflicted_activity_observations": len(conflicts), "tokens": quantities,
            "reported_usd": {"value": sum(row["attributed_reported_usd"]["known"] for row in rows)
                if all(row["attributed_reported_usd"]["complete"] for row in rows) else None,
                "known": sum(row["attributed_reported_usd"]["known"] for row in rows)},
            "summed_execution_seconds": sum(durations) if all(value is not None for value in durations) else None,
            "known_summed_execution_seconds": sum(value for value in durations if value is not None)}
    return result


def summary(state, now=None, *, accounting=None, completion_current=None, visual_acceptance=None):
    """Current acceptance requires a trusted current-checkout completion gate result.

    Never read that authority from saved/model state. Without the caller's fresh
    check, a saved completion is historical only. visual_acceptance may only be
    the runtime's freshly authenticated autocode_visual_acceptance.summary, not
    raw receipts or model/state PASS claims. This consumer cannot mint authority.
    """
    now = dt.datetime.now(dt.timezone.utc).isoformat() if now is None else now
    measured = deepcopy(accounting if accounting is not None else usage.accounting(state))
    observations, issues = _observations(state)
    attempts = measured["attempts"]
    aliases = {row["attempt_id"]: row["identity"] for row in attempts if row.get("attempt_id")}
    for row in attempts:
        labels = {item["category"] for item in observations
                  if item.get("attempt_id") in (row.get("attempt_id"), row.get("identity"), row.get("events"), row.get("output"))
                  and item.get("attempt_id") and item.get("kind") in ("activity", "repeat")}
        row["category"] = next(iter(labels)) if len(labels) == 1 else _category(row)
        row["category_basis"] = "runner_observation" if len(labels) == 1 else "stage_primary_purpose"
        if len(labels) > 1:
            issues.append(f"Ambiguous usage attribution for {row['identity']}")
    timing = _timing(attempts, observations, now)
    if measured.get("parallel_workers_pending"):
        timing.update(wall_union_seconds=None, summed_execution_seconds=None, provider_request_union_seconds=None,
                      coverage_issue="Pending worker intervals are absent from the parent view")
    by_category = _category_usage(attempts, observations, now)
    visual = _visual(state, visual_acceptance, now=now, accounting=measured)
    issues.extend(visual["issues"])
    done = state.get("status") in ("TASK_COMPLETE", "COMPLETE")
    current_completion = (completion_current if type(completion_current) is bool else None) if done else False
    criteria = _rows(_dict(_dict(state.get("goal_contract")).get("body")).get("acceptance_criteria")) or _rows(state.get("acceptance_criteria"))
    decision = _dict(state.get("last_decision"))
    report = _dict(decision.get("report")) or decision
    outcomes = {row.get("id"): row.get("status") for row in _rows(report.get("acceptance_criteria"))}
    criterion_rows = [{"id": row.get("id"), "reported_status": outcomes.get(row.get("id")),
        "accepted": current_completion if current_completion is not True else
                    outcomes.get(row.get("id")) in ("PASS", "verified")} for row in criteria]
    for criterion in criterion_rows:
        cases = [case for case in visual["cases"] if criterion["id"] in case["criterion_ids"]]
        if criterion["accepted"] is True and criterion["id"] in visual["unverified_criterion_ids"]:
            criterion["accepted"] = None  # Invalid projection mappings can veto, never establish acceptance.
        elif cases and criterion["accepted"] is True:
            criterion["accepted"] = (False if any(case["accepted"] is False for case in cases) else
                                     True if all(case["accepted"] is True for case in cases) else None)
    finding_rows = []
    repairs_complete = all(row.get("task_id") for row in attempts if row["category"] == "build")
    for finding in _rows(state.get("findings_ledger")):
        tasks = {item.get("task_id") for item in _rows(finding.get("assigned_history"))}
        if finding.get("assigned_task"):
            tasks.add(finding["assigned_task"])
        ids = {row["identity"] for row in attempts if row["category"] == "build" and row.get("task_id") in tasks and row.get("task_id")}
        ids.update(aliases.get(item.get("attempt_id"), item.get("attempt_id")) for item in observations
                   if finding.get("id") in item.get("finding_ids", []) and item.get("attempt_id")
                   and item.get("category") == "build" and item.get("kind") in ("activity", "repeat"))
        finding_rows.append({"id": finding.get("id"), "status": finding.get("status"),
                             "repair_attempts": len(ids) if repairs_complete else None,
                             "known_repair_attempts": len(ids), "complete": repairs_complete,
                             "attempt_ids": sorted(ids)})
    repeats = [row for row in observations if row.get("kind") == "repeat"]
    redundant = [row for row in repeats if row.get("reason") == "duplicate_no_new_information"]
    delivered = None if current_completion is None else int(current_completion)
    if current_completion is True and visual["required"]:
        delivered = None if visual["current_all_accepted"] is None else int(visual["current_all_accepted"])
    denominator = visual["accepted_states"] if current_completion is True and visual["required"] else delivered
    reason = ("Current completion has not been checked" if current_completion is None else
              "No currently verified delivery" if current_completion is False else
              "No independently accepted visual denominator" if visual["required"] else "No currently verified delivery")
    visual_usage_available = not (visual["projection_valid"] is True and visual["known_accepted_states"]
                                 and not any(not row.get("runner_owned") for row in attempts))
    if not visual_usage_available:
        issues.append("Visual acceptance has no measured provider attempts; unit usage and timing coverage are unknown")
    units = {key: _ratio(value["value"] if visual_usage_available else None, denominator,
                        reason if value["value"] is not None and visual_usage_available else "Usage coverage is incomplete")
             for key, value in measured["tokens"].items()}
    units["reported_usd"] = _ratio(measured["cost"]["reported_usd"]["value"] if visual_usage_available else None,
                                   denominator, "Cost or accepted denominator is unknown")
    units["wall_seconds"] = _ratio(timing["wall_union_seconds"] if visual_usage_available else None, denominator,
                                  "Timing coverage is incomplete" if timing["wall_union_seconds"] is None or not visual_usage_available else reason)
    def visual_units(denominator):
        return {key: _ratio(value["value"] if visual_usage_available else None, denominator, "Visual denominator or usage is unknown")
                for key, value in {**measured["tokens"], "reported_usd": measured["cost"]["reported_usd"]}.items()}
    return {"schema": 1, "run_id": usage.run_identity(state.get("run_dir") or state.get("task_id")),
            "parent_run": usage.run_identity(state.get("parent_run")), "status": state.get("status"),
            "delivery": {"taskrun_done": done, "completed_tasks": int(done),
                "current_completion": current_completion, "verified_deliveries": delivered,
                "completed_tasks_basis": "Saved TaskRun status; historical, not current acceptance",
                "basis": reason if current_completion is not True else
                         visual["reason"] if visual["required"] else "Trusted current-checkout completion gate; not visual fidelity"},
            "criteria": {"requested": len(criteria) if criteria else None,
                "accepted": 0 if criteria and current_completion is False else sum(row["accepted"] for row in criterion_rows)
                    if criteria and all(row["accepted"] is not None for row in criterion_rows)
                    and (not visual["required"] or all(case["criterion_ids"] for case in visual["cases"])) else None,
                "known_accepted": sum(row["accepted"] is True for row in criterion_rows),
                "rows": criterion_rows, "gaps": [row["id"] for row in criterion_rows if not row["accepted"]]},
            "visual": visual, "time": timing, "by_category": by_category,
            "attribution": {"basis": "Stage primary purpose or explicit runner observation; not an inferred split of model reasoning",
                "unclassified_attempts": sum(row["category"] == "other" for row in attempts),
                "uninstrumented_categories": [category for category in CATEGORIES if not by_category[category]["attempts"]
                    and not any(row.get("category") == category for row in observations)],
                "waiting": "Only explicit waiting intervals are measured; gaps between stages are not presumed waiting"},
            "unit_metrics": {"denominator_kind": "accepted_requested_state" if visual["required"] else "completed_task", **units,
                "per_accepted_frame": visual_units(visual["accepted_frames"] if current_completion is True
                                                   else delivered if visual["required"] else None),
                "per_accepted_state": visual_units(visual["accepted_states"] if current_completion is True
                                                   else delivered if visual["required"] else None)},
            "repair_attempts_per_finding": finding_rows,
            "recovery_attempts": [{"identity": row["identity"], **deepcopy(row["recovery_novelty"])}
                                  for row in attempts if row.get("recovery_novelty")],
            "repeats": {"observed": len(repeats), "by_reason": {reason: sum(row.get("reason") == reason for row in repeats) for reason in REASONS},
                "redundant_checks_observed": sum(row.get("category") in ("functional_proof", "visual_capture_review") for row in redundant),
                "reused_observed": sum(row.get("kind") == "reuse" for row in observations),
                "suppressed_observed": sum(row.get("kind") == "suppressed" for row in observations),
                "scope": "Instrumented runner observations only; absence is not proof of zero uninstrumented repeats"},
            "observations": deepcopy(observations), "issues": [*measured["issues"], *issues]}


def aggregate(views, now=None):
    """Campaign accounting from public TaskRun views, including failed/worker runs.

    No run discovery, private state access, grading model, pricing catalog or
    success-only filter. Supply one latest view per run and all active workers.
    """
    now = dt.datetime.now(dt.timezone.utc).isoformat() if now is None else now
    unique, issues = {}, []
    for index, view in enumerate(views):
        efficiency = _dict(view.get("efficiency"))
        key = usage.run_identity(efficiency.get("run_id"))
        if key is None:
            key = f"unidentified:{index}"
            issues.append("Public view has no run identity; deduplication is unavailable")
        if key in unique and unique[key] != view:
            raise ValueError(f"Conflicting public snapshots for {key}; supply one latest view per run")
        unique[key] = view
    attempts, observations, missing_accounting = [], {}, []
    for run_key, view in unique.items():
        accounted = _dict(view.get("usage")).get("accounting")
        visual = _dict(_dict(view.get("efficiency")).get("visual"))
        if (not isinstance(accounted, dict) or visual.get("projection_valid") is True
                and any(row.get("accepted") is True for row in _rows(visual.get("cases")))
                and not any(not row.get("runner_owned") for row in _rows(_dict(accounted).get("attempts")))):
            missing_accounting.append(run_key)
        own_observations = _dict(view.get("efficiency")).get("observations") or []
        for row in _dict(_dict(view.get("usage")).get("accounting")).get("attempts") or []:
            labels = {item["category"] for item in own_observations if item.get("attempt_id")
                      and item.get("attempt_id") in (row.get("identity"), row.get("attempt_id"), row.get("events"), row.get("output"))
                      and item.get("kind") in ("activity", "repeat")}
            attempts.append(dict(row, category=next(iter(labels)) if len(labels) == 1 else _category(row)))
        for row in own_observations:
            key = (run_key, row.get("event_id"))
            observations[key] = row
            if row.get("kind") == "conflict":
                issues.append(f"Conflicting observation: {run_key}/{row.get('event_id')}")
    measured = usage.combine_accounting(attempts)
    available_runs = {str(key) for key in unique}
    unresolved = [worker for view in unique.values()
                  for worker in _dict(_dict(view.get("usage")).get("accounting")).get("parallel_worker_runs") or []
                  if not worker.get("run_id") or worker["run_id"] not in available_runs]
    if unresolved or missing_accounting:
        measured["complete"] = False
        for quantity in measured["tokens"].values():
            quantity.update(value=None, complete=False)
        measured["cost"]["reported_usd"].update(value=None, complete=False)
        measured["cost"]["historical_estimated_usd"].update(value=None, complete=False)
        measured["provider_requests"].update(value=None, complete=False)
        if unresolved:
            issues.append("Missing public views for pending parallel workers")
        if missing_accounting:
            issues.append("Public views lack exact-attempt accounting; absent legacy measurements are not zero")
    tagged = measured["attempts"]
    roots = [view for view in unique.values() if not _dict(view.get("efficiency")).get("parent_run")]
    done = sum(view.get("done") is True for view in roots)
    delivered, current = [], []
    for view in roots:
        projected = _dict(view.get("efficiency"))
        delivery = _dict(projected.get("delivery"))
        fresh = delivery.get("current_completion") if view.get("done") is True else False
        fresh = fresh if type(fresh) is bool else None
        current.append(fresh)
        verified_delivery = delivery.get("verified_deliveries") if fresh is True else 0 if fresh is False else None
        visual = _dict(projected.get("visual"))
        if fresh is True and visual.get("required") is True:
            cases = _rows(visual.get("cases"))
            verified_delivery = (0 if visual.get("current_all_accepted") is False else
                1 if visual.get("projection_valid") is True and visual.get("acceptance_coverage_complete") is True
                     and visual.get("current_all_accepted") is True and cases
                     and all(case.get("accepted") is True for case in cases) else None)
        elif fresh is True and visual.get("required") is not False:
            verified_delivery = None
        delivered.append(verified_delivery)
    verified = sum(delivered) if all(value is not None for value in delivered) else None
    units = {key: _ratio(value["value"], verified, "Root delivery denominator or usage coverage is unknown")
             for key, value in {**measured["tokens"], "reported_usd": measured["cost"]["reported_usd"]}.items()}
    timing = _timing(tagged, list(observations.values()), now)
    categories = _category_usage(tagged, list(observations.values()), now)
    if unresolved or missing_accounting:
        timing.update(wall_union_seconds=None, summed_execution_seconds=None, provider_request_union_seconds=None,
                      coverage_issue="Worker or legacy attempt intervals are unavailable")
        for category in categories.values():
            category["summed_execution_seconds"] = None
            category["reported_usd"]["value"] = None
            for quantity in category["tokens"].values():
                quantity.update(value=None, complete=False)
    units["wall_seconds"] = _ratio(timing["wall_union_seconds"], verified,
        "Timing coverage is incomplete" if timing["wall_union_seconds"] is None else "Root delivery denominator is unknown")
    return {"schema": 1, "runs": len(roots), "worker_views": len(unique) - len(roots),
            "completed_tasks": done, "verified_deliveries": verified, "unit_metrics": units,
            "completed_tasks_basis": "Saved root TaskRun statuses; historical, not current acceptance",
            "known_verified_deliveries": sum(value for value in delivered if value is not None),
            "current_completion_counts": {"verified": sum(value is True for value in current),
                "not_verified": sum(value is False for value in current), "unknown": sum(value is None for value in current)},
            "usage": measured, "time": timing, "by_category": categories,
            "issues": [*issues, *measured["issues"]],
            "missing_worker_views": unresolved,
            "missing_accounting_views": missing_accounting,
            "scope": "All supplied public views; parent-imported attempts and native request events charged once"}

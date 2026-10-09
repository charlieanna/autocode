"""Append-only log of what AutoCode itself did in a run, always on.

Every checkpoint save compares a small snapshot of the run with the last one
logged and appends the differences to RUN/activity.jsonl: which command was
invoked (flag names only, and whether through autocode-unattended), status and phase transitions, stages started and
finished (role, model, exit, duration, token counts), findings counts, and
approvals. It records activity, never content: no prompts, plans, answers,
feedback, code, diffs, file names, findings text or model output. Flag values
are dropped because they can carry answers and feedback.

Unlike progress_messages in state.json, nothing is trimmed. Logging is
best-effort: a failure to append never fails the checkpoint or the run.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

LOG_NAME = "activity.jsonl"
STOP_REASON_LIMIT = 500

# Processes that already logged their invocation, by run directory.
_announced: set[str] = set()


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _model(command) -> str | None:
    if not isinstance(command, list):
        return None
    for flag in ("--model", "-m"):
        if flag in command and command.index(flag) + 1 < len(command):
            return str(command[command.index(flag) + 1])
    return None


def _stage_started(active: dict) -> dict:
    return {"event": "stage_started", "stage": active.get("stage"), "role": active.get("role"),
            "route_role": active.get("route_role"), "iteration": active.get("iteration"),
            "engine": active.get("engine"), "model": _model(active.get("command")),
            "task_id": active.get("task_id"), "started_at": active.get("started_at")}


def _stage_finished(record: dict) -> dict:
    tokens = (record.get("metrics") or {}).get("provider_tokens") or {}
    return {"event": "stage_finished", "stage": record.get("stage"), "role": record.get("role"),
            "route_role": record.get("route_role"), "iteration": record.get("iteration"),
            "engine": record.get("engine"), "model": _model(record.get("command")),
            "task_id": record.get("task_id"), "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at") or record.get("completed_at"),
            "duration_seconds": record.get("duration_seconds"), "exit_code": record.get("exit_code"),
            "timed_out": record.get("timed_out"), "rejected": bool(record.get("rejected")),
            "changed_files": len(record.get("changed_files") or []),
            "cost_usd": (record.get("metrics") or {}).get("provider_cost_usd"),
            "tokens": {key: tokens.get(key) for key in ("input_tokens", "cached_input_tokens",
                                                         "output_tokens", "reasoning_output_tokens")}}


def snapshot(state: dict) -> dict:
    """The activity-only view of a run that the log compares between saves."""
    active = state.get("active_stage") or {}
    ledger = state.get("findings_ledger") or []
    findings: dict[str, int] = {}
    for row in ledger:
        key = f"{row.get('status')}/{row.get('severity')}"
        findings[key] = findings.get(key, 0) + 1
    contract = state.get("goal_contract") or {}
    batch = state.get("orchestration_batch") or {}
    return {
        "status": state.get("status"), "phase": state.get("phase"), "next_stage": state.get("next_stage"),
        "iteration": state.get("iteration"), "task_id": (state.get("current_task") or {}).get("id"),
        "active_stage": [active.get("stage"), active.get("started_at")] if active else None,
        "stages": len(state.get("stages") or []),
        "findings": findings,
        "contract_revision": contract.get("revision"),
        "approval_status": contract.get("approval_status"),
        "human_reviews": sorted(state.get("human_reviews") or {}) if isinstance(state.get("human_reviews"), dict) else [],
        "builders": sorted([str(w.get("milestone_id")), str(w.get("status"))] for w in batch.get("workers", [])),
    }


def _last_snapshot(path: Path) -> dict | None:
    """Read the snapshot saved on the log's last line without loading the file."""
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            end = stream.tell()
            start = max(0, end - 65536)
            stream.seek(start)
            lines = stream.read().splitlines()
    except FileNotFoundError:
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if "snapshot" in entry:
            return entry["snapshot"]
    return None


def events(previous: dict | None, current: dict, state: dict) -> list[dict]:
    found = []
    before = previous or {}
    transition = {key: current[key] for key in ("status", "phase", "next_stage", "iteration", "task_id")
                  if before.get(key) != current[key]}
    if transition:
        entry = {"event": "transition", **{key: current[key] for key in
                                           ("status", "phase", "next_stage", "iteration", "task_id")},
                 "changed": sorted(transition)}
        if "status" in transition and state.get("stop_reason"):
            entry["stop_reason"] = str(state["stop_reason"])[:STOP_REASON_LIMIT]
        found.append(entry)
    if current["active_stage"] and current["active_stage"] != before.get("active_stage"):
        found.append(_stage_started(state.get("active_stage") or {}))
    stages = state.get("stages") or []
    logged = before.get("stages", 0)
    if len(stages) < logged:
        found.append({"event": "stages_rewound", "from": logged, "to": len(stages)})
        logged = len(stages)
    found += [_stage_finished(record) for record in stages[logged:] if isinstance(record, dict)]
    if previous is not None and current["findings"] != before.get("findings"):
        found.append({"event": "findings", "counts": current["findings"]})
    if current["contract_revision"] != before.get("contract_revision") and current["contract_revision"] is not None:
        found.append({"event": "plan_revision", "revision": current["contract_revision"]})
    if previous is not None and current["approval_status"] != before.get("approval_status"):
        found.append({"event": "plan_approval", "status": current["approval_status"],
                      "revision": current["contract_revision"]})
    for criterion in sorted(set(current["human_reviews"]) - set(before.get("human_reviews") or [])):
        found.append({"event": "human_review", "criterion": criterion})
    if current["builders"] != before.get("builders") and (current["builders"] or before.get("builders")):
        found.append({"event": "builders", "workers": current["builders"]})
    return found


def record(state_path, state: dict) -> None:
    """Append this save's activity to the run's log. Never raises."""
    try:
        path = Path(state_path).parent / LOG_NAME
        found = []
        key = str(path)
        if key not in _announced:
            _announced.add(key)
            found.append({"event": "invocation", "program": Path(sys.argv[0]).name,
                          "caller": os.environ.get("AUTOCODE_CALLER") or "direct",
                          "flags": [arg.split("=", 1)[0] for arg in sys.argv[1:] if arg.startswith("-")]})
        current = snapshot(state)
        found += events(_last_snapshot(path), current, state)
        if not found:
            return
        at, pid = _now(), os.getpid()
        lines = [{"at": at, "pid": pid, **entry} for entry in found]
        lines[-1]["snapshot"] = current
        with path.open("a") as stream:
            stream.write("".join(json.dumps(line, sort_keys=True) + "\n" for line in lines))
    except Exception:  # the activity log must never break a checkpoint
        pass

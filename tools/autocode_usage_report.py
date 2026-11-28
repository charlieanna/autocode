"""Aggregate public usage.jsonl ledgers across runs and projects (`autocode usage`, #3346).

Reads only ``PROJECT/.autocode/usage.jsonl`` ledgers (and public registry pointers when
``--all`` is requested); never reads any run's private ``state.json``. Fake-provider and
fixture runs stay strictly separate from live runs in every table, pre-Step-1 legacy rows are
reported as ``not_recorded`` rather than zero, and target comparisons grant no completion
authority.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

try:
    from . import autocode_quota_report as quota_report
    from . import autocode_registry as registry
    from . import autocode_rejection_classes as rejection_classes
    from . import autocode_roles as roles
    from . import autocode_route_scoreboard as scoreboard
except ImportError:
    import autocode_quota_report as quota_report
    import autocode_registry as registry
    import autocode_rejection_classes as rejection_classes
    import autocode_roles as roles
    import autocode_route_scoreboard as scoreboard

SCHEMA = 1
LEDGER = "usage.jsonl"
TARGETS_FILE = Path(__file__).resolve().parents[1] / "docs" / "efficiency-targets.json"
PROVENANCE_ORDER: tuple[str, ...] = ("live", "unknown", "mixed", "fake")
PLAN_SIZE_ORDER: tuple[str, ...] = ("small", "medium", "large", "unrecorded")
INTERACTION_EVENTS: tuple[str, ...] = ("first_question", "first_plan", "first_builder")
OUTCOME_BUCKETS: tuple[str, ...] = ("accepted", "rejected", "report_repair")
ACTIONS: tuple[str, ...] = ("list", "report", "quota", "scoreboard")

_DEFAULT_TARGETS: dict[str, Any] = {
    "schema": 1,
    "as_of": "2026-11-14",
    "basis": "Embedded fallback baseline when docs/efficiency-targets.json is outside package root.",
    "targets": {
        "non_productive_stage_time_share": {"max": 0.361, "source": "2026-11-11 baseline"},
        "rejected_answer_stage_time_share": {"max": 0.186, "source": "#3344 baseline"},
        "first_question_seconds": {"median_max": 360.0, "p90_max": 360.0, "source": "RELIABILITY.md"},
        "small_completed_task_model_calls": {"median_max": 6.0, "source": "6-stage small-task pipeline"},
    },
}


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _nonneg_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        num = float(value)
        return num if math.isfinite(num) and num >= 0 else None
    return None


def _nonneg_int(value: object) -> int | None:
    return int(value) if type(value) is int and value >= 0 else None


def read_ledger(path: Path) -> list[dict[str, Any]]:
    """Read valid JSON object rows from one ``usage.jsonl`` file."""
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def load_targets(path: Path | None = None) -> dict[str, Any]:
    """Load checked-in efficiency targets from ``docs/efficiency-targets.json``."""
    target_path = path or TARGETS_FILE
    try:
        loaded = json.loads(target_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and isinstance(loaded.get("targets"), dict):
            return loaded
    except (OSError, ValueError, UnicodeError):
        pass
    return _DEFAULT_TARGETS


def _ledger_paths_for_project(project_root: Path) -> list[tuple[Path, Path]]:
    roots: list[Path] = []
    try:
        resolved = project_root.resolve()
        roots.append(resolved)
    except OSError:
        return []
    worktrees_dir = resolved / ".autocode" / "worktrees"
    if worktrees_dir.is_dir():
        try:
            for child in sorted(worktrees_dir.iterdir()):
                if child.is_dir():
                    roots.append(child.resolve())
        except OSError:
            pass
    results: list[tuple[Path, Path]] = []
    seen_ledgers: set[Path] = set()
    for root in roots:
        ledger = root / ".autocode" / LEDGER
        if ledger.is_file() and ledger not in seen_ledgers:
            seen_ledgers.add(ledger)
            results.append((resolved, ledger))
    return results


def _registry_workspace_roots(registry_file: Path) -> list[Path]:
    try:
        data = json.loads(registry_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return []
    if not isinstance(data, dict):
        return []
    found: list[Path] = []
    for entry in _dict(data.get("workspaces")).values():
        path = _dict(entry).get("path")
        if isinstance(path, str) and path:
            found.append(Path(path))
    for entry in _dict(data.get("runs")).values():
        path = _dict(entry).get("workspace")
        if isinstance(path, str) and path:
            found.append(Path(path))
    return found


def discover_ledgers(workspace: Path, *, all_projects: bool = False) -> list[tuple[Path, Path]]:
    """Discover ``(project_root, usage.jsonl)`` pairs without reading any ``state.json``."""
    resolved_ws = workspace.resolve()
    canonical_project = registry._registry_project(resolved_ws)
    candidate_projects: list[Path] = [canonical_project]
    if resolved_ws != canonical_project:
        candidate_projects.append(resolved_ws)

    if all_projects:
        reg_dirs: list[Path] = [registry.storage_root()]
        ws_reg = registry.project_storage(resolved_ws)
        if ws_reg is not None:
            reg_dirs.append(ws_reg)
        reg_dirs.extend(registry.other_project_storage())
        for reg_dir in reg_dirs:
            if reg_dir.name == "registry" and reg_dir.parent.name == ".autocode":
                candidate_projects.append(reg_dir.parent.parent)
            candidate_projects.extend(_registry_workspace_roots(reg_dir / "registry.json"))
        for indexed in registry._read_project_index(registry.project_index_path()):
            candidate_projects.append(Path(indexed))

    unique_projects: list[Path] = []
    seen_projects: set[Path] = set()
    for candidate in candidate_projects:
        try:
            canon = registry._registry_project(candidate.resolve())
        except OSError:
            continue
        if canon not in seen_projects:
            seen_projects.add(canon)
            unique_projects.append(canon)
        if candidate.resolve() != canon and candidate.resolve() not in seen_projects:
            seen_projects.add(candidate.resolve())
            unique_projects.append(candidate.resolve())

    discovered: list[tuple[Path, Path]] = []
    seen_ledgers: set[Path] = set()
    for project in sorted(unique_projects):
        for owner, ledger in _ledger_paths_for_project(project):
            if ledger not in seen_ledgers:
                seen_ledgers.add(ledger)
                discovered.append((owner, ledger))
    return discovered


def _run_date(row: dict[str, Any]) -> dt.date | None:
    for key in ("started_at", "updated_at"):
        val = row.get(key)
        if isinstance(val, str) and len(val) >= 10:
            try:
                return dt.date.fromisoformat(val[:10])
            except ValueError:
                pass
    run_name = row.get("run")
    if isinstance(run_name, str) and len(run_name) >= 8 and run_name[:8].isdigit():
        try:
            return dt.date(int(run_name[:4]), int(run_name[4:6]), int(run_name[6:8]))
        except ValueError:
            pass
    return None


def collect_rows(
    workspace: Path,
    *,
    all_projects: bool = False,
    since: dt.date | None = None,
) -> list[dict[str, Any]]:
    """Collect deduplicated ledger rows for one project or every registered project."""
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for project_root, ledger_path in discover_ledgers(workspace, all_projects=all_projects):
        for row in read_ledger(ledger_path):
            run_name = str(row.get("run") or "")
            if not run_name:
                continue
            key = (str(project_root), run_name)
            existing = by_key.get(key)
            if existing is None or str(row.get("updated_at") or "") >= str(existing.get("updated_at") or ""):
                by_key[key] = row
    rows = list(by_key.values())
    if since is not None:
        filtered: list[dict[str, Any]] = []
        for row in rows:
            day = _run_date(row)
            if day is not None and day >= since:
                filtered.append(row)
        rows = filtered
    return sorted(
        rows, key=lambda item: (str(item.get("started_at") or item.get("updated_at") or ""), str(item.get("run") or ""))
    )


def _route_key(route: object) -> tuple[str, str, str]:
    row = _dict(route)
    provider = str(row.get("provider") or "unknown")
    model = str(row.get("model") or "unknown")
    level = str(row.get("level") or "default")
    return (provider, model, level)


def _route_dict(key: tuple[str, str, str]) -> dict[str, str]:
    return {"provider": key[0], "model": key[1], "level": key[2]}


def _route_label(route: dict[str, str]) -> str:
    return f"{route['provider']}:{route['model']}@{route['level']}"


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(pct * len(ordered)) - 1))
    return float(ordered[index])


def _stats(values: list[float], *, digits: int = 3) -> dict[str, Any]:
    if not values:
        return {"count": 0, "median": None, "p90": None}
    return {
        "count": len(values),
        "median": round(float(statistics.median(values)), digits),
        "p90": round(float(_percentile(values, 0.90) or 0.0), digits),
    }


def _aggregate_stage_outcomes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {bucket: {"launches": 0, "seconds": 0.0, "complete": True} for bucket in OUTCOME_BUCKETS}
    job_map: dict[str, dict[str, Any]] = {}
    recorded_runs = 0
    not_recorded_runs = 0

    for row in rows:
        outcomes = row.get("outcomes")
        if not isinstance(outcomes, dict) or not isinstance(outcomes.get("totals"), dict):
            not_recorded_runs += 1
            continue
        recorded_runs += 1
        row_totals = outcomes["totals"]
        row_complete = outcomes.get("complete") is not False
        for bucket in OUTCOME_BUCKETS:
            src = _dict(row_totals.get(bucket))
            attempts = _nonneg_int(src.get("attempts")) or 0
            secs = _nonneg_float(src.get("seconds")) or 0.0
            totals[bucket]["launches"] += attempts
            totals[bucket]["seconds"] = round(totals[bucket]["seconds"] + secs, 3)
            if src.get("complete") is False or not row_complete:
                totals[bucket]["complete"] = False

        for job, job_buckets in _dict(outcomes.get("by_job")).items():
            entry = job_map.setdefault(
                str(job),
                {
                    "job": str(job),
                    "accepted_launches": 0,
                    "accepted_seconds": 0.0,
                    "rejected_launches": 0,
                    "rejected_seconds": 0.0,
                    "report_repair_launches": 0,
                    "report_repair_seconds": 0.0,
                },
            )
            jb = _dict(job_buckets)
            for bucket in OUTCOME_BUCKETS:
                b_src = _dict(jb.get(bucket))
                entry[f"{bucket}_launches"] += _nonneg_int(b_src.get("attempts")) or 0
                entry[f"{bucket}_seconds"] = round(
                    entry[f"{bucket}_seconds"] + (_nonneg_float(b_src.get("seconds")) or 0.0), 3
                )

    total_launches = sum(totals[b]["launches"] for b in OUTCOME_BUCKETS)
    total_seconds = round(sum(totals[b]["seconds"] for b in OUTCOME_BUCKETS), 3)
    non_productive_seconds = round(totals["rejected"]["seconds"] + totals["report_repair"]["seconds"], 3)

    formatted_totals = {}
    for bucket in OUTCOME_BUCKETS:
        secs = totals[bucket]["seconds"]
        formatted_totals[bucket] = {
            "launches": totals[bucket]["launches"],
            "seconds": secs,
            "hours": round(secs / 3600.0, 2),
            "share": round(secs / total_seconds, 4) if total_seconds > 0 else None,
            "complete": totals[bucket]["complete"],
        }

    jobs = []
    for entry in job_map.values():
        job_total = round(entry["accepted_seconds"] + entry["rejected_seconds"] + entry["report_repair_seconds"], 3)
        job_non_prod = round(entry["rejected_seconds"] + entry["report_repair_seconds"], 3)
        jobs.append(
            {
                **entry,
                "total_seconds": job_total,
                "non_productive_share": round(job_non_prod / job_total, 4) if job_total > 0 else None,
            }
        )
    jobs.sort(key=lambda item: (-item["rejected_seconds"] - item["report_repair_seconds"], item["job"]))

    return {
        "recorded_runs": recorded_runs,
        "not_recorded_runs": not_recorded_runs,
        "total_launches": total_launches,
        "total_seconds": total_seconds,
        "total_hours": round(total_seconds / 3600.0, 2),
        "non_productive_seconds": non_productive_seconds,
        "non_productive_share": round(non_productive_seconds / total_seconds, 4) if total_seconds > 0 else None,
        "buckets": formatted_totals,
        "jobs": jobs,
    }


def _aggregate_rejections(
    rows: list[dict[str, Any]],
    *,
    total_stage_seconds: float,
    report_repair_seconds: float,
    outcome_jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    recorded_runs = 0
    not_recorded_runs = 0
    by_class: dict[str, dict[str, Any]] = {
        cls: {"class": cls, "launches": 0, "seconds": 0.0} for cls in rejection_classes.CLASSES
    }
    by_route: dict[tuple[str, str, str], dict[str, Any]] = {}
    by_job_class: dict[str, dict[str, Any]] = {}
    answer_rejected_seconds = 0.0
    repair_failures = {"launches": 0, "seconds": 0.0}
    total_rejected_seconds = 0.0

    for row in rows:
        items = row.get("rejections")
        if not isinstance(items, list):
            not_recorded_runs += 1
            continue
        recorded_runs += 1
        for raw_item in items:
            item = _dict(raw_item)
            cls = str(item.get("class") or "other")
            if cls not in by_class:
                cls = "other"
            secs = _nonneg_float(item.get("seconds")) or 0.0
            if item.get("report_repair") is True:
                repair_failures["launches"] += 1
                repair_failures["seconds"] = round(repair_failures["seconds"] + secs, 3)
                continue
            total_rejected_seconds = round(total_rejected_seconds + secs, 3)
            if cls in rejection_classes.ANSWER_CLASSES:
                answer_rejected_seconds = round(answer_rejected_seconds + secs, 3)
            by_class[cls]["launches"] += 1
            by_class[cls]["seconds"] = round(by_class[cls]["seconds"] + secs, 3)

            job = str(item.get("job") or "Other")
            job_entry = by_job_class.setdefault(job, {"job": job, "launches": 0, "seconds": 0.0, "classes": {}})
            job_entry["launches"] += 1
            job_entry["seconds"] = round(job_entry["seconds"] + secs, 3)
            job_entry["classes"][cls] = round(job_entry["classes"].get(cls, 0.0) + secs, 3)

            rkey = _route_key(item.get("route"))
            rentry = by_route.setdefault(
                rkey,
                {"route": _route_dict(rkey), "launches": 0, "seconds": 0.0, "classes": {}},
            )
            rentry["launches"] += 1
            rentry["seconds"] = round(rentry["seconds"] + secs, 3)
            rentry["classes"][cls] = round(rentry["classes"].get(cls, 0.0) + secs, 3)

    class_rows = []
    for entry in by_class.values():
        if entry["launches"] == 0 and entry["seconds"] == 0.0:
            continue
        secs = entry["seconds"]
        class_rows.append(
            {
                "class": entry["class"],
                "launches": entry["launches"],
                "seconds": secs,
                "hours": round(secs / 3600.0, 2),
                "share_of_stage_time": round(secs / total_stage_seconds, 4) if total_stage_seconds > 0 else None,
                "share_of_rejected_time": round(secs / total_rejected_seconds, 4)
                if total_rejected_seconds > 0
                else None,
            }
        )
    class_rows.sort(key=lambda item: (-item["seconds"], -item["launches"], item["class"]))

    outcome_job_index = {item["job"]: item for item in outcome_jobs}
    job_rows = []
    for job in sorted(set(outcome_job_index) | set(by_job_class)):
        oj = outcome_job_index.get(job, {})
        rej = by_job_class.get(job, {"launches": 0, "seconds": 0.0, "classes": {}})
        secs = rej["seconds"]
        if rej["launches"] == 0 and secs == 0.0 and not oj.get("rejected_launches"):
            continue
        job_rows.append(
            {
                "job": job,
                "launches": rej["launches"] or oj.get("rejected_launches", 0),
                "seconds": secs,
                "hours": round(secs / 3600.0, 2),
                "share_of_stage_time": round(secs / total_stage_seconds, 4) if total_stage_seconds > 0 else None,
                "report_repair_seconds": oj.get("report_repair_seconds", 0.0),
                "top_class": max(rej["classes"].items(), key=lambda kv: (kv[1], kv[0]))[0] if rej["classes"] else None,
            }
        )
    job_rows.sort(key=lambda item: (-item["seconds"], -item["launches"], item["job"]))

    route_rows = []
    for rentry in by_route.values():
        secs = rentry["seconds"]
        top_class = max(rentry["classes"].items(), key=lambda kv: (kv[1], kv[0]))[0] if rentry["classes"] else None
        route_rows.append(
            {
                "route": rentry["route"],
                "launches": rentry["launches"],
                "seconds": secs,
                "hours": round(secs / 3600.0, 2),
                "share_of_stage_time": round(secs / total_stage_seconds, 4) if total_stage_seconds > 0 else None,
                "top_class": top_class,
                "classes": rentry["classes"],
            }
        )
    route_rows.sort(key=lambda item: (-item["seconds"], -item["launches"], _route_label(item["route"])))

    rejected_answer_seconds = round(answer_rejected_seconds + report_repair_seconds, 3)
    return {
        "recorded_runs": recorded_runs,
        "not_recorded_runs": not_recorded_runs,
        "rejected_answer_seconds": rejected_answer_seconds,
        "rejected_answer_share": round(rejected_answer_seconds / total_stage_seconds, 4)
        if total_stage_seconds > 0
        else None,
        "by_class": class_rows,
        "by_job": job_rows,
        "by_route": route_rows,
        "report_repair_failures": repair_failures,
    }


def _aggregate_first_interaction(rows: list[dict[str, Any]]) -> dict[str, Any]:
    recorded_runs = 0
    not_recorded_runs = 0
    samples: dict[str, list[tuple[float, bool | None, tuple[str, str, str]]]] = {
        event: [] for event in INTERACTION_EVENTS
    }
    for row in rows:
        fi = row.get("first_interaction")
        if not isinstance(fi, dict):
            not_recorded_runs += 1
            continue
        recorded_runs += 1
        for event in INTERACTION_EVENTS:
            ev = _dict(fi.get(event))
            secs = _nonneg_float(ev.get("seconds"))
            if secs is None:
                continue
            wait = ev.get("human_wait") if isinstance(ev.get("human_wait"), bool) else None
            samples[event].append((secs, wait, _route_key(ev.get("route"))))

    events_out: dict[str, Any] = {}
    for event in INTERACTION_EVENTS:
        all_vals = [secs for secs, _, _ in samples[event]]
        no_wait_vals = [secs for secs, wait, _ in samples[event] if wait is False]
        human_waits = sum(wait is True for _, wait, _ in samples[event])
        unknown_waits = sum(wait is None for _, wait, _ in samples[event])

        route_groups: dict[tuple[str, str, str], list[tuple[float, bool | None]]] = {}
        for secs, wait, rkey in samples[event]:
            route_groups.setdefault(rkey, []).append((secs, wait))

        by_route = []
        for rkey, ritems in route_groups.items():
            rvals = [secs for secs, _ in ritems]
            summary = _stats(rvals)
            by_route.append(
                {
                    "route": _route_dict(rkey),
                    "count": summary["count"],
                    "median_seconds": summary["median"],
                    "p90_seconds": summary["p90"],
                    "human_wait_count": sum(wait is True for _, wait in ritems),
                }
            )
        by_route.sort(key=lambda item: (-(item["median_seconds"] or 0.0), _route_label(item["route"])))

        all_summary = _stats(all_vals)
        no_wait_summary = _stats(no_wait_vals)
        events_out[event] = {
            "count": all_summary["count"],
            "median_seconds": all_summary["median"],
            "p90_seconds": all_summary["p90"],
            "human_wait_count": human_waits,
            "human_wait_unknown_count": unknown_waits,
            "without_human_wait": {
                "count": no_wait_summary["count"],
                "median_seconds": no_wait_summary["median"],
                "p90_seconds": no_wait_summary["p90"],
            },
            "by_route": by_route,
        }

    return {
        "recorded_runs": recorded_runs,
        "not_recorded_runs": not_recorded_runs,
        "events": events_out,
    }


def _summarize_completed_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    calls: list[float] = []
    walls: list[float] = []
    spent: list[float] = []
    incomplete_cost = 0
    unrecorded_calls = 0
    unrecorded_wall = 0
    for outcome in rows:
        mc = _nonneg_int(outcome.get("model_calls"))
        if mc is None:
            unrecorded_calls += 1
        else:
            calls.append(float(mc))
        ws = _nonneg_float(outcome.get("wall_seconds"))
        if ws is None:
            unrecorded_wall += 1
        else:
            walls.append(ws)
        sp = _dict(outcome.get("spent_usd"))
        sv = _nonneg_float(sp.get("value"))
        if sp.get("complete") is True and sv is not None:
            spent.append(sv)
        else:
            incomplete_cost += 1
    call_stats = _stats(calls, digits=2)
    wall_stats = _stats(walls, digits=3)
    spent_stats = _stats(spent, digits=4)
    return {
        "completed_runs": len(rows),
        "model_calls": {
            **call_stats,
            "total": int(sum(calls)) if calls else None,
            "unrecorded_runs": unrecorded_calls,
        },
        "wall_seconds": {
            **wall_stats,
            "unrecorded_runs": unrecorded_wall,
        },
        "spent_usd": {
            "priced_runs": spent_stats["count"],
            "incomplete_runs": incomplete_cost,
            "median": spent_stats["median"],
            "p90": spent_stats["p90"],
            "total": round(sum(spent), 6) if spent else None,
        },
    }


def _aggregate_completed_tasks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    recorded_runs = 0
    not_recorded_runs = 0
    status_counts = {"completed": 0, "paused": 0, "abandoned": 0, "running": 0}
    pause_reasons: dict[str, int] = {}
    completed_by_size: dict[str, list[dict[str, Any]]] = {size: [] for size in PLAN_SIZE_ORDER}

    for row in rows:
        outcome = row.get("outcome")
        if not isinstance(outcome, dict) or outcome.get("state") not in status_counts:
            not_recorded_runs += 1
            continue
        recorded_runs += 1
        state_kind = str(outcome["state"])
        status_counts[state_kind] += 1
        if state_kind == "paused":
            reason = str(outcome.get("pause_reason_code") or outcome.get("run_status") or "PAUSED")
            pause_reasons[reason] = pause_reasons.get(reason, 0) + 1
        if state_kind == "completed":
            size = str(outcome.get("plan_size") or "unrecorded")
            if size not in completed_by_size:
                size = "unrecorded"
            completed_by_size[size].append(outcome)

    by_plan_size = {
        size: _summarize_completed_group(completed_by_size[size]) for size in PLAN_SIZE_ORDER if completed_by_size[size]
    }
    all_completed = [item for size in PLAN_SIZE_ORDER for item in completed_by_size[size]]
    return {
        "recorded_runs": recorded_runs,
        "not_recorded_runs": not_recorded_runs,
        "run_outcomes": status_counts,
        "pause_reasons": dict(sorted(pause_reasons.items())),
        "all_completed": _summarize_completed_group(all_completed),
        "by_plan_size": by_plan_size,
    }


def _aggregate_stage_prompts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    recorded_runs = 0
    not_recorded_runs = 0
    by_job: dict[str, dict[str, Any]] = {}
    all_overages: dict[str, int] = {}
    for row in rows:
        prompts = row.get("stage_prompts")
        if not isinstance(prompts, list):
            not_recorded_runs += 1
            continue
        recorded_runs += 1
        for raw_entry in prompts:
            entry = _dict(raw_entry)
            job = str(entry.get("job") or entry.get("stage") or "Other")
            bucket = by_job.setdefault(
                job,
                {
                    "bytes": [],
                    "instr_bytes": [],
                    "stable_bytes": [],
                    "changing_bytes": [],
                    "cache_read": [],
                    "cache_write": [],
                    "overages": {},
                    "top_fields": {},
                    "attempts": 0,
                },
            )
            bucket["attempts"] += 1
            for src_key, dst_key in (
                ("prompt_bytes", "bytes"),
                ("instruction_bytes", "instr_bytes"),
                ("stable_handoff_bytes", "stable_bytes"),
                ("changing_handoff_bytes", "changing_bytes"),
            ):
                val = _nonneg_int(entry.get(src_key))
                if val is not None:
                    bucket[dst_key].append(float(val))
            cread = _nonneg_int(entry.get("first_request_cache_read_tokens"))
            if cread is not None:
                bucket["cache_read"].append(float(cread))
            cwrite = _nonneg_int(entry.get("first_request_cache_write_tokens"))
            if cwrite is not None:
                bucket["cache_write"].append(float(cwrite))
            budgeted = entry.get("budgeted_fields")
            if isinstance(budgeted, list):
                for field in budgeted:
                    if isinstance(field, str) and field:
                        bucket["overages"][field] = bucket["overages"].get(field, 0) + 1
                        all_overages[field] = all_overages.get(field, 0) + 1
            top_fields = entry.get("top_handoff_fields")
            if isinstance(top_fields, list):
                for item in top_fields:
                    if not isinstance(item, dict) or not isinstance(item.get("field"), str):
                        continue
                    fbytes = _nonneg_int(item.get("bytes"))
                    if fbytes is not None:
                        bucket["top_fields"].setdefault(item["field"], []).append(float(fbytes))

    jobs_out = []
    for job, bucket in by_job.items():
        byte_stats = _stats(bucket["bytes"], digits=1)
        read_stats = _stats(bucket["cache_read"], digits=1)
        write_stats = _stats(bucket["cache_write"], digits=1)
        total_read = int(sum(bucket["cache_read"])) if bucket["cache_read"] else 0
        total_write = int(sum(bucket["cache_write"])) if bucket["cache_write"] else 0
        denom = total_read + total_write
        cache_hit_share = round(total_read / denom, 4) if denom > 0 else None
        top_summary = sorted(
            (
                {
                    "field": field,
                    "count": len(vals),
                    "median_bytes": _stats(vals, digits=1)["median"],
                }
                for field, vals in bucket["top_fields"].items()
            ),
            key=lambda item: (-(item["median_bytes"] or 0.0), -item["count"], item["field"]),
        )[:3]
        jobs_out.append(
            {
                "job": job,
                "attempts": bucket["attempts"],
                "prompt_bytes": byte_stats,
                "instruction_bytes": _stats(bucket["instr_bytes"], digits=1),
                "stable_handoff_bytes": _stats(bucket["stable_bytes"], digits=1),
                "changing_handoff_bytes": _stats(bucket["changing_bytes"], digits=1),
                "first_request_cache_read_tokens": {
                    "count": read_stats["count"],
                    "median": read_stats["median"],
                    "total": total_read if bucket["cache_read"] else None,
                },
                "first_request_cache_write_tokens": {
                    "count": write_stats["count"],
                    "median": write_stats["median"],
                    "total": total_write if bucket["cache_write"] else None,
                },
                "first_request_cache_hit_share": cache_hit_share,
                "field_budget_overages": dict(sorted(bucket["overages"].items())),
                "top_handoff_fields": top_summary,
            }
        )
    jobs_out.sort(key=lambda item: (-(item["prompt_bytes"]["median"] or 0.0), item["job"]))
    return {
        "recorded_runs": recorded_runs,
        "not_recorded_runs": not_recorded_runs,
        "field_budget_overages": dict(sorted(all_overages.items())),
        "by_job": jobs_out,
    }


def _evaluate_targets(
    targets_doc: dict[str, Any],
    *,
    stage_outcomes: dict[str, Any],
    rejections: dict[str, Any],
    first_interaction: dict[str, Any],
    completed_tasks: dict[str, Any],
) -> dict[str, Any]:
    spec = _dict(targets_doc.get("targets"))

    np_target = _nonneg_float(_dict(spec.get("non_productive_stage_time_share")).get("max"))
    np_measured = stage_outcomes.get("non_productive_share")
    np_ok = (np_measured <= np_target) if np_measured is not None and np_target is not None else None

    ra_target = _nonneg_float(_dict(spec.get("rejected_answer_stage_time_share")).get("max"))
    ra_measured = rejections.get("rejected_answer_share")
    ra_ok = (ra_measured <= ra_target) if ra_measured is not None and ra_target is not None else None

    fq_spec = _dict(spec.get("first_question_seconds"))
    fq_med_max = _nonneg_float(fq_spec.get("median_max"))
    fq_p90_max = _nonneg_float(fq_spec.get("p90_max"))
    fq_ev = _dict(_dict(first_interaction.get("events")).get("first_question"))
    fq_med = fq_ev.get("median_seconds")
    fq_p90 = fq_ev.get("p90_seconds")
    fq_ok = (
        (fq_med <= fq_med_max and fq_p90 <= fq_p90_max)
        if fq_med is not None and fq_p90 is not None and fq_med_max is not None and fq_p90_max is not None
        else None
    )

    sm_target = _nonneg_float(_dict(spec.get("small_completed_task_model_calls")).get("median_max"))
    sm_measured = _dict(_dict(_dict(completed_tasks.get("by_plan_size")).get("small")).get("model_calls")).get("median")
    sm_ok = (sm_measured <= sm_target) if sm_measured is not None and sm_target is not None else None

    return {
        "schema": targets_doc.get("schema", 1),
        "as_of": targets_doc.get("as_of"),
        "authority": "observational_only",
        "metrics": {
            "non_productive_stage_time_share": {
                "measured": np_measured,
                "target_max": np_target,
                "within_target": np_ok,
            },
            "rejected_answer_stage_time_share": {
                "measured": ra_measured,
                "target_max": ra_target,
                "within_target": ra_ok,
            },
            "first_question_seconds": {
                "measured_median": fq_med,
                "measured_p90": fq_p90,
                "without_human_wait": fq_ev.get("without_human_wait"),
                "target_median_max": fq_med_max,
                "target_p90_max": fq_p90_max,
                "within_target": fq_ok,
            },
            "small_completed_task_model_calls": {
                "measured_median": sm_measured,
                "target_median_max": sm_target,
                "within_target": sm_ok,
            },
        },
    }


def _aggregate_requirements(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """First Requirements attempt per run, plus all recorded repair time (including failed repairs)."""
    routes: dict[tuple[str, str, str], dict[str, Any]] = {}
    repair = {"attempts": 0, "seconds": 0.0, "complete": True, "recorded_runs": 0, "not_recorded_runs": 0}
    job = roles.screen_name("requirements_gather")
    for row in rows:
        first = _dict(row.get("requirements_first_pass"))
        accepted = first.get("accepted")
        if type(accepted) is bool:
            key = _route_key(first.get("route"))
            group = routes.setdefault(key, {"route": _route_dict(key), "accepted_runs": 0, "rejected_runs": 0})
            group["accepted_runs" if accepted else "rejected_runs"] += 1

        outcomes = _dict(row.get("outcomes"))
        jobs = outcomes.get("by_job")
        if not isinstance(jobs, dict):
            repair["not_recorded_runs"] += 1
            repair["complete"] = False
            continue
        source = (
            _dict(_dict(jobs[job]).get("report_repair"))
            if job in jobs
            else {"attempts": 0, "seconds": 0.0, "complete": True}
        )
        attempts, seconds = _nonneg_int(source.get("attempts")), _nonneg_float(source.get("seconds"))
        repair["recorded_runs"] += 1
        repair["attempts"] += attempts or 0
        repair["seconds"] = round(repair["seconds"] + (seconds or 0.0), 3)
        if (
            attempts is None
            or seconds is None
            or source.get("complete") is not True
            or outcomes.get("complete") is False
        ):
            repair["complete"] = False

    by_route = []
    for key in sorted(routes):
        group = routes[key]
        by_route.append(
            {**group, "acceptance_rate": group["accepted_runs"] / (group["accepted_runs"] + group["rejected_runs"])}
        )
    accepted_runs = sum(group["accepted_runs"] for group in by_route)
    rejected_runs = sum(group["rejected_runs"] for group in by_route)
    recorded = accepted_runs + rejected_runs
    return {
        "first_pass": {
            "accepted_runs": accepted_runs,
            "rejected_runs": rejected_runs,
            "not_recorded_runs": len(rows) - recorded,
            "acceptance_rate": accepted_runs / recorded if recorded else None,
            "by_route": by_route,
        },
        "report_repair": repair,
    }


def _aggregate_cohort(rows: list[dict[str, Any]], targets_doc: dict[str, Any]) -> dict[str, Any]:
    stage_outcomes = _aggregate_stage_outcomes(rows)
    rejections = _aggregate_rejections(
        rows,
        total_stage_seconds=stage_outcomes["total_seconds"],
        report_repair_seconds=stage_outcomes["buckets"]["report_repair"]["seconds"],
        outcome_jobs=stage_outcomes["jobs"],
    )
    first_interaction = _aggregate_first_interaction(rows)
    completed_tasks = _aggregate_completed_tasks(rows)
    stage_prompts = _aggregate_stage_prompts(rows)
    targets = _evaluate_targets(
        targets_doc,
        stage_outcomes=stage_outcomes,
        rejections=rejections,
        first_interaction=first_interaction,
        completed_tasks=completed_tasks,
    )
    return {
        "runs": len(rows),
        "not_recorded": {
            "outcomes_runs": stage_outcomes["not_recorded_runs"],
            "rejections_runs": rejections["not_recorded_runs"],
            "first_interaction_runs": first_interaction["not_recorded_runs"],
            "outcome_runs": completed_tasks["not_recorded_runs"],
            "stage_prompts_runs": stage_prompts["not_recorded_runs"],
        },
        "stage_outcomes": stage_outcomes,
        "rejections": rejections,
        "requirements": _aggregate_requirements(rows),
        "first_interaction": first_interaction,
        "completed_tasks": completed_tasks,
        "stage_prompts": stage_prompts,
        "targets": targets,
    }


def build_report(
    workspace: Path,
    *,
    all_projects: bool = False,
    since: dt.date | None = None,
    targets_path: Path | None = None,
) -> dict[str, Any]:
    """Aggregate usage.jsonl ledgers into one provenance-separated report payload."""
    rows = collect_rows(workspace, all_projects=all_projects, since=since)
    targets_doc = load_targets(targets_path)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        prov = str(row.get("evidence_provenance") or "unknown")
        if prov not in PROVENANCE_ORDER:
            prov = "unknown"
        grouped.setdefault(prov, []).append(row)

    by_provenance = {
        prov: _aggregate_cohort(grouped[prov], targets_doc) for prov in PROVENANCE_ORDER if prov in grouped
    }
    return {
        "schema": SCHEMA,
        "scope": "all_registered_projects" if all_projects else str(workspace.resolve()),
        "since": since.isoformat() if since else None,
        "total_runs": len(rows),
        "provenances_present": list(by_provenance),
        "by_provenance": by_provenance,
    }


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _fmt_num(value: float | int | None, suffix: str = "") -> str:
    if value is None:
        return "not recorded"
    if isinstance(value, int):
        return f"{value}{suffix}"
    return f"{value:.1f}{suffix}"


def format_report(payload: dict[str, Any]) -> str:
    """Render ``build_report`` as human-readable tables, never mixing fake and live runs."""
    lines = [
        f"Usage outcome report (scope: {payload['scope']}"
        + (f", since: {payload['since']}" if payload.get("since") else "")
        + f", runs: {payload['total_runs']})"
    ]
    if payload["total_runs"] == 0:
        lines.append("No matching runs recorded in usage.jsonl.")
        return "\n".join(lines)

    for prov in payload["provenances_present"]:
        cohort = payload["by_provenance"][prov]
        so = cohort["stage_outcomes"]
        rej = cohort["rejections"]
        nr = cohort["not_recorded"]
        lines.extend(["", f"=== Provenance: {prov} ({cohort['runs']} runs) ==="])
        if any(nr.values()):
            lines.append(
                f"Not recorded (older runs): outcomes={nr['outcomes_runs']}, "
                f"rejections={nr['rejections_runs']}, first_interaction={nr['first_interaction_runs']}, "
                f"run_outcome={nr['outcome_runs']}, stage_prompts={nr['stage_prompts_runs']}"
            )
        lines.append(
            f"Stage time: {so['total_hours']:.2f} h ({so['total_launches']} launches), "
            f"non-productive {_fmt_pct(so['non_productive_share'])}, "
            f"rejected-answer {_fmt_pct(rej['rejected_answer_share'])}"
        )
        lines.append(f"  {'outcome':<18} {'launches':>8} {'hours':>8} {'share':>8}")
        for bucket in OUTCOME_BUCKETS:
            b = so["buckets"][bucket]
            lines.append(f"  {bucket:<18} {b['launches']:>8} {b['hours']:>8.2f} {_fmt_pct(b['share']):>8}")

        requirements = cohort["requirements"]
        first = requirements["first_pass"]
        lines.append(
            f"  Requirements first pass: {first['accepted_runs']}/{first['accepted_runs'] + first['rejected_runs']} "
            f"accepted ({_fmt_pct(first['acceptance_rate'])}); not recorded={first['not_recorded_runs']}"
        )
        for group in first["by_route"]:
            lines.append(
                f"    {_route_label(group['route'])}: "
                f"{group['accepted_runs']}/{group['accepted_runs'] + group['rejected_runs']} "
                f"accepted ({_fmt_pct(group['acceptance_rate'])})"
            )
        repair = requirements["report_repair"]
        repair_time = (
            "not recorded"
            if not repair["recorded_runs"]
            else _fmt_num(repair["seconds"], "s")
            if repair["complete"]
            else f">={_fmt_num(repair['seconds'], 's')} (incomplete)"
        )
        lines.append(
            f"  Requirements report repair: {repair['attempts']} attempts, {repair_time}; "
            f"not recorded={repair['not_recorded_runs']}"
        )

        if rej["by_class"]:
            lines.append("  Rejected time by class:")
            for item in rej["by_class"]:
                lines.append(
                    f"    {item['class']:<26} {item['launches']:>5}x {item['hours']:>7.2f}h ({_fmt_pct(item['share_of_stage_time'])})"
                )
        if rej["by_job"]:
            lines.append("  Rejected time by job:")
            for item in rej["by_job"]:
                lines.append(
                    f"    {item['job']:<26} {item['launches']:>5}x {item['hours']:>7.2f}h ({_fmt_pct(item['share_of_stage_time'])})"
                )
        if rej["by_route"]:
            lines.append("  Rejected time by route:")
            for item in rej["by_route"]:
                lines.append(
                    f"    {_route_label(item['route']):<44} {item['launches']:>5}x {item['hours']:>7.2f}h (top: {item['top_class'] or 'n/a'})"
                )

        lines.append("  Time to first interaction:")
        for event in INTERACTION_EVENTS:
            ev = cohort["first_interaction"]["events"][event]
            lines.append(
                f"    {event:<16} n={ev['count']} median={_fmt_num(ev['median_seconds'], 's')} "
                f"p90={_fmt_num(ev['p90_seconds'], 's')} human_wait_runs={ev['human_wait_count']}"
            )

        ct = cohort["completed_tasks"]
        lines.append(f"  Completed tasks by plan size ({ct['all_completed']['completed_runs']} completed):")
        for size, group in ct["by_plan_size"].items():
            mc = group["model_calls"]
            ws = group["wall_seconds"]
            sp = group["spent_usd"]
            spent_str = "not recorded" if sp["median"] is None else f"${sp['median']:.2f}"
            lines.append(
                f"    {size:<12} n={group['completed_runs']} calls_med={_fmt_num(mc['median'])} "
                f"wall_med={_fmt_num(ws['median'], 's')} spent_med={spent_str}"
            )

        sp_summary = cohort["stage_prompts"]
        if sp_summary["by_job"]:
            lines.append(f"  Stage prompts ({sp_summary['recorded_runs']} runs):")
            for item in sp_summary["by_job"][:8]:
                lines.append(
                    f"    {item['job']:<22} n={item['attempts']} bytes_med={_fmt_num(item['prompt_bytes']['median'])} "
                    f"stable_med={_fmt_num(item['stable_handoff_bytes']['median'])} "
                    f"cache_hit={_fmt_pct(item['first_request_cache_hit_share'])}"
                )
            if sp_summary["field_budget_overages"]:
                overages = ", ".join(f"{k}={v}" for k, v in sp_summary["field_budget_overages"].items())
                lines.append(f"  Field budget overages: {overages}")

        tm = cohort["targets"]["metrics"]
        lines.append("  Targets (observational only):")
        for name, entry in tm.items():
            status = (
                "PASS"
                if entry["within_target"] is True
                else "OVER"
                if entry["within_target"] is False
                else "not recorded"
            )
            lines.append(f"    {name:<36} {status}  {json.dumps(entry, sort_keys=True)}")
    return "\n".join(lines)


def _format_list(project: Path) -> str:
    rows = collect_rows(project)
    if not rows:
        return f"No usage recorded in {project / '.autocode' / LEDGER}"
    lines = [f"{'run':<60} {'status':<24} {'input':>11} {'output':>9} {'reported':>10} {'estimated':>10}"]
    total_in = 0
    total_out = 0
    total_rep = 0.0
    total_est = 0.0
    for row in rows:
        tokens, cost = _dict(row.get("tokens")), _dict(row.get("cost_usd"))
        flag = "" if cost.get("complete") else " *"
        inp = _nonneg_int(tokens.get("input_tokens")) or 0
        out = _nonneg_int(tokens.get("output_tokens")) or 0
        rep = _nonneg_float(cost.get("reported")) or 0.0
        est = _nonneg_float(cost.get("estimated")) or 0.0
        total_in += inp
        total_out += out
        total_rep += rep
        total_est += est
        lines.append(
            f"{str(row.get('run'))[:60]:<60} {str(row.get('status'))[:24]:<24} "
            f"{inp:>11,} {out:>9,} ${rep:>9.2f} ${est:>9.2f}{flag}"
        )
    lines.append(
        f"{f'total ({len(rows)} runs)':<60} {'':<24} "
        f"{total_in:>11,} {total_out:>9,} {f'${total_rep:.2f}':>10} {f'${total_est:.2f}':>10}"
    )
    lines.append(
        "* a stage has no cost or only partial usage is known: the total is a floor. Reported is the provider's "
        "own cost; estimated is tokens at flat comparison rates, not a bill."
    )
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    """The arguments ``autocode usage`` reads; ``autocode completion`` reads them too."""
    result = argparse.ArgumentParser(
        prog="autocode usage",
        description=(
            "Read per-run usage ledgers (`list`), aggregate outcomes, rejections, latency and targets (`report`), "
            "show where each Claude and Codex plan pool's quota went (`quota`), "
            "or how each model did at each job on live, completed runs (`scoreboard`)."
        ),
    )
    result.add_argument(
        "action",
        nargs="?",
        choices=ACTIONS,
        default="list",
        help=(
            "What to show: per-run ledger lines (list, default), cross-run outcome and target tables (report), "
            "burn and waste per Claude and Codex plan pool (quota), "
            "or first-time-right and sent-back counts per job, model and level on live, completed runs (scoreboard)"
        ),
    )
    result.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Project or task worktree whose usage.jsonl ledger to read (default: current directory)",
    )
    result.add_argument(
        "--since",
        metavar="DATE",
        help="Include only runs started or updated on or after YYYY-MM-DD (UTC)",
    )
    result.add_argument(
        "--all",
        action="store_true",
        help="Aggregate usage.jsonl ledgers across every registered project instead of one workspace",
    )
    result.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON instead of formatted tables",
    )
    return result


def _normalize_legacy_path(argv: list[str]) -> list[str]:
    """Support ``python tools/autocode_usage.py PROJECT`` beside ``autocode usage [list|report] --workspace PATH``."""
    out: list[str] = []
    has_workspace = any(arg == "--workspace" or arg.startswith("--workspace=") for arg in argv)
    saw_action = False
    skip_next = False
    for index, arg in enumerate(argv):
        if skip_next:
            out.append(arg)
            skip_next = False
            continue
        if arg in ("--workspace", "--since"):
            out.append(arg)
            skip_next = True
            continue
        if arg.startswith("-"):
            out.append(arg)
            continue
        if not saw_action and arg in ACTIONS:
            out.append(arg)
            saw_action = True
            continue
        if not has_workspace and index == len(argv) - 1 or (not has_workspace and arg not in ACTIONS):
            out.extend(["--workspace", arg])
            has_workspace = True
            continue
        out.append(arg)
    return out


def cli(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    arg_parser = parser()
    args = arg_parser.parse_args(_normalize_legacy_path(raw))
    since_date: dt.date | None = None
    if args.since is not None:
        try:
            since_date = dt.date.fromisoformat(args.since)
        except ValueError:
            arg_parser.error(f"Invalid --since date (expected YYYY-MM-DD): {args.since}")
    if args.action == "quota":
        rows = collect_rows(args.workspace, all_projects=args.all, since=since_date)
        quota = quota_report.build(rows, scope="all projects" if args.all else "workspace", since=args.since)
        print(json.dumps(quota, indent=2) if args.json else quota_report.format_text(quota))
        return 0
    if args.action == "scoreboard":
        rows = collect_rows(args.workspace, all_projects=args.all, since=since_date)
        board = scoreboard.build(rows, scope="all projects" if args.all else "workspace", since=args.since)
        print(json.dumps(board, indent=2) if args.json else scoreboard.format_text(board))
        return 0
    if args.action == "list" and not args.all and args.since is None:
        project = args.workspace
        print(json.dumps(collect_rows(project), indent=2) if args.json else _format_list(project))
        return 0
    payload = build_report(args.workspace, all_projects=args.all, since=since_date)
    print(json.dumps(payload, indent=2) if args.json else format_report(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())

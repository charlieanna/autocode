"""Check a reliability campaign's attempt ledger and report its portfolio (#634).

The declaration (`arena/campaign.json`) fixes the tasks, repetitions, limits and
acceptance rules. The ledger is the published record of what happened: exactly
one row per scheduled attempt, including failures, stops, errors and rows that
were never attempted (`DEFERRED`). This module checks a ledger against its
declaration and reports each kind of result separately: verified delivery,
false completion, appropriate stopping, human decisions and repairs, cost with
failed attempts included, and time. Live and fixture rows are never pooled.

It is a report reader. It never launches AutoCode, an oracle or a model, never
rescores a native outcome and never promotes a version. Evidence references are
evaluator declarations; they are not authenticated here.

    python tools/reliability_ledger.py template arena/campaign.json > ledger.json
    python tools/reliability_ledger.py report arena/campaign.json ledger.json
    python tools/reliability_ledger.py report arena/campaign.json ledger.json --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from statistics import median

SCHEMA_VERSION = 1
DEFERRED = "DEFERRED"
COMPLETE_STATUSES = ("TASK_COMPLETE", "COMPLETE")
INTERVENTION_KINDS = ("decision", "repair")

# Portfolio outcomes in report order. A native PASS lands in one of the first four
# by its status and evidence; every other native outcome maps through NATIVE_CLASS.
CLASSES = {
    "verified_delivery": "Verified delivery",
    "repaired_delivery": "Verified delivery after a human repair",
    "appropriate_stop": "Appropriate stop (the task expected one and its oracle passed)",
    "unverified_pass": "Native PASS without complete evidence, or off the declared configuration",
    "false_completion": "False completion",
    "stopped": "Stopped without claiming completion (appropriateness not established)",
    "failed": "Ended without completing or stopping",
    "error": "Error (no judgement possible)",
    "not_exercised": "Skipped, or never reached the stage under test",
    "ungraded": "Running, interrupted or pending (ungraded)",
    "unattempted": "Not attempted (DEFERRED)",
}
NATIVE_CLASS = {
    "FALSE_COMPLETE": "false_completion",
    "STOPPED": "stopped",
    "HONEST_BLOCKER": "stopped",
    "FAIL": "failed",
    "ERROR": "error",
    "SKIPPED": "not_exercised",
    "NOT_EXERCISED": "not_exercised",
    "RUNNING": "ungraded",
    "INTERRUPTED_UNGRADED": "ungraded",
    "PENDING_UNGRADED": "ungraded",
    DEFERRED: "unattempted",
}
# Rows without a judgement; any of them blocks a comparison (incomplete_blocks_comparison).
NO_JUDGEMENT = ("unverified_pass", "error", "not_exercised", "ungraded", "unattempted")

# The declaration's acceptance rules this report applies. A declaration that
# weakens one is refused rather than reported under rules it does not state.
RULES = {
    "record_every_scheduled_attempt": True,
    "publish_nonpass_and_spent_budget": True,
    "unattempted_outcome": DEFERRED,
    "missing_usage_and_cost": None,
    "preserve_native_outcome": True,
    "arena_stopped_proves_appropriate_stop": False,
    "fixtures_qualify_live_version": False,
    "incomplete_blocks_comparison": True,
    "automatic_promotion": False,
    "unknown_usage_or_cost_is_not_zero": True,
    "statistical_reliability_claim": False,
}
# The attempt fields this report reads: the declaration must require exactly these.
FIELDS = (
    "schedule_id",
    "execution_kind",
    "source_revision",
    "runtime_identity",
    "actual_models_and_efforts",
    "native_status",
    "native_outcome",
    "oracle_checks",
    "evidence_refs",
    "interventions_decision_or_repair",
    "elapsed_seconds",
    "usage",
    "estimated_usd",
    "spent_budget",
    "reason",
)
# Runtime identity checked for every attempt; the provider only for live attempts.
RUNTIME_KEYS = ("autocode_version", "python", "psutil")
LIVE_RUNTIME_KEYS = ("provider", "provider_version")


class LedgerError(ValueError):
    """The ledger or its declaration cannot be reported as a campaign record."""


def _json(data: bytes, label: str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise LedgerError(f"{label}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    def constant(value):
        raise LedgerError(f"{label}: {value} is not a number")

    try:
        value = json.loads(data, object_pairs_hook=unique, parse_constant=constant)
    except LedgerError:
        raise
    except (ValueError, UnicodeError) as error:
        raise LedgerError(f"{label}: {error}") from error
    if not isinstance(value, dict):
        raise LedgerError(f"{label} must be a JSON object")
    return value


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(f"{label} must be a nonempty string")
    return value


def _revision(value, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise LedgerError(f"{label} must be a full 40-character commit")
    return value


def _amount(value, label: str):
    """A finite nonnegative number, or null for unknown. Null is never read as zero."""
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
        raise LedgerError(f"{label} must be a finite nonnegative number or null when unknown")
    return value


def _object(value, label: str):
    if value is not None and not isinstance(value, dict):
        raise LedgerError(f"{label} must be an object or null")
    return value


def _check_campaign(campaign: dict) -> None:
    _text(campaign.get("id"), "campaign id")
    _revision(campaign.get("source_revision"), "campaign source_revision")
    for key in ("runtime", "profile", "acceptance"):
        if not isinstance(campaign.get(key), dict):
            raise LedgerError(f"campaign {key} must be an object")
    definition = campaign["profile"].get("definition")
    if (
        not isinstance(definition, dict)
        or not isinstance(definition.get("provider"), str)
        or not all(isinstance(definition.get(key), dict) for key in ("models", "effort"))
    ):
        raise LedgerError("campaign profile must declare its provider, models and effort")
    if any(key not in campaign["runtime"] for key in RUNTIME_KEYS + LIVE_RUNTIME_KEYS):
        raise LedgerError("campaign runtime must pin every runtime version")
    acceptance = campaign["acceptance"]
    weakened = [key for key, value in RULES.items() if key not in acceptance or acceptance[key] != value]
    if weakened:
        raise LedgerError(f"campaign acceptance rules differ from the rules this report applies: {weakened}")
    fields = acceptance.get("required_attempt_fields")
    if not isinstance(fields, list) or len(set(fields)) != len(fields) or set(fields) != set(FIELDS):
        raise LedgerError(f"campaign must require each attempt field this report reads once: {list(FIELDS)}")
    tasks = campaign.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise LedgerError("campaign must declare its tasks")
    outcomes = acceptance.get("native_outcomes")
    declared = {}
    for task in tasks:
        name = _text(task.get("id") if isinstance(task, dict) else None, "task id")
        if name in declared:
            raise LedgerError(f"campaign declares task {name} twice")
        if not isinstance(outcomes, dict) or not isinstance(outcomes.get(task.get("kind")), list):
            raise LedgerError(f"campaign names no native outcomes for task {name}")
        if task.get("execution_kind") not in ("live", "fixture"):
            raise LedgerError(f"task {name} must be live or fixture")
        declared[name] = task
    schedule = campaign.get("schedule")
    if not isinstance(schedule, list) or not schedule:
        raise LedgerError("campaign must declare its schedule")
    seen = set()
    for row in schedule:
        ident = _text(row.get("id") if isinstance(row, dict) else None, "schedule id")
        if ident in seen or row.get("task") not in declared:
            raise LedgerError(f"schedule row {ident} is duplicated or names an undeclared task")
        seen.add(ident)


def load_campaign(path) -> tuple[dict, str]:
    """The declaration and the SHA-256 of its exact bytes, which a ledger must name."""
    data = Path(path).read_bytes()
    campaign = _json(data, "campaign")
    _check_campaign(campaign)
    return campaign, hashlib.sha256(data).hexdigest()


def template(campaign: dict, campaign_sha256: str) -> dict:
    """A ledger that accounts for every scheduled attempt as not yet attempted."""
    tasks = {task["id"]: task for task in campaign["tasks"]}
    attempts = []
    for scheduled in campaign["schedule"]:
        values: dict[str, object] = {
            "schedule_id": scheduled["id"],
            "execution_kind": tasks[scheduled["task"]]["execution_kind"],
            "source_revision": campaign["source_revision"],
            "runtime_identity": None,
            "actual_models_and_efforts": None,
            "native_status": None,
            "native_outcome": DEFERRED,
            "oracle_checks": None,
            "evidence_refs": [],
            "interventions_decision_or_repair": [],
            "elapsed_seconds": None,
            "usage": None,
            "estimated_usd": None,
            "spent_budget": None,
            "reason": "not attempted yet",
        }
        attempts.append({field: values[field] for field in campaign["acceptance"]["required_attempt_fields"]})
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign": campaign["id"],
        "campaign_sha256": campaign_sha256,
        "attempts": attempts,
    }


def _checks(value, label: str):
    if value is None:
        return None
    if not isinstance(value, list):
        raise LedgerError(f"{label} must be a list of {{name, ok}} results or null when ungraded")
    names = set()
    for check in value:
        if not isinstance(check, dict) or set(check) != {"name", "ok"} or type(check["ok"]) is not bool:
            raise LedgerError(f"{label}: each check is {{name, ok}} with a boolean ok")
        name = _text(check["name"], f"{label} name")
        if name in names:
            raise LedgerError(f"{label}: check {name} is listed twice")
        names.add(name)
    return value


def _interventions(value, label: str) -> list:
    if not isinstance(value, list):
        raise LedgerError(f"{label} must be a list, empty when nobody intervened")
    for item in value:
        if not isinstance(item, dict) or set(item) != {"kind", "note"} or item["kind"] not in INTERVENTION_KINDS:
            raise LedgerError(f"{label}: each intervention is {{kind, note}} with kind decision or repair")
        _text(item["note"], f"{label} note")
    return value


def _deviations(row: dict, task: dict, campaign: dict) -> list[str]:
    """How an attempt differs from the declared revision, runtime and live profile."""
    found = []
    if row["source_revision"] != campaign["source_revision"]:
        found.append(f"ran on {row['source_revision'][:12]}, not the declared {campaign['source_revision'][:12]}")
    live = task["execution_kind"] == "live"
    runtime = row["runtime_identity"]
    if runtime is None:
        found.append("runtime identity not recorded")
    else:
        for key in RUNTIME_KEYS + (LIVE_RUNTIME_KEYS if live else ()):
            if runtime.get(key) != campaign["runtime"][key]:
                found.append(f"runtime {key} {runtime.get(key)!r} is not the declared {campaign['runtime'][key]!r}")
        if not runtime.get("dependencies"):
            found.append("frozen dependency record not recorded")
    actual = row["actual_models_and_efforts"]
    if actual is None:
        found.append("actual models and efforts not recorded")
    elif live:
        declared = campaign["profile"]["definition"]
        if actual.get("provider") != declared["provider"]:
            found.append(f"provider {actual.get('provider')!r} is not the declared {declared['provider']!r}")
        for key in ("models", "effort"):
            used = actual.get(key) if isinstance(actual.get(key), dict) else {}
            roles = sorted(role for role in {*used, *declared[key]} if used.get(role) != declared[key].get(role))
            if roles:
                found.append(f"{key} differ from the declared profile for {', '.join(roles)}")
    return found


def _row(row, task: dict, campaign: dict) -> dict:
    sid = row["schedule_id"]
    if row["execution_kind"] != task["execution_kind"]:
        raise LedgerError(
            f"{sid}: execution_kind {row['execution_kind']!r} is not the declared {task['execution_kind']!r}"
        )
    _revision(row["source_revision"], f"{sid}: source_revision")
    outcome = row["native_outcome"]
    allowed = [*campaign["acceptance"]["native_outcomes"][task["kind"]], DEFERRED]
    if outcome not in allowed:
        raise LedgerError(f"{sid}: native_outcome {outcome!r} is not a {task['kind']} outcome {allowed}")
    status = row["native_status"]
    if status is not None:
        _text(status, f"{sid}: native_status")
    checks = _checks(row["oracle_checks"], f"{sid}: oracle_checks")
    refs = row["evidence_refs"]
    if not isinstance(refs, list):
        raise LedgerError(f"{sid}: evidence_refs must be a list")
    for ref in refs:
        _text(ref, f"{sid}: evidence reference")
    interventions = _interventions(row["interventions_decision_or_repair"], f"{sid}: interventions")
    for key in ("runtime_identity", "actual_models_and_efforts", "usage"):
        _object(row[key], f"{sid}: {key}")
    for key in ("elapsed_seconds", "estimated_usd"):
        _amount(row[key], f"{sid}: {key}")
    budget = _object(row["spent_budget"], f"{sid}: spent_budget")
    for name, spent in (budget or {}).items():
        _amount(spent, f"{sid}: spent_budget {name}")
    if not isinstance(row["reason"], str):
        raise LedgerError(f"{sid}: reason must be a string")
    if outcome != "PASS" and not row["reason"].strip():
        raise LedgerError(f"{sid}: a {outcome} row must say why")

    gaps, deviations = [], []
    if outcome == DEFERRED:
        if status is not None or checks is not None:
            raise LedgerError(f"{sid}: a DEFERRED row was never attempted and has no status or oracle result")
        kind = NATIVE_CLASS[outcome]
    else:
        deviations = _deviations(row, task, campaign)
        kind = NATIVE_CLASS.get(outcome, "unverified_pass")  # a PASS is placed below
    if outcome in ("PASS", "FALSE_COMPLETE", "STOPPED", "HONEST_BLOCKER", "FAIL") and status is None:
        raise LedgerError(f"{sid}: a {outcome} row must preserve the native status it was judged from")
    if outcome == "PASS":
        expected = task.get("expected", "complete")
        complete = status in COMPLETE_STATUSES
        if (complete and expected == "stop") or (not complete and expected == "complete"):
            raise LedgerError(f"{sid}: native PASS at {status} contradicts the task's expected {expected} ending")
        if checks and not all(check["ok"] for check in checks):
            raise LedgerError(f"{sid}: a native PASS with a failing oracle check contradicts its outcome")
        if not checks:
            gaps.append("no oracle checks recorded")
        required = (task.get("upstream") or {}).get("checks") or []
        missing = [name for name in required if name not in {check["name"] for check in checks or []}]
        if missing:
            gaps.append(f"declared oracle checks missing: {', '.join(missing)}")
        if not refs:
            gaps.append("no evidence references")
        if gaps or deviations:
            kind = "unverified_pass"
        elif not complete:
            kind = "appropriate_stop"
        elif any(item["kind"] == "repair" for item in interventions):
            kind = "repaired_delivery"
        else:
            kind = "verified_delivery"
    return {
        "schedule_id": sid,
        "task": task["id"],
        "execution_kind": task["execution_kind"],
        "native_status": status,
        "native_outcome": outcome,
        "outcome": kind,
        "oracle": None if checks is None else {"passed": sum(c["ok"] for c in checks), "total": len(checks)},
        "evidence_gaps": gaps,
        "deviations": deviations,
        "decisions": sum(item["kind"] == "decision" for item in interventions),
        "repairs": sum(item["kind"] == "repair" for item in interventions),
        "elapsed_seconds": row["elapsed_seconds"],
        "estimated_usd": row["estimated_usd"],
        "spent_budget": budget,
        "reason": row["reason"],
    }


def _summary(rows: list[dict]) -> dict:
    counts = {key: sum(row["outcome"] == key for row in rows) for key in CLASSES}
    attempted = [row for row in rows if row["outcome"] != "unattempted"]
    priced = [row["estimated_usd"] for row in rows if row["estimated_usd"] is not None]
    unpriced = sum(row["estimated_usd"] is None for row in attempted)
    known_usd = round(sum(priced), 6)
    total_usd = known_usd if not unpriced else None
    timed = [row["elapsed_seconds"] for row in attempted if row["elapsed_seconds"] is not None]
    verified = counts["verified_delivery"]
    return {
        "scheduled": len(rows),
        "attempted": len(attempted),
        "outcomes": counts,
        "decisions": sum(row["decisions"] for row in rows),
        "repairs": sum(row["repairs"] for row in rows),
        "attempts_with_repair": sum(row["repairs"] > 0 for row in rows),
        "known_usd": known_usd,
        "unpriced_attempts": unpriced,
        "total_usd": total_usd,
        "usd_per_verified_delivery": round(total_usd / verified, 6) if total_usd is not None and verified else None,
        "known_elapsed_seconds": round(sum(timed), 6),
        "median_elapsed_seconds": median(timed) if timed else None,
        "untimed_attempts": len(attempted) - len(timed),
    }


def report(campaign: dict, campaign_sha256: str, ledger: dict) -> dict:
    """Check every row against the declaration and summarize live and fixture rows apart."""
    if set(ledger) != {"schema_version", "campaign", "campaign_sha256", "attempts"}:
        raise LedgerError("ledger must hold exactly schema_version, campaign, campaign_sha256 and attempts")
    if type(ledger["schema_version"]) is not int or ledger["schema_version"] != SCHEMA_VERSION:
        raise LedgerError(f"ledger schema_version must be {SCHEMA_VERSION}")
    if ledger["campaign"] != campaign["id"] or ledger["campaign_sha256"] != campaign_sha256:
        raise LedgerError("ledger names another declaration: freeze the declaration's SHA-256 before admission")
    attempts = ledger["attempts"]
    if not isinstance(attempts, list):
        raise LedgerError("ledger attempts must be a list")
    fields = campaign["acceptance"]["required_attempt_fields"]
    scheduled = {row["id"]: row for row in campaign["schedule"]}
    tasks = {task["id"]: task for task in campaign["tasks"]}
    by_id = {}
    for row in attempts:
        if not isinstance(row, dict):
            raise LedgerError("each ledger row must be an object")
        sid = row.get("schedule_id")
        missing = [field for field in fields if field not in row]
        extra = sorted(set(row) - set(fields))
        if missing or extra:
            raise LedgerError(f"{sid}: missing attempt fields {missing}, undeclared fields {extra}")
        if not isinstance(sid, str) or sid not in scheduled:
            raise LedgerError(f"{sid!r} is not a scheduled attempt; a new attempt needs a new declared schedule")
        if sid in by_id:
            raise LedgerError(f"{sid} appears twice; keep the original attempt, never replace it with a retry")
        by_id[sid] = row
    absent = [sid for sid in scheduled if sid not in by_id]
    if absent:
        raise LedgerError(f"scheduled attempts missing from the ledger (record them as {DEFERRED}): {absent}")
    rows = [_row(by_id[sid], tasks[entry["task"]], campaign) for sid, entry in scheduled.items()]
    reasons = []
    pending = [row for row in rows if row["outcome"] in NO_JUDGEMENT]
    if pending:
        reasons.append(f"{len(pending)} of {len(rows)} scheduled attempts have no judgement")
    deviating = [row for row in rows if row["deviations"]]
    if deviating:
        reasons.append(f"{len(deviating)} attempts differ from the declared revision, runtime or profile")
    sections = {}
    for kind in ("live", "fixture"):
        subset = [row for row in rows if row["execution_kind"] == kind]
        if subset:
            sections[kind] = _summary(subset)
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign": campaign["id"],
        "campaign_sha256": campaign_sha256,
        "declaration_status": campaign.get("status"),
        "source_revision": campaign["source_revision"],
        "scheduled": len(rows),
        "comparison": {"blocked": bool(reasons), "reasons": reasons},
        "sections": sections,
        "rows": rows,
    }


def _money(value) -> str:
    return "unknown" if value is None else f"${value:.6f}"


def _number(value) -> str:
    return "unknown" if value is None else f"{value:g}"


def _seconds(value) -> str:
    return "unknown" if value is None else f"{value:g} s"


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render(result: dict) -> str:
    """Markdown: every scheduled attempt once, each kind of result in its own row."""
    lines = [
        f"# Reliability portfolio: {result['campaign']}",
        "",
        f"Declaration status {result['declaration_status']}, SHA-256 `{result['campaign_sha256']}`, "
        f"source revision `{result['source_revision']}`. {result['scheduled']} scheduled attempts, "
        "each listed once below.",
        "",
        "Counts only: a finite campaign makes no statistical reliability claim and no result promotes a "
        "version. Unknown cost or time is shown as unknown, never as zero. Evidence references are the "
        "evaluator's declarations.",
        "",
    ]
    comparison = result["comparison"]
    if comparison["blocked"]:
        lines.append(f"**Comparison blocked:** {'; '.join(comparison['reasons'])}.")
    else:
        lines.append("**Comparison not blocked:** every attempt has a judgement on the declared configuration.")
    titles = {
        "live": ("Live attempts", ""),
        "fixture": (
            "Fixture attempts",
            "Fixture evidence comes from a scripted provider or fault. It never qualifies a live version.",
        ),
    }
    for kind, summary in result["sections"].items():
        title, note = titles[kind]
        lines += ["", f"## {title}", ""]
        if note:
            lines += [note, ""]
        lines += ["| Outcome | Attempts |", "| --- | ---: |"]
        lines += [f"| {label} | {summary['outcomes'][key]} |" for key, label in CLASSES.items()]
        lines += [
            f"| **Scheduled** | **{summary['scheduled']}** |",
            "",
            f"Human intervention: {summary['decisions']} decisions, {summary['repairs']} repairs "
            f"({summary['attempts_with_repair']} attempts repaired).",
            f"Cost, failed attempts included: total {_money(summary['total_usd'])}; known "
            f"{_money(summary['known_usd'])}; {summary['unpriced_attempts']} attempts unpriced. "
            "Per verified delivery without repair: "
            + (
                _money(summary["usd_per_verified_delivery"])
                if summary["outcomes"]["verified_delivery"]
                else "none (no verified delivery)"
            )
            + ".",
            f"Time: {_number(summary['known_elapsed_seconds'])} s known; median "
            f"{_seconds(summary['median_elapsed_seconds'])}; {summary['untimed_attempts']} attempts untimed.",
            "",
            "| Schedule ID | Native status | Native outcome | Portfolio outcome | Oracle | Decisions | Repairs "
            "| Elapsed (s) | Estimated USD | Spent budget | Notes |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
        for row in result["rows"]:
            if row["execution_kind"] != kind:
                continue
            oracle = "none" if row["oracle"] is None else f"{row['oracle']['passed']}/{row['oracle']['total']}"
            # An attempt that never ran has nothing to price or time; any other null is unknown.
            unknown = "not run" if row["outcome"] == "unattempted" else "unknown"
            budget = (
                unknown
                if row["spent_budget"] is None
                else ", ".join(f"{name}={_number(value)}" for name, value in row["spent_budget"].items()) or "none"
            )
            elapsed = unknown if row["elapsed_seconds"] is None else _number(row["elapsed_seconds"])
            spent = unknown if row["estimated_usd"] is None else _money(row["estimated_usd"])
            notes = "; ".join(item for item in (row["reason"], *row["evidence_gaps"], *row["deviations"]) if item)
            lines.append(
                f"| {row['schedule_id']} | {_cell(row['native_status'] or '')} | {row['native_outcome']} "
                f"| {row['outcome']} | {oracle} | {row['decisions']} | {row['repairs']} "
                f"| {elapsed} | {spent} | {_cell(budget)} "
                f"| {_cell(notes)} |"
            )
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check a reliability campaign ledger and report its portfolio.")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("template", help="print a ledger that records every scheduled attempt as DEFERRED")
    start.add_argument("campaign", type=Path)
    check = commands.add_parser("report", help="check a ledger against its declaration and print the portfolio")
    check.add_argument("campaign", type=Path)
    check.add_argument("ledger", type=Path)
    check.add_argument("--json", action="store_true", help="print the checked report as JSON")
    args = parser.parse_args(argv)
    try:
        campaign, digest = load_campaign(args.campaign)
        if args.command == "template":
            text = json.dumps(template(campaign, digest), indent=2) + "\n"
        else:
            result = report(campaign, digest, _json(args.ledger.read_bytes(), "ledger"))
            text = json.dumps(result, indent=2, allow_nan=False) + "\n" if args.json else render(result)
    except (OSError, LedgerError) as error:
        parser.exit(2, f"ledger error: {error}\n")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

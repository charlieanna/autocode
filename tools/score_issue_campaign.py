#!/usr/bin/env python3
"""Aggregate a declared case set and pinned live-trial reports, without execution.

This is a report reader, not an oracle or permission to run models. Manifest
metadata is supplied by the evaluator. Artifact hashes detect changes; they do
not authenticate evidence against a writer who controls the manifest too.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

try:
    from .score_autocode_run import known_sum, money
except ImportError:
    from score_autocode_run import known_sum, money


class CampaignError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise CampaignError(f"{label} must be a nonempty string")
    return value


def sha256(value, label):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise CampaignError(f"{label} must be a lowercase SHA-256 digest")
    return value


def number(value, label):
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
        raise CampaignError(f"{label} must be a finite nonnegative number or null")
    return value


def document(data: bytes, label):
    def invalid_constant(value):
        raise CampaignError(f"{label}: invalid {value}")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CampaignError(f"{label}: duplicate JSON key {key}")
            result[key] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, UnicodeError) as error:
        raise CampaignError(f"{label}: {error}") from error
    if not isinstance(value, dict):
        raise CampaignError(f"{label} must be an object")
    return value


def validate_manifest(manifest):
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise CampaignError("manifest schema_version must be 1")
    for key in ("name", "profile"):
        nonempty(manifest.get(key), key)
    for key, allowed in (("execution_kind", ("fixture", "live")),
                         ("workload_kind", ("synthetic", "repository")),
                         ("split", ("development", "held_out"))):
        if manifest.get(key) not in allowed:
            raise CampaignError(f"{key} must be one of {allowed}")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise CampaignError("cases must be a nonempty array")
    ids, identities = set(), set()
    for case in cases:
        if not isinstance(case, dict):
            raise CampaignError("each case must be an object")
        ident = nonempty(case.get("id"), "case.id")
        nonempty(case.get("scenario"), f"{ident}.scenario")
        for key in ("baseline_content_sha256", "task_sha256", "oracle_sha256"):
            sha256(case.get(key), f"{ident}.{key}")
        identity = (case["baseline_content_sha256"], case["task_sha256"], case["oracle_sha256"])
        if ident in ids or identity in identities:
            raise CampaignError(f"duplicate case identity: {ident}")
        ids.add(ident)
        identities.add(identity)
        checks = case.get("required_checks")
        if (not isinstance(checks, list) or not checks
                or any(not isinstance(c, str) or not c.strip() for c in checks)
                or len(set(checks)) != len(checks)):
            raise CampaignError(f"{ident}.required_checks must contain unique nonempty names")
        if not isinstance(case.get("attempts"), list):
            raise CampaignError(f"{ident}.attempts must be an array (empty for unattempted cases)")
        for attempt in case["attempts"]:
            if not isinstance(attempt, dict):
                raise CampaignError(f"{ident}: each attempt must be an object")
            nonempty(attempt.get("report"), f"{ident}.report")
            sha256(attempt.get("sha256"), f"{ident}.report sha256")
            if attempt.get("assistance", "unknown") not in ("assisted", "unassisted", "unknown"):
                raise CampaignError(f"{ident}: invalid assistance declaration")
            number(attempt.get("human_seconds"), f"{ident}.human_seconds")


def evaluate_report(report, case, manifest):
    """Qualification uses the harness oracle and frozen contract, never scores."""
    errors = []
    measurement = report.get("measurement")
    if not isinstance(measurement, dict):
        measurement = {}
    if (type(report.get("report_version")) is not int or report["report_version"] != 2
            or type(measurement.get("schema_version")) is not int or measurement["schema_version"] != 1):
        errors.append("missing supported measurement report version")
    for key in ("scenario", "oracle_sha256"):
        if report.get(key) != case[key]:
            errors.append(f"{key} differs from the declared case")
    for key in ("baseline_content_sha256", "task_sha256"):
        if measurement.get(key) != case[key]:
            errors.append(f"{key} differs from the declared case")
    if report.get("profile") != manifest["profile"]:
        errors.append("profile differs from the campaign")
    for key in ("execution_kind", "workload_kind"):
        if measurement.get(key) != manifest[key]:
            errors.append(f"{key} differs from the campaign")
    detail = report.get("profile_detail")
    provider = detail.get("provider") if isinstance(detail, dict) else None
    if not provider or (provider == "fixture") != (manifest["execution_kind"] == "fixture"):
        errors.append("provider does not match execution kind")
    outcome = report.get("verdict")
    if outcome not in ("PASS", "FAIL", "FALSE_COMPLETE", "HONEST_BLOCKER", "ERROR", "DEFERRED"):
        errors.append("missing or unsupported verdict")
    if outcome == "PASS":
        checks = report.get("checks")
        if not isinstance(checks, list) or not checks or any(not isinstance(c, dict) for c in checks):
            errors.append("missing oracle checks")
        else:
            names = [c.get("name") for c in checks]
            if (any(not isinstance(n, str) or not n for n in names)
                    or len(set(names)) != len(names) or not set(case["required_checks"]).issubset(names)):
                errors.append("required oracle checks missing or duplicated")
            if any(c.get("ok") is not True for c in checks):
                outcome = "FALSE_COMPLETE"
        try:
            sha256(report.get("candidate_revision"), "candidate_revision")
            sha256(report.get("run_identity"), "run_identity")
        except CampaignError as error:
            errors.append(str(error))
        if report.get("runner_status") not in ("TASK_COMPLETE", "COMPLETE") or report.get("blocker") or report.get("error"):
            errors.append("pass lacks an unblocked runner completion")
    if errors:
        outcome = "UNVERIFIED"
    elif outcome == "PASS":
        outcome = "VERIFIED_PASS"
    return outcome, errors, measurement


def score_campaign(path: Path) -> dict:
    path = path.resolve()
    manifest_bytes = path.read_bytes()
    manifest = document(manifest_bytes, "manifest")
    validate_manifest(manifest)
    rows, cases = [], []
    seen_paths, seen_hashes, seen_ids, seen_runs = set(), set(), set(), set()
    bases = set()
    for case in manifest["cases"]:
        case_rows = []
        for declaration in case["attempts"]:
            report_path = (path.parent / declaration["report"]).resolve()
            pinned = declaration["sha256"]
            if report_path in seen_paths or pinned in seen_hashes:
                raise CampaignError("duplicate report path or content; a repeated snapshot is not another attempt")
            seen_paths.add(report_path)
            seen_hashes.add(pinned)
            row = {"case": case["id"], "report": str(report_path), "sha256": pinned,
                   "outcome": "UNVERIFIED", "errors": [], "runner_status": None,
                   "assistance": declaration.get("assistance", "unknown"),
                   "human_seconds": declaration.get("human_seconds"), "elapsed_seconds": None,
                   "estimated_usd": None, "known_estimated_usd": None}
            try:
                raw = report_path.read_bytes()
                if digest(raw) != pinned:
                    raise CampaignError("report changed: SHA-256 mismatch")
                report = document(raw, str(report_path))
                # Reject duplicate attempts even when the same report was copied or updated.
                attempt_id = nonempty(report.get("attempt_id"), "attempt_id")
                run_id = report.get("run_identity")
                if run_id is not None:
                    nonempty(run_id, "run_identity")
                if attempt_id in seen_ids or (run_id and run_id in seen_runs):
                    raise CampaignError("DUPLICATE_ATTEMPT")
                seen_ids.add(attempt_id)
                if run_id:
                    seen_runs.add(nonempty(run_id, "run_identity"))
                outcome, errors, measurement = evaluate_report(report, case, manifest)
                status = report.get("runner_status")
                if status is not None:
                    nonempty(status, "runner_status")
                row.update(outcome=outcome, errors=errors, runner_status=status)
                row["elapsed_seconds"] = number(measurement.get("elapsed_seconds"), "elapsed_seconds")
                usage = measurement.get("usage") or {}
                if not isinstance(usage, dict):
                    raise CampaignError("usage must be an object")
                total = number(usage.get("estimated_api_equivalent_usd"), "estimated cost")
                known = number(usage.get("known_estimated_api_equivalent_usd"), "known subtotal")
                basis = usage.get("pricing_basis")
                if total is not None and (known is None or not math.isclose(total, known)):
                    raise CampaignError("complete cost must equal the known subtotal")
                if total is not None or known is not None:
                    if not isinstance(basis, dict) or not basis:
                        raise CampaignError("cost has no pricing basis")
                    bases.add(json.dumps(basis, sort_keys=True))
                row.update(estimated_usd=total, known_estimated_usd=known)
            except (OSError, CampaignError) as error:
                if str(error) == "DUPLICATE_ATTEMPT":
                    raise CampaignError("duplicate attempt/run identity; retain only its final cumulative report") from error
                row.update(outcome="UNVERIFIED", estimated_usd=None, known_estimated_usd=None)
                row["errors"].append(str(error))
            case_rows.append(row)
            rows.append(row)
        passes = [r for r in case_rows if r["outcome"] == "VERIFIED_PASS"]
        cases.append({"id": case["id"], "attempts": len(case_rows), "verified": bool(passes),
                      "unassisted_verified": any(r["assistance"] == "unassisted" for r in passes)})
    verified = sum(c["verified"] for c in cases)
    comparable = len(bases) == 1
    total = known_sum(r["estimated_usd"] for r in rows) if comparable else None
    known = sum(r["known_estimated_usd"] for r in rows if r["known_estimated_usd"] is not None) if comparable else None
    outcomes = dict(Counter(r["outcome"] for r in rows))
    return {"schema_version": 1, "name": manifest["name"], "manifest_sha256": digest(manifest_bytes),
            **{k: manifest[k] for k in ("profile", "execution_kind", "workload_kind", "split")},
            "summary": {"cases": len(cases), "attempts": len(rows), "verified_cases": verified,
                        "unattempted_cases": sum(c["attempts"] == 0 for c in cases),
                        "unassisted_verified_cases": sum(c["unassisted_verified"] for c in cases),
                        "verified_case_rate": verified / len(cases), "attempt_outcomes": outcomes,
                        "estimated_api_equivalent_usd": total, "known_estimated_api_equivalent_usd": known,
                        "cost_per_verified_case_usd": total / verified if total is not None and verified else None,
                        "unpriced_attempts": sum(r["estimated_usd"] is None for r in rows),
                        "pricing_comparable": comparable,
                        "human_seconds": known_sum(r["human_seconds"] for r in rows),
                        "known_human_seconds": sum(r["human_seconds"] for r in rows if r["human_seconds"] is not None),
                        "unknown_human_time_attempts": sum(r["human_seconds"] is None for r in rows),
                        "elapsed_seconds": known_sum(r["elapsed_seconds"] for r in rows),
                        "failure_states": dict(Counter(r["runner_status"] or "unavailable" for r in rows
                                                       if r["outcome"] != "VERIFIED_PASS"))},
            "pricing_basis": json.loads(next(iter(bases))) if comparable else None,
            "cases": cases, "attempts": rows}


def render(report):
    s = report["summary"]
    lines = [f"# {report['name']}", "",
             f"{report['execution_kind']} / {report['workload_kind']} / {report['split']} / {report['profile']}", "",
             f"Verified cases: **{s['verified_cases']}/{s['cases']}** ({s['verified_case_rate']:.1%}); "
             f"{s['attempts']} attempts; {s['unattempted_cases']} unattempted cases.",
             f"Declared unassisted verified cases: {s['unassisted_verified_cases']}.", "",
             f"Historical-rate total: {money(s['estimated_api_equivalent_usd'])}; "
             f"known subtotal: {money(s['known_estimated_api_equivalent_usd'])}; "
             f"{s['unpriced_attempts']} unpriced attempts.",
             f"Cost per verified case (all attempts included): {money(s['cost_per_verified_case_usd'])}.",
             f"Common pricing basis available: {s['pricing_comparable']}.",
             f"Human time: {s['human_seconds'] if s['human_seconds'] is not None else 'unknown'} seconds; "
             f"known subtotal {s['known_human_seconds']} seconds.", "",
             "| Case | Attempts | Oracle verified | Declared unassisted pass |",
             "| --- | ---: | --- | --- |"]
    for case in report["cases"]:
        lines.append(f"| {case['id']} | {case['attempts']} | {case['verified']} | {case['unassisted_verified']} |")
    lines += ["", "Attempt outcomes: " + json.dumps(s["attempt_outcomes"], sort_keys=True), ""]
    for attempt in report["attempts"]:
        if attempt["errors"]:
            lines.append(f"- {attempt['case']}: " + "; ".join(attempt["errors"]))
    lines += ["", "Historical estimates are not invoices. Assistance and human time are evaluator declarations. "
              "Fixture and synthetic results do not establish real-repository solving ability. "
              "Oracle verification does not by itself establish maintainer acceptance or patch quality.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    try:
        report = score_campaign(args.manifest)
        inputs = {args.manifest.resolve(), *(Path(r["report"]).resolve() for r in report["attempts"])}
        outputs = [p.resolve() for p in (args.out, args.json_out) if p]
        if len(outputs) != len(set(outputs)) or inputs.intersection(outputs):
            raise CampaignError("outputs must be distinct and must not overwrite campaign inputs")
        text = render(report)
        for target, value in ((args.out, text), (args.json_out, json.dumps(report, indent=2, allow_nan=False) + "\n")):
            if target:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(value)
        print(text)
    except (OSError, CampaignError) as error:
        parser.exit(2, f"campaign error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

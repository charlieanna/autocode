"""Read-only pre-submit report validation CLI (`autocode report-check`, #3344)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import (
        autocode_brief_obligations as brief_obligations,
    )
    from . import (
        autocode_check_refs as check_refs,
    )
    from . import (
        autocode_planning_graph as planning_graph,
    )
    from . import (
        autocode_requirement_coverage as requirement_coverage,
    )
    from . import (
        autocode_requirement_cues as requirement_cues,
    )
    from . import (
        autocode_requirement_quotes as requirement_quotes,
    )
    from . import (
        autocode_source_trace as source_trace,
    )
    from . import (
        autocode_util as util,
    )
except ImportError:
    import autocode_brief_obligations as brief_obligations
    import autocode_check_refs as check_refs
    import autocode_planning_graph as planning_graph
    import autocode_requirement_coverage as requirement_coverage
    import autocode_requirement_cues as requirement_cues
    import autocode_requirement_quotes as requirement_quotes
    import autocode_source_trace as source_trace
    import autocode_util as util

SPEC_VERSION = 1
_PLANNING_STAGES = ("draft_plan", "revise_plan", "finalize_plan")
_VALIDATOR_STAGES = (
    "tester",
    "review_completion",
    "review_checkpoint",
    "judge_acceptance",
    "verify_build",
)
_CHECK_REF_RE = re.compile(r"\bcheck:(\d+)\b")
_EVIDENCE_JSON_RE = re.compile(r"(?<![A-Za-z0-9_./-])(?:\./)?(\.autocode/evidence/[A-Za-z0-9_./-]+\.json)\b")


def build_spec(
    *,
    stage: str,
    workspace: Path,
    schema: dict[str, Any],
    state: RunState | None = None,
) -> dict[str, Any]:
    """Build a public, self-contained report-check specification from schema and public cues."""
    spec: dict[str, Any] = {
        "version": SPEC_VERSION,
        "stage": stage,
        "workspace": str(workspace),
        "schema": schema,
    }
    if state is None:
        return spec
    if stage == "requirements_gather":
        spec["source_texts"] = requirement_cues.source_texts(state)
        spec["coverage_checklist"] = requirement_cues.coverage_checklist(state)
    elif stage in _PLANNING_STAGES:
        spec["sources"] = brief_obligations.sources(state)
        if stage == "revise_plan":
            planning_state = state.get("planning") or {}
            review_report = ((planning_state.get("reports") or {}).get("review_plan") or {}).get("report") or {}
            concerns = review_report.get("concerns") or []
            spec["concern_ids"] = [
                str(row.get("id") or "")
                for row in concerns
                if isinstance(row, dict) and str(row.get("id") or "").strip()
            ]
    return spec


def _check_requirements_gather(data: dict[str, Any], spec: dict[str, Any], errors: list[str]) -> None:
    sources = [str(text) for text in (spec.get("source_texts") or []) if str(text)]
    checklist = [str(item) for item in (spec.get("coverage_checklist") or [])]
    rows = data.get("requirements") or []
    if not isinstance(rows, list):
        return
    verified_quotes: list[str] = []
    bad_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_quote = str(row.get("source_quote") or "")
        resolved = requirement_quotes.resolve_checklist_id(raw_quote, checklist) if checklist else raw_quote
        if sources:
            grounded = requirement_quotes.ground(resolved, sources)
            if grounded is None:
                bad_rows.append(row)
            else:
                verified_quotes.append(grounded)
        else:
            verified_quotes.append(resolved)
    if bad_rows and sources:
        reason = requirement_quotes.unsupported(bad_rows, sources, inherited=False)
        if reason:
            errors.append(reason)
    if checklist and not bad_rows:
        ignored = [str(item) for item in (data.get("ignored_statements") or [])]
        missing = requirement_coverage.missing_sentences(checklist, verified_quotes, ignored)
        if missing:
            preview = "; ".join(repr(str(item)[:80]) for item in missing[:5])
            errors.append(
                f"Coverage checklist still has {len(missing)} unaddressed item(s): {preview}. "
                "Quote each in requirements or list it in ignored_statements."
            )


def _check_revise_responses(data: dict[str, Any], concern_ids: list[str], errors: list[str]) -> None:
    rows = data.get("responses") or []
    if not isinstance(rows, list):
        return
    expected = set(concern_ids)
    seen: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("concern_id") or "")
        for key, val in row.items():
            if isinstance(val, str) and not val.strip():
                errors.append(f"Response field {key!r} for concern {cid or '?'} cannot be blank")
        if not [ref for ref in (row.get("evidence_refs") or []) if str(ref).strip()]:
            errors.append(f"Planner response for concern {cid or '?'} must cite investigated evidence in evidence_refs")
        if cid in expected:
            seen.append(cid)
    if expected and (len(seen) != len(set(seen)) or set(seen) != expected):
        missing = sorted(expected - set(seen))
        dupes = sorted({cid for cid in seen if seen.count(cid) > 1})
        parts = []
        if missing:
            parts.append(f"missing: {', '.join(missing)}")
        if dupes:
            parts.append(f"duplicate: {', '.join(dupes)}")
        errors.append("Plan revision responses must answer each review concern once (" + "; ".join(parts) + ")")


def _check_planning(stage: str, data: dict[str, Any], spec: dict[str, Any], errors: list[str]) -> None:
    contract = data.get("contract")
    if isinstance(contract, dict):
        contract = json.loads(json.dumps(contract))
        records = [row for row in (spec.get("sources") or []) if isinstance(row, dict)]
        if records:
            source_trace.resolve_sentence_ids(contract, records)
            try:
                source_trace.check_citations(contract, records)
            except ValueError as exc:
                errors.append(str(exc))
            if stage == "finalize_plan" and any(
                row.get("sources") for row in (contract.get("acceptance_criteria") or []) if isinstance(row, dict)
            ):
                try:
                    source_trace.check_ready(
                        contract,
                        records,
                        source_trace.DEFAULT_TRACE,
                        coverage_records=source_trace._coverage_scoped(records),
                    )
                except ValueError as exc:
                    errors.append(str(exc))
        if contract.get("milestones"):
            try:
                planning_graph.derive(contract)
            except ValueError as exc:
                errors.append(str(exc))
    if stage == "revise_plan":
        concern_ids = [str(cid) for cid in (spec.get("concern_ids") or []) if str(cid).strip()]
        _check_revise_responses(data, concern_ids, errors)


def _iter_evidence_strings(data: Any) -> list[str]:
    found: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "evidence_refs" and isinstance(value, list):
                found.extend(str(item) for item in value if isinstance(item, str))
            elif key in ("evidence", "e2e_evidence") and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_iter_evidence_strings(value))
    elif isinstance(data, list):
        for item in data:
            found.extend(_iter_evidence_strings(item))
    return found


def _check_validator(data: dict[str, Any], workspace: Path, errors: list[str]) -> None:
    raw_checks = data.get("checks")
    checks: list[Any] = raw_checks if isinstance(raw_checks, list) else []
    passing_claim = data.get("status") == "PASS"
    evidence_strings = _iter_evidence_strings(data)
    for text in evidence_strings:
        for match in _CHECK_REF_RE.finditer(text):
            index = int(match.group(1))
            if index < 1 or index > len(checks):
                errors.append(f"Evidence check:{index} names no listed check; checks are numbered from 1")
            elif (
                passing_claim
                and isinstance(checks[index - 1], dict)
                and checks[index - 1].get("exit_code") not in (0, None)
            ):
                code = checks[index - 1].get("exit_code")
                errors.append(f"Passing validation cites check:{index} with nonzero exit_code {code}")
        if str(workspace):
            for match in _EVIDENCE_JSON_RE.finditer(text):
                rel = match.group(1)
                if not (workspace / rel).is_file():
                    errors.append(f"Cited evidence file does not exist in workspace: {rel}")
    if "checks" in data and passing_claim and not any(isinstance(c, dict) and c.get("exit_code") == 0 for c in checks):
        errors.append("Technically passing validation lists no check with exit_code 0")
    if "checks" in data and "status" in data:
        try:
            check_refs.require_passing_evidence(data)
        except ValueError as exc:
            message = str(exc)
            if message not in errors:
                errors.append(message)


def _check_investigate_stuck(data: dict[str, Any], errors: list[str]) -> None:
    if not str(data.get("diagnosis") or "").strip():
        errors.append("An investigation must say what it found")
    if data.get("recommendation") != "retry":
        return
    if not str(data.get("guidance") or "").strip():
        errors.append("A retry needs concrete guidance for the next attempt")
    if not str(data.get("example") or "").strip():
        errors.append("A retry needs the diagnosed cause as an example in plain English")
    if bool(str(data.get("probe") or "").strip()) == bool(str(data.get("untestable") or "").strip()):
        errors.append(
            "A retry needs exactly one of probe (a command that exits 0 exactly when the cited "
            "files show the cause) or untestable (why no command can)"
        )
    if not [ref for ref in (data.get("evidence_refs") or []) if str(ref).strip()]:
        errors.append("A retry must cite the files it relied on in evidence_refs")


def check_report(raw_text: str, *, spec: dict[str, Any]) -> list[str]:
    """Validate candidate report JSON text against a public spec without mutating any files."""
    try:
        data = json.loads(raw_text)
    except ValueError as exc:
        return [f"Report is not valid JSON: {exc}"]
    schema = spec.get("schema")
    if isinstance(schema, dict) and schema:
        try:
            util.validate_schema(data, schema, where="$")
        except ValueError as exc:
            return [str(exc)]
    if not isinstance(data, dict):
        return ["Report JSON must be an object"]
    errors: list[str] = []
    stage = str(spec.get("stage") or "")
    workspace_raw = str(spec.get("workspace") or "")
    workspace = Path(workspace_raw) if workspace_raw else Path()
    if stage == "requirements_gather":
        _check_requirements_gather(data, spec, errors)
    elif stage in _PLANNING_STAGES:
        _check_planning(stage, data, spec, errors)
    elif stage in _VALIDATOR_STAGES or stage == "review_plan":
        _check_validator(data, workspace, errors)
    elif stage == "investigate_stuck":
        _check_investigate_stuck(data, errors)
    return errors


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="autocode report-check",
        description="Validate a candidate stage JSON report before outputting it (read-only).",
    )
    p.add_argument(
        "report",
        nargs="?",
        default="-",
        help="Path to candidate report JSON file, or '-' to read from stdin (default).",
    )
    p.add_argument("--spec", type=Path, help="Path to the runner-written report-check spec JSON.")
    p.add_argument("--schema", type=Path, help="Path to a stage JSON schema (when --spec is omitted).")
    p.add_argument("--stage", help="Stage name for validation rules (when --spec is omitted).")
    p.add_argument("--workspace", type=Path, help="Workspace root for evidence file checks (when --spec is omitted).")
    p.add_argument("--json", action="store_true", help="Emit validation result as JSON.")
    return p


def _load_spec(args: argparse.Namespace) -> dict[str, Any]:
    if args.spec is not None:
        return json.loads(args.spec.read_text(encoding="utf-8"))
    if args.schema is None or not args.stage:
        raise ValueError("Provide --spec SPEC_PATH or both --schema SCHEMA_PATH and --stage STAGE")
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    workspace = args.workspace.resolve() if args.workspace is not None else Path.cwd()
    return build_spec(stage=args.stage, workspace=workspace, schema=schema)


def _read_candidate(report_arg: str) -> str:
    if report_arg == "-":
        return sys.stdin.read()
    return Path(report_arg).read_text(encoding="utf-8")


def cli(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        spec = _load_spec(args)
        raw_text = _read_candidate(args.report)
    except (OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "stage": args.stage or "", "errors": [str(exc)]}, indent=2))
        else:
            print(f"autocode report-check error: {exc}", file=sys.stderr)
        return 2
    errors = check_report(raw_text, spec=spec)
    stage = str(spec.get("stage") or "")
    if args.json:
        print(json.dumps({"ok": not errors, "stage": stage, "errors": errors}, indent=2))
    elif errors:
        for err in errors:
            print(err, file=sys.stderr)
    else:
        print(f"OK ({stage})")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(cli())

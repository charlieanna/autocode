"""Canonical, versioned evidence document and its deterministic Markdown rendering.

This is a report of supplied public facts, never a verifier or completion gate.
"""

import html
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

try:
    from . import autocode_roles as roles
    from . import autocode_util as util
except ImportError:
    import autocode_roles as roles
    import autocode_util as util

SCHEMA_PATH = Path(__file__).parent / "autocode-schemas/evidence-report.schema.json"


def mapping(value):
    return value if isinstance(value, dict) else {}


def rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def select(row, keys):
    return {key: deepcopy(row.get(key)) for key in keys}


def accounting_facts(accounting, *, role_context=None):
    """Copy exactly the usage facts rendered by a completed checkpoint report.

    Native request/log bookkeeping and status inspection metadata are not report
    facts. The caller supplies its already computed public accounting projection.
    """
    accounting = mapping(accounting)
    attempts = []
    for row in rows(accounting.get("attempts")):
        attempt = select(
            row,
            (
                "identity",
                "stage",
                "engine",
                "model",
                "source_revision",
                "started_at",
                "finished_at",
                "duration_seconds",
                "runner_owned",
                "rejected",
                "interrupted",
                "timed_out",
                "exit_code",
                "tokens",
                "reported_cost_usd",
                "historical_estimated_cost_usd",
                "usage_basis",
                "request_coverage_complete",
            ),
        )
        attempt["role"] = roles.screen_name(row.get("stage") or "", role_context) or row.get("role") or "Runner"
        attempts.append(attempt)
    return {
        "attempts": attempts,
        "usage": select(accounting, ("tokens", "provider_requests", "cost")),
        "incomplete": not bool(accounting) or bool(accounting.get("issues")),
    }


def build(view, *, run_identity, completed_at, provenance, binding, kind="task", children=(), role_context=None):
    evidence, verification = mapping(view.get("evidence")), mapping(view.get("verification"))
    accounting = accounting_facts(mapping(view.get("usage")).get("accounting"), role_context=role_context)
    coverage = {row.get("id"): row for row in rows(verification.get("coverage"))}
    criteria = []
    for row in rows(evidence.get("acceptance")):
        detail = coverage.get(row.get("id"), {})
        criteria.append(
            {
                **select(row, ("id", "criterion", "status", "validator_status", "human_reviewed", "evidence")),
                **select(detail, ("verification_method", "human_review", "evidence_refs")),
            }
        )
    checks = [
        select(
            row,
            ("command", "exit_code", "timed_out", "duration_seconds", "output", "output_sha256", "purpose", "results"),
        )
        for row in rows(mapping(evidence.get("check_replay")).get("checks"))
    ]
    unverified = []
    for row in criteria:
        if row["validator_status"] != "PASS":
            unverified.append(f"{row['id']}: Validator {row['validator_status'] or 'not recorded'}")
        if row["human_review"] and not row["human_reviewed"]:
            unverified.append(f"{row['id']}: human acceptance not recorded")
    if not checks:
        unverified.append("No independent check replay recorded; job completion does not invent code verification")
    for check in checks:
        if check["timed_out"] or (check["exit_code"] != 0 and mapping(check["results"]).get("ok") is not True):
            unverified.append("Check failed or unavailable: " + str(check["command"]))
    proof = mapping(evidence.get("regression_proof"))
    if proof.get("verdict") and proof["verdict"] != "PASS":
        unverified.append("Regression proof verdict: " + str(proof["verdict"]))
    unverified += ["Regression proof unverified: " + str(gap) for gap in proof.get("unverified") or []]
    if accounting["incomplete"]:
        unverified.append("Reported usage is incomplete or unavailable")
    if provenance.get("kind") in ("fake", "mixed", "unknown"):
        unverified.append("Model effectiveness is not established by fake, mixed or undeclared provider evidence")
    started_at = evidence.get("created_at")
    elapsed = None
    if started_at and completed_at:
        try:
            duration = (datetime.fromisoformat(completed_at) - datetime.fromisoformat(started_at)).total_seconds()
            elapsed = duration if duration >= 0 else None
        except (TypeError, ValueError):
            pass
    document = {
        "version": 1,
        "kind": kind,
        "binding": binding,
        "run": {
            "id": str(run_identity),
            "status": view.get("status") or "",
            "workflow": view.get("workflow"),
            "completed_at": completed_at,
            "turn": view.get("turn"),
            "started_at": started_at,
            "elapsed_seconds": elapsed,
            **(
                {"interaction_timing": deepcopy(evidence["interaction_timing"])}
                if evidence.get("interaction_timing")
                else {}
            ),
        },
        "provenance": deepcopy(provenance),
        "plan": {
            "contract_token": verification.get("contract_token"),
            "criteria_revision": verification.get("criteria_revision"),
            "task_id": verification.get("task_id"),
            "intended_outcome": evidence.get("outcome"),
        },
        "revision": {
            "base_commit": evidence.get("base_commit"),
            "source_revision": evidence.get("validator_source_revision"),
            "meaning": "Content snapshot checked before delivery; a later delivery commit is not a tested Git SHA",
        },
        "criteria": criteria,
        "checks": checks,
        "workflow_result": deepcopy(evidence.get("workflow_result")),
        "regression_proof": deepcopy(evidence.get("regression_proof")),
        "test_cases": deepcopy(evidence.get("test_cases") or []),
        "findings": deepcopy(evidence.get("findings") or []),
        "attempts": accounting["attempts"],
        "usage": accounting["usage"],
        "unverified": unverified,
        "children": deepcopy(list(children)),
    }
    validate(document)
    return document


def validate(document):
    util.validate_schema(document, json.loads(SCHEMA_PATH.read_text()))
    # Also reject non-JSON numbers (NaN/Infinity) in nested public accounting.
    json.dumps(document, allow_nan=False)


REDACTION_NOTICE = (
    "Text matching the shared credential policy was masked. Artifact hashes still "
    "identify the original raw evidence bytes."
)
_AUTH_FIELDS = frozenset(("binding", "contract_token", "criteria_revision", "source_revision", "base_commit"))


def sanitize(value):
    """Copy export facts through the shared policy without rebinding raw proof."""

    def visit(item):
        if isinstance(item, str):
            return util.redact(item)
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, dict):
            copied = {}
            for key, child in item.items():
                safe_key, safe_child = util.redact(key), visit(child)
                if safe_key in copied:
                    raise ValueError("Credential masking would merge distinct evidence keys")
                if isinstance(key, str) and (key in _AUTH_FIELDS or key.endswith("_sha256")) and safe_child != child:
                    raise ValueError("Credential masking would change an authentication identifier")
                copied[safe_key] = safe_child
            return copied
        return item

    result = visit(value)
    formatted = _render(result)
    if (result != value or util.redact(formatted) != formatted) and REDACTION_NOTICE not in result["unverified"]:
        result["unverified"].append(REDACTION_NOTICE)
    return result


def render(document):
    """Mask formatting-introduced matches before the canonical Markdown is bound."""
    return util.redact(_render(document))


def text(value):
    value = "not recorded" if value is None else str(value)
    return (
        html.escape(value, quote=False)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("`", "\\`")
        .replace("\r", " ")
        .replace("\n", "<br>")
    )


def _render(document):
    validate(document)
    run, revision, plan = document["run"], document["revision"], document["plan"]
    provenance = document["provenance"]
    lines = [
        "## AutoCode evidence",
        "",
        f"Run **{text(run['id'])}**: {text(run['status'])} ({text(run['workflow'])}).",
        f"Provider evidence: **{text(provenance['kind'])}**; {text(provenance['basis'])}. {text(provenance['declaration'])}.",
        "This record reports what was checked. Review the diff; completion is evidence, not proof of correctness.",
        "",
        "## Intended outcome",
        "",
        text(plan["intended_outcome"]),
        "",
        f"Agreed plan: {text(plan['contract_token'])}. Completed: {text(run['completed_at'])}.",
        f"Elapsed wall time (including input waits): {text(run['elapsed_seconds'])} seconds.",
        f"Checked source snapshot: {text(revision['source_revision'])}; base commit: {text(revision['base_commit'])}.",
        revision["meaning"],
        "",
        "## Acceptance criteria",
        "",
        "| ID | Criterion | Result | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    timing = run.get("interaction_timing")
    if timing:
        position = lines.index("## Acceptance criteria")
        lines[position:position] = [
            "## First interaction",
            "",
            f"Launched: {text(timing['launched_at'])}. Wall time includes waits for human input.",
            "Builder measures successful provider supervisor or parallel Builder worker launch; it does not attest first model response.",
            "",
            "| Milestone | First observed at | Seconds from launch |",
            "| --- | --- | ---: |",
            *[
                f"| {label} | {text(timing[f'first_{event}_at'])} | {text(timing[f'first_{event}_seconds'])} |"
                for event, label in (
                    ("question", "Question shown"),
                    ("plan", "Plan shown for approval"),
                    ("builder", "Builder dispatched"),
                )
            ],
            "",
        ]
    job = document.get("workflow_result")
    if job:
        lines[lines.index("## Acceptance criteria") : lines.index("## Acceptance criteria")] = [
            "## Workflow result",
            "",
            f"Outcome: {text(job['outcome'])}. {text(job['summary'])}.",
            f"Artifact: {text(job['artifact'])} ({text(job['artifact_sha256'])}).",
            "",
        ]
    for row in document["criteria"]:
        status = f"{row['status'] or 'no outcome recorded'}, validator: {row['validator_status'] or 'unchecked'}"
        if row["human_reviewed"]:
            status += ", accepted by a person"
        lines.append(f"| {text(row['id'])} | {text(row['criterion'])} | {text(status)} | {text(row['evidence'])} |")
    if not document["criteria"]:
        lines.append("None recorded.")
    lines += [
        "",
        "## Independent checks",
        "",
        "Artifact references below are local files, not publicly hosted evidence.",
        "",
        "| Command | Exit | Timed out | Seconds | Artifact (SHA-256) | Results |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in document["checks"]:
        result = json.dumps(row["results"], sort_keys=True, allow_nan=False) if row["results"] is not None else None
        lines.append(
            f"| {text(row['command'])} | {text(row['exit_code'])} | {text(row['timed_out'])} | {text(row['duration_seconds'])} | {text(row['output'])} ({text(row['output_sha256'])}) | {text(result)} |"
        )
    if not document["checks"]:
        lines.append("None recorded.")
    proof = mapping(document["regression_proof"])
    if document["test_cases"]:
        lines += [
            "",
            "## Regression tests in plain English",
            "",
            "| Case | Given | When | Then | Test |",
            "| --- | --- | --- | --- | --- |",
        ]
        for case in document["test_cases"]:
            names = mapping(proof.get("case_tests")).get(case.get("id")) or []
            lines.append(
                "| "
                + " | ".join(text(case.get(key)) for key in ("id", "given", "when", "then"))
                + " | "
                + text(", ".join(names) or "not proven")
                + " |"
            )
    lines += ["", "## Regression proof", "", "Runner verdict: " + text(proof.get("verdict")) + "."]
    if proof.get("fail_to_pass"):
        lines.append("Fail-before/pass-after tests: " + text(", ".join(proof["fail_to_pass"])))
    for key, names in sorted(mapping(proof.get("case_tests")).items()):
        lines.append(f"- {text(key)}: {text(', '.join(names))}")
    for label, command in sorted(mapping(proof.get("commands")).items()):
        lines.append(f"- {text(label)} command: {text(command)}")
    lines += ["- Failure: " + text(failure) for failure in proof.get("failures") or []]
    lines += ["- Unverified: " + text(gap) for gap in proof.get("unverified") or []]
    lines += ["", "## Review findings", ""]
    lines += [
        f"- {text(row.get('id'))} [{text(row.get('status'))}, {text(row.get('severity'))}]: {text(row.get('finding'))}"
        for row in document["findings"]
    ] or ["None recorded."]
    lines += [
        "",
        "## Roles and models",
        "",
        "| Job | Engine | Model | Seconds | Result |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in document["attempts"]:
        result = "interrupted" if row["interrupted"] else "rejected" if row["rejected"] else f"exit {row['exit_code']}"
        lines.append(
            f"| {text(row['role'])} | {text(row['engine'])} | {text(row['model'])} | {text(row['duration_seconds'])} | {text(result)} |"
        )
    lines += [
        "",
        "## Reported usage",
        "",
        text(json.dumps(document["usage"], sort_keys=True, allow_nan=False)),
        "",
        "Usage is the accounting snapshot captured at this completed checkpoint; it does not attest all later raw log bytes.",
        "Reported cost, historical estimates and unknown quantities remain separate; this is not a subscription invoice.",
        "",
        "## Not verified",
        "",
    ]
    lines += ["- " + text(reason) for reason in document["unverified"]] or [
        "No additional gap recorded; this does not replace review."
    ]
    if document["children"]:
        lines += ["", "## Child evidence", ""]
        lines += [
            f"- {text(row['id'])}: {text(row['json_path'])} ({text(row['json_sha256'])})"
            for row in document["children"]
        ]
    return "\n".join(lines) + "\n"

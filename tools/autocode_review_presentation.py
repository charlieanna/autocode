"""Human artifact review from the public task-run status view, with no I/O.

Recorded checks remain recorded checks: presentation never authenticates their
files, grants approval, or treats a log excerpt as complete command output.
"""

from __future__ import annotations

import base64
import binascii
import json
from copy import deepcopy

try:
    from . import autocode_brief_acceptance as brief_acceptance
except ImportError:
    import autocode_brief_acceptance as brief_acceptance


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _ids(value):
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def review_data(view):
    """Copy human criteria and their supporting evidence from one inspected view.

    ``pending`` comes only from the run's current review request, including IDs
    missing from older projections. All declared human criteria stay visible so
    a refreshed presentation retains the decisions already recorded.
    """
    view = _mapping(view)
    needs = _mapping(view.get("needs"))
    pending = _ids(needs.get("criteria")) if needs.get("kind") == "review" else []
    evidence = _mapping(view.get("evidence"))
    verification = _mapping(view.get("verification"))
    contract = _mapping(_mapping(view.get("approved_contract")).get("body"))
    planned = {row.get("id"): row for row in _rows(contract.get("acceptance_criteria"))}
    coverage = {row.get("id"): row for row in _rows(verification.get("coverage"))}
    acceptance = {row.get("id"): row for row in _rows(evidence.get("acceptance"))}
    order = list(dict.fromkeys([*planned, *coverage, *acceptance, *pending]))
    replay = _mapping(evidence.get("check_replay"))
    checks = _rows(replay.get("checks"))
    observations = _observations(contract, replay)
    findings = [row for row in _rows(evidence.get("findings")) if row.get("status") == "open"]
    criteria = []
    for cid in order:
        plan, verified, accepted = planned.get(cid, {}), coverage.get(cid, {}), acceptance.get(cid, {})
        if not isinstance(cid, str) or not (
            plan.get("human_review") is True or verified.get("human_review") is True or cid in pending
        ):
            continue
        refs = _ids(verified.get("evidence_refs"))
        associated = [check for check in checks if check.get("evidence_ref") in refs]
        # Older views may omit check evidence_ref; do not imply their command
        # proves this criterion just because it ran somewhere in the task.
        unassociated = [check for check in checks if not check.get("evidence_ref")]
        criteria.append(
            {
                "id": cid,
                "text": plan.get("criterion")
                or plan.get("text")
                or verified.get("criterion")
                or accepted.get("criterion")
                or "Criterion text is unavailable in this status view.",
                "pending": cid in pending,
                "human_reviewed": accepted.get("human_reviewed") is True,
                "verification_method": plan.get("verification_method") or verified.get("verification_method"),
                "recorded_status": verified.get("recorded_status") or accepted.get("validator_status"),
                "freshness": verification.get("freshness") or "not_inspected",
                "reasons": deepcopy(verification.get("reasons") or []),
                "evidence": deepcopy(accepted.get("evidence")),
                "evidence_refs": refs,
                "checks": deepcopy(associated),
                "unassociated_checks": deepcopy(unassociated),
                "exact_observations": deepcopy([check for check in observations if cid in check["criteria"]]),
                "unassociated_observations": deepcopy([check for check in observations if not check["criteria"]]),
                "regression_proof": deepcopy(evidence.get("regression_proof")),
                "diff_coverage": deepcopy(evidence.get("diff_coverage")),
                "open_findings": deepcopy(
                    [
                        finding
                        for finding in findings
                        if not _ids(_mapping(finding.get("scope")).get("criteria"))
                        or cid in _ids(_mapping(finding.get("scope")).get("criteria"))
                    ]
                ),
            }
        )
    return {
        "status": view.get("status"),
        "token": needs.get("token") if needs.get("kind") == "review" else None,
        "question": needs.get("question") if needs.get("kind") == "review" else None,
        "freshness": verification.get("freshness") or "not_inspected",
        "reasons": deepcopy(verification.get("reasons") or []),
        "criteria": criteria,
    }


def _observations(contract, replay):
    manifest = _mapping(_mapping(contract.get("brief_acceptance")).get("manifest"))
    planned = {row.get("hash"): row for row in _rows(manifest.get("observations"))}
    observations = []
    for row in _rows(_mapping(replay.get("brief_acceptance")).get("checks")):
        plan = planned.get(row.get("observation_hash"), {})
        proposal = _mapping(plan.get("proposal"))
        expected = None
        if plan:
            expected = {
                "format": _mapping(plan.get("declaration")).get("literal"),
                "exact_items": proposal.get("expect") is not None,
            }
            try:
                expected.update(
                    required_values=brief_acceptance.required_values(plan),
                    absent_values=brief_acceptance.absent_values(plan),
                )
            except (KeyError, TypeError, ValueError, IndexError):
                expected["unavailable"] = "The status view does not contain a complete expected-output declaration."
        observations.append({**deepcopy(row), "criteria": _ids(proposal.get("criterion_ids")), "expected": expected})
    return observations


def display_text(value):
    """Keep report text literal; terminal control sequences cannot hide a prompt."""
    if value is None:
        return "not recorded"
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "".join(char if (char in "\n\t" or char.isprintable()) else f"\\x{ord(char):02x}" for char in value)


def _output(lines, check, *, indent="  "):
    path, digest = check.get("output"), check.get("output_sha256")
    lines.append(f"{indent}Exact output: {display_text(path)}")
    if digest:
        lines.append(f"{indent}Output SHA-256: {display_text(digest)}")
    if check.get("tail"):
        lines.append(f"{indent}Saved output excerpt (not the complete output):")
        lines.extend(indent + "  " + line for line in display_text(check["tail"]).splitlines())


def _check(lines, check):
    lines.extend(
        [
            "  Command: " + display_text(check.get("command")),
            "  Exit code: " + display_text(check.get("exit_code")),
        ]
    )
    if check.get("timed_out"):
        lines.append("  Timed out: true")
    if check.get("error"):
        lines.append("  Check error: " + display_text(check["error"]))
    results = _mapping(check.get("results"))
    if not results:
        lines.append("  Named tests: not recorded")
    else:
        lines.append("  Named tests (complete: " + display_text(results.get("complete")) + "):")
        found = False
        for outcome in ("passed", "failed", "skipped", "collection_errors", "uncollected"):
            for name in _ids(results.get(outcome)):
                lines.append(f"    {outcome}: {display_text(name)}")
                found = True
        if not found:
            lines.append("    No named test results recorded.")
    _output(lines, check)


def _exact_observation(lines, check):
    lines.append("  Command: " + display_text(check.get("command")))
    lines.append("  Exit code: " + display_text(check.get("exit_code")))
    if check.get("error"):
        lines.append("  Observation error: " + display_text(check["error"]))
    expected = _mapping(check.get("expected"))
    if not expected:
        lines.append("  Expected output: unavailable in this status view")
    else:
        lines.append("  Expected output format: " + display_text(expected.get("format")))
        if expected.get("unavailable"):
            lines.append("  " + display_text(expected["unavailable"]))
        else:
            lines.append(
                "  Expected items"
                + (" (exact)" if expected.get("exact_items") else "")
                + ": "
                + display_text(expected.get("required_values"))
            )
            lines.append("  Expected absent items: " + display_text(expected.get("absent_values")))
    observation = _mapping(check.get("observation"))
    lines.append("  Recorded output comparison: " + display_text(observation.get("verdict")))
    for step in _rows(observation.get("steps")):
        lines.append("    Invocation: " + display_text(step.get("argv")))
        lines.append("    Exit code: " + display_text(step.get("exit_code")))
        for stream in ("stdout", "stderr"):
            try:
                data = base64.b64decode(step.get(stream + "_base64"), validate=True)
            except (ValueError, TypeError, binascii.Error):
                lines.append(f"    Exact {stream}: unavailable (invalid encoded output)")
                continue
            # repr of bytes preserves whitespace and non-UTF-8 output without
            # interpreting terminal escape bytes or silently replacing them.
            lines.append(f"    Exact {stream} bytes: {data!r}")
    _output(lines, check)


def _criterion_steps(lines, row):
    """Show the saved steps attributed to this criterion, labeled as attribution."""
    lines.append("Saved Builder steps attributed to this criterion (attribution, not proof):")
    shown = _mapping(row.get("criterion_diffs"))
    criterion = next((item for item in _rows(shown.get("criteria")) if item.get("id") == row.get("id")), None)
    if criterion is None:
        lines.append("  Unavailable in this status view.")
        if shown.get("reason"):
            lines.append("  " + display_text(shown["reason"]))
        return
    lines.append("  " + display_text(shown.get("meaning")))
    if criterion.get("overlapping"):
        lines.append("  A step of another milestone also changed some of these files; their diffs may hold that work.")
    if not _ids(criterion.get("checkpoints")):
        lines.append("  No saved Builder step is attributed to this criterion yet.")
    steps = _mapping(shown.get("steps"))
    # A parallel batch's step is shared; its other members' files belong to their own criteria.
    own = set(_ids(criterion["files"])) if isinstance(criterion.get("files"), list) else None
    for ident in _ids(criterion.get("checkpoints")):
        step = _mapping(steps.get(ident))
        served = _ids(step.get("milestones"))
        owner = (
            "milestones " + ", ".join(display_text(item) for item in served)
            if served
            else "milestone " + display_text(step.get("milestone_id"))
        )
        lines.append("  " + display_text(ident) + " (" + owner + "): " + display_text(step.get("label")))
        lines.append("    Availability: " + display_text(step.get("availability") or "unavailable"))
        if step.get("reason"):
            lines.append("    " + display_text(step["reason"]))
        files = _rows(step.get("files"))
        others = [file.get("path") for file in files if own is not None and file.get("path") not in own]
        if others:
            lines.append(
                "    Not shown, changed in this step for another milestone: "
                + ", ".join(display_text(path) for path in others)
            )
        for file in files:
            if own is not None and file.get("path") not in own:
                continue
            lines.append("    " + display_text(file.get("path")))
            if file.get("text"):
                lines.extend("      " + line for line in display_text(file["text"]).splitlines())
            if file.get("reason"):
                lines.append("      " + display_text(file["reason"]))


def render_review(row):
    """Render one ``review_data`` criterion without fetching paths or running checks."""
    lines = [
        display_text(row.get("id")) + ": " + display_text(row.get("text")),
        "Human review: "
        + (
            "pending"
            if row.get("pending")
            else "previously recorded"
            if row.get("human_reviewed")
            else "not currently requested"
        ),
        "Verification method: " + display_text(row.get("verification_method")),
        "Recorded automated result: " + display_text(row.get("recorded_status")),
        "Evidence freshness: " + display_text(row.get("freshness")),
    ]
    lines.extend("  " + display_text(reason) for reason in row.get("reasons") or [])
    lines.append("Evidence summary: " + display_text(row.get("evidence")))
    lines.append("Evidence references:")
    lines.extend("  " + display_text(ref) for ref in row.get("evidence_refs") or [])
    if not row.get("evidence_refs"):
        lines.append("  None recorded.")
    lines.append("Supporting commands, named tests and exact output:")
    if not row.get("checks"):
        lines.append("  No runner check is linked to this criterion in the status view.")
    for check in _rows(row.get("checks")):
        _check(lines, check)
    if row.get("unassociated_checks"):
        lines.append("Run-level checks (criterion association unavailable):")
        for check in _rows(row["unassociated_checks"]):
            _check(lines, check)
    lines.append("Exact-output observations linked to this criterion:")
    if not row.get("exact_observations"):
        lines.append("  None recorded.")
    for check in _rows(row.get("exact_observations")):
        _exact_observation(lines, check)
    if row.get("unassociated_observations"):
        lines.append("Run-level exact-output observations (criterion association unavailable):")
        for check in _rows(row["unassociated_observations"]):
            _exact_observation(lines, check)
    proof = _mapping(row.get("regression_proof"))
    lines.append("Run-level regression proof (not criterion-specific):")
    if not proof:
        lines.append("  Not recorded.")
    else:
        lines.append("  Recorded verdict: " + display_text(proof.get("verdict")))
        for purpose, command in _mapping(proof.get("commands")).items():
            if not purpose.endswith("_source"):
                lines.append("  " + display_text(purpose) + " command: " + display_text(command))
        lines.append("  Named fail-to-pass tests: " + display_text(proof.get("fail_to_pass")))
        for case, names in _mapping(proof.get("case_tests")).items():
            lines.append("  " + display_text(case) + " named tests: " + display_text(names))
        if proof.get("unstable"):
            lines.append("  Unstable tests: " + display_text(proof["unstable"]))
        for kind in ("failures", "unverified"):
            for reason in _ids(proof.get(kind)):
                lines.append("  " + kind + ": " + display_text(reason))
    lines.append("Run-level diff coverage (not criterion-specific):")
    coverage = _mapping(row.get("diff_coverage"))
    if not coverage:
        lines.append("  Not recorded.")
    else:
        lines.append("  Collected: " + display_text(coverage.get("collected")))
        lines.append("  Tool: " + display_text(coverage.get("tool")))
        if coverage.get("note"):
            lines.append("  " + display_text(coverage["note"]))
        for file in _rows(coverage.get("files")):
            lines.append("  " + display_text(file.get("path")))
            for hunk in _rows(file.get("hunks")):
                status = (
                    "covered"
                    if hunk.get("covered") is True
                    else "uncovered"
                    if hunk.get("covered") is False
                    else "unmeasured"
                )
                lines.append(f"    Lines {display_text(hunk.get('start'))}-{display_text(hunk.get('end'))}: {status}")
                if hunk.get("uncovered_lines"):
                    lines.append("    Uncovered lines: " + display_text(hunk["uncovered_lines"]))
        if coverage.get("unmeasured"):
            lines.append("  Unmeasured files: " + display_text(coverage["unmeasured"]))
    diff = _mapping(row.get("diff_hunks"))
    _criterion_steps(lines, row)
    lines.append("Run-level diff hunks:")
    if not diff:
        lines.append("  Unavailable in this status view.")
    else:
        lines.append("  " + display_text(diff.get("meaning")))
        lines.append("  Availability: " + display_text(diff.get("availability")))
        if diff.get("reason"):
            lines.append("  " + display_text(diff["reason"]))
        for file in _rows(diff.get("files")):
            lines.append("  " + display_text(file.get("path")))
            if file.get("text"):
                lines.extend("    " + line for line in display_text(file["text"]).splitlines())
            if file.get("reason"):
                lines.append("    " + display_text(file["reason"]))
    lines.append("Open findings (including findings without criterion scope):")
    if not row.get("open_findings"):
        lines.append("  None recorded.")
    for finding in _rows(row.get("open_findings")):
        lines.append(
            "  "
            + display_text(finding.get("id"))
            + " ["
            + display_text(finding.get("severity"))
            + "]: "
            + display_text(finding.get("finding"))
        )
    return "\n".join(lines)

"""Tie a claim a stage reports to the recorded observation that establishes it (#3904, #3947).

A stage's report states what an observation established (this input exits 2, these documentation
references resolve) and AutoCode used to accept the statement without reading that observation. This
module finds such claims in the report fields that already carry them and judges each against what
the runner recorded, returning rows ``{source, id, claim, observation, status, reason}`` with status
``established``, ``unverified`` or ``contradicted``, and the problems that refuse the report.

It decides only from what recorded receipts show (exit_code, per-test results, receipt kind, recorded
base revision, recorded command), never from a runner, language or file name:

- An exit-code statement in a Completion Reviewer's ``agreed_limitations`` or ``deferred_backlog`` that
  states exactly one exit code and cites captures by file name is compared with their recorded exit
  codes. A capture that is a test run (per-test results, or a command
  autocode_verification_commands.command_framework recognizes) records its runner's exit code, never
  the product's, so it is not compared. Established when one compared capture matches; contradicted,
  and refused, only when every cited capture was read, none is a test run and every one differs;
  otherwise unverified (a missing or unreadable capture is never a contradiction).
- A Tester PASS for an approved criterion that claims references resolve needs a base-versus-candidate
  diagnostics comparison (autocode_diagnostics_comparison) against the run's base. A single run, a
  comparison against another base or one with new diagnostics is refused. One with no new diagnostics
  is admitted but recorded unverified: nothing recorded shows the compared command reports unresolved
  references.

``autocode report-check`` and runner admission call the same functions (one answer, read from one
place). Imports only low-level helpers; callers pass state, workspace and run directory.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_evidence_events as event_log
    from . import autocode_receipt_auth as receipt_auth
    from . import autocode_run_base as run_base
    from . import autocode_util as util
    from . import autocode_verification_commands as verification_commands
except ImportError:
    import autocode_evidence_events as event_log
    import autocode_receipt_auth as receipt_auth
    import autocode_run_base as run_base
    import autocode_util as util
    import autocode_verification_commands as verification_commands

ESTABLISHED, UNVERIFIED, CONTRADICTED = "established", "unverified", "contradicted"
COMPARISON = "diagnostics_comparison"
SPEC_KEY = "claim_context"
CLAIM_STAGES = ("tester", "review_checkpoint", "review_completion")
REPORT_FIELDS = ("agreed_limitations", "deferred_backlog")

_EXIT = re.compile(r"\bexit(?:s|ed)?(?:\s+(?:with\s+)?(?:code|status))?[\s:=-]+(\d+)\b", re.IGNORECASE)
_CITED = re.compile(r"(?<![A-Za-z0-9_./-])((?:\./)?[A-Za-z0-9_./-]*[A-Za-z0-9_-]\.json)\b")
_REFERENCE = re.compile(r"\b(?:cross-?references?|references?|refs?|xrefs?|links?)\b", re.IGNORECASE)
_RESOLVE = re.compile(r"\bresolve[sd]?\b", re.IGNORECASE)
_CHECK = re.compile(r"check:(\d+)")


def context(state: RunState, workspace, run_dir=None, *, record=None) -> dict[str, Any]:
    """What the judge reads from a run: approved criterion texts, the run's base and evidence folders."""
    contract = (state.get("goal_contract") or {}).get("body") or {}
    rows = contract.get("acceptance_criteria") or state.get("acceptance_criteria") or []
    criteria = {
        str(row.get("id")): str(row.get("criterion") or row.get("text") or "")
        for row in rows
        if isinstance(row, dict) and row.get("id")
    }
    run_dir = run_dir or state.get("run_dir")
    base = state.get("base_commit")
    if not base and workspace and any(reference_claim(text) for text in criteria.values()):
        try:  # only a reference claim reads the base; recorded by the run, else its first stage's HEAD
            base = run_base.base_commit(state, workspace)
        except (OSError, ValueError):
            base = None
    return {
        "workspace": str(workspace or ""),
        "criteria": criteria,
        "base_commit": base or "",
        "evidence_dirs": evidence_dirs(workspace, run_dir),
        "attempt": {key: record.get(key) for key in ("events", "output_mode", "capture_context")}
        if isinstance(record, dict)
        else {},
    }


def evidence_dirs(workspace, run_dir) -> list[str]:
    folders = [Path(workspace) / ".autocode" / "evidence"] if workspace else []
    folders += [Path(run_dir) / "evidence"] if run_dir else []
    return list(dict.fromkeys(str(folder) for folder in folders))


def judge(stage: str, report: dict, claim_context: dict) -> tuple[list[str], list[dict]]:
    """Problems that refuse ``report`` and the rows recorded for it, for one stage's report."""
    problems: list[str] = []
    rows: list[dict] = []
    if stage in ("tester", "review_checkpoint"):
        target = report.get("validation") if stage == "review_checkpoint" else report
        if isinstance(target, dict):
            found, recorded = reference_claims(target, claim_context)
            problems += found
            rows += recorded
    if stage in ("review_completion", "review_checkpoint"):
        target = report.get("decision") if stage == "review_checkpoint" else report
        if isinstance(target, dict):
            recorded = exit_code_claims(target, claim_context)
            problems += [row["reason"] for row in recorded if row["status"] == CONTRADICTED]
            rows += recorded
    return problems, rows


# Exit-code statements (#3904) ------------------------------------------------------------------------


def _command_text(receipt: dict) -> str:
    text = receipt.get("command_text")
    if isinstance(text, str):
        return text
    command = receipt.get("command")
    if isinstance(command, list) and all(isinstance(word, str) for word in command):
        return shlex.join(command)
    return str(command or "")


def is_test_run(receipt: dict) -> bool:
    """Whether a receipt records a test run, whose exit code is its runner's rather than the product's."""
    if receipt.get("results") is not None or receipt.get("results_expected"):
        return True
    command = _command_text(receipt)
    return bool(command.strip()) and verification_commands.command_framework(command) is not None


def _find(name: str, claim_context: dict) -> Path | None:
    name = name.removeprefix("./")
    workspace = claim_context.get("workspace") or ""
    folders = [Path(folder) for folder in claim_context.get("evidence_dirs") or []]
    candidates = [Path(workspace) / name] if workspace and "/" in name else []
    candidates += [folder / name for folder in folders] + [folder / Path(name).name for folder in folders]
    return next((path for path in candidates if path.is_file()), None)


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _authenticated(path: Path, receipt: dict, claim_context: dict) -> bool:
    """The same attempt-bound capture check as Validator admission, never a model-written JSON alone."""
    workspace = claim_context.get("workspace") or ""
    attempt = claim_context.get("attempt") or {}
    events = attempt.get("events")
    try:
        if not workspace or not path.resolve().is_relative_to(Path(workspace).resolve() / ".autocode"):
            return False
        rows = event_log.events(events) if events else []
        outputs = [
            row["item"]
            for row in rows
            if row.get("type") == "item.completed"
            and row.get("item", {}).get("type") in ("command_execution", "tool_output")
        ]
        receipt_auth.authenticate(
            receipt,
            path,
            workspace,
            outputs,
            receipt_only=attempt.get("output_mode") == "report_file",
            capture_context=attempt.get("capture_context"),
        )
        if receipt.get("kind") == COMPARISON:
            base_output = Path(receipt["base_output"])
            if not base_output.resolve().is_relative_to(Path(workspace).resolve() / ".autocode"):
                return False
            if util.file_hash(base_output) != receipt["base_output_sha256"]:
                return False
        return True
    except (KeyError, OSError, TypeError, ValueError):
        return False


def exit_code_claims(report: dict, claim_context: dict) -> list[dict]:
    """Rows for each limitation or backlog sentence that states one exit code and cites captures."""
    rows = []
    for field in REPORT_FIELDS:
        entries = report.get(field)
        for index, sentence in enumerate(entries if isinstance(entries, list) else [], 1):
            if not isinstance(sentence, str):
                continue
            codes = {int(match.group(1)) for match in _EXIT.finditer(sentence)}
            cited = list(dict.fromkeys(match.group(1).removeprefix("./") for match in _CITED.finditer(sentence)))
            if len(codes) == 1 and cited:
                rows.append(_exit_row(field, index, sentence, codes.pop(), cited, claim_context))
    return rows


def _exit_row(field, index, sentence, stated, cited, claim_context) -> dict:
    missing, unreadable, unauthenticated, tests, differing, matching = [], [], [], [], [], []
    for name in cited:
        path = _find(name, claim_context)
        receipt = _read(path) if path else None
        if path is None:
            missing.append(name)
        elif receipt is None or (not is_test_run(receipt) and not isinstance(receipt.get("exit_code"), int)):
            unreadable.append(name)
        elif not _authenticated(path, receipt, claim_context):
            unauthenticated.append(name)
        elif is_test_run(receipt):
            tests.append(name)
        elif receipt["exit_code"] == stated:
            matching.append(name)
        else:
            differing.append(f"{name} recorded exit {receipt['exit_code']} (`{_command_text(receipt)}`)")
    row = {"source": field, "id": f"{field}[{index}]", "claim": sentence, "observation": ", ".join(cited)}
    if matching:
        return {**row, "status": ESTABLISHED, "reason": f"{', '.join(matching)} recorded exit {stated}."}
    if differing and not (missing or unreadable or unauthenticated or tests):
        return {
            **row,
            "status": CONTRADICTED,
            "reason": (
                f"{field} entry {sentence!r} states exit {stated}, but every capture it cites recorded another "
                f"exit code: {'; '.join(differing)}. Correct the statement to what the cited capture recorded, "
                "or cite the capture that shows it."
            ),
        }
    reasons = []
    if missing:
        reasons.append(f"no recorded capture was found for {', '.join(missing)}")
    if unreadable:
        reasons.append(f"{', '.join(unreadable)} could not be read as a capture with an exit code")
    if unauthenticated:
        reasons.append(f"{', '.join(unauthenticated)} is not authenticated to this attempt")
    if tests:
        reasons.append(f"{', '.join(tests)} is a test run, whose exit code is its runner's, not the product's")
    if differing:
        reasons.append("; ".join(differing))
    return {
        **row,
        "status": UNVERIFIED,
        "reason": f"{field} entry states exit {stated}, which AutoCode has not observed: {'; '.join(reasons)}.",
    }


# Reference-resolution claims (#3947) --------------------------------------------------------------------


def reference_claim(text: str) -> bool:
    """Whether a criterion claims references resolve: a reference word together with resolve(s/d)."""
    return bool(_REFERENCE.search(text) and _RESOLVE.search(text))


def _comparison_output(command) -> list[str]:
    """The receipt a listed `capture --compare-base ... --output FILE` check wrote, by its own --output."""
    try:
        words = shlex.split(command) if isinstance(command, str) else []
    except ValueError:
        return []
    words = words[: words.index("--")] if "--" in words else words
    if not any(word == "--compare-base" or word.startswith("--compare-base=") for word in words):
        return []
    outputs = [words[i + 1] for i, word in enumerate(words[:-1]) if word == "--output"]
    return outputs + [word.split("=", 1)[1] for word in words if word.startswith("--output=")]


def _cited_observations(refs, checks, claim_context):
    """The comparison receipts and the other observations (single runs) a criterion result cites."""
    comparisons, single = [], []
    workspace = claim_context.get("workspace") or ""
    for ref in refs:
        if not isinstance(ref, str):
            continue
        match = _CHECK.fullmatch(ref.strip())
        listed = (
            [checks[int(match.group(1)) - 1]]
            if match and 0 < int(match.group(1)) <= len(checks)
            else [check for check in checks if check.get("evidence_ref") == ref]
        )
        paths = [check.get("evidence_ref") for check in listed if isinstance(check.get("evidence_ref"), str)]
        paths += [output for check in listed for output in _comparison_output(check.get("command"))]
        paths += [] if listed or match else [ref]
        found = None
        for path in paths:
            file = Path(path) if Path(path).is_absolute() else Path(workspace) / path
            receipt = _read(file) if workspace and not path.startswith("event:") and file.is_file() else None
            if receipt and receipt.get("kind") == COMPARISON and _authenticated(file, receipt, claim_context):
                found = (path, receipt)
        if found:
            comparisons.append(found)
        else:
            single += [str(check.get("command") or ref) for check in listed] or [ref]
    return comparisons, single


def reference_claims(report: dict, claim_context: dict) -> tuple[list[str], list[dict]]:
    """Problems and unverified rows for PASS results of approved criteria that claim references resolve."""
    criteria = claim_context.get("criteria") or {}
    checks = [check for check in report.get("checks") or [] if isinstance(check, dict)]
    base = str(claim_context.get("base_commit") or "")
    problems, rows = [], []
    for result in report.get("criterion_results") or []:
        if not isinstance(result, dict) or result.get("status") != "PASS":
            continue
        cid = str(result.get("id") or "")
        text = criteria.get(cid, "")
        if not reference_claim(text):
            continue
        comparisons, single = _cited_observations(result.get("evidence_refs") or [], checks, claim_context)
        if not comparisons:
            cited = ", ".join(f"`{item}`" for item in dict.fromkeys(single)) or "no observation"
            problems.append(
                f"Criterion {cid} claims references resolve, but its PASS cites only a single run of {cited}: a "
                "single run cannot show what the change added. Run the base-versus-candidate comparison instead, "
                f"`autocode capture --compare-base {base or 'BASE'} --output .autocode/evidence/<name>.json "
                "-- COMMAND` (with the capture command your handoff names), where COMMAND must be a build that "
                f"reports unresolved references, and cite that receipt for {cid}."
            )
            continue
        found = []
        for path, receipt in comparisons:
            command = _command_text(receipt)
            recorded = str(receipt.get("base_revision") or "")
            new = [str(line) for line in receipt.get("new_diagnostics") or []]
            if base and recorded != base:
                found.append(
                    f"Criterion {cid} cites the diagnostics comparison {path} of `{command}` made against base "
                    f"{recorded or 'unknown'}, not the run's base {base}: the comparison was not made against the "
                    "run's base, so it cannot see the defect the change may have added."
                )
            elif new:
                found.append(
                    f"Criterion {cid} claims references resolve, but the diagnostics comparison {path} of "
                    f"`{command}` recorded new diagnostics the change added: " + "; ".join(new)
                )
        if found:
            problems += found
            continue
        path, receipt = comparisons[0]
        command = _command_text(receipt)
        rows.append(
            {
                "source": "criterion_results",
                "id": cid,
                "claim": text,
                "observation": path,
                "status": UNVERIFIED,
                "reason": (
                    f"The diagnostics comparison {path} of `{command}` against the run's base shows no new "
                    "diagnostics, but a comparison with no new diagnostics does not show that the command reports "
                    "unresolved references, so AutoCode has not observed that they resolve."
                ),
            }
        )
    return problems, rows


# Admission --------------------------------------------------------------------------------------------


def record_rows(record: dict, source_stage: str, rows: list[dict]) -> None:
    """Save the unverified rows on a stage record, replacing earlier rows from the same stage."""
    kept = [row for row in record.get("claim_checks") or [] if row.get("stage") != source_stage]
    added = [{**row, "stage": source_stage} for row in rows if row["status"] == UNVERIFIED]
    if kept or added:
        record["claim_checks"] = kept + added


def tester_problems(state: RunState, validation: dict, record: dict) -> list[str]:
    """The Tester rule at admission (changed-code preflight); rows are saved only when nothing is refused."""
    problems, rows = judge("tester", validation, context(state, state.get("workspace"), record=record))
    if not problems:
        record_rows(record, "tester", rows)
    return problems


def admit(state: RunState, stage: str, value: dict, record: dict, workspace, run_dir) -> None:
    """Refuse a contradicted completion statement, record unverified ones, then merge the backlog."""
    if stage == "review_completion":
        problems, rows = judge(stage, value, context(state, workspace, run_dir, record=record))
        if problems:
            raise ValueError("\n\n".join(problems))
        record_rows(record, stage, rows)
    for entry in value.get("deferred_backlog", []):
        if entry not in state.setdefault("deferred_backlog", []):
            state["deferred_backlog"].append(entry)

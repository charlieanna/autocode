"""Which checks a Tester's PASS criteria cite are its own probes (#3937, #3923, #3922, #3902, #3900).

Five live runs delivered a defect just outside the approved examples as "checked": the independent
Tester marked each behavioral criterion PASS from the plan's own approved checks alone, so its
independence added nothing the Builder's tests had not already shown. The Tester prompt now asks,
for each PASS criterion, for at least one probe of its own that varies the input beyond those
examples, or a short ``probe_reason`` when nothing can vary.

This module records what the report shows and decides the one rejection, for the runner
(autopilot.apply_review_result) and ``autocode report-check`` alike. A cited check is an approved
required command when its command, without the runner's workspace wrapper (``cd <dir> && ... 2>&1``)
and a ``sh -c`` wrapper, has the argument list of an approved command (the comparison
autocode_receipts.adopt_command makes); any other cited passing check is the Tester's own probe.

A PASS with neither a probe nor a reason is refused:

- as ``ProbeEvidenceRequired`` (an ``ExecutionEvidenceRequired``) when the report lists no passing
  probe of the Tester's own that no criterion cites: a report-only repair cannot run one, so the
  Tester runs again once (autocode_repair_triage classifies it ``missing_execution``);
- as a plain ``ValueError`` when the report lists such an uncited probe, or when that one rerun was
  already spent for this stage, source and contract (``rerun_used``): the bounded report-only repair
  can cite the listed probe, give a ``probe_reason`` or report the criterion NOT_VERIFIED.

Only a report whose verdict is PASS is refused. Imports nothing from AutoCode but autocode_util.
"""

from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

REFERENCE = re.compile(r"check:(\d+)")
RERUN_KINDS = ("missing_execution", "rerun_missing_execution")
_SHELLS = ("sh", "bash")


class ProbeEvidenceRequired(util.ExecutionEvidenceRequired):
    """A PASS criterion lacks a Tester probe and a probe_reason, and no listed probe could be cited:
    only a fresh Tester execution can run one."""


def command_argv(text) -> list[str] | None:
    """The argument list a check's command runs, without the runner's ``cd <dir> && ... 2>&1``
    wrapper or a ``sh -c '<script>'`` wrapper; None when it cannot be split."""
    if not isinstance(text, str):
        return None
    try:
        words = shlex.split(text)
    except ValueError:
        return None
    if len(words) >= 4 and words[0] == "cd" and words[2] == "&&":
        words = words[3:]
        if words and words[-1] == "2>&1":
            words = words[:-1]
    if len(words) == 3 and PurePosixPath(words[0]).name in _SHELLS and words[1] in ("-c", "-lc"):
        return command_argv(words[2])
    return words or None


def is_approved(command, approved) -> bool:
    """True when ``command`` runs one of the ``approved`` commands: the same argument list, or the
    same text when either side cannot be split."""
    argv = command_argv(command)
    for each in approved or ():
        other = command_argv(each)
        if argv is not None and other is not None:
            if argv == other:
                return True
        elif isinstance(command, str) and isinstance(each, str) and command.strip() == each.strip():
            return True
    return False


def _checks(report) -> list:
    raw = report.get("checks") if isinstance(report, dict) else None
    return raw if isinstance(raw, list) else []


def _rows(report) -> list[dict]:
    raw = report.get("criterion_results") if isinstance(report, dict) else None
    return [row for row in raw or [] if isinstance(row, dict)] if isinstance(raw, list) else []


def _passing(check) -> bool:
    """A listed check that counts as run: exit 0, or not yet bound (report-check before the runner binds it)."""
    return isinstance(check, dict) and check.get("exit_code") in (0, None)


def _cited_positions(row, checks) -> list[int]:
    """The 1-based positions of the listed checks a criterion cites, as check:<n> or by the check's own
    evidence reference (what ``check_refs.resolve`` swaps check:<n> for)."""
    found: list[int] = []
    for ref in row.get("evidence_refs") or []:
        if not isinstance(ref, str):
            continue
        match = REFERENCE.fullmatch(ref.strip())
        if match:
            positions = [int(match.group(1))] if 1 <= int(match.group(1)) <= len(checks) else []
        elif ref.strip() and ref != "event:":
            positions = [
                n for n, check in enumerate(checks, 1) if isinstance(check, dict) and check.get("evidence_ref") == ref
            ]
        else:
            positions = []
        found += [n for n in positions if n not in found]
    return found


def _own(check, approved) -> bool:
    """A passing listed check whose command is known and is not an approved required command."""
    return _passing(check) and isinstance(check.get("command"), str) and not is_approved(check["command"], approved)


def classify(report, approved) -> dict[str, dict]:
    """For each PASS criterion, the cited passing checks that are the Tester's own and those that are
    approved required commands (by command), and its stripped probe_reason."""
    checks = _checks(report)
    result: dict[str, dict] = {}
    for row in _rows(report):
        if row.get("status") != "PASS" or not isinstance(row.get("id"), str):
            continue
        own: list[str] = []
        known: list[str] = []
        for position in _cited_positions(row, checks):
            check = checks[position - 1]
            if not _passing(check) or not isinstance(check.get("command"), str):
                continue
            target = own if _own(check, approved) else known
            if check["command"] not in target:
                target.append(check["command"])
        reason = row.get("probe_reason")
        result[row["id"]] = {
            "tester_checks": own,
            "approved_checks": known,
            "reason": reason.strip() if isinstance(reason, str) else "",
        }
    return result


def missing(classified: dict[str, dict]) -> list[str]:
    """The PASS criteria with neither a Tester probe nor a nonblank probe_reason, in report order."""
    return [cid for cid, row in classified.items() if not row["tester_checks"] and not row["reason"]]


def uncited_own(report, approved) -> list[int]:
    """Positions of passing checks of the Tester's own that no criterion cites."""
    checks = _checks(report)
    cited = {n for row in _rows(report) for n in _cited_positions(row, checks)}
    return [n for n, check in enumerate(checks, 1) if n not in cited and _own(check, approved)]


def message(ids, uncited=()) -> str:
    """The one rejection text the runner and report-check give. It never says "exit " or "failed check",
    which autocode_repair_triage reads as a failing test."""
    text = (
        f"Tester PASS without a probe: {', '.join(ids)}. Each cites only approved required commands with neither "
        "a Tester probe nor a probe_reason. For each behavioral criterion you mark PASS, run at least one probe of "
        "your own beyond the approved checks, varying its input along an axis the approved examples held fixed, "
        "list it as a check and cite it as check:<n>. Where the criterion is process, documentation or "
        "human-review only, or no input can vary, give a short probe_reason instead; otherwise report the "
        "criterion NOT_VERIFIED."
    )
    if uncited:
        named = ", ".join(f"check:{n}" for n in uncited)
        text += (
            f" The report already lists {named}, a passing check of the Tester's own that no criterion cites: "
            "cite it in the criterion it probes."
        )
    return text


def rerun_used(history, stage, revision, contract_hash) -> bool:
    """Whether the one fresh execution of ``stage`` for a missing execution was already requested for this
    source revision and contract (``resolve_repair_history``)."""
    return any(
        isinstance(entry, dict)
        and entry.get("kind") in RERUN_KINDS
        and entry.get("stage") == stage
        and entry.get("source_revision") == revision
        and entry.get("contract_hash") == contract_hash
        for entry in history or ()
    )


def admit(report, approved, *, rerun_used: bool = False) -> dict[str, dict]:
    """The per-criterion probe record of a Tester report, or the rejection of a PASS report whose PASS
    criteria include one with neither a Tester probe nor a probe_reason."""
    classified = classify(report, approved)
    if not isinstance(report, dict) or report.get("verdict") != "PASS":
        return classified
    ids = missing(classified)
    if not ids:
        return classified
    uncited = uncited_own(report, approved)
    if uncited or rerun_used:
        raise ValueError(message(ids, uncited))
    raise ProbeEvidenceRequired(message(ids))

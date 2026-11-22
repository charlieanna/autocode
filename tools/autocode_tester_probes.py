"""Which checks a Tester's PASS criteria cite are its own probes (#3937, #3923, #3922, #3902, #3900).

Five live runs delivered a defect just outside the approved examples as "checked": the independent
Tester marked each behavioral criterion PASS from the plan's own approved checks alone, so its
independence added nothing the Builder's tests had not already shown. The Tester prompt now asks,
for each PASS criterion, for at least one probe of its own that varies the input beyond those
examples, or a short ``probe_reason`` when nothing can vary.

This module records what the report shows and decides the one rejection, for the runner
(autopilot.apply_review_result) and ``autocode report-check`` alike. A cited check is an approved
required command when what it runs, without the runner's workspace wrapper (``cd <dir> && ... 2>&1``),
a ``sh -c`` wrapper, environment and ``timeout`` prefixes, redirections and output filters, is an
approved command's program with that command's inputs or a part of them: a display option (``-q``,
``--verbose``) or one test of an approved test file is not a probe (#3979). Any other cited passing
check is the Tester's own probe.

A PASS with neither a probe nor a reason is refused:

- as ``ProbeEvidenceRequired`` (an ``ExecutionEvidenceRequired``) when the report lists no passing
  probe of the Tester's own that no criterion cites: a report-only repair cannot run one, so the
  Tester runs again once (autocode_repair_triage classifies it ``missing_execution``);
- as a plain ``ValueError`` when the report lists such an uncited probe, or when that one rerun was
  already spent for this stage, source and contract (``rerun_used``): the bounded report-only repair
  can cite the listed probe, give a ``probe_reason`` or report the criterion NOT_VERIFIED.

A ``probe_reason`` stands only when it names, by path, a file or directory that exists in the workspace
(``README.md names the --strict flag``): a bare "anything" names nothing AutoCode can check (#3979). A PASS
whose only support is such a reason is refused as a plain ``ValueError``, since a report-only repair can
name the file. The gate applies to both Tester routes: the ``tester`` stage and the Tester half of the
combined ``review_checkpoint`` (its ``validation``).

Only a report whose verdict is PASS is refused. Imports nothing from AutoCode but autocode_util.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path, PurePosixPath

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


def default_reason(value, schema):
    """A Tester report whose schema offers ``probe_reason`` with each omitted one set to None; for the
    combined ``review_checkpoint`` report, its ``validation``.

    Strict generation schemas require every property, so the field is nullable there; reports written
    before it, and scripted Testers that leave it out, omit it. Supplied values are kept."""
    if not isinstance(schema, dict) or not isinstance(value, dict):
        return value
    nested = (schema.get("properties") or {}).get("validation")
    if isinstance(nested, dict) and isinstance(value.get("validation"), dict) and "criterion_results" not in value:
        return {**value, "validation": default_reason(value["validation"], nested)}
    rows = ((schema.get("properties") or {}).get("criterion_results") or {}).get("items", {}).get("properties", {})
    if "probe_reason" not in (rows or {}):
        return value
    if not isinstance(value.get("criterion_results"), list):
        return value
    result = dict(value)
    result["criterion_results"] = [
        {"probe_reason": None, **row} if isinstance(row, dict) else row for row in value["criterion_results"]
    ]
    return result


_SEPARATORS = frozenset({";", "&&", "||", "|", "&", "\n"})
# What a shell line runs a command through without changing what it runs.
_PREFIXES = {"env": 0, "timeout": 1, "time": 0, "nice": 0, "nohup": 0, "command": 0, "exec": 0, "stdbuf": 0}
# Commands that only show, keep or drop another command's output, in a pipeline or a list.
_PREFIX_VALUES = frozenset({"-n", "-s", "-k", "-u", "--signal", "--kill-after", "--unset"})
_FILTERS = frozenset(
    {"head", "tail", "cat", "tee", "grep", "egrep", "fgrep", "wc", "sort", "uniq", "cut", "less", "more"}
)
_NOTES = frozenset({"cd", "pushd", "echo", "printf", "true", ":", "set", "export"})
# Runner options that take a separate value and change only how results are shown (#3979).
_SHOWN_WITH_VALUE = frozenset(
    {"--tb", "--color", "--colour", "--reporter", "--format", "--durations", "--verbosity", "--log-level"}
)
_PATHLIKE = re.compile(r"/?(?:[\w.@+-]+/)*[\w@+-][\w.@+-]*(?:/|\.[A-Za-z0-9]+)|(?:[\w.@+-]+/)+[\w@+-][\w.@+-]*")
_VERSION = re.compile(r"^([A-Za-z][A-Za-z_+-]*?)[0-9][0-9.]*$")


def _words(text) -> list[str] | None:
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None


def _segments(words) -> list[tuple[str | None, list[str]]]:
    """Each simple command of a shell line with the operator before it; redirections removed."""
    result: list[tuple[str | None, list[str]]] = []
    current: list[str] = []
    before: str | None = None
    skip = False
    for word in words:
        if skip:
            skip = False
            continue
        if word in _SEPARATORS:
            result.append((before, current))
            before, current = word, []
        elif word and set(word) <= set("<>&"):
            if current and current[-1].isdigit():
                current.pop()  # the descriptor of 2>&1
            skip = True  # the redirection's target
        else:
            current.append(word)
    result.append((before, current))
    return [(op, argv) for op, argv in result if argv]


def _unwrap(argv) -> list[str]:
    """The command a ``VAR=value``, ``env``, ``timeout N`` or similar prefix runs."""
    while argv:
        if "=" in argv[0] and not argv[0].startswith(("-", "=")) and argv[0].split("=", 1)[0].isidentifier():
            argv = argv[1:]
            continue
        name = PurePosixPath(argv[0]).name
        if name not in _PREFIXES:
            return argv
        argv = argv[1:]
        while argv and argv[0].startswith("-"):
            argv = argv[2:] if argv[0] in _PREFIX_VALUES else argv[1:]
        argv = argv[_PREFIXES[name] :]
    return argv


def commands(text, _depth=0) -> list[list[str]] | None:
    """The commands a check's shell line runs, each as its argument list, without the runner's
    ``cd <dir> && ... 2>&1`` wrapper, a ``sh -c '<script>'`` wrapper, environment and ``timeout`` prefixes,
    redirections, ``echo`` notes and output filters (``| tail``); None when it cannot be split."""
    if not isinstance(text, str) or _depth > 4:
        return None
    words = _words(text)
    if words is None:
        return None
    found: list[list[str]] = []
    for operator, argv in _segments(words):
        argv = _unwrap(argv)
        if not argv:
            continue
        name = PurePosixPath(argv[0]).name
        if len(argv) == 3 and name in _SHELLS + ("zsh",) and argv[1] in ("-c", "-lc", "-ec", "-ic"):
            inner = commands(argv[2], _depth + 1)
            if inner is None:
                return None
            found += inner
        elif name in _NOTES or (operator == "|" and name in _FILTERS):
            continue
        else:
            found.append(argv)
    return found


def _program(word) -> str:
    """``python3.13``, ``/usr/bin/python3`` and ``.venv/bin/python`` all run ``python``."""
    name = PurePosixPath(word).name
    match = _VERSION.match(name)
    return match.group(1) if match else name


def _selection(argv) -> list[str]:
    """What a command selects to run: its program and positional words, without options, which change how a
    runner shows or stops (``-q``, ``--verbose``, ``--tb=short``), not the inputs it runs (#3979)."""
    words = [_program(argv[0])]
    skip = False
    for word in argv[1:]:
        if skip:
            skip = False
        elif word == "--":
            continue
        elif word.startswith("-"):
            skip = word in _SHOWN_WITH_VALUE
        else:
            words.append(word)
    return words


def _narrows(word, other) -> bool:
    """``word`` names part of what ``other`` names: a test inside an approved test file or dotted module."""
    if word == other:
        return True
    if word.startswith(other + "::"):
        return True
    return "." in other and not other.startswith(".") and word.startswith(other + ".")


def _covered(argv, approved_argv) -> bool:
    mine, theirs = _selection(argv), _selection(approved_argv)
    if mine[0] != theirs[0]:
        return False
    return all(any(_narrows(word, other) for other in theirs[1:]) for word in mine[1:])


def is_approved(command, approved) -> bool:
    """True when ``command`` runs only approved commands, or parts of them: the same program and the same
    selection of inputs, whatever wrapper, output filter or display option it adds; the same text when
    either side cannot be split."""
    mine = commands(command)
    units = []
    for each in approved or ():
        theirs = commands(each)
        if mine is None or theirs is None:
            if isinstance(command, str) and isinstance(each, str) and command.strip() == each.strip():
                return True
            continue
        units += theirs
    if not mine:
        return False
    return all(any(_covered(argv, unit) for unit in units) for argv in mine)


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


def reason_paths(reason, workspace) -> list[str]:
    """The workspace files and directories a probe_reason names by path (``README.md``, ``docs/cli.md``).

    A reason stands only when it names one: what keeps the input from varying is a file AutoCode can find,
    not a claim it cannot check (#3979). Nothing outside ``workspace`` counts, and no path counts without one."""
    if not isinstance(reason, str) or workspace is None:
        return []
    root = Path(workspace).resolve()
    found: list[str] = []
    for word in reason.split():
        name = word.strip("`'\"()[]{}<>,;:!?").rstrip(".")
        if not name or name in found or not _PATHLIKE.fullmatch(name):
            continue
        try:
            target = (root / name).resolve()
            target.relative_to(root)
        except (OSError, ValueError):
            continue
        if target != root and target.exists():
            found.append(name)
    return found


def classify(report, approved, workspace=None) -> dict[str, dict]:
    """For each PASS criterion, the cited passing checks that are the Tester's own and those that are
    approved required commands (by command), its stripped probe_reason and the workspace paths that reason
    names."""
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
        reason = reason.strip() if isinstance(reason, str) else ""
        result[row["id"]] = {"tester_checks": own, "approved_checks": known, "reason": reason}
        if reason:
            result[row["id"]]["reason_paths"] = reason_paths(reason, workspace)
    return result


def missing(classified: dict[str, dict]) -> list[str]:
    """The PASS criteria with neither a Tester probe nor a nonblank probe_reason, in report order."""
    return [cid for cid, row in classified.items() if not row["tester_checks"] and not row["reason"]]


def ungrounded(classified: dict[str, dict]) -> list[str]:
    """The PASS criteria whose only support is a probe_reason that names no workspace path, in report order."""
    return [
        cid
        for cid, row in classified.items()
        if not row["tester_checks"] and row["reason"] and not row.get("reason_paths")
    ]


def uncited_own(report, approved) -> list[int]:
    """Positions of passing checks of the Tester's own that no criterion cites."""
    checks = _checks(report)
    cited = {n for row in _rows(report) for n in _cited_positions(row, checks)}
    return [n for n, check in enumerate(checks, 1) if n not in cited and _own(check, approved)]


def message(ids, uncited=(), unnamed=()) -> str:
    """The one rejection text the runner and report-check give. It never says "exit " or "failed check",
    which autocode_repair_triage reads as a failing test."""
    parts = []
    if ids:
        parts.append(
            f"Tester PASS without a probe: {', '.join(ids)}. Each cites only approved required commands with neither "
            "a Tester probe nor a probe_reason. For each behavioral criterion you mark PASS, run at least one probe of "
            "your own beyond the approved checks, varying its input along an axis the approved examples held fixed, "
            "list it as a check and cite it as check:<n>. Rerunning an approved command with other display options "
            "or wrappers, or one of its tests, is not a probe. Where the criterion is process, documentation or "
            "human-review only, or no input can vary, give a short probe_reason instead; otherwise report the "
            "criterion NOT_VERIFIED."
        )
    if unnamed:
        parts.append(
            f"Tester PASS on a probe_reason that names no file: {', '.join(unnamed)}. A probe_reason stands only "
            "when it names, by its path in the workspace, the file or document whose fixed content leaves no input "
            "to vary (for example README.md); run a probe of your own and cite it, name that path, or report the "
            "criterion NOT_VERIFIED."
        )
    text = " ".join(parts)
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


def admit(report, approved, *, rerun_used: bool = False, workspace=None) -> dict[str, dict]:
    """The per-criterion probe record of a Tester report, or the rejection of a PASS report whose PASS
    criteria include one with neither a Tester probe nor a probe_reason that names a ``workspace`` path."""
    classified = classify(report, approved, workspace)
    if not isinstance(report, dict) or report.get("verdict") != "PASS":
        return classified
    ids, unnamed = missing(classified), ungrounded(classified)
    if not ids and not unnamed:
        return classified
    uncited = uncited_own(report, approved)
    if uncited or rerun_used or not ids:
        # A listed probe can be cited, and a reason can name its file, by the bounded report-only repair.
        raise ValueError(message(ids, uncited, unnamed))
    raise ProbeEvidenceRequired(message(ids, (), unnamed))

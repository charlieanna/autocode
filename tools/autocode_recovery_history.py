"""What went wrong in a run and what AutoCode did about it: the status view's ``recoveries``.

A run recovers on its own. A rejected answer is recorded with its reason, a stage
whose report fails its checks rewrites the report, the changed-code gate sends the
Builder back for lines without a test, a stage that stops converging goes to the
Investigator, and the Resolver settles a failure its rules cover. The saved run
keeps each of these in its own record; this projects them, oldest first, as plain
sentences a client shows as they are, so a person can see which stage went wrong,
why, and what happened next without opening the run's files.

Pure function over the run state: no file access, no decision, nothing a client can
act on. Text is redacted and bounded, and a sentence that names an internal command
is left out, because a client never shows a person a command to type.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_python_test_roles as test_roles
    from . import autocode_roles as roles
    from . import autocode_util as util
except ImportError:
    import autocode_python_test_roles as test_roles
    import autocode_roles as roles
    import autocode_util as util

LIMIT = 20  # the newest entries a view carries
TEXT = 400  # characters per sentence field
DETAIL = 900  # characters of an Investigator's diagnosis
_SCHEMA_SUMMARY = "The answer was not in the format AutoCode requires"
# "Completed terra output was rejected (WHY); attempt archived. ..." keeps only WHY.
_REJECTED = re.compile(r"^Completed \w+ output was rejected \((?P<why>.+)\)(?:;|\.|$)", re.S)
# autocode_requirement_quotes.unsupported: requirements whose quote is not the person's own words.
_UNSOURCED = re.compile(r"^Requirements? (?P<ids>[^:]+?):? source_quote is not in the task or a saved user event")
_UNSOURCED_TEXT = "it listed {ids} as things you asked for, but you never wrote the words it quoted for them."
# autocode_report_edits: a repair's edit to the rejected report that could not be applied.
_REPORT_EDIT = re.compile(r"^Report edits?\b")
_REPORT_EDIT_TEXT = "its correction to the earlier answer was not in a form AutoCode can apply."
# A sentence that tells someone to run something is machinery, not an explanation.
_COMMAND = re.compile(r"\bautocode\s+[a-z-]+|(?:^|\s)--[a-z]")
_PARENTHESIS = re.compile(r"\([^()]*\)")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_LAST_CAUSE = re.compile(r"\bLast cause:\s*(?P<cause>.*?)(?:\.\s|$)")
# Schema validation errors: "$: missing x", "$: unexpected fields: a, b", "$.path: reason".
_SCHEMA_MISSING = re.compile(r"\bmissing\b")
_SCHEMA_UNEXPECTED = re.compile(r"unexpected fields:\s*([^;]+)")
_SCHEMA_MORE = re.compile(r"\band\s+(\d+)\s+more\b")
# autocode_resolver.ACTIONS, as a person reads them.
_RESOLVER_ACTIONS = {
    "continue": "carry on",
    "retry": "try the stage again",
    "escalate": "stop the stage",
    "replan": "revise the plan",
    "hold": "stop here",
}


_RETRIED = "again itself"  # autocode_rejection_retry: AutoCode ran the stage again itself


def project(state: RunState) -> list[dict[str, Any]]:
    """Every recorded recovery, oldest first, the newest ``LIMIT`` of them."""
    investigations = list(_investigations(state))
    explained = {(row["job"], row["problem"]) for row in investigations}
    entries = [
        # A rejection AutoCode retried itself after the investigation stays: that retry is not in the investigation's entry.
        *(
            row
            for row in _rejections(state)
            if (row["job"], row["problem"]) not in explained or _RETRIED in row["action"]
        ),
        *_report_repairs(state),
        *_test_role_corrections(state),
        *_sent_back(state),
        *investigations,
        *_resolver(state),
    ]
    entries.sort(key=lambda row: (row["at"] or "", row["id"]))
    return entries[-LIMIT:]


def _entry(
    kind: str,
    at: object,
    stage: str | None,
    state: RunState,
    *,
    headline: str,
    problem: str | None = None,
    action: str,
    by: str | None = None,
    detail: str | None = None,
    handled: bool = False,
) -> dict[str, Any]:
    job = roles.screen_name(stage, state) if stage else None
    body = {
        "kind": kind,
        "at": at if isinstance(at, str) else None,
        "job": job,
        "headline": headline,
        "problem": _plain(problem),
        "action": action,
        "by": by or job,
        "detail": _plain(detail, DETAIL),
        "handled": handled,
    }
    identity = "\0".join(str(body[key] or "") for key in ("kind", "at", "job", "headline"))
    return {"id": hashlib.sha256(identity.encode()).hexdigest()[:16], **body}


def _summarize_schema(text: str) -> str | None:
    """A person-readable count of what the schema rejected, never the raw ``$:`` clauses."""
    items = [item.strip() for item in text.split(";") if item.strip()]
    missing = 0
    unexpected = 0
    other = 0
    for item in items:
        more = _SCHEMA_MORE.search(item)
        if more and not _SCHEMA_MISSING.search(item) and not _SCHEMA_UNEXPECTED.search(item):
            other += int(more.group(1))
        elif _SCHEMA_MISSING.search(item):
            missing += 1
        else:
            fields = _SCHEMA_UNEXPECTED.search(item)
            if fields:
                unexpected += len([f for f in fields.group(1).split(",") if f.strip()])
            else:
                other += 1
    if not (missing or unexpected or other):
        return None
    parts = []
    if missing:
        parts.append(f"{missing} required part{'s' if missing != 1 else ''} missing")
    if unexpected:
        parts.append(f"{unexpected} part{'s' if unexpected != 1 else ''} it does not take")
    if other:
        parts.append(f"{other} other mismatch{'es' if other != 1 else ''}")
    return f"{_SCHEMA_SUMMARY}: {', '.join(parts)}."


def _is_schema_rejection(text: str) -> bool:
    return "$:" in text or "$." in text


def _plain(text, limit=TEXT):
    """Redacted, bounded prose without a sentence that names an internal command."""
    if not isinstance(text, str) or not text.strip():
        return None
    text = " ".join(text.split())
    text = _PARENTHESIS.sub(lambda match: "" if _COMMAND.search(match.group()) else match.group(), text)
    text = re.sub(r"\s+([.!?])", r"\1", text)
    match = _REJECTED.match(text)
    if match:
        text = match["why"]
    unsourced = _UNSOURCED.match(text)
    if unsourced:
        return _UNSOURCED_TEXT.format(ids=unsourced["ids"].strip())
    if _REPORT_EDIT.match(text):
        return _REPORT_EDIT_TEXT
    if _is_schema_rejection(text):
        summary = _summarize_schema(text)
        if summary:
            return summary
    kept = [sentence for sentence in _SENTENCE.split(text) if not _COMMAND.search(sentence)]
    text = util.redact(" ".join(kept).strip())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def explain(text) -> str | None:
    """Why an answer was not accepted, in plain words a person can read; None when unknown.

    The checks' own reasons are written for the stage that repairs them: "Requirements R3, R4:
    source_quote is not in the task or a saved user event. R3 quotes ...". The chat said only
    "answer rejected" and "needs correction" (2026-11-11), so a person waiting twelve minutes on
    Requirements could not tell what went wrong.
    """
    return _plain(text)


def _times(count):
    return "" if not isinstance(count, int) or count < 2 else f" ({count} times)"


def _rejections(state: RunState):
    """A stage's answer that AutoCode would not accept, with the reason it gave."""
    for row in (state.get("failure_history") or {}).values():
        if not isinstance(row, dict):
            continue
        stage = (row.get("identity") or {}).get("stage")
        problem = _plain(row.get("last_error"))
        if not stage or not problem:
            continue
        job = roles.screen_name(stage, state)
        retried = [item for item in row.get("automatic_retries") or [] if isinstance(item, dict)]
        last = retried[-1] if retried else {}
        action = "AutoCode did not accept it."
        if isinstance(last.get("attempt"), int) and isinstance(last.get("limit"), int):
            # autocode_rejection_retry: AutoCode ran the stage again itself, within its limit.
            action = (
                f"AutoCode did not accept it and ran the {job} {_RETRIED} "
                f"(attempt {last['attempt']} of at most {last['limit']} at this source)."
            )
        yield _entry(
            "rejected",
            row.get("last_seen"),
            stage,
            state,
            headline=f"{job} answer rejected{_times(row.get('count'))}",
            problem=problem,
            action=action,
        )


def _report_repairs(state: RunState):
    """A stage that finished its work but whose report failed its checks, and rewrote it."""
    for row in state.get("report_repair_history") or []:
        if not isinstance(row, dict):
            continue
        repair = row.get("repair") or {}
        stage = repair.get("original_stage") or repair.get("stage")
        if not stage:
            continue
        job = roles.screen_name(stage, state)
        tries = row.get("attempts")
        took = f" It took {tries} tries." if isinstance(tries, int) and tries > 1 else ""
        done = (
            "AutoCode accepted the rewritten report."
            if row.get("result") == "accepted"
            else "The rewritten report was not accepted."
        )
        yield _entry(
            "report_repaired",
            row.get("at"),
            stage,
            state,
            headline=f"{job} rewrote its report",
            problem=None,
            action=f"Its work stood; only the report was rewritten.{took} {done}",
            handled=row.get("result") == "accepted",
        )
    for row in state.get("report_repair_archive") or []:
        if not isinstance(row, dict) or row.get("kind") != "verification_inputs_changed":
            continue
        original = (row.get("repair") or {}).get("original") or {}
        yield _entry(
            "report_repair_retired",
            row.get("at"),
            original.get("stage"),
            state,
            headline="Obsolete report repair retired",
            problem=row.get("reason"),
            action="The rejected report remains archived. Fresh checks and an independent Tester attempt are required.",
            by="AutoCode",
        )


def _lines(obligations):
    """``path lines 48, 56-62, 77`` for each file, from the gate's exact changed lines."""
    by_path: dict[str, list[int]] = {}
    for row in obligations or []:
        if isinstance(row, dict) and isinstance(row.get("line"), int) and isinstance(row.get("path"), str):
            by_path.setdefault(row["path"], []).append(row["line"])
    parts = []
    for path, numbers in by_path.items():
        spans: list[list[int]] = []
        for number in sorted(set(numbers)):
            if spans and number == spans[-1][1] + 1:
                spans[-1][1] = number
            else:
                spans.append([number, number])
        text = ", ".join(str(first) if first == last else f"{first}-{last}" for first, last in spans)
        parts.append(f"{path} line{'s' if len(numbers) > 1 else ''} {text}")
    return "; ".join(parts)


def _sent_back(state: RunState):
    """The changed-code gate returning the Builder for lines no test shows to matter."""
    for row in state.get("regression_proofs") or []:
        if not isinstance(row, dict) or row.get("kind") != "changed_code_rework":
            continue
        assessment = row.get("assessment") or {}
        where = _lines(assessment.get("obligations"))
        problem = (
            f"Changed lines have no test that fails when they break, and no checked reason: {where}."
            if where
            else assessment.get("reason")
        )
        investigation = row.get("investigation") or {}
        if row.get("action") == "reviewer":
            yield _entry(
                "sent_back",
                row.get("at"),
                "review_completion",
                state,
                headline="Test repair needs reassignment",
                problem=row.get("scope_reason") or problem,
                action="AutoCode requested review and reassignment within the approved plan before another Builder attempt.",
            )
            continue
        if row.get("action") == "investigator":
            action, by = (
                "The Builder had already been sent back twice, so AutoCode called the Investigator.",
                "Investigator",
            )
        else:
            action, by = "AutoCode sent the Builder back to test these lines or give a reason.", "Builder"
        # The round the record names, out of the rounds the gate allows (autocode_changed_code_rework);
        # a record saved before rounds were recorded has none.
        number, limit = row.get("round"), row.get("round_limit")
        rounds = (
            f" (round {number} of {limit})"
            if row.get("action") == "builder" and type(number) is int and type(limit) is int and 0 < number <= limit
            else ""
        )
        yield _entry(
            "sent_back",
            row.get("at"),
            "builder",
            state,
            headline="Builder sent back for untested lines" + rounds,
            problem=problem,
            action=action,
            by=by,
            detail=investigation.get("guidance"),
        )


def _investigations(state: RunState):
    """A stage that stopped converging, handed to the Investigator, and what it found."""
    for row in state.get("stuck_investigations") or []:
        if not isinstance(row, dict) or not row.get("stage"):
            continue
        job = roles.screen_name(row["stage"], state)
        finished = bool(row.get("finished_at"))
        if not finished:
            action = "AutoCode called the Investigator to find out why."
        elif row.get("outcome") == "retried":
            action = (
                f"AutoCode called the Investigator, which found the cause, and the {job} tried again with its advice."
            )
        else:
            action = "AutoCode called the Investigator to find out why."
        yield _entry(
            "investigated",
            row.get("requested_at") or row.get("finished_at"),
            row["stage"],
            state,
            headline=f"{job} stuck",
            problem=_plain(row.get("reason")),
            action=action,
            by="Investigator",
            detail=" ".join(part for part in (row.get("diagnosis"), row.get("guidance")) if isinstance(part, str)),
        )


def _resolver(state: RunState):
    """The Resolver's next-step decision on a failure its rules cover; a decision is not a resolution."""
    for row in state.get("history") or []:
        if not isinstance(row, dict) or row.get("stage") not in ("resolver", "resolve"):
            continue
        decision = row.get("decision") or {}
        if not isinstance(decision, dict) or not decision.get("action"):
            continue
        choice = _RESOLVER_ACTIONS.get(decision["action"], str(decision["action"]))
        reason = _plain(decision.get("rationale"))
        # The runner's own policy (autocode_resolver_runtime, engine "runner") decides without a model; a
        # person reading "the Resolver chose" otherwise takes it for a model's judgement (2026-11-05 live run).
        who = (
            "The Resolver's rules, with no model call, chose" if row.get("engine") == "runner" else "The Resolver chose"
        )
        receipt = row.get("receipt")
        evidence = receipt.get("evidence") if isinstance(receipt, dict) else None
        evidence = evidence if isinstance(evidence, dict) else {}
        cause = next(
            (
                evidence.get(key)
                for key in ("timeout_reason", "capacity_error")
                if isinstance(evidence.get(key), str) and evidence[key].strip()
            ),
            None,
        )
        if not cause and isinstance(decision.get("rationale"), str):
            last_cause = _LAST_CAUSE.search(decision["rationale"])
            cause = last_cause["cause"] if last_cause else decision["rationale"]
        yield _entry(
            "resolver_decision",
            row.get("finished_at"),
            row["stage"],
            state,
            headline=f"Resolver: {choice}",
            problem=cause,
            action=f"{who} to {choice}" + (f": {reason}" if reason else "."),
            by="Resolver",
        )


def _test_role_corrections(state: RunState):
    for event in state.get("user_events") or []:
        if isinstance(event, dict) and event.get("kind") == "protected_tests_roles_corrected":
            summary = test_roles.correction_summary(event)
            yield _entry(
                "protected_tests_roles_corrected",
                summary["at"],
                None,
                state,
                headline="Original test inventory corrected",
                problem="A filename hint had included runtime product code as a protected test.",
                action="AutoCode retained the original tests and command, excluded proven product entries, and retired the old proof. Fresh verification remains required.",
                by="AutoCode",
                detail=summary["reason"],
            )

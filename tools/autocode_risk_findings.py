"""A failed runner lifecycle observation becomes a runner-owned blocking finding (#451).

A Tester can honestly report PASS while the product fails a source-declared process-recovery
promise that only the runner's own lifecycle replay (autocode_risk_evidence) observes. That is a
product defect, not a malformed report: a report repair cannot fix it, and before this module the
run stopped with the observation in a receipt the Builder never saw. Instead, the controller keeps
the Tester's report and calls ``reconcile`` with the runner's replay: each failed observation opens
(or refreshes) one blocking finding with source ``runner`` in the findings ledger, naming the
violated promise, what the fixed protocol does and the pinned receipts. The ordinary route then
carries it: the completion gate refuses while it is open, the Completion Reviewer and Resolver see
it in ``open_findings``, and a correction task assigns it to the Builder (``actionable_findings``).

Only this module writes or closes ``runner`` findings, and only from the runner's own replay: a
later replay that passes that observation on a source it has not failed on closes it. A race can be
missed, so a PASS on a source listed in the row's ``failed_revisions`` closes nothing. An approved
contract that no longer holds the observation retracts it. No model report can resolve or retract
one (autocode_findings applies a reviewer's dispositions to its own source).
Imports nothing above the findings ledger; the caller passes the replay result it just verified.
State: findings_ledger rows with source ``runner``, an ``observation_hash`` and ``failed_revisions``
(every source revision the observation failed on; read only here); appends the opened rows to
``unresolved_findings`` when the validation they refute is the saved one.
"""
from __future__ import annotations

try:
    from . import autocode_findings as findings_ledger, autocode_util as util
    from . import autocode_risk_protocols as protocols
except ImportError:
    import autocode_findings as findings_ledger
    import autocode_util as util
    import autocode_risk_protocols as protocols

SOURCE = "runner"
FAIL, PASS = "FAIL", "PASS"


def _observations(state):
    body = (state.get("goal_contract") or {}).get("body") or {}
    manifest = (body.get("risk_acceptance") or {}).get("manifest") or {}
    return {row.get("hash"): row for row in manifest.get("observations") or [] if isinstance(row, dict)}


def open_rows(state):
    return [row for row in findings_ledger.open_entries(state, SOURCE) if row.get("observation_hash")]


def _text(observation, check, revision):
    declaration = observation.get("declaration") or {}
    target = observation.get("target") or {}
    name = f"{target.get('module', '?')}.{target.get('class_name', '?')}"
    quote = " ".join(str(declaration.get("source_quote") or "").split())
    return (f"The runner's lifecycle observation of {name} failed on source {str(revision)[:12]}: "
            f"{str(check.get('error') or 'the observation did not pass')[:600]}. "
            f"It checks the brief's lifecycle promises for criteria {', '.join(observation.get('criterion_ids') or [])}"
            + (f" (\"{quote[:400]}{'...' if len(quote) > 400 else ''}\")" if quote else "")
            + ". " + protocols.describe(observation))


def reconcile(state, replay, record, *, saved=True):
    """Open, refresh or close runner findings from one verified replay; returns the opened/refreshed rows.

    ``replay`` is validation["check_replay"] as check_replay returned it (with keep_lifecycle_failure).
    An observation the replay did not run leaves its finding unchanged; one the approved contract no
    longer holds is retracted. ``saved`` says the validation holding this replay is the run's current
    one, whose findings the Builder acts on.
    """
    result = (replay or {}).get("risk_acceptance") if isinstance(replay, dict) else None
    result = result if isinstance(result, dict) and result.get("verdict") in (PASS, FAIL) else None
    body = (state.get("goal_contract") or {}).get("body")
    known = _observations(state)
    at, reported = util.now(), []
    for row in open_rows(state):
        if isinstance(body, dict) and body and row["observation_hash"] not in known:
            # An approved amendment replaced or removed the observation, with or without a lifecycle
            # replay now; completion still needs every current one to pass (autocode_risk_evidence.ready).
            row.update(status="retracted", resolved_at=at, resolved_in=(result or {}).get("summary"),
                       resolution_evidence="The approved contract no longer contains this lifecycle observation; "
                                           "completion requires every current one to pass")
    if result is None:
        return []
    revision = result.get("source_revision") or record.get("source_revision")
    rows = findings_ledger.ledger(state)
    current = {row["observation_hash"]: row for row in open_rows(state)}
    for check in result.get("checks") or []:
        observation = known.get(check.get("observation_hash"))
        if observation is None:
            continue
        row = current.get(observation["hash"])
        if not check.get("error"):
            # A race can be missed: a PASS on a source this observation already failed on (validated
            # again, after --resume-paused, or reverted to) is not a correction and closes nothing.
            if row is not None and revision not in (row.get("failed_revisions") or []):
                row.update(status="resolved", resolved_at=at, resolved_in=result.get("summary"),
                           resolution_evidence=f"Runner lifecycle replay PASS on source {str(revision)[:12]}: "
                                               f"{result.get('summary')}")
                row.pop("not_rechecked_in", None)
            continue
        evidence = "; ".join(part for part in (
            f"Runner replay summary: {result.get('summary')}" if result.get("summary") else "",
            f"supervisor transcript: {check.get('output')}" if check.get("output") else "") if part)
        text = _text(observation, check, revision)
        if row is None:
            row = {"id": findings_ledger.allocate_id(state), "source": SOURCE, "severity": "high",
                   "finding": text, "evidence": evidence, "blocking": True, "status": "open",
                   "opened_at": at, "opened_in": result.get("summary"), "scope": findings_ledger.report_scope(state),
                   "times_reported": 1, "last_reported_at": at, "last_reported_in": result.get("summary"),
                   "assigned_task": None, "observation_hash": observation["hash"],
                   "protocol": observation.get("protocol"), "criterion_ids": list(observation.get("criterion_ids") or [])}
            rows.append(row)
        else:
            row.update(finding=text, evidence=evidence, times_reported=row.get("times_reported", 1) + 1,
                       last_reported_at=at, last_reported_in=result.get("summary"))
        if revision not in row.setdefault("failed_revisions", []):
            row["failed_revisions"].append(revision)
        reported.append(row)
    if reported and saved:
        brief = [{key: row[key] for key in ("id", "source", "severity", "finding", "evidence", "blocking")}
                 for row in reported]
        state["unresolved_findings"] = [*[item for item in state.get("unresolved_findings") or []
                                          if item.get("id") not in {row["id"] for row in brief}], *brief]
    return reported


def blocking_summary(state, limit=3):
    """The open blocking findings in a sentence for a completion refusal, or ''."""
    rows = findings_ledger.blocking_entries(state)
    if not rows:
        return ""
    shown = "; ".join(f"{row.get('id')} ({row.get('source')}): {str(row.get('finding', ''))[:300]}"
                      for row in rows[:limit])
    more = f"; and {len(rows) - limit} more" if len(rows) > limit else ""
    runner = any(row.get("source") == SOURCE for row in rows)
    return (shown + more + (". A runner finding closes only when the runner's own observation passes on a "
                            "new source: request REWORK so the Builder can correct it" if runner else ""))

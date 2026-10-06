"""A design review revised turn by turn as the user answers it (issue #185).

The report keeps its whole trail (``revision``, ``revisions``: one entry per review, with the
message that prompted it and the ids of its open blocking, open advisory and resolved concerns),
so the files alone show how the review changed: no ordering blocker before any answer, one after
"per-domain", the same concern resolved after "per-registry is fine"; the planted migration gap
blocking in every revision; the unowned DLQ raised as advisory; no invented blocker; nothing
renumbered. With a run record, each turn also ran only the Architect, changed only the review,
and left a report whose concerns match its trail entry (read from the copy the harness kept as
that turn ended, never from AutoCode's state).
"""
import re
from pathlib import Path

from harness.oracle import Check, load_json, only_changed_under, run_checks

REPORT = "review/design-review.json"
SAYS = ("Ordering is per-domain.", "Per-registry is fine.")
ORDERING = (r"\border(ing|ed)?\b(?! to\b)|\breorder|out of order|sequenc|\bseq\b|per[- ](domain|registry)"
            r"|partition key|keyed by")
# What a concern is about: its area label decides, else its summary; the first match wins.
AREAS = (
    ("migration", r"rollback|roll back|revert|switch(ing|es)? back|reconcil|dual[- ]?writ|cut ?over|migrat"),
    # The DLQ's gap is that a poison event stops its partition and nobody owns getting it going again. Live
    # Architects named it "a poison event stops its partition" and "a halted partition has no owner"; a
    # halted partition counts only when nothing says the concern is about order.
    ("dlq", r"\bdlq\b|dead[- ]letter|poison"),
    ("ordering", ORDERING),
    ("dlq", r"\b(halt|stop|stall|paus|stuck)\w*\W+(\w+\W+){0,3}partition"
            r"|partition\w*\W+(\w+\W+){0,3}(halt|stop|stall|paus|resum|stuck)"),
    ("idempotency", r"idempot|duplicat|dedup|twice|double[- ]charg|exactly[- ]once|redeliver"),
    ("throughput", r"throughput|events/s|per second|capacity|sizing|headroom|hot partition|\bskew"),
)
# Covered by the design and the code: durable event_id dedupe in billing and notifications (a rare
# duplicate notification is accepted), an idempotent producer, and per-key sizing.
INVENTED = ("idempotency", "throughput")
# A reply runs the recognizer and the Architect (and the Architect's report repair) and nothing else.
ARCHITECT_ONLY = ("recognize_workflow", "review_design", "review_design_report_repair")


def about(text):
    return next((area for area, pattern in AREAS if re.search(pattern, str(text or ""), re.I | re.S)), None)


def area_of(concern):
    return about(concern.get("area")) or about(concern.get("summary"))


def ids(row):
    return {*(row.get("blocking") or []), *(row.get("advisory") or []), *(row.get("resolved") or [])}


def check(project, scenario, run=None):
    report, error = load_json(project / REPORT)
    valid = (isinstance(report, dict) and isinstance(report.get("concerns"), list)
             and isinstance(report.get("revisions"), list))
    checks = [Check("report_valid", valid, error or ("" if valid else "the report has no concerns or revisions"))]
    if valid:
        checks += report_checks(report)
    checks.append(only_changed_under(project, "review/"))
    checks += conversation_checks(run, report if valid else {})
    return checks


def report_checks(report):
    concerns = {c.get("id"): c for c in report["concerns"] if isinstance(c, dict)}
    trail = [row for row in report["revisions"] if isinstance(row, dict)]
    first, second, third = (trail[index] if index < len(trail) else {} for index in range(3))
    said = [row.get("said") for row in trail]
    checks = [Check("three_revisions", report.get("revision") == 3 and len(trail) == 3 and said[1:] == list(SAYS),
                    f"revision {report.get('revision')}; messages {said}")]
    ordering = {key for key, concern in concerns.items() if area_of(concern) == "ordering"}
    # A question has no area label; one that asks about order counts whatever else it mentions.
    asked = [q.get("question") for q in first.get("questions") or [] if re.search(ORDERING, str(q.get("question")), re.I)]
    checks.append(Check("asked_about_ordering_first", bool(asked),
                        f"first review's questions: {[q.get('question') for q in first.get('questions') or []]}"))
    early = sorted(ordering & set(first.get("blocking") or []))
    checks.append(Check("ordering_not_blocking_before_any_answer", bool(first) and not early,
                        f"blocked on ordering before any answer: {early}" if early else ""))
    raised = sorted(ordering & set(second.get("blocking") or []))
    checks.append(Check("ordering_blocking_after_first_answer", bool(raised),
                        f"second review blocks on {second.get('blocking')}; ordering concerns {sorted(ordering)}"))
    settled = [key for key in raised if key in (third.get("resolved") or [])
               and concerns[key].get("status") == "resolved"
               and re.search(r"registr|transfer", str(concerns[key].get("resolution") or ""), re.I)]
    blocking = sorted(key for key in ordering if concerns[key].get("status") != "resolved"
                      and concerns[key].get("severity") == "blocking")
    # Still asked: an ordering question kept from an earlier review (questions keep their ids), or a new one
    # about ordering. A later question that only mentions order in passing is not re-asking it.
    earlier = {q.get("id") for row in (first, second) for q in row.get("questions") or []
               if re.search(ORDERING, str(q.get("question")), re.I)}
    seen = {q.get("id") for row in (first, second) for q in row.get("questions") or []}
    still_asked = [q.get("question") for q in third.get("questions") or [] if q.get("id") in earlier
                   or (q.get("id") not in seen and about(q.get("question")) == "ordering")]
    checks.append(Check("ordering_resolved_after_second_answer", bool(settled) and not blocking and not still_asked,
                        f"resolved {third.get('resolved')}; still blocking on ordering {blocking}; "
                        f"still asking {still_asked}"))
    lost = [(index + 2, sorted(ids(trail[index]) - ids(trail[index + 1])))
            for index in range(len(trail) - 1) if ids(trail[index]) - ids(trail[index + 1])]
    kept = len(trail) == 3 and not lost and ids(trail[-1]) == set(concerns)
    checks.append(Check("revised_not_rewritten", kept,
                        f"concern ids missing from a later revision (revision, ids): {lost}; "
                        f"last entry {sorted(ids(trail[-1])) if trail else []} vs report {sorted(concerns)}"))
    migration = [key for key, concern in concerns.items() if area_of(concern) == "migration"
                 and len(trail) == 3 and all(key in (row.get("blocking") or []) for row in trail)]
    checks.append(Check("migration_gap_blocking_every_revision", bool(migration),
                        f"blocking per revision: {[row.get('blocking') for row in trail]}"))
    dlq = [key for key, concern in concerns.items() if area_of(concern) == "dlq" and concern.get("severity") == "advisory"]
    checks.append(Check("dlq_advisory_raised", bool(dlq),
                        f"concerns: {[(key, area_of(c), c.get('severity')) for key, c in concerns.items()]}"))
    ever = set().union(*(set(row.get("blocking") or []) for row in trail))
    invented = sorted(key for key in ever if key in concerns and area_of(concerns[key]) in INVENTED)
    checks.append(Check("no_invented_blockers", not invented,
                        f"blocking on what the design and code cover: {invented}" if invented else ""))
    checks.append(Check("still_requests_changes", report.get("verdict") == "request_changes",
                        f"verdict {report.get('verdict')!r}: the migration gap is still open"))
    return checks


def conversation_checks(run, report):
    if run is None:
        return []
    turns = run.get("turns") or []
    checks = [Check("three_turns_in_one_run", len(turns) == 3, f"{len(turns)} turns recorded")]
    if len(turns) != 3:
        return checks
    trail = [row for row in report.get("revisions") or [] if isinstance(row, dict)]
    mismatched = []
    for number, turn in enumerate(turns, start=1):
        checks += [Check(f"turn{number}_{c.name}", c.ok, c.detail) for c in run_checks(
            turn, workflow="design", no_build=True, no_requirements=True, max_questions=0)]
        changed = turn.get("changed_files")
        checks.append(Check(f"turn{number}_changed_only_the_review", changed == [REPORT], f"changed {changed}"))
        stages = turn.get("model_stages") or []
        others = [stage for stage in stages if stage not in ARCHITECT_ONLY]
        checks.append(Check(f"turn{number}_ran_only_the_architect", "review_design" in stages and not others,
                            f"model stages {stages}"))
        left, error = load_json(Path(turn["kept_files"]) / REPORT) if turn.get("kept_files") else (None, "not kept")
        entry = trail[number - 1] if number <= len(trail) else {}
        found = opened(left) if isinstance(left, dict) else None
        if found != {key: set(entry.get(key) or []) for key in ("blocking", "advisory", "resolved")}:
            mismatched.append((number, error if found is None else {key: sorted(v) for key, v in found.items()}))
    checks.append(Check("trail_matches_each_turns_report", not mismatched,
                        f"turns whose report differs from its trail entry: {mismatched}" if mismatched else ""))
    return checks


def opened(report):
    """A report's open blocking, open advisory and resolved concern ids, read from its concerns."""
    concerns = [c for c in report.get("concerns") or [] if isinstance(c, dict)]
    unresolved = [c for c in concerns if c.get("status", "open") != "resolved"]
    return {"blocking": {c.get("id") for c in unresolved if c.get("severity") == "blocking"},
            "advisory": {c.get("id") for c in unresolved if c.get("severity") != "blocking"},
            "resolved": {c.get("id") for c in concerns if c.get("status") == "resolved"}}

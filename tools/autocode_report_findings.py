"""Identify finding resolutions retained verbatim from a completed review.

This is provenance only. The findings ledger still checks reviewer ownership,
reviewed scope and passing verification before applying a disposition.

``retained`` gives the two fields the runner sets on its temporary copy of the
original stage record before it applies a repaired report (they are not saved
in run state). ``autocode_findings`` reads both: ``preserved_finding_dispositions``
decides which rows a repair may keep, and ``unretained_finding_dispositions``
only words its refusal (``refusal``) for any other row.
"""
from __future__ import annotations

FIELDS = frozenset(('id', 'disposition', 'evidence'))
REVIEW_SOURCES = {'sol': 'sol', 'astra_review': 'astra', 'astra_plan': 'astra'}
# These original outcomes already reach disposition application. A BLOCKED
# result carries no such authority, even if repair changes its outcome later.
APPLICABLE_OUTCOMES = {'sol': ('verdict', ('PASS', 'FAIL')),
                      'astra': ('status', ('CONTINUE', 'REWORK', 'COMPLETE', 'TASK_COMPLETE'))}
# The report-repair prompt's rule for these rows. In #459's live run a repair
# "corrected" the evidence citation inside a closure row, was refused, and the
# next repair dropped the closure, which cost another validation cycle.
REPAIR_INSTRUCTION = (
    'Never introduce or change finding resolutions or retractions. Copy each finding_dispositions row you keep '
    "byte-for-byte from the original completed, nonblocked review (the handoff's original_report when it is "
    'set, otherwise its rejected_report): the same id, disposition and evidence text, even where you correct '
    'a citation elsewhere in the report. Never copy a row that a later repair changed or added. A row that '
    'differs by one character cannot close its finding and the repair is rejected; omit a row rather than edit it. ')


def _rows(report):
    if not isinstance(report, dict):
        return None
    rows = report.get('finding_dispositions', [])
    if not isinstance(rows, list):
        return None
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != FIELDS
                or not isinstance(row['id'], str) or not row['id'].strip()
                or row['id'] != row['id'].strip() or row['id'] in result
                or row['disposition'] not in ('resolved', 'retracted')
                or not isinstance(row['evidence'], str) or not row['evidence'].strip()):
            return None
        result[row['id']] = dict(row)
    return result


def _reports(stage, report):
    if stage == 'astra_checkpoint':
        if not isinstance(report, dict):
            return None
        return {'sol': report.get('validation'), 'astra': report.get('decision')}
    return {REVIEW_SOURCES[stage]: report}


def preserved_dispositions(record, original_report, repaired_report):
    """Return detached exact rows, keyed by reviewer, from a fresh original.

    ``original_report`` is the extracted original JSON object, or ``None`` when
    extraction failed. Report repairs may retain earlier dispositions but cannot
    create or change one, even if their proposed result would otherwise be valid.
    A checkpoint's blocked or unknown reviewer outcome authorizes no rows for
    that reviewer; its other valid reviewer can still retain exact dispositions.
    """
    if (not isinstance(record, dict)
            or record.get('stage') not in (*REVIEW_SOURCES, 'astra_checkpoint')
            or type(record.get('exit_code')) is not int or record['exit_code'] != 0
            or any(record.get(flag) for flag in ('report_only', 'report_repaired',
                'truncated_output', 'timed_out', 'interrupted', 'abandoned'))):
        return {}
    if not isinstance(original_report, dict) or not isinstance(repaired_report, dict):
        return {}
    original = _reports(record['stage'], original_report)
    repaired = _reports(record['stage'], repaired_report)
    if original is None or repaired is None:
        return {}
    retained, original_ids, repaired_ids = {}, set(), set()
    for source in original:
        before, after = _rows(original[source]), _rows(repaired[source])
        if (before is None or after is None
                or original_ids.intersection(before) or repaired_ids.intersection(after)):
            return {}
        original_ids.update(before)
        repaired_ids.update(after)
        field, applicable = APPLICABLE_OUTCOMES[source]
        if original[source].get(field) not in applicable:
            continue
        rows = [dict(row) for fid, row in after.items() if before.get(fid) == row]
        if rows:
            retained[source] = rows
    return retained


def _named_rows(report):
    """Disposition rows with a string id, however malformed the rest of the report is."""
    rows = report.get('finding_dispositions') if isinstance(report, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get('id'), str)]


UNAUTHORIZED = ('cannot be kept: the original review was blocked, incomplete or already repaired, '
                'or a finding_dispositions row is malformed')


def _reason(before, row):
    if before is None:
        return 'is not in the original review'
    missing = object()
    changed = [key for key in sorted(set(before) | set(row)) if before.get(key, missing) != row.get(key, missing)]
    return f"changed its {' and '.join(changed)} from the original review's row" if changed else UNAUTHORIZED


def retained(record, original_report, repaired_report):
    """The record fields for applying a repaired report (see the module docstring).

    The second maps reviewer and finding ID to why that repaired row is not
    among the exact rows kept. It grants nothing: only the first field can.
    """
    kept = preserved_dispositions(record, original_report, repaired_report)
    stage = record.get('stage') if isinstance(record, dict) else None
    unretained = {}
    if stage in (*REVIEW_SOURCES, 'astra_checkpoint') and isinstance(repaired_report, dict):
        original = _reports(stage, original_report) if isinstance(original_report, dict) else None
        for source, report in (_reports(stage, repaired_report) or {}).items():
            earlier = {}
            for row in _named_rows((original or {}).get(source)):
                earlier.setdefault(row['id'], row)
            for row in _named_rows(report):
                if row not in kept.get(source, []):
                    why = _reason(earlier.get(row['id']), row) if original is not None else UNAUTHORIZED
                    unretained.setdefault(source, {}).setdefault(row['id'], why)
    return {'preserved_finding_dispositions': kept, 'unretained_finding_dispositions': unretained}


def refusal(source, finding_id, record):
    """The findings ledger's error for a repaired disposition it may not apply."""
    why = ((record.get('unretained_finding_dispositions') or {}).get(source) or {}).get(finding_id)
    return (f'A report-only repair cannot close findings: {source} finding_dispositions row {finding_id} '
            f"{why or 'is not an exact row of the original completed review'}. A repair keeps a closure only as "
            'a byte-for-byte copy, evidence included, of a row from a completed, nonblocked original review; '
            'otherwise omit the row and leave the finding open for a fresh review')

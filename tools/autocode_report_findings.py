"""Identify finding resolutions retained verbatim from a completed review.

This is provenance only. The findings ledger still checks reviewer ownership,
reviewed scope and passing verification before applying a disposition.
"""
from __future__ import annotations

FIELDS = frozenset(('id', 'disposition', 'evidence'))
REVIEW_SOURCES = {'sol': 'sol', 'astra_review': 'astra', 'astra_plan': 'astra'}
# These original outcomes already reach disposition application. A BLOCKED
# result carries no such authority, even if repair changes its outcome later.
APPLICABLE_OUTCOMES = {'sol': ('verdict', ('PASS', 'FAIL')),
                      'astra': ('status', ('CONTINUE', 'REWORK', 'COMPLETE', 'TASK_COMPLETE'))}


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

"""Recorded coverage and inspected evidence freshness, without completion authority."""
from copy import deepcopy
try:
    from .autocode_util import digest
except ImportError:
    from autocode_util import digest


def mapping(value):
    return value if isinstance(value, dict) else {}


def rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def project(state, *, current_revision=None, evidence_matches=None, inspection_error=None, accepted_human_ids=()):
    """Describe recorded results; freshness requires caller-supplied actual inspection.

    The interface owns source snapshots and file reads. This projection never
    writes, runs checks, closes findings or approves/completes a task.
    """
    report = mapping(state.get('validation'))
    contract = mapping(state.get('goal_contract'))
    task = mapping(state.get('current_task'))
    criteria = rows(mapping(contract.get('body')).get('acceptance_criteria')) or rows(state.get('acceptance_criteria'))
    freshness, reasons = 'not_recorded', ['No independent verification report has been saved.']
    if report:
        freshness, reasons = 'not_inspected', ['The saved report has not been checked against the current checkout.']
        if inspection_error:
            freshness, reasons = 'unavailable', [inspection_error]
        elif current_revision is not None:
            reasons = []
            if not report.get('source_revision'):
                reasons.append('The report has no recorded source identity.')
            elif report['source_revision'] != current_revision:
                reasons.append('The source changed after these checks.')
            if contract and (report.get('contract_hash') != contract.get('hash') or report.get('contract_revision') != contract.get('revision')):
                reasons.append('The report belongs to a different or unbound plan revision.')
            if task and report.get('task_id') != task.get('id'):
                reasons.append('The report belongs to a different or unbound task.')
            if report.get('criteria_revision') != state.get('criteria_revision'):
                reasons.append('The acceptance checklist changed after these checks.')
            if evidence_matches is not True:
                reasons.append('The recorded evidence is missing, changed or not authenticated.')
            if state.get('active_stage') or state.get('active_runner_check'):
                reasons.append('An attempt or runner check is still recorded; current work needs verification.')
            freshness = 'stale_or_unverified' if reasons else 'current'
            if not reasons:
                reasons = ['The recorded report and its pinned evidence match the inspected source and plan. This is not completion approval.']
    outcomes = {}
    for row in rows(report.get('criterion_results')):
        outcomes.setdefault(str(row.get('id')), []).append(row)
    coverage = []
    for criterion in criteria:
        matches = outcomes.get(str(criterion.get('id')), [])
        result = matches[0] if len(matches) == 1 else {}
        recorded = str(result.get('status') or '').upper()
        current = freshness == 'current' and bool(result.get('evidence_refs'))
        human_pending = criterion.get('human_review') is True and criterion.get('id') not in accepted_human_ids
        status = ('checked' if current and recorded == 'PASS' and not human_pending else
                  'failed' if current and recorded in ('FAIL','BLOCKED') else 'unchecked')
        coverage.append({'id': criterion.get('id'), 'criterion': criterion.get('criterion') or criterion.get('description'),
                         'verification_method': criterion.get('verification_method'), 'human_review': criterion.get('human_review') is True,
                         'state': status, 'recorded_status': recorded or 'UNVERIFIED', 'human_acceptance_pending': human_pending,
                         'evidence_refs': deepcopy(result.get('evidence_refs') or [])})
    return {'version': 1, 'freshness': freshness, 'reasons': reasons, 'report_token': digest(report) if report else None,
            'contract_token': f"r{contract['revision']}:{contract['hash']}" if contract.get('hash') and contract.get('revision') is not None else None,
            'criteria_token': digest(criteria), 'task_id': task.get('id'), 'criteria_revision': state.get('criteria_revision'),
            'source_revision': report.get('source_revision'), 'inspected_source_revision': current_revision,
            'coverage': coverage}

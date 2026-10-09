"""Completion reports for coordinators, using only public child TaskRun reports."""
from pathlib import Path
from copy import deepcopy

try:
    from . import autocode_evidence_document as document, autocode_evidence_export as export
    from . import autocode_evidence_provenance as provenance, autocode_util as util
except ImportError:
    import autocode_evidence_document as document, autocode_evidence_export as export
    import autocode_evidence_provenance as provenance, autocode_util as util


def quantity(values):
    values = [document.mapping(value) for value in values]
    known = [value.get('value') for value in values if isinstance(value.get('value'), (int, float))]
    observed = [next((value[key] for key in ('value', 'observed', 'known')
                      if isinstance(value.get(key), (int, float))), 0) for value in values]
    complete = bool(values) and len(known) == len(values) and all(value.get('complete') for value in values)
    return {'value': sum(known) if complete else None, 'observed': sum(observed), 'complete': complete}


def combine(reports):
    """Preserve unknown quantities; never describe the known subtotal as a total."""
    usage = [report['document']['usage'] for report in reports]
    keys = set().union(*(document.mapping(row.get('tokens')) for row in usage))
    return {'tokens': {key: quantity([document.mapping(row.get('tokens')).get(key) for row in usage]) for key in sorted(keys)},
            'cost': {key: quantity([document.mapping(row.get('cost')).get(key) for row in usage])
                     for key in ('reported_usd', 'historical_estimated_usd')},
            'provider_requests': quantity([row.get('provider_requests') for row in usage])}


def publish(root, *, kind, identity, status, child_runs, source_revision, base_commit=None,
            contract_token=None, outcome=None, checks=(), not_verified=(), binding_values=None):
    """Coordinator supplies its own receipts/identity; child_runs are TaskRun handles.

    Child records are historical delivery receipts: later integration may change
    their checkout. The coordinator's existing inheritance/source and integration
    gates remain responsible for accepting their delivery; this adds no approval.
    """
    reports, children = [], []
    for child_id, run in child_runs:
        report = run.evidence_report(require_current=False)
        reports.append(report)
        child = report['document']
        children.append({'id': str(child_id), 'json_path': report['json_path'],
            'json_sha256': report['json_sha256'], 'markdown_sha256': report['markdown_sha256'],
            'source_revision': child['revision']['source_revision'], 'provenance': child['provenance']})
    bound = util.digest({'kind': kind, 'identity': identity, 'status': status, 'children': children,
                         'source_revision': source_revision, 'coordinator': binding_values})
    criteria, attempts, findings, child_checks = [], [], [], []
    for child, report in zip(children, reports):
        value = report['document']
        criteria += [{**row, 'id': child['id'] + '/' + str(row['id'])} for row in value['criteria']]
        attempts += deepcopy(value['attempts'])
        child_checks += deepcopy(value['checks'])
        findings += [{**row, 'id': child['id'] + '/' + str(row.get('id'))} for row in value['findings']]
    view = {'status': status, 'workflow': kind, 'evidence': {'outcome': outcome, 'base_commit': base_commit,
             'validator_source_revision': source_revision, 'acceptance': criteria, 'findings': findings,
             'check_replay': {'checks': [*child_checks, *checks]}},
            'verification': {'contract_token': contract_token},
            'usage': {'accounting': {'attempts': attempts, **combine(reports)}}}
    value = document.build(view, kind=kind, run_identity=identity, completed_at=None,
            provenance=provenance.combined([row['provenance'] for row in children]), binding=bound, children=children)
    # Retain methods/human acceptance from the child's canonical criteria.
    value['criteria'] = criteria
    value['unverified'] += list(not_verified)
    value['unverified'].append('Coordinator completion timestamp and elapsed wall time are not recorded; '
                              'child completion times do not establish final integration or runtime completion')
    value['unverified'] += [child['id'] + ': ' + gap for child, report in zip(children, reports)
                            for gap in report['document']['unverified']]
    value['attempts'] = attempts
    anchor = export.write(Path(root), value)
    return export.read(root, anchor, expected_binding=bound, include=True)

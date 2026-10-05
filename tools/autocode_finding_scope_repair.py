"""Explicit, audited correction of historical finding attribution at a stopped boundary.

This policy never closes findings or accepts milestones. The CLI owns the run
lock, authenticates the pending permission, commits the candidate atomically,
and leaves independent checkpoint evaluation to the Completion Owner.
"""
from copy import deepcopy


def reconcile(ledger, changes, milestones):
    owners = {m['id']: set(m['acceptance_criteria']) for m in milestones}
    if len({row['id'] for row in ledger}) != len(ledger):
        raise ValueError('Ambiguous ledger identities')
    if not changes or len({change['id'] for change in changes}) != len(changes):
        raise ValueError('Require distinct explicit finding changes')
    rows = {row['id']: row for row in ledger}
    result = deepcopy(ledger)
    candidates = {row['id']: row for row in result}
    for change in changes:
        row = rows.get(change['id'])
        if not row or row.get('status') != 'open':
            raise ValueError('Reconciliation requires the exact open finding')
        if row.get('scope') != change['expected_scope']:
            raise ValueError('Saved scope changed; review again')
        original = change['expected_scope']
        if (original.get('milestone_id') not in owners
                or not original.get('criteria')
                or set(original['criteria']) <= owners[original['milestone_id']]):
            raise ValueError('Only a historical owner-scope mismatch may be corrected')
        target = change['new_scope']
        criteria = target.get('criteria')
        if (target.get('milestone_id') not in owners or not isinstance(criteria, list)
                or not criteria or len(set(criteria)) != len(criteria)
                or not set(criteria) <= owners[target['milestone_id']]
                or not set(criteria) <= set(original['criteria'])):
            raise ValueError('New scope must retain approved original criteria')
        if not isinstance(change.get('evidence'), str) or not change['evidence'].strip():
            raise ValueError('An explicit reviewed attribution is required')
        candidates[change['id']]['scope'] = deepcopy(target)
    return result


def prepare(state, manifest, published, at):
    """Return a candidate; caller must authenticate published before calling."""
    if (state.get('status') != 'WAITING_FOR_USER' or published.get('scope') != 'permission'
            or not published.get('request', {}).get('proposed_delta', '').startswith(
                'Operational ledger-metadata reconciliation only:')):
        raise ValueError('Require the exact finding-scope permission checkpoint')
    if any(state.get(k) for k in ('active_stage', 'active_runner_check', 'runner_check',
                                 'pending_report_repair', 'uncertain_artifacts')):
        raise ValueError('Reconcile active or uncertain workers before attribution repair')
    contract = state.get('goal_contract') or {}
    if (contract.get('approval_status') != 'approved'
            or manifest.get('contract_token') != f"r{contract.get('revision')}:{contract.get('hash')}"):
        raise ValueError('Require the exact approved contract')
    if (manifest.get('request_id') != published.get('request_id')
            or manifest.get('request_token') != published.get('request_token')
            or len(published.get('questions', [])) != 1
            or manifest.get('question_id') != published['questions'][0]['id']
            or state.get('next_stage') not in ('astra_review', 'astra_checkpoint')):
        raise ValueError('Require the exact permission and checkpoint frontier')
    candidate = deepcopy(state)
    changes = manifest['changes']
    candidate['findings_ledger'] = reconcile(state.get('findings_ledger', []), changes,
                                            contract['body']['milestones'])
    candidate.setdefault('user_events', []).append({
        'kind': 'finding_scopes_reconciled', 'actor': 'user_cli', 'at': at,
        'contract_token': manifest['contract_token'], 'request_id': manifest['request_id'],
        'request_token': manifest['request_token'], 'question_id': manifest['question_id'],
        'changes': deepcopy(changes),
    })
    candidate['pending_questions'] = []
    candidate.pop('user_request', None)
    candidate.pop('stop_reason', None)
    candidate.update(status='RUNNING', phase='BUILDING')
    return candidate

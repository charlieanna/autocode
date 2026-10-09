"""Attach task chat to its durable conversation and project the approved boundary."""
from copy import deepcopy

try:
    from .. import autocode_conversation as protocol
    from ..autocode_goals import approved as contract_approved
    from ..autocode_planner_contract import record_product_change
except ImportError:
    import autocode_conversation as protocol
    from autocode_goals import approved as contract_approved
    from autocode_planner_contract import record_product_change


def project_conversation(run, state):
    if not protocol.journal_file(run).exists():
        return None
    try:
        doc = protocol.read_journal(run)['conversation']
    except (ValueError, OSError):
        return {'status': 'error', 'error': 'The saved conversation journal could not be verified.',
                'messages': [], 'plan_drafts': []}
    contract = state.get('goal_contract') or {}
    token = 'r{}:{}'.format(contract.get('revision'), contract.get('hash')) if contract.get('hash') else None
    planning = state.get('planning') or {}
    reviewed = bool(token and planning.get('final_token') == token)
    change = doc.get('pending_product_change') or {}
    pending_change = bool(change and (not reviewed or not change.get('prior_goal_token')
                                      or token == change['prior_goal_token']))
    reviewed = reviewed and not pending_change
    try:
        approved = bool(reviewed and contract_approved(state))
    except (KeyError, TypeError, ValueError):
        approved = False
    doc['plan_gate'] = {'token': token, 'architect_reviewed': reviewed,
                        'pending_product_change': pending_change,
                        'approved': approved, 'approval': deepcopy(contract.get('approval_event')),
                        'status': 'approved' if approved else 'ready' if reviewed else 'reviewing'}
    doc['attachment'] = {'status': 'linked', 'run': str(run), 'workspace': state.get('workspace')}
    return doc


def append_feedback(run, row, *, product_change, goal_token=None):
    """Mirror an accepted task input once; execution authority stays with the runner."""
    if not protocol.journal_file(run).exists():
        return
    def apply(journal):
        doc = journal['conversation']
        ident = 'task-' + row['id']
        if any(item.get('id') == ident for item in doc['messages']):
            return journal
        message = {'id': ident, 'logical_turn_id': ident, 'client_request_id': row['id'],
                   'in_reply_to': None, 'role': 'user', 'speaker': 'You', 'text': row['text'],
                   'created_at': protocol.now(), 'status': row['status']}
        doc['messages'].append(message)
        doc.setdefault('request_logical_turns', []).append(
            {'client_request_id': row['id'], 'logical_turn_id': ident})
        if product_change:
            # The intervention inbox has already accepted this input. Existing
            # contract guards pause and re-review it before further build work;
            # the preview must become stale immediately, never imply approval.
            record_product_change(doc, detail=row['text'][:4000])
            doc['pending_product_change'] = {'prior_goal_token': goal_token,
                                             'logical_turn_id': ident, 'created_at': message['created_at']}
        return journal
    protocol.update_journal(run, apply)

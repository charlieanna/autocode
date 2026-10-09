"""Pure source-epoch checks for an original native approval journal.

Inputs are explicit protected journal rows, never a state file or live workspace.
This validates causal consistency; only the original guarded caller and durable
sink can produce the authority being checked. It cannot adopt historical proof.
"""
from copy import deepcopy

try:
    from . import autocode_contract_identity as contracts, autocode_source_scope as scope
    from . import autocode_util as util
except ImportError:
    import autocode_contract_identity as contracts, autocode_source_scope as scope
    import autocode_util as util


def _require(value, reason):
    if not value:
        raise util.Paused('PAUSED_VISUAL_EVIDENCE', 'Native source transition: ' + reason)


def selected(snapshot, body):
    """Check the complete canonical inventory declaration without normalizing it."""
    paths = scope.declared_paths(body)
    expected = {'head', 'files', 'revision'} | ({'source_paths'} if paths else set())
    _require(isinstance(snapshot, dict) and set(snapshot) == expected
             and snapshot.get('source_paths', []) == paths
             and isinstance(snapshot['head'], str) and bool(snapshot['head'])
             and isinstance(snapshot['files'], dict)
             and snapshot['revision'] == util.digest({key: snapshot[key] for key in ('head', 'files')}),
             'snapshot does not declare the exact contract inventory')
    return snapshot


def cursor(rows):
    """Replay source and one initial scope transition in chronological order."""
    _require(rows and rows[0]['kind'] == 'origin', 'missing immutable raw origin')
    source = rows[0]['body']['source']
    runtime = rows[0]['body']['runtime_sha256']
    approved_contract = None
    prior, used, latest_ack = {}, set(), None
    for row in rows[1:]:
        kind, body = row['kind'], row['body']
        if kind == 'finished':
            source = body['after']  # Original launch replay validates this delta.
        elif kind == 'parent-commit-acknowledged':
            latest_ack = row['id']
        elif kind == 'parent-commit-invalidated':
            _require(body == {'parent_commit_id': latest_ack} and latest_ack is not None,
                     'invalidation does not identify the latest original ACK')
            latest_ack = None
        elif kind == 'approval-committed':
            _require(approved_contract is None and body['prepared_id'] not in used,
                     'initial source transition was already committed')
            preparation = prior.get(body['prepared_id'])
            acknowledgement = prior.get(body['parent_commit_id'])
            _require(preparation is not None and preparation['kind'] == 'approval-prepared'
                     and acknowledgement is not None and acknowledgement['kind'] == 'parent-commit-acknowledged'
                     and latest_ack == acknowledgement['id'], 'missing current causal approval preparation/ACK')
            prepared, ack = preparation['body'], acknowledgement['body']
            intent = prior.get(ack['intent_id'])
            _require(intent is not None and intent['kind'] == 'parent-commit-prepared'
                     and preparation['sequence'] < intent['sequence'] < acknowledgement['sequence']
                     and ack == {**intent['body'], 'intent_id': intent['id'], 'sink_identity': ack['sink_identity']}
                     and intent['body'].get('approval_prepared_id') == preparation['id']
                     and isinstance(ack['approval_nonce'], str) and bool(ack['approval_nonce']),
                     'ACK does not belong to the original prepared approval')
            draft, goal = prepared['draft_contract'], body['contract']
            event = goal.get('approval_event') or {}
            _require(contracts.sealed(draft) and contracts.sealed(goal)
                     and all(goal[key] == draft[key] for key in ('task_id', 'revision', 'hash', 'body'))
                     and goal.get('approval_status') == 'approved'
                     and event.get('kind') == 'goal_approval' and event.get('actor') == 'user_cli'
                     and event.get('token') == prepared['selected'] == contracts.token(draft)
                     and not goal['body']['open_blocking_questions'], 'changed original approved contract')
            chosen = selected(prepared['selected_snapshot'], draft['body'])
            _require(prepared['snapshot'] == source and 'source_paths' not in source
                     and chosen['head'] == source['head']
                     and body['snapshot'] == chosen == ack['source']
                     and body['decision'] == prepared['decision']
                     and body['settings'] == prepared['settings']
                     and ack['settings_sha256'] == util.digest(body['settings'])
                     and ack['contract'] == goal and ack['task'] == body['current_task']
                     and prepared['runtime_sha256'] == body['runtime_sha256'] == ack['runtime_sha256'] == runtime,
                     'source/contract/settings/task/runtime changed across approval')
            source, approved_contract = chosen, goal
            used.add(preparation['id'])
            latest_ack = None
        elif kind == 'planning-committed':
            if body.get('parent_commit_id') == latest_ack:
                latest_ack = None
        prior[row['id']] = row
    return deepcopy(source), deepcopy(approved_contract)

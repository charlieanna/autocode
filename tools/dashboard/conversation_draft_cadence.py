"""Pure policy for bounded live-draft refreshes and their human provenance."""

ANSWERS_PER_UPDATE = 3


def held(dispatch):
    return isinstance(dispatch, dict) and dispatch.get('cadence_hold') is True


def schedule(doc, revision):
    # Legacy intentions were already authorized for dispatch. Count them as
    # scheduled; never rewrite or relaunch a historical delivery receipt.
    prior = [row.get('requirements_revision', 0)
             for row in doc.get('_planner_dispatches', {}).values()
             if isinstance(row, dict) and not held(row)
             and type(row.get('requirements_revision')) is int]
    return not prior or revision - max(prior) >= ANSWERS_PER_UPDATE


def sources(doc, after=0):
    messages = {row['id']: row for row in doc.get('messages', []) if row.get('role') == 'user'}
    result = []
    for revision in doc.get('requirements', {}).get('revisions', []):
        if revision.get('revision', 0) <= after:
            continue
        source = revision.get('source') or {}
        message = messages.get(source.get('message_id'))
        if message:
            result.append({'message_id': message['id'], 'logical_turn_id': message.get('logical_turn_id'),
                           'requirements_revision': revision['revision'],
                           'excerpt': ' '.join(message.get('text', '').split())[:240]})
    return result


def public(doc):
    revisions = doc.get('requirements', {}).get('revisions') or []
    if not revisions:
        return None
    latest = revisions[-1]
    turn = (latest.get('source') or {}).get('logical_turn_id')
    dispatch = doc.get('_planner_dispatches', {}).get(turn) or {}
    waiting = held(dispatch)
    scheduled = [row.get('requirements_revision', 0)
                 for row in doc.get('_planner_dispatches', {}).values()
                 if isinstance(row, dict) and not held(row)
                 and type(row.get('requirements_revision')) is int]
    count = latest['revision'] - max(scheduled, default=0) if waiting else 0
    return {'held': waiting, 'requirements_revision': latest['revision'], 'logical_turn_id': turn,
            'answers_since_update': count, 'answers_per_update': ANSWERS_PER_UPDATE,
            'can_refresh': waiting and dispatch.get('state') == 'SAVED'
                           and doc.get('status') == 'ready' and not doc.get('attachment')
                           and not doc.get('archived_at')}

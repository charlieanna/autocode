"""AutoResolver's request-only authority; publication never grants execution."""

try:
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_source_scope as source_scope

import copy
import shlex
import subprocess
from pathlib import Path

try:
    from . import autocode_support as support, autocode_operational_information as information
    from .autocode_goals import RESOLVER_PROPOSAL_KEY, RESOLVER_REQUEST_KEY
except ImportError:
    import autocode_support as support
    import autocode_operational_information as information
    from autocode_goals import RESOLVER_PROPOSAL_KEY, RESOLVER_REQUEST_KEY


VERSION = 1
SCOPES = frozenset({'clarification', 'goal_approval', 'permission', 'goal_change',
                    'human_review', 'blocker', 'operational_exhaustion', 'intake'})
PRIVATE = RESOLVER_PROPOSAL_KEY
PUBLIC = RESOLVER_REQUEST_KEY


def _contract(state):
    contract = state.get('goal_contract')
    if not contract:
        return None
    expected = support.digest({key: contract[key] for key in ('task_id', 'revision', 'body')})
    if contract.get('hash') != expected:
        raise ValueError('Cannot authorize a question against an unsealed contract')
    return {'task_id': contract['task_id'], 'revision': contract['revision'],
            'hash': contract['hash'], 'approval_status': contract.get('approval_status'),
            'approval_event': copy.deepcopy(contract.get('approval_event'))}


def _binding(state):
    """Stable state frontier; excludes publication/response bookkeeping itself."""
    source = None
    if state.get('workspace'):
        try:
            source = {'revision': source_scope.snapshot(Path(state['workspace']), state, base_snapshot=support.snapshot)['revision']}
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            source = {'unavailable': type(error).__name__ + ': ' + str(error)}
    interruptions = {'pending': [], 'pause_requested': False}
    if state.get('run_dir') and Path(state['run_dir']).is_dir():
        try:
            try:
                from . import autocode_interventions as inbox
            except ImportError:
                import autocode_interventions as inbox
            interruptions['pending'] = inbox.pending(Path(state['run_dir']))
            interruptions['pause_requested'] = (Path(state['run_dir']) / 'pause-requested').exists()
        except (OSError, RuntimeError, ValueError) as error:
            interruptions['error'] = type(error).__name__ + ': ' + str(error)
    binding = {
        'task_id': state.get('task_id'), 'workspace': state.get('workspace'),
        'run_dir': state.get('run_dir'), 'conversation_id': state.get('conversation_id'),
        'intake_input_hash': state.get('intake_input_hash'),
        'next_stage': state.get('next_stage'),
        'task': state.get('task'), 'contract': _contract(state),
        'current_task': support.digest(state.get('current_task')),
        'settings': support.digest(state.get('settings', {})),
        'answers': support.digest(state.get('answers', {})),
        'user_events': support.digest(state.get('user_events', [])),
        'active_stage': support.digest(state.get('active_stage')),
        'uncertain_artifacts': support.digest(state.get('uncertain_artifacts')),
        'pending_report_repair': support.digest(state.get('pending_report_repair')),
        'interruptions': interruptions,
        'source_snapshot': state.get('source_snapshot'),
        'source_revision': state.get('source_revision'),
        'observed_source': source,
        'validation': support.digest(state.get('validation', {})),
        'human_reviews': support.digest(state.get('human_reviews', {})),
        'planning': support.digest(state.get('planning', {})),
        'failure_history': support.digest(state.get('failure_history', {})),
        'accounting': {key: copy.deepcopy(state.get(key)) for key in (
            'iteration', 'active_seconds', 'milestone_active_seconds',
            'automatic_recoveries_since_resume', 'consecutive_timeout_recoveries',
            'automatic_timeout_recoveries', 'automatic_capacity_recoveries',
            'automatic_permission_recoveries', 'no_progress_batches')},
    }
    return copy.deepcopy(binding)


def queue(state, scope, origin, *, request=None, questions=None, evidence=None,
          status='WAITING_FOR_USER', phase=None, next_stage=None):
    """Stage a nonpublic proposal. Only evaluate() may publish its controls."""
    if scope not in SCOPES or not isinstance(origin, dict) or not origin.get('stage'):
        raise ValueError('A human proposal needs a supported scope and runner-supplied origin')
    if status not in ('WAITING_FOR_USER', 'AWAITING_GOAL_APPROVAL'):
        raise ValueError('Unsupported human publication status')
    proposal = {'version': VERSION, 'scope': scope, 'origin': copy.deepcopy(origin),
                'request': copy.deepcopy(request or {}), 'questions': copy.deepcopy(questions or []),
                'evidence': copy.deepcopy(evidence or {}), 'status': status,
                'phase': phase or status, 'next_stage': next_stage,
                'previous_status': state.get('status')}
    if scope == 'goal_approval' and status != 'AWAITING_GOAL_APPROVAL':
        raise ValueError('Goal approval must keep its own approval boundary')
    if scope != 'goal_approval' and not proposal['questions'] and not proposal['request']:
        raise ValueError('A human proposal must retain its actual question or request')
    previous = state.get(PRIVATE)
    if previous is None:
        key = (state.get(PUBLIC) or {}).get('request_id')
        previous = state.get('resolver', {}).get('human_escalations', {}).get(key, {}).get('identity', {}).get('proposal')
    if previous and {k: v for k, v in previous.items() if k != 'previous_status'} == {
            k: v for k, v in proposal.items() if k != 'previous_status'}:
        proposal['previous_status'] = previous['previous_status']
    # Raw model issuer/receipt-shaped values remain data, never authority.
    state[PRIVATE] = proposal
    state.pop(PUBLIC, None)
    state.pop('user_request', None)
    state['pending_questions'] = []
    state.update(status='RESOLVER_PENDING', phase='RESOLVING')
    if next_stage is not None:
        state['next_stage'] = next_stage
    return copy.deepcopy(proposal)


def internal_questions(state):
    proposal = state.get(PRIVATE) or {}
    return copy.deepcopy(proposal.get('questions', state.get('pending_questions', [])))


def _questions(proposal, key, reason):
    questions = copy.deepcopy(proposal['questions'])
    if not questions and proposal['scope'] != 'goal_approval':
        request = proposal['request']
        questions = [{'id': 'resolver-' + key[:16], 'question': request['decision_needed'],
                      'why': request.get('impact', reason), 'options': request.get('options', []),
                      'proposed_default': ''}]
    return questions


def _evidence_valid(proposal):
    try:
        pins = proposal.get('evidence', {}).get('hashes', {})
        if not isinstance(pins, dict):
            return False
        return all(isinstance(path, str) and isinstance(digest, str)
                   and Path(path).is_file() and support.file_hash(Path(path)) == digest
                   for path, digest in pins.items())
    except (OSError, ValueError, TypeError):
        return False


def _decision(state, proposal):
    """Evaluate permitted recovery or established human-only authority."""
    scope = proposal['scope']
    # Asking for intent/permission is valid before a workspace exists. Its
    # absence is bound in the receipt; artifact review and joint finalization
    # below still require actual current source evidence. Asking is not execution.
    contract = state.get('goal_contract') or {}
    body = contract.get('body') or state.get('requirements_body') or {}
    if scope == 'goal_approval':
        token = f"r{contract.get('revision')}:{contract.get('hash')}"
        planning = state.get('planning') or {}
        if not contract or body.get('open_blocking_questions'):
            return 'defer', 'Resolve the draft questions before requesting approval'
        if state.get('settings', {}).get('joint_planning') and planning.get('final_token') != token:
            return 'defer', 'Independent planning must finish before requesting approval'
        if state.get('settings', {}).get('joint_planning'):
            # An adaptive-planning review that approved the draft is that plan's final review.
            final_stage = ('plan_finalize' if state.get('settings', {}).get('planning_flow') == 'v2'
                           else (planning.get('adaptive') or {}).get('final_stage') or 'astra_finalize')
            final = planning.get('reports', {}).get(final_stage, {})
            record = next((row for row in reversed(state.get('stages', []))
                           if (row.get('original_stage') or row.get('stage')) == final_stage
                           and row.get('output') == final.get('output') and not row.get('rejected')), None)
            source = _binding(state)['observed_source'] or {}
            if not record or not source.get('revision') or record.get('source_revision') != source['revision']:
                return 'defer', 'Final planning evidence must match the current source before approval'
        if contract.get('approval_status') != 'draft':
            return 'reject', 'This contract does not require a new approval'
        return 'escalate', 'Only the user can approve this exact sealed plan'
    if scope == 'clarification':
        questions = proposal['questions']
        declared = body.get('open_blocking_questions') or []
        if not questions or any(q not in declared for q in questions):
            return 'reject', 'Clarification must match the validated draft questions'
        for question in questions:
            answer = state.get('answers', {}).get(question.get('id'))
            if (answer and answer.get('actor') == 'user_cli' and answer in state.get('user_events', [])
                    and answer.get('question') == question):
                return 'defer', 'An authenticated answer already exists; resolve it internally first'
        return 'escalate', 'The validated draft requires user intent absent from authenticated saved answers'
    if scope in ('permission', 'goal_change'):
        request = proposal['request']
        if request.get('kind') != scope or not request.get('decision_needed') or not request.get('impact'):
            return 'reject', 'Material decisions need the exact structured request and impact'
        return 'escalate', 'The requested change is outside autonomous permission or contract authority'
    if scope == 'human_review':
        requested = set(proposal['request'].get('criteria') or [])
        required = {row['id'] for row in body.get('acceptance_criteria', []) if row.get('human_review')}
        if not requested or not requested <= required or not proposal['evidence'].get('review_token'):
            return 'reject', 'Human review requires declared criteria and their current artifact token'
        source = _binding(state)['observed_source'] or {}
        validation = state.get('validation') or {}
        try:
            from . import autocode_goals as goals
        except ImportError:
            import autocode_goals as goals
        technical_pass = (validation.get('verdict') == 'PASS' or
                          all(goals.human_only_pending_validation(state, validation, cid) for cid in requested))
        if (not technical_pass or not source.get('revision')
                or validation.get('source_revision') != source['revision']
                or not _evidence_valid({'evidence': {'hashes': validation.get('evidence_hashes', {})}})):
            return 'defer', 'Current independent technical validation must precede human artifact review'
        return 'escalate', 'The approved acceptance boundary explicitly requires a human review'
    if scope == 'intake':
        if (proposal['origin'].get('stage') != 'resolver_intake' or not state.get('conversation_id')
                or not state.get('intake_input_hash')):
            return 'reject', 'Only the resolver intake adapter can publish a project-free question'
        return 'escalate', 'The resolver needs user intent before a project or contract can be established'
    evidence = proposal['evidence']
    if evidence.get('recovery_available'):
        return 'recover', 'A permitted bounded recovery remains; do not ask the user yet'
    # These references must be constructed from recorded runner evidence, not
    # copied from model output. Their concrete pins are rechecked by the caller.
    if (proposal['origin'].get('stage') == 'astra_resolve'
            and evidence.get('diagnosis') and evidence.get('output')):
        resolved = next((row for row in reversed(state.get('stages', []))
                         if row.get('stage') == 'astra_resolve' and row.get('output') == evidence['output']
                         and row.get('exit_code') == 0 and not row.get('rejected')
                         and not row.get('changed_files')), None)
        if resolved:
            return 'escalate', 'The read-only AutoResolver diagnosed the blocker but could not resolve it'
    recorded = state.get('resolver', {}).get('operational_diagnostics', [])
    receipt = state.get('resolver', {}).get('operational_receipts', {}).get(evidence.get('resolver_receipt_id'))
    if (evidence.get('resolver_receipt_id') in recorded and isinstance(receipt, dict)
            and receipt.get('runner_owned') and receipt.get('stage') == 'resolver'
            and receipt.get('decision', {}).get('action') == 'hold'
            and receipt.get('receipt', {}).get('evaluation_binding') == _binding(state)):
        return 'escalate', 'Recorded AutoResolver recovery is exhausted or has no safe permitted next action'
    return 'defer', 'This blocker needs internal AutoResolver investigation before any human request'


def evaluate(state):
    """Serialized writer boundary. Readers must use projection(), never this."""
    proposal = state.get(PRIVATE)
    if not proposal:
        return 'none'
    if proposal.get('version') != VERSION or proposal.get('scope') not in SCOPES:
        raise ValueError('Malformed internal human proposal')
    if not _evidence_valid(proposal):
        raise ValueError('Resolver request evidence changed before publication')
    binding = _binding(state)
    if any(binding['interruptions'].values()):
        state.setdefault('resolver', {})['human_disposition'] = {
            'action': 'defer', 'reason': 'Reconcile accepted input or the requested pause before asking for a decision'}
        return 'defer'
    action, reason = _decision(state, proposal)
    if action != 'escalate':
        state.setdefault('resolver', {})['human_disposition'] = {'action': action, 'reason': reason}
        return action
    identity = {'version': VERSION, 'issuer': 'resolver', 'binding': binding,
                'proposal': proposal, 'action': action, 'reason': reason}
    key = support.digest(identity)
    ledger = state.setdefault('resolver', {}).setdefault('human_escalations', {})
    existing = ledger.get(key)
    if existing and existing.get('status') == 'consumed':
        state.pop(PRIVATE, None)
        state.setdefault('resolver', {})['human_disposition'] = {
            'action': 'hold', 'reason': 'This exact request was already answered; reevaluate the saved response internally'}
        return 'consumed'
    _publish(state, identity)
    return action


def _publish(state, identity):
    """Publish an evaluated identity as this run's one answerable request."""
    key = support.digest(identity)
    proposal = identity['proposal']
    ledger = state['resolver']['human_escalations']
    existing = ledger.get(key)
    entry = {'identity': identity, 'receipt_hash': support.digest(identity),
             'status': 'pending', 'issued_at': existing['issued_at'] if existing else support.now()}
    ledger[key] = entry
    # Publication replaces any live request; a run has at most one answerable
    # request, and retired ones must not linger as pending forever.
    for other_key, other in ledger.items():
        if other_key != key and other.get('status') == 'pending':
            other.update(status='superseded', superseded_at=support.now(),
                         superseded_reason='AutoResolver published a newer request for this run')
    questions = _questions(proposal, key, identity['reason'])
    public = {'version': VERSION, 'issuer': 'resolver', 'request_id': key,
              'request_token': support.digest({'request_id': key, 'binding': identity['binding']}),
              'scope': proposal['scope'], 'receipt_hash': entry['receipt_hash'],
              'questions': questions, 'request': copy.deepcopy(proposal['request']), 'reason': identity['reason']}
    state[PUBLIC] = public
    state['pending_questions'] = copy.deepcopy(questions)
    if proposal['request']:
        state['user_request'] = copy.deepcopy(proposal['request'])
    else:
        state.pop('user_request', None)
    state.update(status=proposal['status'], phase=proposal['phase'])
    if proposal.get('next_stage') is not None:
        state['next_stage'] = proposal['next_stage']
    state.pop(PRIVATE, None)
    return copy.deepcopy(public)


def rebind_stale(state, request_id, request_token):
    """Re-publish a stranded request so an operator decision applies once.

    The operator displayed a request, the saved state then changed (for example
    a tree edit while paused), and their decision command now carries a token
    bound to the old frontier. The token is authentic for exactly that request,
    so the identical request is re-published under the current binding and the
    decision applies in the same invocation. A request already re-published
    with identical content by the writer boundary is accepted as the one the
    operator answered. Consumed, forged, diverged, interruption-shadowed or
    evidence-invalid requests are never re-bound.
    """
    ledger = (state.get('resolver', {}) or {}).get('human_escalations') or {}
    entries = ([request_id] if request_id in ledger else []) + [
        key for key in ledger if key != request_id]
    for key in entries:
        entry = ledger.get(key) or {}
        identity = entry.get('identity') or {}
        if (identity.get('issuer') != 'resolver' or support.digest(identity) != key
                or not request_token
                or support.digest({'request_id': key, 'binding': identity.get('binding')}) != request_token):
            continue
        if entry.get('status') == 'consumed':
            return None
        live = current(state)
        if entry.get('status') == 'superseded':
            proposal = identity['proposal']
            live_entry = ledger.get((live or {}).get('request_id')) or {}
            live_proposal = (live_entry.get('identity') or {}).get('proposal') or {}
            # The operator answered the question text and request body; a
            # republication that preserves exactly those is the same ask.
            # Artifact review additionally pins its evidence identity.
            same = (live is not None and live_entry.get('status') == 'pending'
                    and live_proposal.get('scope') == proposal.get('scope')
                    and live_proposal.get('request') == proposal.get('request')
                    and live_proposal.get('questions') == proposal.get('questions')
                    and (proposal.get('scope') != 'human_review'
                         or live_proposal.get('evidence') == proposal.get('evidence')))
            if same:
                return copy.deepcopy(live)
            return None
        if state.get('status') not in ('WAITING_FOR_USER', 'AWAITING_GOAL_APPROVAL'):
            return None
        binding = _binding(state)
        if any(binding['interruptions'].values()) or not _evidence_valid(identity['proposal']):
            return None
        reborn = {'version': VERSION, 'issuer': 'resolver', 'binding': binding,
                  'proposal': identity['proposal'], 'action': identity['action'],
                  'reason': identity['reason']}
        entry.update(status='superseded', superseded_at=support.now(),
                     superseded_reason='Re-bound to the current run state for the operator decision')
        return _publish(state, reborn)
    return None


def current(state):
    """Read-only receipt verification; an issuer label alone grants nothing."""
    public = state.get(PUBLIC)
    if not isinstance(public, dict) or public.get('version') != VERSION or public.get('issuer') != 'resolver':
        return None
    try:
        entry = state.get('resolver', {}).get('human_escalations', {}).get(public.get('request_id'))
    except (AttributeError, TypeError):
        return None
    if not isinstance(entry, dict) or entry.get('status') != 'pending':
        return None
    try:
        identity = entry['identity']
        key = support.digest(identity)
        if (identity.get('issuer') != 'resolver' or identity.get('action') != 'escalate'
                or key != public['request_id'] or entry['receipt_hash'] != key
                or state.get('status') != identity['proposal']['status']
                or public['receipt_hash'] != key or identity['binding'] != _binding(state)
                or public['request_token'] != support.digest({'request_id': key, 'binding': identity['binding']})
                or public['scope'] != identity['proposal']['scope']
                or public['reason'] != identity['reason']
                or public['request'] != identity['proposal']['request']
                or not _evidence_valid(identity['proposal'])
                or public['questions'] != _questions(identity['proposal'], key, identity['reason'])
                or public['questions'] != state.get('pending_questions', [])
                or public['request'] != state.get('user_request', {})):
            return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return copy.deepcopy(public)


def projection(state):
    public = current(state)
    saved_status = state.get('status')
    status = ('RESOLVER_PENDING' if not public and saved_status in ('WAITING_FOR_USER', 'AWAITING_GOAL_APPROVAL')
              else saved_status)
    return {'status': status, 'saved_status': saved_status,
            'human_request_authorized': public is not None,
            'human_escalation': public,
            'pending_questions': public['questions'] if public else [],
            'user_request': public['request'] if public else None}


def stale_request_message(state):
    """How to answer after the displayed request stopped being current."""
    live = current(state)
    if live:
        return ('This request is out of date; a newer AutoResolver request is active. '
                'Answer request ' + live['request_id'] + ' with its own token.')
    parts = ['autocode']
    if state.get('workspace'):
        parts.append('--workspace ' + shlex.quote(str(state['workspace'])))
    if state.get('run_dir'):
        parts.append('--run-dir ' + shlex.quote(str(state['run_dir'])))
    parts.append('--no-chat')
    return ('This request is out of date (the run or AutoCode changed since it was shown). '
            'Run `' + ' '.join(parts) + '` to publish a fresh request, then answer with its token.')


def require_response(state, request_id, request_token):
    public = current(state)
    if public and public['request_id'] == request_id and public['request_token'] == request_token:
        return public
    # A saved request whose token the caller reproduced exactly is stale, not
    # forged: the frontier moved after it was displayed, or a newer request
    # superseded it.
    entry = state.get('resolver', {}).get('human_escalations', {}).get(request_id) or {}
    identity = entry.get('identity') or {}
    if (entry.get('status') in ('pending', 'superseded')
            and identity.get('issuer') == 'resolver'
            and support.digest({'request_id': request_id, 'binding': identity.get('binding')}) == request_token):
        raise ValueError(stale_request_message(state))
    raise ValueError('Human response requires the exact current AutoResolver request and token')


def supersede_operational(state, reason):
    """Withdraw only a resolver-issued operational question after explicit repair."""
    public = state.get(PUBLIC) or {}
    key = public.get('request_id')
    entry = state.get('resolver', {}).get('human_escalations', {}).get(key)
    if not isinstance(entry, dict) or entry.get('status') != 'pending':
        return False
    identity = entry.get('identity') or {}
    proposal = identity.get('proposal') or {}
    if (support.digest(identity) != key or identity.get('issuer') != 'resolver'
            or proposal.get('scope') not in ('operational_exhaustion', 'blocker')):
        return False
    issued = _questions(proposal, key, identity['reason'])
    state['pending_questions'] = [q for q in state.get('pending_questions', []) if q not in issued]
    if state.get('user_request') == proposal['request']:
        state.pop('user_request', None)
    entry.update(status='superseded', superseded_at=support.now(), superseded_reason=reason)
    state.pop(PUBLIC, None)
    if state.get('status') == 'WAITING_FOR_USER' and not state['pending_questions']:
        previous = proposal['origin'].get('pause_status') or proposal.get('previous_status')
        state.update(status=previous if str(previous).startswith('PAUSED_') else 'PAUSED_RESOLVER',
                     phase='PAUSED_OR_BLOCKED')
    return True


def respond_operational(state, request_id, request_token, action, text=''):
    """Store human input for the resolver; this is not retry or approval consent."""
    old = state.get('resolver', {}).get('human_escalations', {}).get(request_id, {})
    if old.get('status') == 'consumed':
        response = old.get('response') or {}
        if (response.get('request_token') == request_token and response.get('action') == action
                and response.get('text') == text and response.get('actor') == 'user_cli'):
            return copy.deepcopy(response)
        raise ValueError('This AutoResolver request was already answered with a different response')
    public = require_response(state, request_id, request_token)
    if public['scope'] not in ('operational_exhaustion', 'blocker'):
        raise ValueError('Use the existing exact plan, permission or artifact-review action for this decision')
    if action not in ('provide_information', 'leave_paused') or (action == 'provide_information' and not text.strip()):
        raise ValueError('Operational response must supply corrective information or leave the run paused')
    entry = state['resolver']['human_escalations'][request_id]
    event = {'actor': 'user_cli', 'request_id': request_id, 'request_token': request_token,
             'action': action, 'text': text, 'at': support.now()}
    entry.update(status='consumed', response=copy.deepcopy(event))
    state['resolver'].setdefault('human_responses', []).append(event)
    state['resolver']['pending_human_response'] = copy.deepcopy(event)
    state.pop(PUBLIC, None)
    state.pop('user_request', None)
    state['pending_questions'] = []
    proposal = entry['identity']['proposal']
    previous = proposal['origin'].get('pause_status') or proposal['previous_status']
    state.update(status=previous if str(previous).startswith('PAUSED_') else 'PAUSED_RESOLVER',
                 phase='PAUSED_OR_BLOCKED',
                 stop_reason='AutoResolver received the human response; no execution, approval or additional allowance was authorized.')
    return copy.deepcopy(event)


def review_operational_response(state):
    """Consume information without manufacturing authority for another attempt.

    The response itself stays a hold. Corrective information on an operational_exhaustion
    request is also scheduled for one AutoResolver re-evaluation at the next resume (#486,
    autocode_operational_information); leave_paused is final.
    """
    resolver = state.get('resolver') or {}
    event = resolver.get('pending_human_response')
    if not event:
        return None
    entry = resolver.get('human_escalations', {}).get(event.get('request_id'), {})
    if (entry.get('status') != 'consumed' or entry.get('response') != event
            or event.get('actor') != 'user_cli'):
        raise ValueError('Operational guidance lacks its consumed AutoResolver request')
    resolution = {'request_id': event['request_id'], 'at': support.now(), 'action': 'hold',
                  'reason': 'Corrective information is retained; this response did not authorize another attempt or new limits.',
                  'response': copy.deepcopy(event)}
    resolver.setdefault('human_response_resolutions', {})[event['request_id']] = resolution
    state.setdefault('recovery_context', {})['human_information'] = copy.deepcopy(event)
    resolver.pop('pending_human_response', None)
    resolver['human_response_frontier'] = {'binding': _binding(state), 'pause_status': state['status'],
                                         'request_id': event['request_id']}
    if information.schedule(state, event, entry, resolver['human_response_frontier']):
        resolution['reason'] = ('Corrective information is retained for one AutoResolver re-evaluation at the next '
                                'resume; this response did not authorize another attempt or new limits.')
    return copy.deepcopy(resolution)


def response_holds_current_frontier(state):
    frontier = state.get('resolver', {}).get('human_response_frontier') or {}
    return (frontier.get('pause_status') == state.get('status')
            and frontier.get('binding') == _binding(state))

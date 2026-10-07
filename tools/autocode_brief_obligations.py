"""Authenticate and retain bounded original-brief observations in the goal body.

Only reviewed_body writes body.brief_acceptance. Draft/approval/replay/completion
read it; the manifest and reviewer provenance share the existing contract hash.
There is no new run-state key or controller. The compiler only receives genuine
human sources, never delegated defaults or model-authored criteria.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

try:
    from . import autocode_brief_acceptance as acceptance, autocode_conversation as conversation
    from . import autocode_util as util
except ImportError:
    import autocode_brief_acceptance as acceptance
    import autocode_conversation as conversation
    import autocode_util as util

PROPOSALS_SCHEMA = acceptance.PROPOSALS_SCHEMA
REVIEW_STAGES = frozenset({'astra_challenge', 'astra_finalize', 'plan_finalize'})
KEY = 'brief_acceptance'


def sources(state):
    task = state.get('task') or ''
    texts = conversation.task_user_texts(task) if task else None
    records = [{'id': f'task:{i}', 'kind': 'conversation_user' if texts is not None else 'task', 'text': text}
               for i, text in enumerate(texts if texts is not None else [task]) if text]
    # Preserve the actual human-event order. A replacement cannot point back to
    # an older answer or revive an earlier declaration through a cycle.
    feedback = state.get('brief_feedback') or []
    answers = state.get('answers') or {}
    for event in state.get('user_events') or []:
        if not isinstance(event, dict):
            continue
        if (event in feedback and event.get('kind') == 'brief_feedback'
                and event.get('actor') in ('user_cli', 'user_intervention')
                and event.get('id') and event.get('text')):
            records.append({'id': 'feedback:' + event['id'],
                            'kind': 'user_intervention' if event['actor'] == 'user_intervention' else 'user_feedback',
                            'text': event['text']})
        elif (event.get('kind') == 'answer' and event.get('actor') == 'user_cli'
                and event.get('question_id') and answers.get(event['question_id']) == event and event.get('text')):
            records.append({'id': 'answer:' + event['question_id'], 'kind': 'user_answer', 'text': event['text']})
    return records


def inventory(state):
    return [{**row, 'declaration_hash': acceptance.digest(row)} for row in acceptance.inventory(sources(state))]


def _wrapper(body):
    return (body or {}).get(KEY)


def _past_observations(state):
    rows = {}
    for contract in [*(state.get('contract_history') or []), state.get('goal_contract') or {}]:
        wrapper = _wrapper(contract.get('body')) or {}
        for row in (wrapper.get('manifest') or {}).get('observations', []):
            rows[row['hash']] = row
    return rows


def _amendments(state, changes):
    """Derive inactive declarations from exact, genuine source amendments."""
    declarations = {row['id']: row for row in acceptance.inventory(sources(state))}
    human_sources = sources(state)
    records = {row['id']: row for row in human_sources}
    order = {row['id']: index for index, row in enumerate(human_sources)}
    past = _past_observations(state)
    inactive, normalized, old_to_new = [], [], []
    seen = set()
    for change in changes:
        if not isinstance(change, dict) or set(change) != {'previous_hash', 'declaration_id', 'source_event_id'}:
            raise ValueError('Brief observation changes need the exact previous hash, declaration and human source')
        previous = past.get(change['previous_hash'])
        old = previous['declaration'] if previous else next(
            (row for row in declarations.values() if acceptance.digest(row) == change['previous_hash']), None)
        new = declarations.get(change['declaration_id'])
        source = records.get(change['source_event_id'])
        if (not old or not new or not source or source['kind'] == 'task'
                or new['source_id'] != source['id'] or old['id'] in seen
                or old['id'] == new['id'] or old['program'] != new['program']
                or old['observe_argv'] != new['observe_argv']
                or order.get(old['source_id'], -1) >= order[source['id']]):
            raise ValueError('A brief observation replacement needs an actual human amendment to that exact CLI')
        text = source['text']
        invocation = old['program'] + ' ' + ' '.join(old['observe_argv'])
        if (not re.search(r'\b(?:replace|instead|change|revise)\b', text, re.I)
                or not (old['literal'] in text or invocation in text)):
            raise ValueError('The saved human source does not explicitly replace this original output obligation')
        seen.add(old['id'])
        inactive.append(old['id'])
        normalized.append(copy.deepcopy(change))
        old_to_new.append((previous or {'declaration': old, 'hash': change['previous_hash']}, new))
    return inactive, normalized, old_to_new


def _verify_review(review, manifest):
    if not isinstance(review, dict) or set(review) != {'stage', 'events', 'events_sha256', 'output', 'output_sha256', 'proposals_sha256'}:
        raise ValueError('Original-brief observations need retained independent Plan Reviewer provenance')
    path = Path(review['events'])
    output = Path(review['output'])
    proposals = [row['proposal'] for row in manifest['observations']]
    proposal_hash = acceptance.digest(sorted(proposals, key=lambda row: row['declaration_id']))
    if (review['stage'] not in REVIEW_STAGES or path.is_symlink() or not path.is_file()
            or util.file_hash(path) != review['events_sha256']
            or output.is_symlink() or not output.is_file() or util.file_hash(output) != review['output_sha256']
            or proposal_hash != review['proposals_sha256']):
        raise ValueError('Original-brief Plan Reviewer provenance is missing or changed')
    declarations = {row['declaration']['id']: row['declaration'] for row in manifest['observations']}
    raw = util.read_object(output).get('brief_observations') or []
    try:
        sealed = [acceptance.normalized_proposal(declarations[row['declaration_id']], row)[0] for row in raw]
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError('Original-brief Plan Reviewer provenance is missing or changed') from error
    if acceptance.digest(sorted(sealed, key=lambda row: row['declaration_id'])) != proposal_hash:
        raise ValueError('Original-brief Plan Reviewer provenance is missing or changed')


def validate_body(state, body, *, ready=False):
    wrapper = _wrapper(body)
    declared = acceptance.inventory(sources(state))
    if wrapper is None:
        if ready and declared:
            raise ValueError('The original brief needs independent Plan Reviewer output observations before approval')
        return
    if not isinstance(wrapper, dict) or set(wrapper) != {'manifest', 'review', 'amendments'}:
        raise ValueError('Invalid protected original-brief observation record')
    inactive, _, _ = _amendments(state, wrapper['amendments'])
    human_sources = sources(state)
    manifest = wrapper['manifest']
    previous = _wrapper((state.get('goal_contract') or {}).get('body'))
    if (not ready and wrapper == previous
            and manifest.get('inventory_hash') != acceptance.digest(acceptance.inventory(human_sources))):
        # A new genuine human event can add obligations while the Planner drafts
        # a revision. Retain and authenticate the old binding; only the final
        # Reviewer may replace it, and approval always requires the full inventory.
        retained = next((human_sources[:length] for length in range(1, len(human_sources))
                         if acceptance.digest(acceptance.inventory(human_sources[:length])) == manifest['inventory_hash']), None)
        if retained is None:
            raise ValueError('The retained original-brief source inventory changed')
        manifest = acceptance.verify(retained, manifest, inactive=inactive)
    else:
        manifest = acceptance.verify(human_sources, manifest, inactive=inactive)
    ids = {row['id'] for row in body.get('acceptance_criteria', [])}
    if any(not set(row['proposal']['criterion_ids']) <= ids for row in manifest['observations']):
        raise ValueError('A brief observation refers to an unknown acceptance criterion')
    _verify_review(wrapper['review'], manifest)


def reviewed_body(state, body, proposals, record, *, changes=()):
    result = copy.deepcopy(body)
    previous = _wrapper((state.get('goal_contract') or {}).get('body'))
    if not acceptance.inventory(sources(state)) and previous is None:
        if result.get(KEY) is not None:
            raise ValueError('A model cannot invent an original-brief observation record')
        return result
    if record.get('stage') not in REVIEW_STAGES:
        raise ValueError('Only the independent Plan Reviewer can originate brief observations')
    events = Path(record.get('events') or '')
    if events.is_symlink() or not events.is_file():
        raise ValueError('Original-brief observations require the actual Plan Reviewer execution evidence')
    output = Path(record.get('output') or '')
    if output.is_symlink() or not output.is_file():
        raise ValueError('Original-brief observations require the actual Plan Reviewer report')
    amendments = [*(previous or {}).get('amendments', []), *changes]
    inactive, amendments, replacements = _amendments(state, amendments)
    manifest = acceptance.bind(sources(state), proposals, inactive=inactive)
    if previous:
        by_declaration = {row['declaration']['id']: row for row in manifest['observations']}
        successors = {old['declaration']['id']: new['id'] for old, new in replacements}
        authorizations = []
        for old in previous['manifest']['observations']:
            ident = old['declaration']['id']
            if ident not in successors:
                continue
            while ident in successors:
                ident = successors[ident]
            new = by_declaration[ident]
            authorizations.append({'previous_hash': old['hash'], 'replacement_hash': new['hash'],
                                   'replacement_source_sha256': new['declaration']['source_sha256']})
        acceptance.preserve(previous['manifest'], manifest, replacements=authorizations)
    review = {'stage': record['stage'], 'events': str(events), 'events_sha256': util.file_hash(events),
              'output': str(output), 'output_sha256': util.file_hash(output),
              'proposals_sha256': acceptance.digest(sorted([row['proposal'] for row in manifest['observations']],
                                                           key=lambda row: row['declaration_id']))}
    result[KEY] = {'manifest': manifest, 'review': review, 'amendments': amendments}
    validate_body(state, result)
    return result


def prepare_body(state, body, origin, record=None):
    result = copy.deepcopy(body)
    previous = _wrapper((state.get('goal_contract') or {}).get('body'))
    supplied = _wrapper(result)
    if supplied == previous:
        return result
    if supplied is None and previous is not None:
        result[KEY] = copy.deepcopy(previous)
        return result
    if origin in {'astra_finalize', 'plan_finalize', 'adaptive_review_approval'}:
        validate_body(state, result)
        if supplied is not None and (record or {}).get('stage') != supplied['review']['stage']:
            raise ValueError('Original-brief observations do not belong to this Plan Reviewer execution')
        return result
    raise ValueError('A Planner, Builder or edited contract cannot replace independently derived brief observations')


def whole_product_claim(state, report, *, progressive_context=None):
    """Select full observations only for an evidenced current whole-product claim.

    Task-local PASS and progressive contributions are insufficient. The existing
    controller separately authenticates report identity, source and human-only
    pending proof before calling clean replay.
    """
    criteria = (state.get('goal_contract') or {}).get('body', {}).get('acceptance_criteria') or []
    required = {row['id'] for row in criteria if not row.get('human_review')}
    rows = report.get('criterion_results') or []
    passed = {row['id'] for row in rows if row.get('status') == 'PASS' and row.get('evidence_refs')}
    if not required or not required <= passed or len({row['id'] for row in rows}) != len(rows):
        return False
    if progressive_context:
        due = {cid for check in progressive_context.get('required_checks', [])
               if check.get('relation') == 'fully_verify' for cid in check.get('criterion_ids', [])}
        if progressive_context.get('outstanding_criteria') or not required <= due:
            return False
    return True


def observations(state, *, progressive_context=None, all_observations=False):
    """Canonical cases due in this task; completion independently requests all."""
    body = (state.get('goal_contract') or {}).get('body') or {}
    validate_body(state, body, ready=True)
    wrapper = _wrapper(body)
    if wrapper is None:
        return []
    rows = wrapper['manifest']['observations']
    if all_observations:
        return rows
    if progressive_context:
        selected = {cid for check in progressive_context.get('required_checks', [])
                    if check.get('relation') == 'fully_verify' for cid in check.get('criterion_ids', [])}
    else:
        selected = set((state.get('current_task') or {}).get('acceptance_criteria') or [])
        if not selected:
            return rows
    return [row for row in rows if selected.intersection(row['proposal']['criterion_ids'])]


def commands(state, *, python='python3', timeout=15, progressive_context=None, all_observations=False):
    selected = observations(state, progressive_context=progressive_context, all_observations=all_observations)
    wrapper = _wrapper((state.get('goal_contract') or {}).get('body'))
    if wrapper is None:
        return []
    inactive, _, _ = _amendments(state, wrapper['amendments'])
    generated = acceptance.commands(sources(state), wrapper['manifest'], inactive=inactive, python=python, timeout=timeout)
    wanted = {row['hash'] for row in selected}
    return [command for row, command in zip(wrapper['manifest']['observations'], generated) if row['hash'] in wanted]

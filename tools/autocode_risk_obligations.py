"""Protect source-linked lifecycle protocols and initial public API provenance.

Only reviewed_body writes body.risk_acceptance. Approval, replay and completion
read it under the existing contract hash. Targets come from retained initial
source, not an API the Builder invents while implementing the product.
"""
from __future__ import annotations

import copy
import re
import uuid
from pathlib import Path

try:
    from . import autocode_risk_acceptance as acceptance, autocode_risk_targets as targets
    from . import autocode_brief_obligations as human, autocode_risk_protocols as protocols
    from . import autocode_util as util
except ImportError:
    import autocode_risk_acceptance as acceptance
    import autocode_risk_targets as targets
    import autocode_brief_obligations as human
    import autocode_risk_protocols as protocols
    import autocode_util as util

PROPOSALS_SCHEMA = acceptance.PROPOSALS_SCHEMA
REVIEW_STAGES = human.REVIEW_STAGES
KEY = 'risk_acceptance'


def sources(state):
    return human.sources(state)


def _target_rows(wrapper):
    record = wrapper.get('targets') or {}
    if set(record) != {'artifact', 'sha256'}:
        raise ValueError('Risk observations need the retained initial public API inventory')
    path = Path(record['artifact'])
    if path.is_symlink() or not path.is_file() or util.file_hash(path) != record['sha256']:
        raise ValueError('The retained initial public API inventory is missing or changed')
    return targets.verify(util.read_object(path))['targets']


def inventory(state):
    wrapper = _wrapper((state.get('goal_contract') or {}).get('body'))
    rows = _target_rows(wrapper) if wrapper else []
    declared = acceptance.inventory(sources(state), rows)
    if declared and not wrapper:
        rows = targets.capture(state['workspace'])['targets']
        declared = acceptance.inventory(sources(state), rows)
    return [{**row, 'declaration_hash': acceptance.digest(row)} for row in declared]


def _wrapper(body):
    return (body or {}).get(KEY)


def _past_observations(state):
    rows = {}
    for contract in [*(state.get('contract_history') or []), state.get('goal_contract') or {}]:
        wrapper = _wrapper(contract.get('body')) or {}
        for row in (wrapper.get('manifest') or {}).get('observations', []):
            rows[row['hash']] = row
    return rows


def _amendments(state, changes, public_targets):
    """Derive inactive declarations from exact, genuine source amendments."""
    declarations = {row['id']: row for row in acceptance.inventory(sources(state), public_targets)}
    human_sources = sources(state)
    records = {row['id']: row for row in human_sources}
    order = {row['id']: index for index, row in enumerate(human_sources)}
    past = _past_observations(state)
    inactive, normalized, old_to_new = [], [], []
    seen = set()
    for change in changes:
        if not isinstance(change, dict) or set(change) != {'previous_hash', 'declaration_id', 'source_event_id'}:
            raise ValueError('Risk observation changes need the exact previous hash, declaration and human source')
        previous = past.get(change['previous_hash'])
        old = previous['declaration'] if previous else next(
            (row for row in declarations.values() if acceptance.digest(row) == change['previous_hash']), None)
        new = declarations.get(change['declaration_id'])
        source = records.get(change['source_event_id'])
        if (not old or not new or not source or source['kind'] == 'task'
                or new['source_id'] != source['id'] or old['id'] in seen
                or old['id'] == new['id'] or old['class_name'] != new['class_name']
                or old['protocol'] != new['protocol']
                or order.get(old['source_id'], -1) >= order[source['id']]):
            raise ValueError('A risk observation replacement needs an actual human amendment to that exact public API')
        text = source['text']
        invocation = old['constructor'] + '(path)'
        if (not re.search(r'\b(?:replace|instead|change|revise)\b', text, re.I)
                or not (old['source_quote'] in text or invocation in text or old['class_name'] in text)):
            raise ValueError('The saved human source does not explicitly replace this original lifecycle obligation')
        seen.add(old['id'])
        inactive.append(old['id'])
        normalized.append(copy.deepcopy(change))
        old_to_new.append((previous or {'declaration': old, 'hash': change['previous_hash']}, new))
    return inactive, normalized, old_to_new


def _verify_review(review, manifest):
    if not isinstance(review, dict) or set(review) != {'stage', 'events', 'events_sha256', 'output', 'output_sha256', 'proposals_sha256'}:
        raise ValueError('Original-risk observations need retained independent Plan Reviewer provenance')
    path = Path(review['events'])
    output = Path(review['output'])
    proposals = [row['proposal'] for row in manifest['observations']]
    proposal_hash = acceptance.digest(sorted(proposals, key=lambda row: row['declaration_id']))
    if (review['stage'] not in REVIEW_STAGES or path.is_symlink() or not path.is_file()
            or util.file_hash(path) != review['events_sha256']
            or output.is_symlink() or not output.is_file() or util.file_hash(output) != review['output_sha256']
            or proposal_hash != review['proposals_sha256']
            or acceptance.digest(sorted(util.read_object(output).get('risk_observations') or [],
                                        key=lambda row: row['declaration_id'])) != proposal_hash):
        raise ValueError('Risk lifecycle Plan Reviewer provenance is missing or changed')


def validate_body(state, body, *, ready=False):
    wrapper = _wrapper(body)
    declared = acceptance.inventory(sources(state), [])
    if wrapper is None:
        if ready and declared:
            raise ValueError('The original brief needs independent Plan Reviewer lifecycle observations before approval')
        return
    if not isinstance(wrapper, dict) or set(wrapper) != {'manifest', 'review', 'amendments', 'targets'}:
        raise ValueError('Invalid protected original-risk observation record')
    public_targets = _target_rows(wrapper)
    inactive, _, _ = _amendments(state, wrapper['amendments'], public_targets)
    human_sources = sources(state)
    manifest = wrapper['manifest']
    previous = _wrapper((state.get('goal_contract') or {}).get('body'))
    if (not ready and wrapper == previous
            and manifest.get('inventory_hash') != acceptance.digest(acceptance.inventory(human_sources, public_targets))):
        # A new genuine human event can add obligations while the Planner drafts
        # a revision. Retain and authenticate the old binding; only the final
        # Reviewer may replace it, and approval always requires the full inventory.
        retained = next((human_sources[:length] for length in range(1, len(human_sources))
                         if acceptance.digest(acceptance.inventory(human_sources[:length], public_targets)) == manifest['inventory_hash']), None)
        if retained is None:
            raise ValueError('The retained risk lifecycle source inventory changed')
        manifest = acceptance.verify(retained, manifest, public_targets=public_targets, inactive=inactive)
    else:
        manifest = acceptance.verify(human_sources, manifest, public_targets=public_targets, inactive=inactive)
    ids = {row['id'] for row in body.get('acceptance_criteria', [])}
    if any(not set(row['proposal']['criterion_ids']) <= ids for row in manifest['observations']):
        raise ValueError('A risk observation refers to an unknown acceptance criterion')
    _verify_review(wrapper['review'], manifest)


def reviewed_body(state, body, proposals, record, *, changes=()):
    result = copy.deepcopy(body)
    previous = _wrapper((state.get('goal_contract') or {}).get('body'))
    if not acceptance.inventory(sources(state), []) and previous is None:
        if result.get(KEY) is not None:
            raise ValueError('A model cannot invent an original-risk observation record')
        return result
    if record.get('stage') not in REVIEW_STAGES:
        raise ValueError('Only the independent Plan Reviewer can originate risk observations')
    events = Path(record.get('events') or '')
    if events.is_symlink() or not events.is_file():
        raise ValueError('Original-risk observations require the actual Plan Reviewer execution evidence')
    output = Path(record.get('output') or '')
    if output.is_symlink() or not output.is_file():
        raise ValueError('Original-risk observations require the actual Plan Reviewer report')
    if previous:
        public_targets = _target_rows(previous)
        target_record = copy.deepcopy(previous['targets'])
    else:
        captured = targets.capture(state['workspace'])
        target_file = output.parent / ('risk-targets-' + uuid.uuid4().hex + '.json')
        util.atomic_json(target_file, captured)
        public_targets = captured['targets']
        target_record = {'artifact': str(target_file), 'sha256': util.file_hash(target_file)}
    amendments = [*(previous or {}).get('amendments', []), *changes]
    inactive, amendments, replacements = _amendments(state, amendments, public_targets)
    manifest = acceptance.bind(sources(state), proposals, public_targets=public_targets, inactive=inactive)
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
    result[KEY] = {'manifest': manifest, 'review': review, 'amendments': amendments, 'targets': target_record}
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
            raise ValueError('Original-risk observations do not belong to this Plan Reviewer execution')
        return result
    raise ValueError('A Planner, Builder or edited contract cannot replace independently derived risk observations')



def observations(state, *, progressive_context=None, all_observations=False):
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
    return [row for row in rows if selected.intersection(row['criterion_ids'])]


def commands(state, *, python='python3', timeout=30, progressive_context=None, all_observations=False):
    return protocols.commands(observations(state, progressive_context=progressive_context,
                                         all_observations=all_observations), python=python, timeout=timeout)

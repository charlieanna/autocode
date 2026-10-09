"""Audited design-input revision at a stopped, reconciled task boundary.

Owns design_input_changes and settings.design_manifest_history. Historical
references, approvals and independently recorded results stay retrievable.
"""
import copy
from urllib.parse import parse_qs, urlparse

try:
    from . import autocode_design_identity as identity
    from . import autocode_design_manifest as manifest
    from . import autocode_goals as goals
    from . import autocode_util as util
except ImportError:
    import autocode_design_identity as identity
    import autocode_design_manifest as manifest
    import autocode_goals as goals
    import autocode_util as util


def _references(candidate, previous):
    keys = {file['key'] for file in candidate['body']['files']}
    nodes = {(file['key'], node) for file in candidate['body']['files'] for page in file['pages']
             for node in manifest.inventory._metadata_nodes(page, candidate['root'])[0]}
    current, remaining = [], set(keys)
    for url in previous:
        parsed = urlparse(url); key = parsed.path.strip('/').split('/')[1]
        if key not in keys:
            continue
        node = parse_qs(parsed.query).get('node-id', [''])[0].replace('-', ':')
        current.append(url if not node or (key, node) in nodes else 'https://www.figma.com/design/' + key)
        remaining.discard(key)
    current.extend('https://www.figma.com/design/' + key for key in sorted(remaining))
    return current


def apply(state, candidate, expected, reason, workspace):
    old = (state.get('settings') or {}).get('design_manifest')
    if not old or expected != old['manifest_hash'] or not reason.strip():
        raise ValueError('Reference correction requires the exact inspected design hash and a concrete reason')
    stopped = state.get('status', '')
    if not (stopped.startswith('PAUSED_') or stopped in ('TASK_COMPLETE', 'AWAITING_GOAL_APPROVAL', 'WAITING_FOR_USER')) or any(state.get(key) for key in
            ('active_stage','pending_report_repair','uncertain_artifacts','active_runner_check','runner_check')):
        raise ValueError('Pause and reconcile controller, provider and runner-owned checks before changing references')
    if state.get('user_request',{}).get('kind') == 'human_review':
        raise ValueError('Resolve the pending artifact review before changing the reference')
    manifest.verify(old); manifest.verify(candidate)
    if candidate['body']['version'] != 2:
        raise ValueError('Reference revisions require the complete v2 inventory')
    if candidate['manifest_hash'] == old['manifest_hash']:
        raise ValueError('Reference correction is unchanged; no review boundary is needed')
    change = identity.delta(old,candidate)
    selected = manifest.retain(candidate,workspace)
    validation = copy.deepcopy(state.get('validation') or {})
    contract = copy.deepcopy(state.get('goal_contract'))
    previous_refs = state['settings'].get('figma_references') or ([state['settings']['figma_file']] if state['settings'].get('figma_file') else [])
    current_refs = _references(selected, previous_refs) if previous_refs else []
    event = {'kind':'design_input_changed','actor':'user_cli','at':util.now(), 'reason':reason,
        'previous_hash':old['manifest_hash'],'current_hash':selected['manifest_hash'],
        'coverage_change':change,'approved_contract':contract,
        'previous_validation':validation, 'previous_references':copy.deepcopy(previous_refs), 'current_references':current_refs}
    state.setdefault('design_input_changes',[]).append(event)
    state.setdefault('user_events',[]).append({key:copy.deepcopy(value) for key,value in event.items()
                                             if key not in ('previous_validation','approved_contract')})
    state['settings'].setdefault('design_manifest_history',[]).append(copy.deepcopy(old))
    state['settings']['design_manifest'] = selected
    if current_refs:
        state['settings']['figma_references'] = current_refs
        state['settings']['figma_file'] = current_refs[0]
    if contract:
        state.setdefault('contract_history',[]).append(contract)
    if stopped == 'TASK_COMPLETE':
        state.setdefault('completion_archive',[]).append({
            'completed_at':state.pop('completed_at',None),'decision':state.pop('final_decision',None),
            'reason':'Reviewed Figma reference revision'})
        state.pop('completion_actor',None)
    # Use the existing brief-correction/review path; replacing inputs is never approval.
    state['status'] = 'AWAITING_GOAL_APPROVAL'
    goals.feedback(state, 'Design input revision '+selected['manifest_hash']+': '+reason+
                   '. Coverage change: '+str(change)+'. Review the complete updated design mapping.')
    state.update(status='PAUSED_DESIGN_INPUT_CHANGED',phase='PAUSED_OR_BLOCKED',
        stop_reason='Updated Figma references are retained. Resume for plan review and fresh affected-case validation; no implementation was launched.')
    return event

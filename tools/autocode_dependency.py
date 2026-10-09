"""Single-run dependency boundary. No orchestration or cross-run state access.

Writes dependency_wait only here; run_view and the external dependency driver read it.
A CLI-authorized binding replaces one exact permission request, never plan approval.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def contained(root, relative):
    root = Path(root).resolve()
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts or path.is_symlink():
        raise ValueError('Dependency paths must be relative regular files')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Dependency path escapes its authorized root')
    return path


def bind(state, spec, public):
    if state.get('active_stage') or state.get('status') != 'WAITING_FOR_USER':
        raise ValueError('Bind a dependency only at a stopped user boundary')
    if not public or public.get('scope') != 'permission' or spec.get('request_token') != public.get('request_token'):
        raise ValueError('Dependency binding requires the exact current permission token')
    if len(public.get('questions', [])) != 1:
        raise ValueError('A dependency cannot consume multiple decisions')
    if spec.get('question_id') != public['questions'][0]['id']:
        raise ValueError('Dependency binding does not match the current question')
    producer = Path(spec['producer_workspace']).resolve()
    run = Path(spec['producer_run']).resolve()
    if not run.is_relative_to(producer / '.autocode/runs') or run == Path(state['run_dir']).resolve():
        raise ValueError('Expected a different producer run in its workspace')
    files = spec['files']
    if not isinstance(files, list) or not files or len(files) != len(set(files)):
        raise ValueError('Declare a nonempty unique delivery file list')
    for name in files:
        contained(producer, name)
        if name.startswith(('.git/', '.autocode/')):
            raise ValueError('Delivery source paths must be project files')
    destination = spec['destination']
    contained(state['run_dir'], destination)
    if not destination.startswith('evidence/'):
        raise ValueError('Deliveries belong beneath this run evidence directory')
    contract = state.get('goal_contract') or {}
    if contract.get('approval_status') != 'approved':
        raise ValueError('Dependency binding requires an approved consumer contract')
    wait = {key: copy.deepcopy(spec[key]) for key in
            ('producer_workspace', 'producer_run', 'files', 'destination', 'question_id')}
    wait.update(producer_workspace=str(producer), producer_run=str(run))
    wait.update(label=spec.get('label') or 'prerequisite task', status='waiting',
                consumer_contract=contract['hash'], request_id=public['request_id'],
                next_stage=state['next_stage'], authorization='user_cli_dependency_binding')
    wait['consumer_source'] = state['resolver']['human_escalations'][public['request_id']]['identity']['binding']['observed_source']['revision']
    state['dependency_wait'] = wait
    entry = state['resolver']['human_escalations'][public['request_id']]
    entry.update(status='superseded', superseded_reason='Operator delegated this delivery to the dependency driver')
    state.pop('resolver_human_request', None)
    state.pop('resolver_human_proposal', None)
    state.pop('user_request', None)
    state['pending_questions'] = []
    state.update(status='WAITING_FOR_DEPENDENCY', phase='WAITING_FOR_DEPENDENCY',
                 stop_reason=f"Waiting for {wait['label']} to finish and pass independent review. No user action needed.")
    state.setdefault('user_events', []).append({'kind': 'dependency_binding', 'actor': 'user_cli', **copy.deepcopy(wait)})


def ready(state, receipt_path):
    wait = state.get('dependency_wait') or {}
    if state.get('status') != 'WAITING_FOR_DEPENDENCY' or wait.get('status') != 'waiting':
        raise ValueError('No dependency is waiting for delivery')
    if wait['consumer_contract'] != (state.get('goal_contract') or {}).get('hash'):
        raise ValueError('Consumer contract changed; reconcile the dependency')
    root = contained(state['run_dir'], wait['destination'])
    expected = root / 'manifest.json'
    if Path(receipt_path).resolve() != expected.resolve():
        raise ValueError('Receipt must be the registered delivery manifest')
    receipt = json.loads(expected.read_text())
    if (receipt.get('producer_run') != wait['producer_run'] or
            receipt.get('producer_workspace') != wait['producer_workspace'] or
            receipt.get('consumer_contract') != wait['consumer_contract'] or
            set(receipt.get('files', {})) != set(wait['files']) or
            receipt.get('verified_complete') is not True):
        raise ValueError('Delivery does not match the authorized dependency')
    for name, digest in receipt['files'].items():
        if sha(contained(root / 'source', name)) != digest:
            raise ValueError('Delivered source hash mismatch: ' + name)
    proofs = receipt.get('proofs') or {}
    if not {'validator', 'reviewer'} <= proofs.keys():
        raise ValueError('Delivery requires independent Validator and completion review receipts')
    if not {'contract.json', 'source-snapshot.json'} <= receipt.get('pins', {}).keys():
        raise ValueError('Missing contract or source snapshot pin')
    for name, digest in receipt['pins'].items():
        if sha(contained(root, name)) != digest:
            raise ValueError('Missing or stale contract/source snapshot pin')
    contract = json.loads((root / 'contract.json').read_text())
    if contract.get('hash') != receipt.get('producer_contract') or contract.get('approval_status') != 'approved':
        raise ValueError('Producer contract is not the accepted delivery contract')
    sealed = hashlib.sha256(json.dumps({key: contract[key] for key in ('task_id', 'revision', 'body')}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if sealed != contract['hash']:
        raise ValueError('Producer contract seal is invalid')
    snapshot = json.loads((root / 'source-snapshot.json').read_text())
    revision = hashlib.sha256(json.dumps({'head': snapshot['head'], 'files': snapshot['files']}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if revision != receipt['source_revision']:
        raise ValueError('Source snapshot does not match reviewed revision')
    for name, digest in receipt['files'].items():
        if snapshot['files'].get(name) not in (digest, 'executable:' + digest):
            raise ValueError('Delivered file is not part of the reviewed snapshot')
        if bool(contained(root / 'source', name).stat().st_mode & 0o111) != snapshot['files'][name].startswith('executable:'):
            raise ValueError('Delivered file mode differs from the reviewed snapshot')
    for role in ('validator', 'reviewer'):
        proof = proofs[role]
        if proof.get('source_revision') != receipt.get('source_revision') or not proof.get('sha256'):
            raise ValueError('Review receipt is not bound to the delivered source')
        if sha(contained(root, proof['path'])) != proof['sha256']:
            raise ValueError('Review receipt hash mismatch')
        report = json.loads(contained(root, proof['path']).read_text())
        accepted = report.get('verdict') == 'PASS' if role == 'validator' else report.get('status') in ('COMPLETE', 'TASK_COMPLETE')
        if not accepted or report.get('contract_hash') != receipt['producer_contract']:
            raise ValueError('Review does not accept the delivered contract')
    wait.update(status='delivered', manifest=str(expected), manifest_sha256=sha(expected))
    state['recovery_context'] = {'kind': 'verified_dependency_delivery', 'manifest': str(expected),
        'instruction': 'The registered prerequisite finished and its pinned delivery is available. Verify this manifest and review receipts before importing only authorized files. Independent consumer integration validation is still required.'}
    state.update(status='RUNNING', phase='EXECUTING', next_stage=wait['next_stage'])
    state.pop('stop_reason', None)


def apply(args, state, run_dir, public, save, source_revision):
    """Controller adapter: execute one locked CLI action, or keep a waiting run stopped."""
    if args.bind_dependency or args.receive_dependency:
        candidate = copy.deepcopy(state)
        try:
            if args.bind_dependency:
                bind(candidate, json.loads(Path(args.bind_dependency).read_text()), public)
            else:
                if (candidate.get('dependency_wait') or {}).get('consumer_source') != source_revision():
                    raise ValueError('Consumer source changed while waiting; reconcile before delivery')
                ready(candidate, args.receive_dependency)
        except (ValueError, KeyError, OSError, TypeError) as error:
            print('Input rejected: ' + str(error))
            return 2
        save(run_dir / 'state.json', candidate)
        print('Dependency registered; waiting for verified delivery.' if args.bind_dependency else
              'Verified delivery recorded; ready to continue with independent integration review.')
        return 0
    if state.get('status') == 'WAITING_FOR_DEPENDENCY':
        if args.feedback is not None or args.edit_goal:
            state['dependency_wait']['status'] = 'cancelled'
            state.update(status='RUNNING', phase='EXECUTING')
            return None
        print(state['stop_reason'])
        return 2
    return None


def export(state, current, completion_current):
    """Expose only a runner-verified current completion through the status interface."""
    if not completion_current or state.get('status') != 'TASK_COMPLETE':
        return None
    revision = current['revision']
    proofs = {}
    for label, stages in (('validator', ('sol',)), ('reviewer', ('astra_review', 'astra_checkpoint'))):
        record = next((row for row in reversed(state.get('stages', []))
                       if row.get('stage') in stages and not row.get('rejected')
                       and row.get('source_revision') == revision and row.get('output')), None)
        if not record:
            return None
        path = Path(record['output'])
        if not path.resolve().is_relative_to(Path(state['run_dir']).resolve()) or path.is_symlink():
            return None
        proofs[label] = {'path': str(path), 'sha256': sha(path), 'source_revision': revision}
        events = Path(record['events']) if record.get('events') else None
        if events and events.is_file() and events.resolve().is_relative_to(Path(state['run_dir']).resolve()):
            proofs[label]['events'] = {'path': str(events), 'sha256': sha(events)}
    return {'verified_complete': True, 'source_revision': revision, 'files': current['files'],
            'contract_hash': (state.get('goal_contract') or {}).get('hash'),
            'contract': copy.deepcopy(state.get('goal_contract')), 'source_snapshot': current, 'proofs': proofs}

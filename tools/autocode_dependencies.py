"""Transport reviewed deliveries and wake dependent runs through TaskRun only.

Run `python tools/autocode_dependencies.py --workspace W --run-dir R --watch`.
A restart re-reads the durable binding; no cross-task state.json reads or writes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import fcntl

try:
    from .autocode_taskrun import TaskRun
    from .autocode_dependency import contained, sha
except ImportError:
    from autocode_taskrun import TaskRun
    from autocode_dependency import contained, sha


def transport(wait, producer, destination):
    before = producer.status()
    delivery = before.get('delivery')
    if not before.get('done') or not delivery or not delivery.get('verified_complete'):
        return None
    root = Path(producer.workspace).resolve()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.delivery-', dir=destination.parent))
    try:
        receipt = {'version': 1, 'verified_complete': True,
                   'producer_workspace': str(root), 'producer_run': str(producer.run_dir),
                   'consumer_contract': wait['consumer_contract'],
                   'producer_contract': delivery['contract_hash'],
                   'source_revision': delivery['source_revision'], 'files': {}, 'proofs': {}, 'pins': {},
                   'mapping': {name: name for name in wait['files']},
                   'transport': 'autocode_dependencies via TaskRun current-completion status'}
        for name, value in (('contract.json', delivery['contract']), ('source-snapshot.json', delivery['source_snapshot'])):
            target = temporary / name
            target.write_text(json.dumps(value, indent=2) + '\n')
            receipt['pins'][name] = sha(target)
        for name in wait['files']:
            source = contained(root, name)
            expected = delivery['files'].get(name, '')
            digest = sha(source)
            if expected not in (digest, 'executable:' + digest):
                raise ValueError('Delivery file is missing or changed since review: ' + name)
            target = contained(temporary / 'source', name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o755 if expected.startswith('executable:') else 0o644)
            if sha(target) != digest:
                raise ValueError('Source changed during transport')
            receipt['files'][name] = digest
        for label in ('validator', 'reviewer'):
            proof = delivery['proofs'][label]
            source = Path(proof['path'])
            if source.is_symlink() or not source.resolve().is_relative_to(Path(producer.run_dir).resolve()):
                raise ValueError('Review evidence escapes producer run')
            if proof['source_revision'] != delivery['source_revision'] or sha(source) != proof['sha256']:
                raise ValueError('Stale independent review evidence')
            relative = 'reviews/' + label + '.json'
            target = temporary / relative
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, target)
            if sha(target) != proof['sha256']:
                raise ValueError('Review evidence changed during transport')
            receipt['proofs'][label] = {**proof, 'path': relative, 'original_path': str(source)}
            if proof.get('events'):
                events = proof['events']
                event_source = Path(events['path'])
                if event_source.is_symlink() or not event_source.resolve().is_relative_to(Path(producer.run_dir).resolve()):
                    raise ValueError('Execution evidence escapes producer run')
                event_target = temporary / ('reviews/' + label + '.jsonl')
                shutil.copyfile(event_source, event_target)
                if sha(event_target) != events['sha256']:
                    raise ValueError('Execution evidence changed during transport')
                receipt['pins'][str(event_target.relative_to(temporary))] = events['sha256']
        after = producer.status()
        if not after.get('done') or after.get('delivery') != delivery:
            raise ValueError('Producer changed during transport; delivery discarded')
        manifest = temporary / 'manifest.json'
        manifest.write_text(json.dumps(receipt, indent=2) + '\n')
        if destination.exists():
            # A prior tick may have delivered then crashed before recording receipt.
            if (destination / 'manifest.json').read_bytes() != manifest.read_bytes():
                raise ValueError('Existing delivery differs; preserve it for reconciliation')
            for name, digest in receipt['files'].items():
                if sha(contained(destination / 'source', name)) != digest:
                    raise ValueError('Existing delivery was modified')
        else:
            os.rename(temporary, destination)
        return destination / 'manifest.json'
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def tick(consumer):
    view = consumer.status()
    wait = view.get('dependency') or {}
    if wait.get('status') == 'delivered':
        # Crash after receipt, before launch: only advance if the runner needs it.
        if (view.get('needs') or {}).get('kind') == 'continue':
            consumer.advance()
        return 'delivered'
    if (view.get('needs') or {}).get('kind') != 'dependency':
        return 'not_waiting'
    producer = TaskRun(Path(wait['producer_workspace']), Path(wait['producer_run']),
                       command=consumer.command, env=consumer.env)
    manifest = transport(wait, producer, contained(consumer.run_dir, wait['destination']))
    if manifest is None:
        return 'waiting'
    consumer.receive_dependency(manifest)
    consumer.advance()
    return 'delivered'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=float, default=30)
    args = parser.parse_args()
    if args.interval < 1:
        parser.error('interval must be at least one second')
    consumer = TaskRun(args.workspace.resolve(), args.run_dir.resolve())
    # Only one transporter can answer and launch a consumer at a time.
    with (consumer.run_dir / 'dependency-driver.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            result = tick(consumer)
            print(result, flush=True)
            if not args.watch or result != 'waiting':
                return
            time.sleep(args.interval)


if __name__ == '__main__':
    main()

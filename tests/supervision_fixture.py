"""Fake the provider launch boundary in controller unit tests.

Real process ownership, pre-exec admission and cleanup are exercised in
TestSupervision and public CLI fault controls. A controller-only test supplies
its provider factory and retains the same deterministic response/exit checks.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import uuid


def launcher(factory):
    @contextmanager
    def launch(command, *, receipt_path, timeout=None, checkpoint=None, **options):
        child = factory(command, **options)
        provider = {'pid': child.pid, 'birth_identity': 1}
        metadata = {'schema': 1, 'nonce': uuid.uuid4().hex,
                    'owner': {'pid': 99999997, 'birth_identity': 1},
                    'keeper': {'pid': 99999998, 'birth_identity': 1},
                    'provider': provider, 'receipt': str(receipt_path)}
        if checkpoint:
            checkpoint(metadata)
        yield child
        Path(receipt_path).write_text(json.dumps({**metadata, 'phase': 'stopped',
            'cause': 'controller_finished', 'processes': [provider],
            'cleanup_error': None, 'observed_at': 'fixture'}))
    return launch

"""Pure command ownership evidence and safe-retry policy."""
import copy
from pathlib import Path
import tempfile
import unittest

import autocode_command_receipt as policy
import autocode_util as util


def guarded_command_receipt(receipt):
    """Valid ownership domain input; physical lifecycle tests are separate."""
    path = Path(receipt['output']).with_suffix('.ownership.json').resolve()
    metadata = {'schema': 1, 'nonce': 'b' * 32, 'receipt': str(path),
                'owner': {'pid': 201, 'birth_identity': 1},
                'keeper': {'pid': 202, 'birth_identity': 2},
                'provider': {'pid': 203, 'birth_identity': 3}}
    util.atomic_json(path, {**metadata, 'phase': 'stopped', 'cause': 'provider_stopped',
                           'cleanup_error': None, 'observed_at': 'fixture completed',
                           'processes': [metadata['provider']]})
    return {**receipt, 'supervision': metadata, 'supervision_sha256': util.file_hash(path),
            'supervision_errors': []}


class CommandReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / 'ownership.json'
        self.metadata = {'schema': 1, 'nonce': 'a' * 32, 'receipt': str(self.path),
                         'owner': {'pid': 101, 'birth_identity': 1},
                         'keeper': {'pid': 102, 'birth_identity': 2},
                         'provider': {'pid': 103, 'birth_identity': 3}}
        self.value = {**self.metadata, 'phase': 'stopped', 'cause': 'provider_stopped',
                      'cleanup_error': None, 'observed_at': '2026-10-06T00:00:00Z',
                      'processes': [self.metadata['provider'], {'pid': 104, 'birth_identity': 4}]}
        self.write()

    def write(self, value=None):
        util.atomic_json(self.path, self.value if value is None else value)

    def receipt(self, **changes):
        return {'supervision': self.metadata, 'supervision_sha256': util.file_hash(self.path),
                'supervision_errors': [], 'exit_code': 0, 'timed_out': False, **changes}

    def test_normal_collected_success_and_failure_are_valid_evidence(self):
        before = self.path.read_bytes()
        for code in (0, 1, -15):
            with self.subTest(code=code):
                self.assertTrue(policy.completed(self.receipt(exit_code=code), root=self.root))
        self.assertTrue(policy.cleanup_complete(self.metadata))
        self.assertEqual(before, self.path.read_bytes())

    def test_absent_supervision_preserves_legacy_but_invalid_presence_does_not(self):
        self.assertTrue(policy.completed({'exit_code': 0}))
        for supervision in (None, {}, False, 'missing'):
            with self.subTest(supervision=supervision):
                self.assertFalse(policy.completed(self.receipt(supervision=supervision)))

    def test_partial_ownership_extension_never_becomes_legacy_evidence(self):
        receipt = self.receipt()
        for name in policy.OWNERSHIP_FIELDS:
            with self.subTest(field=name):
                partial = {key: value for key, value in receipt.items() if key != name}
                self.assertFalse(policy.completed(partial))

    def test_projection_keeps_recorded_pins_when_a_receipt_is_missing(self):
        receipt = self.receipt()
        projected = policy.project(receipt)
        self.assertEqual({str(self.path): receipt['supervision_sha256']}, policy.pins(projected))
        projected['supervision']['nonce'] = 'changed'
        self.assertEqual('a' * 32, receipt['supervision']['nonce'])
        self.path.unlink()
        self.assertEqual({str(self.path): receipt['supervision_sha256']}, policy.pins(receipt))
        self.assertFalse(policy.completed(receipt))
        partial = {'supervision_sha256': receipt['supervision_sha256']}
        self.assertEqual(partial, policy.project(partial))
        self.assertFalse(policy.completed(partial))

    def test_uncollected_exit_timeout_interruption_and_keeper_error_are_not_proof(self):
        changes = [{'exit_code': code} for code in (None, False, True, '0')]
        changes += [{'timed_out': True}, {'interrupted': True}, {'error': 'incomplete'},
                    {'supervision_errors': ['Keeper stopped']}, {'supervision_errors': None},
                    {'supervision_sha256': None}, {'supervision_sha256': 'bad'}]
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(policy.completed(self.receipt(**change)))

    def test_deadline_and_owner_loss_cleanup_can_permit_retry_but_never_pass(self):
        for cause in ('stage_deadline', 'owner_lost', 'keeper_failure', 'lifeline_failure'):
            with self.subTest(cause=cause):
                self.write({**self.value, 'cause': cause})
                self.assertFalse(policy.completed(self.receipt()))
                self.assertTrue(policy.cleanup_complete(self.metadata))

    def test_unfinished_or_failed_cleanup_never_permits_retry(self):
        for change in ({'phase': 'armed', 'cause': None}, {'phase': 'stopping'},
                       {'phase': 'uncertain'}, {'cleanup_error': 'access denied'}):
            with self.subTest(change=change):
                self.write({**self.value, **change})
                self.assertFalse(policy.completed(self.receipt()))
                self.assertFalse(policy.cleanup_complete(self.metadata))

    def test_replaced_receipt_cannot_retain_its_original_evidence_pin(self):
        receipt = self.receipt()
        self.write({**self.value, 'cause': 'controller_finished'})
        self.assertIsNotNone(policy.load(self.metadata))
        self.assertFalse(policy.completed(receipt))
        self.path.unlink()
        self.assertFalse(policy.completed(receipt))

    def test_schema_nonce_roles_and_native_inventory_are_authenticated(self):
        variants = [{'schema': True}, {'nonce': 'b' * 32}, {'owner': {'pid': 101, 'birth_identity': 9}},
                    {'keeper': {'pid': 0, 'birth_identity': 2}}, {'phase': []}, {'cause': []},
                    {'cleanup_error': 1}, {'observed_at': ''}, {'processes': []},
                    {'processes': [self.metadata['provider']] * 2},
                    {'processes': [self.metadata['provider'], {'pid': 104, 'birth_identity': float('nan')}]},
                    {'processes': [self.metadata['provider'], {'pid': True, 'birth_identity': 4}]},
                    {'processes': [{'pid': 103, 'birth_identity': 99}]}]
        for change in variants:
            with self.subTest(change=change):
                self.write({**self.value, **change})
                self.assertIsNone(policy.load(self.metadata))
        self.write()
        for role in ('owner', 'keeper', 'provider'):
            metadata = copy.deepcopy(self.metadata)
            metadata[role]['birth_identity'] = float('inf')
            self.assertIsNone(policy.load(metadata))

    def test_provider_inventory_accepts_display_drift_but_refuses_native_birth_reuse(self):
        metadata = copy.deepcopy(self.metadata)
        metadata['provider'].update(started='declared start', birth_time=1, group=103)
        inventory = {**metadata['provider'], 'started': 'refreshed start', 'birth_time': 2.25}
        self.write({**self.value, 'provider': metadata['provider'], 'processes': [inventory]})
        self.assertIsNotNone(policy.load(metadata))
        self.assertTrue(policy.completed(self.receipt(supervision=metadata)))
        inventory['birth_identity'] += 1
        self.write({**self.value, 'provider': metadata['provider'], 'processes': [inventory]})
        self.assertIsNone(policy.load(metadata))
        self.assertFalse(policy.completed(self.receipt(supervision=metadata)))

    def test_invalid_and_oversized_bytes_are_unavailable(self):
        for data in (b'not json', b'[]', b'x' * (policy.MAX_RECEIPT_BYTES + 1)):
            with self.subTest(length=len(data)):
                self.path.write_bytes(data)
                self.assertIsNone(policy.load(self.metadata))

    def test_outside_and_symlinked_receipt_paths_cannot_supply_evidence(self):
        outside = self.root / 'different-root'
        outside.mkdir()
        self.assertIsNone(policy.load(self.metadata, root=outside))
        target = self.root / 'saved.json'
        self.path.rename(target)
        self.path.symlink_to(target)
        self.assertIsNone(policy.load(self.metadata))
        self.path.unlink()
        target.rename(self.path)
        directory = self.root / 'linked'
        directory.symlink_to(self.root, target_is_directory=True)
        metadata = {**self.metadata, 'receipt': str(directory / self.path.name)}
        self.assertIsNone(policy.load(metadata, root=self.root))
        self.assertIsNone(policy.load(self.metadata, root=directory))
        nested = self.root / 'nested'
        nested.mkdir()
        util.atomic_json(nested / self.path.name, self.value)
        linked_root = directory / nested.name
        metadata = {**self.metadata, 'receipt': str(linked_root / self.path.name)}
        self.assertIsNone(policy.load(metadata, root=linked_root))


if __name__ == '__main__':
    unittest.main()

"""Command-local output rules and compatibility with manifests sealed on master."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import autocode_brief_acceptance as acceptance


FIXTURES = json.loads((Path(__file__).parent / 'fixtures/brief-manifests-v1.json').read_text())


class CommandBoundaryTests(unittest.TestCase):
    def test_saved_v1_manifests_recompute_exactly_without_changing_sealed_hashes(self):
        # These bytes were generated on untouched master, before the parser change.
        for fixture in FIXTURES:
            with self.subTest(name=fixture['name']):
                old = fixture['manifest']
                self.assertEqual(old, acceptance.verify(fixture['sources'], old))
                proposals = [row['proposal'] for row in old['observations']]
                self.assertEqual(old, acceptance.bind(fixture['sources'], proposals, version=1))
                self.assertEqual(len(proposals), len(acceptance.commands(fixture['sources'], old)))

    def test_unknown_versions_and_cross_version_replacement_are_refused(self):
        old = FIXTURES[2]['manifest']
        records = FIXTURES[2]['sources']
        new = acceptance.bind(records, [row['proposal'] for row in old['observations']])
        self.assertEqual(2, new['version'])
        with self.assertRaisesRegex(ValueError, 'compiler version cannot change'):
            acceptance.preserve(old, new)
        for version in (True, 0, 3, '1'):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'Unsupported'):
                acceptance.verify(records, {**old, 'version': version})

    def test_next_invocation_with_nonzero_exit_still_ends_the_previous_description(self):
        text = ('`todo.py add TEXT` appends a to-do and exits 0. '
                '`todo.py list` prints entries as `ID TEXT` one per line and exits 1.')
        self.assertEqual([], acceptance.inventory([{'id': 'task', 'kind': 'task', 'text': text}]))

    def test_silent_add_does_not_inherit_list_output_and_correct_product_passes(self):
        records = FIXTURES[0]['sources']
        declarations = acceptance.inventory(records)
        self.assertEqual([['list']], [row['observe_argv'] for row in declarations])
        old = FIXTURES[0]['manifest']['observations'][1]['proposal']
        proposal = {**old, 'declaration_id': declarations[0]['id']}
        manifest = acceptance.bind(records, [proposal])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'todo.py').write_text("import sys\nif sys.argv[1]=='list': print('1 buy milk [open]\\n2 walk dog [open]')\n")
            command = acceptance.commands(records, manifest, python=sys.executable)[0]
            result = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual('PASS', json.loads(result.stdout)['verdict'])

    def test_single_item_add_cannot_become_a_listing_from_later_per_line_text(self):
        records = FIXTURES[1]['sources']
        # Neither command independently declares this compiler's format + one-per-line grammar.
        self.assertEqual([], acceptance.inventory(records))
        phantom = FIXTURES[1]['manifest']['observations'][0]['proposal']
        with self.assertRaisesRegex(ValueError, 'known and unique'):
            acceptance.bind(records, [phantom])

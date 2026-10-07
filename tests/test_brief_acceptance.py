"""Source-derived CLI observations; independent tiny product and no providers."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import autocode_brief_acceptance as brief

TASK = ('Commands: `todo.py add TEXT` appends a to-do and exits 0; '
        '`todo.py list` prints every to-do as `ID TEXT [open|done]` one per line and exits 0; '
        '`todo.py complete ID` marks the to-do done and exits 0.')
# Neither catalog reference nor oracle; opaque ID rules out an invented ID 1.
PRODUCT = """import json
from pathlib import Path
import sys
store = Path('state.json')
if sys.argv[1] == 'add':
    store.write_text(json.dumps({'text': sys.argv[2]}))
elif sys.argv[1] == 'list':
    print('ticket-z ' + json.loads(store.read_text())['text'] + ' [open]')
else:
    sys.exit(2)
"""


def sources(text=TASK, ident='task', kind='task'):
    return [{'id': ident, 'kind': kind, 'text': text}]


def proposal(records, value='brief-probe', criterion='AC1'):
    return {'declaration_id': brief.inventory(records)[0]['id'], 'criterion_ids': [criterion],
            'steps': [{'argv': ['add', value]}, {'argv': ['list']}], 'observe_step': 1,
            'bindings': [{'placeholder': 'TEXT', 'step': 0, 'argument': 1}]}


def listing_proposal(records):
    """The observation the live Plan Reviewers proposed in both #452 runs on d6aded9."""
    return {'declaration_id': brief.inventory(records)[0]['id'], 'criterion_ids': ['AC1'],
            'steps': [{'argv': ['add', 'buy milk']}, {'argv': ['add', 'walk dog']},
                      {'argv': ['complete', '1']}, {'argv': ['list']}], 'observe_step': 3,
            'bindings': [{'placeholder': 'TEXT', 'step': 0, 'argument': 1},
                         {'placeholder': 'ID', 'step': 2, 'argument': 1}]}


# A correct multi-item to-do list, independent of the catalog reference and oracle.
LIST_PRODUCT = """import json
from pathlib import Path
import sys
store = Path('todos.json')
items = json.loads(store.read_text()) if store.exists() else []
if sys.argv[1] == 'add':
    items.append({'id': len(items) + 1, 'text': sys.argv[2], 'done': False})
    print(items[-1]['id'])
elif sys.argv[1] == 'complete':
    for item in items:
        item['done'] = item['done'] or str(item['id']) == sys.argv[2]
elif sys.argv[1] == 'list':
    for item in items:
        print(f"{item['id']} {item['text']} [{'done' if item['done'] else 'open'}]")
else:
    sys.exit(2)
store.write_text(json.dumps(items))
"""


def printing(listing):
    """A product whose `list` prints exactly these bytes; every other command succeeds silently."""
    return f"import sys\nif sys.argv[1] == 'list':\n    sys.stdout.buffer.write({listing!r})\n"


# What `todo.py list` prints after the live setup, whether the brief's format admits it, and
# whether the contained runner also replays it (one per branch of the rule; each costs processes).
LISTINGS = [
    (b'1 buy milk [done]\n2 walk dog [open]\n', True, True),  # live run ewqn70hi
    (b'1 buy milk [done]\n2 walk dog [open]', True, False),
    (b'1 buy milk [done]\r\n2 walk dog [open]\r\n', True, True),
    (b'2 walk dog [open]\n1 buy milk [done]\n', True, False),  # order is not part of the format
    (b'1 buy milk done\n2 walk dog open\n', False, False),  # the unbracketed mutant
    (b'1 buy milk [done]\n2 walk dog open\n', False, True),  # only the observed line is formatted
    (b'2 walk dog [open]\n', False, True),  # the observed item is missing
    (b'1 buy milk [closed]\n2 walk dog [open]\n', False, False),
    (b'01 buy milk [done]\n2 walk dog [open]\n', False, False),  # the bound ID changed
    (b'ID TEXT STATUS\n1 buy milk [done]\n2 walk dog [open]\n', False, False),
    (b'1 buy milk [done]\n\n2 walk dog [open]\n', False, False),
    (b'1 buy milk [done]\n2 walk dog [open]\n\n', False, False),
    (b'1 buy milk [done] \n2 walk dog [open]\n', False, False),
    (b'1 buy milk [done]\r\n2 walk dog [open]\n', False, True),  # mixed line endings
    (b'1 buy milk [done]\n2 walk dog [open]\n3 eggs [open]\n', False, False),  # nobody added eggs
    (b'', False, False),
    (b'1 buy milk [done]\n\xff\n', False, True),
]


class BriefAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.sources = sources()
        self.proposal = proposal(self.sources)
        self.manifest = brief.bind(self.sources, [self.proposal])

    def test_exact_original_quote_literal_and_source_hash_bind_inventory(self):
        declaration = brief.inventory(self.sources)[0]
        self.assertEqual('ID TEXT [open|done]', declaration['literal'])
        self.assertEqual(TASK[slice(*declaration['span'])], declaration['quote'])
        self.assertEqual(declaration['literal'], TASK[slice(*declaration['literal_span'])])
        self.assertEqual(hashlib.sha256(TASK.encode()).hexdigest(), declaration['source_sha256'])
        self.assertEqual(self.manifest, brief.verify(self.sources, self.manifest))

    def test_unsupported_example_and_described_broken_output_do_not_invent_obligations(self):
        for text in ['Build a useful API.', '```\n' + TASK + '\n```',
                     TASK.replace('exits 0', 'exits 1'), TASK.replace('exits 0', 'exits 0.5'),
                     TASK.replace('`todo.py list` prints', 'Currently `todo.py list` prints'),
                     TASK.replace('prints every to-do', 'must not print every to-do')]:
            with self.subTest(text=text):
                self.assertEqual([], brief.inventory(sources(text)))

    def test_noncommand_inline_quotes_do_not_abort_or_hide_supported_obligations(self):
        for example in ["A token `can't` becomes `can` and `t`.",
                        'A quote in an unquoted field such as `Jo"e` is malformed.']:
            with self.subTest(example=example):
                self.assertEqual([], brief.inventory(sources(example)))
                records = sources(example + '\n' + TASK)
                declarations = brief.inventory(records)
                self.assertEqual(1, len(declarations))
                self.assertEqual('todo.py', declarations[0]['program'])
                self.assertEqual('ID TEXT [open|done]', declarations[0]['literal'])
                manifest = brief.bind(records, [proposal(records)])
                self.assertEqual(manifest, brief.verify(records, manifest))

    def test_wordfreq_and_csv_catalog_briefs_do_not_treat_data_examples_as_invocations(self):
        catalog = Path(__file__).resolve().parents[1] / 'scenarios' / 'catalog'
        for scenario in ['ladder-02-word-frequency-cli', 'ladder-03-csv-validation-cli']:
            with self.subTest(scenario=scenario):
                text = (catalog / scenario / 'brief.md').read_text()
                self.assertEqual([], brief.inventory(sources(text)))

    def test_actual_python_invocations_still_reject_invalid_quoting(self):
        for invocation in ['todo.py list "bad', "todo.py add 'bad", '"todo.py list']:
            with self.subTest(invocation=invocation):
                text = f'`{invocation}` prints entries as `ID TEXT` one per line and exits 0.'
                with self.assertRaisesRegex(ValueError, 'Source CLI invocation has invalid quoting'):
                    brief.inventory(sources(text))

    def test_quoted_program_and_arguments_remain_supported(self):
        records = sources(TASK.replace('`todo.py', '`"todo.py"').replace('add TEXT`', 'add "TEXT"`'))
        declaration = brief.inventory(records)[0]
        self.assertEqual('todo.py', declaration['program'])
        self.assertIn(['add', 'TEXT'], declaration['commands'])
        manifest = brief.bind(records, [proposal(records)])
        self.assertEqual(manifest, brief.verify(records, manifest))

    def test_midtoken_program_quotes_and_escapes_cannot_hide_supported_declarations(self):
        for program in ['todo."py"', "todo.p'y'", r'todo.p\y']:
            with self.subTest(program=program):
                records = sources(TASK.replace('todo.py', program))
                declarations = brief.inventory(records)
                self.assertEqual(1, len(declarations))
                self.assertEqual('todo.py', declarations[0]['program'])
                self.assertEqual('ID TEXT [open|done]', declarations[0]['literal'])
                self.assertEqual(TASK.replace('todo.py', program)[slice(*declarations[0]['span'])],
                                 declarations[0]['quote'])
                manifest = brief.bind(records, [proposal(records)])
                self.assertEqual(manifest, brief.verify(records, manifest))

    def test_malformed_midtoken_python_program_and_argument_quotes_are_rejected(self):
        for invocation in ['todo.p"y list', "todo.p'y list", r'todo.p\y list "bad']:
            with self.subTest(invocation=invocation):
                text = f'`{invocation}` prints entries as `ID TEXT` one per line and exits 0.'
                with self.assertRaisesRegex(ValueError, 'Source CLI invocation has invalid quoting'):
                    brief.inventory(sources(text))

    def test_negative_context_does_not_cross_supported_clause_boundaries(self):
        declaration = '`todo.py list` prints entries as `ID [open|done]` one per line and exits 0'
        for separator in ['; ', '\n', '\r\n']:
            with self.subTest(separator=separator):
                self.assertEqual(1, len(brief.inventory(sources('Currently the project is empty.' + separator + declaration))))
        self.assertEqual([], brief.inventory(sources('Currently ' + declaration)))

    def test_every_independently_inventoried_declaration_needs_observation(self):
        with self.assertRaisesRegex(ValueError, 'Every supported'):
            brief.bind(self.sources, [])
        added = sources(TASK.replace('todo.py', 'other.py'), 'answer', 'user_answer')
        with self.assertRaisesRegex(ValueError, 'Every supported'):
            brief.bind(self.sources + added, [self.proposal])

    def test_model_cannot_propose_expected_program_regex_or_code(self):
        for key, value in [('expected', 'ticket-z brief-probe open'), ('program', '../todo.py'),
                           ('regex', '.*'), ('script', 'print(1)')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                brief.bind(self.sources, [{**self.proposal, key: value}])

    def test_source_program_paths_cannot_traverse_or_be_absolute(self):
        for program in ['../todo.py', 'a/../todo.py', '/tmp/todo.py', './todo.py',
                        '"cli tools/todo.py"', "'cli tools/todo.py'"]:
            with self.subTest(program=program), self.assertRaises(ValueError):
                brief.inventory(sources(TASK.replace('todo.py', program)))

    def test_unknown_or_ambiguous_source_commands_reject(self):
        changed = copy.deepcopy(self.proposal)
        changed['steps'][0]['argv'] = ['remove', 'brief-probe']
        with self.assertRaisesRegex(ValueError, 'source-declared'):
            brief.bind(self.sources, [changed])
        ambiguous = sources(TASK + '; `todo.py add ID` also adds and exits 0')
        with self.assertRaisesRegex(ValueError, 'uniquely match'):
            brief.bind(ambiguous, [proposal(ambiguous)])

    def test_text_requires_argument_binding_and_id_remains_opaque(self):
        with self.assertRaisesRegex(ValueError, 'TEXT must be tied'):
            brief.bind(self.sources, [{**self.proposal, 'bindings': []}])
        no_id_command = sources(TASK.split('; `todo.py complete ID`')[0])
        manifest = brief.bind(no_id_command, [proposal(no_id_command)])
        self.assertIn(r'[^\s]+', manifest['observations'][0]['pattern'])

    def test_wrong_binding_names_indices_and_future_arguments_reject(self):
        for binding in [{'placeholder': 'TEXT', 'step': 1, 'argument': 0},
                        {'placeholder': 'ID', 'step': 0, 'argument': 1},
                        {'placeholder': 'TEXT', 'step': True, 'argument': 1},
                        {'placeholder': 'TEXT', 'step': 0, 'argument': 9}]:
            with self.subTest(binding=binding), self.assertRaises(ValueError):
                brief.bind(self.sources, [{**self.proposal, 'bindings': [binding]}])
        changed = copy.deepcopy(self.proposal)
        changed['steps'].reverse()
        changed['observe_step'] = 0
        changed['bindings'][0]['step'] = 1
        with self.assertRaises(ValueError):
            brief.bind(self.sources, [changed])

    def test_criterion_link_requires_unique_well_formed_ids(self):
        for ids in [[], ['AC1', 'AC1'], [''], [42]]:
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                brief.bind(self.sources, [{**self.proposal, 'criterion_ids': ids}])

    def test_uppercase_literal_is_not_a_wildcard(self):
        records = sources(TASK.replace('ID TEXT [open|done]', 'SUCCESS ID TEXT [open|done]'))
        manifest = brief.bind(records, [proposal(records)])
        self.assertTrue(manifest['observations'][0]['pattern'].startswith('SUCCESS'))

    def test_source_shapes_kinds_and_duplicate_ids_reject(self):
        for records in [sources(kind='builder'), sources(kind=[]), self.sources * 2,
                        [{**self.sources[0], 'hash': 'invented'}]]:
            with self.subTest(records=records), self.assertRaises(ValueError):
                brief.inventory(records)

    def test_changed_source_and_rehashed_forged_pattern_cannot_replay(self):
        with self.assertRaises(ValueError):
            brief.verify(sources(TASK + ' Added human text.'), self.manifest)
        changed = copy.deepcopy(self.manifest)
        row = changed['observations'][0]
        row['pattern'] = '.*'
        row['hash'] = brief.digest({key: value for key, value in row.items() if key != 'hash'})
        changed['hash'] = brief.digest({key: value for key, value in changed.items() if key != 'hash'})
        with self.assertRaisesRegex(ValueError, 'binding changed'):
            brief.verify(self.sources, changed)

    def test_probe_or_criterion_change_cannot_erase_previous_observation(self):
        for changed in [proposal(self.sources, 'another-probe'), proposal(self.sources, criterion='AC2')]:
            with self.assertRaisesRegex(ValueError, 'authenticated replacement'):
                brief.preserve(self.manifest, brief.bind(self.sources, [changed]))
        self.assertEqual(self.manifest, brief.preserve(self.manifest, self.manifest))

    def test_new_obligations_may_be_added_while_old_observation_survives(self):
        added = sources(TASK.replace('todo.py', 'other.py'), 'answer', 'user_answer')
        manifest = brief.bind(self.sources + added, [self.proposal, proposal(added)])
        self.assertEqual(manifest, brief.preserve(self.manifest, manifest))

    def test_amendment_needs_independent_inactive_context_and_exact_source_hash(self):
        amendment = sources(TASK.replace('[open|done]', '[pending|done]'), 'human-amendment', 'user_feedback')
        records = self.sources + amendment
        inactive = [self.proposal['declaration_id']]
        manifest = brief.bind(records, [proposal(amendment)], inactive=inactive)
        with self.assertRaisesRegex(ValueError, 'Every supported'):
            brief.verify(records, manifest)
        self.assertEqual(manifest, brief.verify(records, manifest, inactive=inactive))
        replacement = {'previous_hash': self.manifest['observations'][0]['hash'],
                       'replacement_hash': manifest['observations'][0]['hash'],
                       'replacement_source_sha256': hashlib.sha256(amendment[0]['text'].encode()).hexdigest()}
        with self.assertRaisesRegex(ValueError, 'authenticated replacement'):
            brief.preserve(self.manifest, manifest)
        with self.assertRaisesRegex(ValueError, 'source content hash'):
            brief.preserve(self.manifest, manifest, replacements=[{**replacement, 'replacement_source_sha256': '0' * 64}])
        self.assertEqual(manifest, brief.preserve(self.manifest, manifest, replacements=[replacement]))
        with self.assertRaises(ValueError):
            brief.preserve(self.manifest, manifest, replacements=[replacement, replacement])

    def test_silent_deactivation_cannot_pass_preservation(self):
        manifest = brief.bind(self.sources, [], inactive=[self.proposal['declaration_id']])
        with self.assertRaisesRegex(ValueError, 'authenticated replacement'):
            brief.preserve(self.manifest, manifest)

    def test_interpreter_and_timeout_are_bounded_caller_inputs(self):
        for python in ['sh', 'python3; echo bad', '../python3', 'python3\n']:
            with self.subTest(python=python), self.assertRaises(ValueError):
                brief.commands(self.sources, self.manifest, python=python)
        for timeout in [True, 0, -1, 901, float('nan')]:
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                brief.commands(self.sources, self.manifest, timeout=timeout)

    def run_candidate(self, program, value='brief-probe', extra_files=None, records=None, manifest=None, inactive=()):
        with tempfile.TemporaryDirectory(prefix='brief-acceptance-test-') as directory:
            root = Path(directory)
            (root / 'todo.py').write_text(program)
            for name, contents in (extra_files or {}).items():
                (root / name).write_text(contents)
            records = self.sources if records is None else records
            manifest = brief.bind(records, [proposal(records, value)]) if manifest is None else manifest
            command = brief.commands(records, manifest, inactive=inactive, python=sys.executable)[0]
            result = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True, timeout=20)
            self.assertEqual('', result.stderr)
            return result.returncode, json.loads(result.stdout), [path.name for path in root.iterdir()]

    def test_actual_reference_passes_unbracketed_mutant_fails_only_format(self):
        for product, expected in [(PRODUCT, 0), (PRODUCT.replace(' [open]', ' open'), 1)]:
            with self.subTest(expected=expected):
                code, observation, files = self.run_candidate(product)
                self.assertEqual(expected, code)
                self.assertEqual([0, 0], [step['exit_code'] for step in observation['steps']])
                self.assertEqual('PASS' if expected == 0 else 'FAIL', observation['verdict'])
                if expected:
                    self.assertIn('original brief format', observation['reason'])
                self.assertEqual(['todo.py'], files)

    def test_terse_human_amendment_inherits_only_earlier_commands_and_exact_replacement(self):
        original = sources(TASK, 'z-original')
        previous = brief.bind(original, [proposal(original)])
        amendment = sources('Change todo.py list: `todo.py list` prints every to-do as '
                            '`ID TEXT open|done` one per line and exits 0.', 'a-feedback', 'user_feedback')
        records = original + amendment  # Source IDs deliberately sort against chronology.
        inventory = brief.inventory(records)
        old = next(row for row in inventory if row['source_id'] == 'z-original')
        new = next(row for row in inventory if row['source_id'] == 'a-feedback')
        self.assertEqual(previous['observations'][0]['declaration'], old)
        self.assertEqual(previous['observations'][0]['hash'],
                         brief.bind(records, [proposal(original), {**proposal(original), 'declaration_id': new['id']}])['observations'][1]['hash'])
        self.assertIn(['add', 'TEXT'], new['commands'])
        amended_proposal = {**proposal(original), 'declaration_id': new['id']}
        inactive = [old['id']]
        amended = brief.bind(records, [amended_proposal], inactive=inactive)
        with self.assertRaisesRegex(ValueError, 'authenticated replacement'):
            brief.preserve(previous, amended)
        authorization = {'previous_hash': previous['observations'][0]['hash'],
                         'replacement_hash': amended['observations'][0]['hash'],
                         'replacement_source_sha256': new['source_sha256']}
        self.assertEqual(amended, brief.preserve(previous, amended, replacements=[authorization]))
        with self.assertRaisesRegex(ValueError, 'source-declared'):
            brief.bind(amendment, [{**amended_proposal, 'declaration_id': brief.inventory(amendment)[0]['id']}])
        unbracketed = PRODUCT.replace(' [open]', ' open')
        self.assertEqual(1, self.run_candidate(unbracketed)[0], 'Original brackets remain mandatory')
        for status, expected in [('open', 0), ('done', 0), ('Open', 1)]:
            with self.subTest(status=status):
                code, observed, _ = self.run_candidate(PRODUCT.replace(' [open]', ' ' + status),
                    records=records, manifest=amended, inactive=inactive)
                self.assertEqual(expected, code, observed)

    def test_trusted_runner_cannot_import_candidate_dependency_shadow(self):
        shadow = "import sys\nprint('candidate base64 shadow ran')\nsys.exit(0)\n"
        code, observation, files = self.run_candidate(PRODUCT, extra_files={'base64.py': shadow})
        self.assertEqual(0, code, observation)
        self.assertEqual('PASS', observation['verdict'])
        self.assertEqual([0, 0], [row['exit_code'] for row in observation['steps']])
        self.assertEqual({'base64.py', 'todo.py'}, set(files))

    def test_shell_metacharacters_are_literal_argument_data(self):
        value = "brief '$(touch escaped)' ; & `touch escaped`"
        code, observation, files = self.run_candidate(PRODUCT, value)
        self.assertEqual(0, code, observation)
        self.assertEqual(value, observation['steps'][0]['argv'][1])
        self.assertEqual(['todo.py'], files)

    def test_whitespace_and_extra_output_lines_are_not_normalized_away(self):
        for suffix in [' [open] ', ' [open]\\nextra']:
            with self.subTest(suffix=suffix):
                code, observation, _ = self.run_candidate(PRODUCT.replace(' [open]', suffix))
                self.assertEqual(1, code)
                self.assertIn('original brief format', observation['reason'])

    def test_live_multi_item_listing_of_a_correct_product_passes_and_its_mutant_fails(self):
        manifest = brief.bind(self.sources, [listing_proposal(self.sources)])
        # The pattern both live runs sealed: the bound item, not the whole listing.
        self.assertEqual(r'1\ buy\ milk\ \[(?:open|done)\]', manifest['observations'][0]['pattern'])
        code, observation, files = self.run_candidate(LIST_PRODUCT, manifest=manifest)
        self.assertEqual(0, code, observation)
        self.assertEqual('PASS', observation['verdict'])
        self.assertEqual(b'1 buy milk [done]\n2 walk dog [open]\n',
                         base64.b64decode(observation['steps'][3]['stdout_base64']))
        self.assertEqual(['todo.py'], files)
        unbracketed = LIST_PRODUCT.replace("[{'done' if item['done'] else 'open'}]",
                                           "{'done' if item['done'] else 'open'}")
        code, observation, _ = self.run_candidate(unbracketed, manifest=manifest)
        self.assertEqual(1, code, observation)
        self.assertEqual([0, 0, 0, 0], [step['exit_code'] for step in observation['steps']])
        self.assertIn('original brief format', observation['reason'])

    def test_every_listed_line_must_have_the_declared_format(self):
        manifest = brief.bind(self.sources, [listing_proposal(self.sources)])
        observation = manifest['observations'][0]
        # Derived from the sealed manifest, never stored in it: no new hash or manifest version.
        self.assertEqual(r'[^\s]+\ (?:buy\ milk|walk\ dog)\ \[(?:open|done)\]', brief.line_pattern(observation))
        self.assertEqual({'declaration', 'proposal', 'pattern', 'hash'}, set(observation))
        for listing, accepted, replayed in LISTINGS:
            with self.subTest(listing=listing):
                reason = brief.output_reason(listing, observation['pattern'], brief.line_pattern(observation))
                self.assertEqual(accepted, reason == '', reason)
                if not replayed:
                    continue
                code, observed, _ = self.run_candidate(printing(listing), manifest=manifest)
                self.assertEqual(0 if accepted else 1, code, observed)
                self.assertEqual(reason, observed['reason'])

    def test_the_bound_item_is_always_a_valid_listed_line(self):
        # An ID argument is any string, but the line pattern's opaque ID is one word. The
        # bound item alone passed before the per-line rule and must still pass (#452 review).
        steps = [{'argv': ['add', 'buy milk']}, {'argv': ['complete', 'a b']}, {'argv': ['list']}]
        bindings = [{'placeholder': 'TEXT', 'step': 0, 'argument': 1}, {'placeholder': 'ID', 'step': 1, 'argument': 1}]
        manifest = brief.bind(self.sources, [{**listing_proposal(self.sources), 'steps': steps, 'observe_step': 2,
                                              'bindings': bindings}])
        observation = manifest['observations'][0]
        line = brief.line_pattern(observation)
        for listing, accepted in [(b'a b buy milk [done]\n', True), (b'a b buy milk [done]\nc buy milk [open]\n', True),
                                  (b'a b buy milk done\n', False), (b'c d buy milk [done]\n', False)]:
            with self.subTest(listing=listing):
                reason = brief.output_reason(listing, observation['pattern'], line)
                self.assertEqual(accepted, reason == '', reason)
                code, observed, _ = self.run_candidate(printing(listing), manifest=manifest)
                self.assertEqual((0 if accepted else 1, reason), (code, observed['reason']), observed)

    def test_listed_text_is_limited_to_values_supplied_before_the_listing(self):
        records = sources(TASK.split('; `todo.py complete ID`')[0])
        later = {'steps': [{'argv': ['add', 'brief-probe']}, {'argv': ['list']}, {'argv': ['add', 'later']}]}
        observation = brief.bind(records, [{**proposal(records), **later}])['observations'][0]
        line = brief.line_pattern(observation)
        self.assertEqual(r'[^\s]+\ (?:brief\-probe)\ \[(?:open|done)\]', line)
        self.assertEqual('', brief.output_reason(b'a brief-probe [open]\n', observation['pattern'], line))
        self.assertIn('original brief format', brief.output_reason(
            b'a brief-probe [open]\nb later [open]\n', observation['pattern'], line))

    def test_nonzero_exit_cannot_pass_even_when_output_format_is_correct(self):
        code, observation, _ = self.run_candidate(PRODUCT + '\nsys.exit(7)\n')
        self.assertEqual(1, code)
        self.assertEqual(7, observation['steps'][0]['exit_code'])
        self.assertIn('exited nonzero', observation['reason'])

    def test_symlink_entrypoint_cannot_execute_outside_candidate(self):
        with tempfile.TemporaryDirectory(prefix='brief-acceptance-path-test-') as directory:
            folder = Path(directory)
            root = folder / 'candidate'
            root.mkdir()
            marker = folder / 'executed'
            outside = folder / 'outside.py'
            outside.write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
            (root / 'todo.py').symlink_to(outside)
            command = brief.commands(self.sources, self.manifest, python=sys.executable)[0]
            result = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True, timeout=20)
            self.assertEqual(1, result.returncode)
            self.assertIn('escapes the clean source tree', json.loads(result.stdout)['reason'])
            self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()

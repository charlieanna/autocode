import copy
import hashlib
import unittest
from pathlib import Path

import autocode_risk_acceptance as risk

QUEUE = (
    'Build a durable SQLite LeaseQueue(path), shared safely across instances and threads. '
    'enqueue(id, payload) accepts string payloads, returns True once, False for an identical replay, '
    'and raises ValueError for a conflicting payload even after completion. '
    'claim(now, lease_seconds) returns None or a dict with id, payload, token, deadline. '
    'It claims the earliest-enqueued unfinished job whose lease is absent or expired, including exactly at its deadline; '
    'token is fresh and opaque on every claim. now is a caller-injected nonnegative integer; '
    'lease_seconds is a positive integer; bool is invalid for either. '
    'ack(id, token, now) returns True only for the current unexpired lease and completes the job; '
    'otherwise False with no effect. nack(id, token, now) similarly releases a current unexpired lease for immediate retry. '
    'pending() counts unfinished jobs including leased ones. '
    'Expired acknowledgments and old tokens must never complete a reassigned job. '
    'Enqueue and claims must be atomic under contention and survive restart. '
    'Include public regression tests, with injected time and no sleeps.'
)
OUTBOX = (
    'Build a local SQLite Store(path) for orders and their transactional outbox. '
    'create_order(order_id, amount, key) returns True on a new order and False for an identical idempotent replay, '
    'and atomically commits exactly one durable event together with the order. amount is a positive integer excluding bool. '
    'Reusing key with different order/amount or creating an existing order under another key raises ValueError, '
    'with no extra order/event or reserved key. orders() returns a dict of order IDs to amounts. '
    'pending(limit=100) returns up to limit unpublished dicts in commit order, '
    'each containing event_id (stable opaque string), order_id, amount; limit must be a positive integer. '
    'publish(sink, limit=100) calls sink(event) for pending events in order, '
    'acknowledging only after each successful callback, and returns the count acknowledged. '
    'If the callback raises, propagate that exception immediately; earlier successes stay acknowledged, '
    'the failing event and later ones remain pending. Callbacks may use the store public read API. '
    'A sink may record a delivery then fail: retry must use the same event_id, enabling an idempotent sink to deduplicate; '
    'do not claim exactly-once delivery. Reopening a store preserves orders and events. '
    'Concurrent create_order requests for the same key commit once. Only one publisher at a time is in scope.'
)
TARGETS = [
    {'module': 'leasequeue', 'path': 'leasequeue/__init__.py', 'kind': 'initial_public_import',
     'sha256': hashlib.sha256(b'original leasequeue package').hexdigest()},
    {'module': 'outbox', 'path': 'outbox/__init__.py', 'kind': 'initial_public_import',
     'sha256': hashlib.sha256(b'original outbox package').hexdigest()},
]


def source(text, ident='task:0', kind='task'):
    return {'id': ident, 'kind': kind, 'text': text}


def proposal(declaration, module=None, criteria=None):
    module = module or ('leasequeue' if declaration['protocol'] == 'lease_queue_lifecycle_v1' else 'outbox')
    return {'declaration_id': declaration['id'], 'criterion_ids': criteria or ['AC-lifecycle'], 'module': module}


class RiskAcceptanceTests(unittest.TestCase):
    def test_both_explicit_public_api_promises_are_mandatory_and_source_bound(self):
        sources = [source(QUEUE), source(OUTBOX, 'answer:outbox', 'user_answer')]
        declarations = risk.inventory(sources, TARGETS)
        self.assertEqual({row['protocol'] for row in declarations}, risk.PROTOCOLS)
        self.assertTrue(all(row['supported'] for row in declarations), declarations)
        manifest = risk.bind(sources, [proposal(row) for row in declarations], public_targets=TARGETS)
        self.assertEqual(risk.verify(sources, manifest, public_targets=TARGETS), manifest)
        for row in manifest['observations']:
            self.assertEqual(risk.normalize_observation(row), row)
            text = next(item['text'] for item in sources if item['id'] == row['declaration']['source_id'])
            binding = row['source_bindings'][0]
            start, end = binding['span']
            self.assertEqual(text[start:end], binding['quote'])
            self.assertEqual(binding['source_sha256'], hashlib.sha256(text.encode()).hexdigest())
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            risk.bind(sources, [], public_targets=TARGETS)

    def test_one_human_task_with_both_apis_has_independent_protocols_and_exact_quotes(self):
        text = QUEUE + '\n\n' + OUTBOX
        sources = [source(text)]
        declarations = risk.inventory(sources, TARGETS)
        by_class = {row['class_name']: row for row in declarations}
        self.assertEqual(set(by_class), {'LeaseQueue', 'Store'})
        self.assertEqual(by_class['LeaseQueue']['protocol'], 'lease_queue_lifecycle_v1')
        self.assertEqual(by_class['Store']['protocol'], 'transactional_outbox_lifecycle_v1')
        self.assertTrue(all(row['supported'] for row in declarations), declarations)
        self.assertEqual(by_class['LeaseQueue']['source_quote'], QUEUE)
        self.assertEqual(by_class['Store']['source_quote'], OUTBOX)
        manifest = risk.bind(sources, [proposal(row) for row in declarations], public_targets=TARGETS)
        self.assertEqual(risk.verify(sources, manifest, public_targets=TARGETS), manifest)
        by_module = {row['target']['module']: row for row in manifest['observations']}
        self.assertEqual(by_module['leasequeue']['target']['class_name'], 'LeaseQueue')
        self.assertEqual(by_module['outbox']['target']['class_name'], 'Store')
        for row in manifest['observations']:
            binding = row['source_bindings'][0]
            self.assertEqual(text[slice(*binding['span'])], binding['quote'])
            self.assertEqual(binding['source_sha256'], hashlib.sha256(text.encode()).hexdigest())
            self.assertEqual(risk.normalize_observation(row), row)

    def test_ordinary_and_nonhuman_text_add_no_lifecycle_battery(self):
        for text in ('Build a helpful to-do CLI.', 'Explain durability and performance tradeoffs.',
                     'Store(path) reads strings. Include unit tests.'):
            self.assertEqual(risk.inventory([source(text)], TARGETS), [])
        self.assertEqual(risk.inventory([source(QUEUE, kind='assistant'),
                                         source(OUTBOX, 'delegated:1', 'delegated')], TARGETS), [])
        empty = risk.bind([source('Build a to-do CLI.')], [], public_targets=[])
        self.assertEqual(empty['observations'], [])

    def test_a_negated_example_is_not_a_promise_and_previous_line_does_not_negate_the_task(self):
        excluded = QUEUE.replace('Build a durable', 'Do not build a durable')
        self.assertEqual(risk.inventory([source(excluded)], TARGETS), [])
        requested = 'Do not create an external broker.\n' + QUEUE
        rows = risk.inventory([source(requested)], TARGETS)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['supported'])

    def test_known_family_without_required_semantics_is_explicitly_unsupported(self):
        changed = QUEUE.replace('fresh and opaque on every claim', 'a process-local sequence number')
        declarations = risk.inventory([source(changed)], TARGETS)
        self.assertEqual(len(declarations), 1)
        self.assertFalse(declarations[0]['supported'])
        self.assertIn('fresh_opaque_token', declarations[0]['missing'])
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            risk.bind([source(changed)], [proposal(declarations[0])], public_targets=TARGETS)

    def test_an_unsupported_declaration_names_the_missing_facts_with_or_without_a_reviewer_row(self):
        # #451: a reworded brief used to stop on "requires exactly one independent observation"
        # when the Reviewer left the unsupported row out, which named neither the gap nor the fix.
        changed = QUEUE.replace('token is fresh and opaque on every claim', 'tokens identify claims')
        declaration = risk.inventory([source(changed)], TARGETS)[0]
        self.assertEqual(['fresh_opaque_token'], declaration['missing'])
        for rows in ([], [proposal(declaration)]):
            with self.subTest(rows=len(rows)), self.assertRaisesRegex(
                    ValueError, r'unsupported: LeaseQueue\(\.\.\.\).*does not state: the token is fresh and opaque '
                                r'on every claim \(fresh_opaque_token\).*feedback that says it changes LeaseQueue'):
                risk.bind([source(changed)], rows, public_targets=TARGETS)

    def test_held_out_paraphrases_of_the_catalog_briefs_keep_the_same_proof(self):
        # #451: renaming LeaseQueue(path) to LeaseQueue(db_path) let the token mutant complete with no
        # lifecycle proof. These rewordings never appear in a catalog brief; each must keep the same
        # supported declaration, so the same fixed protocol runs.
        catalog = Path(__file__).resolve().parents[1] / 'scenarios' / 'catalog'
        queue = (catalog / 'ladder-18-durable-lease-queue' / 'brief.md').read_text()
        outbox = (catalog / 'ladder-19-transactional-outbox' / 'brief.md').read_text()
        cases = {
            'queue db_path': (queue, [('LeaseQueue(path)', 'LeaseQueue(db_path)')]),
            'queue database': (queue, [('LeaseQueue(path)', 'LeaseQueue( database )')]),
            'queue token wording': (queue, [('token is fresh and opaque on every claim',
                                             'every claim issues a new unguessable token')]),
            'queue both': (queue, [('LeaseQueue(path)', 'LeaseQueue(filename)'),
                                   ('token is fresh and opaque on every claim',
                                    'each claim returns a unique random token')]),
            'outbox sqlite_path': (outbox, [('Store(path)', 'Store(sqlite_path)')]),
        }
        for label, (brief, replacements) in cases.items():
            with self.subTest(label):
                original = risk.inventory([source(brief)], TARGETS)
                text = brief
                for old, new in replacements:
                    self.assertIn(old, text)
                    text = text.replace(old, new)
                reworded = risk.inventory([source(text)], TARGETS)
                self.assertEqual(1, len(original))
                self.assertEqual(1, len(reworded))
                self.assertTrue(reworded[0]['supported'], reworded[0]['missing'])
                for key in ('protocol', 'class_name', 'constructor', 'methods', 'promises', 'allowed_modules'):
                    self.assertEqual(original[0][key], reworded[0][key], key)
                manifest = risk.bind([source(text)], [proposal(reworded[0])], public_targets=TARGETS)
                self.assertEqual(original[0]['class_name'], manifest['observations'][0]['target']['class_name'])

    def test_contention_is_promised_only_where_the_source_states_it_for_that_api(self):
        # #451: a stated contention promise adds the runner's race to the same observation; durability
        # wording alone, an exclusion, or no statement adds nothing.
        contention = risk.protocols.CONTENTION
        queue_sentence = 'Enqueue and claims must be atomic under contention and survive restart.'
        outbox_sentence = 'Concurrent create_order requests for the same key commit once.'
        cases = [(QUEUE, True), (OUTBOX, True),
                 (QUEUE.replace(queue_sentence, 'Enqueue and claims survive restart.'), False),
                 (QUEUE.replace(queue_sentence, 'Enqueue and claims need not be atomic under contention; '
                                                'they survive restart.'), False),
                 (QUEUE.replace(queue_sentence, 'Claims made concurrently by many workers stay atomic, '
                                                'and jobs survive restart.'), True),
                 # Any negation in the clause keeps it unraced (the grammar cannot place it); it is disclosed.
                 (QUEUE.replace(queue_sentence, 'Enqueue and claims must be atomic under contention, so a job '
                                                'is never leased twice, and survive restart.'), False),
                 (OUTBOX.replace(outbox_sentence, ''), False),
                 (OUTBOX.replace(outbox_sentence, 'Concurrent create_order requests are out of scope.'), False),
                 (OUTBOX.replace(outbox_sentence, 'create_order calls made in parallel for one key succeed once.'), True)]
        for text, promised in cases:
            with self.subTest(text=text[-160:]):
                declaration = risk.inventory([source(text)], TARGETS)[0]
                self.assertTrue(declaration['supported'], declaration['missing'])
                self.assertEqual(promised, contention in declaration['promises'])
                manifest = risk.bind([source(text)], [proposal(declaration)], public_targets=TARGETS)
                self.assertEqual(manifest, risk.verify([source(text)], manifest, public_targets=TARGETS))
        # The promise comes from the person's words: dropping or inventing it cannot pass verification.
        declaration = risk.inventory([source(QUEUE)], TARGETS)[0]
        manifest = risk.bind([source(QUEUE)], [proposal(declaration)], public_targets=TARGETS)
        for promises in (declaration['promises'][:-1], declaration['promises'] + ['performance_limit']):
            forged = copy.deepcopy(manifest)
            row = forged['observations'][0]
            row['declaration']['promises'] = promises
            row['hash'] = risk.digest({key: value for key, value in row.items() if key != 'hash'})
            forged['hash'] = risk.digest({key: value for key, value in forged.items() if key != 'hash'})
            with self.subTest(promises=promises[-1]), self.assertRaises(ValueError):
                risk.verify([source(QUEUE)], forged, public_targets=TARGETS)

    def test_a_capitalized_call_without_a_lifecycle_family_declares_nothing(self):
        for text in ('Path(root_dir) holds durable files that survive restart. Include tests.',
                     'Build Counter(items) with durable restart semantics and a claim on correctness.'):
            self.assertEqual(risk.inventory([source(text)], TARGETS), [])
        # An exception call inside the API description neither declares nor truncates the queue.
        text = QUEUE.replace('raises ValueError for a conflicting payload',
                             'raises ValueError(message) for a conflicting payload')
        rows = risk.inventory([source(text)], TARGETS)
        self.assertEqual([('LeaseQueue', True, text)], [(row['class_name'], row['supported'], row['source_quote'])
                                                        for row in rows])

    def test_a_renamed_storage_argument_declares_only_a_full_lifecycle_api(self):
        # #451 accepts LeaseQueue(db_path), but a storage-like argument other than `path` must not pull
        # an ordinary task into a lifecycle proof it can never satisfy: it declares a family only when
        # the API names that family's core methods (enqueue, claim and ack; create_order and publish).
        ordinary = ['Write TodoList(filename) with add(item) and claim(item); the list must survive restart.',
                    'Make TaskBoard(data_dir) where users claim(task) tasks; durable storage please.',
                    'Build Blog(db_path) with publish(post) and drafts(). Reopening the app keeps drafts.',
                    'Write Client(base_url) with publish(message) to our outbox endpoint.']
        for text in ordinary:
            with self.subTest(text=text):
                self.assertEqual([], risk.inventory([source(text)], TARGETS))
                self.assertEqual([], risk.bind([source(text)], [], public_targets=TARGETS)['observations'])
        # `Name(path)` keeps its earlier recognition: the same weak text stays explicitly unsupported.
        legacy = 'Build Blog(path) with publish(post) and drafts(). Reopening the app keeps drafts.'
        self.assertEqual([False], [row['supported'] for row in risk.inventory([source(legacy)], TARGETS)])
        # A renamed argument with the family's core API is still a declaration, gaps included.
        gap = QUEUE.replace('LeaseQueue(path)', 'LeaseQueue(db_path)').replace(
            'token is fresh and opaque on every claim', 'tokens identify claims')
        self.assertEqual([(False, ['fresh_opaque_token'])],
                         [(row['supported'], row['missing']) for row in risk.inventory([source(gap)], TARGETS)])
        outbox = OUTBOX.replace('Store(path)', 'Store(db_file)')
        self.assertEqual([True], [row['supported'] for row in risk.inventory([source(outbox)], TARGETS)])

    def test_a_renamed_argument_call_that_declares_nothing_never_splits_a_declaration(self):
        # The renamed-argument match (#451) made `Logger(log_file)` a declaration boundary: placed
        # between LeaseQueue(path) and its methods, it took the API text, so LeaseQueue declared nothing
        # and Logger became an unsupported queue the plan could never satisfy. A renamed-argument call
        # that declares no family is not a boundary; `Name(path)` splits a source as it always did.
        for brief, first_sentence in ((QUEUE, 'threads. '), (OUTBOX, 'outbox. ')):
            original = risk.inventory([source(brief)], TARGETS)
            for extra in ('Diagnostics go through Logger(log_file). ', 'Settings load from Config(config_path). '):
                text = brief.replace(first_sentence, first_sentence + extra, 1)
                with self.subTest(extra=extra, constructor=original[0]['constructor']):
                    rows = risk.inventory([source(text)], TARGETS)
                    self.assertEqual(1, len(rows))
                    self.assertTrue(rows[0]['supported'], rows[0]['missing'])
                    for key in ('protocol', 'class_name', 'constructor', 'methods', 'promises'):
                        self.assertEqual(original[0][key], rows[0][key], key)
                    self.assertIn(extra.strip(), rows[0]['source_quote'])
        # A negated renamed-argument family is no boundary either.
        text = QUEUE.replace('threads. ', 'threads. Do not build Archive(db_path) with enqueue(id), claim(now) and '
                                          'ack(id). ', 1)
        self.assertEqual([('LeaseQueue', True)], [(row['constructor'], row['supported'])
                                                  for row in risk.inventory([source(text)], TARGETS)])
        # A renamed-argument call that declares a family still starts its own declaration.
        second = 'Separately build JobQueue(db_path) with enqueue(id, payload), claim(now) and ack(id); it is durable.'
        rows = risk.inventory([source(QUEUE + '\n\n' + second)], TARGETS)
        self.assertEqual([('JobQueue', False), ('LeaseQueue', True)],
                         sorted((row['constructor'], row['supported']) for row in rows))

    def test_module_binding_requires_original_target_and_human_import(self):
        text = 'from leasequeue import LeaseQueue\n' + QUEUE
        declarations = risk.inventory([source(text)], TARGETS)
        with self.assertRaisesRegex(ValueError, 'original public inventory'):
            risk.bind([source(text)], [proposal(declarations[0], 'outbox')], public_targets=TARGETS)
        for module in ('fake_correct_queue', '../leasequeue', 'tests.queue', 'leasequeue;true'):
            with self.subTest(module=module), self.assertRaises(ValueError):
                risk.bind([source(text)], [proposal(declarations[0], module)], public_targets=TARGETS)
        self.assertEqual(risk.inventory([source(text)], [])[0]['allowed_modules'], [])
        with self.assertRaises(ValueError):
            risk.bind([source(text)], [proposal(declarations[0])], public_targets=[])

    def test_source_spelled_constructor_alias_is_allowed_but_reviewer_alias_is_not(self):
        text = 'from leasequeue import LeaseQueue as Q\n' + QUEUE.replace('LeaseQueue(path)', 'Q(path)')
        declaration = risk.inventory([source(text)], TARGETS)[0]
        self.assertEqual((declaration['constructor'], declaration['class_name']), ('Q', 'LeaseQueue'))
        manifest = risk.bind([source(text)], [proposal(declaration)], public_targets=TARGETS)
        self.assertEqual(manifest['observations'][0]['target']['class_name'], 'LeaseQueue')
        bad = {**proposal(declaration), 'class_name': 'CorrectDecoy'}
        with self.assertRaises(ValueError):
            risk.bind([source(text)], [bad], public_targets=TARGETS)

    def test_reviewer_cannot_supply_methods_expected_outputs_or_execution_script(self):
        declarations = risk.inventory([source(QUEUE)], TARGETS)
        for key, value in (('methods', {'ack': 'always_true'}), ('expected', True),
                           ('script', 'assert True'), ('protocol', 'weaker_protocol')):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'select only'):
                risk.bind([source(QUEUE)], [{**proposal(declarations[0]), key: value}], public_targets=TARGETS)

    def test_every_case_and_unique_nonempty_criteria_are_required(self):
        sources = [source(QUEUE), source(OUTBOX, 'task:1')]
        declarations = risk.inventory(sources, TARGETS)
        valid = [proposal(row) for row in declarations]
        for rows in (valid[:1], valid + valid[:1], [{**row, 'criterion_ids': []} for row in valid],
                     [{**row, 'criterion_ids': ['AC1', 'AC1']} for row in valid]):
            with self.assertRaises(ValueError):
                risk.bind(sources, rows, public_targets=TARGETS)

    def test_binding_is_canonical_and_changing_real_human_source_invalidates_it(self):
        sources = [source(QUEUE + '\nA note with café and λ.')]
        declarations = risk.inventory(sources, TARGETS)
        manifest = risk.bind(sources, [proposal(declarations[0], criteria=['AC2', 'AC1'])], public_targets=TARGETS)
        other = risk.bind(sources, [proposal(declarations[0], criteria=['AC1', 'AC2'])],
                          public_targets=list(reversed(TARGETS)))
        self.assertEqual(manifest, other)
        with self.assertRaises(ValueError):
            risk.verify([source(QUEUE + '\nA changed note.')], manifest, public_targets=TARGETS)
        altered_targets = copy.deepcopy(TARGETS)
        altered_targets[0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            risk.verify(sources, manifest, public_targets=altered_targets)

    def test_forged_observation_and_recomputed_hash_do_not_replace_source_authority(self):
        sources = [source(QUEUE)]
        declarations = risk.inventory(sources, TARGETS)
        manifest = risk.bind(sources, [proposal(declarations[0])], public_targets=TARGETS)
        forged = copy.deepcopy(manifest)
        row = forged['observations'][0]
        row['target']['methods']['ack'] = 'complete_any_job'
        row['hash'] = risk.digest({key: value for key, value in row.items() if key != 'hash'})
        forged['hash'] = risk.digest({key: value for key, value in forged.items() if key != 'hash'})
        with self.assertRaises(ValueError):
            risk.verify(sources, forged, public_targets=TARGETS)
        forged = copy.deepcopy(manifest)
        forged['observations'][0]['declaration']['source_quote'] = QUEUE.replace('never', 'always')
        with self.assertRaises(ValueError):
            risk.verify(sources, forged, public_targets=TARGETS)

    def test_old_observation_survives_new_feedback_and_only_exact_replacement_is_allowed(self):
        initial = [source(QUEUE)]
        declaration = risk.inventory(initial, TARGETS)[0]
        old = risk.bind(initial, [proposal(declaration)], public_targets=TARGETS)
        added = [*initial, source('Change LeaseQueue(path):\n' + QUEUE, 'feedback:revision', 'user_feedback')]
        declarations = risk.inventory(added, TARGETS)
        new_decl = next(row for row in declarations if row['source_id'] == 'feedback:revision')
        kept = risk.bind(added, [proposal(row) for row in declarations], public_targets=TARGETS)
        self.assertIn(old['observations'][0], kept['observations'])
        self.assertEqual(risk.preserve(old, kept), kept)
        revised = risk.bind(added, [proposal(new_decl)], public_targets=TARGETS, inactive=[declaration['id']])
        with self.assertRaisesRegex(ValueError, 'cannot remove'):
            risk.preserve(old, revised)
        replacement = {'previous_hash': old['observations'][0]['hash'],
                       'replacement_hash': revised['observations'][0]['hash'],
                       'replacement_source_sha256': new_decl['source_sha256']}
        self.assertEqual(risk.preserve(old, revised, replacements=[replacement]), revised)
        with self.assertRaises(ValueError):
            risk.preserve(old, revised, replacements=[{**replacement, 'replacement_source_sha256': '0' * 64}])

    def test_source_and_target_inventory_cannot_be_forged_or_ambiguous(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            risk.inventory([source(QUEUE), source(OUTBOX)], TARGETS)
        for target in ({**TARGETS[0], 'path': '../leasequeue/__init__.py'},
                       {**TARGETS[0], 'path': 'other/__init__.py'},
                       {**TARGETS[0], 'kind': 'model_report'},
                       {**TARGETS[0], 'sha256': 'not-a-content-hash'}):
            with self.subTest(target=target), self.assertRaises(ValueError):
                risk.inventory([source(QUEUE)], [target])
        with self.assertRaises(ValueError):
            risk.inventory([source(QUEUE)], [TARGETS[0], TARGETS[0]])

    def test_full_human_quote_is_retained_and_empty_kind_or_criteria_cannot_bind(self):
        sources = [source(QUEUE)]
        declaration = risk.inventory(sources, public_targets=TARGETS)[0]
        self.assertEqual(declaration['source_span'], [0, len(QUEUE)])
        self.assertEqual(declaration['source_quote'], QUEUE)
        with self.assertRaises(ValueError):
            risk.inventory([source(QUEUE, kind=[])], TARGETS)
        with self.assertRaises(ValueError):
            risk.bind(sources, [proposal(declaration, criteria=[' '])], public_targets=TARGETS)

    def test_preservation_does_not_accept_forged_manifest_pins_or_duplicate_rows(self):
        sources = [source(QUEUE)]
        declaration = risk.inventory(sources, TARGETS)[0]
        original = risk.bind(sources, [proposal(declaration)], public_targets=TARGETS)
        changed = copy.deepcopy(original)
        changed['hash'] = '0' * 64
        with self.assertRaises(ValueError):
            risk.preserve(original, changed)
        changed = copy.deepcopy(original)
        changed['observations'].append(changed['observations'][0])
        changed['hash'] = risk.digest({key: value for key, value in changed.items() if key != 'hash'})
        with self.assertRaises(ValueError):
            risk.preserve(original, changed)

    def test_inactive_is_caller_authority_and_cannot_name_an_unknown_obligation(self):
        with self.assertRaisesRegex(ValueError, 'exact known'):
            risk.bind([source(QUEUE)], [], public_targets=TARGETS, inactive=['invented-id'])


if __name__ == '__main__':
    unittest.main()

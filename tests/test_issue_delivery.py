"""A delivery receipt authenticates bytes and approval without rebadging old proof."""
import contextlib
import copy
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_evidence_export as export
import autocode_evidence_provenance as provenance
import autocode_issue as issue
import autocode_issue_delivery as delivery
import autocode_source_snapshot as source


class IssueDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='issue-delivery-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git('init', '-q', '-b', 'main')
        (self.root/'old.txt').write_text('removed by the task\n')
        (self.root/'.gitignore').write_text('.autocode/\n')
        self.git('add', 'old.txt', '.gitignore')
        self.git('commit', '-qm', 'base')
        (self.root/'old.txt').unlink()
        self.script = self.root/'odd\n|script.py'
        self.script.write_text('print("checked")\n')
        self.script.chmod(0o755)
        (self.root/'link').symlink_to(self.script.name)
        (self.root/'.autocode').mkdir()
        (self.root/'.autocode'/'state.json').write_text('private generated run data')
        current = source.snapshot(self.root)
        self.view = {'approved_contract': {'token': 'r1:approved', 'body': {}}}
        self.report = {'availability': 'current', 'binding': 'facts', 'json_sha256': 'json', 'markdown_sha256': 'md',
            'document': {'plan': {'contract_token': 'r1:approved', 'criteria_revision': 'criteria'},
                         'revision': {'source_revision': current['revision']}}}

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=T', '-c', 'user.email=t@example.test',
            *args], capture_output=True, text=True, check=True).stdout.strip()

    def published(self):
        receipt = delivery.capture(self.root, self.view, self.report)
        issue.stage_source(self.root)
        delivery.unchanged(self.root, receipt)
        self.git('commit', '-qm', 'delivery')
        receipt = delivery.seal(self.root, receipt)
        report = {**self.report, 'availability': 'recorded'}
        delivery.verify(self.root, self.view, report, receipt)
        return receipt, report

    def test_first_and_repeat_delivery_preserve_deletions_modes_and_literal_names(self):
        self.git('config', 'core.fileMode', 'false')
        receipt, report = self.published()
        self.assertEqual('deleted', receipt['tested_source']['files']['old.txt'])
        self.assertTrue(receipt['tested_source']['files'][self.script.name].startswith('executable:'))
        self.assertIn('100755', self.git('ls-tree', 'HEAD', '--', self.script.name))
        self.assertIn('120000', self.git('ls-tree', 'HEAD', 'link'))
        self.assertNotIn('.autocode/', self.git('ls-tree', '-r', '--name-only', 'HEAD'))
        self.assertEqual('recorded', report['availability'])
        self.assertNotEqual(receipt['tested_source']['head'], receipt['delivery_commit'])

    def test_repeat_refuses_changed_source_deletion_modes_or_untracked_files(self):
        receipt, report = self.published()
        saved = self.script.read_bytes()
        changes = [('bytes', lambda: self.script.write_bytes(saved+b'# later\n')),
                   ('mode', lambda: self.script.chmod(0o644)),
                   ('deletion', lambda: self.script.unlink()),
                   ('restored deletion', lambda: (self.root/'old.txt').write_text('later')),
                   ('untracked', lambda: (self.root/'extra.py').write_text('pass'))]
        for label, change in changes:
            change()
            with self.subTest(label=label), self.assertRaises(ValueError):
                delivery.verify(self.root, self.view, report, receipt)
            self.script.write_bytes(saved)
            self.script.chmod(0o755)
            for name in ('old.txt', 'extra.py'):
                (self.root/name).unlink(missing_ok=True)

    def test_repeat_refuses_different_commit_plan_criteria_digests_and_incomplete_receipts(self):
        receipt, report = self.published()
        for field in ('binding', 'json_sha256', 'markdown_sha256'):
            changed = {**report, field: 'changed'}
            with self.subTest(field=field), self.assertRaises(ValueError):
                delivery.verify(self.root, self.view, changed, receipt)
        changed = copy.deepcopy(report)
        changed['document']['plan']['criteria_revision'] = 'new criteria'
        with self.assertRaises(ValueError): delivery.verify(self.root, self.view, changed, receipt)
        with self.assertRaises(ValueError):
            delivery.verify(self.root, {'approved_contract': {'token': 'r2:new'}}, report, receipt)
        with self.assertRaises(ValueError): delivery.verify(self.root, self.view, report, {'version': 1})
        changed_receipt = copy.deepcopy(receipt)
        changed_receipt['tested_source']['files'].pop(self.script.name)
        with self.assertRaisesRegex(ValueError, 'manifest'):
            delivery.verify(self.root, self.view, report, changed_receipt)
        self.git('commit', '-q', '--allow-empty', '-m', 'a different commit')
        with self.assertRaisesRegex(ValueError, 'HEAD/tree'):
            delivery.verify(self.root, self.view, report, receipt)

    def test_first_delivery_refuses_source_changed_after_current_report(self):
        self.script.write_text('changed\n')
        with self.assertRaisesRegex(ValueError, 'Source changed'):
            delivery.capture(self.root, self.view, self.report)

    def test_repeat_refuses_stale_checkpoint_facts_with_intact_pair_and_delivery(self):
        state = {'status': 'TASK_COMPLETE', 'base_commit': 'base',
                 'findings_ledger': [{'id': 'F1', 'status': 'resolved', 'finding': 'Checked detail'}]}
        supplied = {'status': 'TASK_COMPLETE', 'workflow': 'build', 'evidence': {
            'validator_source_revision': source.snapshot(self.root)['revision'], 'base_commit': 'base'},
            'verification': {'contract_token': 'r1:approved', 'criteria_revision': 'criteria'},
            'usage': {'accounting': {'issues': []}}}
        report_root = self.root/'.autocode'/'canonical'
        export.publish(report_root, state, supplied, provenance.configured({}, 'fake'))
        anchor = state['evidence_export']
        current = export.read(report_root, anchor, expected_binding=export.binding(state, {'issues': []}),
                              current=True, include=True)
        receipt = delivery.capture(self.root, self.view, current)
        issue.stage_source(self.root)
        self.git('commit', '-qm', 'delivery')
        receipt = delivery.seal(self.root, receipt)
        historical = export.read(report_root, anchor, expected_binding=export.binding(state, {'issues': []}), include=True)
        delivery.verify(self.root, self.view, historical, receipt)
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in report_root.iterdir()}
        head, tree = self.git('rev-parse', 'HEAD'), self.git('write-tree')
        changed = copy.deepcopy(state)
        changed['findings_ledger'][0]['finding'] = 'Revised resolved detail'
        stale = export.read(report_root, anchor, expected_binding=export.binding(changed, {'issues': []}),
                            current=True, include=True)
        self.assertEqual('stale', stale['availability'])
        self.assertEqual(current['markdown'], stale['markdown'])
        with self.assertRaisesRegex(ValueError, 'stale'):
            delivery.verify(self.root, self.view, stale, receipt)
        self.assertEqual(head, self.git('rev-parse', 'HEAD'))
        self.assertEqual(tree, self.git('write-tree'))
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in report_root.iterdir()})

    def test_publication_pushes_the_receipt_commit_when_HEAD_changes_after_final_verification(self):
        temporary = tempfile.TemporaryDirectory(prefix='issue-delivery-remote-')
        self.addCleanup(temporary.cleanup)
        remote = Path(temporary.name)/'acme/widgets.git'
        subprocess.run(['git', 'init', '-q', '--bare', str(remote)], check=True)
        self.git('remote', 'add', 'origin', str(remote))
        record = {'owner': 'acme', 'repo': 'widgets', 'number': 7, 'slug': 'acme-widgets-7',
                  'title': 'Deliver checked source', 'worktree': str(self.root), 'run_dir': str(self.root/'.autocode/run'),
                  'base_commit': self.git('rev-parse', 'HEAD'), 'remote': 'origin', 'branch': 'issue-7', 'options': []}
        issue.save(self.root, record)
        initial_head = record['base_commit']
        def public_status(**kwargs):
            report = {**self.report, 'availability': 'current' if self.git('rev-parse', 'HEAD') == initial_head else 'recorded',
                      'markdown': '## Exact canonical checked source\n'}
            return {**self.view, 'done': True, 'status': 'TASK_COMPLETE', 'evidence_report': report}
        run = Mock()
        run.status.side_effect = public_status
        run.evidence_report.side_effect = lambda **kwargs: public_status()['evidence_report']
        original_verify = delivery.verify
        injected = []
        def verify_with_late_commit(*args, **kwargs):
            original_verify(*args, **kwargs)
            if not injected:
                self.script.write_text('UNVALIDATED LATE COMMIT\n')
                (self.root/'unverified-only.py').write_text('pass\n')
                issue.stage_source(self.root)
                self.git('commit', '-qm', 'unverified late commit')
                injected.append(self.git('rev-parse', 'HEAD'))
        client = Mock()
        client.open_pull_request.return_value = {'html_url': 'https://github.example/acme/widgets/pull/1'}
        with patch.object(issue, 'task_run', return_value=run), patch.object(delivery, 'verify', verify_with_late_commit), \
             patch.object(issue.github, 'Client', return_value=client), contextlib.redirect_stdout(io.StringIO()):
            result = issue.main(['pr', '#7', '--open', '--project', str(self.root), '--base', 'main'])
        self.assertEqual(0, result)
        saved = issue.load(self.root, issue.github.IssueRef('acme', 'widgets', 7))
        receipt_commit = saved['delivery_receipt']['delivery_commit']
        published = subprocess.check_output(['git', '--git-dir', str(remote), 'rev-parse', 'refs/heads/issue-7'], text=True).strip()
        self.assertEqual(receipt_commit, published)
        self.assertNotEqual(injected[0], published)
        content = subprocess.check_output(['git', '--git-dir', str(remote), 'show', published+':'+self.script.name], text=True)
        self.assertEqual('print("checked")\n', content)
        body = client.open_pull_request.call_args.kwargs['body']
        self.assertIn('## Exact canonical checked source\n', body)
        self.assertIn(receipt_commit, body)
        self.assertNotIn('unverified-only.py', body)

    def _public_issue_fixture(self, *, run_name='run'):
        import autocode_evidence_document as document
        import autocode_evidence_export as export
        import autocode_evidence_provenance as provenance
        current = source.snapshot(self.root)
        supplied = document.build({'status': 'TASK_COMPLETE', 'workflow': 'build',
            'evidence': {'outcome': 'Deliver checked source', 'validator_source_revision': current['revision']},
            'verification': {'contract_token': 'r1:approved', 'criteria_revision': 'criteria'}},
            run_identity=run_name, completed_at=None, provenance=provenance.configured({}, 'fake'), binding='facts')
        report_root = self.root/'.autocode'/run_name
        anchor = export.write(report_root, supplied)
        canonical = export.read(report_root, anchor, current=True, include=True)
        self.assertEqual('current', canonical['availability'])
        record = {'owner': 'acme', 'repo': 'widgets', 'number': 7, 'slug': 'acme-widgets-7',
                  'title': 'Deliver checked source', 'worktree': str(self.root),
                  'run_dir': str(Path(canonical['json_path']).parent),
                  'base_commit': self.git('rev-parse', 'HEAD'), 'remote': 'origin', 'branch': 'issue-7', 'options': []}
        issue.save(self.root, record)
        run = Mock()
        run.status.return_value = {**self.view, 'done': True, 'status': 'TASK_COMPLETE', 'evidence_report': canonical}
        run.evidence_report.return_value = canonical
        return record, run, canonical

    def _refuses_before_source_or_network_mutation(self, record, run, canonical):
        before = {name: (Path(canonical[name+'_path']).read_bytes(),
                         Path(canonical[name+'_path']).stat().st_mtime_ns) for name in ('json', 'markdown')}
        head, tree = self.git('rev-parse', 'HEAD'), self.git('write-tree')
        saved = issue.record_path(self.root, issue.github.IssueRef('acme', 'widgets', 7)).read_bytes()
        stderr = io.StringIO()
        with patch.object(issue, 'task_run', return_value=run), patch.object(issue, 'stage_source') as stage, \
             patch.object(issue, 'git', wraps=issue.git) as git_call, \
             patch.object(issue.github, 'Client') as client, contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(stderr):
            result = issue.main(['pr', 'acme/widgets#7', '--open', '--project', str(self.root), '--base', 'main'])
        self.assertEqual(1, result, stderr.getvalue())
        self.assertIn('unsafe', stderr.getvalue())
        self.assertNotIn('ghp_CANARY0123456789abcdef', stderr.getvalue())
        stage.assert_not_called()
        client.assert_not_called()
        self.assertFalse(any(call.args[1] in ('commit', 'push') for call in git_call.call_args_list))
        self.assertEqual(head, self.git('rev-parse', 'HEAD'))
        self.assertEqual(tree, self.git('write-tree'))
        self.assertEqual(saved, issue.record_path(self.root, issue.github.IssueRef('acme', 'widgets', 7)).read_bytes())
        self.assertEqual(before, {name: (Path(canonical[name+'_path']).read_bytes(),
                                        Path(canonical[name+'_path']).stat().st_mtime_ns) for name in before})
        self.assertFalse((self.root/issue.STORE/(record['slug']+'-pr.md')).exists())

    def test_unsafe_canonical_or_wrapper_refuses_before_staging_or_push(self):
        canary = 'ghp_CANARY0123456789abcdef'
        record, run, canonical = self._public_issue_fixture()
        unsafe = {**canonical, 'markdown': '## Unsafe evidence\n'+canary+'\n'}
        run.evidence_report.return_value = unsafe
        run.status.return_value['evidence_report'] = unsafe
        self._refuses_before_source_or_network_mutation(record, run, canonical)
        record, run, canonical = self._public_issue_fixture(run_name=canary)
        self.assertNotIn(canary, canonical['markdown'])
        self._refuses_before_source_or_network_mutation(record, run, canonical)

    def test_untracked_raw_and_git_quoted_unicode_names_refuse_before_staging(self):
        for name in ('ghp_CANARY0123456789abcdef.txt', 'api_key=é'):
            with self.subTest(name=name):
                path = self.root/name
                path.write_text('checked source\n')
                record, run, canonical = self._public_issue_fixture()
                # The existing --stat preflight cannot see this untracked name.
                self.assertNotIn(name, self.git('diff', '--stat', record['base_commit']))
                self._refuses_before_source_or_network_mutation(record, run, canonical)
                path.unlink()

    def test_repeat_unsafe_wrapper_refuses_with_intact_historical_pair_and_receipt(self):
        record, run, canonical = self._public_issue_fixture(run_name='ghp_CANARY0123456789abcdef')
        receipt = delivery.capture(self.root, self.view, canonical)
        issue.stage_source(self.root)
        self.git('commit', '-qm', 'delivery')
        receipt = delivery.seal(self.root, receipt)
        historical = {**canonical, 'availability': 'recorded'}
        delivery.verify(self.root, self.view, historical, receipt)
        record['delivery_receipt'] = receipt
        issue.save(self.root, record)
        run.status.return_value['evidence_report'] = historical
        run.evidence_report.return_value = historical
        self._refuses_before_source_or_network_mutation(record, run, canonical)
        run.evidence_report.assert_called_once_with(require_current=False)

    def test_safe_separate_names_are_not_joined_into_a_policy_match(self):
        (self.root/'Bearer').write_text('checked\n')
        (self.root/'abcdefghijklmnop').write_text('checked\n')
        head, tree = self.git('rev-parse', 'HEAD'), self.git('write-tree')
        delivery.require_safe_public_names(self.root, head)
        self.assertEqual(head, self.git('rev-parse', 'HEAD'))
        self.assertEqual(tree, self.git('write-tree'))

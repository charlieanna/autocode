"""Public provider launch and real capture behavior, without model dispatch."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import autocode_provider_launch as launch
import autocode_source_snapshot as source
import autocode_util as util
import autocode_verification_copy as copies
from providers.command import CommandProvider


class ProviderLaunchTests(unittest.TestCase):
    def test_stage_record_preserves_each_provider_launch_contract(self):
        cases = [
            ({'engine': 'codex'}, {}),
            ({'engine': 'qwen'}, {'isolation':
                'Qwen CLI with workspace boundary enforcement; no OS sandbox'}),
            ({'engine': 'opencode', 'configured': True, 'provider': 'offline'},
                {'provider': 'offline', 'isolation':
                 'Config-tool sandbox flag and workspace snapshot checks'}),
            ({'engine': 'opencode', 'output_token_cap': 123},
                {'isolation': 'OpenCode tool permissions and workspace snapshot checks; no OS sandbox',
                 'tool_containment': None, 'output_token_cap': 123}),
            ({'engine': 'opencode', 'tool_containment': {'scratch': '/scratch'},
              'output_token_cap': 123},
                {'isolation': 'Kernel-constrained native shell; other tools disabled',
                 'tool_containment': {'scratch': '/scratch'}, 'output_token_cap': 123}),
        ]
        for worker, expected in cases:
            with self.subTest(engine=worker['engine'], configured=worker.get('configured')):
                self.assertEqual(expected, launch.stage_record(worker))

    def test_qwen_child_environment_drops_another_stage_copy_authority(self):
        options = dict(engine='qwen', adapter=None, role='sol', route_role='sol',
            workspace=Path('/workspace'), run_dir=Path('/run'), session=None,
            model='qwen/qwen-max', effort='high', allow_write=False, planning=False,
            report=Path('/report'), schema=Path('/schema'), prompt_file=Path('/prompt'),
            sandbox='workspace-write', transport_args=[], chatgpt=False, provider=None)
        ambient = {'AUTOCODE_VERIFICATION_COPY': '/old/stage/manifest',
                   'AUTOCODE_VERIFICATION_COPY_SHA256': 'old',
                   'QWEN_WORKSPACE_MARKER': 'retained'}
        for changes in ({}, {'allow_write': True}, {'planning': True},
                        {'enforce_tool_boundary': False}):
            with self.subTest(changes=changes), mock.patch.dict(os.environ, ambient), \
                    mock.patch.object(launch.qwen, 'launch', side_effect=lambda *a, **kw:
                        (['qwen'], dict(os.environ), None)) as native:
                command, environment, _, worker = launch.prepare(**{**options, **changes})
                self.assertEqual(['qwen'], command)
                native.assert_called_once_with('sol', Path('/workspace'), Path('/run'), None,
                    'qwen/qwen-max', 'high', changes.get('allow_write', False),
                    planning=changes.get('planning', False), report=Path('/report'),
                    schema=Path('/schema'), sandbox='workspace-write')
                self.assertNotIn('AUTOCODE_VERIFICATION_COPY', environment)
                self.assertNotIn('AUTOCODE_VERIFICATION_COPY_SHA256', environment)
                self.assertNotIn('verification_copy', worker)
                self.assertEqual('retained', environment['QWEN_WORKSPACE_MARKER'])
                self.assertEqual('/old/stage/manifest', os.environ['AUTOCODE_VERIFICATION_COPY'])

    def test_retired_or_unknown_engine_cannot_fall_back_to_codex(self):
        for engine in ("gocode", "unknown"):
            with self.subTest(engine=engine), self.assertRaisesRegex(
                    RuntimeError, "providers live in .*--provider"):
                launch.prepare(engine=engine, adapter=None, role="sol", route_role="sol",
                    workspace=Path("/workspace"), run_dir=Path("/run"), session=None,
                    model="example/model", effort="high", allow_write=False, planning=True,
                    report=Path("/report"), schema=Path("/schema"), prompt_file=Path("/prompt"),
                    sandbox="read-only", transport_args=[], chatgpt=False, provider=None)


class CodexCaptureCopyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'source.py').write_text('value = 42\n')
        (self.root / '.gitignore').write_text('.autocode/\napproved/\n')
        for args in (('init', '-q'), ('add', '.'), ('-c', 'user.name=T', '-c',
                     'user.email=t@example.test', 'commit', '-qm', 'source')):
            subprocess.run(['/usr/bin/git', *args], cwd=self.root, check=True, capture_output=True)
        self.run = self.root / '.autocode/runs/one'
        self.run.mkdir(parents=True)
        self.options = dict(engine='codex', adapter=None, role='sol', route_role='sol',
            workspace=self.root, run_dir=self.run, session=None,
            model='gpt-5.6-sol', effort='high', allow_write=False, planning=False,
            report=self.root / 'report.json', schema=self.root / 'schema.json',
            prompt_file=self.root / 'prompt.txt', sandbox='workspace-write', transport_args=[],
            chatgpt=True, provider=None)

    def capture(self, environment, name, command):
        entry = ('import sys; sys.path.insert(0, ' + repr(str(Path(launch.__file__).parent))
                 + '); import autocode_capture_command as c; sys.exit(c.cli())')
        return subprocess.run([sys.executable, '-c', entry,
            '--output', str(self.root / '.autocode/evidence' / (name + '.json')),
            '--', *command], cwd=self.root, env=environment,
            capture_output=True, text=True, timeout=30)

    def test_real_capture_preserves_exact_command_and_keeps_generated_files_out_of_source(self):
        (self.root / 'approved').mkdir()
        (self.root / 'approved/value.txt').write_text('selected ignored input')
        before = source.snapshot(self.root, paths=['approved/value.txt'])
        command, environment, _, worker = launch.prepare(**self.options,
            source_paths=['approved/value.txt'])
        self.assertEqual(['codex', 'exec', '-C', str(self.root), '--sandbox', 'workspace-write',
            '-c', 'forced_login_method="chatgpt"', '-c', 'model_reasoning_effort="high"',
            '-', '--json', '--output-schema', str(self.options['schema']), '-o',
            str(self.options['report']), '--model', 'gpt-5.6-sol'], command)
        code = ("from pathlib import Path; from source import value; assert value == 42; "
                "assert Path('approved/value.txt').read_text() == 'selected ignored input'; "
                "Path('other_1.sqlite3').touch(); print('real native check')")
        native = [sys.executable, '-B', '-c', code]
        result = self.capture(environment, 'first', native)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        # Baseline must fail on the real generated original-source file,
        # before this test depends on new metadata.
        self.assertFalse((self.root / 'other_1.sqlite3').exists())
        self.assertEqual(before, source.snapshot(self.root, paths=['approved/value.txt']))
        copy = worker['verification_copy']
        self.assertEqual(self.run, Path(copy['manifest']).parent.parent)
        self.assertEqual(['evidence', 'output', 'runs'], sorted(path.name for path in (self.root / '.autocode').iterdir()))
        self.assertEqual(copy['manifest'], environment['AUTOCODE_VERIFICATION_COPY'])
        self.assertEqual(copy['sha256'], environment['AUTOCODE_VERIFICATION_COPY_SHA256'])
        self.assertNotIn('tool_containment', worker)
        launch.verify_containment(worker)
        record = launch.stage_record(worker)
        self.assertEqual(copy, record['verification_copy'])
        self.assertNotIn('Kernel', record['isolation'])
        receipt = json.loads(result.stdout)
        self.assertEqual(native, receipt['command'])
        self.assertEqual({'source_revision': copy['source_revision'],
                          'manifest_sha256': copy['sha256']}, receipt['verification_copy'])
        self.assertEqual('real native check\n', Path(receipt['full_output']).read_text())
        self.assertTrue((Path(copy['tree']) / 'other_1.sqlite3').is_file())
        # New outputs survive a later capture; retained inputs are still checked.
        next_check = self.capture(environment, 'second', [sys.executable, '-B', '-c',
            "from pathlib import Path; assert Path('other_1.sqlite3').is_file()"])
        self.assertEqual(0, next_check.returncode, next_check.stderr)
        self.assertEqual('value = 42\n', (self.root / 'source.py').read_text())

    def test_each_native_stage_allocates_fresh_authority_even_with_opencode_opt_out(self):
        copies_created = []
        for _ in range(2):
            _, environment, _, worker = launch.prepare(**self.options,
                settings={'allow_uncontained_tools': True})
            copies_created.append(worker['verification_copy']['manifest'])
            self.assertIn('AUTOCODE_VERIFICATION_COPY', environment)
        self.assertNotEqual(*copies_created)

    def test_writer_planning_and_preview_allocate_nothing_and_drop_ambient_authority(self):
        for changes in ({'allow_write': True}, {'planning': True}, {'enforce_tool_boundary': False},
                        {'sandbox':'read-only', 'role':'astra', 'route_role':'investigator'}):
            with self.subTest(changes=changes), mock.patch.dict(os.environ, {
                    'AUTOCODE_VERIFICATION_COPY': '/old/stage/manifest',
                    'AUTOCODE_VERIFICATION_COPY_SHA256': 'old'}):
                _, environment, _, worker = launch.prepare(**{**self.options, **changes})
                self.assertNotIn('AUTOCODE_VERIFICATION_COPY', environment)
                self.assertNotIn('AUTOCODE_VERIFICATION_COPY_SHA256', environment)
                self.assertNotIn('verification_copy', worker)
                self.assertEqual([], list(self.run.iterdir()))
                self.assertEqual(['runs'], sorted(path.name for path in (self.root / '.autocode').iterdir()))

    def test_prelaunch_rejects_changed_manifest_copy_source_and_authority(self):
        for mutation, expected in (
            ('manifest', 'authority changed'), ('copy', 'input changed'),
            ('source', 'source changed'), ('environment', 'launch authority changed')):
            with self.subTest(mutation=mutation):
                _, _, _, worker = launch.prepare(**self.options)
                copy = worker['verification_copy']
                if mutation == 'manifest':
                    Path(copy['manifest']).write_text('{}')
                elif mutation == 'copy':
                    (Path(copy['tree']) / 'source.py').write_text('changed copy\n')
                elif mutation == 'source':
                    (self.root / 'source.py').write_text('concurrent original change\n')
                else:
                    worker['environment']['AUTOCODE_VERIFICATION_COPY'] = '/other/manifest'
                with self.assertRaisesRegex(util.Paused, expected) as caught:
                    launch.verify_containment(worker)
                self.assertEqual('PAUSED_STALE_VALIDATION', caught.exception.status)
                if mutation == 'source':
                    self.assertEqual('concurrent original change\n', (self.root / 'source.py').read_text())
                    (self.root / 'source.py').write_text('value = 42\n')

    def test_capture_rejects_current_source_drift_before_running_command(self):
        _, environment, _, _ = launch.prepare(**self.options)
        (self.root / 'source.py').write_text('concurrent original change\n')
        result = self.capture(environment, 'stale', [sys.executable, '-c',
            "from pathlib import Path; Path('should-not-run').touch()"])
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn('Verification source changed', result.stderr)
        self.assertFalse((self.root / 'should-not-run').exists())
        self.assertFalse((self.root / '.autocode/evidence/stale.log').exists())
        self.assertEqual('concurrent original change\n', (self.root / 'source.py').read_text())

    def test_failed_preparation_removes_only_its_new_control_directory(self):
        prior = self.run / 'tool-containment-prior'
        prior.mkdir(parents=True)
        (prior / 'owned-by-other-stage').write_text('keep')
        other = self.root / '.autocode/runs/other'
        other.mkdir()
        (other / 'owned-by-other-run').write_text('keep too')
        def fail(workspace, scratch, **kwargs):
            (scratch / 'partial').write_text('disposable')
            raise ValueError('copy failed')
        with mock.patch.object(copies, 'create', side_effect=fail):
            with self.assertRaisesRegex(util.Paused, 'copy failed'):
                launch.prepare(**self.options)
        self.assertEqual([prior], list(self.run.iterdir()))
        self.assertEqual('keep', (prior / 'owned-by-other-stage').read_text())
        self.assertEqual('keep too', (other / 'owned-by-other-run').read_text())
        self.assertEqual(['runs'], sorted(path.name for path in (self.root / '.autocode').iterdir()))

    def test_symlinked_storage_is_rejected_without_touching_external_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory)
            self.run.rmdir()
            self.run.parent.rmdir()
            self.run.parent.parent.rmdir()
            self.root.joinpath('.autocode').symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(util.Paused, 'must not be a symlink'):
                launch.prepare(**self.options)
            self.assertEqual([], list(external.iterdir()))

    def test_copy_requires_the_existing_direct_run_directory(self):
        outside, nested = self.root / 'outside-run', self.run / 'nested'
        outside.mkdir()
        nested.mkdir()
        invalid = (self.root / 'outside-run', self.run / 'nested',
                   self.run.parent / 'missing', self.run.parent,
                   Path('.autocode/runs/one'))
        for directory in invalid:
            with self.subTest(directory=directory), self.assertRaisesRegex(
                    util.Paused, 'existing physical task run directory'):
                launch.prepare(**{**self.options, 'run_dir':directory})
        self.assertEqual([], list(outside.iterdir()))
        self.assertEqual([], list(nested.iterdir()))
        nested.rmdir()
        self.assertEqual([], list(self.run.iterdir()))
        self.assertEqual(['runs'], sorted(path.name for path in (self.root / '.autocode').iterdir()))

    def test_symlinked_run_directory_cannot_allocate_external_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory).resolve()
            self.run.rmdir()
            self.run.symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(util.Paused, 'existing physical task run directory'):
                launch.prepare(**self.options)
            self.assertEqual([], list(external.iterdir()))

    def test_run_copy_authority_cannot_move_to_another_run(self):
        _, environment, _, worker = launch.prepare(**self.options)
        original = Path(worker['verification_copy']['manifest'])
        other_run = self.run.parent / 'other'
        other_run.mkdir()
        moved = other_run / original.parent.name
        original.parent.rename(moved)
        manifest = moved / original.name
        with self.assertRaisesRegex(ValueError, 'Unexpected verification copy layout'):
            copies.execution(manifest, environment['AUTOCODE_VERIFICATION_COPY_SHA256'], self.root)

    def test_run_copy_rejects_lookalike_control_names(self):
        _, environment, _, worker = launch.prepare(**self.options)
        original = Path(worker['verification_copy']['manifest'])
        moved = self.run / 'tool-containment-lookalike'
        original.parent.rename(moved)
        with self.assertRaisesRegex(ValueError, 'Verification copy authority changed'):
            copies.execution(moved / original.name, environment['AUTOCODE_VERIFICATION_COPY_SHA256'], self.root)

    def test_existing_kernel_contained_copy_layout_remains_executable(self):
        scratch = self.root / '.autocode' / ('tool-containment-' + 'f' * 32) / 'scratch'
        scratch.mkdir(parents=True)
        legacy = copies.create(self.root, scratch)
        tree, provenance = copies.execution(legacy['manifest'], legacy['sha256'], self.root)
        self.assertEqual(scratch / 'verification', tree)
        self.assertEqual('value = 42\n', (tree / 'source.py').read_text())
        self.assertEqual(legacy['sha256'], provenance['manifest_sha256'])

    def configured(self):
        return CommandProvider({'name':'offline',
            'command':['fixture-provider','{model}','{workspace}','{sandbox}','{report}'],
            'roles':{'sol':{'model':'verifier','effort':'high'}}}, self.root / 'provider.toml')

    def test_configured_provider_real_capture_creates_its_fixture_only_in_the_copy(self):
        before = source.snapshot(self.root)
        ambient = {'AUTOCODE_FIXTURE_MARKER':'preserved'}
        with mock.patch.dict(os.environ, ambient):
            command, environment, _, worker = launch.prepare(**{**self.options,
                'engine':'opencode', 'adapter':self.configured(), 'model':'verifier'})
        self.assertEqual(['fixture-provider','verifier',str(self.root),'workspace-write',
                          str(self.options['report'])], command)
        native = [sys.executable, '-B', '-c',
            "from pathlib import Path; import runpy,os; "
            "assert os.environ['AUTOCODE_FIXTURE_MARKER']=='preserved'; "
            "Path('probe.py').write_text('from source import value; assert value == 42\\n'); "
            "runpy.run_path('probe.py'); Path('other_1.sqlite3').touch(); print('configured native capture')"]
        result = self.capture(environment, 'configured', native)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertFalse((self.root / 'probe.py').exists())
        self.assertFalse((self.root / 'other_1.sqlite3').exists())
        self.assertEqual(before, source.snapshot(self.root))
        copy = worker['verification_copy']
        self.assertEqual(self.run, Path(copy['manifest']).parent.parent)
        self.assertEqual(['evidence', 'output', 'runs'], sorted(path.name for path in (self.root / '.autocode').iterdir()))
        self.assertEqual('offline', worker['provider'])
        self.assertNotIn('tool_containment', worker)
        record = launch.stage_record(worker)
        self.assertEqual('offline', record['provider'])
        self.assertIn('Configured-provider permission checks', record['isolation'])
        self.assertNotIn('Codex', record['isolation'])
        self.assertNotIn('Kernel', record['isolation'])
        receipt = json.loads(result.stdout)
        self.assertEqual(native, receipt['command'])
        self.assertEqual(copy['sha256'], receipt['verification_copy']['manifest_sha256'])
        self.assertEqual('configured native capture\n', Path(receipt['full_output']).read_text())
        self.assertTrue((Path(copy['tree']) / 'probe.py').is_file())
        launch.verify_containment(worker)

    def test_configured_judging_mints_new_authority_instead_of_inheriting_an_old_copy(self):
        ambient = {'AUTOCODE_VERIFICATION_COPY':'/old/verification-copy.json',
                   'AUTOCODE_VERIFICATION_COPY_SHA256':'old'}
        with mock.patch.dict(os.environ, ambient):
            _, environment, _, worker = launch.prepare(**{**self.options,
                'engine':'opencode','adapter':self.configured()})
        copy = worker['verification_copy']
        self.assertNotEqual(ambient['AUTOCODE_VERIFICATION_COPY'], copy['manifest'])
        self.assertEqual(copy['manifest'], environment['AUTOCODE_VERIFICATION_COPY'])
        self.assertEqual(copy['sha256'], environment['AUTOCODE_VERIFICATION_COPY_SHA256'])
        launch.verify_containment(worker)

    def test_real_configured_adapter_writer_planning_preview_and_investigator_keep_no_copy(self):
        for changes in ({'allow_write':True}, {'planning':True}, {'enforce_tool_boundary':False},
                        {'sandbox':'read-only','role':'astra','route_role':'investigator'}):
            with self.subTest(changes=changes), mock.patch.dict(os.environ, {
                    'AUTOCODE_VERIFICATION_COPY':'/old/verification-copy.json',
                    'AUTOCODE_VERIFICATION_COPY_SHA256':'old'}):
                _, environment, _, worker = launch.prepare(**{**self.options,
                    'engine':'opencode', 'adapter':self.configured(), **changes})
                self.assertNotIn('AUTOCODE_VERIFICATION_COPY', environment)
                self.assertNotIn('AUTOCODE_VERIFICATION_COPY_SHA256', environment)
                self.assertNotIn('verification_copy', worker)
                self.assertEqual([], list(self.run.iterdir()))
                self.assertEqual(['runs'], sorted(path.name for path in (self.root / '.autocode').iterdir()))

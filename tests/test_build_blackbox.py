"""Handwritten plans -> public CLI -> actual candidate. No runner state injection.

Provider is deterministic, not a live LLM. Set BUILD_AUDIT_ARTIFACTS to retain
every workspace, prompt, report and process log rather than delete temp fixtures.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import autocode_process as processes
import build_product_fixtures as products
from goal_fixtures import body


def supervised_processes(root):
    """The keeper, provider and owner identities every supervision receipt under root records."""
    rows = {}
    # os.walk skips a directory that vanishes mid-walk; pathlib's rglob raises on it.
    receipts = [Path(top, name) for top, _, names in os.walk(root) for name in names
                if name.endswith('supervision.json')]
    for receipt in receipts:
        try:
            value = json.loads(receipt.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        for row in (value.get('keeper'), value.get('provider'), value.get('owner'), *(value.get('processes') or ())):
            if isinstance(row, dict) and type(row.get('pid')) is int and row['pid'] != os.getpid():
                rows[row['pid'], row.get('birth_identity'), row.get('started')] = row
    return list(rows.values())


def _alive(row):
    try:
        return bool(processes.live_processes([row]))
    except processes.ProcessError:
        return False  # the PID now names a process this user cannot inspect, so not one of ours


def await_supervised_exit(root, bound=30):
    """Let the supervised processes a fixture started exit before its temporary root is removed.

    A stage's keeper runs in its own session, so it outlives a controller a test
    kills on purpose. Once the provider is gone the keeper rewrites the stage's
    receipt, and that write recreates the receipt's directory: removing the root
    while it is still alive fails with "Directory not empty", or the write lands
    afterwards and silently rebuilds the tree. Waiting on the birth identities the
    receipts record is bounded; a survivor is a real leak, so it is killed (it
    must not write into a later fixture) and reported.
    """
    rows = supervised_processes(root)
    deadline = time.monotonic() + bound
    while True:
        live = [row for row in rows if _alive(row)]
        if not live:
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(.05)
    for row in live:
        try:
            os.kill(row['pid'], signal.SIGKILL)
        except ProcessLookupError:
            pass
    raise AssertionError(f'Supervised processes outlived the test by {bound} s: {sorted(row["pid"] for row in live)}')


def plan(rows, payloads, checks, title):
    contract = body()
    contract.update(intended_outcome=title, required_behaviors=[title],
        end_to_end_flow=['Execute the application entry point', 'Observe the specified product behavior'],
        important_failure_cases=['Missing prerequisite blocks dependent execution'],
        accepted_assumptions=[], deliverables=[p for files in payloads.values() for p in files],
        technical_approach=['Python standard library'], scope_exclusions=['External accounts', 'Deployment'],
        acceptance_criteria=[dict(id=f'C{i}', criterion=r[1], verification_method=checks[f'M{i}'], human_review=False)
                             for i, r in enumerate(rows, 1)],
        milestones=[dict(id=f'M{i}', objective=r[1], depends_on=r[0], affected_paths=r[2], acceptance_criteria=[f'C{i}'])
                    for i, r in enumerate(rows, 1)])
    return dict(contract=contract, payloads=payloads, checks=checks, observe=list(contract['deliverables']))


def independent():
    files = {'M1': {'server/health.py': "def health(): return {'status': 'ok'}\n"},
             'M2': {'web/about/index.html': '<h1>About this app</h1>\n'},
             'M3': {'cli/version.py': "print('1.0.0')\n"}}
    return plan([([], 'Health endpoint function returns ok', ['server/']),
                 ([], 'About page contains visible heading', ['web/about/']),
                 ([], 'CLI version prints 1.0.0', ['cli/'])], files,
        {'M1': "from server.health import health; assert health() == {'status': 'ok'}",
         'M2': "from pathlib import Path; assert '<h1>About this app</h1>' in Path('web/about/index.html').read_text()",
         'M3': "import subprocess,sys; assert subprocess.check_output([sys.executable,'cli/version.py'], text=True).strip() == '1.0.0'"},
        'Build three independent application components')


def notes():
    storage = "import json\nfrom pathlib import Path\ndef load(p): return json.loads(Path(p).read_text()) if Path(p).exists() else []\ndef save(p, rows): Path(p).write_text(json.dumps(rows))\n"
    add = "from notes.storage import load,save\ndef add(p,text):\n if not text.strip(): raise ValueError('empty note')\n rows=load(p); rows.append(text); save(p,rows)\n"
    listing = "from notes.storage import load\ndef listing(p): return '\\n'.join(load(p))\n"
    cli = "import sys\nfrom notes.add import add\nfrom notes.listing import listing\nif sys.argv[1]=='add': add(sys.argv[2],sys.argv[3])\nelif sys.argv[1]=='list': print(listing(sys.argv[2]))\nelse: raise SystemExit(2)\n"
    prefix = 'import tempfile; from pathlib import Path; '
    return plan([([], 'Persist and reload notes as JSON', ['notes/storage.py']),
                 (['M1'], 'Add a note and reject empty input', ['notes/add.py']),
                 (['M1'], 'List stored notes in insertion order', ['notes/listing.py']),
                 (['M2', 'M3'], 'CLI integrates add and list', ['main.py'])],
        {'M1': {'notes/storage.py': storage}, 'M2': {'notes/add.py': add},
         'M3': {'notes/listing.py': listing}, 'M4': {'main.py': cli}},
        {'M1': prefix + "from notes.storage import load,save; d=tempfile.TemporaryDirectory(); p=Path(d.name)/'n'; save(p,['a']); assert load(p)==['a']",
         'M2': prefix + "from notes.add import add; from notes.storage import load; d=tempfile.TemporaryDirectory(); p=Path(d.name)/'n'; add(p,'a'); assert load(p)==['a']",
         'M3': prefix + "from notes.listing import listing; from notes.storage import save; d=tempfile.TemporaryDirectory(); p=Path(d.name)/'n'; save(p,['a','b']); assert listing(p)=='a\\nb'",
         'M4': prefix + "import subprocess,sys; d=tempfile.TemporaryDirectory(); p=str(Path(d.name)/'n'); subprocess.check_call([sys.executable,'main.py','add',p,'hello']); assert subprocess.check_output([sys.executable,'main.py','list',p],text=True).strip()=='hello'"},
        'Build a persistent notes CLI with independent add and list tasks')


class BuildBlackbox(unittest.TestCase):
    def setUp(self):
        base = os.environ.get('BUILD_AUDIT_ARTIFACTS')
        if base:
            Path(base).mkdir(parents=True, exist_ok=True)
            self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=base)).resolve()
        else:
            temp = tempfile.TemporaryDirectory()
            self.addCleanup(temp.cleanup)
            self.root = Path(temp.name).resolve()
        # Cleanups run last-in first-out, so this wait finishes before the root is removed.
        self.addCleanup(await_supervised_exit, self.root)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.source = Path(__file__).resolve().parents[1] / "tools"
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        subprocess.run(['git', '-C', str(self.project), '-c', 'user.name=Fixture', '-c', 'user.email=f@example.test',
                        'commit', '--allow-empty', '-qm', 'baseline'], check=True)
        bindir = self.root / 'bin'
        bindir.mkdir()
        shutil.copy2(self.source / 'blackbox_build_provider.py', bindir / 'codex')
        (bindir / 'codex').chmod(0o755)
        # Hermetic provider/model resolution: the child autocode.py process
        # must never read a contributor's own ~/.config/autocode or ~/.codex.
        config_home = self.root / 'xdg-config'
        config_home.mkdir()
        codex_home = self.root / 'codex-home'
        codex_home.mkdir()
        self.env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'],
            AUTOCODE_HOME=str(self.root / 'registry'), PYTHONDONTWRITEBYTECODE='1',
            BUILD_AUDIT_SPEC=str(self.root / 'plan.json'), BUILD_AUDIT_LOG=str(self.root / 'events.jsonl'),
            XDG_CONFIG_HOME=str(config_home), CODEX_HOME=str(codex_home))
        self.env.pop('AUTOCODE_PROVIDER', None)
        self.counter = 0

    def command(self, unit, args):
        return [sys.executable, str(self.source / (unit + '.py')), '--workspace', str(self.project), *args]

    def invoke(self, unit, args, code=0):
        if '--run-dir' in args and any(flag in args for flag in ('--answer', '--delegate')) and '--resolver-token' not in args:
            try:
                import autocode_resolver_human as human
            except ImportError:
                import autocode_resolver_human as human
            current = human.current(json.loads((Path(args[args.index('--run-dir') + 1]) / 'state.json').read_text()))
            if current:
                args = [*args, '--resolver-token', current['request_token']]
        result = subprocess.run(self.command(unit, args), cwd=self.root, env=self.env, capture_output=True, text=True,
                                timeout=600 if self.env.get('BUILD_AUDIT_LIVE_CODEX') else 90)
        self.counter += 1
        (self.root / f'cli-{self.counter}.json').write_text(json.dumps(dict(command=self.command(unit,args),
            returncode=result.returncode, stdout=result.stdout, stderr=result.stderr), indent=2))
        self.assertEqual(code, result.returncode, result.stdout + result.stderr)
        return result

    def state(self):
        return json.loads((self.run / 'state.json').read_text())

    def progress(self):
        status = self.invoke('autocode', ['--run-dir', str(self.run), '--status'])
        return json.loads(status.stdout)['view']['progress']

    def events(self, kind='start', stage='terra'):
        return [e for e in map(json.loads, (self.root/'events.jsonl').read_text().splitlines())
                if e['event'] == kind and e['stage'] == stage]

    def seed(self, spec=None, checker='gpt-5.6-sol', max_parallel=3, planner_extra=()):
        self.spec = spec or independent()
        (self.root/'plan.json').write_text(json.dumps(self.spec, indent=2))
        self.invoke('autoplanner', [self.spec['contract']['intended_outcome'], '--engine','codex','--in-place',
            '--terra-model','gpt-6-luna','--terra-reasoning-effort','medium',
            '--sol-model',checker,'--sol-reasoning-effort','high',
            '--completion-model',checker,'--completion-reasoning-effort','medium',
            '--max-parallel-builders',str(max_parallel),'--no-chat', *planner_extra], 2)
        self.run = next((self.project/'.autocode/runs').iterdir())
        token = self.state()['displayed_goal']
        self.invoke('autoplanner', ['--run-dir',str(self.run),'--approve-goal',token,'--no-chat'])
        self.assertEqual(self.spec['contract']['milestones'], self.state()['goal_contract']['body']['milestones'])
        self.assertEqual([], self.events())
        self.original_contract = self.state()['goal_contract']

    def build(self, code=0, extra=()):
        self.invoke('autocode_build', ['--run-dir',str(self.run),'--no-chat',*extra], code)
        self.assertNotEqual('TASK_COMPLETE', self.state()['status'])
        self.assertEqual(self.original_contract, self.state()['goal_contract'])

    def candidate(self):
        state = self.state()
        self.assertEqual('sol',state['next_stage'])
        candidate = json.loads(Path(state['unit_handoffs']['autocode']['path']).read_text())
        self.assertEqual('build-candidate', candidate['kind'])
        self.assertEqual(state['implementation']['source_revision'], candidate['source_revision'])
        return candidate

    def test_02_three_workers_parallel_isolated_candidate_34(self):
        # Each fake Builder waits until all three have started, so the overlap check below does not
        # depend on how busy the machine is; if the Builders ran one at a time the run would stall and fail.
        self.env['BUILD_AUDIT_OVERLAP'] = '3'
        self.seed(); self.build(); candidate = self.candidate()
        starts, ends = self.events(), self.events('finish')
        self.assertEqual({'M1','M2','M3'}, {e['milestone'] for e in starts})
        self.assertEqual(3, len({e['workspace'] for e in starts}))
        self.assertLess(max(e['time'] for e in starts), min(e['time'] for e in ends))
        self.assertTrue(all(not e['inputs'] for e in starts))
        self.assertEqual(3,len(candidate['implementation']['builder_reports']))
        for cmd in self.spec['checks'].values():
            self.assertEqual(0,subprocess.run([sys.executable,'-c',cmd],cwd=self.project).returncode)

    def test_01_notes_diamond_dependency_and_shared_inputs_03_04_27(self):
        self.seed(notes()); self.build(); self.candidate()
        self.assertEqual(['M1'],[e['milestone'] for e in self.events()])
        self.invoke('autoreview',['--run-dir',str(self.run),'--no-chat'])
        # The status view counts the accepted task and its check, and names the next job (#29).
        progress = self.progress()
        self.assertEqual(['done', 'waiting', 'waiting', 'waiting'], [t['state'] for t in progress['tasks']['items']])
        self.assertEqual(['checked', 'unchecked', 'unchecked', 'unchecked'],
                         [r['state'] for r in progress['requirements']['items']])
        self.assertEqual('Next: Orchestrator', progress['headline'])
        self.build(); self.candidate()
        # Integrating the parallel Builders set M1's check aside: its pass is now from an earlier check.
        progress = self.progress()
        self.assertEqual('checked_earlier', progress['requirements']['items'][0]['state'])
        self.assertEqual('1 of 4 requirements checked (1 from earlier checks)', progress['requirements']['label'])
        self.assertEqual(1, progress['tasks']['done'])
        self.assertEqual({'M1','M2','M3'},{e['milestone'] for e in self.events()})
        for event in self.events()[1:]:
            self.assertIn('notes/storage.py',event['inputs'])
        self.invoke('autoreview',['--run-dir',str(self.run),'--no-chat'])
        self.build(); self.candidate()
        last = self.events()[-1]
        self.assertEqual('M4',last['milestone'])
        self.assertTrue({'notes/add.py','notes/listing.py'} <= set(last['inputs']))
        self.assertEqual(0,subprocess.run([sys.executable,'-c',self.spec['checks']['M4']],cwd=self.project).returncode)
        self.assertEqual([],self.events(stage='astra_resolve'))

    def test_05_overlap_falls_back_to_serial(self):
        spec = independent()
        for row in spec['contract']['milestones']:
            row['affected_paths'] = ['server/']
        self.seed(spec); self.build(); self.candidate()
        self.assertEqual(['M1'],[e['milestone'] for e in self.events()])

    def test_completed_paths_do_not_serialize_next_diamond_wave(self):
        spec = notes()
        spec.update(review_completed_paths=True, complete_product=True)
        self.seed(spec, max_parallel=2)
        self.build(); self.candidate()
        storage = (self.project / 'notes/storage.py').read_bytes()
        self.assertEqual(['M1'], [e['milestone'] for e in self.events()])
        self.invoke('autoreview', ['--run-dir', str(self.run), '--no-chat'])
        handoff = self.events('decision', stage='astra_review')[-1]
        self.assertEqual('M2', handoff['next_task']['milestone_id'])
        self.assertEqual(['notes/storage.py'], handoff['affected_paths'])

        # M1 has already started; require both new Builder processes before either finishes.
        self.env['BUILD_AUDIT_OVERLAP'] = '3'
        self.build(); candidate = self.candidate()
        self.env.pop('BUILD_AUDIT_OVERLAP')
        starts, ends = self.events()[1:], self.events('finish')[1:]
        self.assertEqual({'M2', 'M3'}, {e['milestone'] for e in starts})
        self.assertEqual(2, len(starts))
        self.assertEqual(2, len({e['pid'] for e in starts}))
        self.assertEqual(2, len({e['workspace'] for e in starts}))
        self.assertTrue(all(e['workspace'] != str(self.project) for e in starts))
        self.assertLess(max(e['time'] for e in starts), min(e['time'] for e in ends))
        self.assertEqual(2, len(candidate['implementation']['builder_reports']))
        for event in starts:
            owned = spec['contract']['milestones'][int(event['milestone'][1:]) - 1]['affected_paths']
            self.assertEqual(owned, event['task_paths'])
            self.assertEqual(owned, event['affected_paths'])
            self.assertEqual(storage.decode(), event['inputs']['notes/storage.py'])
            self.assertFalse({'notes/add.py', 'notes/listing.py'} & set(event['inputs']))
        self.assertEqual(storage, (self.project / 'notes/storage.py').read_bytes())

        self.invoke('autoreview', ['--run-dir', str(self.run), '--no-chat'])
        self.build(); self.candidate()
        last = self.events()[-1]
        self.assertEqual('M4', last['milestone'])
        self.assertEqual(['main.py'], last['task_paths'])
        self.assertEqual(['main.py'], last['affected_paths'])
        self.assertTrue({'notes/add.py', 'notes/listing.py'} <= set(last['inputs']))
        self.invoke('autoreview', ['--run-dir', str(self.run), '--no-chat'])
        # The completion owner requests one final read-only validation of all criteria.
        self.invoke('autoreview', ['--run-dir', str(self.run), '--no-chat'])
        status = json.loads(self.invoke('autocode', ['--run-dir', str(self.run), '--status']).stdout)
        self.assertEqual('TASK_COMPLETE', status['status'])
        self.assertTrue(status['completion_current'])
        self.assertEqual(4, len(self.events()))
        self.assertEqual([], self.events(stage='astra_resolve'))
        for files in spec['payloads'].values():
            for name, content in files.items():
                self.assertEqual(content, (self.project / name).read_text())
        for check in spec['checks'].values():
            checked = subprocess.run([sys.executable, '-c', check], cwd=self.project,
                                     capture_output=True, text=True)
            self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)

    def test_ignored_document_completes_and_edit_invalidates_completion(self):
        ignored = self.project / '.gitignore'
        ignored.write_text('docs/\n')
        for args in [('add', '.gitignore'), ('-c', 'user.name=Fixture', '-c',
                     'user.email=f@example.test', 'commit', '-qm', 'Ignore documentation output')]:
            subprocess.run(['git', *args], cwd=self.project, check=True, capture_output=True)
        content = '# Deployment checklist\n- Check the service.\n'
        spec = plan([([], 'Write the deployment checklist', ['docs/checklist.md'])],
                    {'M1': {'docs/checklist.md': content}},
                    {'M1': "from pathlib import Path; assert Path('docs/checklist.md').read_text() == " + repr(content)},
                    'Deliver the specified deployment checklist')
        spec['complete_product'] = True
        self.seed(spec, max_parallel=1)
        index = (self.project / '.git/index').read_bytes()
        self.build()
        identity = ['--run-dir', str(self.run)]
        for _ in range(2):
            self.invoke('autoreview', [*identity, '--no-chat'])
            status = json.loads(self.invoke('autocode', [*identity, '--status']).stdout)
            if status['status'] == 'TASK_COMPLETE':
                break
        self.assertEqual('TASK_COMPLETE', status['status'])
        self.assertTrue(status['completion_current'])
        self.assertEqual(1, len(self.events()), 'An ignored deliverable must not trigger Builder retries')
        document = self.project / 'docs/checklist.md'
        self.assertEqual(content, document.read_text())
        self.assertEqual('docs/\n', ignored.read_text())
        self.assertEqual(index, (self.project / '.git/index').read_bytes())
        self.assertEqual(0, subprocess.run(['git', 'check-ignore', '-q', 'docs/checklist.md'],
                                         cwd=self.project).returncode)
        document.write_text('# Changed after completion\n')
        stale = json.loads(self.invoke('autocode', [*identity, '--status', '--inspect-evidence']).stdout)
        self.assertFalse(stale['completion_current'])
        self.assertEqual('stale_or_unverified', stale['view']['verification']['freshness'])

    def test_06_hidden_ownership_escape_rejected(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='escape'; self.build(2)
        self.assertFalse((self.project/'unauthorized.txt').exists())
        self.assertNotIn('autocode',self.state()['unit_handoffs'])

    def test_final_validator_gets_own_repairs_on_unchanged_source(self):
        spec = notes()
        spec.update(complete_product=True, validator_repair_incidents=True)
        self.seed(spec, max_parallel=2)
        for _ in range(3):
            self.build()
            self.invoke('autoreview', ['--run-dir', str(self.run), '--no-chat'])
        status = json.loads(self.invoke('autocode', ['--run-dir', str(self.run), '--status']).stdout)
        self.assertEqual('TASK_COMPLETE', status['status'])
        self.assertTrue(status['completion_current'])
        after = self.state()
        originals = [row for row in after['stages'] if row['stage'] == 'sol' and row.get('rejected')]
        self.assertEqual(2, len(originals))
        self.assertEqual(originals[0]['source_revision'], originals[1]['source_revision'])
        self.assertNotEqual(originals[0]['task_id'], originals[1]['task_id'])
        self.assertEqual(originals[0]['failure_key'], originals[1]['failure_key'])
        self.assertEqual([2, 1], [row['attempts'] for row in after['report_repair_history']])
        self.assertEqual(['accepted', 'accepted'], [row['result'] for row in after['report_repair_history']])
        self.assertEqual([1, 2], sorted(after['resolver']['attempts'].values()))
        self.assertEqual('validate', after['current_task']['kind'])
        self.assertEqual(3, len(self.events(stage='sol_report_repair')))
        self.assertEqual(4, len(self.events()))
        for files in spec['payloads'].values():
            for name, content in files.items():
                self.assertEqual(content, (self.project / name).read_text())

    def test_07_failed_worker_retries_without_successful_siblings(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='crash'; self.build(2)
        self.env.pop('BUILD_AUDIT_FAULT')
        self.build(extra=['--resume-paused','--retry-builder','M1']); self.candidate()
        mids=[e['milestone'] for e in self.events()]
        self.assertEqual(2,mids.count('M1')); self.assertEqual(1,mids.count('M2')); self.assertEqual(1,mids.count('M3'))

    def test_13_user_edit_during_build_preserved(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='hold'
        process=subprocess.Popen(self.command('autocode_build',['--run-dir',str(self.run),'--no-chat']),cwd=self.root,
            env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+30
            while not self.events() and process.poll() is None and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue(self.events(),'Builder did not start')
            (self.project/'user.txt').write_text('User edit must survive')
            (self.root/'release').touch()
            stdout,stderr=process.communicate(timeout=40)
            (self.root/'concurrent-cli.log').write_text(stdout+stderr)
            self.assertEqual(2,process.returncode,stdout+stderr)
            self.assertEqual('User edit must survive',(self.project/'user.txt').read_text())
            self.assertFalse((self.project/'server/health.py').exists())
        finally:
            if process.poll() is None:
                process.terminate(); process.communicate(timeout=10)

    def test_15_no_change_is_not_successful_implementation(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='no_change'; self.build(2)
        state=self.state()
        self.assertNotEqual('TASK_COMPLETE',state['status'])
        self.assertNotIn('autocode',state.get('unit_handoffs',{}))
        worker=next(w for w in state['orchestration_batch']['workers'] if w['milestone_id']=='M1')
        child=json.loads((Path(worker['run_dir'])/'state.json').read_text())
        self.assertGreaterEqual(child['no_progress_batches'],1)
        self.assertNotEqual('BUILT',child['status'])
        self.assertFalse((self.project/'server/health.py').exists())

    def test_09_completed_workers_survive_controller_crash_without_replay(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='hold'
        process=subprocess.Popen(self.command('autocode_build',['--run-dir',str(self.run),'--no-chat']),cwd=self.root,
            env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+30
            while len(self.events())<3 and process.poll() is None and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertEqual(3,len(self.events()))
            # Stop only this test-owned controller; its independently launched workers finish.
            process.send_signal(signal.SIGSTOP)
            (self.root/'release').touch()
            deadline=time.monotonic()+30
            while len(list(self.project.glob('.autocode/builders/*/*/.autocode/runs/*/result.json')))<3 and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertEqual(3,len(list(self.project.glob('.autocode/builders/*/*/.autocode/runs/*/result.json'))))
            process.kill()
            stdout,stderr=process.communicate(timeout=10)
            (self.root/'crashed-controller.log').write_text(stdout+stderr)
        finally:
            if process.poll() is None:
                process.kill(); process.communicate(timeout=10)
        self.env.pop('BUILD_AUDIT_FAULT')
        self.build(extra=['--resume-paused']); self.candidate()
        self.assertEqual(3,len(self.events()))

    def test_17_changed_contract_identity_rejected(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='contract_change'; self.build(2)
        self.assertNotIn('autocode',self.state()['unit_handoffs'])

    def test_16_missing_checks_not_fabricated(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='no_evidence'; self.build(2)
        self.assertFalse(self.events(stage='sol'))
        self.assertNotEqual('PASS',self.state().get('validation',{}).get('verdict'))
        self.assertNotIn('autocode',self.state()['unit_handoffs'])
        self.assertTrue(any('evidence' in p.read_text().lower() for p in self.project.glob('.autocode/builders/*/*/.autocode/runs/*/state.json')))

    def test_18_infeasible_plan_pauses(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='infeasible'; self.build(2)
        self.assertFalse((self.project/'server/health.py').exists())
        self.assertNotIn('autocode',self.state()['unit_handoffs'])

    def test_19_permission_not_self_granted(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='permission'; self.build(2)
        self.assertFalse((self.project/'server/health.py').exists())
        self.assertNotIn('autocode',self.state()['unit_handoffs'])

    def test_28_report_repair_does_not_reimplement(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='malformed'; self.build(); self.candidate()
        self.assertEqual(3,len(self.events()))
        self.assertEqual(1,len(self.events('repair',stage='terra_report_repair')))

    def test_29_report_repair_cannot_invent_passing_checks(self):
        # #431 restores original execution history instead of rejecting a usable
        # format repair. The candidate still needs an independent Tester: a repair
        # may fix summary shape, but cannot turn the original failed check into PASS.
        self.spec = independent()
        (self.root / 'plan.json').write_text(json.dumps(self.spec, indent=2))
        # Record the actual malicious repair draft in this test-owned fixture log.
        # Install it before any CLI launch so provider identity stays unchanged.
        # This changes logging only; the checked-in provider and its behavior stay
        # unchanged, and the unique anchor refuses a silently different fixture.
        provider = self.root / 'bin/codex'
        provider_source = provider.read_text()
        anchor = "        record('repair')\n"
        self.assertEqual(1, provider_source.count(anchor))
        provider.write_text(provider_source.replace(anchor,
            "        record('repair', results=result.get('results'), commands_run=result.get('commands_run'))\n", 1))
        self.invoke('autoplanner', [self.spec['contract']['intended_outcome'],
            '--engine', 'codex', '--in-place', '--terra-model', 'gpt-6-luna',
            '--terra-reasoning-effort', 'medium', '--sol-model', 'gpt-5.6-sol',
            '--sol-reasoning-effort', 'high', '--completion-model', 'gpt-5.6-sol',
            '--completion-reasoning-effort', 'medium', '--max-parallel-builders', '3', '--no-chat'], 2)
        self.run = next((self.project / '.autocode/runs').iterdir())
        identity = ['--run-dir', str(self.run)]
        status = json.loads(self.invoke('autocode', [*identity, '--status', '--inspect-evidence']).stdout)
        approved_token = status['contract_token']
        self.invoke('autoplanner', [*identity, '--approve-goal', approved_token, '--no-chat'])

        self.env['BUILD_AUDIT_FAULT'] = 'repair_lies'
        self.invoke('autocode_build', [*identity, '--no-chat'])
        status = json.loads(self.invoke('autocode', [*identity, '--status', '--inspect-evidence']).stdout)
        self.assertEqual(approved_token, status['contract_token'])
        self.assertEqual('sol', status['next_stage'])
        self.assertNotEqual('TASK_COMPLETE', status['status'])
        self.assertFalse(status['view']['done'])
        inspected = status['view']['verification']
        self.assertEqual('not_recorded', inspected['freshness'])
        self.assertIsNone(inspected['report_token'])
        self.assertEqual({'C1', 'C2', 'C3'}, {row['id'] for row in inspected['coverage']})
        self.assertTrue(all(row['state'] == 'unchecked' for row in inspected['coverage']))
        self.assertNotIn('autoreview', status['unit_handoffs'])
        self.assertFalse(self.events(stage='sol'))
        self.assertEqual(3, len(self.events()))
        repairs = self.events('repair', stage='terra_report_repair')
        self.assertEqual(1, len(repairs))
        self.assertEqual(['All tests passed; exit code 0'], repairs[0]['results'])
        self.assertEqual(['python3 -c "raise SystemExit(0)"'], repairs[0]['commands_run'])

        candidate_path = Path(status['unit_handoffs']['autocode']['path'])
        self.assertTrue(candidate_path.resolve().is_relative_to(self.project))
        candidate = json.loads(candidate_path.read_text())
        self.assertIsInstance(candidate, dict)
        self.assertEqual('build-candidate', candidate['kind'])
        self.assertEqual(status['current_task']['id'], candidate['task_id'])
        reports = []
        for name in candidate['implementation']['builder_reports']:
            result_path = Path(name)
            self.assertTrue(result_path.resolve().is_relative_to(self.project))
            result = json.loads(result_path.read_text())
            self.assertIsInstance(result, dict)
            self.assertEqual('BUILT', result['status'])
            report_path = Path(result['report'])
            self.assertTrue(report_path.resolve().is_relative_to(self.project))
            report = json.loads(report_path.read_text())
            self.assertIsInstance(report, dict)
            reports.append((report_path, report))
        self.assertEqual(3, len(reports))
        m1 = [(path, value) for path, value in reports
              if value['commands_run'] == [self.spec['checks']['M1']]]
        self.assertEqual(1, len(m1))
        repaired_path, repaired = m1[0]
        self.assertEqual(['check exit=1'], repaired['results'])
        self.assertEqual('Reformatted preserved builder report', repaired['summary'])

        # A rejected first Builder report is retained under its archive stem;
        # the accepted format repair stays in the same owning iteration. Read
        # only that original report, never checkpoints or schemas.
        self.assertEqual('builder-report-repair-01.json', repaired_path.name)
        original_paths = list(repaired_path.parent.glob('archived-builder-01-*/builder-01.json'))
        self.assertEqual(1, len(original_paths))
        original_path = original_paths[0]
        self.assertTrue(original_path.resolve().is_relative_to(self.project))
        original = json.loads(original_path.read_text())
        self.assertIsInstance(original, dict)
        self.assertNotIn('summary', original)
        self.assertEqual(original['task_id'], repaired['task_id'])
        self.assertEqual(['check exit=1'], original['results'])
        self.assertEqual(original['commands_run'], repaired['commands_run'])
        self.assertEqual(original['contract_hash'], repaired['contract_hash'])

        def executed(path):
            return [event['item'] for event in map(json.loads, path.read_text().splitlines())
                    if event.get('type') == 'item.completed'
                    and event.get('item', {}).get('type') == 'command_execution']
        self.assertEqual([(self.spec['checks']['M1'], 1)],
                         [(item['command'], item['exit_code'])
                          for item in executed(original_path.with_suffix('.jsonl'))])
        self.assertEqual([], executed(repaired_path.with_suffix('.jsonl')),
                         'format repair must not execute a replacement passing command')

    def test_30_33_repeated_build_does_not_redispatch_completed_wave(self):
        self.seed(); self.build(); first=self.events(); original=self.candidate()
        self.build(); self.assertEqual(first,self.events()); self.assertEqual(original,self.candidate())

    def finish_product(self, spec):
        self.seed(spec)
        for wave in range(len(spec['contract']['milestones'])):
            self.build(); self.candidate()
            starts={e['milestone'] for e in self.events()}
            if starts==set(spec['payloads']):
                break
            self.invoke('autoreview',['--run-dir',str(self.run),'--no-chat'])
        self.assertEqual(set(spec['payloads']),{e['milestone'] for e in self.events()})
        for cmd in spec['checks'].values():
            checked=subprocess.run([sys.executable,'-c',cmd],cwd=self.project,capture_output=True,text=True)
            self.assertEqual(0,checked.returncode,checked.stdout+checked.stderr)
        self.assertNotEqual('TASK_COMPLETE',self.state()['status'])

    def test_product_b_duplicate_safe_http_api(self):
        self.finish_product(products.duplicate_api(plan))

    def test_product_e_shared_contract_http_client_server(self):
        self.finish_product(products.client_server(plan))

    def test_product_c_dashboard_candidate_requires_browser_check(self):
        self.finish_product(products.dashboard(plan))

    def test_26_individually_valid_incompatible_client_server_fails_combined_check(self):
        spec=products.client_server(plan)
        spec['payloads']['M3']['client.py']=spec['payloads']['M3']['client.py'].replace('base+USER_PATH+uid',"base+'/user?id='+uid")
        self.seed(spec)
        for i in range(3):
            self.build(); self.candidate()
            if i<2:
                self.invoke('autoreview',['--run-dir',str(self.run),'--no-chat'])
        check=subprocess.run([sys.executable,'-c',spec['checks']['M4']],cwd=self.project,capture_output=True,text=True,timeout=10)
        self.assertNotEqual(0,check.returncode)
        self.assertIn('HTTP Error 404',check.stderr)
        (self.root/'independent-integration-failure.txt').write_text(check.stdout+check.stderr)
        self.invoke('autoreview',['--run-dir',str(self.run),'--no-chat'])
        self.assertEqual('FAIL',self.state()['validation']['verdict'])
        self.assertNotEqual('TASK_COMPLETE',self.state()['status'])

    @unittest.skipUnless(shutil.which('go'),'Go toolchain required')
    def test_product_d_go_registry_synthetic_oracle_not_executed_csharp(self):
        self.finish_product(products.registry(plan))


class HttpFixtureTests(unittest.TestCase):
    def test_loopback_checks_avoid_dns_and_proxy_discovery(self):
        for fixture, incompatible in ((products.client_server, False),
                                      (products.client_server, True),
                                      (products.duplicate_api, False)):
            with self.subTest(fixture=fixture.__name__, incompatible=incompatible), \
                    tempfile.TemporaryDirectory() as temp:
                spec = fixture(plan)
                if incompatible:
                    spec['payloads']['M3']['client.py'] = spec['payloads']['M3']['client.py'].replace(
                        'base+USER_PATH+uid', "base+'/user?id='+uid")
                for files in spec['payloads'].values():
                    for name, content in files.items():
                        (Path(temp) / name).write_text(content)
                for mid, check in spec['checks'].items():
                    command = (
                        'import socket, urllib.request\n'
                        'from unittest.mock import patch\n'
                        'with patch.object(socket, "getfqdn", side_effect=AssertionError("reverse DNS")), '
                        'patch.object(urllib.request, "getproxies", side_effect=AssertionError("system proxies")):\n'
                        f'    exec({check!r})\n'
                    )
                    result = subprocess.run([sys.executable, '-c', command], cwd=temp,
                                            capture_output=True, text=True, timeout=10)
                    if incompatible and mid == 'M4':
                        self.assertNotEqual(0, result.returncode)
                        self.assertIn('HTTP Error 404', result.stderr)
                    else:
                        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__=='__main__':
    unittest.main()

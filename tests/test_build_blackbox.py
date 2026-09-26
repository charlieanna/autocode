# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / "tools"
_FAKES = _ROOT / "tests" / "fakes"
for _p in (_ROOT, _TOOLS, _ROOT / "tests", _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
"""Handwritten plans -> public CLI -> actual candidate. No runner state injection.

Provider is deterministic, not a live LLM. Set BUILD_AUDIT_ARTIFACTS to retain
every workspace, prompt, report and process log rather than delete temp fixtures.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from goal_fixtures import body
from . import build_product_fixtures as products


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
        self.project = self.root / 'project'
        self.project.mkdir()
        self.source = _TOOLS
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
        result = subprocess.run(self.command(unit, args), cwd=self.root, env=self.env, capture_output=True, text=True,
                                timeout=600 if self.env.get('BUILD_AUDIT_LIVE_CODEX') else 90)
        self.counter += 1
        (self.root / f'cli-{self.counter}.json').write_text(json.dumps(dict(command=self.command(unit,args),
            returncode=result.returncode, stdout=result.stdout, stderr=result.stderr), indent=2))
        self.assertEqual(code, result.returncode, result.stdout + result.stderr)
        return result

    def state(self):
        return json.loads((self.run / 'state.json').read_text())

    def events(self, kind='start', stage='terra'):
        return [e for e in map(json.loads, (self.root/'events.jsonl').read_text().splitlines())
                if e['event'] == kind and e['stage'] == stage]

    def seed(self, spec=None):
        self.spec = spec or independent()
        (self.root/'plan.json').write_text(json.dumps(self.spec, indent=2))
        self.invoke('autoplanner', [self.spec['contract']['intended_outcome'], '--engine','codex','--in-place',
            '--terra-model','gpt-6-luna','--terra-reasoning-effort','medium',
            '--sol-model','gpt-5.6-sol','--sol-reasoning-effort','high',
            '--completion-model','gpt-5.6-sol','--completion-reasoning-effort','medium',
            '--max-parallel-builders','3','--no-chat'], 2)
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
        self.build(); self.candidate()
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

    def test_06_hidden_ownership_escape_rejected(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='escape'; self.build(2)
        self.assertFalse((self.project/'unauthorized.txt').exists())
        self.assertNotIn('autocode',self.state()['unit_handoffs'])

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
        self.seed(); self.env['BUILD_AUDIT_FAULT']='repair_lies'; self.build(2)
        self.assertNotIn('autocode',self.state()['unit_handoffs'])

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
        check=subprocess.run([sys.executable,'-c',spec['checks']['M4']],cwd=self.project,capture_output=True,text=True)
        self.assertNotEqual(0,check.returncode)
        (self.root/'independent-integration-failure.txt').write_text(check.stdout+check.stderr)
        self.invoke('autoreview',['--run-dir',str(self.run),'--no-chat'])
        self.assertEqual('FAIL',self.state()['validation']['verdict'])
        self.assertNotEqual('TASK_COMPLETE',self.state()['status'])

    @unittest.skipUnless(shutil.which('go'),'Go toolchain required')
    def test_product_d_go_registry_synthetic_oracle_not_executed_csharp(self):
        self.finish_product(products.registry(plan))


if __name__=='__main__':
    unittest.main()

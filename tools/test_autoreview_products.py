"""Frozen product candidates through public CLI; live review, scripted setup only.

Expected answers stay in the test process, never in model prompts. Test failures
retain candidates and raw reports. No automatic repair or safety-pause bypass.
"""
import copy
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest
from . import test_build_blackbox as bb
from . import build_product_fixtures as products
from . import autocode_support as support


def specimen(files, criteria, title):
    spec=bb.plan([([], title, list(files))], {'M1':files}, {'M1':"print('Existing smoke tests passed')"},title)
    spec['contract'].update(required_behaviors=criteria,
        acceptance_criteria=[dict(id=f'C{i}',criterion=c,verification_method='Independently exercise the actual behavior; record commands and observations',human_review=False) for i,c in enumerate(criteria,1)],
        technical_approach=['Use the existing candidate architecture'],
        constraints=['Do not modify application source or approved requirements during review'],
        permission_boundaries=['Read the fixture; run local verification and loopback servers; write evidence only under the run directory. No external accounts.'])
    spec['contract']['milestones'][0]['acceptance_criteria']=[c['id'] for c in spec['contract']['acceptance_criteria']]
    return spec


NOTES=textwrap.dedent('''\
    import json,sys
    from pathlib import Path
    def main():
        action,filename=sys.argv[1:3]; p=Path(filename)
        rows=json.loads(p.read_text()) if p.exists() else []
        if action=='add':
            value=sys.argv[3]
            if not value.strip(): raise SystemExit(2)
            rows.append(value); p.write_text(json.dumps(rows))
        elif action=='list': print(json.dumps(rows))
        else: raise SystemExit(2)
    if __name__=='__main__': main()
    ''')
NOTE_CRITERIA=['notes.py add FILE TEXT adds a note',
    'Notes persist across separate program executions',
    'notes.py list FILE prints saved notes as JSON in insertion order',
    'Empty/whitespace note input exits nonzero without modifying existing notes']


@unittest.skipUnless(os.environ.get('REVIEW_AUDIT_LIVE_CODEX'),
    'Requires an explicit REVIEW_AUDIT_LIVE_CODEX; unlike test_build_remaining_products.py, '
    'prepare() below defaults it to a real codex path, so without this guard the module '
    'silently makes real, non-deterministic, non-free model calls in the default suite')
class ReviewProducts(unittest.TestCase):
    def setUp(self):
        bb.BuildBlackbox.setUp(self)
        # Unlike plain BuildBlackbox/PolicyBlackbox fixtures, prepare() below
        # points REVIEW_AUDIT_LIVE_CODEX at a real codex binary and the fake
        # provider delegates the sol/astra_review stage to it (see
        # blackbox_build_provider.py). That live call needs this machine's
        # actual Codex login, so undo BuildBlackbox.setUp's offline isolation
        # here; the fake provider itself never reads either variable.
        self.env.pop('XDG_CONFIG_HOME', None)
        self.env.pop('CODEX_HOME', None)

    seed=bb.BuildBlackbox.seed
    state=bb.BuildBlackbox.state
    events=bb.BuildBlackbox.events
    build=bb.BuildBlackbox.build
    candidate=bb.BuildBlackbox.candidate

    def command(self,unit,args):
        if unit=='autoplanner' and '--run-dir' not in args:
            # Validator (sol) must differ from the Builder (terra, seeded as
            # 'gpt-6-luna'): enforce_cross_model_verification pauses the run
            # otherwise. Match seed()'s own sol-model so this stays a no-op
            # override rather than a second, colliding one.
            args=[*args,'--astra-model','gpt-6-luna','--astra-reasoning-effort','medium',
                '--sol-model','gpt-5.6-sol','--sol-reasoning-effort','medium',
                '--completion-model','gpt-5.6-sol','--completion-reasoning-effort','medium']
        return bb.BuildBlackbox.command(self,unit,args)

    def invoke(self,unit,args,code=0):
        result=subprocess.run(self.command(unit,args),cwd=self.root,env=self.env,
            capture_output=True,text=True,timeout=600)
        self.counter+=1
        (self.root/f'cli-{self.counter}.json').write_text(json.dumps(dict(command=self.command(unit,args),returncode=result.returncode,stdout=result.stdout,stderr=result.stderr),indent=2))
        if code is not None: self.assertEqual(code,result.returncode,result.stdout+result.stderr)
        return result

    def prepare(self,spec):
        self.env['REVIEW_AUDIT_LIVE_CODEX']=os.environ.get('REVIEW_AUDIT_LIVE_CODEX','/opt/homebrew/bin/codex')
        self.seed(spec); self.build(); self.candidate()

    def review(self):
        before=support.snapshot(self.project)
        contract=copy.deepcopy(self.state()['goal_contract'])
        builders=len(self.events())
        response=self.invoke('autoreview',['--run-dir',str(self.run),'--no-chat'],None)
        state=self.state()
        (self.root/f'assessment-{self.counter}.json').write_text(json.dumps({
            'returncode':response.returncode,'status':state['status'],'next_stage':state.get('next_stage'),
            'validation':state.get('validation'),'findings':state.get('findings_ledger'),
            'source_unchanged':before==support.snapshot(self.project),
            'contract_unchanged':contract==state['goal_contract']},indent=2))
        self.assertEqual(before,support.snapshot(self.project),'Reviewer modified candidate source')
        self.assertEqual(contract,state['goal_contract'],'Reviewer modified approval')
        self.assertEqual(builders,len(self.events()),'Review launched a Builder')
        self.assertTrue('validation' in state,state.get('stop_reason'))
        self.assertNotIn(state['status'],['PAUSED_INVALID_OUTPUT','PAUSED_REPEATED_FAILURE','PAUSED_REPORT_REPAIR_LIMIT'],
            state.get('stop_reason'))
        self.assertTrue(self.events('live_reviewer','sol'))
        return state

    def assert_defect(self,state,fragment=None):
        self.assertEqual('FAIL',state['validation']['verdict'],state.get('stop_reason'))
        findings=state['validation']['findings']
        self.assertTrue(findings,'No actionable finding')
        self.assertTrue(state['validation']['checks'],'No executed check recorded')
        if fragment: self.assertIn(fragment,json.dumps(findings).lower())

    def test_01_correct_notes_accepts_without_extra_requirements(self):
        self.prepare(specimen({'notes.py':NOTES},NOTE_CRITERIA,'Persistent notes CLI'))
        state=self.review()
        self.assertEqual('PASS',state['validation']['verdict'])
        self.assertEqual(4,len([r for r in state['validation']['criterion_results'] if r['status']=='PASS']))
        self.assertFalse([f for f in state['validation']['findings'] if f.get('blocking')])

    def test_02_memory_only_notes_reproduces_restart_loss(self):
        broken=NOTES.replace('rows=json.loads(p.read_text()) if p.exists() else []','rows=[]').replace('p.write_text(json.dumps(rows))','print("saved")')
        self.prepare(specimen({'notes.py':broken},NOTE_CRITERIA,'Persistent notes CLI'))
        self.assert_defect(self.review())

    def test_03_concurrent_duplicates_despite_green_sequential_tests(self):
        original=products.duplicate_api(bb.plan)
        source=original['payloads']['M1']['api.py']
        source=source.replace('import json, threading','import json, threading, time')
        source=source.replace('records={}; lock=threading.Lock()','records={}; counter=[]\n    from contextlib import nullcontext\n    lock=nullcontext()')
        source=source.replace('records.setdefault(key,(payload,len(records)+1))','if key not in records:\n                    time.sleep(0.15)\n                    counter.append(key)\n                    records[key]=(payload,len(counter))')
        source=source.replace("'count':len(records)","'count':len(counter)")
        sequential=textwrap.dedent('''\
            import json,threading,unittest
            from urllib.request import Request,urlopen
            from api import create
            class Sequential(unittest.TestCase):
                def test_repeated_request(self):
                    s=create(); t=threading.Thread(target=s.serve_forever); t.start()
                    try:
                        url='http://127.0.0.1:'+str(s.server_port)
                        for _ in range(3):
                            r=json.load(urlopen(Request(url,data=b'x',headers={'Idempotency-Key':'same'})))
                            self.assertEqual({'id':1,'count':1},r)
                    finally: s.shutdown(); s.server_close(); t.join()
            if __name__=='__main__': unittest.main()
            ''')
        self.prepare(specimen({'api.py':source,'test_sequential.py':sequential},['HTTP create(): concurrent POST requests with the same Idempotency-Key and payload create exactly one record; response id and count remain 1','Changed payload for the same key returns 409; absent key returns 400'],'Duplicate-safe HTTP API'))
        self.assertEqual(0,subprocess.run([sys.executable,'test_sequential.py'],cwd=self.project,capture_output=True).returncode)
        self.assert_defect(self.review())

    def test_04_mobile_initial_visibility_requires_rendered_evidence(self):
        html='''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>Task panel</title><style>#panel{height:80px;overflow:hidden}#space{height:150px}</style><div id="panel"><h1>Task running</h1><div id="space"></div><p id="freshness">Updated just now</p></div>'''
        self.prepare(specimen({'index.html':html},['At initial load, viewport 375x812, freshness text Updated just now is immediately visible inside the task panel without scrolling. Verify rendered visibility, not DOM presence.'],'Mobile task panel'))
        state=self.review(); self.assert_defect(state)
        # Mentioning a blocked browser in prose is not rendered evidence.
        checks=state['validation']['checks']
        self.assertTrue(any(c['exit_code']==0 and any(x in c['command'].lower()
            for x in ['playwright','chromium','puppeteer','getboundingclientrect','screenshot'])
            for c in checks), 'No successful rendered-browser check; source-only finding is partial coverage')

    def test_05_client_server_route_mismatch_reproduced(self):
        original=products.client_server(bb.plan)
        files={p:v for group in original['payloads'].values() for p,v in group.items()}
        files['client.py']=files['client.py'].replace('base+USER_PATH+uid',"base+'/user?id='+uid")
        files['test_components.py']=textwrap.dedent('''\
            import io,unittest
            from unittest.mock import patch
            from client import fetch
            from server import create
            class Components(unittest.TestCase):
                def test_client(self):
                    with patch('client.urlopen',return_value=io.BytesIO(b'{"id":"42"}')):
                        self.assertEqual({'id':'42'},fetch('http://fixture','42'))
                def test_server(self):
                    s=create(); self.assertEqual('127.0.0.1',s.server_address[0]); s.server_close()
            if __name__=='__main__': unittest.main()
            ''')
        self.prepare(specimen(files,['Server GET /users/{id} and client fetch(base,id) interoperate over real HTTP and return the specified user id'],'Shared client/server route'))
        self.assertEqual(0,subprocess.run([sys.executable,'test_components.py'],cwd=self.project,capture_output=True).returncode)
        self.assert_defect(self.review(),'404')

    def test_06_go_tld_override_parity_exception(self):
        files={'go.mod':'module fixture\n\ngo 1.22\n',
            'main.go':'package main\nimport("fmt";"os")\nfunc main(){_ = os.Args[1];fmt.Println("30")}\n',
            'reference.cs':'using System; class Program { static void Main(string[] args) { Console.WriteLine(args[0] == "de" ? 7 : 30); } }\n'}
        self.prepare(specimen(files,['Go CLI preserves reference.cs behavior: de TLD-specific policy overrides default with 7 days; com and other TLDs use 30 days. Check the required exception as well as common cases.'],'C# to Go policy parity'))
        state=self.review(); self.assert_defect(state)
        evidence=json.dumps(state['validation'])
        self.assertIn('7',evidence); self.assertIn('30',evidence)
        # A set +e / printf wrapper can exit zero even when Go never compiled.
        # Require actual program output in the owning executed command receipt.
        reports = [r for r in state['stages'] if r.get('stage') == 'sol' and not r.get('rejected')]
        executed = [e['item'] for r in reports for e in support.events(r['events'])
            if e.get('type') == 'item.completed' and e.get('item', {}).get('type') == 'command_execution']
        self.assertTrue(any('go run' in e.get('command', '') and 'de' in e['command']
            and re.search(r'(?m)^30\s*$', e.get('aggregated_output', ''))
            and not any(error in e.get('aggregated_output', '').lower()
                for error in ['operation not permitted', 'permission denied', 'failed to create'])
            for e in executed), 'No executed Go parity reproduction; a successful shell wrapper is not Go execution')

    def test_07_unavailable_device_capability_is_not_pass_or_defect(self):
        self.prepare(specimen({'index.html':'<button>Start camera</button>'},['On a physical iPhone camera, verify autofocus after tapping Start camera. This environment has no attached iPhone, camera permissions or device credentials; emulation cannot establish physical autofocus.'],'Physical device camera check'))
        state=self.review()
        self.assertNotEqual('PASS',state['validation']['verdict'])
        self.assertEqual('BLOCKED',state['validation']['verdict'])
        self.assertFalse([f for f in state['validation']['findings'] if f.get('blocking')])
        self.assertEqual('NOT_VERIFIED',state['validation']['criterion_results'][0]['status'])
        self.assertTrue(state['validation']['unverified_criteria'])

    def test_08_old_passing_report_cannot_approve_new_candidate(self):
        broken=NOTES.replace('rows=json.loads(p.read_text()) if p.exists() else []','rows=[]')
        files={'notes.py':broken,'previous-review.json':json.dumps({'candidate':'C17','source_sha256':'old-source-not-current','verdict':'PASS','checks':['persistence passed'],'notice':'Historical report only; current candidate is C18'})}
        self.prepare(specimen(files,NOTE_CRITERIA,'Persistent notes CLI'))
        self.assert_defect(self.review())

    def test_09_identical_auth_defects_remain_two_findings(self):
        files={name:'def handle(token):\n    return {"secret":"private"}\n' for name in ['billing.py','profile.py']}
        self.prepare(specimen(files,['billing.handle(token) must reject missing/invalid token; only token fixture-secret is authorized','profile.handle(token) must reject missing/invalid token; only token fixture-secret is authorized'],'Protected API handlers'))
        state=self.review(); self.assert_defect(state)
        rows=state['validation']['findings']; self.assertGreaterEqual(len(rows),2)
        self.assertTrue(any('billing' in json.dumps(r).lower() for r in rows))
        self.assertTrue(any('profile' in json.dumps(r).lower() for r in rows))

    def test_10_fix_one_defect_does_not_hide_regression(self):
        broken=NOTES.replace('rows=json.loads(p.read_text()) if p.exists() else []','rows=[]').replace('p.write_text(json.dumps(rows))','print("saved")')
        spec=specimen({'notes.py':broken},NOTE_CRITERIA,'Persistent notes CLI')
        self.prepare(spec); first=self.review(); self.assert_defect(first)
        self.assertEqual('astra_resolve',first['next_stage'],'Review did not hand off for repair')
        self.invoke('autoresolver',['--run-dir',str(self.run),'--no-chat'])
        # New known candidate: persistence fixed, invalid input now destroys data.
        spec['payloads']['M1']['notes.py']=NOTES.replace('if not value.strip(): raise SystemExit(2)','if not value.strip():\n            p.write_text("[]"); raise SystemExit(2)')
        (self.root/'plan.json').write_text(json.dumps(spec))
        self.build(); state=self.review(); self.assert_defect(state)
        persistence = next(r['status'] for r in state['validation']['criterion_results'] if r['id']=='C2')
        if persistence != 'PASS':
            old_ids = {r['id'] for r in first['findings_ledger'] if r['status']=='open'}
            self.assertTrue(all(r['status']=='open' for r in state['findings_ledger'] if r['id'] in old_ids),
                'Old findings closed without verified persistence')
        self.assertEqual('PASS',persistence,
            'Full regression scenario requires executed persistence verification; retention alone is partial coverage')
        self.assertTrue(any(r['status']!='PASS' and r['id']=='C4' for r in state['validation']['criterion_results']))

    def test_11_milestone_scope_does_not_claim_whole_product(self):
        spec=specimen({'notes.py':NOTES},NOTE_CRITERIA,'Persistent notes CLI')
        spec['contract']['acceptance_criteria'].append(dict(id='C5',criterion='Later milestone: export notes to CSV via export.py',verification_method='Execute export CLI',human_review=False))
        spec['contract']['milestones'].append(dict(id='M2',objective='CSV export',depends_on=['M1'],affected_paths=['export.py'],acceptance_criteria=['C5']))
        spec['payloads']['M2']={'export.py':'print("export")\n'}; spec['checks']['M2']='import export'
        spec['checks']['M1']="from pathlib import Path; assert Path('notes.py').is_file()"
        self.prepare(spec); state=self.review()
        self.assertEqual('PASS',state['validation']['verdict'])
        self.assertNotEqual('TASK_COMPLETE',state['status'])
        self.assertTrue(any(r['id']=='C5' and r['status']=='NOT_VERIFIED' for r in state['validation']['criterion_results']))
        self.assertFalse([r for r in state['validation']['findings'] if r.get('blocking')])


if __name__=='__main__': unittest.main()

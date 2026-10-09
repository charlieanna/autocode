"""Frozen product candidates through public CLI; live review, scripted setup only.

Expected answers stay in the test process, never in model prompts. Test failures
retain candidates and raw reports. No automatic repair or safety-pause bypass.
"""
import base64
import copy
import json
import math
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import autocode_support as support
import autoreview_product_probe as probe
import build_product_fixtures as products

from . import test_build_blackbox as bb


def probe_argv(command):
    argv = shlex.split(command)
    if len(argv) == 3 and Path(argv[0]).name in ('sh', 'bash', 'zsh') and argv[1] in ('-c', '-lc'):
        argv = shlex.split(argv[2])
    return argv


def probe_command_matches(command, expected):
    argv = probe_argv(command)
    if argv == expected:
        return True
    # A reviewer may provision a run-owned TMPDIR/GOCACHE in a shell before
    # invoking the exact canonical probe. Preserve the wrapper as evidence but
    # match the inner argv without accepting an unrelated command.
    if ('TMPDIR=' not in command or 'GOCACHE=' not in command) and len(argv) != len(expected):
        return False
    if len(argv) >= len(expected):
        for start in range(len(argv) - len(expected) + 1):
            if argv[start:start + len(expected)] == expected:
                return True
    return False


def go_evidence(executed, command, project, identity):
    """Keep expected answers in the harness, not the reviewer-invoked probe."""
    for event in executed:
        try:
            argv = probe_argv(event.get('command', ''))
            if not probe_command_matches(event.get('command', ''), command) or event.get('exit_code') != 0:
                continue
            rows = [json.loads(line) for line in event.get('aggregated_output', '').splitlines()]
            if len(rows) != 3:
                continue
            for row, tld in zip(rows, ('de', 'com', 'org')):
                run = row['execution']
                if (row['candidate'] != identity or run['command'] != ['go', 'run', '.', tld]
                        or run.get('scope') != 'probe'
                        or run['cwd'] != str(Path(project).resolve()) or not probe.successful(run)
                        or run['stdout'] != '30\n' or run['stderr']):
                    break
            else:
                return True
        except (ValueError, KeyError, TypeError):
            continue
    return False


def go_finding(validation):
    """Compare the reviewer's labelled diagnosis, never incidental prose tokens."""
    if validation.get('verdict') != 'FAIL':
        return False
    expected = dict(input='de', expected='7', observed='30', reference='reference.cs',
                    candidate='main.go', relation='different')
    for finding in validation.get('findings', []):
        if finding.get('finding') != 'reference-parity-mismatch':
            continue
        try:
            # The existing evidence string carries the comparison; runtime
            # report schemas and the executable probe remain unchanged.
            comparison = json.loads(finding.get('evidence', ''), object_pairs_hook=lambda pairs:
                dict(pairs) if len(dict(pairs)) == len(pairs) else None)
            if (isinstance(comparison, dict) and set(comparison) == set(expected)
                    and all(isinstance(comparison[key], str)
                            and comparison[key] in ((value, value + '\n', value + '\r\n')
                                                    if key in ('expected', 'observed') else (value,))
                            for key, value in expected.items())):
                return True
        except (TypeError, ValueError):
            pass
    return False


def browser_evidence(executed, checks, command, project, identity):
    """Trust provider command events, not report prose or model-emitted MCP JSON.

    Call once per original invocation, including a repair's archived original.
    Only the exact trusted probe may attest rendered observations; PNG validation
    checks integrity/dimensions, not visual content or execution permissions.
    """
    project = Path(project).resolve()
    for event in executed:
        try:
            argv = probe_argv(event.get('command', ''))
            if (argv != command or event.get('type') != 'command_execution'
                    or event.get('status') != 'completed' or event.get('exit_code') != 0
                    or any(event.get(key) for key in ('error', 'timed_out', 'interrupted'))
                    or not any(c.get('exit_code') == 0 and probe_argv(c.get('command', '')) == command
                               and c.get('evidence_ref') == 'event:' + event['id'] for c in checks)):
                continue
            row = json.loads(event['aggregated_output'])
            run = row['execution']
            uri = (project / 'index.html').as_uri()
            if (row['candidate'] != identity or run['command'] != [sys.executable, '-c', probe.BROWSER_PROBE, uri]
                    or run['cwd'] != str(project) or run['scope'] != 'probe'
                    or not probe.successful(run) or run['stderr']):
                continue
            obs = json.loads(run['stdout'])
            if (obs['url'] != uri or obs['viewport'] != dict(width=375, height=812, scrollX=0, scrollY=0)):
                continue
            panel, fresh, space = (obs['elements'][key] for key in ('panel', 'freshness', 'space'))
            if (fresh['text'] != 'Updated just now' or 'Task running' not in panel['text']
                    or panel['style']['overflow'] != 'hidden'
                    or fresh['style']['display'] == 'none' or fresh['style']['visibility'] != 'visible'
                    or fresh['style']['opacity'] != '1'):
                continue
            for element in (panel, fresh, space):
                r = element['rect']
                if (not all(type(r[k]) in (int, float) and math.isfinite(r[k]) for k in
                            ('x', 'y', 'width', 'height', 'top', 'right', 'bottom', 'left'))
                        or r['width'] <= 0 or r['height'] <= 0
                        or r['x'] != r['left'] or r['y'] != r['top']
                        or not math.isclose(r['right'], r['left'] + r['width'])
                        or not math.isclose(r['bottom'], r['top'] + r['height'])):
                    break
            else:
                p, f = panel['rect'], fresh['rect']
                width = max(0, min(f['right'], p['right'], 375) - max(f['left'], p['left'], 0))
                height = max(0, min(f['bottom'], p['bottom'], 812) - max(f['top'], p['top'], 0))
                if (p['height'] != 80 or space['rect']['height'] != 150
                        or not 0 <= p['top'] < p['bottom'] <= 812
                        or width <= 0 or height != 0 or f['top'] < p['bottom']):
                    continue
                # Validate complete non-interlaced RGB/RGBA PNG, not just a magic prefix.
                png = base64.b64decode(obs['screenshot_base64'], validate=True)
                if png[:8] != b'\x89PNG\r\n\x1a\n':
                    continue
                chunks, pos = [], 8
                while pos < len(png):
                    size = int.from_bytes(png[pos:pos + 4], 'big')
                    kind, data = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + size]
                    crc = png[pos + 8 + size:pos + 12 + size]
                    if len(crc) != 4 or zlib.crc32(kind + data) != int.from_bytes(crc, 'big'):
                        break
                    chunks.append((kind, data)); pos += 12 + size
                else:
                    if (not chunks or chunks[0][0] != b'IHDR' or chunks[-1] != (b'IEND', b'')
                            or sum(k == b'IHDR' for k, _ in chunks) != 1
                            or sum(k == b'IEND' for k, _ in chunks) != 1):
                        continue
                    header = chunks[0][1]
                    if (len(header) != 13 or header[:8] != (375).to_bytes(4, 'big') + (812).to_bytes(4, 'big')
                            or header[8] != 8 or header[9] not in (2, 6) or header[10:] != b'\0\0\0'):
                        continue
                    stride = 1 + 375 * (3 if header[9] == 2 else 4)
                    decoder = zlib.decompressobj()
                    pixels = decoder.decompress(b''.join(d for k, d in chunks if k == b'IDAT'), stride * 812 + 1)
                    if (decoder.eof and not decoder.unused_data and len(pixels) == stride * 812
                            and all(pixels[y * stride] <= 4 for y in range(812))):
                        return True
        except (ValueError, KeyError, TypeError, zlib.error):
            continue
    return False


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
        # Live runs retain every case, including timeouts and completed cases.
        artifacts = os.environ.get('BUILD_AUDIT_ARTIFACTS') or str(Path(tempfile.gettempdir()) / 'autoreview-products')
        with patch.dict(os.environ, BUILD_AUDIT_ARTIFACTS=artifacts):
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

    def invoke(self,unit,args,code=0,timeout=600):
        self.counter+=1
        receipt=self.root/f'cli-{self.counter}.json'
        row=probe.capture(self.command(unit,args),cwd=self.root,env=self.env,
            timeout=timeout,receipt=receipt)
        self.assertFalse(row['timed_out'] or row['error'], f'Invocation unavailable/timeout; receipt: {receipt}: {row}')
        result=subprocess.CompletedProcess(row['command'],row['returncode'],row['stdout'],row['stderr'])
        if code is not None: self.assertEqual(code,result.returncode,result.stdout+result.stderr)
        return result

    def require_capability(self,kind,timeout=30,*,canonical=False):
        row=probe.preflight(kind,self.root,self.env,timeout,canonical=canonical)
        if not probe.successful(row):
            self.skipTest(f'{kind} unavailable / NOT_VERIFIED (host-only preflight, not reviewer sandbox); '
                f"receipt: {self.root / ('preflight-'+kind+'.json')}; {row['error'] or row['stderr'] or 'timeout'}")

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
        self.assertTrue(probe.successful(probe.capture([sys.executable,'test_sequential.py'],
            cwd=self.project,env=self.env,timeout=30,receipt=self.root/'sequential.json')))
        self.assert_defect(self.review())

    def test_04_mobile_initial_visibility_requires_rendered_evidence(self):
        self.require_capability('browser',canonical=True)
        html='''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>Task panel</title><style>#panel{height:80px;overflow:hidden}#space{height:150px}</style><div id="panel"><h1>Task running</h1><div id="space"></div><p id="freshness">Updated just now</p></div>'''
        command=[sys.executable,str(Path(probe.__file__).resolve()),'--project',str(self.project),'--browser']
        spec=specimen({'index.html':html},['At initial load, viewport 375x812, freshness text Updated just now is immediately visible inside the task panel without scrolling. Verify rendered visibility, not DOM presence.'],'Mobile task panel')
        spec['contract']['acceptance_criteria'][0]['verification_method']=(
            'Execute the canonical rendered-browser probe and record its command event: `'
            +shlex.join(command)+'`. Independently assess its raw browser observations against the criterion; '
            'the probe records the candidate identity, rendered geometry and screenshot, not a verdict.')
        self.prepare(spec)
        identity=probe.candidate_identity(self.project,files=('index.html',))
        state=self.review(); self.assert_defect(state)
        self.assertEqual(identity,probe.candidate_identity(self.project,files=('index.html',)))
        reports=[r for r in state['stages'] if r.get('stage') in ('sol','sol_report_repair') and not r.get('rejected')]
        self.assertTrue(any(browser_evidence(
            [e['item'] for e in support.events(r.get('applied_original_events') or r['events'])
             if e.get('type') == 'item.completed'],state['validation']['checks'],command,self.project,identity)
            for r in reports[-1:]), 'No successful canonical rendered-browser evidence for this candidate')

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
        self.assertTrue(probe.successful(probe.capture([sys.executable,'test_components.py'],
            cwd=self.project,env=self.env,timeout=30,receipt=self.root/'components.json')))
        self.assert_defect(self.review(),'404')

    def test_06_go_tld_override_parity_exception(self):
        self.require_capability('go')
        files={'go.mod':'module fixture\n\ngo 1.22\n',
            'main.go':'package main\nimport("fmt";"os")\nfunc main(){_ = os.Args[1];fmt.Println("30")}\n',
            'reference.cs':'using System; class Program { static void Main(string[] args) { Console.WriteLine(args[0] == "de" ? 7 : 30); } }\n'}
        command=[sys.executable,str(Path(probe.__file__).resolve()),'--project',str(self.project)]
        spec=specimen(files,['Go CLI preserves reference.cs behavior: de TLD-specific policy overrides default with 7 days; com and other TLDs use 30 days. Check the required exception as well as common cases.'],'C# to Go policy parity')
        spec['contract']['acceptance_criteria'][0]['verification_method']=(
            'Execute the canonical probe, which records argv, raw output, exit and candidate hashes: `'
            +shlex.join(command)+'`. Independently assess the observed behavior against reference.cs; '
            'the probe reports observations, not a verdict. For a reproduced parity defect, '
            'set the existing finding field to "reference-parity-mismatch" and its evidence field '
            'to a JSON object (no code fence) with exactly these string fields: '
            'input, expected, observed, reference, candidate, relation. Use source filenames '
            'for reference/candidate and "different" or "equal" for relation. '
            'Independently derive all comparison values from the reference and execution receipts.')
        self.prepare(spec)
        identity=probe.candidate_identity(self.project)
        state=self.review(); self.assert_defect(state)
        self.assertEqual(identity,probe.candidate_identity(self.project))
        self.assertTrue(go_finding(state['validation']), 'Reviewer did not identify de: expected 7, observed 30')
        reports = [r for r in state['stages'] if r.get('stage') == 'sol' and not r.get('rejected')]
        executed = [e['item'] for r in reports for e in support.events(r['events'])
            if e.get('type') == 'item.completed' and e.get('item', {}).get('type') == 'command_execution']
        self.assertTrue(go_evidence(executed,command,self.project,identity),
            'No successful reviewer-owned canonical Go reproduction; host preflight is not reviewer evidence')

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

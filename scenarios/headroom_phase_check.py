"""Opt-in genuine Headroom startup qualification; no models or vendor traffic.

Inputs are a frozen package/SDK copy and its existing qualified interpreter.
Each call creates fresh phase roots and retains its own source-bound receipts.
The stats-only usage URL adapter points to a declared synthetic loopback server;
compatibility uses the production default URL, refused before transport I/O.
"""
from __future__ import annotations
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import threading

from harness.phase_env import GREEN, PhaseSequence

ROOT_VARS = {'CLAUDE_CONFIG_DIR': 'credential_root', 'HEADROOM_CONFIG_DIR': 'config_root',
             'HEADROOM_WORKSPACE_DIR': 'state_root', 'XDG_CONFIG_HOME': 'config_root',
             'XDG_STATE_HOME': 'state_root', 'XDG_CACHE_HOME': 'cache_root'}

CHILD = r'''
import asyncio, hashlib, json, os, sys, threading, time
from pathlib import Path
sys.path[:0] = [os.environ['PHASE_HARNESS_ROOT'], os.environ['FROZEN_HEADROOM_SOURCE']]
from harness.phase_httpx import guard_default_httpx
from harness.phase_sockets import guard_connections
with guard_connections(), guard_default_httpx():
    import httpx
    import headroom._core as core
    from headroom.proxy.server import create_app, ProxyConfig
    import headroom.subscription.client as subscription
    declared = Path(os.environ['FROZEN_HEADROOM_SOURCE']).resolve()
    loaded = [Path(module.__file__).resolve() for module in
              (sys.modules['headroom'], core, sys.modules[create_app.__module__], subscription)]
    assert all(path.is_relative_to(declared / 'headroom') for path in loaded), 'undeclared application import'
    assert Path(core.__file__).suffix in ('.so', '.pyd'), 'real compiled SDK required'
    loaded_inputs = {str(path.relative_to(declared)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in loaded}
    mode = sys.argv[1]
    credential = Path(os.environ['CLAUDE_CONFIG_DIR']) / '.credentials.json'
    before = credential.exists()
    completed = threading.Event()
    fetches = []
    original_fetch = subscription.SubscriptionClient.fetch
    async def observed_fetch(self, *args, **kwargs):
        try:
            result = await original_fetch(self, *args, **kwargs)
            fetches.append({'snapshot_returned': result is not None})
            return result
        finally:
            completed.set()
    subscription.SubscriptionClient.fetch = observed_fetch
    config = ProxyConfig(optimize=False, memory_enabled=False, stateless=False,
                         offline=True, port=int(os.environ['STATS_PORT']))
    report = {'mode': mode, 'core_marker': core.hello(), 'loaded_inputs': loaded_inputs,
              'interpreter': sys.executable, 'credentials_before': before,
              'credential_path': str(credential), 'fetches': fetches}
    if mode == 'stats':
        credential.parent.mkdir(parents=True, exist_ok=True)
        credential.write_text(json.dumps({'claudeAiOauth': {'accessToken': 'synthetic-stats-only',
                                                       'expiresAt': int((time.time()+3600)*1000)}}))
        subscription._USAGE_URL = os.environ['SYNTHETIC_USAGE_URL']
        import uvicorn
        app = create_app(config)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=config.port,
                                              log_level='warning', loop='asyncio', lifespan='on'))
        thread = threading.Thread(target=server.run, daemon=True); thread.start()
        try:
            deadline = time.monotonic()+15
            while not server.started and thread.is_alive() and time.monotonic()<deadline:
                time.sleep(.01)
            assert server.started, 'genuine Uvicorn did not start'
            assert completed.wait(10), 'production subscription fetch did not complete'
            with httpx.Client(trust_env=False) as client:
                response = client.get(f'http://127.0.0.1:{config.port}/stats')
            assert response.status_code == 200
            assert isinstance(response.json(), dict)
            assert app.state.rust_core_status == 'loaded'
            assert fetches and fetches[0]['snapshot_returned']
            report.update(started=True, polled=True, status=response.status_code,
                          rust_core_status=app.state.rust_core_status)
        finally:
            server.should_exit=True; thread.join(timeout=10)
            report['stopped']=not thread.is_alive()
        assert report['stopped'], 'Uvicorn did not stop'
    else:
        from fastapi.testclient import TestClient
        import pytest
        import xml.etree.ElementTree as ET
        injected = os.environ.get('INJECT_SWALLOWED_STEP', '')
        generated = r"""
import asyncio
import pytest
from __main__ import (config, create_app, TestClient, completed, credential,
                      subscription, report, injected)
if injected == 'collection':
    asyncio.run(subscription.SubscriptionClient().fetch('synthetic-collection-only'))
@pytest.fixture(scope='session', autouse=True)
def observer_lifecycle():
    if injected == 'setup':
        asyncio.run(subscription.SubscriptionClient().fetch('synthetic-setup-only'))
    yield
    if injected == 'teardown':
        asyncio.run(subscription.SubscriptionClient().fetch('synthetic-teardown-only'))
@pytest.mark.parametrize('index', [0, 1])
def test_application_lifespan_stats(index):
    completed.clear()
    app = create_app(config)
    with TestClient(app, client=('127.0.0.1', 12345)) as client:
        if not credential.exists():
            asyncio.run(subscription.SubscriptionClient().fetch())
        assert completed.wait(10), 'production startup fetch did not complete'
        if injected == 'call' and index == 0:
            asyncio.run(subscription.SubscriptionClient().fetch('synthetic-call-only'))
        response = client.get('/stats')
        assert response.status_code == 200
        assert app.state.rust_core_status == 'loaded'
        report.setdefault('rust_core_status', []).append(app.state.rust_core_status)
        report['lifespans'] = report.get('lifespans', 0) + 1
"""
        module = Path('test_phase_compat.py'); module.write_text(generated)
        xml = Path('phase-cases.xml')
        code = pytest.main(['-q', '-p', 'no:cacheprovider', '--confcutdir=' + str(Path.cwd()),
                            '--junitxml=' + str(xml), str(module)])
        cases = list(ET.parse(xml).getroot().iter('testcase'))
        report.update(pytest_exit=int(code), test_cases=[case.attrib['name'] for case in cases],
                      test_module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(),
                      injected_refusal_at=injected or None, stopped=True)
        assert code == 0 and len(cases) == 2
        assert all(not list(case) or all(item.tag in ('system-out', 'system-err') for item in case) for case in cases)
    report['credentials_after']=credential.exists()
    print('PHASE_RESULT '+json.dumps(report))
'''


def source_inventory(source):
    package = Path(source)/'headroom'
    if package.is_symlink() or any(path.is_symlink() for path in package.rglob('*')):
        raise ValueError('frozen application inputs must not follow symlinks')
    files = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted((Path(source)/'headroom').rglob('*'))
             if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}
    if not any(name.endswith(('.so','.pyd')) and '/_core' in name for name in files):
        raise ValueError('a real Headroom Rust SDK must be declared in the frozen source copy')
    return files


def prepared_environment():
    # Acceptance account/config inputs must not inherit provider or developer
    # credentials. The model-provider process and its OAuth HOME are untouched.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('HEADROOM_', 'ANTHROPIC_', 'CLAUDE_CODE_', 'GITHUB_', 'GITLAB_',
                                  'OPENAI_', 'AZURE_OPENAI_', 'OTEL_', 'LANGFUSE_'))
           and key.lower() not in ('http_proxy','https_proxy','all_proxy','no_proxy')}
    env.update(PYTHONDONTWRITEBYTECODE='1', HEADROOM_OFFLINE='1', HEADROOM_BEACON='off',
               HEADROOM_TELEMETRY='off', HEADROOM_REQUIRE_RUST_CORE='true', NO_PROXY='*',
               LITELLM_LOCAL_MODEL_COST_MAP='True', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    return env


def result_of(child):
    lines = [line for line in child.stdout.splitlines() if line.startswith('PHASE_RESULT ')]
    return json.loads(lines[-1].split(' ',1)[1]) if child.returncode == 0 and lines else None


def qualify(source, python, output, *, isolate=True, bind=True, inject=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.is_relative_to(source):
        raise ValueError('proof output must stay outside immutable application inputs')
    before = source_inventory(source)
    helper_files=(Path(__file__),Path(__file__).parent/'harness/phase_env.py',
                  Path(__file__).parent/'harness/phase_httpx.py',Path(__file__).parent/'harness/phase_sockets.py')
    helpers={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in helper_files}
    parent_environment = dict(os.environ)
    env = prepared_environment()
    # An owned synthetic ambient root reproduces the original undeclared input;
    # no developer credential file is read, even in deliberately broken controls.
    env['CLAUDE_CONFIG_DIR'] = str(output / 'shared-application-credentials')
    sequence = PhaseSequence('headroom-stats-compat', output, env=env)
    Path(env['CLAUDE_CONFIG_DIR']).mkdir()
    traffic = []
    class Usage(BaseHTTPRequestHandler):
        def do_GET(self):
            synthetic = self.headers.get('Authorization') == 'Bearer synthetic-stats-only'
            traffic.append({'path':self.path, 'synthetic_authorization':synthetic})
            body = json.dumps({'five_hour':{'utilization':10,'resets_at':'2099-01-01T00:00:00Z'},
                               'seven_day':{'utilization':20,'resets_at':'2099-01-01T00:00:00Z'}}).encode()
            self.send_response(200 if synthetic and self.path=='/usage' else 403)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
        def log_message(self,*args): pass
    usage = ThreadingHTTPServer(('127.0.0.1',0), Usage)
    usage_thread = threading.Thread(target=usage.serve_forever,daemon=True);usage_thread.start()
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0)); stats_port=reservation.getsockname()[1]
    usage_port=usage.server_port
    bindings=ROOT_VARS if bind else {key: value for key,value in ROOT_VARS.items() if key!='CLAUDE_CONFIG_DIR'}
    stats=sequence.phase('stats',root_vars=bindings,
                         allowed_endpoints=(f'127.0.0.1:{stats_port}',f'127.0.0.1:{usage_port}'))
    compat=sequence.phase('compat',root_vars=bindings,share_credential_root_with=None if isolate else stats)
    extra={'FROZEN_HEADROOM_SOURCE':str(source),'STATS_PORT':str(stats_port),
           'SYNTHETIC_USAGE_URL':f'http://127.0.0.1:{usage_port}/usage',
           'INJECT_SWALLOWED_STEP':inject or ''}
    try:
        children=[phase.run([str(python),'-B','-c',CHILD,mode],cwd=output,timeout=90,env=extra)
                  for phase,mode in ((stats,'stats'),(compat,'compat'))]
    finally:
        usage.shutdown();usage_thread.join(timeout=5);usage.server_close()
    results=[result_of(child) for child in children]
    for phase,child in zip(('stats','compat'),children):
        (output/(phase+'-stdout.log')).write_text(child.stdout)
        (output/(phase+'-stderr.log')).write_text(child.stderr)
    record=sequence.finish()
    record.update(source_files=before, source_unchanged=source_inventory(source)==before,
                  parent_environment_unchanged=dict(os.environ)==parent_environment,
                  app_results=results, synthetic_usage_requests=traffic,
                  stats_url_adapter={'phase':'stats','reason':'synthetic-only usage fixture',
                                     'target':extra['SYNTHETIC_USAGE_URL'], 'compatibility':'production default URL unchanged'},
                  helper_hashes=helpers,
                  helpers_unchanged=helpers=={path.name:hashlib.sha256(path.read_bytes()).hexdigest()
                                             for path in helper_files})
    stats_result,compat_result=results
    record['passed']=bool(record['outcome']==GREEN and record['source_unchanged']
                          and record['parent_environment_unchanged'] and record['helpers_unchanged'] and traffic
                          and all(row['synthetic_authorization'] for row in traffic)
                          and stats_result and stats_result.get('started') and stats_result.get('stopped')
                          and all(result and result.get('loaded_inputs')
                                  and all(before.get(name)==sha for name,sha in result['loaded_inputs'].items())
                                  for result in results)
                          and compat_result and compat_result.get('lifespans')==2
                          and not compat_result['credentials_before'] and not compat_result['credentials_after'])
    (output/'qualified-result.json').write_text(json.dumps(record,indent=2)+'\n')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--python',required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--share-credentials',action='store_true')
    parser.add_argument('--omit-application-binding',action='store_true')
    parser.add_argument('--inject-swallowed-step',choices=('setup','collection','call','teardown'))
    parser.add_argument('--policy',type=Path,help='Owned candidate JSON with boolean isolate and bind fields')
    args=parser.parse_args()
    policy={'isolate':not args.share_credentials,'bind':not args.omit_application_binding}
    policy_bytes=args.policy.read_bytes() if args.policy else None
    if args.policy:
        policy=json.loads(policy_bytes)
        if (set(policy)!={'isolate','bind'} or any(type(value) is not bool for value in policy.values())
                or args.share_credentials or args.omit_application_binding):
            parser.error('policy must contain exactly boolean isolate/bind, without conflicting control flags')
    result=qualify(args.source,args.python,args.out,inject=args.inject_swallowed_step,**policy)
    if args.policy:
        result['candidate_policy_sha256']=hashlib.sha256(policy_bytes).hexdigest()
        result['candidate_policy_unchanged']=args.policy.read_bytes()==policy_bytes
        if not result['candidate_policy_unchanged']:
            result.update(passed=False,outcome='ERROR',reason='candidate policy changed during qualification')
        (args.out/'qualified-result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({key:result[key] for key in ('passed','outcome','app_results','source_unchanged',
                                                'parent_environment_unchanged')},indent=2))
    return 0 if result['passed'] else 1

if __name__=='__main__':
    raise SystemExit(main())

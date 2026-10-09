"""Offline audit reproductions. Uses temporary repositories and native metadata only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

SOURCE = Path('/Users/ankurkothari/Documents/workspace/autocode')
sys.path.insert(0, str(SOURCE / 'tools'))
import autocode as runner
import autocode_opencode as oc
import autocode_support as support
from test_autocode import RetrofitTest
from test_opencode import event, terminal


def fixture():
    root = Path(tempfile.mkdtemp(prefix='opencode-audit-probes-')).resolve()
    workspace = root / 'project'
    workspace.mkdir()
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    env = {key: value for key, value in os.environ.items()
           if key in ('PATH', 'HOME', 'TMPDIR', 'USER', 'LOGNAME', 'LANG', 'SHELL')}
    for key, name in [('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'),
                      ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state'),
                      ('OPENCODE_CONFIG_DIR', 'custom'), ('OPENCODE_TEST_MANAGED_CONFIG_DIR', 'managed')]:
        env[key] = str(root / name)
        (root / name).mkdir()
    for key in ('OPENCODE_DISABLE_MODELS_FETCH', 'OPENCODE_DISABLE_AUTOUPDATE',
                'OPENCODE_DISABLE_DEFAULT_PLUGINS', 'OPENCODE_DISABLE_CLAUDE_CODE',
                'OPENCODE_DISABLE_EXTERNAL_SKILLS', 'OPENCODE_PURE'):
        env[key] = '1'
    return root, workspace, env


def native_agent(workspace, env):
    with patch.dict(os.environ, env, clear=True):
        _, selected, _ = oc.launch('sol', workspace, workspace / '.autocode/runs/probe',
                                  None, 'audit/model', None, False)
    result = subprocess.run(['opencode', '--pure', 'debug', 'agent', 'autocode_sol'],
                            cwd=workspace, env=selected, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def inline_permission_probe():
    root, workspace, env = fixture()
    env['OPENCODE_CONFIG_CONTENT'] = json.dumps({'agent': {'autocode_sol': {
        'description': 'Audit fixture', 'permission': {'bash': 'deny', 'webfetch': 'deny'}}}})
    original = subprocess.run(['opencode', '--pure', 'debug', 'agent', 'autocode_sol'],
                              cwd=workspace, env=env, capture_output=True, text=True, timeout=30)
    if original.returncode:
        raise RuntimeError(original.stderr)
    before = json.loads(original.stdout)
    after = native_agent(workspace, env)
    return {'fixture': str(root),
            'before': {tool: before['tools'][tool] for tool in ('bash', 'webfetch')},
            'after': {tool: after['tools'][tool] for tool in ('bash', 'webfetch')}}


def config_drift_probe():
    root, workspace, env = fixture()
    path = root / 'custom/opencode.json'
    result = {'fixture': str(root), 'effective': {}}
    identities = []
    for action in ('deny', 'allow'):
        path.write_text(json.dumps({'$schema': 'https://opencode.ai/config.json',
                                    'permission': {'bash': action}}))
        agent = native_agent(workspace, env)
        with patch.dict(os.environ, env, clear=True):
            identities.append(oc.local_settings(workspace))
        rules = [r for r in agent['permission'] if r['permission'] in ('*', 'bash')]
        result['effective'][action] = {'bash_enabled': agent['tools']['bash'], 'rules': rules}
    result['identity_equal_after_permission_change'] = identities[0] == identities[1]
    return result


def project_model_probe():
    root, workspace, env = fixture()
    (workspace / 'opencode.json').write_text(json.dumps({
        '$schema': 'https://opencode.ai/config.json',
        'provider': {'audit-fixture': {
            'npm': '@ai-sdk/openai-compatible', 'name': 'Offline audit fixture',
            'options': {'baseURL': 'http://127.0.0.1:9/v1', 'apiKey': 'audit-placeholder'},
            'models': {'audit-model': {'name': 'Audit model'}}}}}))
    result = {'fixture': str(root)}
    listing = subprocess.run(['opencode', '--pure', 'models'], cwd=workspace, env=env,
                             capture_output=True, text=True, timeout=30)
    result['target_listing_exit'] = listing.returncode
    result['target_model_present'] = 'audit-fixture/audit-model' in listing.stdout.splitlines()
    old_cwd = Path.cwd()
    try:
        os.chdir(root)
        with patch.dict(os.environ, env, clear=True):
            try:
                oc.check_models({'sol': {'model': 'audit-fixture/audit-model'}})
            except RuntimeError as error:
                result['adapter_check_from_other_cwd'] = str(error)
    finally:
        os.chdir(old_cwd)
    return result


def malformed_report_probe(text):
    test = RetrofitTest()
    test.setUp()
    try:
        base = test.run / 'terra-01'
        before = base.with_suffix('.before.json')
        support.atomic_json(before, support.snapshot(test.root))
        rows = [event('text', text=text), terminal()]
        log = base.with_suffix('.jsonl')
        log.write_text('\n'.join(json.dumps(row) for row in rows))
        test.state['settings']['engine'] = 'opencode'
        test.state['active_stage'] = {
            'engine': 'opencode', 'role': 'terra', 'stage': 'terra', 'iteration': 5,
            'output': str(base.with_suffix('.json')), 'events': str(log),
            'schema': str(runner.SCHEMA_DIR / 'v2/terra-report.schema.json'),
            'before_ref': str(before), 'exit_code': 0}
        result = {'report': text, 'attempts': []}
        for _ in range(2):
            try:
                runner.reconcile_active(test.state, test.run, test.root)
            except (ValueError, RuntimeError) as error:
                result['attempts'].append({'error': str(error),
                    'active_stage_retained': 'active_stage' in test.state,
                    'archived_attempts': len(test.state.get('reconciliation_notes', []))})
        return result
    finally:
        test.doCleanups()


if __name__ == '__main__':
    result = {
        'inline_permission_override': inline_permission_probe(),
        'custom_config_drift': config_drift_probe(),
        'project_model_context': project_model_probe(),
        'malformed_report_recovery': malformed_report_probe('The implementation is finished.'),
        'wrong_schema_recovery': malformed_report_probe('{"summary":"missing required fields"}'),
    }
    output = Path('/private/tmp/autocode-opencode-audit-results.json')
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))

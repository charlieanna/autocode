"""Package entry-point and project-metric regressions; no provider calls."""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard_metrics as metrics
import dashboard_monitor as monitor


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name) / 'project'
        self.run = self.workspace / '.autocode/runs/task'
        self.run.mkdir(parents=True)
        self.artifact = self.workspace / 'counts.json'
        self.artifact.write_text(json.dumps({'groups': {'trees': {'done': ['a'], 'total': ['a', 'b']}}}))
        self.spec = {'id': 'mapped', 'label': 'Mapped records', 'runs': ['task'], 'source': 'counts.json',
                     'groups_pointer': '/groups', 'completed_field': 'done', 'total_field': 'total'}
        self.save_config()
        self.addCleanup(metrics._cache.clear)

    def save_config(self):
        (self.workspace / '.autocode/dashboard.json').write_text(json.dumps({'version': 1, 'metrics': [self.spec]}))

    def view(self):
        return metrics.project_metrics(self.workspace, self.run)[0]

    def test_counts_snapshot_and_explicit_run_scope(self):
        row = self.view()
        self.assertEqual((row['done'], row['total']), (1, 2))
        self.assertTrue(row['updated'])
        self.assertNotIn('a', json.dumps(row['areas']).split())
        self.assertEqual(metrics.project_metrics(self.workspace, self.run.parent / 'other-task'), [])

    def test_cache_invalidates_with_source_and_config(self):
        self.assertEqual(self.view()['done'], 1)
        self.artifact.write_text(json.dumps({'groups': {'trees': {'done': 2, 'total': 3}}}))
        self.assertEqual(self.view()['done'], 2)
        self.spec['completed_field'] = 'missing'; self.save_config()
        self.assertIn('error', self.view())

    def test_symlink_and_traversal_never_read_outside_project(self):
        outside = Path(self.tmp.name) / 'secret.json'
        outside.write_text(self.artifact.read_text())
        (self.workspace / 'escape.json').symlink_to(outside)
        for source in ('../secret.json', 'escape.json', str(outside)):
            self.spec['source'] = source; self.save_config()
            self.assertIn('error', self.view())

    def test_invalid_partial_counts_never_become_zero_or_pass(self):
        for groups in ({}, {'x': {'done': True, 'total': 3}}, {'x': {'done': 4, 'total': 3}}, {'x': {'total': 4}}, {'x': {'done': -1, 'total': 4}}, {'x': None}):
            self.artifact.write_text(json.dumps({'groups': groups}))
            self.assertIn('error', self.view())
            self.assertNotIn('done', self.view())
        self.artifact.write_text('{incomplete')
        self.assertIn('error', self.view())

    def test_size_limit_and_missing_source_are_explicit(self):
        with patch.object(metrics, 'MAX_SOURCE_BYTES', 1):
            self.assertIn('error', self.view())
        self.artifact.unlink()
        self.assertIn('error', self.view())

    def test_missing_or_bad_config_does_not_break_task_view(self):
        config = self.workspace / '.autocode/dashboard.json'
        config.write_text('[]')
        self.assertIn('error', self.view())
        config.unlink()
        self.assertEqual(metrics.project_metrics(self.workspace, self.run), [])


# Use a subprocess with an isolated module search path. The old tests inserted
# tools/dashboard in sys.path, which concealed the installed-entry-point bug.
PACKAGE_CHECK = r'''
import hashlib,http.client,importlib,importlib.util,json,os,pathlib,sys,tempfile,threading
from unittest.mock import patch
tools=pathlib.Path(sys.argv[1]); spec=importlib.util.spec_from_file_location('autocode_cli',tools/'__init__.py',submodule_search_locations=[str(tools)])
package=importlib.util.module_from_spec(spec);sys.modules['autocode_cli']=package;spec.loader.exec_module(package)
module=importlib.import_module('autocode_cli.dashboard.agent_console')
with tempfile.TemporaryDirectory() as temporary:
 root=pathlib.Path(temporary);os.environ['AUTOCODE_HOME']=str(root/'registry')
 ws=root/'workspace';(ws/'.git').mkdir(parents=True);run=ws/'.autocode/runs/test';run.mkdir(parents=True)
 state=run/'state.json';state.write_text(json.dumps({'task':'Preserve existing work','status':'PAUSED_INTERVENTION','settings':{'roles':{'terra':{'model':'glm-5.3','reasoning_effort':'max'}}}}))
 before=state.read_bytes()
 console=module.Console([ws],root/'unused-runner.py',lambda:False,conversation_root=root/'conversations',project_store_root=root/'dashboard')
 console._json_command=lambda *a,**kw:({'registry_version':1,'operation':'location','registry_path':str(root/'registry.json')},None)
 console._intervention_view=lambda *a,**kw:{'mode':'unavailable'}
 server=module.ThreadingHTTPServer(('127.0.0.1',0),module.Handler);server.console=console;server.hosts={'127.0.0.1:'+str(server.server_port)}
 threading.Thread(target=server.serve_forever,daemon=True).start()
 def get():
  from urllib.parse import urlencode
  conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
  conn.request('GET','/api/run?'+urlencode({'workspace':str(ws),'run':str(run)}));response=conn.getresponse();body=json.loads(response.read());conn.close();return response.status,body
 try:
  status,body=get();assert status==200,(status,body);assert body['task']=='Preserve existing work';assert body['monitor']['roles']['terra']['model']=='glm-5.3'
  with patch.object(console,'task_view',side_effect=AttributeError('PRIVATE secret')):
   status,body=get();assert status==500;assert 'PRIVATE' not in json.dumps(body);assert 'Saved work is unchanged' in body['error']
  assert get()[0]==200
  assert state.read_bytes()==before
  print('Packaged HTTP task detail, error recovery, and unchanged checkpoint PASS')
 finally:
  server.shutdown();server.server_close();console.pool.shutdown(wait=True)
  if console._conversation_store is not None:console._conversation_store.close()
'''


class PackagedEntryTests(unittest.TestCase):
    def test_numeric_tool_totals_never_expose_raw_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text(json.dumps({'type': 'tool_use', 'part': {'id': 't', 'tool': 'bash', 'state': {
                'status': 'completed', 'output': 'private source and token\n7 runs, 164 assertions, 0 failures, 0 errors, 0 skips\nprivate trailing data', 'metadata': {'exit': 0}}}}))
            rows = monitor.activity_log(path)
            self.assertEqual(rows[0]['test_summary'], '7 runs, 164 assertions, 0 failures, 0 errors, 0 skips')
            self.assertNotIn('private', json.dumps(rows))

    def test_package_http_details_and_error_boundary(self):
        result = subprocess.run([sys.executable, '-I', '-c', PACKAGE_CHECK, str(Path(__file__).parents[2])],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()

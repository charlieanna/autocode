"""Complete browser-consumer protocol through the real CLI and fake OpenCode."""
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
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from . import test_opencode


class DashboardConsumerTests(unittest.TestCase):
    def test_registry_busy_feedback_pause_reapproval_review_and_completion(self):
        flow = test_opencode.OpenCodeFlow()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        # This provider is a copied offline fixture. A file barrier proves input
        # reaches the inbox before releasing the active implementation stage.
        provider = flow.root/'fixture-bin/codex'
        source = provider.read_text()
        needle = 'stage = data["stage"]\n'
        self.assertIn(needle, source)
        source = source.replace(needle, needle+'''
if stage == "terra" and os.environ.get("AUTOCODE_CONSUMER_BARRIER"):
    import time
    barrier = Path(os.environ["AUTOCODE_CONSUMER_BARRIER"])
    if barrier.exists():
        barrier.with_suffix(".entered").write_text("stage active")
        deadline = time.monotonic() + 15
        while barrier.exists():
            if time.monotonic() > deadline: raise SystemExit("fixture barrier timed out")
            time.sleep(0.02)
''')
        # This test exercises reapproval and human-review transport, not no-op
        # detection: make the fixture's post-replan implementation a real delta.
        source = source.replace('    result = {**common, "summary": "Greeting written"',
            '    with Path("greet.py").open("a") as fixture_output: fixture_output.write("# revision " + str(uuid.uuid4()) + "\\n")\n'
            '    result = {**common, "summary": "Greeting written"')
        provider.write_text(source)
        barrier = flow.root/'hold-terra'
        flow.env['AUTOCODE_CONSUMER_BARRIER'] = str(barrier)
        flow.launch(['Build a greeting tool','--no-chat'], 2)
        run, state = flow.saved()
        self.assertEqual('WAITING_FOR_USER',state['status'])

        def cli(arguments, expected=0):
            result = subprocess.run([*flow.entry,*arguments], cwd=flow.root,env=flow.env,
                                    capture_output=True,text=True,timeout=20)
            self.assertEqual(expected,result.returncode,result.stdout+result.stderr)
            return json.loads(result.stdout)
        identity=['--workspace',str(flow.project),'--run-dir',str(run)]
        listed=cli(['registry','list','--json'])
        self.assertEqual([str(run)], [row['run_dir'] for row in listed['runs']])
        self.assertEqual('available',listed['runs'][0]['availability'])
        prior_stages=len(state['stages'])
        flow.launch(['--run-dir',str(run),'--answer','Q1=CLI','--no-chat'],0)
        self.assertEqual(prior_stages,len(flow.saved()[1]['stages']))
        flow.launch(['--run-dir',str(run),'--no-chat'],2)
        state=flow.saved()[1]
        old_token=state['displayed_goal']
        flow.launch(['--run-dir',str(run),'--approve-goal',old_token,'--no-chat'],0)
        barrier.touch()
        worker=subprocess.Popen([*flow.entry,*identity,'--no-chat'],cwd=flow.root,env=flow.env,
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+10
            while not barrier.with_suffix('.entered').exists() and worker.poll() is None and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue(barrier.with_suffix('.entered').exists(),'Fake implementation never reached its barrier')
            self.assertIsNone(worker.poll())
            request=['intervention','submit',*identity,'--request-id','feedback-1','--kind','feedback','--text','Keep the original plan history','--json']
            receipt=cli(request)
            pause=cli(['intervention','submit',*identity,'--request-id','pause-1','--kind','pause','--json'])
            self.assertTrue(receipt['accepted'] and pause['accepted'])
            retry=cli(request)
            self.assertTrue(retry['idempotent'])
            self.assertEqual(receipt['receipt'],retry['receipt'])
            pending=cli(['intervention','inspect',*identity,'--json'])
            self.assertEqual(['feedback-1','pause-1'],[row['id'] for row in pending['requests']])
            self.assertIsNone(worker.poll(),'Submission was serialized behind the running workspace')
            barrier.unlink()
            stdout,stderr=worker.communicate(timeout=20)
            self.assertEqual(2,worker.returncode,stdout+stderr)
        finally:
            barrier.unlink(missing_ok=True)
            if worker.poll() is None:
                worker.terminate()
                worker.communicate(timeout=20)

        state=flow.saved()[1]
        self.assertEqual('PAUSED_INTERVENTION',state['status'])
        self.assertEqual('requirements_gather',state['next_stage'])
        self.assertTrue((flow.project/'greet.py').is_file())
        self.assertEqual(1,sum(row['stage']=='terra' for row in state['stages']))
        self.assertEqual(0,sum(row['stage']=='sol' for row in state['stages']))
        status=cli([*identity,'--status'])
        self.assertEqual(0,status['interventions']['pending_count'])
        self.assertEqual(2,len(status['interventions']['applied_receipts']))
        self.assertIsNone(status['interventions']['pause_intent']['acknowledged_at'])
        flow.launch(['--run-dir',str(run),'--approve-goal',old_token,'--no-chat'],2)
        self.assertEqual('draft',flow.saved()[1]['goal_contract']['approval_status'])

        flow.launch(['--run-dir',str(run),'--resume-paused','--no-chat'],2)
        state=flow.saved()[1]
        self.assertEqual('AWAITING_GOAL_APPROVAL',state['status'])
        self.assertIn('Keep the original plan history',state['goal_contract']['body']['constraints'])
        self.assertTrue(all(row.get('resumed_at') for row in state['applied_interventions']))
        self.assertTrue(any(event.get('kind')=='goal_approval' and event.get('token')==old_token for event in state['user_events']))
        new_token=state['displayed_goal']
        self.assertNotEqual(old_token,new_token)
        flow.launch(['--run-dir',str(run),'--approve-goal',new_token,'--no-chat'],0)
        flow.launch(['--run-dir',str(run),'--no-chat'],2)
        state=flow.saved()[1]
        self.assertEqual('human_review',state['user_request']['kind'])
        self.assertEqual('PASS',state['validation']['verdict'])
        before_review=len(state['stages'])
        flow.launch(['--run-dir',str(run),'--approve-review','C1','--review-token',state['displayed_review'],'--no-chat'],0)
        self.assertEqual(before_review,len(flow.saved()[1]['stages']))
        flow.launch(['--run-dir',str(run),'--no-chat'],0)
        self.assertEqual('TASK_COMPLETE',flow.saved()[1]['status'])
        before=(run/'state.json').read_bytes()
        self.assertTrue(cli([*identity,'--status'])['completion_current'])
        self.assertEqual(before,(run/'state.json').read_bytes())
        self.assertEqual(1,len(cli(['registry','list','--json'])['runs']))
        self.assertEqual(receipt['receipt'],cli(request)['receipt'])


if __name__ == '__main__':
    unittest.main()

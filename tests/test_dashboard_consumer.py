"""Complete browser-consumer protocol through the real CLI and fake OpenCode."""
import datetime
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
        # The exact trusted fixture includes this barrier and a post-replan delta;
        # do not mutate the verified executable to add them at runtime.
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
        def missed_barrier(worker,started):
            # #314: tell a slow start from an early product/provider exit. State is read before the
            # terminate, so it shows the run at the miss; the output wait keeps the 20 s cleanup bound.
            waited,code,at=time.monotonic()-started,worker.poll(),datetime.datetime.now(datetime.timezone.utc).isoformat()
            try:
                saved=json.loads((run/'state.json').read_text())
                active,last=saved.get('active_stage') or {},(saved.get('stages') or [{}])[-1]
                where=(f"status={saved.get('status')} next_stage={saved.get('next_stage')} "
                       f"active_stage={active.get('stage')} started_at={active.get('started_at')} "
                       f"last_stage={last.get('stage')} finished_at={last.get('finished_at')}")
            except (OSError,ValueError) as error:
                where=f'unreadable: {error!r}'
            if code is None:
                worker.terminate()
            try:
                out,err=worker.communicate(timeout=20)
            except subprocess.TimeoutExpired as error:
                out,err=(f'<no exit 20 s after terminate; partial: {text!r}>' for text in (error.output,error.stderr))
            ended=(f'exited with code {code}' if code is not None else
                   f'still running; terminated, code {worker.returncode}' if worker.returncode is not None else
                   'still running; terminate sent, no exit within 20 s')
            return (f'\nwaited {waited:.3f} s (miss at {at}); worker {ended}; state.json at the miss: {where}'
                    f'\n--- worker stdout ---\n{out}\n--- worker stderr ---\n{err}')
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
            # A live worker on a contended host can need well over 10 s to reach the Builder (#314);
            # the loop still ends as soon as the barrier is entered or the worker exits.
            started=time.monotonic(); deadline=started+120
            while not barrier.with_suffix('.entered').exists() and worker.poll() is None and time.monotonic()<deadline:
                time.sleep(.02)
            entered=barrier.with_suffix('.entered').exists()
            self.assertTrue(entered,'Fake implementation never reached its barrier'+('' if entered else missed_barrier(worker,started)))
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

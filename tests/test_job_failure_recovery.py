"""Stopped jobs through the public CLI, with a fast provider and injected deadline.

No private checkpoint is fabricated or edited. Faults are injected at the
provider/stopped-source boundary; source, artifact and TaskRun receipts are the
oracles. A copied Git seed avoids repeated Git setup and no test sleeps.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from autocode_taskrun import TaskRun, TaskRunError
import autocode_job_source as source
import autocode_run_records as records

HERE = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.environ.get('AUTOCODE_JOB_TEST_RUNTIME', HERE))
ORIGINAL = 'def double(n):\n    return n * 2\n'
DIRTY = 'def double(n):\n    return n * 3\n'
BROKEN = 'def double(n):\n    return n + 2\n'
LATER = 'def double(n):\n    return n - 2\n'

PROVIDER = r'''#!/usr/bin/env python3
import json,os,sys,signal,subprocess,uuid
from pathlib import Path
if sys.argv[1:3]==['sandbox','--help']:
 print('--config -- macos linux');raise SystemExit(0)
if sys.argv[1:2]==['sandbox']:
 raise SystemExit(subprocess.call(sys.argv[sys.argv.index('--')+1:]))
if sys.argv[1:]==['login','status']:
 print('Logged in (offline)');raise SystemExit(0)
if sys.argv[1:]==['--version']:
 print('codex-cli fixture');raise SystemExit(0)
sys.stdin.read()
with Path(os.environ['JOB_CALLS']).open('a') as f:f.write('request\n')
mode=os.environ['JOB_MODE']
if mode=='lock':print('Error: database is locked',flush=True);raise SystemExit(42)
print(json.dumps({'type':'thread.started','thread_id':str(uuid.uuid4())}),flush=True)
for i in range(4):print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'fragment '+str(i)}}),flush=True)
if mode in ('success','terminal'):
 result={'verdict':'approve','summary':'Equivalent integer addition','change_under_review':'pr-double.patch','findings':[],'change_patch':'pr-double.patch','tests_run':[],'delivered_tests':[]}
 Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps(result))
 print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1200,'output_tokens':450}}),flush=True);raise SystemExit(42 if mode=='terminal' else 0)
if mode in ('write','later','missing','corrupt','replace_error','staging_error','mode','cleanup'):
 Path('calc.py').write_text('def double(n):\n    return n + 2\n')
 Path('notes.txt').write_text('changed\n')
 if mode=='mode':Path('calc.py').chmod(0o644)
if mode=='create':Path('scratch.py').write_text('attempt scratch\n')
if mode=='delete':Path('calc.py').unlink();Path('notes.txt').unlink()
error={'capacity':'resource_exhausted','rate':'429 rate_limit','external':'permission requested: external_directory'}.get(mode,'interrupted partial attempt')
print(json.dumps({'type':'turn.failed','error':{'message':error},'usage':{'input_tokens':1200,'output_tokens':450}}),flush=True)
if mode not in ('exit42','abandon','capacity','rate','external'):os.kill(os.getpid(),signal.SIGTERM)
raise SystemExit(42)
'''

WRAPPER = r'''import json,os,sys
from pathlib import Path
sys.path.insert(0,os.environ['JOB_RUNTIME']+'/tools')
import autocode
try:import autocode_job_source as source
except ImportError:source=None
mode=os.environ['JOB_MODE']
# Model a provider's saved joint-transport configuration at the normal settings
# boundary, never by editing a checkpoint or replacing the controller.
if os.environ.get('JOB_TRANSPORT'):
 configure=autocode.configure
 def selected_transport(*args,**kwargs):
  selected=configure(*args,**kwargs)
  selected['transport_identities']={'codex':{'fixture':os.environ['JOB_TRANSPORT']}}
  return selected
 autocode.configure=selected_transport
import autocode_provider_recovery as provider_recovery
startup=provider_recovery.recover_startup
def no_wait(*args,**kwargs):
 kwargs['sleep']=lambda seconds:None
 return startup(*args,**kwargs)
provider_recovery.recover_startup=no_wait
if mode=='abandon':
 run_role=autocode.run_role
 def crash(**kw):
  try:return run_role(**kw)
  except autocode.support.Paused:os._exit(0)
 autocode.run_role=crash
if mode not in ('success','terminal','abandon','exit42','capacity','rate','external','lock'):
 def deadline(child, seconds, checkpoint, *, activity=None, **kwargs):
  code=child.wait(timeout=10);checkpoint([])
  if mode=='cleanup':raise autocode.processes.ProcessError('injected cleanup cannot be proved')
  activity.timeout={'kind':'stage','reason':f'Stage exceeded its {seconds}-second hard runtime limit (injected clock)'}
  return code,True
 autocode.processes.wait_for_stage=deadline
if source and mode in ('later','missing','corrupt','replace_error','staging_error'):
 real=source.stopped
 def witness(workspace,base,record):
  real(workspace,base,record)
  if mode=='later':(Path(workspace)/'calc.py').write_text('def double(n):\n    return n - 2\n')
  if mode in ('missing','corrupt'):
   capture=json.loads(Path(record['job_source']['capture']).read_text())
   blob=Path(capture['blobs']['notes.txt'])
   if mode=='missing':blob.unlink()
   else:blob.write_text('not the original bytes')
 source.stopped=witness
 if mode=='replace_error':
  replace=source.os.replace
  def fail(a,b):
   if Path(b).name=='notes.txt':raise OSError('injected replacement failure')
   return replace(a,b)
  source.os.replace=fail
 if mode=='staging_error':
  create=source.tempfile.mkstemp
  def fail(*a,**kw):
   if kw.get('prefix')=='.autocode-restore-':raise OSError('injected staging failure')
   return create(*a,**kw)
  source.tempfile.mkstemp=fail
raise SystemExit(autocode.main())
'''


class JobFailureTaskRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_temp = tempfile.TemporaryDirectory(prefix='job-failure-seed-')
        cls.seed = Path(cls.seed_temp.name) / 'seed'
        cls.seed.mkdir()
        (cls.seed/'.gitignore').write_text('.autocode/\n__pycache__/\nreview/\ndesign/\ndiscussion/\n')
        (cls.seed/'calc.py').write_text(ORIGINAL)
        (cls.seed/'pr-double.patch').write_text('diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def double(n):\n-    return n * 2\n+    return n + n\n')
        subprocess.run(['git','init','-q',str(cls.seed)],check=True)
        # A copied seed must stay immutable while each case copies its objects.
        subprocess.run(['git','-C',str(cls.seed),'config','maintenance.auto','false'],check=True)
        subprocess.run(['git','-C',str(cls.seed),'config','gc.auto','0'],check=True)
        subprocess.run(['git','-C',str(cls.seed),'add','-A'],check=True)
        subprocess.run(['git','-C',str(cls.seed),'-c','user.name=Fixture','-c','user.email=f@example.test','commit','-qm','seed'],check=True)

    @classmethod
    def tearDownClass(cls):
        cls.seed_temp.cleanup()

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='job-failure-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.workspace = root/'project'
        shutil.copytree(self.seed,self.workspace)
        (self.workspace/'notes.txt').write_text('memo\n')
        private = self.workspace/'.autocode'
        bindir = private/'bin';bindir.mkdir(parents=True)
        (bindir/'codex').write_text(PROVIDER);(bindir/'codex').chmod(0o755)
        self.wrapper = private/'wrapper.py';self.wrapper.write_text(WRAPPER)
        self.calls = private/'calls'
        self.env = {'PATH':str(bindir)+os.pathsep+os.environ['PATH'],'AUTOCODE_HOME':str(root/'registry'),
                    'PYTHONDONTWRITEBYTECODE':'1','JOB_RUNTIME':str(RUNTIME),'JOB_CALLS':str(self.calls)}
        self.options = ('--engine','codex','--sol-model','gpt-6-sol','--astra-model','gpt-6-sol',
                        '--max-stage-seconds','900','--max-seconds','1200','--max-idle-seconds','0','--max-tool-seconds','0')

    def start(self, mode='timeout', workflow='review'):
        self.env['JOB_MODE']=mode
        return TaskRun.start(self.workspace,'Review pr-double.patch against calc.py for built-in integer equivalence.',
                    command=(sys.executable,'-B',str(self.wrapper)),options=self.options,
                    start_options=('--workflow',workflow),env=self.env,timeout=60)

    def count(self):
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0

    def paused(self,run,stage='review_change'):
        view=run.status();self.assertEqual('PAUSED_JOB_FAILURE',view['status'],view)
        self.assertEqual(stage,view['next_stage']);self.assertFalse(view['done'])
        self.assertEqual('retry_job',view['needs']['kind']);self.assertFalse(view['needs'].get('questions'))
        self.assertTrue(view['needs']['job_retry_token']);self.assertEqual(1,self.count())
        return view['needs']

    def test_t1_timed_out_review_change_pauses_under_review_owner(self):
        run=self.start();need=self.paused(run)
        self.assertEqual('review',run.status()['workflow']);self.assertIn('900-second',need['reason'])
        self.assertEqual(ORIGINAL,(self.workspace/'calc.py').read_text())
        usage=run.status()['usage']['tokens'];self.assertEqual(1200,usage['input_tokens']);self.assertEqual(450,usage['output_tokens'])
        logs=list(Path(need['archive']).glob('*.jsonl'));self.assertEqual(1,len(logs))
        events=[json.loads(line) for line in logs[0].read_text().splitlines()]
        self.assertEqual(4,len([e for e in events if e.get('type')=='item.completed']))
        finished=[json.loads(line) for line in (run.run_dir/'activity.jsonl').read_text().splitlines() if json.loads(line).get('event')=='stage_finished' and json.loads(line).get('stage')=='review_change']
        self.assertEqual(1,len(finished));self.assertTrue(finished[0]['timed_out']);self.assertEqual(-15,finished[0]['exit_code']);self.assertEqual(0,finished[0]['changed_files'])

    def test_t2_abandon_names_the_owning_reviewer_and_retains_fragments(self):
        run=self.start('abandon')
        proc=subprocess.run([*run.command,'--status','--workspace',str(self.workspace),'--run-dir',str(run.run_dir)],
                    env={**os.environ,**run.env},capture_output=True,text=True,check=True,timeout=60)
        selected=json.loads(proc.stdout)['attempt_id']
        self.assertEqual(1,self.count())
        action=subprocess.run([*run.command,'--abandon-stage',selected,'--workspace',str(self.workspace),'--run-dir',str(run.run_dir)],
                    env={**os.environ,**run.env},capture_output=True,text=True,timeout=60)
        self.assertEqual(0,action.returncode,action.stderr)
        view=run.status();self.assertEqual('PAUSED_STAGE_ABANDONED',view['status'],view)
        self.assertEqual('review_change',view['next_stage']);self.assertIn('Reviewer',view['stop_reason'])
        self.assertIn('explicit fresh retry',view['stop_reason']);self.assertEqual(1,self.count())
        self.assertEqual(ORIGINAL,(self.workspace/'calc.py').read_text())
        logs=list(Path(view['needs']['archive']).glob('*.jsonl'));self.assertTrue(logs)
        self.assertEqual(4,logs[0].read_text().count('fragment '))

    def test_terminal_turn_is_retained_for_reconciliation(self):
        run=self.start('terminal');view=run.status()
        self.assertNotEqual('PAUSED_JOB_FAILURE',view['status'],view)
        self.assertEqual(1,self.count());self.assertFalse(list(run.run_dir.rglob('archived-review-change-*')))
        self.assertTrue(list(run.run_dir.rglob('review-change-01.jsonl')))

    def test_t3_explicit_retry_launches_exactly_one_fresh_attempt(self):
        run=self.start();token=self.paused(run)['job_retry_token'];run.env['JOB_MODE']='success'
        view=run.retry_job(token);self.assertTrue(view['done'],view);self.assertEqual(2,self.count())
        with self.assertRaises(TaskRunError):run.retry_job(token)
        self.assertEqual(2,self.count())

    def test_t4_retry_rejects_stale_source_revision(self):
        run=self.start();token=self.paused(run)['job_retry_token'];(self.workspace/'calc.py').write_text(BROKEN)
        with self.assertRaisesRegex(TaskRunError,'Source changed'):run.retry_job(token)
        self.assertEqual(1,self.count());self.assertEqual(BROKEN,(self.workspace/'calc.py').read_text())

    def test_t5_design_discuss_and_bug_jobs_keep_their_owner(self):
        for workflow,stage in (('design','review_design'),('discuss','answer_question'),('bugfix','investigate_bug')):
            with self.subTest(workflow=workflow):
                # Each workflow owns a separate real CLI run and provider attempt.
                other=self.workspace.with_name('project-'+workflow);shutil.copytree(self.seed,other)
                prior=self.workspace;self.workspace=other
                try:self.paused(self.start(workflow=workflow),stage)
                finally:self.workspace=prior
                self.calls.unlink()

    def test_t6_special_failures_pause_without_automatic_retry(self):
        for mode,reason in (('capacity','capacity'),('rate','rate limit'),('external','external-directory'),('lock','database is locked')):
            with self.subTest(mode=mode):
                run=self.start(mode);need=self.paused(run);self.assertIn(reason,need['reason'])
                run.advance();self.assertEqual(1,self.count());self.calls.unlink()

    def test_t7_successful_read_only_review_completion_preserved(self):
        run=self.start('success');view=run.status();self.assertTrue(view['done'],view);self.assertIsNone(view['needs'])
        self.assertEqual(ORIGINAL,(self.workspace/'calc.py').read_text());self.assertEqual(1,self.count())
        self.assertEqual('approve',json.loads((self.workspace/'review/findings.json').read_text())['verdict'])

    def test_ac14_plain_resume_keeps_pause_and_spends_nothing(self):
        run=self.start();self.paused(run);run.resume_paused();self.paused(run)

    def test_ac15_retry_rejects_model_effort_and_limit_changes(self):
        run=self.start();token=self.paused(run)['job_retry_token']
        for flags in (('--sol-model','gpt-6-astra'),('--sol-reasoning-effort','high'),('--max-stage-seconds','901')):
            with self.subTest(flags=flags):
                changed=TaskRun(self.workspace,run.run_dir,command=run.command,options=flags,env=run.env,timeout=60)
                with self.assertRaisesRegex(TaskRunError,'configuration|limits'):changed.retry_job(token)
                self.assertEqual(1,self.count())

    def test_saved_joint_transport_change_invalidates_retry(self):
        self.env['JOB_TRANSPORT']='original'
        run=self.start();token=self.paused(run)['job_retry_token']
        run.env.update(JOB_TRANSPORT='changed',JOB_MODE='success')
        with self.assertRaisesRegex(TaskRunError,'configuration|limits'):run.retry_job(token)
        self.assertEqual(1,self.count())

    def test_non_executable_mode_change_invalidates_retry(self):
        run=self.start();token=self.paused(run)['job_retry_token']
        (self.workspace/'calc.py').chmod(0o600)
        with self.assertRaisesRegex(TaskRunError,'Source changed'):run.retry_job(token)
        self.assertEqual(1,self.count())

    def test_ac18_old_token_cannot_authorize_a_new_failure(self):
        run=self.start();old=self.paused(run)['job_retry_token'];run.retry_job(old)
        latest=run.status()['needs']['job_retry_token'];self.assertNotEqual(old,latest);self.assertEqual(2,self.count())
        with self.assertRaisesRegex(TaskRunError,'token'):run.retry_job(old)
        self.assertEqual(2,self.count())

    def test_ac19_ordinary_failure_is_archived_once_on_repeated_attach(self):
        run=self.start('exit42');need=self.paused(run)
        for _ in range(2):
            again=TaskRun(self.workspace,run.run_dir,command=run.command,options=run.options,env=run.env,timeout=60)
            again.advance();self.paused(again)
        self.assertEqual(1,len(list(Path(need['archive']).parent.glob('archived-review-change-01-*'))))

    def test_ac20_created_file_is_deleted(self):
        run=self.start('create');need=self.paused(run);self.assertFalse((self.workspace/'scratch.py').exists())
        self.assertIn('scratch.py',need['write_diagnosis']['deleted'])

    def test_ac21_dirty_tracked_and_untracked_originals_are_restored(self):
        (self.workspace/'calc.py').write_text(DIRTY)
        run=self.start('write');need=self.paused(run)
        self.assertEqual(DIRTY,(self.workspace/'calc.py').read_text());self.assertEqual('memo\n',(self.workspace/'notes.txt').read_text())
        self.assertEqual(['calc.py','notes.txt'],need['write_diagnosis']['restored'])

    def test_ac22_later_user_edit_is_preserved_and_blocks_retry(self):
        run=self.start('later');need=self.paused(run);self.assertEqual(LATER,(self.workspace/'calc.py').read_text())
        self.assertIn('calc.py',need['unrestored'])
        with self.assertRaisesRegex(TaskRunError,'Unrestored'):run.retry_job(need['job_retry_token'])
        self.assertEqual(1,self.count())

    def test_ac24_exact_executable_mode_is_restored(self):
        (self.workspace/'calc.py').chmod(0o755)
        run=self.start('mode');self.paused(run)
        self.assertEqual(ORIGINAL,(self.workspace/'calc.py').read_text())
        self.assertEqual(0o755,stat.S_IMODE((self.workspace/'calc.py').stat().st_mode))

    def test_ac25_missing_and_corrupt_original_capture_block_retry(self):
        for mode in ('missing','corrupt'):
            with self.subTest(mode=mode):
                run=self.start(mode);need=self.paused(run);self.assertEqual('changed\n',(self.workspace/'notes.txt').read_text())
                self.assertIn('notes.txt',need['unrestored'])
                with self.assertRaisesRegex(TaskRunError,'Unrestored'):run.retry_job(need['job_retry_token'])
                self.assertEqual(1,self.count());self.calls.unlink();(self.workspace/'notes.txt').write_text('memo\n')

    def test_ac27_deleted_dirty_tracked_and_untracked_originals_return(self):
        (self.workspace/'calc.py').write_text(DIRTY);(self.workspace/'notes.txt').chmod(0o600)
        run=self.start('delete');self.paused(run)
        self.assertEqual(DIRTY,(self.workspace/'calc.py').read_text());self.assertEqual('memo\n',(self.workspace/'notes.txt').read_text())
        self.assertEqual(0o600,stat.S_IMODE((self.workspace/'notes.txt').stat().st_mode))

    def test_ac28_staging_and_replace_errors_keep_current_bytes_and_clean_temps(self):
        for mode in ('staging_error','replace_error'):
            with self.subTest(mode=mode):
                run=self.start(mode);need=self.paused(run)
                self.assertEqual('changed\n',(self.workspace/'notes.txt').read_text());self.assertIn('notes.txt',need['unrestored'])
                self.assertFalse(list(self.workspace.glob('.autocode-restore-*')))
                self.assertTrue(list(run.run_dir.rglob('*.source/*')))
                with self.assertRaisesRegex(TaskRunError,'Unrestored'):run.retry_job(need['job_retry_token'])
                self.assertEqual(1,self.count());self.calls.unlink();(self.workspace/'notes.txt').write_text('memo\n')

    def test_unproven_process_cleanup_retains_attempt_without_restoration(self):
        run=self.start('cleanup');view=run.status()
        self.assertNotEqual('PAUSED_JOB_FAILURE',view['status'],view)
        self.assertEqual('review_change',view['next_stage']);self.assertEqual(1,self.count())
        self.assertEqual(BROKEN,(self.workspace/'calc.py').read_text())
        self.assertFalse(list(run.run_dir.rglob('*.witness.json')))
        self.assertFalse(list(run.run_dir.rglob('archived-review-change-*')))

    def test_generic_retry_boolean_does_not_authorize_a_job(self):
        run=self.start();self.paused(run)
        with self.assertRaisesRegex(TaskRunError,'token'):run.retry_failed_stage()
        self.assertEqual(1,self.count())


class JobRecoveryRouteTests(unittest.TestCase):
    def test_t8_build_and_planning_routes_are_preserved(self):
        for stage,role,expected in (('sol','sol',('astra_review','EXECUTING')),('terra','terra',('astra_review','EXECUTING')),
                ('astra_review','astra',('astra_review','EXECUTING')),('astra_discovery','astra',('astra_discovery','DISCOVERING'))):
            with self.subTest(stage=stage):self.assertEqual(expected,records.timeout_recovery_route({}, {'stage':stage,'role':role}))

    def test_all_six_jobs_keep_their_stage(self):
        for stage in ('review_change','review_design','check_design','answer_question','investigate_bug','investigate_stuck'):
            with self.subTest(stage=stage):self.assertEqual((stage,'PAUSED_OR_BLOCKED'),records.timeout_recovery_route({}, {'stage':stage,'role':'sol'}))


if __name__=='__main__':unittest.main()

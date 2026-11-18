"""A hard-stopped Builder's retry gets its ordinary inactivity budget through TaskRun."""

import sys
import unittest

from autocode_taskrun import TaskRun

from tests import test_subprocess as fixture

WRAPPER = """\
import sys,runpy
sys.path.insert(0, TOOLS)
import autocode

wait=autocode.processes.wait_for_stage
def deadline(child, seconds, checkpoint, *, activity=None, **kwargs):
    name=activity.path.stem if activity is not None else ''
    stopped=None
    if name=='builder-02':
        clock=activity.clock; later=clock()+120; activity.clock=lambda:later
        try:stopped=activity.expired()
        finally:activity.clock=clock
    code,timed_out=wait(child,seconds,checkpoint,activity=activity,**kwargs)
    if name=='builder-01':
        activity.timeout={'kind':'stage','reason':'Injected hard stage deadline after provider activity'}
        return code,True
    if stopped:
        activity.timeout=stopped
        return code,True
    return code,timed_out
autocode.processes.wait_for_stage=deadline
before_review=autocode.build_loop.regression.before_review
def before_validation(state,*args,**kwargs):
    if state.get('next_stage')=='tester':
        raise autocode.support.Paused('PAUSED_REQUESTED','Fixture stops before fresh independent validation; no PASS granted')
    return before_review(state,*args,**kwargs)
autocode.build_loop.regression.before_review=before_validation
sys.argv[0]=TOOLS+'/autocode.py'
runpy.run_path(sys.argv[0],run_name='__main__')
"""


class StageRetryStartupCLITests(unittest.TestCase):
    setUp = fixture.SubprocessFlow.setUp
    new_run_engine_args = ("--engine", "codex")

    def test_hard_timeout_then_slow_start_reaches_validation_without_another_timeout(self):
        provider = self.root / "fixture-bin" / "codex"
        source = provider.read_text()
        marker = '\n_quota_model = os.environ.get("AUTOCODE_FIXTURE_QUOTA_MODEL")'
        self.assertEqual(1, source.count(marker))
        first = """
if stage == 'builder' and Path(sys.argv[sys.argv.index('-o')+1]).stem == 'builder-01':
    print(json.dumps({'type':'thread.started','thread_id':'startup-regression'}),flush=True)
    raise SystemExit(0)
"""
        provider.write_text(source.replace(marker, first + marker))
        wrapper = self.root / "stage-startup.py"
        wrapper.write_text(WRAPPER.replace("TOOLS", repr(str(fixture.TOOLS))))
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        run = TaskRun.start(
            self.project,
            "Build greeting",
            command=(sys.executable, str(wrapper)),
            options=("--engine", "codex", "--max-cost-usd", "0", "--max-stage-seconds", "300"),
            env=self.env,
            cwd=self.root,
            timeout=240,
        )
        initial = run.status()
        self.assertIn(
            "Q1", [q["id"] for q in (initial.get("needs") or {}).get("questions", [])], initial.get("stop_reason")
        )
        run.answer("Q1", "CLI")
        view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], view["stop_reason"])
        run.approve_plan(view["needs"]["token"])
        view = run.advance_until_input()
        self.assertEqual("PAUSED_REQUESTED", view["status"], view.get("stop_reason"))
        self.assertEqual("tester", view["next_stage"])
        self.assertFalse(view["done"], "independent validation still has to execute")
        attempts = [row for row in view["usage"]["accounting"]["attempts"] if row.get("stage") == "builder"]
        self.assertEqual(2, len(attempts))
        self.assertTrue(attempts[0]["timed_out"])
        self.assertFalse(attempts[1]["timed_out"])

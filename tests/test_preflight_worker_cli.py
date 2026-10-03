"""Real TaskRun admission with a fake CLI whose model branch is counted."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import autocode_taskrun as taskrun
from tests.test_task_preflight import PreflightFixture, ROOT, BRIEF, OPTIONS
from tests.test_preflight_contract import browser_contract


class WorkerAdmissionCliTests(PreflightFixture):
    def setUp(self):
        super().setUp()
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        self.marker = self.root / 'paid-attempts'
        model = (ROOT / 'tools/live_fixture_provider.py').read_text()
        model = model.replace('import json', 'import os\nimport json', 1)
        model = model.replace('    output.write_text(json.dumps(report))',
            "    with open(os.environ['FAKE_PREFLIGHT_CALLS'], 'a') as marker: marker.write(stage + '\\n')\n    output.write_text(json.dumps(report))")
        script = ('#!' + sys.executable + '\nimport os,sys\n'
            'if len(sys.argv)>1 and sys.argv[1]=="sandbox":\n'
            ' if "--help" in sys.argv: print("Model-free fixture sandbox --config"); raise SystemExit(0)\n'
            ' args=sys.argv[sys.argv.index("--")+1:]\n'
            ' os.execvpe(args[0],args,dict(os.environ, FIXTURE_WORKER="restricted"))\n'
            'else:\n exec(compile(' + repr(model) + ', "offline-provider", "exec"))\n')
        (bin_dir / 'codex').write_text(script)
        (bin_dir / 'codex').chmod(0o755)
        self.env = {'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
            'FAKE_PREFLIGHT_CALLS': str(self.marker), 'AUTOCODE_HOME': str(self.root / 'registry'),
            'PYTHONDONTWRITEBYTECODE': '1'}

    def configure(self, kind=None):
        body = {'version': 2, 'inputs': [], 'checks': [{'id': 'worker-setup', 'phase': 'planning',
            'argv': ['{python}', 'probe.py'], 'execution': 'worker', 'contract': kind or {'kind': 'command'},
            'reuse': False, 'timeout_seconds': 20, 'recovery': 'Restore the approved local setup and resume'}]}
        self.path.write_text(json.dumps(body))

    def start(self):
        return taskrun.TaskRun.start(self.workspace, BRIEF, options=OPTIONS,
            start_options=('--workflow', 'build', '--task-preflight', str(self.path)), env=self.env, timeout=90)

    def resume_once(self, run):
        run.options += ('--pause-after-stage',)
        view = run.resume_paused()
        self.assertEqual(['requirements_gather'], self.marker.read_text().splitlines())
        self.assertEqual('READY', view['task_preflight']['status'], view)
        self.assertFalse(view['done'])
        self.assertFalse(view['evidence'].get('regression_proof'))
        self.assertTrue(view['task_preflight']['execution_identity']['worker_permissions_checked'])
        self.assertEqual('codex', view['task_preflight']['execution_identity']['worker']['provider'])

    def test_operator_success_does_not_hide_denial_under_worker_permissions(self):
        self.configure()
        probe = self.workspace / 'probe.py'
        probe.write_text("import os\nraise SystemExit(13 if os.environ.get('FIXTURE_WORKER') else 0)\n")
        operator = subprocess.run([sys.executable, str(probe)], cwd=self.workspace)
        self.assertEqual(0, operator.returncode)
        run = self.start()
        view = run.status()
        self.assertEqual('PAUSED_TASK_PREFLIGHT', view['status'], view)
        self.assertEqual(13, view['task_preflight']['checks'][0]['exit_code'])
        self.assertFalse(self.marker.exists())
        probe.write_text("import os\nassert os.environ['FIXTURE_WORKER']=='restricted'\n"
            "assert os.environ['AUTOCODE_OUTPUT_MODE']=='conservative'\n"
            "assert os.environ['AUTOCODE_OUTPUT_ATTEMPT'].endswith('.jsonl')\nprint('worker setup repaired')\n")
        self.resume_once(run)

    def test_zero_exit_in_wrong_browser_state_blocks_before_paid_dispatch(self):
        expected = browser_contract()
        self.configure(expected)
        good = {**expected, 'kind': 'prerequisite', 'status': 'READY', 'setup': True, 'teardown': True, 'launched': True, 'captured': True}
        probe = self.workspace / 'probe.py'
        def write(value):
            probe.write_text('print(' + repr('AUTOCODE_PREREQUISITE=' + json.dumps(value)) + ')\n')
        write({**good, 'ready': ['catalogue-completed']})
        run = self.start()
        view = run.status()
        self.assertEqual('PAUSED_TASK_PREFLIGHT', view['status'], view)
        self.assertIn('Browser ready differs', view['stop_reason'])
        self.assertFalse(self.marker.exists())
        write(good)
        self.resume_once(run)

    def test_pause_at_the_end_of_a_probe_does_not_admit_the_model(self):
        self.configure()
        (self.workspace / 'probe.py').write_text("from pathlib import Path\nrun=next(Path('.autocode/runs').iterdir())\n(run/'pause-requested').touch()\n")
        run = self.start()
        view = run.status()
        self.assertEqual('PAUSED_REQUESTED', view['status'], view)
        self.assertFalse(self.marker.exists())


if __name__ == '__main__':
    unittest.main()

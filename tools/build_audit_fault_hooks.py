"""TEST ONLY: copied as sitecustomize in an isolated CLI audit environment.

Crashes the test-owned controller at real process/Git I/O boundaries, without
editing production code or fabricating its persisted state. Never import normally.
"""
import os
from pathlib import Path
import subprocess
import sys

mode = os.environ.get('BUILD_AUDIT_CONTROLLER_FAULT')
root = os.environ.get('BUILD_AUDIT_FAULT_ROOT')
if mode and root and Path(sys.argv[0]).name == 'autocode_build.py':
    marker = Path(root) / ('fault-' + mode)
    popen = subprocess.Popen
    run = subprocess.run

    def crash():
        marker.write_text(str(os.getpid()))
        os._exit(97)

    def launch(command, *args, **kwargs):
        worker = isinstance(command, list) and any(str(c).endswith('autocode_builder_worker.py') for c in command)
        if worker and not marker.exists() and mode == 'before_worker':
            crash()
        child = popen(command, *args, **kwargs)
        if worker and not marker.exists() and mode == 'after_worker':
            crash()
        return child

    def execute(command, *args, **kwargs):
        applies = isinstance(command, list) and command[0] == 'git' and 'apply' in command
        if applies and not marker.exists() and mode == 'integration_conflict' and '--check' in command:
            # Real conflicting filesystem content appears after the baseline check.
            target = Path(command[command.index('-C') + 1]) / 'server/health.py'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('USER_CONFLICT = True\n')
            marker.write_text(str(os.getpid()))
        result = run(command, *args, **kwargs)
        if applies and not marker.exists() and mode == 'after_integration' and '--check' not in command and result.returncode == 0:
            crash()
        return result

    subprocess.Popen = launch
    subprocess.run = execute

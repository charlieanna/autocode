"""Independent inventory oracle using only public CLI and frozen fixture inputs."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from harness.oracle import Check, python_tests, scratch_copy, tail

ROOT = Path(__file__).resolve().parents[3]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(project, scenario):
    tests = python_tests(project)
    checks = [Check('functional_health', tests.returncode == 0, tail(tests))]
    frozen = scenario.seed / 'design'
    files = [path for path in frozen.iterdir() if path.name != 'manifest-v2.json']
    intact = all((project / 'design' / path.name).is_file()
                 and digest(project / 'design' / path.name) == digest(path) for path in files)
    checks.append(Check('original_references_unchanged', intact))
    try:
        body = json.loads((project / 'design/manifest-v2.json').read_text())
        expected = json.loads((scenario.reference / 'design/manifest-v2.json').read_text())
        ids = [row['id'] for row in body['cases']]
        checks.append(Check('every_unique_source_case', len(ids) == len(set(ids))
                            and set(ids) == {row['id'] for row in expected['cases']}))
    except (ValueError, KeyError, OSError, TypeError):
        checks.append(Check('every_unique_source_case', False, 'Missing or malformed inventory'))
    # No imported runner module and no live provider: input readiness is tested by
    # the same CLI an operator uses, in a scratch workspace with model calls refused.
    with scratch_copy(project) as copy:
        subprocess.run(['git','init','-q',str(copy)],check=True)
        subprocess.run(['git','-C',str(copy),'add','.'],check=True)
        subprocess.run(['git','-C',str(copy),'-c','user.name=Oracle','-c','user.email=oracle@example.test',
                        'commit','-qm','frozen offline input'],check=True)
        bin_dir=copy.parent/'bin'; bin_dir.mkdir()
        stub=bin_dir/'codex'
        stub.write_text('#!/usr/bin/env python3\nimport sys\n'
                        'if sys.argv[1:] == ["login","status"]: print("Logged in using ChatGPT (offline fixture)")\n'
                        'elif sys.argv[1:] == ["--version"]: print("codex-cli 0.125.0")\n'
                        'else: sys.exit("Model launches are forbidden in this offline oracle")\n')
        stub.chmod(0o755)
        env={**os.environ,'PATH':str(bin_dir)+os.pathsep+os.environ.get('PATH',''),
             'AUTOCODE_HOME':str(copy.parent/'registry'),'PYTHONDONTWRITEBYTECODE':'1'}
        result=subprocess.run([sys.executable,str(ROOT/'tools/autocode.py'),
            'Implement the complete approved exported inventory','--workspace',str(copy),'--in-place',
            '--engine','codex','--no-chat','--dry-run','--figma-manifest',str(copy/'design/manifest-v2.json')],
            cwd=copy,text=True,capture_output=True,timeout=30,env=env)
        checks.append(Check('public_cli_complete_input_gate',result.returncode==0,tail(result)))
    return checks

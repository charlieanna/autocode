"""A catalog citation repair reaches the requirements gate without replaying intake."""

import json
import subprocess
import sys
import unittest

from autocode_taskrun import TaskRun

from . import test_subprocess


@unittest.skipUnless(sys.platform == "linux", "supervised provider requires supported Linux")
class RequirementsCitationCliTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp

    def test_repair_receives_exact_invalid_citations_and_reopen_does_not_replay(self):
        (self.project / "greet.py").write_text("print('Hello')\n")
        subprocess.run(["git", "-C", str(self.project), "add", "greet.py"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.project),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=f@example.test",
                "commit",
                "-qm",
                "source",
            ],
            check=True,
        )
        provider = self.root / "fixture-bin/codex"
        base = provider.with_name("base_codex.py")
        provider.rename(base)
        probe = self.root / "provider-observations.jsonl"
        provider.write_text(f"""#!{sys.executable}
import json, subprocess, sys
from pathlib import Path
if sys.argv[1:] == ['login', 'status']:
    print('Logged in using ChatGPT (offline fixture)')
    raise SystemExit(0)
prompt = sys.stdin.read()
data = json.loads(prompt.split('CURRENT HANDOFF DATA\\n', 1)[1])
repair = bool(data.get('report_repair'))
stage = data['original']['stage'] if repair else data['stage']
result = subprocess.run([sys.executable, {str(base)!r}, *sys.argv[1:]],
                        input=prompt, text=True, capture_output=True, check=True)
output = Path(sys.argv[sys.argv.index('-o') + 1])
report = json.loads(output.read_text())
bad = ['catalog/unknown/missing.go:1', 'catalog/unknown/other.go:2']
if stage == 'requirements_gather':
    report.setdefault('assistant_reply', 'Please clarify the greeting interface.')
    if repair:
        assert all(ref in data['error'] for ref in bad), data['error']
        assert 'greet.py:1' not in data['error'], data['error']
        assert 'Retain valid inspected references' in data['error'], data['error']
        report['source_refs'] = [ref for ref in report['source_refs'] if ref not in bad]
    else:
        report['source_refs'] += bad
output.write_text(json.dumps(report))
with Path({str(probe)!r}).open('a') as handle:
    handle.write(json.dumps({{'stage': stage, 'repair': repair,
                            'references': report.get('source_refs')}}) + '\\n')
print(result.stdout, end='')
""")
        provider.chmod(0o755)
        run = TaskRun.start(
            self.project,
            "Build greeting",
            command=self.entry,
            env=self.env,
            cwd=self.root,
            options=("--engine", "codex", "--joint-planning", "--spec-first", "--in-place"),
            timeout=120,
        )
        view = run.status()
        self.assertEqual("answer", view["needs"]["kind"], view)
        rows = [json.loads(line) for line in probe.read_text().splitlines()]
        self.assertEqual(
            [("requirements_gather", False), ("requirements_gather", True)],
            [(row["stage"], row["repair"]) for row in rows],
        )
        self.assertEqual(["greet.py:1"], rows[-1]["references"])
        self.assertTrue(any(a.get("rejected") for a in view["usage"]["accounting"]["attempts"]))
        before = probe.read_bytes()
        reopened = TaskRun(self.project, run.run_dir, command=self.entry, env=self.env, cwd=self.root)
        self.assertEqual(view["needs"], reopened.status()["needs"])
        self.assertEqual(before, probe.read_bytes())

"""A completed intake probe reaches its first Planner without granting task authority."""

import json
import subprocess
import sys
import unittest

from autocode_taskrun import TaskRun

from . import test_subprocess


@unittest.skipUnless(sys.platform == "linux", "supervised provider requires supported Linux")
class RequirementsObservationCLI(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp

    def test_actual_requirements_probe_reaches_only_first_planner_and_preserves_gates(self):
        self.observation_flow(repair=False)

    def test_repaired_requirements_preserve_original_probe_without_repeating_it(self):
        self.observation_flow(repair=True)

    def observation_flow(self, *, repair):
        (self.project / "greet.py").write_text(
            "import sys\nif len(sys.argv) == 2:\n    print('Hello, ' + sys.argv[1])\n"
        )
        (self.project / "test_existing_greeting.py").write_text(
            "import subprocess, sys, unittest\nfrom pathlib import Path\n"
            "class ExistingGreeting(unittest.TestCase):\n"
            "    def test_valid_name_keeps_its_existing_output(self):\n"
            "        result = subprocess.run([sys.executable, str(Path(__file__).with_name('greet.py')), 'Ada'], "
            "capture_output=True, text=True, check=True)\n"
            "        self.assertEqual(('Hello, Ada\\n', ''), (result.stdout, result.stderr))\n"
        )
        (self.project / "date_helper.py").write_text(
            "from datetime import date\ndef week(text):\n    return date.fromisoformat(text).isocalendar()[:2]\n"
        )
        subprocess.run(["git", "-C", str(self.project), "add", "."], check=True)
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
        observed = self.root / "observed-packets.jsonl"
        probe = "from date_helper import week; print('2025-01-01', week('2025-01-01'))"
        provider.write_text(f"""#!{sys.executable}
import json, subprocess, sys
from pathlib import Path
if sys.argv[1:] == ['login', 'status']:
    print('Logged in using ChatGPT (offline fixture)')
    raise SystemExit(0)
prompt = sys.stdin.read()
data = json.loads(prompt.split('CURRENT HANDOFF DATA\\n', 1)[1])
stage = data.get('stage')
if data.get('report_repair'):
    stage = data['original']['stage'] + '_report_repair'
result = subprocess.run([sys.executable, {str(base)!r}, *sys.argv[1:]],
                        input=prompt, text=True, capture_output=True, check=True)
output = Path(sys.argv[sys.argv.index('-o') + 1])
report = json.loads(output.read_text())
if {repair!r} and stage in ('requirements_gather', 'requirements_gather_report_repair'):
    report['access_blockers'] = 'invalid fixture type' if stage == 'requirements_gather' else []
    output.write_text(json.dumps(report))
with Path({str(observed)!r}).open('a') as stream:
    stream.write(json.dumps({{'stage': stage, 'observations': data.get('requirements_observations'),
                             'handoff': data.get('requirements_handoff'),
                             'report': report if stage == 'requirements_gather' else None}}) + '\\n')
lines = result.stdout.splitlines(keepends=True)
if stage == 'requirements_gather':
    command = [sys.executable, '-c', {probe!r}]
    checked = subprocess.run(command, capture_output=True, text=True, check=True)
    import shlex
    event = {{'type': 'item.completed', 'item': {{'type': 'command_execution', 'id': 'actual-date-probe',
              'command': shlex.join(command), 'exit_code': checked.returncode,
              'aggregated_output': checked.stdout + checked.stderr}}}}
    position = next(i for i, line in enumerate(lines) if json.loads(line).get('type') == 'turn.completed')
    lines.insert(position, json.dumps(event) + '\\n')
print(''.join(lines), end='')
""")
        provider.chmod(0o755)
        run = TaskRun.start(
            self.project,
            "Build the greeting tool.",
            command=self.entry,
            env=self.env,
            cwd=self.root,
            options=("--engine", "codex", "--joint-planning", "--in-place", "--max-cost-usd", "0"),
            timeout=120,
        )
        view = run.status()
        self.assertIn(view["needs"]["kind"], ("answer", "approve_plan"), view["needs"])
        rows = [json.loads(line) for line in observed.read_text().splitlines()]
        requirements = next(row for row in rows if row["stage"] == "requirements_gather")
        planner = next(row for row in rows if row["stage"] == "astra_discovery")
        for key, value in requirements["report"].items():
            if repair and key == "access_blockers":
                value = []
            self.assertEqual(value, planner["handoff"]["report"][key], key)
        repairs = [row for row in rows if row["stage"] == "requirements_gather_report_repair"]
        self.assertEqual(int(repair), len(repairs), "repair the report once, without rerunning Requirements")
        self.assertEqual(1, sum(row["stage"] == "requirements_gather" for row in rows))
        retained = planner["observations"]
        self.assertIsNotNone(retained, "first Planner must receive the completed source-bound intake probe")
        self.assertEqual("2025-01-01 (2025, 1)\n", retained["observations"][0]["output"])
        self.assertIn("untrusted", retained["instruction"])
        self.assertTrue(all(row["observations"] is None for row in rows if row["stage"] != "astra_discovery"))
        if view["needs"]["kind"] == "answer":
            for question in view["needs"]["questions"]:
                run.answer(question["id"], "CLI")
            view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], view["needs"])
        rows = [json.loads(line) for line in observed.read_text().splitlines()]
        self.assertTrue(any(row["stage"] == "astra_challenge" for row in rows), "independent plan review was skipped")
        self.assertTrue(run.show_goal())
        run.approve_plan(view["needs"]["token"])
        view = run.advance_until_input()
        self.assertEqual("review", view["needs"]["kind"], view["needs"])
        self.assertEqual(["C1"], view["needs"]["criteria"])
        for criterion in view["needs"]["criteria"]:
            run.approve_review(criterion, view["needs"]["token"])
        view = run.advance_until_input()
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        rows = [json.loads(line) for line in observed.read_text().splitlines()]
        for stage in ("sol", "astra_review"):
            self.assertTrue(any(row["stage"] == stage for row in rows), stage)
            self.assertTrue(all(row["observations"] is None for row in rows if row["stage"] == stage))

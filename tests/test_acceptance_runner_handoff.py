"""Writer-local containment is not an untestable plan; the runner qualifies supplied tests."""

import dataclasses
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from . import test_acceptance_tests as fixture

TOOLS = Path(__file__).resolve().parents[1] / "tools"
# An actual provider bootstrap is the behavior under test, not a simulated PASS. The test runs AutoCode's
# supervision as an installed tool, in its own process: importing it from the checkout's absolute path would
# load source from outside the proof copy, which the source-resolution observation rightly holds (#4152).
NESTED_TEST = r"""import sys, tempfile, subprocess, unittest
from pathlib import Path
LAUNCH = '''
import sys, subprocess
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import autocode_supervision as supervision
with supervision.launch(
    [sys.executable, '-c', "print('nested-provider-executed')"],
    receipt_path=Path(sys.argv[2]) / 'receipt.json', timeout=10,
    start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
) as child:
    out, err = child.communicate(timeout=10)
sys.stderr.write(err)
print(out.strip())
sys.exit(child.returncode)
'''
class Nested(unittest.TestCase):
    def test_runner_provider(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as root:
            done = subprocess.run(
                [sys.executable, '-c', LAUNCH, TOOLS_PATH, root], capture_output=True, text=True, timeout=30
            )
            out = done.stdout
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual('nested-provider-executed', out.strip())
            ASSERTION
if __name__ == '__main__': unittest.main()
"""

WRITER = r"""
def acceptance_tests(data):
    report = _stock_acceptance_tests(data)
    case = next(c for c in data['cases'] if c['id'] == 'NESTED')
    path = Path('tests/test_nested.py')
    path.write_text(NESTED_SOURCE)
    trial = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, timeout=20)
    evidence = Path('.autocode') / 'nested-provider-refusal.json'
    evidence.write_text(json.dumps({'exit': trial.returncode, 'stderr': trial.stderr}))
    report['cannot_write'] = [r for r in report['cannot_write'] if r['case_id'] != 'NESTED']
    report['tests_run'].append(sys.executable + ' ' + str(path))
    if "Test authoring and runner qualification are separate" not in PROMPT:
        report['cannot_write'].append({'case_id':'NESTED', 'reason':'provider-local nested listener refused'})
    else:
        report['tests'].append({'case_id':'NESTED','file':str(path),'test_name':case['test_name']})
        report['summary'] += ' Nested provider test authored; local admission refused; core runner must qualify it.'
    return report
"""


@unittest.skipUnless(sys.platform == "linux", "Real inherited Linux provider listener")
class AcceptanceRunnerHandoffTests(unittest.TestCase):
    setUp = fixture.AcceptanceTestsCliTests.setUp
    run_to_end = fixture.AcceptanceTestsCliTests.run_to_end
    stage_order = staticmethod(fixture.AcceptanceTestsCliTests.stage_order)
    details = fixture.AcceptanceTestsCliTests.details

    def run_nested(self, assertion):
        self.scenario = dataclasses.replace(
            self.scenario, fake_criteria={**fixture.CASES, "NESTED": "guard: test_runner_provider"}
        )
        original = fixture.fake_setup

        def setup(*args):
            flags, env = original(*args)
            provider = self.root / "bin" / "codex"
            source = NESTED_TEST.replace("TOOLS_PATH", repr(str(TOOLS))).replace("ASSERTION", assertion)
            text = provider.read_text().replace("def acceptance_tests(data:", "def _stock_acceptance_tests(data:")
            text = text.replace(
                "def edit_acceptance_tests_once()",
                "NESTED_SOURCE = " + repr(source) + "\n" + WRITER + "\ndef edit_acceptance_tests_once()",
            )
            provider.write_text(text)
            return flags, env

        with patch.object(fixture, "fake_setup", setup):
            return self.run_to_end("--joint-planning", "--no-adaptive-planning", "--spec-first")

    def test_runner_qualifies_a_nested_provider_test_after_writer_exit(self):
        run, project, view = self.run_nested("")
        local = json.loads((project / ".autocode/nested-provider-refusal.json").read_text())
        self.assertNotEqual(0, local["exit"])
        self.assertIn("listener descriptor transfer", local["stderr"])
        self.assertEqual("passes", view["acceptance_tests"]["results"]["NESTED"])
        order = self.stage_order(run)
        self.assertEqual(1, order.count("acceptance-tests"), order)
        self.assertLess(order.index("acceptance-tests"), order.index("builder"))
        # This fixture qualifies the writer-to-runner handoff and Builder admission;
        # its later reviewer does not cover our synthetic extra guard.

    def test_deferred_test_with_bad_assertion_reaches_the_builder_only_through_a_corrected_plan(self):
        run, _, view = self.run_nested("self.assertEqual('wrong', out.strip())")
        order = self.stage_order(run)
        before = order[: order.index("builder")] if "builder" in order else order
        # The Tester's rewrites come first. A guard still failing on the original code is a plan question: the
        # plan goes back relabelled test:, naming the failure, and needs its own approval before any Builder.
        self.assertEqual(4, before.count("acceptance-tests"), order)
        self.assertIn("plan-challenge", before[before.index("acceptance-tests") :], order)
        self.assertEqual(2, len(self.plan_approvals))
        shown = {row["id"]: row for row in self.plan_views[1]["displayed_plan"]["acceptance_criteria"]}
        self.assertEqual("test: test_runner_provider", shown["NESTED"]["verification_method"])

"""Report-only citation recovery through the real CLI and an offline provider."""
import json
import os
from pathlib import Path
import unittest

import tests.test_subprocess as subprocess_support


class ReportFindingRepairCLI(unittest.TestCase):
    def run_case(self, attack=""):
        fixture = subprocess_support.SubprocessFlow()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.env.update(AUTOCODE_FIXTURE_MODE="rework",
                           REPORT_FINDING_PROBE=str(fixture.root / "reports.jsonl"),
                           REPORT_FINDING_ATTACK=attack)
        provider = Path(fixture.env["PATH"].split(os.pathsep)[0]) / "codex"
        text = provider.read_text()
        check = '    if not os.environ.get("AUTOCODE_FIXTURE_NO_CHECK_EVENT"):\n'
        text = text.replace(check, '''    observed = subprocess.run(shlex.split(command), capture_output=True, text=True)
    if observed.returncode != (0 if passed else 1):
        raise RuntimeError('Recorded check differs from actual execution')
''' + check, 1)
        output = 'Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(result))\n'
        offset = text.rfind(output)
        self.assertGreaterEqual(offset, 0)
        text = text[:offset] + '''if stage == 'sol' and result['verdict'] == 'PASS' and result['finding_dispositions']:
    result['end_to_end_result']['evidence_refs'].append('missing-unrelated-artifact.json')
    if os.environ['REPORT_FINDING_ATTACK'] == 'blocked-original':
        result['verdict'] = 'BLOCKED'
    with Path(os.environ['REPORT_FINDING_PROBE']).open('a') as stream:
        stream.write(json.dumps({'kind': 'original', 'dispositions': result['finding_dispositions']}) + '\\n')
''' + text[offset:]
        session = '    session = str(uuid.uuid4())\n'
        self.assertIn(session, text)
        text = text.replace(session, '''    if data['original']['stage'] == 'sol':
        result['end_to_end_result']['evidence_refs'] = [
            'greet.py' if ref == 'missing-unrelated-artifact.json' else ref
            for ref in result['end_to_end_result']['evidence_refs']]
        attack = os.environ['REPORT_FINDING_ATTACK']
        if attack == 'evidence':
            result['finding_dispositions'][0]['evidence'] = 'Invented verification from the repairer'
        elif attack == 'retraction':
            result['finding_dispositions'][0]['disposition'] = 'retracted'
        elif attack == 'blocked-original':
            result['verdict'] = 'PASS'
        with Path(os.environ['REPORT_FINDING_PROBE']).open('a') as stream:
            stream.write(json.dumps({'kind': 'repair', 'error': data['error'],
                'dispositions': result['finding_dispositions']}) + '\\n')
''' + session, 1)
        provider.write_text(text)
        fixture.launch(["Build a greeting tool", "--chat"], 2 if attack else 0, answers="CLI\nyes\n")
        run, _ = fixture.saved()
        view = json.loads(fixture.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        probes = [json.loads(line) for line in (fixture.root / "reports.jsonl").read_text().splitlines()]
        return view, probes

    def test_citation_only_repair_preserves_fresh_verified_resolution(self):
        view, probes = self.run_case()
        self.assertEqual("TASK_COMPLETE", view["status"])
        self.assertEqual(0, sum(row["status"] == "open" for row in view["evidence"]["findings"]))
        original = next(row for row in probes if row["kind"] == "original")
        repairs = [row for row in probes if row["kind"] == "repair"]
        self.assertEqual(1, len(repairs))
        self.assertEqual(original["dispositions"], repairs[0]["dispositions"])
        self.assertIn("missing-unrelated-artifact.json", repairs[0]["error"])

    def test_repair_cannot_invent_verification_or_change_disposition(self):
        for attack in ("evidence", "retraction", "blocked-original"):
            with self.subTest(attack=attack):
                view, probes = self.run_case(attack)
                self.assertEqual("PAUSED_INVALID_OUTPUT", view["status"])
                self.assertGreater(sum(row["status"] == "open" for row in view["evidence"]["findings"]), 0)
                self.assertIn("report-only repair cannot close findings", view["stop_reason"])
                self.assertTrue(any("report-only repair cannot close findings" in row.get("error", "")
                                    for row in probes if row["kind"] == "repair"))

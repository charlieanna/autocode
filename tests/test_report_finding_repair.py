"""Report-only citation recovery through the real CLI and an offline provider."""
import json
import os
import unittest
from pathlib import Path

from autocode_report_findings import REPAIR_INSTRUCTION as DISPOSITION_REPAIR_RULE

import tests.test_subprocess as subprocess_support


class ReportFindingRepairCLI(unittest.TestCase):
    def run_case(self, attack="", expected=None):
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
        elif attack == 'live-churn':
            # #459 live run: the first repair "corrected" a citation inside a closure row. Told only that
            # a repair cannot close findings, the next one dropped the closures. This scripted repairer
            # keeps the row exactly when the refusal names it, as the prompt and refusal ask; one that
            # ignores both still churns as before until the run pauses (#446 owns report-repair limits).
            rows = result['finding_dispositions']
            if 'cannot close findings' not in data['error']:
                rows[0]['evidence'] = '.autocode/evidence/receipt.json'
            elif not any(row['id'] in data['error'] for row in rows):
                result['finding_dispositions'] = []
        with Path(os.environ['REPORT_FINDING_PROBE']).open('a') as stream:
            stream.write(json.dumps({'kind': 'repair', 'error': data['error'],
                'instructions': prompt.split('CURRENT HANDOFF DATA\\n', 1)[0],
                'dispositions': result['finding_dispositions']}) + '\\n')
''' + session, 1)
        provider.write_text(text)
        fixture.launch(["Build a greeting tool", "--chat"], (2 if attack else 0) if expected is None else expected,
                       answers="CLI\nyes\n")
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
        for attack, why in (("evidence", "changed its evidence"), ("retraction", "changed its disposition"),
                            ("blocked-original", "original review was blocked")):
            with self.subTest(attack=attack):
                view, probes = self.run_case(attack)
                self.assertEqual("PAUSED_INVALID_OUTPUT", view["status"])
                self.assertGreater(sum(row["status"] == "open" for row in view["evidence"]["findings"]), 0)
                self.assertIn("report-only repair cannot close findings", view["stop_reason"])
                self.assertTrue(any("report-only repair cannot close findings" in row.get("error", "")
                                    for row in probes if row["kind"] == "repair"))
                # The refusal names the row and why it cannot be kept, for the next repair and for people.
                fid = next(row for row in probes if row["kind"] == "original")["dispositions"][0]["id"]
                self.assertIn(f"sol finding_dispositions row {fid} ", view["stop_reason"])
                self.assertIn(why, view["stop_reason"])

    def test_refusal_names_the_edited_closure_row_so_a_repair_that_follows_it_keeps_the_closure(self):
        # #459 (live run 8soi9a5s): a Validator report was rejected for a citation. Its repair also
        # rewrote the evidence inside a closure row, so it was refused with a message that named no row;
        # the next repair dropped the closures, the finding stayed open, and the whole validation and
        # repair cycle repeated until completion was refused. This checks that the refusal and prompt
        # give a repair what it needs to keep the closure; it cannot show that a live model follows them.
        view, probes = self.run_case("live-churn", expected=0)
        self.assertEqual("TASK_COMPLETE", view["status"])
        self.assertEqual(0, sum(row["status"] == "open" for row in view["evidence"]["findings"]))
        originals = [row for row in probes if row["kind"] == "original"]
        repairs = [row for row in probes if row["kind"] == "repair"]
        self.assertEqual(1, len(originals), "the closure was lost and the Validator ran again")
        self.assertEqual(2, len(repairs))
        fid = originals[0]["dispositions"][0]["id"]
        refusal = repairs[1]["error"]
        self.assertIn("report-only repair cannot close findings", refusal)
        self.assertIn(f"sol finding_dispositions row {fid} changed its evidence", refusal)
        # The fresh review's exact row closed the finding; the repair neither created nor changed it.
        self.assertEqual(originals[0]["dispositions"], repairs[1]["dispositions"])
        # Every repair is told to copy closure rows byte-for-byte, evidence included.
        for repair in repairs:
            self.assertIn(DISPOSITION_REPAIR_RULE, repair["instructions"])
            self.assertIn("evidence citations outside finding_dispositions", repair["instructions"])

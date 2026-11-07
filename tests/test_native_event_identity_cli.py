"""Public report repair must preserve completed native command identity (#2212)."""

import textwrap
import unittest

from . import test_report_repair as fixtures

INJECT = textwrap.dedent("""
    original_emit = emit

    def emit(kind, **part):
        if kind == 'tool_use' and stage == 'sol' and not repairing and part.get('id') == 'synthetic-original-sol':
            earlier = {**part, 'state': {**part['state'], 'metadata': {}}}
            if args.fault == 'conflicting_command':
                earlier['state']['input'] = {'command': 'python3 conflicting-observation.py'}
                original_emit(kind, **earlier)
            elif args.fault == 'same_command_refinement':
                original_emit(kind, **earlier)
            elif args.fault == 'sessionless_noise':
                earlier['state']['input'] = {'command': 'python3 unrelated-observation.py'}
                print(json.dumps({'type': kind, 'part': {
                    'sessionID': session, 'messageID': 'synthetic-message', **earlier,
                }}), flush=True)
        original_emit(kind, **part)

""")


class NativeEventIdentityCLI(unittest.TestCase):
    setUp = fixtures.ExactEventRepairPublicCliTests.setUp
    start_receipt_fixture = fixtures.ExactEventRepairPublicCliTests.start_receipt_fixture
    observations = fixtures.ExactEventRepairPublicCliTests.observations
    PROVIDER_OUTPUT = "opencode_events"
    PROVIDER_SOURCE = fixtures.ExactEventRepairPublicCliTests.PROVIDER_SOURCE.replace(
        "def execute(event_id):", INJECT + "def execute(event_id):", 1
    )

    def exercise(self, fault):
        run = self.start_receipt_fixture(fault)
        view = run.advance_until_input()
        rows = self.observations()
        providers = [row for row in rows if row["kind"] == "provider"]
        repairs = [row for row in rows if row["kind"] == "repair" and row["stage"] == "sol"]
        self.assertEqual(1, sum(row["stage"] == "terra" and not row["repairing"] for row in providers))
        self.assertEqual(1, sum(row["stage"] == "sol" and not row["repairing"] for row in providers))
        self.assertFalse(
            any(row["repairing"] for row in rows if row["kind"] == "execution" and row["stage"] == "sol"),
            "Tester report-only repair must not execute another check",
        )
        return view, providers, repairs

    def assert_completed(self, fault):
        view, providers, repairs = self.exercise(fault)
        self.assertEqual("TASK_COMPLETE", view["status"], view.get("stop_reason"))
        self.assertEqual("PASS", view["evidence"]["check_replay"]["verdict"])
        self.assertEqual(1, len(repairs))
        self.assertEqual(1, sum(row["stage"] == "astra_review" and not row["repairing"] for row in providers))

    def test_completed_identity_conflict_cannot_be_repaired_into_a_pass(self):
        view, providers, _ = self.exercise("conflicting_command")
        self.assertFalse(view["done"])
        self.assertFalse(any(row["stage"] == "astra_review" and not row["repairing"] for row in providers))

    def test_ignored_sessionless_noise_does_not_force_further_repairs(self):
        self.assert_completed("sessionless_noise")

    def test_same_command_metadata_refinement_remains_one_operation(self):
        self.assert_completed("same_command_refinement")

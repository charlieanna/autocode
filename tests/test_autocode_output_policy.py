from argparse import Namespace
from pathlib import Path
import unittest
from autocode_output_policy import configure, mode, environment, view


class PolicyTests(unittest.TestCase):
    def test_default_and_legacy_resume_keep_their_policy(self):
        self.assertEqual(mode(configure({}, {}, Namespace())), 'conservative')
        self.assertEqual(mode(configure({'settings': {'engine': 'opencode'}}, {}, Namespace())), 'raw')

    def test_live_or_unreconciled_attempt_cannot_change_mode(self):
        args = Namespace(tool_output_mode='conservative', resume_paused=True)
        for state in ({'status': 'RUNNING'}, {'status': 'PAUSED_X', 'active_stage': {'stage': 'sol'}},
                      {'status': 'PAUSED_X', 'pending_report_repair': {'x': 1}}):
            with self.subTest(state=state), self.assertRaises(ValueError):
                configure({'settings': {'engine': 'opencode'}, **state}, {}, args)
        saved = configure({'settings': {'engine': 'opencode'}, 'status': 'PAUSED_X'}, {}, args)
        self.assertEqual(mode(saved), 'conservative')

    def test_runtime_environment_and_status_do_not_invent_cost_savings(self):
        env = environment({'output_transport': {'mode': 'raw'}}, '/workspace', 'attempt1')
        self.assertEqual(env['AUTOCODE_OUTPUT_ATTEMPT'], 'attempt1')
        self.assertEqual(env['AUTOCODE_OUTPUT_MODE'], 'raw')
        self.assertEqual(env['AUTOCODE_OUTPUT_WORKSPACE'], str(Path('/workspace').resolve()))
        self.assertEqual(env['AUTOCODE_OUTPUT_STORE'], str(Path('/workspace').resolve() / '.autocode/output'))
        result = view({'stages': [{}]})
        self.assertEqual(result['unavailable_stages'], 1)
        self.assertIsNone(result['cost_savings_usd'])

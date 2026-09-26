"""Nonsecret connection-summary checks; no network or credential-file reads."""
# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

_ROOT = _Path(__file__).resolve().parents[1] if _Path(__file__).name != 'live_trial.py' else _Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / 'tools', _ROOT / 'tests', _ROOT / 'tests' / 'fakes'):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import autocode_opencode as oc


class OpenCodeSubscriptionTests(unittest.TestCase):
    roles = {'terra': {'model': 'openai/gpt-5.6-terra'}}

    def test_oauth_summary_is_accepted_without_reading_credentials(self):
        response = SimpleNamespace(returncode=0, stdout='● Z.AI Coding Plan api\n● OpenAI \x1b[90moauth\n', stderr='')
        with patch.object(oc.subprocess, 'run', return_value=response) as run, \
             patch.object(Path, 'read_text', side_effect=AssertionError('Do not read auth files')):
            oc.check_subscription_routes(self.roles, Path('/workspace'))
        self.assertEqual(['opencode', 'auth', 'list'], run.call_args.args[0])
        self.assertEqual(15, run.call_args.kwargs['timeout'])

    def test_api_missing_ambiguous_and_unrecognized_auth_cannot_launch(self):
        for summary in ('● OpenAI api', '● OpenAI unknown', '', 'OAuth exists somewhere',
                        '● Other OpenAI oauth', '● OpenAI oauth\n● OpenAI api'):
            with self.subTest(summary=summary), patch.object(oc.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout=summary, stderr='')), self.assertRaisesRegex(RuntimeError, 'API-key fallback is disabled'):
                oc.check_subscription_routes(self.roles)
        with patch.object(oc.subprocess, 'run', return_value=SimpleNamespace(
                returncode=1, stdout='● OpenAI oauth', stderr='')), self.assertRaises(RuntimeError):
            oc.check_subscription_routes(self.roles)

    def test_timeout_and_missing_cli_do_not_fallback_or_expose_output(self):
        for error in (subprocess.TimeoutExpired(['opencode'], 15), FileNotFoundError('missing')):
            with patch.object(oc.subprocess, 'run', side_effect=error), self.assertRaisesRegex(RuntimeError, 'no provider request'):
                oc.check_subscription_routes(self.roles)

    def test_existing_zai_routes_do_not_gain_an_auth_probe(self):
        with patch.object(oc.subprocess, 'run', side_effect=AssertionError('Unrelated routes remain unchanged')):
            oc.check_subscription_routes({'terra': {'model':'zai-coding-plan/glm-5.3'}})

    def test_api_environment_override_blocks_all_role_names(self):
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL'):
            for role in ('glm','astra','terra','sol'):
                with patch.dict(oc.os.environ, {key:'fixture-only'}), \
                     patch.object(oc.subprocess, 'run') as command:
                    with self.assertRaisesRegex(RuntimeError, 'will not silently change billing'):
                        oc.check_subscription_routes({role:{'model':'openai/gpt-5.6-sol'}})
                    command.assert_not_called()


if __name__ == '__main__':
    unittest.main()

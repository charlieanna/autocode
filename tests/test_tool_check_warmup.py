"""A run asks its tool's model-free checks at once, then the checks that follow are kept answers.

On 2026-11-15 a "hi" in the VS Code chat waited 77 s for a 2.8 s model answer: each Kilo start cost
5-9 s on macOS, and `kilo --version`, `kilo auth list` and `kilo models` ran one after another before
the stage, then `kilo debug config` and `kilo debug agent` before every stage of every message.
"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_run_setup as run_setup
from providers import env_prep, kilocode, kilocode_policy

# With TOGETHER set, each call waits (up to 10 s) until that many calls have started, then records
# whether they all did: checks asked one after another never meet.
KILO = """#!/bin/sh
echo "$*" >> "{log}"
if [ -n "$TOGETHER" ]; then
  touch "{log}.started.$$"
  n=0
  while [ "$(ls "{log}".started.* | wc -l)" -lt "$TOGETHER" ] && [ $n -lt 100 ]; do sleep 0.1; n=$((n+1)); done
  [ $n -lt 100 ] && echo together >> "{log}.met" || echo alone >> "{log}.met"
fi
case "$*" in
  --version) echo 7.8.3 ;;
  "auth list") echo "● OpenAI oauth" ;;
  "models") echo openai/gpt-5.6-sol ;;
  "models --verbose") printf 'openai/gpt-5.6-sol\\n{{"id": "gpt-5.6-sol", "variants": {{"high": {{}}}}}}\\n' ;;
  "debug config") echo '{{"default_agent": "code"}}' ;;
  "debug agent code") echo '{{"permission": [{{"permission": "read", "pattern": "*", "action": "allow"}}]}}' ;;
  *) exit 2 ;;
esac
"""


class WarmupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.bin, self.home = root / "bin", root / "home"
        self.bin.mkdir()
        self.home.mkdir()
        self.log = root / "calls"
        kilo = self.bin / "kilo"
        kilo.write_text(KILO.format(log=self.log))
        kilo.chmod(0o755)
        self.workspace = root / "project"
        self.workspace.mkdir()
        self.run_dir = self.workspace / ".autocode" / "runs" / "r1"
        self.run_dir.mkdir(parents=True)
        environment = {"PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin", "HOME": str(self.home)}
        patcher = mock.patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.provider = kilocode.load("kilocode")

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def setup_checks(self):
        """The checks a run asks in turn before its first stage, as the runner asks them."""
        self.provider.local_settings(self.workspace)
        self.provider.check_subscription_routes({"tester": {"model": "openai/gpt-5.6-sol"}}, self.workspace)
        self.provider.check_models({"tester": {"model": "openai/gpt-5.6-sol"}}, self.workspace)
        self.provider._variant_listing(self.workspace, None)

    def test_the_checks_run_at_once_and_the_setup_after_them_starts_no_tool(self):
        os.environ["TOGETHER"] = "4"
        with env_prep.reuse_checks(self.run_dir):
            self.assertEqual(4, self.provider.warm_checks(self.workspace))
            self.assertEqual(
                {"--version", "auth list", "models", "models --verbose"}, set(self.calls()), "each check once"
            )
            met = Path(f"{self.log}.met").read_text().split()
            self.assertEqual(["together"] * 4, met, "the four checks ran at the same time")
            self.setup_checks()
        self.assertEqual(4, len(self.calls()), "every check was answered from the warmed answer")

    def test_outside_a_run_nothing_is_warmed(self):
        self.assertEqual(0, self.provider.warm_checks(self.workspace))
        self.assertEqual([], self.calls())

    def test_a_failed_warm_check_is_asked_again_by_the_check_itself(self):
        (self.bin / "kilo").write_text(KILO.format(log=self.log).replace('"models") echo', '"models") exit 1; echo'))
        with env_prep.reuse_checks(self.run_dir):
            self.provider.warm_checks(self.workspace)
            with self.assertRaisesRegex(RuntimeError, "Cannot list kilocode models"):
                self.provider.check_models({"tester": {"model": "openai/gpt-5.6-sol"}}, self.workspace)
        self.assertEqual(2, self.calls().count("models"), "a failure is never kept or decided by the warm-up")

    def test_the_runner_warms_its_tool_unless_the_run_is_on_codex(self):
        warm = mock.Mock()
        runner = SimpleNamespace(opencode=SimpleNamespace(warm_checks=warm))
        run_setup.warm_tool_checks(runner, SimpleNamespace(engine=None), {}, self.workspace)
        warm.assert_called_once_with(self.workspace)
        run_setup.warm_tool_checks(runner, SimpleNamespace(engine="codex"), {}, self.workspace)
        run_setup.warm_tool_checks(
            runner, SimpleNamespace(engine=None), {"settings": {"engine": "codex"}}, self.workspace
        )
        run_setup.warm_tool_checks(SimpleNamespace(opencode=object()), SimpleNamespace(engine=None), {}, self.workspace)
        self.assertEqual(1, warm.call_count)


class PolicyReuseTests(WarmupTests):
    """Kilo's debug answers, read before every stage, kept while nothing they depend on changed."""

    # The warm-up tests ran above; these share only the fixture.
    test_the_checks_run_at_once_and_the_setup_after_them_starts_no_tool = None
    test_outside_a_run_nothing_is_warmed = None
    test_a_failed_warm_check_is_asked_again_by_the_check_itself = None
    test_the_runner_warms_its_tool_unless_the_run_is_on_codex = None

    def policy(self):
        child = dict(os.environ)
        return kilocode_policy.effective_agent(["kilo", "run"], child, self.workspace)[1]

    def debug_calls(self):
        return [call for call in self.calls() if call.startswith("debug")]

    def test_the_next_message_reuses_kilos_permission_answers(self):
        with env_prep.reuse_checks(self.run_dir):
            first = self.policy()
        with env_prep.reuse_checks(self.run_dir):  # the next message: a new controller process
            second = self.policy()
        self.assertEqual(first, second)
        self.assertEqual(["debug agent code", "debug config"], sorted(self.debug_calls()))

    def test_a_changed_permission_or_agent_is_asked_again(self):
        config = self.home / ".config" / "kilo"
        for change in (
            lambda: (config / "kilo.json").write_text("{}"),  # Kilo's own settings
            lambda: (config / "agent").mkdir() or (config / "agent" / "code.md").write_text("tools"),  # an agent
            lambda: (self.workspace / ".kilo").mkdir() or (self.workspace / ".kilo" / "kilo.json").write_text("{}"),
            lambda: (self.workspace / "kilo.json").write_text("{}"),  # the project's settings
        ):
            config.mkdir(parents=True, exist_ok=True)
            with env_prep.reuse_checks(self.run_dir):
                self.policy()
            before = len(self.debug_calls())
            change()
            with env_prep.reuse_checks(self.run_dir):
                self.policy()
            self.assertEqual(before + 2, len(self.debug_calls()), "both answers were asked again after the change")

    def test_an_answer_that_is_not_a_policy_is_never_kept(self):
        (self.bin / "kilo").write_text(
            KILO.format(log=self.log).replace("""echo '{"default_agent": "code"}'""", "echo not-json")
        )
        for _ in range(2):
            with env_prep.reuse_checks(self.run_dir), self.assertRaises(ValueError):
                self.policy()
        self.assertEqual(2, self.debug_calls().count("debug config"))

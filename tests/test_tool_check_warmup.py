"""A run asks its tool's model-free checks at once, then the checks that follow are kept answers.

On 2026-11-15 a "hi" in the VS Code chat waited 77 s for a 2.8 s model answer: each Kilo start cost
5-9 s on macOS, and `kilo --version`, `kilo auth list` and `kilo models` ran one after another before
the stage, then `kilo debug config` and `kilo debug agent` before every stage of every message.
"""

import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest import mock

from providers import command, env_prep, kilocode, kilocode_policy

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
  --version) echo {version} ;;
  "auth list") echo "● OpenAI oauth" ;;
  "models") echo openai/gpt-5.6-sol ;;
  "models --verbose") printf 'openai/gpt-5.6-sol\\n{{"id": "gpt-5.6-sol", "variants": {{"high": {{}}}}}}\\n' ;;
  "debug config") echo '{{"default_agent": "code"}}' ;;
  "debug agent code") echo '{{"permission": [{{"permission": "read", "pattern": "*", "action": "allow"}}]}}' ;;
  *) exit 2 ;;
esac
"""


class KiloFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.bin, self.home = root / "bin", root / "home"
        self.bin.mkdir()
        self.home.mkdir()
        self.log = root / "calls"
        kilo = self.bin / "kilo"
        self.script = KILO.format(log=self.log, version=kilocode.containment.kilo_versions()[0])
        kilo.write_text(self.script)
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


class WarmupTests(KiloFixture):
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
        (self.bin / "kilo").write_text(self.script.replace('"models") echo', '"models") exit 1; echo'))
        with env_prep.reuse_checks(self.run_dir):
            self.provider.warm_checks(self.workspace)
            with self.assertRaisesRegex(RuntimeError, "Cannot list kilocode models"):
                self.provider.check_models({"tester": {"model": "openai/gpt-5.6-sol"}}, self.workspace)
        self.assertEqual(2, self.calls().count("models"), "a failure is never kept or decided by the warm-up")

    def test_the_first_check_a_run_asks_warms_the_rest_once(self):
        os.environ["TOGETHER"] = "4"
        with env_prep.reuse_checks(self.run_dir):
            self.provider.local_settings(self.workspace)
            self.assertEqual(4, len(self.calls()), "the version check asked all four at once")
            self.setup_checks()
            self.assertEqual(0, self.provider.warm_checks(self.workspace), "once per run")
        self.assertEqual(4, len(self.calls()))
        self.assertEqual(["together"] * 4, Path(f"{self.log}.met").read_text().split())


class PolicyReuseTests(KiloFixture):
    """Kilo's debug answers, read before every stage, kept while nothing they depend on changed."""

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
        (self.bin / "kilo").write_text(self.script.replace("""echo '{"default_agent": "code"}'""", "echo not-json"))
        for _ in range(2):
            with env_prep.reuse_checks(self.run_dir), self.assertRaises(ValueError):
                self.policy()
        self.assertEqual(2, self.debug_calls().count("debug config"))


@unittest.skipUnless(os.name == "posix", "Linux metadata filesystem and locks")
class WarmupCustodyTests(unittest.TestCase):
    @contextmanager
    def batch(self, *, extended=True, uncertain=None):
        custody = env_prep.captured.custody
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "run").mkdir()
            tool = root / "kilo"
            tool.write_text("# The metadata boundary is replaced; this tool is never executed.\n")
            tool.chmod(0o700)
            environment = {"HOME": str(root), "XDG_STATE_HOME": str(root / "state"), "PATH": str(root)}
            provider = kilocode.load("kilocode") if extended else command.load("kilocode")
            calls = [(words, options) for words, options in provider._warm_calls(root, environment) if words]
            together = threading.Barrier(len(calls), timeout=0.5)
            records, lock = [], threading.Lock()

            def metadata(words, *, env, timeout, **kwargs):
                with custody.admission(env=env, timeout=timeout) as scope:
                    stream, nonce = scope.arm()
                    kind = tuple(words[1:])
                    with lock:
                        records.append((kind, scope.group.nonce, nonce, stream.fileno()))
                    try:
                        together.wait()
                    finally:
                        if kind != uncertain:
                            stream.write(
                                json.dumps(
                                    {
                                        "schema": 1,
                                        "nonce": nonce,
                                        "clean": True,
                                        "started": True,
                                        "returncode": 0,
                                        "expired": False,
                                    }
                                ).encode()
                            )
                            stream.flush()
                            os.fsync(stream.fileno())
                    output = {
                        ("--version",): "7.8.3\n",
                        ("auth", "list"): "● OpenAI oauth\n",
                        ("models",): "openai/gpt-5.6-sol\n",
                        ("models", "--verbose"): 'openai/gpt-5.6-sol\n{"variants":{"high":{}}}\n',
                    }[kind]
                    return env_prep.subprocess.CompletedProcess(words, 0, output, "")

            with (
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch.object(custody.sys, "platform", "linux"),
                mock.patch.object(custody, "_boot_id", return_value="00000000-0000-4000-8000-000000000001"),
                mock.patch.object(env_prep.captured, "run_metadata", side_effect=metadata) as native,
                mock.patch.object(
                    env_prep.captured.subprocess, "Popen", side_effect=AssertionError("native forbidden")
                ),
                env_prep.reuse_checks(root / "run"),
            ):
                yield provider, root, environment, calls, records, native

    def test_warm_workers_share_custody_and_each_kept_answer_is_reused(self):
        custody = env_prep.captured.custody
        for extended in (False, True):
            with self.subTest(extended=extended), self.batch(extended=extended) as data:
                provider, root, environment, calls, records, native = data
                with mock.patch.object(custody, "group", wraps=custody.group) as groups:
                    self.assertEqual(len(calls), provider.warm_checks(root, env=environment))
                self.assertEqual(1, len({row[1] for row in records}), "workers share one explicit authority")
                self.assertEqual(len(calls), len({row[2] for row in records}), "each command owns its receipt")
                self.assertEqual(len(calls), len({row[3] for row in records}), "all receipts overlap")
                groups.assert_called_once_with(
                    timeout=max(options["timeout"] for _, options in calls) + env_prep.captured.HELPER_RESERVE
                )
                for words, options in calls:
                    self.assertIsInstance(env_prep.preflight_run(words, environment, **options), env_prep.KeptCheck)
                self.assertEqual(len(calls), native.call_count)
                store = root / "state/autocode/metadata"
                self.assertFalse((store / "pending.json").exists())
                kept = list(env_prep.reuse_folder(root / "run").glob("*.json"))
                self.assertEqual(
                    [env_prep.OWNED_CHECK_SCHEMA] * len(calls),
                    [json.loads(p.read_text())["ownership_schema"] for p in kept],
                )

    def test_uncertain_warm_member_propagates_after_siblings_settle_and_blocks_cached_reads(self):
        custody = env_prep.captured.custody
        with self.batch(uncertain=("models",)) as data:
            provider, root, environment, calls, records, native = data
            with self.assertRaises(custody.ProcessError):
                provider.warm_checks(root, env=environment)
            store = root / "state/autocode/metadata"
            pending = json.loads((store / "pending.json").read_text())
            self.assertEqual(len(calls), len(pending["attempts"]))
            self.assertEqual(
                len(calls) - 1, sum(bool((store / row["result_file"]).stat().st_size) for row in pending["attempts"])
            )
            self.assertEqual(custody.BLOCKED, custody.status())
            words, options = calls[0]
            with self.assertRaises(custody.ProcessError):
                env_prep.preflight_run(words, environment, **options)
            self.assertEqual(len(records), native.call_count, "cached success cannot bypass uncertain custody")


class VersionTimeoutTests(KiloFixture):
    def test_a_failed_version_command_is_not_a_timeout(self):
        result = env_prep.subprocess.CompletedProcess(["kilo", "--version"], 1, "", "failed")
        with mock.patch.object(env_prep.captured, "run_metadata", return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError, "version command failed; no agent was launched"):
                self.provider.local_settings(self.workspace)
        run.assert_called_once()

    def test_slow_version_retries_once_cold_or_warmed_then_validates_the_answer(self):
        good = kilocode.containment.kilo_versions()[0]
        for warmed in (False, True):
            for answer in (good, "0.0.0", None):
                bounds = []

                def run(words, *, timeout, **kwargs):
                    if words == ["kilo", "--version"]:
                        bounds.append(timeout)
                        if timeout == 15 or answer is None:
                            raise env_prep.subprocess.TimeoutExpired(words, timeout)
                        return env_prep.subprocess.CompletedProcess(words, 0, answer, "")
                    return env_prep.subprocess.CompletedProcess(words, 1, "", "")

                run_dir = self.run_dir / str(answer)
                run_dir.mkdir(exist_ok=True)
                with (
                    self.subTest(warmed=warmed, answer=answer),
                    env_prep.reuse_checks(run_dir) if warmed else nullcontext(),
                    mock.patch.object(env_prep.captured, "run_metadata", side_effect=run),
                ):
                    if answer == good:
                        self.assertEqual(good, self.provider.local_settings(self.workspace)["version"])
                    elif answer is None:
                        with self.assertRaisesRegex(
                            RuntimeError, "timed out after 15 seconds and again after 30 seconds"
                        ):
                            self.provider.local_settings(self.workspace)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "requires Kilo.*found 0.0.0"):
                            self.provider.local_settings(self.workspace)
                    self.assertEqual([15, 30], bounds)


class WarmTimeoutTests(unittest.TestCase):
    """A warm-up that overran its bound was the listing's first attempt; the listing asks once more."""

    def test_the_listing_takes_only_its_second_attempt_after_a_warm_timeout(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        run_dir = Path(temp.name) / "run"
        bounds = []

        def run(command, **kwargs):
            bounds.append(kwargs["timeout"])
            if len(bounds) == 1:
                raise env_prep.subprocess.TimeoutExpired(command, kwargs["timeout"])
            return env_prep.subprocess.CompletedProcess(command, 0, "listed\n", "")

        environment = {"PATH": "/usr/bin:/bin"}
        options = {"cwd": temp.name, "capture_output": True, "text": True}
        with env_prep.reuse_checks(run_dir), mock.patch.object(env_prep.captured, "run_metadata", side_effect=run):
            with self.assertRaises(env_prep.subprocess.TimeoutExpired):
                env_prep.preflight_run(["sh", "-c", "true"], environment, warming=True, timeout=30, **options)
            result = env_prep.preflight_listing(["sh", "-c", "true"], environment, timeout=30, **options)
        self.assertEqual("listed\n", result.stdout)
        self.assertEqual([30, 60], bounds, "the bounds the listing names, once each")

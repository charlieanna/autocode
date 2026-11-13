"""A standalone unrelated limit save is not an action on an investigated failure."""

import copy
import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import autocode_action_authority as authority
import autocode_args as arguments
import autocode_resolver_human as human
from autocode_taskrun import TaskRun

from . import test_subprocess as fixture
from .test_repeated_tester_timeout_cli import TIMEOUT


class ResumeAuthorityReached(Exception):
    pass


class StandaloneLimitActionTests(unittest.TestCase):
    def test_actions_and_existing_requests_reach_the_ordinary_authority_checks(self):
        for flags, state in (
            (["--delegate-all"], {}),
            (["--reject-assumption", "A1"], {}),
            (["--migrate-only"], {}),
            (["--resolver-response", "provide_information"], {}),
            (["--resume-paused"], {}),
            (["New task text"], {}),
            ([], {human.PUBLIC: {"request_id": "existing"}}),
            ([], {human.PRIVATE: {"scope": "operational_exhaustion"}}),
            (["--max-seconds", "900"], {"status": "PAUSED_TIME_LIMIT"}),
        ):
            with self.subTest(flags=flags, state=state):
                parser = arguments.build_parser(None, arguments.DEFAULT_ROLE_MODELS)
                args = parser.parse_args(["--run-dir", "/run", "--max-stage-seconds", "900", *flags])
                args._explicit_budget_flags = {"max_stage_seconds"}
                if args.max_seconds is not None:
                    args._explicit_budget_flags.add("max_seconds")
                candidate = {"status": "PAUSED_REPEATED_FAILURE", **state}
                runner = SimpleNamespace(cli_args=arguments, now=lambda: "t")
                # The existing authority step owns these inputs. Stop at that boundary instead of
                # manufacturing a request or letting a provider run in a classification control.
                with (
                    patch.object(human, "permission_hold", side_effect=ResumeAuthorityReached),
                    self.assertRaises(ResumeAuthorityReached),
                ):
                    authority.settle(runner, args, parser, candidate, None, None, None, SimpleNamespace())


@unittest.skipUnless(sys.platform == "linux", "supervised fake-provider regression requires supported Linux")
class StandaloneLimitPauseCliTests(unittest.TestCase):
    setUp = fixture.SubprocessFlow.setUp

    def stopped_tester(self):
        provider = self.root / "fixture-bin" / "codex"
        source = provider.read_text()
        marker = '\n_quota_model = os.environ.get("AUTOCODE_FIXTURE_QUOTA_MODEL")'
        self.assertEqual(1, source.count(marker))
        provider.write_text(source.replace(marker, '\nif stage == "tester":\n    raise SystemExit(0)\n' + marker))
        wrapper = self.root / "tester-timeout.py"
        wrapper.write_text(TIMEOUT.replace("TOOLS", repr(str(fixture.TOOLS))))
        self.entry = [sys.executable, str(wrapper)]
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        run = TaskRun.start(
            self.project,
            "Build greeting",
            command=self.entry,
            options=("--engine", "codex", "--max-stage-seconds", "600", "--max-cost-usd", "0"),
            env=self.env,
            cwd=self.root,
            timeout=180,
        )
        run.answer("Q1", "CLI")
        view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], view["stop_reason"])
        run.approve_plan(view["needs"]["token"])
        run.advance_until_input()
        return run

    def cli(self, run, *args, expected=0):
        result = subprocess.run(
            [*self.entry, *args, "--workspace", str(run.workspace), "--run-dir", str(run.run_dir)],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return result.stdout

    def status(self, run):
        return json.loads(self.cli(run, "--status"))

    def events(self, run):
        return [json.loads(line) for line in self.cli(run, "events").splitlines()]

    def test_standalone_limit_change_and_restatement_preserve_the_investigated_pause(self):
        run = self.stopped_tester()
        before = self.status(run)
        view = before["view"]
        self.assertEqual(("PAUSED_REPEATED_FAILURE", "tester"), (view["status"], view["next_stage"]))
        self.assertFalse(view["usage"]["accounting"]["active_attempts"])
        self.assertEqual("resume", view["needs"]["kind"])
        stages = [row["stage"] for row in view["usage"]["accounting"]["attempts"] if not row["runner_owned"]]
        self.assertEqual(2, stages.count("tester"), stages)
        self.assertEqual(1, stages.count("investigate_stuck"), stages)
        self.assertTrue(view["approved_contract"])
        events = self.events(run)
        implementation = (self.project / "greet.py").read_bytes()
        settings = copy.deepcopy(before["settings"])
        settings["limits"]["stage_timeout_seconds"] = 900
        settings["budget_origins"]["stage_timeout_seconds"] = "user_explicit"

        for _ in range(2):  # Reasserting the same saved value is still no resume or retry.
            self.cli(run, "--max-stage-seconds", "900", "--no-chat", expected=2)
            after = self.status(run)
            self.assertEqual(settings, after["settings"])
            for field in ("status", "next_stage", "phase", "contract_token", "current_task", "last_decision"):
                self.assertEqual(before[field], after[field], field)
            for field in ("needs", "stop_reason", "usage", "cost", "routes", "approvals", "approved_contract"):
                self.assertEqual(view[field], after["view"][field], field)
            self.assertEqual(before["pending_questions"], after["pending_questions"])
            self.assertEqual(implementation, (self.project / "greet.py").read_bytes())

        after_events = self.events(run)
        self.assertEqual(events, after_events[:-1])
        changed = after_events[-1]
        self.assertEqual(
            ("limit_change", "stage_timeout_seconds", 600, 900, "--max-stage-seconds", "tester"),
            tuple(changed[field] for field in ("kind", "limit", "previous", "value", "flag", "stage")),
        )

        # A named decision/read action beside the limit must still run, rather than being swallowed
        # as a standalone metadata write. Showing the approved plan grants no execution authority.
        shown = self.cli(run, "--max-stage-seconds", "1200", "--show-goal", "--no-chat")
        self.assertIn("greet.py", shown)
        after_show = self.status(run)
        self.assertEqual(1200, after_show["settings"]["limits"]["stage_timeout_seconds"])
        self.assertEqual(view["status"], after_show["view"]["status"])
        self.assertEqual(view["usage"], after_show["view"]["usage"])


if __name__ == "__main__":
    unittest.main()

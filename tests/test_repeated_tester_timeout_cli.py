"""A repeated Tester timeout reaches diagnosis before another identical verification launch."""

import sys
import unittest

from . import test_subprocess as fixture

# The configured fake exits without a report. Advance only that attempt's actual monitor clock;
# no model traffic, sleeping provider, shortened production deadline or private checkpoint edits.
TIMEOUT = """\
import sys
sys.path.insert(0, TOOLS)
import runpy
import autocode_process as processes

wait = processes.wait_for_stage
def deadline(child, seconds, checkpoint, *, activity=None, **kwargs):
    if activity is None or not activity.path.name.startswith("validator-"):
        return wait(child, seconds, checkpoint, activity=activity, **kwargs)
    code, timed_out = wait(child, seconds, checkpoint, activity=activity, **kwargs)
    assert not timed_out
    assert child.poll() is not None
    later = activity.clock() + activity.idle_limit + 1
    activity.clock = lambda: later
    activity.timeout = activity.expired()
    assert activity.timeout is not None
    return code, True

processes.wait_for_stage = deadline
sys.argv[0] = TOOLS + "/autocode.py"
runpy.run_path(sys.argv[0], run_name="__main__")
"""


class RepeatedTesterTimeoutCliTests(unittest.TestCase):
    setUp = fixture.SubprocessFlow.setUp
    new_run_engine_args = ("--engine", "codex")

    def test_two_timeouts_are_diagnosed_and_reopening_spends_no_more_attempts(self):
        provider = self.root / "fixture-bin" / "codex"
        source = provider.read_text()
        marker = '\n_quota_model = os.environ.get("AUTOCODE_FIXTURE_QUOTA_MODEL")'
        self.assertEqual(1, source.count(marker))
        provider.write_text(source.replace(marker, '\nif stage == "tester":\n    raise SystemExit(0)\n' + marker))
        wrapper = self.root / "tester-timeout.py"
        wrapper.write_text(TIMEOUT.replace("TOOLS", repr(str(fixture.TOOLS))))
        self.entry = [sys.executable, str(wrapper)]
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        from autocode_taskrun import TaskRun

        run = TaskRun.start(
            self.project,
            "Build greeting",
            command=self.entry,
            options=("--engine", "codex", "--max-cost-usd", "0"),
            env=self.env,
            cwd=self.root,
            timeout=180,
        )
        run.answer("Q1", "CLI")
        view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], view["stop_reason"])
        view = run.approve_plan(view["needs"]["token"])
        view = run.advance_until_input()
        attempts = view["usage"]["accounting"]["attempts"]
        models = [row for row in attempts if not row["runner_owned"]]
        stages = [row["stage"] for row in models]
        self.assertEqual(1, stages.count("builder"), stages)
        self.assertEqual(2, stages.count("tester"), stages)
        self.assertEqual(1, stages.count("investigate_stuck"), stages)
        self.assertEqual(["tester", "investigate_stuck"], stages[-2:])
        self.assertTrue(all(row["timed_out"] for row in models if row["stage"] == "tester"))
        self.assertFalse(view["done"])
        self.assertIn("Offline fixture: it cannot diagnose", view["stop_reason"])
        retained = (self.project / "greet.py").read_bytes()

        reopened = run.advance()
        self.assertEqual(models, [r for r in reopened["usage"]["accounting"]["attempts"] if not r["runner_owned"]])
        self.assertEqual(view["routes"], reopened["routes"])
        for key in ("spent_usd", "estimated_usd", "reported_usd", "cap_usd"):
            self.assertEqual(view["cost"][key], reopened["cost"][key], key)
        self.assertEqual(retained, (self.project / "greet.py").read_bytes())
        self.assertFalse(reopened["done"])


if __name__ == "__main__":
    unittest.main()

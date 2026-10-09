"""Every recovery action a stop advertises must be accepted there, and one
documented action must move the run again (#288/#301).

Real CLI processes with an explicitly fake provider; no sleeps, no live models.
Checkpoints mirror the pause classes reported live: exhausted automatic
timeout recovery, an explicit user time cap, and exhausted AutoResolver
operational-recovery attempts.
"""

import re
import subprocess
import unittest

import autocode as runner
import autocode_resolver_human as human
import autocode_support as support

from . import test_subprocess

ADVERTISED_FLAGS = re.compile(r"(--[a-z][a-z0-9-]*)")


class RecoveryAdviceConformanceTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def base_checkpoint(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.env["AUTOCODE_FIXTURE_QUOTA_STAGE"] = "astra_review"
        self.launch(["Build greeting", "--chat"], 2, answers="CLI\nyes\n")
        run, paused = self.saved()
        self.launch(["--run-dir", str(run), "--abandon-stage", runner.attempt_id(paused["active_stage"])], 0)
        return run, self.saved()[1]

    def publish(self, stopped, status, stop_reason):
        stopped.update(status=status, stop_reason=stop_reason)
        self.assertTrue(
            runner.resolver_runtime.record_operational_exhaustion(
                runner, stopped, self.run, support.Paused(status, stop_reason)
            )
        )
        runner.write_json(self.run / "state.json", stopped)
        return self.saved()[1]

    def timeout_exhausted_checkpoint(self):
        self.run, stopped = self.base_checkpoint()
        stopped.update(automatic_recoveries_since_resume=3, consecutive_timeout_recoveries=3)
        stopped["automatic_timeout_recoveries"] = [
            {"stage": "terra", "timeout_reason": "idle watchdog"} for _ in range(3)
        ]
        return self.publish(
            stopped, "PAUSED_TIMEOUT_RECOVERY", "Automatic recovery budget exhausted; no further provider will launch."
        )

    def explicit_time_cap_checkpoint(self):
        self.run, stopped = self.base_checkpoint()
        stopped.setdefault("settings", {}).setdefault("limits", {})["max_seconds"] = 7200
        stopped["settings"].setdefault("budget_origins", {})["max_seconds"] = "user_explicit"
        stopped["active_seconds"] = 7300
        return self.publish(stopped, "PAUSED_TIME_LIMIT", "Saved active-time limit reached at stage boundary")

    def attempt_exhausted_checkpoint(self):
        self.run, stopped = self.base_checkpoint()
        return self.publish(
            stopped, "PAUSED_RESOLVER_OPERATIONAL", "AutoResolver exhausted its recorded operational recoveries"
        )

    def advertised_commands(self, state):
        published = human.current(state)
        surfaces = [state.get("stop_reason") or ""]
        if published:
            request = published.get("request") or {}
            surfaces.append(request.get("decision_needed") or "")
            surfaces.extend(request.get("options") or [])
            surfaces.extend((q.get("question") or "") for q in published.get("questions") or [])
        flags = set()
        for text in surfaces:
            flags.update(ADVERTISED_FLAGS.findall(text))
        return flags, published

    def progress(self, before, probe=None):
        """A resume made progress when a provider launched or the saved run moved."""
        _, after = self.saved()
        moved = after.get("status") != before.get("status") or len(after.get("stages", [])) != len(
            before.get("stages", [])
        )
        return moved or (probe is not None and probe.exists())

    def test_timeout_exhaustion_advertises_only_accepted_actions(self):
        state = self.timeout_exhausted_checkpoint()
        flags, published = self.advertised_commands(state)
        self.assertIn("--grant-recovery", flags)
        self.assertNotIn("--retry-failed-stage", flags)
        result = self.launch(["--run-dir", str(self.run), "--resume-paused", "--grant-recovery", "1", "--no-chat"], 2)
        self.assertNotIn("Input rejected", result.stderr)
        self.assertNotIn("requires a run paused for exhausted timeout recovery", result.stderr)
        self.assertTrue(self.progress(state), "the advertised grant must move the run")

    def test_explicit_time_cap_never_advertises_grant_and_bound_change_resumes_once(self):
        state = self.explicit_time_cap_checkpoint()
        flags, published = self.advertised_commands(state)
        self.assertNotIn("--grant-recovery", flags, "an explicit user cap must not advertise the timeout-only grant")
        result = self.launch(["--run-dir", str(self.run), "--resume-paused", "--grant-recovery", "1", "--no-chat"], 2)
        self.assertIn("requires a run paused for exhausted timeout recovery", result.stderr)
        raised = int(state["settings"]["limits"]["max_seconds"]) + 3600
        result = self.launch(
            ["--run-dir", str(self.run), "--resume-paused", "--max-seconds", str(raised), "--no-chat"], 2
        )
        self.assertNotIn(
            "retained the human guidance",
            result.stdout,
            "raising the exhausted bound must supersede in the same invocation",
        )
        self.assertNotIn("Input rejected", result.stderr)
        self.assertTrue(self.progress(state), "raising the exhausted bound must move the run in one invocation")

    def test_attempt_exhaustion_advertises_only_accepted_actions(self):
        state = self.attempt_exhausted_checkpoint()
        state.setdefault("automatic_permission_recoveries", []).extend(
            {"stage": "terra", "instruction": "workspace paths only"} for _ in range(3)
        )
        runner.write_json(self.run / "state.json", state)
        flags, published = self.advertised_commands(state)
        self.assertNotIn("--grant-recovery", flags)
        self.assertNotIn("--retry-failed-stage", flags)
        for flag in sorted(flags):
            if flag in ("--resolver-response",):
                continue  # answered through the request's own options, not a bare CLI retry
            with self.subTest(flag=flag):
                self.assertNotIn("requires", self.describe_rejection(flag))

    def test_unchanged_builder_batches_are_not_an_exhausted_recovery_budget(self):
        # #511: three Builder batches without source changes and no automatic recovery. The resume used to
        # stop as "Automatic recovery budget exhausted ... after 0 recorded operational recoveries" and
        # offer --grant-recovery; it must run the next stage instead.
        self.run, stopped = self.base_checkpoint()
        stopped.update(no_progress_batches=3)
        stopped.pop("automatic_recoveries_since_resume", None)
        runner.write_json(self.run / "state.json", stopped)
        self.launch(["--run-dir", str(self.run), "--resume-paused", "--no-chat"], 2)
        _, state = self.saved()
        self.assertGreater(len(state["stages"]), len(stopped["stages"]), "the next stage must run")
        # The fixture's Completion Reviewer quota stops it again: an honest stop that names no spent recovery.
        self.assertEqual("PAUSED_BUDGET", self.paused_for(state))
        self.assertNotIn("Automatic recovery budget exhausted", state["stop_reason"])
        self.assertIn("no automatic operational recovery ran", state["stop_reason"])
        self.assertNotIn("--grant-recovery", self.advertised_commands(state)[0])

    def resume(self, *flags):
        """Run --resume-paused with ``flags``; return (exit code, whether a provider launched, saved state)."""
        probe = self.root / f"launch-{len(list(self.root.glob('launch-*')))}.jsonl"
        self.env["AUTOCODE_REGISTRY_LAUNCH_PROBE"] = str(probe)
        result = subprocess.run(
            [
                *self.entry,
                "--workspace",
                str(self.project),
                "--run-dir",
                str(self.run),
                "--resume-paused",
                *flags,
                "--no-chat",
            ],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode, probe.exists(), self.saved()[1]

    def paused_for(self, state):
        published = human.current(state)
        if published:
            entry = state["resolver"]["human_escalations"][published["request_id"]]
            return entry["identity"]["proposal"]["origin"].get("pause_status")
        return state.get("status")

    def test_an_unrelated_limit_change_never_releases_the_time_cap(self):
        # #379: --max-stage-seconds is a different bound from the exhausted --max-seconds. The settings
        # change rebound the published request into a legacy blocker, and the generic resume launched a stage.
        state = self.explicit_time_cap_checkpoint()
        code, launched, after = self.resume("--max-stage-seconds", "1200")
        self.assertFalse(launched, "an unrelated limit must not admit a provider past the exhausted time cap")
        self.assertEqual(2, code)
        self.assertEqual("PAUSED_TIME_LIMIT", self.paused_for(after))
        self.assertEqual(
            1200, after["settings"]["limits"]["stage_timeout_seconds"], "the unrelated change itself is kept"
        )
        code, launched, after = self.resume()
        model_stages = [row["stage"] for row in after["stages"][len(state["stages"]) :] if not row.get("runner_owned")]
        self.assertEqual([], model_stages, "no model may run past the exhausted cap, AutoResolver included")
        self.assertEqual("PAUSED_TIME_LIMIT", self.paused_for(after))
        raised = int(state["settings"]["limits"]["max_seconds"]) + 3600
        code, launched, after = self.resume("--max-seconds", str(raised))
        self.assertTrue(
            launched or after["status"] != "PAUSED_TIME_LIMIT", "raising the exhausted bound still resumes afterwards"
        )

    def test_restating_a_saved_cap_with_headroom_resumes_after_a_consumed_response(self):
        # #378: the user answered the time-cap request (provide_information consumes it), then saved a raised
        # cap. Restating that cap with --resume-paused changed no setting, so it set no acknowledgment, and the
        # consumed response held the run: no command could resume it although the cap left headroom.
        state = self.explicit_time_cap_checkpoint()
        published = human.current(state)
        self.launch(
            [
                "--run-dir",
                str(self.run),
                "--resolver-request",
                published["request_id"],
                "--resolver-token",
                published["request_token"],
                "--resolver-response",
                "provide_information",
                "--resolver-message",
                "One more hour is authorized",
                "--no-chat",
            ],
            0,
        )
        raised = int(state["settings"]["limits"]["max_seconds"]) + 3600
        result = subprocess.run(
            [
                *self.entry,
                "--workspace",
                str(self.project),
                "--run-dir",
                str(self.run),
                "--max-seconds",
                str(raised),
                "--no-chat",
            ],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(raised, self.saved()[1]["settings"]["limits"]["max_seconds"], result.stdout + result.stderr)
        code, launched, after = self.resume("--max-seconds", str(raised))
        self.assertTrue(launched, "restating the saved cap that leaves headroom must resume the time-limit pause")

    def test_restating_an_exhausted_cap_stays_paused(self):
        state = self.explicit_time_cap_checkpoint()
        code, launched, after = self.resume("--max-seconds", str(state["settings"]["limits"]["max_seconds"]))
        self.assertFalse(launched, "a cap without headroom is not authority to continue")
        self.assertEqual("PAUSED_TIME_LIMIT", self.paused_for(after))

    def describe_rejection(self, flag):
        probe = self.root / "probe.jsonl"
        self.env["AUTOCODE_REGISTRY_LAUNCH_PROBE"] = str(probe)
        args = ["--run-dir", str(self.run), "--no-chat"]
        if flag == "--grant-recovery":
            args += ["--resume-paused", "--grant-recovery", "1"]
        elif flag == "--resume-paused":
            args += ["--resume-paused"]
        result = self.launch(args, 2)
        return result.stderr


if __name__ == "__main__":
    unittest.main()

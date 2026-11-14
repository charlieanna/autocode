"""Offline runner coverage for activity timeouts, durable status, and recovery."""

import contextlib
import copy
import io
import json
import os
import sys
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode as runner
import autocode_activity as activity
import autocode_completion as completion_gate
import autocode_completion_result as completion_result
import autocode_configure
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_milestones as milestones
import autocode_planning as planning
import autocode_recovery_accounting as recovery_accounting
import autocode_stuck_job as stuck_job
import autocode_support as support
import autocode_validation_recovery as validation_recovery
import autopilot
from autocode_activity import ActivityMonitor
from goal_fixtures import assert_operational_wait, envelope

from . import test_goals, test_stuck_job
from .supervision_fixture import launcher


class ActivityRuntimeTests(unittest.TestCase):
    setUp = test_goals.GoalTests.setUp
    draft = test_goals.GoalTests.draft
    approve = test_goals.GoalTests.approve
    decision = test_goals.GoalTests.decision

    def args(self, **selected):
        values = dict(
            engine="codex",
            task_planner_model=None,
            builder_model=None,
            tester_model=None,
            reasoning_effort=None,
            headroom=None,
            context_soft_tokens=None,
            rotate_after_input_tokens=None,
            legacy_iteration_ceiling=None,
            max_iterations=None,
            max_seconds=None,
            no_progress_limit=None,
            max_stage_seconds=None,
            max_idle_seconds=None,
            max_tool_seconds=None,
        )
        return SimpleNamespace(**{**values, **selected})

    def start_task(self):
        self.approve()
        self.state["settings"]["milestone_checkpoints"] = copy.deepcopy(milestones.DEFAULTS)
        lifecycle.assign_task(self.state, self.decision(), support.snapshot(self.root))
        self.state.update(next_stage="builder", status="RUNNING", phase="EXECUTING")

    def invoke(self, *args):
        output, error = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "argv", ["autocode", "--workspace", str(self.root), "--run-dir", str(self.run), *args]),
            patch.object(runner, "run_role", side_effect=AssertionError("No provider may launch")),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(error),
        ):
            code = runner.main()
        return code, output.getvalue(), error.getvalue()

    def test_new_run_limits_separate_idle_and_tool_from_the_stage_cap(self):
        fresh = {"workspace": str(self.root), "iteration": 1}
        with patch.object(support, "local_settings", return_value=self.local):
            configured = autocode_configure.configure(
                self.args(), fresh, planning=planning, milestones=milestones, autopilot=autopilot
            )
        self.assertEqual(3600, configured["limits"]["stage_timeout_seconds"])
        self.assertEqual(300, configured["limits"]["idle_timeout_seconds"])
        self.assertEqual(1800, configured["limits"]["tool_timeout_seconds"])

    def test_saved_hard_cap_preserved_while_missing_activity_limits_are_added(self):
        for saved_cap in (0, 300, 1800):
            with self.subTest(saved_cap=saved_cap):
                self.state["settings"]["limits"]["stage_timeout_seconds"] = saved_cap
                original = copy.deepcopy(self.state)
                configured = autocode_configure.configure(
                    self.args(), self.state, planning=planning, milestones=milestones, autopilot=autopilot
                )
                self.assertEqual(saved_cap, configured["limits"]["stage_timeout_seconds"])
                self.assertEqual(300, configured["limits"]["idle_timeout_seconds"])
                self.assertEqual(1800, configured["limits"]["tool_timeout_seconds"])
                self.assertEqual(
                    original["settings"]["roles"],
                    {role: configured["roles"][role] for role in original["settings"]["roles"]},
                )
                self.assertIn("completion", configured["roles"])
                self.assertEqual(original, self.state, "configure must return a new configuration")

    def test_saved_activity_limits_are_not_reset_on_resume(self):
        self.state["settings"]["limits"].update(
            stage_timeout_seconds=4200, idle_timeout_seconds=600, tool_timeout_seconds=2400
        )
        configured = autocode_configure.configure(
            self.args(), self.state, planning=planning, milestones=milestones, autopilot=autopilot
        )
        for key in ("stage_timeout_seconds", "idle_timeout_seconds", "tool_timeout_seconds"):
            self.assertEqual(self.state["settings"]["limits"][key], configured["limits"][key])

    def test_explicit_flags_can_disable_or_change_each_limit_independently(self):
        self.state["settings"]["limits"].update(
            stage_timeout_seconds=300, idle_timeout_seconds=300, tool_timeout_seconds=1800
        )
        configured = autocode_configure.configure(
            self.args(max_stage_seconds=0, max_idle_seconds=90, max_tool_seconds=0),
            self.state,
            planning=planning,
            milestones=milestones,
            autopilot=autopilot,
        )
        self.assertEqual(0, configured["limits"]["stage_timeout_seconds"])
        self.assertEqual(90, configured["limits"]["idle_timeout_seconds"])
        self.assertEqual(0, configured["limits"]["tool_timeout_seconds"])

    def test_cli_rejects_negative_activity_limits_before_touching_state(self):
        support.atomic_json(self.run / "state.json", self.state)
        original = (self.run / "state.json").read_bytes()
        for flag in ("--max-stage-seconds", "--max-idle-seconds", "--max-tool-seconds"):
            with self.subTest(flag=flag):
                error = io.StringIO()
                with (
                    contextlib.redirect_stderr(error),
                    patch.object(
                        sys, "argv", ["autocode", "--workspace", str(self.root), "--run-dir", str(self.run), flag, "-1"]
                    ),
                    self.assertRaises(SystemExit) as caught,
                ):
                    runner.main()
                self.assertEqual(2, caught.exception.code)
                self.assertIn(flag + " must be nonnegative", error.getvalue())
                self.assertEqual(original, (self.run / "state.json").read_bytes())

    def test_status_shows_checkpointed_activity_without_migrating_or_launching(self):
        observed = {
            "activity": "running_tool",
            "command": "python3 -m unittest",
            "observed_at": "2026-09-20T01:02:03Z",
            "elapsed_seconds": 600,
            "stage_limit_seconds": 1800,
            "tool_elapsed_seconds": 240,
        }
        self.state["settings"]["limits"]["stage_timeout_seconds"] = 1800
        self.state["active_stage"] = {
            "iteration": 1,
            "role": "builder",
            "stage": "builder",
            "output": str(self.run / "iterations/001/terra-01.json"),
            "activity": observed,
        }
        support.atomic_json(self.run / "state.json", self.state)
        original = (self.run / "state.json").read_bytes()
        code, output, _ = self.invoke("--status")
        self.assertEqual(0, code)
        displayed = json.loads(output)
        for key, value in observed.items():
            self.assertEqual(value, displayed["active_stage"]["activity"][key])
        self.assertNotIn("idle_timeout_seconds", displayed["settings"]["limits"])
        self.assertNotIn("tool_timeout_seconds", displayed["settings"]["limits"])
        self.assertEqual(original, (self.run / "state.json").read_bytes())

    def test_timeout_reason_and_kind_are_durable_for_each_deadline(self):
        self.start_task()
        self.state["settings"]["limits"].update(
            stage_timeout_seconds=20, idle_timeout_seconds=5, tool_timeout_seconds=10
        )

        class Child:
            pid = 987654321

            def __init__(child, command, **kwargs):
                kwargs["stdout"].write('{"type":"thread.started","thread_id":"fixture"}\n')
                kwargs["stdout"].flush()

        for kind, reason in [
            ("idle", "No meaningful provider activity for 5 seconds"),
            ("tool", "Tool python3 -m unittest exceeded 10 seconds"),
            ("stage", "Provider stage exceeded explicit 20-second limit"),
        ]:
            with self.subTest(kind=kind):
                state = copy.deepcopy(self.state)
                run = self.run / kind
                observed = []

                def wait(
                    child, hard_limit, checkpoint, *, activity, activity_checkpoint, startup_grace, stop_requested=None
                ):
                    self.assertEqual(20, hard_limit)
                    self.assertEqual(5, startup_grace)
                    checkpoint([])
                    activity.timeout = {"kind": kind, "reason": reason}
                    detail = {
                        **activity.snapshot(),
                        "activity": "stalled" if kind == "idle" else "timed_out",
                        "timeout_kind": kind,
                        "timeout_reason": reason,
                    }
                    activity_checkpoint(detail)
                    observed.append(support.read(run / "state.json")["active_stage"]["activity"])
                    return -15, True

                with (
                    patch.object(runner.supervision, "launch", launcher(Child)),
                    patch.object(support, "snapshot", return_value={"head": "h", "files": {}, "revision": "r"}),
                    patch.object(runner.processes, "preflight", return_value=None),
                    patch.object(runner.processes, "wait_for_stage", side_effect=wait),
                    contextlib.redirect_stdout(io.StringIO()),
                    self.assertRaises(support.Paused) as caught,
                ):
                    runner.run_role(
                        role="builder",
                        prompt="Finish the bounded greeting task",
                        sandbox="workspace-write",
                        workspace=self.root,
                        run_dir=run,
                        state=state,
                        schema=runner.SCHEMA_DIR / "v2/terra-report.schema.json",
                        model="fixture-terra",
                        allow_write=True,
                        dry_run=False,
                    )
                self.assertEqual("PAUSED_PROVIDER_TIMEOUT", caught.exception.status)
                self.assertIn(reason, str(caught.exception))
                active = support.read(run / "state.json")["active_stage"]
                self.assertEqual(kind, active["timeout_kind"])
                self.assertEqual(reason, active["timeout_reason"])
                self.assertTrue(active["accounted"])
                self.assertTrue(active["timed_out"])
                self.assertEqual(kind, observed[0]["timeout_kind"])
                self.assertIn("observed_at", observed[0])
                self.assertGreaterEqual(observed[0]["elapsed_seconds"], 0)
                self.assertEqual(20, observed[0]["stage_limit_seconds"])

    def idle_stop(self, *, supervisor_first, model="fixture-terra", limit=300, origin="runner default"):
        """Run a Builder whose provider falls quiet, through run_role with the real ActivityMonitor.

        The supervisor (autocode_process.wait_for_stage) usually stops the stage before the observer's own
        poll marks it stalled; it then publishes the last observation merged with its timeout_reason. When
        the observer polls first, the monitor's own detail carries the reason."""
        self.start_task()
        self.state["settings"]["limits"].update(
            stage_timeout_seconds=3600, idle_timeout_seconds=300, tool_timeout_seconds=1800
        )
        self.state["settings"]["budget_origins"] = {"idle_timeout_seconds": "runner_default"}
        clock = [0.0]

        class Child:
            pid = 987654321

            def __init__(child, command, **kwargs):
                child.stdout = kwargs["stdout"]  # the provider writes its events through its own handle
                child.stdout.write('{"type":"thread.started","thread_id":"fixture"}\n')
                child.stdout.flush()

        def monitor(*args, **kwargs):
            return ActivityMonitor(*args, clock=lambda: clock[0], **kwargs)

        def wait(child, hard_limit, checkpoint, *, activity, activity_checkpoint, startup_grace, stop_requested=None):
            checkpoint([])
            activity_checkpoint(activity.poll())
            clock[0] = 181
            child.stdout.write(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"id": "note", "type": "agent_message", "text": "Reading the failing module"},
                    }
                )
                + "\n"
            )
            child.stdout.flush()
            activity_checkpoint(activity.poll())
            if supervisor_first:
                # Shaped like autocode_process.stop_at_deadline: polling lag fired the limit at 299.5 s.
                clock[0] = 181 + limit - 0.5
                activity.timeout = {"kind": "idle", "reason": activity.idle_reason()}
                activity_checkpoint(
                    {
                        **activity.poll(),
                        "activity": "stalled",
                        "timeout_kind": "idle",
                        "timeout_reason": activity.timeout["reason"],
                    }
                )
            else:
                clock[0] = 181 + limit
                activity_checkpoint(activity.poll())
                activity.timeout = activity.expired()
            return -15, True

        output = io.StringIO()
        with (
            patch.object(activity, "ActivityMonitor", monitor),
            patch.object(runner.supervision, "launch", launcher(Child)),
            patch.object(support, "snapshot", return_value={"head": "h", "files": {}, "revision": "r"}),
            patch.object(runner.processes, "preflight", return_value=None),
            patch.object(runner.processes, "process_table", return_value={}),
            patch.object(runner.processes, "wait_for_stage", side_effect=wait),
            contextlib.redirect_stdout(output),
            self.assertRaises(support.Paused) as caught,
        ):
            runner.run_role(
                role="builder",
                prompt="Finish the bounded greeting task",
                sandbox="workspace-write",
                workspace=self.root,
                run_dir=self.run,
                state=self.state,
                schema=runner.SCHEMA_DIR / "v2/terra-report.schema.json",
                model=model,
                allow_write=True,
                dry_run=False,
            )
        reason = (
            f"No new provider activity within the inactivity limit ({limit} seconds, {origin}; "
            "change it with autocode resume --max-idle-seconds N)"
        )
        self.assertEqual("PAUSED_PROVIDER_TIMEOUT", caught.exception.status)
        self.assertIn(reason, str(caught.exception))
        stalled = [line for line in output.getvalue().splitlines() if line.startswith("Builder: stalled;")]
        self.assertEqual(1, len(stalled))
        self.assertTrue(stalled[0].endswith("; " + reason), stalled[0])
        active = support.read(self.run / "state.json")["active_stage"]
        self.assertEqual(("idle", reason), (active["timeout_kind"], active["timeout_reason"]))
        self.assertEqual(181, active["activity"]["longest_idle_seconds"])
        return stalled[0], active

    def test_idle_stop_names_saved_limit_origin_when_the_supervisor_stops_first(self):
        stalled, active = self.idle_stop(supervisor_first=True)
        self.assertIn("idle=299.5s/300", stalled)
        self.assertEqual(299.5, active["activity"]["idle_seconds"])

    def test_a_mimo_route_runs_under_its_longer_default_and_says_so(self):
        # #298: live MiMo Builders were stopped at 300 s mid-turn and finished at 900 s. The saved
        # setting stays the runner default; only this launch's limit is raised.
        stalled, active = self.idle_stop(
            supervisor_first=False,
            model="xiaomi-token-plan-sgp/mimo-v2.6-pro",
            limit=900,
            origin="runner default for MiMo routes",
        )
        self.assertIn("idle=900s/900", stalled)
        self.assertEqual(300, active["idle_timeout_seconds"])
        self.assertEqual(300, support.read(self.run / "state.json")["settings"]["limits"]["idle_timeout_seconds"])

    def test_idle_stop_names_saved_limit_origin_when_the_monitor_sees_it_first(self):
        stalled, active = self.idle_stop(supervisor_first=False)
        self.assertIn("idle=300s/300", stalled)
        self.assertEqual(300, active["activity"]["idle_seconds"])

    def test_the_launch_names_its_stage_to_the_idle_policy(self):
        # The report-only planning stages get their own floor from the stage name (autocode_idle_policy); the
        # Builder here keeps the runner default, so the stage must reach the policy with the limit.
        seen = []
        real = runner.idle_policy.effective

        def spy(limit, origin, model, stage=None):
            seen.append((limit, origin, stage))
            return real(limit, origin, model, stage=stage)

        with patch.object(runner.idle_policy, "effective", spy):
            stalled, _ = self.idle_stop(supervisor_first=False)
        self.assertEqual([(300, "runner_default", "builder")], seen)
        self.assertIn("idle=300s/300", stalled)

    def interrupted_builder(self, launch):
        """Run an admitted Builder whose stage's one interrupt lands outside its wait (#454)."""
        self.start_task()
        with (
            patch.object(runner.supervision, "launch", launch),
            patch.object(support, "snapshot", return_value={"head": "h", "files": {}, "revision": "r"}),
            patch.object(runner.processes, "preflight", return_value=None),
            patch.object(runner.processes, "wait_for_stage", return_value=(0, False)),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            try:
                runner.run_role(
                    role="builder",
                    prompt="Finish the bounded greeting task",
                    sandbox="workspace-write",
                    workspace=self.root,
                    run_dir=self.run,
                    state=self.state,
                    schema=runner.SCHEMA_DIR / "v2/terra-report.schema.json",
                    model="fixture-terra",
                    allow_write=True,
                    dry_run=False,
                )
            except KeyboardInterrupt as error:
                self.fail(f"One {error} escaped as a bare KeyboardInterrupt, leaving the run RUNNING")
            except support.Paused as error:
                return error, support.read(self.run / "state.json")["active_stage"]
        self.fail("An interrupted stage must pause")

    def failed_before_admission(self, error):
        """Run a Builder whose launch fails after its files are prepared and before it is the active stage (#1114)."""
        self.start_task()
        with (
            patch.object(runner.job_source, "capture", side_effect=error),
            patch.object(support, "snapshot", return_value={"head": "h", "files": {}, "revision": "r"}),
            patch.object(runner.processes, "preflight", return_value=None),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(type(error)),
        ):
            runner.run_role(
                role="builder",
                prompt="Finish the bounded greeting task",
                sandbox="workspace-write",
                workspace=self.root,
                run_dir=self.run,
                state=self.state,
                schema=runner.SCHEMA_DIR / "v2/terra-report.schema.json",
                model="fixture-terra",
                allow_write=True,
                dry_run=False,
            )
        left = sorted(p.name for p in (self.run / "iterations").rglob("*") if p.is_file())
        self.assertEqual([], left, "a launch that never became the active stage left files no state names")
        self.assertNotIn("active_stage", self.state)

    def test_a_full_disk_before_admission_leaves_no_prompt_to_block_the_next_attempt(self):
        self.failed_before_admission(OSError(28, "No space left on device"))

    def test_a_signal_before_admission_leaves_no_prompt_to_block_the_next_attempt(self):
        self.failed_before_admission(KeyboardInterrupt("SIGTERM"))

    def test_a_first_signal_during_the_provider_launch_pauses_as_interrupted(self):
        # Arming the keeper and starting the provider take a moment after the attempt is admitted.
        @contextlib.contextmanager
        def launch(command, *, receipt_path, timeout=None, checkpoint=None, **options):
            raise KeyboardInterrupt("SIGINT")  # once the launch's own cleanup stopped what it started
            yield

        error, active = self.interrupted_builder(launch)
        self.assertEqual("PAUSED_INTERRUPTED", error.status)
        self.assertIn("SIGINT", str(error))
        self.assertEqual("builder", active["stage"], "the admitted attempt stays for inspection")
        self.assertTrue(Path(active["events"]).is_file())

    def test_a_first_signal_after_the_provider_was_collected_pauses_as_interrupted(self):
        # The provider ended on its own; the signal landed while its keeper was discharged.
        class Child:
            pid = 987654321

            def __init__(child, command, **kwargs):
                pass

        @contextlib.contextmanager
        def launch(*args, **kwargs):
            with launcher(Child)(*args, **kwargs) as child:
                yield child
            raise KeyboardInterrupt("SIGHUP")

        error, active = self.interrupted_builder(launch)
        self.assertEqual("PAUSED_INTERRUPTED", error.status)
        self.assertEqual((0, True), (active["exit_code"], active["interrupted"]))

    def test_a_first_signal_between_the_launch_and_the_wait_pauses_as_interrupted(self):
        # The provider is running and the wait has not begun: its "started" line can block on a
        # stalled stdout. Here the signal lands as the activity monitor is built.
        class Child:
            pid = 987654321
            returncode = None

            def __init__(child, command, **kwargs):
                pass

            def poll(child):
                return child.returncode

        @contextlib.contextmanager
        def launch(*args, **kwargs):
            with launcher(Child)(*args, **kwargs) as child:
                yield child
            child.returncode = -9  # the keeper stopped the provider the wait never reached

        def monitor(*args, **kwargs):
            raise KeyboardInterrupt("SIGHUP")

        with patch.object(activity, "ActivityMonitor", monitor):
            error, active = self.interrupted_builder(launch)
        self.assertEqual("PAUSED_INTERRUPTED", error.status)
        self.assertEqual(
            (-9, True),
            (active["exit_code"], active["interrupted"]),
            "the exit code is collected once the keeper is discharged",
        )

    def test_resume_with_max_idle_seconds_applies_to_the_next_launch(self):
        self.start_task()
        self.state["settings"]["limits"]["idle_timeout_seconds"] = 300
        self.state["settings"]["budget_origins"] = {"idle_timeout_seconds": "runner_default"}
        self.state.update(status="PAUSED_PROVIDER_TIMEOUT", stop_reason="Builder idle stop")
        launched = []

        def inspect(**kwargs):
            settings = kwargs["state"]["settings"]
            launched.append(
                (settings["limits"]["idle_timeout_seconds"], settings["budget_origins"]["idle_timeout_seconds"])
            )
            raise support.Paused("PAUSED_TEST", "Offline dispatch inspected")

        test_goals.GoalTests.invoke(self, "--resume-paused", "--max-idle-seconds", "900", role=inspect)
        self.assertEqual([(900, "user_explicit")], launched)

    def interrupted_attempt(self, *, terminal=False):
        before = support.snapshot(self.root)
        base = self.run / "iterations/001/terra-01"
        base.parent.mkdir(parents=True, exist_ok=True)
        support.atomic_json(base.with_suffix(".before.json"), before)
        rows = [{"type": "thread.started", "thread_id": "expired-session"}]
        if terminal:
            rows.append({"type": "turn.completed"})
        base.with_suffix(".jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        source = self.root / "greet.py"
        source.write_text("print('retained partial greeting')\n")
        record = {
            "role": "builder",
            "stage": "builder",
            "iteration": 1,
            "output": str(base.with_suffix(".json")),
            "events": str(base.with_suffix(".jsonl")),
            "before_ref": str(base.with_suffix(".before.json")),
            "duration_seconds": 10,
            "exit_code": 0 if terminal else -15,
            "timed_out": True,
            "processes": [],
            "timeout_kind": "idle",
            "timeout_reason": "No meaningful activity for 5 seconds",
        }
        self.state["active_stage"] = record
        self.state["sessions"]["builder"] = "expired-session"
        return source, record

    def test_partial_timeout_archives_once_preserves_work_and_routes_to_astra(self):
        self.start_task()
        self.state["settings"]["limits"].update(
            tool_timeout_seconds=1800, idle_timeout_seconds=600, stage_timeout_seconds=5400
        )
        source, record = self.interrupted_attempt()
        original_source = source.read_bytes()
        original_goal = copy.deepcopy(self.state["goal_contract"])
        with patch.object(runner, "run_role", side_effect=AssertionError("Recovery must not replay the writer")):
            self.assertTrue(
                runner.automatically_recover_timed_out_stage(
                    self.state, self.run, self.root, support.Paused("PAUSED_PROVIDER_TIMEOUT", "idle")
                )
            )
            self.assertFalse(
                runner.automatically_recover_timed_out_stage(
                    self.state, self.run, self.root, support.Paused("PAUSED_PROVIDER_TIMEOUT", "idle")
                )
            )
        self.assertEqual(original_source, source.read_bytes())
        self.assertEqual(original_goal, self.state["goal_contract"])
        self.assertEqual("review_completion", self.state["next_stage"])
        self.assertNotIn("active_stage", self.state)
        self.assertNotIn("builder", self.state["sessions"])
        self.assertEqual(1, len(self.state["automatic_timeout_recoveries"]))
        recovery = self.state["recovery_context"]
        self.assertEqual(self.state["current_task"]["id"], recovery["task_id"])
        self.assertEqual(1800, recovery["execution_limits"]["tool_timeout_seconds"])
        self.assertEqual(600, recovery["execution_limits"]["idle_timeout_seconds"])
        self.assertIn("split long tool work into bounded calls", recovery["instruction"])
        # AutoResolver's runner-owned observation receipt may follow the archive.
        archived = [row for row in self.state["stages"] if not row.get("runner_owned")][-1]
        self.assertEqual("idle", archived["timeout_kind"])
        self.assertTrue(Path(archived["events"]).is_file())
        self.assertEqual(["greet.py"], archived["changed_files"])

    def test_archived_builder_reaches_failure_inspection_before_running_regression(self):
        self.start_task()
        self.state["implementation"] = {"source_revision": "earlier", "summary": "An older accepted report"}
        source, record = self.interrupted_attempt()
        record.update(
            task_id=self.state["current_task"]["id"],
            contract_hash=self.state["goal_contract"]["hash"],
            contract_revision=self.state["goal_contract"]["revision"],
        )
        with patch.object(runner, "run_role", side_effect=AssertionError("Recovery must not replay the writer")):
            self.assertTrue(
                runner.automatically_recover_timed_out_stage(
                    self.state, self.run, self.root, support.Paused("PAUSED_PROVIDER_TIMEOUT", "idle")
                )
            )
        self.assertEqual("review_completion", self.state["next_stage"])
        retained = source.read_bytes()
        with patch.object(runner.regression, "prove", side_effect=AssertionError("Inspect the failed attempt first")):
            completion_result.before_review(self.state, "review_completion", self.root, self.run)
        self.assertEqual(retained, source.read_bytes())
        self.assertNotIn("regression_proof", self.state)
        self.assertFalse(
            completion_gate.completion_ready(self.state, self.decision("TASK_COMPLETE"), support.snapshot(self.root))
        )
        # A later validation launch runs the proof even while the recovery note is still current.
        with patch.object(runner.regression, "prove") as prove:
            completion_result.before_review(self.state, "tester", self.root, self.run)
        prove.assert_called_once()

    def test_a_timeout_that_exited_0_before_any_session_row_recovers_on_resume(self):
        # A provider that handles the stop signal exits 0. With no output there is no session row, which is
        # still a timeout to recover, not a response from an unexpected session (#464).
        for saved in (None, "expired-session"):
            with self.subTest(saved_session=saved):
                self.setUp()
                self.start_task()
                source, record = self.interrupted_attempt()
                Path(record["events"]).write_text("")
                record.update(exit_code=0, supports_sessions=True, expected_session=saved)
                if not saved:
                    self.state["sessions"].pop("builder")
                original_source = source.read_bytes()
                with self.assertRaises(support.Paused) as caught:
                    runner.reconcile_active(self.state, self.run, self.root)
                self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", caught.exception.status, str(caught.exception))
                with patch.object(
                    runner, "run_role", side_effect=AssertionError("Recovery must not replay the writer")
                ):
                    self.assertTrue(
                        runner.automatically_recover_timed_out_stage(self.state, self.run, self.root, caught.exception)
                    )
                self.assertNotIn("active_stage", self.state)
                self.assertNotIn("builder", self.state["sessions"])
                self.assertEqual(original_source, source.read_bytes())

    def test_uncertain_recovery_requires_a_durable_timeout_not_just_an_error_label(self):
        self.start_task()
        _, record = self.interrupted_attempt()
        record["timed_out"] = False
        original = copy.deepcopy(self.state)
        self.assertFalse(
            runner.automatically_recover_timed_out_stage(
                self.state, self.run, self.root, support.Paused("PAUSED_PROVIDER_UNCERTAIN", "no final")
            )
        )
        self.assertEqual(original, self.state)
        record["timed_out"] = True
        self.assertFalse(
            runner.automatically_recover_timed_out_stage(
                self.state, self.run, self.root, support.Paused("PAUSED_BILLING_ROUTE", "not authorized")
            )
        )
        decision = self.decision("VALIDATE")
        decision["next_task"]["kind"] = "validate"
        runner.apply_result(self.state, "review_completion", decision, {"output": "review.json"}, self.root, self.run)
        self.assertEqual("tester", self.state["next_stage"], "Retained work still needs independent verification")
        self.assertFalse(
            completion_gate.completion_ready(self.state, self.decision("TASK_COMPLETE"), support.snapshot(self.root))
        )

    def test_timeout_racing_a_completed_report_reconciles_to_sol_without_replay(self):
        self.start_task()
        source, record = self.interrupted_attempt(terminal=True)
        evidence = self.run / "retained-check.log"
        evidence.write_text("Implementation finished; the Validator must verify the result.\n")
        report = {
            **envelope(self.state),
            "summary": "Retained completed greeting",
            "changed_files": ["greet.py"],
            "commands_run": [],
            "results": ["Implementation saved"],
            "remaining_risks": ["Independent verification pending"],
            "evidence_refs": [str(evidence)],
            "addressed_requirements": ["Greet a valid name"],
            "untested_behavior": ["Invalid input"],
            "recommended_checks": ["Execute both CLI cases"],
        }
        schema = self.run / "terra.schema.json"
        support.atomic_json(
            schema, goals.role_schema(support.read(runner.SCHEMA_DIR / "v2/terra-report.schema.json"), "builder")
        )
        record["schema"] = str(schema)
        support.atomic_json(Path(record["output"]), report)
        original_source = source.read_bytes()
        with patch.object(runner, "run_role", side_effect=AssertionError("A completed writer must not replay")):
            self.assertFalse(
                runner.automatically_recover_timed_out_stage(
                    self.state, self.run, self.root, support.Paused("PAUSED_PROVIDER_TIMEOUT", "idle")
                )
            )
            runner.reconcile_active(self.state, self.run, self.root)
            runner.reconcile_active(self.state, self.run, self.root)
        self.assertEqual(original_source, source.read_bytes())
        self.assertEqual("tester", self.state["next_stage"])
        self.assertNotIn("active_stage", self.state)
        self.assertEqual(1, sum(row.get("stage") == "builder" for row in self.state["stages"]))
        self.assertFalse(self.state.get("automatic_timeout_recoveries"))

    def test_timeout_does_not_recover_while_a_recorded_worker_is_live(self):
        self.start_task()
        self.interrupted_attempt()
        self.state["active_stage"]["processes"] = [{"pid": 12345, "started": "fixture"}]
        original = copy.deepcopy(self.state)
        with patch.object(runner.processes, "live_processes", return_value=[{"pid": 12345}]):
            self.assertFalse(
                runner.automatically_recover_timed_out_stage(
                    self.state, self.run, self.root, support.Paused("PAUSED_PROVIDER_TIMEOUT", "idle")
                )
            )
        self.assertEqual(original, self.state)

    def test_repeated_astra_timeout_recovery_stops_before_another_provider_launch(self):
        self.start_task()
        self.state["next_stage"] = "review_completion"
        for attempt in range(3):
            runner.timeout_recovery_guard(self.state)
            _, record = self.interrupted_attempt()
            record.update(role="task_planner", stage="review_completion", iteration=attempt + 1)
            self.assertTrue(
                runner.automatically_recover_timed_out_stage(
                    self.state, self.run, self.root, support.Paused("PAUSED_PROVIDER_TIMEOUT", "idle")
                )
            )
            self.assertEqual(attempt + 1, self.state["consecutive_timeout_recoveries"])
            self.assertEqual("review_completion", self.state["next_stage"])
        with (
            patch.object(runner.subprocess, "Popen", side_effect=AssertionError("Recovery budget must stop launch")),
            self.assertRaises(support.Paused) as caught,
        ):
            runner.run_role(
                role="task_planner",
                prompt="Inspect retained work",
                sandbox="read-only",
                workspace=self.root,
                run_dir=self.run,
                state=self.state,
                schema=runner.SCHEMA_DIR / "v2/astra-decision.schema.json",
                model="fixture-astra",
                allow_write=False,
                dry_run=False,
            )
        self.assertEqual("PAUSED_TIMEOUT_RECOVERY", caught.exception.status)
        self.assertEqual(3, len(self.state["automatic_timeout_recoveries"]))
        failures = list(support.read(self.run / "state.json")["failure_history"].values())
        self.assertEqual(1, len(failures))
        self.assertEqual(3, failures[0]["count"])
        self.assertEqual("PAUSED_PROVIDER_TIMEOUT", failures[0]["identity"]["error_class"])
        self.assertEqual(1, failures[0]["output_probe"]["attempts"])
        self.assertNotIn("active_stage", self.state)

    def stalled_tester(self, iteration, *, stage="tester"):
        _, record = self.interrupted_attempt()
        record.update(role="tester" if stage == "tester" else "task_planner", stage=stage, iteration=iteration)
        return runner.automatically_recover_timed_out_stage(
            self.state, self.run, self.root, support.Paused("PAUSED_PROVIDER_TIMEOUT", "idle")
        )

    def saved_report(self, stage, name):
        """A valid report ``stage`` saved, which ends the run-wide failure streak."""
        events = self.run / f"{name}.jsonl"
        events.write_text('{"type":"turn.completed"}\n')
        self.state["stages"].append(
            {
                "role": "task_planner" if stage == "review_completion" else "tester",
                "stage": stage,
                "output": str(self.run / f"{name}.json"),
                "events": str(events),
                "schema": str(self.run / f"{name}.schema.json"),
                "exit_code": 0,
                "finished_at": support.now(),
            }
        )
        recovery_accounting.stage_saved(self.state)

    def test_only_the_investigator_of_an_exhausted_recovery_budget_passes_its_guard(self):
        state = test_stuck_job.state_for(
            next_stage="review_completion", automatic_recoveries_since_resume=3, consecutive_timeout_recoveries=3
        )
        with self.assertRaises(support.Paused) as stopped:
            runner.timeout_recovery_guard(state)
        self.assertEqual("PAUSED_TIMEOUT_RECOVERY", stopped.exception.status)
        self.assertTrue(stuck_job.intercept(state, stopped.exception.status, str(stopped.exception)))
        self.assertEqual("investigate_stuck", state["next_stage"])
        runner.timeout_recovery_guard(state)  # the queued diagnosis launches
        for change in ({"in_force": True}, {"status": "PAUSED_NO_PROGRESS"}):
            other = copy.deepcopy(state)
            other["stuck_investigation"].update(change)
            with self.subTest(change=change), self.assertRaises(support.Paused):
                runner.timeout_recovery_guard(other)
        other = {**copy.deepcopy(state), "next_stage": "review_completion"}
        with self.assertRaises(support.Paused):
            runner.timeout_recovery_guard(other)

    def test_a_tester_stalling_again_on_unchanged_source_goes_to_the_investigator(self):
        # 2026-11-06 live run: each stalled Tester was re-issued by a Completion Reviewer whose valid
        # report ended the failure streak, so six Testers stalled and none reached the Investigator.
        self.start_task()
        self.state["next_stage"] = "tester"
        self.assertTrue(self.stalled_tester(1))
        self.assertEqual("RUNNING", self.state["status"])
        self.saved_report("review_completion", "completion-review-01")
        self.assertEqual(0, self.state["consecutive_timeout_recoveries"])
        self.state["next_stage"] = "tester"
        self.assertTrue(self.stalled_tester(2))
        self.assertEqual("PAUSED_REPEATED_FAILURE", self.state["status"])
        self.assertEqual("tester", self.state["next_stage"])
        self.assertIn("Tester stopped 2 times on unchanged source", self.state["stop_reason"])
        self.assertIn("idle timeout", self.state["stop_reason"])
        self.assertEqual("PAUSED_REPEATED_FAILURE", support.read(self.run / "state.json")["status"])
        # The pause is one the Investigator takes before anyone is asked.
        self.assertTrue(stuck_job.intercept(self.state, self.state["status"], self.state["stop_reason"]))
        self.assertEqual("investigate_stuck", self.state["next_stage"])
        self.assertEqual("tester", self.state["stuck_investigation"]["stage"])

    def test_a_valid_tester_report_or_changed_source_starts_the_stall_count_again(self):
        self.start_task()
        self.state["next_stage"] = "tester"
        self.assertTrue(self.stalled_tester(1))
        self.saved_report("tester", "validator-01")
        self.state["next_stage"] = "tester"
        self.assertTrue(self.stalled_tester(2))
        self.assertEqual("RUNNING", self.state["status"])
        (self.root / "other.py").write_text("print('changed by a Builder')\n")
        self.state["next_stage"] = "tester"
        self.assertTrue(self.stalled_tester(3))
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual(3, len(self.state["automatic_timeout_recoveries"]))

    def test_consecutive_tester_stalls_are_investigated_before_a_third_identical_attempt(self):
        # #3233: the direct Tester retry bypassed the repeated-stage guard because no successful
        # Completion Review reset the streak. Each attempt repeated the same long verification.
        self.start_task()
        self.state["next_stage"] = "tester"
        self.assertTrue(self.stalled_tester(1))
        self.assertEqual("RUNNING", self.state["status"])
        self.assertTrue(self.stalled_tester(2))
        self.assertEqual("PAUSED_REPEATED_FAILURE", self.state["status"])
        self.assertEqual(2, len(self.state["automatic_timeout_recoveries"]))
        self.assertTrue(all(Path(row["events"]).is_file() for row in self.state["automatic_timeout_recoveries"]))
        self.assertTrue(stuck_job.intercept(self.state, self.state["status"], self.state["stop_reason"]))
        self.assertEqual("investigate_stuck", self.state["next_stage"])
        self.assertEqual("tester", self.state["stuck_investigation"]["stage"])

    def test_consecutive_completion_stalls_reach_diagnosis_before_a_third_review(self):
        for stage in ("review_completion", "review_checkpoint"):
            with self.subTest(stage=stage):
                self.start_task()
                self.state["next_stage"] = stage
                self.assertTrue(self.stalled_tester(1, stage=stage))
                self.assertEqual("RUNNING", self.state["status"])
                self.assertTrue(self.stalled_tester(2, stage=stage))
                self.assertEqual("PAUSED_REPEATED_FAILURE", self.state["status"])
                self.assertTrue(stuck_job.intercept(self.state, self.state["status"], self.state["stop_reason"]))
                self.assertEqual("investigate_stuck", self.state["next_stage"])
                self.assertEqual(stage, self.state["stuck_investigation"]["stage"])

    def completion_review_timeout(self):
        """A stopped non-terminal Completion Review over a source the Validator already passed."""
        self.start_task()
        self.state["next_stage"] = "review_completion"
        _, record = self.interrupted_attempt()
        # The retained edit belongs to the earlier Builder attempt: this review writes nothing, so
        # its own before/after snapshots are the same source.
        support.atomic_json(Path(record["before_ref"]), support.snapshot(self.root))
        evidence = self.run / "tester-evidence.txt"
        evidence.write_text("Executed the approved checks.\n")
        record.update(role="task_planner", stage="review_completion", original_stage="review_completion")
        self.state["sessions"].pop("builder", None)
        self.state["sessions"]["task_planner"] = "expired-session"
        self.state["validation"] = {
            "verdict": "PASS",
            "reviewer_role": "tester",
            "source_revision": support.snapshot(self.root)["revision"],
            "criteria_revision": self.state["criteria_revision"],
            "contract_revision": self.state["goal_contract"]["revision"],
            "contract_hash": self.state["goal_contract"]["hash"],
            "task_id": self.state["current_task"]["id"],
            "evidence_hashes": {str(evidence): support.file_hash(evidence)},
        }
        self.state["human_reviews"] = {"C1": "accepted"}
        self.state["displayed_review"] = "review-token"
        return record, evidence

    def timed_out_review(self, state):
        return runner.automatically_recover_timed_out_stage(
            state, self.run, self.root, support.Paused("PAUSED_PROVIDER_TIMEOUT", "idle")
        )

    def tester_timeout(self, *, changed_source=False):
        self.start_task()
        _, record = self.interrupted_attempt()
        record.update(role="tester", stage="tester", original_stage="tester")
        self.state["next_stage"] = "tester"
        self.state["sessions"].pop("builder")
        self.state["sessions"]["tester"] = "expired-session"
        if not changed_source:
            support.atomic_json(Path(record["before_ref"]), support.snapshot(self.root))

    def test_read_only_tester_timeout_retries_the_missing_validation_before_completion(self):
        self.tester_timeout()
        original_task = copy.deepcopy(self.state["current_task"])
        with patch.object(runner, "run_role", side_effect=AssertionError("Recovery must not run a provider")):
            self.assertTrue(self.timed_out_review(self.state))
        saved = support.read(self.run / "state.json")
        self.assertEqual("tester", saved["next_stage"])
        self.assertEqual(original_task, saved["current_task"])
        self.assertNotIn("validation", saved)
        self.assertNotIn("tester", saved["sessions"])
        self.assertEqual(1, len(saved["automatic_timeout_recoveries"]))
        self.assertIn("no accepted Tester report", saved["recovery_context"]["instruction"])
        self.assertTrue(any(row.get("abandoned") and row["stage"] == "tester" for row in saved["stages"]))

    def test_tester_timeout_with_source_changes_keeps_the_recovery_review(self):
        self.tester_timeout(changed_source=True)
        self.assertTrue(self.timed_out_review(self.state))
        self.assertEqual("review_completion", self.state["next_stage"])
        self.assertEqual(["greet.py"], self.state["recovery_context"]["changed_files"])

    def test_completion_review_timeout_keeps_current_validation_and_retries_completion(self):
        # 2026-10-30 live run: two idle-stopped Completion Reviews archived the Tester's current
        # evidence, so the third valid COMPLETE could never pass the gate that needs it.
        self.completion_review_timeout()
        original = copy.deepcopy(self.state["validation"])
        with patch.object(runner, "run_role", side_effect=AssertionError("Recovery must not replay a verdict")):
            self.assertTrue(self.timed_out_review(self.state))
        self.assertEqual(original, self.state["validation"], "A read-only review cannot invalidate its own evidence")
        self.assertEqual({"C1": "accepted"}, self.state["human_reviews"])
        self.assertEqual("review-token", self.state["displayed_review"])
        self.assertFalse(self.state.get("validation_archive"))
        self.assertEqual("review_completion", self.state["next_stage"])
        self.assertEqual(1, len(self.state["automatic_timeout_recoveries"]))
        self.assertEqual(original, support.read(self.run / "state.json")["validation"])

    def test_completion_review_timeout_revalidates_when_the_evidence_no_longer_binds(self):
        initial = copy.deepcopy(self.state)
        for binding, expected in (
            ("source", "tester"),
            ("contract", "tester"),
            ("criteria", "tester"),
            ("task", "tester"),
            ("pin", "tester"),
            ("absent", "review_completion"),
        ):
            with self.subTest(binding=binding):
                # Each binding fault is a separate run, not another retry of the previous fault.
                self.state = copy.deepcopy(initial)
                record, evidence = self.completion_review_timeout()
                if binding == "source":
                    (self.root / "greet.py").write_text("print('changed after the validation ran')\n")
                elif binding == "contract":
                    self.state["validation"]["contract_hash"] = "another-approved-goal"
                elif binding == "criteria":
                    self.state["criteria_revision"] = 99
                elif binding == "task":
                    self.state["validation"]["task_id"] = "another-task"
                elif binding == "pin":
                    evidence.write_text("Changed after the Validator pinned it.\n")
                else:
                    del self.state["validation"]
                self.assertFalse(validation_recovery.current(self.state, record))
                with patch.object(runner, "run_role", side_effect=AssertionError("Recovery must not replay a verdict")):
                    self.assertTrue(self.timed_out_review(self.state))
                if binding == "absent":
                    self.assertEqual(
                        "review_completion", self.state["next_stage"], "Nothing was retired to route around"
                    )
                    continue
                archived = self.state["validation_archive"][-1]
                self.assertEqual("Timed-out implementation automatically archived", archived["reason"])
                self.assertNotIn("validation", self.state)
                self.assertEqual({}, self.state["human_reviews"])
                self.assertNotIn("displayed_review", self.state)
                self.assertEqual(expected, self.state["next_stage"])

    def test_completion_review_timeout_routes_the_workflows_verification_stage(self):
        # The retry must ask the reviewer that this routing uses for independent verification, and a
        # final-audit-only run has no separate Validator: its own audit stage re-produces the evidence.
        initial = copy.deepcopy(self.state)
        for mode, policy, expected in (
            (None, None, "tester"),
            (runner.workflow.MODE, runner.workflow.APPROVAL_TEXT, "review_checkpoint"),
            (runner.workflow.FINAL_MODE, runner.workflow.FINAL_APPROVAL, "review_completion"),
        ):
            with self.subTest(mode=mode):
                self.state = copy.deepcopy(initial)
                self.completion_review_timeout()
                self.state["settings"]["milestone_checkpoints"] = {"enabled": False}
                if mode:
                    event = {
                        "kind": "workflow_approval",
                        "actor": "user",
                        "at": support.now(),
                        "policy": policy,
                        "contract_hash": self.state["goal_contract"]["hash"],
                        "source": "test_operator",
                    }
                    self.state["user_events"].append(event)
                    self.state["settings"]["workflow"] = {"mode": mode, "approval_event": event}
                (self.root / "greet.py").write_text("print('changed after the validation ran')\n")
                with patch.object(runner, "run_role", side_effect=AssertionError("Recovery must not replay a verdict")):
                    self.assertTrue(self.timed_out_review(self.state))
                self.assertNotIn("validation", self.state)
                self.assertEqual(expected, self.state["next_stage"])

    def test_writer_timeout_retires_current_validation_and_keeps_the_recovery_review(self):
        # A timed-out Builder may have left half-written work, so even current evidence is retired —
        # and the established Plan Reviewer recovery review still inspects it before another writer.
        self.start_task()
        _, record = self.interrupted_attempt()
        evidence = self.run / "tester-evidence.txt"
        evidence.write_text("Executed the approved checks.\n")
        self.state["validation"] = {
            "verdict": "PASS",
            "reviewer_role": "tester",
            "source_revision": support.snapshot(self.root)["revision"],
            "criteria_revision": self.state["criteria_revision"],
            "contract_revision": self.state["goal_contract"]["revision"],
            "contract_hash": self.state["goal_contract"]["hash"],
            "task_id": self.state["current_task"]["id"],
            "evidence_hashes": {str(evidence): support.file_hash(evidence)},
        }
        # The evidence binds this exact source; only the writer's retained change makes it unsafe.
        self.assertTrue(
            validation_recovery.current(
                self.state, {"changed_files": [], "source_revision": self.state["validation"]["source_revision"]}
            )
        )
        with patch.object(runner, "run_role", side_effect=AssertionError("Recovery must not replay the writer")):
            self.assertTrue(self.timed_out_review(self.state))
        self.assertNotIn("validation", self.state)
        self.assertEqual({}, self.state["human_reviews"])
        self.assertEqual("review_completion", self.state["next_stage"])

    def test_only_an_accepted_result_resets_consecutive_timeout_recoveries(self):
        self.start_task()
        self.state["consecutive_timeout_recoveries"] = 2
        evidence = self.run / "implementation-check.log"
        evidence.write_text("Retained greeting implementation.\n")
        events = self.run / "completed-terra.jsonl"
        events.write_text('{"type":"turn.completed"}\n')
        report = {
            **envelope(self.state),
            "summary": "Implementation ready for the Validator",
            "changed_files": ["greet.py"],
            "commands_run": [],
            "results": ["Ready"],
            "remaining_risks": [],
            "evidence_refs": [str(evidence)],
        }
        record = {
            "role": "builder",
            "stage": "builder",
            "events": str(events),
            "output": str(self.run / "completed-terra.json"),
            "schema": str(self.run / "terra.schema.json"),
            "changed_files": ["greet.py"],
            "after_ref": str(self.run / "completed-terra.after.json"),
            "source_revision": support.snapshot(self.root)["revision"],
            # #1825 renews the streak only for a valid saved checkpoint, as run_role records it.
            "exit_code": 0,
            "finished_at": support.now(),
        }
        with self.assertRaises(support.Paused) as caught:
            runner.apply_result(
                self.state, "builder", {**report, "contract_hash": "stale"}, record, self.root, self.run
            )
        self.assertEqual("PAUSED_STALE_GOAL", caught.exception.status)
        self.assertEqual(2, self.state["consecutive_timeout_recoveries"])
        runner.apply_result(self.state, "builder", report, record, self.root, self.run)
        self.assertEqual(0, self.state.get("consecutive_timeout_recoveries", 0))
        self.assertEqual("tester", self.state["next_stage"])

    def test_zero_no_progress_budget_does_not_disable_aggregate_recovery_ceiling(self):
        self.state["settings"]["limits"]["no_progress_batches"] = 0
        self.state["consecutive_timeout_recoveries"] = 0
        self.state["automatic_recoveries_since_resume"] = runner.MAX_AUTOMATIC_RECOVERIES
        with self.assertRaises(support.Paused):
            runner.timeout_recovery_guard(self.state)

    def test_disabled_no_progress_threshold_allows_launch_below_recovery_ceiling(self):
        self.state["settings"]["limits"]["no_progress_batches"] = 0
        for used in range(runner.MAX_AUTOMATIC_RECOVERIES):
            with self.subTest(recoveries=used):
                self.state["consecutive_timeout_recoveries"] = used
                self.state["automatic_recoveries_since_resume"] = used
                runner.timeout_recovery_guard(self.state)

    def test_positive_no_progress_threshold_still_stops_consecutive_recoveries(self):
        self.state["settings"]["limits"]["no_progress_batches"] = 2
        self.state["automatic_recoveries_since_resume"] = 1
        self.state["consecutive_timeout_recoveries"] = 2
        with self.assertRaises(support.Paused):
            runner.timeout_recovery_guard(self.state)

    def test_legacy_recent_failures_seed_the_aggregate_recovery_ceiling(self):
        self.state["settings"]["limits"]["no_progress_batches"] = 0
        self.state.update(
            consecutive_timeout_recoveries=1,
            no_progress_batches=runner.MAX_AUTOMATIC_RECOVERIES,
            automatic_timeout_recoveries=[{}, {}],
            automatic_permission_recoveries=[{}],
        )
        self.assertEqual(runner.MAX_AUTOMATIC_RECOVERIES, runner.recovery_count(self.state))
        with self.assertRaises(support.Paused):
            runner.timeout_recovery_guard(self.state)

    def test_unchanged_builder_batches_never_exhaust_the_recovery_budget(self):
        # #511: no recovery ran, so the guard must leave the run to its own no-progress stop.
        self.state.update(no_progress_batches=runner.MAX_AUTOMATIC_RECOVERIES)
        self.state.pop("automatic_recoveries_since_resume", None)
        self.assertEqual(0, runner.recovery_count(self.state))
        runner.timeout_recovery_guard(self.state)

    def test_legacy_recovered_history_does_not_exhaust_a_new_resume(self):
        self.state.update(
            consecutive_timeout_recoveries=0,
            no_progress_batches=0,
            automatic_timeout_recoveries=[{}, {}, {}],
            automatic_recoveries_since_resume=0,
        )
        self.assertEqual(0, runner.recovery_count(self.state))
        runner.timeout_recovery_guard(self.state)

    def test_explicit_resume_reopens_recovery_budget_without_erasing_history(self):
        self.start_task()
        history = [{"attempt_id": f"001/review_completion-{n:02d}", "timeout_kind": "idle"} for n in range(1, 4)]
        self.state.update(
            status="PAUSED_TIMEOUT_RECOVERY",
            next_stage="review_completion",
            consecutive_timeout_recoveries=3,
            no_progress_batches=3,
            automatic_timeout_recoveries=copy.deepcopy(history),
            automatic_recoveries_since_resume=3,
        )
        calls = []

        def inspect(**kwargs):
            calls.append(kwargs["role"])
            self.assertEqual(0, kwargs["state"].get("consecutive_timeout_recoveries", 0))
            # One granted recovery reopens exactly one slot of the spent allowance.
            self.assertEqual(runner.MAX_AUTOMATIC_RECOVERIES - 1, runner.recovery_count(kwargs["state"]))
            self.assertEqual(history, kwargs["state"]["automatic_timeout_recoveries"])
            raise support.Paused("PAUSED_TEST", "Offline dispatch inspected")

        self.assertEqual(2, test_goals.GoalTests.invoke(self, role=inspect))
        self.assertEqual([], calls, "An ordinary invocation must retain the exhausted pause")
        self.assertEqual(3, self.state["consecutive_timeout_recoveries"])
        published = assert_operational_wait(self, self.state, "PAUSED_TIMEOUT_RECOVERY")
        self.assertIn("--grant-recovery", published["request"]["decision_needed"])
        # Since 009c8b8 a plain explicit resume no longer replenishes the spent
        # allowance; only an audited --grant-recovery N does.
        self.assertEqual(2, test_goals.GoalTests.invoke(self, "--resume-paused", role=inspect))
        self.assertEqual([], calls, "A plain resume must not restore a spent recovery allowance")
        self.assertEqual(3, self.state["consecutive_timeout_recoveries"])
        self.assertEqual(2, test_goals.GoalTests.invoke(self, "--resume-paused", "--grant-recovery", "1", role=inspect))
        self.assertEqual(["task_planner"], calls)
        self.assertEqual(history, self.state["automatic_timeout_recoveries"])
        grants = [event for event in self.state["user_events"] if event.get("kind") == "recovery_grant"]
        self.assertEqual([1], [event["amount"] for event in grants])
        self.assertEqual(published["request_id"], grants[0]["request_id"])

    def fake_provider(self, script):
        """Install a workspace-local offline executable, never the user's Codex."""
        directory = self.root / ".autocode" / "fixture-bin"
        directory.mkdir(parents=True, exist_ok=True)
        binary = directory / "codex"
        binary.write_text(f"#!{sys.executable}\n" + textwrap.dedent(script))
        binary.chmod(0o755)
        return patch.dict(os.environ, {"PATH": str(directory) + os.pathsep + os.environ["PATH"]})

    def real_terra(self):
        schema = self.run / "fixture-terra.schema.json"
        support.atomic_json(
            schema, goals.role_schema(support.read(runner.SCHEMA_DIR / "v2/terra-report.schema.json"), "builder")
        )
        return runner.run_role(
            role="builder",
            prompt="Complete the greeting fixture",
            sandbox="workspace-write",
            workspace=self.root,
            run_dir=self.run,
            state=self.state,
            schema=schema,
            model="offline-fixture",
            allow_write=True,
            dry_run=False,
        )

    def test_real_quiet_tool_survives_idle_deadline_and_completed_writer_routes_sol(self):
        self.start_task()
        self.state["settings"]["limits"].update(
            stage_timeout_seconds=4, idle_timeout_seconds=0.4, tool_timeout_seconds=2
        )
        report = {
            **envelope(self.state),
            "summary": "Greeting implementation tested",
            "changed_files": ["greet.py"],
            "commands_run": [],
            "results": ["Hello, fixture"],
            "remaining_risks": [],
            "evidence_refs": ["event:quiet-check"],
            "addressed_requirements": ["Greet a valid name"],
            "untested_behavior": ["Invalid input"],
            "recommended_checks": ["The Validator executes valid and invalid CLI cases"],
        }
        script = f"""
            import json, shlex, subprocess, sys
            from pathlib import Path
            def event(value):
                print(json.dumps(value), flush=True)
            sys.stdin.read()
            event({{'type': 'thread.started', 'thread_id': 'offline-tool-session'}})
            Path('greet.py').write_text("print('Hello, fixture')\\n")
            command = [sys.executable, '-c', "import time; time.sleep(.85); exec(open('greet.py').read())"]
            text = shlex.join(command)
            event({{'type': 'item.started', 'item': {{'id': 'quiet-check', 'type': 'command_execution',
                'command': text, 'status': 'in_progress'}}}})
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            event({{'type': 'item.completed', 'item': {{'id': 'quiet-check', 'type': 'command_execution',
                'command': text, 'exit_code': result.returncode, 'aggregated_output': result.stdout}}}})
            report = {report!r}
            report['commands_run'] = [text]
            Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(report))
            event({{'type': 'turn.completed', 'usage': {{'input_tokens': 1, 'output_tokens': 1}}}})
        """
        with self.fake_provider(script), contextlib.redirect_stdout(io.StringIO()):
            value, record = self.real_terra()
        self.assertGreaterEqual(record["duration_seconds"], 0.8)
        self.assertFalse(record["timed_out"])
        self.assertEqual(0, record["exit_code"])
        command = next(row["item"] for row in support.events(record["events"]) if row.get("type") == "item.completed")
        self.assertEqual(0, command["exit_code"])
        self.assertEqual("Hello, fixture\n", command["aggregated_output"])
        self.assertEqual([], runner.processes.live_processes(record.get("processes", [])))
        runner.apply_result(self.state, "builder", value, record, self.root, self.run)
        self.assertEqual("tester", self.state["next_stage"])
        self.assertEqual("Hello, fixture", (self.root / "greet.py").read_text().split("'")[1])
        self.assertNotIn("active_stage", self.state)

    def test_real_stalled_writer_preserves_partial_work_and_recovers_once_to_astra(self):
        self.start_task()
        self.state["settings"]["limits"].update(
            stage_timeout_seconds=4, idle_timeout_seconds=0.4, tool_timeout_seconds=2
        )
        script = """
            import json, sys, time
            from pathlib import Path
            sys.stdin.read()
            Path('greet.py').write_text("print('retained partial greeting')\\n")
            print(json.dumps({'type': 'thread.started', 'thread_id': 'offline-stalled-session'}), flush=True)
            time.sleep(30)
        """
        with (
            self.fake_provider(script),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(support.Paused) as caught,
        ):
            self.real_terra()
        self.assertEqual("PAUSED_PROVIDER_TIMEOUT", caught.exception.status)
        record = support.read(self.run / "state.json")["active_stage"]
        self.assertEqual("idle", record["timeout_kind"])
        self.assertTrue(record["timeout_reason"])
        self.assertTrue(record["timed_out"])
        self.assertTrue(record["accounted"])
        self.assertNotEqual(0, record["exit_code"])
        self.assertEqual([], runner.processes.live_processes(record.get("processes", [])))
        source = self.root / "greet.py"
        original = source.read_bytes()
        self.assertTrue(runner.automatically_recover_timed_out_stage(self.state, self.run, self.root, caught.exception))
        self.assertFalse(
            runner.automatically_recover_timed_out_stage(self.state, self.run, self.root, caught.exception)
        )
        self.assertEqual(original, source.read_bytes())
        self.assertEqual("review_completion", self.state["next_stage"])
        self.assertEqual(1, len(self.state["automatic_timeout_recoveries"]))
        archived = [row for row in self.state["stages"] if not row.get("runner_owned")][-1]
        self.assertEqual("idle", archived["timeout_kind"])
        self.assertEqual("during_reasoning_or_output", archived.get("stall_phase"))
        self.assertTrue(Path(archived["events"]).is_file())
        self.assertNotIn("active_stage", self.state)

    def test_before_first_event_dead_stream_fails_fast_and_records_stall_phase(self):
        import autocode_usage as usage

        self.start_task()
        self.state["settings"]["limits"].update(
            stage_timeout_seconds=60, idle_timeout_seconds=10, tool_timeout_seconds=10
        )
        self.state["settings"]["budget_origins"] = {"idle_timeout_seconds": "runner_default"}
        script = """
            import sys, time
            sys.stdin.read()
            time.sleep(30)
        """
        with (
            patch.object(runner.idle_policy, "FIRST_EVENT_SECONDS", 0.3),
            self.fake_provider(script),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(support.Paused) as caught,
        ):
            self.real_terra()
        self.assertEqual("PAUSED_PROVIDER_TIMEOUT", caught.exception.status)
        record = support.read(self.run / "state.json")["active_stage"]
        self.assertEqual("idle", record["timeout_kind"])
        self.assertIn("initial stream event", record["timeout_reason"])
        self.assertEqual("before_first_event", record.get("stall_phase"))
        self.assertEqual(0, record.get("completed_tool_calls"))
        self.assertIsNone(record.get("last_event_offset_seconds"))
        attempt = usage.accounting(support.read(self.run / "state.json"))["attempts"][-1]
        self.assertEqual("before_first_event", attempt.get("stall_phase"))
        self.assertEqual(0, attempt.get("completed_tool_calls"))
        self.assertIsNone(attempt.get("last_event_offset_seconds"))


if __name__ == "__main__":
    unittest.main()

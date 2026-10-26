"""Recovery buttons call existing CLI controls with fresh exact-pause identity."""

import unittest
from unittest.mock import patch

try:
    from . import test_registry_interventions as fixtures
except ImportError:
    import test_registry_interventions as fixtures

from autocode_control_tokens import private_command


class RecoveryActionTests(unittest.TestCase):
    setUp = fixtures.RegistryInterventionTests.setUp
    save = fixtures.RegistryInterventionTests.save
    write_state = fixtures.RegistryInterventionTests.write_state
    console_new = fixtures.RegistryInterventionTests.console_new
    action = fixtures.RegistryInterventionTests.action

    def prepare(self, kind, **fields):
        # A card action names its exact flags in args (#730); the dashboard never maps kinds.
        action = {"id": kind, "kind": kind, "label": "Reviewed recovery", "effect": "Keeps history", **fields}
        self.card = {"version": 1, "status": "PAUSED_REQUESTED", "token": "recovery-v1:exact", "actions": [action]}
        self.status.update(status="PAUSED_REQUESTED", view={"recovery": self.card})
        self.save("status.json", self.status)
        return {"recovery_token": self.card["token"], "recovery_action": kind}

    def check(self, kind, tail, **fields):
        payload = self.prepare(kind, args=tail, **fields)
        before = (self.run / "state.json").read_bytes()
        with patch.object(self.console.pool, "submit", wraps=self.console.pool.submit) as submit:
            result = self.action("recover_pause", **payload)
        expected = ["--no-chat", "--expected-recovery-token", self.card["token"], *tail]
        expected, private_env = private_command(expected)
        self.assertEqual(expected, result["command"][-len(expected) :])
        self.assertEqual(private_env, submit.call_args.args[-1])
        self.assertNotIn(self.card["token"], result["command"])
        self.assertEqual(before, (self.run / "state.json").read_bytes())

    def test_resume_button_uses_existing_resume(self):
        self.check("resume", ["--resume-paused"])

    def test_abandon_button_does_not_resume(self):
        self.check("abandon", ["--abandon-stage", "004/builder-02"], attempt_id="004/builder-02")
        self.assertNotIn("--resume-paused", self.console.action_log(self.workspace, self.run)[0]["command"])

    def test_quota_card_from_public_view_maps_only_the_saved_attempt(self):
        import autocode_run_view as run_view

        self.state.update(
            status="PAUSED_BUDGET",
            stop_reason="Quota restored; inspect the uncertain attempt.",
            active_stage={"iteration": 4, "stage": "terra", "output": str(self.run / "builder-02.json")},
            settings={
                "roles": {"terra": {"model": "p/builder", "reasoning_effort": "high", "model_pinned": True}},
                "limits": {"iteration_ceiling": 0},
            },
            goal_contract={"revision": 1, "hash": "approved"},
            failure_history={"quota": {"count": 1, "attempts": ["004/builder-02"]}},
            stages=[{"stage": "astra_plan", "iteration": 3, "finished_at": "earlier"}],
        )
        self.write_state()
        public = run_view.view(self.state)
        self.status.update(status=self.state["status"], active_stage=self.state["active_stage"], view=public)
        self.save("status.json", self.status)
        before = (self.run / "state.json").read_bytes()
        card = self.console.view(self.workspace, self.run)["interventions"]["recovery"]
        self.assertEqual(public["recovery"], card)
        self.assertEqual(["inspect", "abandon", "feedback"], [row["kind"] for row in card["actions"]])
        self.assertEqual("004/builder-02", public["needs"]["abandon_stage"])
        with patch.object(self.console.pool, "submit", wraps=self.console.pool.submit) as submit:
            result = self.action(
                "recover_pause",
                recovery_token=card["token"],
                recovery_action="abandon",
                attempt_id="999/injected",
                args=["--resume-paused", "--approve-goal", "injected"],
            )
        expected = ["--no-chat", "--expected-recovery-token", "-", "--abandon-stage", "004/builder-02"]
        self.assertEqual(expected, result["command"][-len(expected) :])
        self.assertEqual({"AUTOCODE_EXPECTED_RECOVERY_TOKEN": card["token"]}, submit.call_args.args[-1])
        self.assertNotIn("--resume-paused", result["command"])
        self.assertNotIn("--approve-goal", result["command"])
        self.assertNotIn("999/injected", result["command"])
        self.assertEqual(before, (self.run / "state.json").read_bytes())

    def test_an_edited_source_pause_continues_by_the_runner_named_acceptance(self):
        # The dashboard learns this action only from the public card; it has no table of kinds (#730).
        import autocode_run_view as run_view

        self.state.update(
            status="PAUSED_STALE_HANDOFF",
            stop_reason="Repair diagnosis needs the current reviewed source and task",
            resolution_request={"source_revision": "reviewed-source"},
            goal_contract={"revision": 1, "hash": "approved"},
        )
        self.write_state()
        public = run_view.view(self.state)
        self.status.update(status=self.state["status"], view=public)
        self.save("status.json", self.status)
        card = self.console.view(self.workspace, self.run)["interventions"]["recovery"]
        self.assertEqual(public["recovery"], card)
        with patch.object(self.console.pool, "submit", wraps=self.console.pool.submit) as submit:
            result = self.action("recover_pause", recovery_token=card["token"], recovery_action="accept_source_edit")
        expected = ["--no-chat", "--expected-recovery-token", "-", "--resume-paused", "--accept-source-edit"]
        self.assertEqual(expected, result["command"][-len(expected) :])
        self.assertEqual({"AUTOCODE_EXPECTED_RECOVERY_TOKEN": card["token"]}, submit.call_args.args[-1])

    def test_builder_button_names_only_current_members(self):
        self.check(
            "retry_builder",
            ["--resume-paused", "--retry-builder", "M1", "--retry-builder", "M2"],
            milestone_ids=["M1", "M2"],
        )

    def test_job_button_passes_its_specific_saved_failure_token(self):
        self.check(
            "retry_job",
            ["--resume-paused", "--retry-failed-stage", "--job-retry-token", "job-exact"],
            job_retry_token="job-exact",
        )

    def test_report_button_names_its_exact_attempt(self):
        self.check(
            "retry_report", ["--resume-paused", "--retry-report", "007/validator-01"], attempt_id="007/validator-01"
        )

    def test_repeated_failure_button_grants_an_explicit_attempt(self):
        self.check("retry_failed_stage", ["--resume-paused", "--retry-failed-stage"])

    def test_an_action_the_dashboard_has_never_seen_runs_exactly_its_named_flags(self):
        self.check("retry_future", ["--resume-paused", "--retry-future", "M4"])

    def test_an_action_without_well_formed_flags_runs_nothing(self):
        for args in (
            None,
            [],
            "--resume-paused",
            ["resume"],
            ["--resume-paused", ""],
            ["--resume-paused", 3],
            ["--resume-paused", "--run-dir", "/elsewhere"],
            ["--resume-paused", "--workspace", "/elsewhere"],
            ["--resume-paused", "--expected-recovery-token", "forged"],
        ):
            with self.subTest(args=args):
                payload = self.prepare("resume", **({} if args is None else {"args": args}))
                with self.assertRaisesRegex(ValueError, "does not run"):
                    self.action("recover_pause", **payload)
        self.assertEqual([], self.console.action_log(self.workspace, self.run))

    def test_stale_token_action_removal_unavailable_status_and_nonexecution_actions_refuse(self):
        payload = self.prepare("resume", args=["--resume-paused"])
        self.console.view(self.workspace, self.run)  # prime cached status
        self.card["token"] = "new-token"
        self.save("status.json", self.status)
        with self.assertRaisesRegex(ValueError, "pause changed"):
            self.action("recover_pause", **payload)
        payload["recovery_token"] = "new-token"
        self.card["actions"] = []
        self.save("status.json", self.status)
        with self.assertRaisesRegex(ValueError, "no longer offered"):
            self.action("recover_pause", **payload)
        payload = self.prepare("feedback")
        with self.assertRaisesRegex(ValueError, "does not run"):
            self.action("recover_pause", **payload)
        with patch.object(self.console, "_json_command", return_value=(None, {"message": "status unavailable"})):
            with self.assertRaisesRegex(ValueError, "status unavailable"):
                self.action("recover_pause", **payload)
        self.assertEqual([], self.console.action_log(self.workspace, self.run))

    def test_busy_workspace_refuses_duplicate_recovery_and_client_cannot_supply_flags(self):
        payload = self.prepare("retry_builder", milestone_ids=["M1"], args=["--resume-paused", "--retry-builder", "M1"])
        self.console.workspace_busy.add(str(self.workspace))
        with self.assertRaisesRegex(ValueError, "already queued or running"):
            self.action("recover_pause", **payload)
        self.console.workspace_busy.clear()
        result = self.action(
            "recover_pause",
            **payload,
            milestone_ids=["M999"],
            job_retry_token="injected",
            args=["--approve-goal", "anything"],
        )
        self.assertEqual(["--resume-paused", "--retry-builder", "M1"], result["command"][-3:])
        self.assertNotIn("M999", result["command"])
        self.assertNotIn("--approve-goal", result["command"])

    def bound_pause(self, held=False):
        """A run stopped at its cost cap, published as the CLI's public status view (#2044).

        ``held``: AutoResolver's operational request holds it, so next_command is a plain resume and
        the command_inputs row names the raise as its own command.
        """
        import autocode as runner
        import autocode_run_view as run_view
        import autocode_support as support

        self.state.update(
            status="PAUSED_COST_LIMIT",
            stop_reason="Saved limit reached",
            run_dir=str(self.run),
            stages=[],
            history=[],
            iteration=2,
            next_stage="terra",
            phase="EXECUTING",
            settings={"limits": {"max_cost_usd": 2.0}, "budget_origins": {"max_cost_usd": "user_explicit"}, "roles": {}},
            resolver={},
        )
        if held:
            error = support.Paused("PAUSED_COST_LIMIT", self.state["stop_reason"])
            self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(runner, self.state, self.run, error))
        self.write_state()
        public = run_view.view(self.state, run_dir=self.run, workspace=self.workspace)
        self.status.update(status=public["status"], view=public)
        self.save("status.json", self.status)
        return public

    def test_a_number_typed_at_a_bound_pause_takes_the_command_the_view_names_for_it(self):
        for held in (False, True):
            with self.subTest(held=held):
                self.console.pending.clear()
                self.console.workspace_busy.clear()
                public = self.bound_pause(held)
                row = public["needs"]["command_inputs"][0]
                self.assertEqual(held, "command" in row)
                token = public["recovery"]["token"]
                before = (self.run / "state.json").read_bytes()
                with patch.object(self.console.pool, "submit") as submit:
                    result = self.action(
                        "set_pause_number",
                        recovery_token=token,
                        input_id="max_cost_usd",
                        value="5",
                        args=["--approve-goal", "injected"],
                    )
                expected = ["--no-chat", "--resume-paused", "--max-cost-usd", "5", "--expected-recovery-token", "-"]
                self.assertEqual(expected, result["command"][-len(expected) :])
                self.assertEqual({"AUTOCODE_EXPECTED_RECOVERY_TOKEN": token}, submit.call_args.args[-1])
                self.assertNotIn(token, result["command"])
                self.assertNotIn("--approve-goal", result["command"])
                self.assertEqual(before, (self.run / "state.json").read_bytes())

    def test_a_pause_number_out_of_bounds_stale_or_unoffered_runs_nothing(self):
        public = self.bound_pause(held=True)
        token = public["recovery"]["token"]
        for fields, message in (
            ({"value": "2"}, "whole number from 3"),
            ({"value": "3.5"}, "whole number from 3"),
            ({"value": ""}, "whole number from 3"),
            ({"value": 5}, "whole number"),
            ({"input_id": "max_seconds"}, "limit this pause offers changed"),
            ({"recovery_token": "old"}, "pause changed"),
        ):
            with self.subTest(fields=fields):
                payload = {"recovery_token": token, "input_id": "max_cost_usd", "value": "5", **fields}
                with self.assertRaisesRegex(ValueError, message):
                    self.action("set_pause_number", **payload)
        moved = dict(public)
        moved["needs"] = {**public["needs"]}
        moved["needs"]["command_inputs"] = [
            {**public["needs"]["command_inputs"][0], "command": public["needs"]["command_inputs"][0]["command"].replace(
                str(self.run), "/elsewhere/run"
            )}
        ]
        self.status.update(view=moved)
        self.save("status.json", self.status)
        with self.assertRaisesRegex(ValueError, "not this run"):
            self.action("set_pause_number", recovery_token=token, input_id="max_cost_usd", value="5")
        del moved["needs"]["command_inputs"]
        self.save("status.json", self.status)
        with self.assertRaisesRegex(ValueError, "offers no number"):
            self.action("set_pause_number", recovery_token=token, input_id="max_cost_usd", value="5")
        self.assertEqual([], self.console.action_log(self.workspace, self.run))

    def test_stale_running_worker_gets_inspection_but_no_execution_token(self):
        from dashboard_recovery import projection

        for check in (False, True):
            with self.subTest(runner_check=check):
                status = {
                    "status": "RUNNING",
                    "stale": True,
                    "view": {"recovery": None, "runner_check": {"stage": "regression_proof"} if check else None},
                    "active_stage": None if check else {"stage": "terra"},
                    "runner_check_workers": {"checked": True, "alive": False} if check else None,
                }
                card = projection(status)
                self.assertIsNone(card["token"])
                self.assertEqual("RUNNING", card["status"])
                self.assertEqual(["inspect", "feedback"], [row["kind"] for row in card["actions"]])
                for field in ("what_happened", "retained", "actions"):
                    self.assertTrue(card[field])
                self.status.update(status)
                self.save("status.json", self.status)
                with self.assertRaisesRegex(ValueError, "pause changed"):
                    self.action("recover_pause", recovery_token=None, recovery_action="resume")
        self.assertIsNone(projection({"status": "RUNNING", "stale": False, "view": {"recovery": None}}))
        self.assertEqual([], self.console.action_log(self.workspace, self.run))

    def test_projected_card_is_from_supported_status_and_absent_on_inspection_failure(self):
        self.prepare("resume", args=["--resume-paused"])
        self.state["recovery"] = {"token": "private-stale"}
        self.write_state()
        view = self.console.view(self.workspace, self.run)
        self.assertEqual(self.card, view["interventions"]["recovery"])
        self.console.status_cache.clear()
        with patch.object(
            self.console, "_json_command", return_value=(None, {"message": "offline", "uncertain": False})
        ):
            self.assertIsNone(self.console.view(self.workspace, self.run)["interventions"]["recovery"])

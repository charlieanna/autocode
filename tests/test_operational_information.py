"""Corrective information on an operational pause is re-evaluated once by AutoResolver (#486).

Before #486 an accepted `--resolver-response provide_information` was retained and never
evaluated: every resume repeated the same hold. Real CLI processes with the fake provider;
no sleeps, no live models. A launch is observed through the registry launch probe. The control
table itself is tested without processes in test_operational_information_controls.
"""

import copy
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_source_scope as source_scope
import autocode_support as support
from harness.conformance import ConformanceDriver

from . import test_subprocess
from .operational_fixtures import save_legacy_checkpoint


class OperationalInformationCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def checkpoint(self, status, reason, edit=None, *, legacy=False, settling=False, **fields):
        """A run AutoResolver stopped with a published operational_exhaustion request."""
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.env["AUTOCODE_FIXTURE_QUOTA_STAGE"] = "review_completion"
        self.launch(["Build greeting", "--chat"], 2, answers="CLI\nyes\n")
        run, paused = self.saved()
        self.launch(["--run-dir", str(run), "--abandon-stage", runner.attempt_id(paused["active_stage"])], 0)
        self.run, stopped = self.saved()
        if edit:
            edit(stopped)
        stopped.update(status=status, stop_reason=reason, **fields)
        cached = None
        if legacy:
            budget = runner.resolver_runtime.disposition.budget(stopped, status)
            receipt = runner.resolver_runtime._operational_receipt(
                stopped,
                self.run,
                "hold",
                reason,
                {"status": status, "stage": stopped.get("next_stage"), "budget": budget},
            )
            human = runner.resolver_human
            human.queue(
                stopped,
                "operational_exhaustion",
                {"stage": stopped.get("next_stage"), "pause_status": status, "budget": budget},
                request={
                    "kind": "blocker",
                    "discovered": reason,
                    "impact": "Recovery exhausted",
                    "decision_needed": "Provide corrective information",
                    "options": ["Provide corrective information", "Leave paused"],
                },
                evidence={"resolver_receipt_id": receipt},
                next_stage=stopped.get("next_stage"),
            )
            proposal = copy.deepcopy(stopped[human.PRIVATE])
            identity = {
                "version": human.VERSION,
                "issuer": "resolver",
                "binding": human._binding(stopped),
                "proposal": proposal,
                "action": "escalate",
                "reason": "Legacy operational hold",
            }
            stopped["resolver"].setdefault("human_escalations", {})
            human._publish(stopped, identity)
            # Only this fixture reconstructs the historical public offer. The
            # new CLI is always invoked with its real policy, and must refuse it.
            with (
                patch.object(runner.resolver_runtime.disposition, "refused", return_value=False),
                patch.object(runner.resolver_runtime.disposition, "presentation", return_value=None),
            ):
                cached = runner.run_view.view(stopped, run_dir=self.run, workspace=self.project)
        else:
            self.assertTrue(
                runner.resolver_runtime.record_operational_exhaustion(
                    runner, stopped, self.run, support.Paused(status, reason)
                )
            )
        if legacy:
            save_legacy_checkpoint(stopped, self.run)
        else:
            runner.write_json(self.run / "state.json", stopped)
        view = self.status()
        if settling:
            self.assert_settling(view)
        elif cached is not None or runner.resolver_runtime.disposition.presentation(stopped):
            self.assert_blocked(view)
        else:
            self.assertEqual("operational_exhaustion", view["needs"]["resolver_scope"])
        return cached if cached is not None else view

    def assert_blocked(self, view):
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["next_command"])
        self.assertIsNone(view["chat"]["input"])
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertFalse(view["done"])

    def assert_settling(self, view):
        self.assertEqual({"kind": "continue", "action": "--resume-paused"}, view["needs"])
        self.assertIsNone(view["chat"]["input"], "Settling spent recovery asks no machinery question")
        self.assertFalse(view["done"])

    def refuse_cached_information(self, view, text="The environment was repaired"):
        before = (self.run / "state.json").read_bytes()
        code, launched, output = self.invoke(
            "--resolver-request",
            view["needs"]["resolver_request_id"],
            "--resolver-token",
            view["needs"]["resolver_token"],
            "--resolver-response",
            "provide_information",
            "--resolver-message",
            text,
        )
        self.assertEqual((2, False), (code, launched), output)
        self.assertEqual(before, (self.run / "state.json").read_bytes())
        self.assert_blocked(self.status())
        self.assertEqual([], self.evaluations())

    def issue_checkpoint(self, edit=None, **fields):
        """#486's run: two operational recoveries, then PAUSED_RESOLVER_OPERATIONAL."""
        recoveries = [{"stage": "builder", "instruction": "workspace paths only"} for _ in range(2)]
        return self.checkpoint(
            "PAUSED_RESOLVER_OPERATIONAL",
            "AutoResolver exhausted its operational recoveries",
            edit,
            legacy=True,
            automatic_permission_recoveries=recoveries,
            **fields,
        )

    def status(self):
        return json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)["view"]

    def inform(self, view, text, *, token=None, expected=0):
        return self.launch(
            [
                "--run-dir",
                str(self.run),
                "--resolver-request",
                view["needs"]["resolver_request_id"],
                "--resolver-token",
                token or view["needs"]["resolver_token"],
                "--resolver-response",
                "provide_information",
                "--resolver-message",
                text,
                "--no-chat",
            ],
            expected,
        )

    def invoke(self, *args, env=None):
        """One CLI process; returns (exit code, whether any provider launched, output)."""
        probe = self.root / f"launch-{len(list(self.root.glob('launch-*')))}.jsonl"
        result = subprocess.run(
            [*self.entry, "--workspace", str(self.project), "--run-dir", str(self.run), *args, "--no-chat"],
            cwd=self.root,
            env={**self.env, "AUTOCODE_REGISTRY_LAUNCH_PROBE": str(probe), **(env or {})},
            capture_output=True,
            text=True,
            timeout=240,
        )
        return result.returncode, probe.exists(), result.stdout + result.stderr

    def resume(self, *flags, env=None):
        return self.invoke("--resume-paused", *flags, env=env)

    def evaluations(self):
        return sorted((self.run / "resolver").glob("information-*.json"))

    @staticmethod
    def pending_repair(attempts):
        """The Builder's last report failed validation; `attempts` of its 2 report-only repairs are used."""

        def edit(state):
            original = copy.deepcopy(
                next(
                    row
                    for row in reversed(state["stages"])
                    if row.get("stage") == "builder" and not row.get("report_only")
                )
            )
            original["rejection_reason"] = "Report is missing summary"
            state["settings"]["report_repair"] = {"max_attempts": 2}
            state["pending_report_repair"] = {
                "attempts": attempts,
                "contract_hash": state["goal_contract"]["hash"],
                "pins": {},
                "error": "Report is missing summary",
                "original": original,
            }
            state.update(next_stage="builder")
            state.setdefault("sessions", {})["builder"] = "spent-builder-session"

        return edit

    def test_cached_chat_offer_cannot_submit_machinery_information(self):
        cached = self.issue_checkpoint()
        self.assertEqual("blocked", self.status()["recovery"]["category"])
        self.assertIsNone(self.status()["chat"]["input"], "internal recovery is not a person-facing question")
        client = object.__new__(ConformanceDriver)
        client.project, client.run_dir = self.project, self.run
        words, env, _ = client.message_command(cached, "The operator inspected the blocked run")
        before = (self.run / "state.json").read_bytes()
        code, launched, output = self.invoke(*words, env=env)
        self.assertEqual((2, False), (code, launched), output)
        self.assertEqual(before, (self.run / "state.json").read_bytes())
        self.refuse_cached_information(cached)

    def test_accepted_information_is_reevaluated_exactly_once(self):
        def explicit_cap(state):
            state["settings"]["limits"]["max_seconds"] = 7200
            state["settings"].setdefault("budget_origins", {})["max_seconds"] = "user_explicit"
            state["active_seconds"] = 7300

        view = self.checkpoint("PAUSED_TIME_LIMIT", "Saved active-time limit reached at stage boundary", explicit_cap)
        result = self.inform(view, "The operator inspected the run; the approved scope is unchanged")
        self.assertIn("re-evaluates the response once at the next autocode resume", result.stdout)
        view = self.status()
        self.assertEqual("pending", view["information_review"]["status"])
        self.assertEqual("--resume-paused", view["needs"]["action"])
        # Only an explicit resume consumes it; a plain relaunch neither evaluates nor launches.
        code, launched, output = self.invoke()
        self.assertEqual((2, False), (code, launched), output)
        self.assertIn("re-evaluates it once", output)
        self.assertEqual("pending", self.status()["information_review"]["status"])
        self.assertEqual([], self.evaluations())
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        first = self.status()["information_review"]
        self.assertEqual(("held", "hold"), (first["status"], first["decision"]))
        # Repeated resumes and a fresh process are the same decision, never a second evaluation.
        for args in (("--resume-paused",), ("resume",), ("--resume-paused",)):
            code, launched, output = self.invoke(*args)
            self.assertEqual((2, False), (code, launched), output)
            self.assertIn(first["reason"], output)
            self.assertEqual(first, self.status()["information_review"])
        self.assertEqual(1, len(self.evaluations()))

    def test_named_finite_total_keeps_other_limits_and_unapproved_plan(self):
        # A real CLI creates and displays the plan, but nobody approves it.
        self.launch(["Build greeting", "--no-chat"], 2)
        self.run, _ = self.saved()
        self.launch(["--run-dir", str(self.run), "--answer", "Q1=CLI", "--resume-paused", "--no-chat"], 2)
        self.assertEqual("approve_plan", self.status()["needs"]["kind"])
        plan = self.status()["displayed_plan"]
        # Only this owned fixture is seeded at the exhausted active-time boundary.
        _, state = self.saved()
        state["settings"]["limits"].update(
            max_seconds=2700,
            stage_timeout_seconds=600,
            idle_timeout_seconds=300,
            tool_timeout_seconds=600,
            max_cost_usd=2,
        )
        state["settings"].setdefault("budget_origins", {})["max_seconds"] = "user_explicit"
        state.update(status="PAUSED_TIME_LIMIT", active_seconds=2800, stop_reason="Saved active-time limit reached")
        self.assertTrue(
            runner.resolver_runtime.record_operational_exhaustion(
                runner, state, self.run, support.Paused(state["status"], state["stop_reason"])
            )
        )
        runner.write_json(self.run / "state.json", state)
        initial = json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)["settings"]
        self.inform(self.status(), "Use total 5400 seconds; preserve every other setting and require plan approval")
        pending = self.status()
        self.assertEqual("pending", pending["information_review"]["status"])
        self.assertNotIn("command_inputs", pending["needs"])
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        held = self.status()
        self.assertEqual("held", held["information_review"]["status"])
        self.assertEqual(2801, held["needs"]["command_inputs"][0]["minimum"])
        unchanged = json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)["settings"]
        self.assertEqual(initial, unchanged, "Corrective information and its review grant no setting change")
        client = object.__new__(ConformanceDriver)
        client.project, client.run_dir = self.project, self.run
        words, env, _ = client.named_command(held)
        selected = client.decide(held["needs"], words, command_inputs={"max_seconds": "5400"}, view=held, env=env)
        bad = {**env, "AUTOCODE_EXPECTED_RECOVERY_TOKEN": env["AUTOCODE_EXPECTED_RECOVERY_TOKEN"] + "stale"}
        code, launched, output = self.invoke(*selected, env=bad)
        self.assertEqual((2, False), (code, launched), output)
        self.assertIn("saved pause changed", output)
        code, launched, output = self.invoke(*selected, env=env)
        self.assertEqual((2, False), (code, launched), output)
        current = self.status()
        self.assertEqual("approve_plan", current["needs"]["kind"])
        self.assertEqual(plan, current["displayed_plan"])
        self.assertFalse((self.project / "greet.py").exists(), "A total-time decision cannot approve the plan")
        after = json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)["settings"]
        expected = copy.deepcopy(initial)
        expected["limits"]["max_seconds"] = 5400
        self.assertEqual(expected, after, "Only the explicitly selected total changes")

    def test_stale_or_replayed_information_launches_nothing(self):
        cached = self.issue_checkpoint()
        before = (self.run / "state.json").read_bytes()
        forged = cached["needs"]["resolver_token"][:-4] + "beef"
        self.inform(cached, "The temporary directory is valid", token=forged, expected=2)
        self.assertEqual(before, (self.run / "state.json").read_bytes())
        self.refuse_cached_information(cached)
        self.refuse_cached_information(cached, "Launch the Builder now")
        (self.project / "greet.py").write_text("# edited while paused\n")
        self.refuse_cached_information(cached)
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        self.assert_blocked(self.status())
        self.assertEqual([], self.evaluations())

    def test_information_bound_to_altered_evidence_launches_nothing(self):
        cached = self.issue_checkpoint()
        for receipt in (self.run / "resolver").glob("*.json"):
            receipt.write_text(receipt.read_text() + "\n")
        self.refuse_cached_information(cached)
        for _ in range(2):
            code, launched, output = self.resume()
            self.assertEqual((2, False), (code, launched), output)
            self.assert_blocked(self.status())
        self.assertEqual([], self.evaluations())

    def test_old_unscheduled_information_never_creates_a_fresh_machinery_question(self):
        cached = self.issue_checkpoint()
        run, old = self.saved()
        needs = cached["needs"]
        event = {
            "actor": "user_cli",
            "request_id": needs["resolver_request_id"],
            "request_token": needs["resolver_token"],
            "action": "provide_information",
            "text": "The environment was repaired",
            "at": support.now(),
        }
        entry = old["resolver"]["human_escalations"][event["request_id"]]
        entry.update(status="consumed", response=copy.deepcopy(event))
        old["resolver"]["human_responses"] = [copy.deepcopy(event)]
        old.update(status="PAUSED_RESOLVER_OPERATIONAL", pending_questions=[])
        old.pop(runner.resolver_human.PUBLIC, None)
        old.pop("user_request", None)
        old["resolver"]["human_response_frontier"] = {
            "binding": runner.resolver_human._binding(old),
            "pause_status": old["status"],
            "request_id": event["request_id"],
        }
        old["resolver"].pop("information_reviews", None)
        save_legacy_checkpoint(old, run)
        self.refuse_cached_information(cached, event["text"])
        for flags in ((), ("--resume-paused",)):
            code, launched, output = self.invoke(*flags)
            self.assertEqual((2, False), (code, launched), output)
            self.assert_blocked(self.status())
        self.assertEqual([], self.evaluations())

    def assert_plain_resume_settles_without_a_grant(self, checkpoint):
        self.assert_settling(checkpoint)
        _, before = self.saved()
        for _ in range(2):
            code, launched, output = self.resume()
            self.assertEqual((0, False), (code, launched), output)
            view = self.status()
            self.assertIsNone(view["needs"])
            self.assertIsNone(view["next_command"])
            self.assertIsNone(view["chat"]["input"])
            self.assertEqual("could_not_finish", view["recovery"]["outcome"]["result"])
        _, after = self.saved()
        for key in (
            "automatic_recoveries_since_resume",
            "automatic_timeout_recoveries",
            "consecutive_timeout_recoveries",
            "settings",
        ):
            self.assertEqual(before.get(key), after.get(key), key)
        self.assertEqual([], self.evaluations())
        # A later explicit grant cannot reopen the ended run. Granting before
        # settlement remains covered by test_an_explicit_control_needs_no_machinery_information_review.
        settled = (self.run / "state.json").read_bytes()
        code, launched, output = self.resume("--grant-recovery", "1")
        self.assertEqual((2, False), (code, launched), output)
        self.assertEqual(settled, (self.run / "state.json").read_bytes())

    def test_a_decision_without_allowance_settles_without_a_machinery_question(self):
        self.assert_plain_resume_settles_without_a_grant(
            self.checkpoint(
                "PAUSED_TIMEOUT_RECOVERY",
                "Automatic recovery budget exhausted; no further provider will launch.",
                settling=True,
                automatic_recoveries_since_resume=3,
                consecutive_timeout_recoveries=3,
                automatic_timeout_recoveries=[
                    {"stage": "builder", "timeout_reason": "idle watchdog"} for _ in range(3)
                ],
            )
        )

    def test_spent_allowance_cannot_make_machinery_information_answerable(self):
        cached = self.issue_checkpoint(automatic_recoveries_since_resume=3)
        _, before = self.saved()
        self.refuse_cached_information(cached)
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        _, after = self.saved()
        for key in (
            "automatic_recoveries_since_resume",
            "automatic_permission_recoveries",
            "automatic_timeout_recoveries",
            "settings",
            "goal_contract",
        ):
            self.assertEqual(before.get(key), after.get(key), key)
        self.assertFalse(after.get("recovery_grants"))

    def assert_spent_report_repair_held(self, status, reason):
        cached = self.checkpoint(status, reason, self.pending_repair(2), legacy=True)
        self.env.pop("AUTOCODE_FIXTURE_QUOTA_STAGE")
        _, before = self.saved()
        self.refuse_cached_information(cached, "The workspace is fine now")
        for _ in range(2):
            code, launched, output = self.resume()
            self.assertEqual((2, False), (code, launched), output)
            self.assert_blocked(self.status())
            _, after = self.saved()
            for key in ("pending_report_repair", "report_repair_archive", "sessions", "settings"):
                self.assertEqual(before.get(key), after.get(key), key)
        self.assertEqual([], self.evaluations())

    def test_a_spent_report_repair_allowance_is_held_for_the_operator(self):
        self.assert_spent_report_repair_held(
            "PAUSED_REPORT_REPAIR_LIMIT", "Bounded report-only repair attempts exhausted"
        )

    def test_stale_plan_token_preserves_spent_planning_repair_without_retry(self):
        def failed_planning(state):
            from types import SimpleNamespace

            import autocode_configure as configure

            configure.configure_codex_joint(state["settings"], SimpleNamespace(), planning=runner.planning)
            original = dict(
                next(row for row in state["stages"] if row["stage"] == "builder"),
                stage="finalize_plan",
                role="task_planner",
            )
            revision = support.snapshot(self.project)["revision"]
            for index in range(3):
                record = dict(
                    original,
                    output=str(self.run / f"rejected-plan-{index}.json"),
                    rejected=True,
                    source_revision=revision,
                )
                runner.failures.record(state, record, ValueError("invalid source trace"), support.now())
                state["stages"].append(record)
            state.update(next_stage="finalize_plan")
            state["settings"]["report_repair"] = {"max_attempts": 2}
            state["pending_report_repair"] = {"attempts": 2, "original": record}

        cached = self.checkpoint(
            "PAUSED_REPEATED_FAILURE", "Repeated invalid source trace", failed_planning, legacy=True
        )
        self.refuse_cached_information(
            cached, "Retain the validated contract; correct only contradictory source trace metadata"
        )
        _, before = self.saved()
        self.assertEqual((2, False), self.resume()[:2])
        self.assert_blocked(self.status())
        _, held = self.saved()
        self.assertEqual(before["pending_report_repair"], held["pending_report_repair"])
        # A stale token cannot archive a repair or obtain retry authority before
        # its final refusal. The entire retained checkpoint stays unchanged.
        before_retry = (self.run / "state.json").read_bytes()
        code, launched, output = self.resume("--retry-failed-stage", "--expected-goal-token", "r0:stale")
        self.assertEqual((2, False), (code, launched), output)
        _, saved = self.saved()
        self.assertEqual(before_retry, (self.run / "state.json").read_bytes())
        self.assertEqual(before["pending_report_repair"], saved["pending_report_repair"])
        self.assertFalse(saved.get("failure_retry_authorizations"))
        for key in ("failure_history", "sessions", "settings", "planning"):
            self.assertEqual(before.get(key), saved.get(key), key)
        self.assertTrue(
            all(row in saved["stages"] for row in before["stages"]), "all original observations are retained"
        )
        self.assert_blocked(self.status())
        self.assertEqual([], self.evaluations())

    def test_a_spent_report_repair_behind_a_resolver_stop_is_held(self):
        # resolver_runtime.boundary publishes an exhausted report repair as PAUSED_RESOLVER.
        self.assert_spent_report_repair_held(
            "PAUSED_RESOLVER", "Persisted failure identity or report-repair budget exhausted"
        )

    def test_stalled_validation_rounds_behind_a_resolver_stop_are_held(self):
        def stalled(state):
            """Two validation-only rounds at this source left blocking finding F-1 open (autocode_validation_rounds)."""
            revision = source_scope.snapshot(Path(state["workspace"]), state, base_snapshot=support.snapshot)[
                "revision"
            ]
            contract = state["goal_contract"]["hash"]
            validation = state.setdefault("validation", {})
            validation.update(contract_hash=contract, source_revision=revision)
            state["findings_ledger"] = [
                {
                    "id": "F-1",
                    "source": "tester",
                    "status": "open",
                    "blocking": True,
                    "not_rechecked_in": "validator.json",
                }
            ]
            frontier = {
                "contract_hash": contract,
                "source_revision": revision,
                "findings": {"F-1": ["not_rechecked", None]},
                "passed": sorted(
                    str(row.get("id"))
                    for row in validation.get("criterion_results") or []
                    if row.get("status") == "PASS"
                ),
                "accepted": sorted(
                    key
                    for key, row in (state.get("milestone_progress") or {}).items()
                    if isinstance(row, dict) and row.get("accepted") and row.get("contract_hash") == contract
                ),
            }
            task = state["current_task"]["id"]
            state["validation_only_rounds"] = [
                {"attempt": {"task_id": task, "after_validation": f"earlier-{n}"}, "frontier": frontier, "at": "x"}
                for n in (1, 2)
            ]
            state.update(next_stage="tester")

        cached = self.checkpoint(
            "PAUSED_RESOLVER", "Validation-only rounds made no progress on F-1", stalled, legacy=True
        )
        self.env.pop("AUTOCODE_FIXTURE_QUOTA_STAGE")
        _, before = self.saved()
        self.refuse_cached_information(cached, "The Validator environment is fixed")
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        self.assert_blocked(self.status())
        _, after = self.saved()
        for key in ("validation_only_rounds", "findings_ledger", "settings", "goal_contract"):
            self.assertEqual(before.get(key), after.get(key), key)
        self.assertEqual([], self.evaluations())

    def test_a_held_content_filter_stop_names_a_model_change(self):
        # The content filter refused the Tester's model; it would likely refuse again (#464/#465).
        self.env.update(
            AUTOCODE_FIXTURE_MODE="no-human",
            AUTOCODE_FIXTURE_QUOTA_STAGE="tester",
            AUTOCODE_FIXTURE_QUOTA_MODEL="gpt-5.6-sol",
            AUTOCODE_FIXTURE_QUOTA_MESSAGE="The response was blocked by the provider's content filter",
        )
        self.launch(["Build greeting", "--chat"], 2, answers="CLI\nyes\n")
        self.run, stopped = self.saved()
        attempt = runner.attempt_id(stopped["active_stage"])
        self.inform(self.status(), "The refusal was transient")
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        view = self.status()
        self.assertEqual("held", view["information_review"]["status"])
        self.assertIn(f"--abandon-stage {attempt} ", view["stop_reason"])
        self.assertIn(", then autocode resume --tester-model MODEL", view["stop_reason"])
        self.assertEqual(
            f"--abandon-stage {attempt} then --resume-paused --tester-model MODEL", view["needs"]["action"]
        )
        # The commands it names are accepted and run the Tester on another model.
        self.assertEqual(0, self.invoke("--abandon-stage", attempt)[0])
        code, launched, output = self.invoke("resume", "--tester-model", "gpt-6-luna")
        self.assertTrue(launched, output)
        self.assertIn("Tester: started; model=gpt-6-luna", output)
        self.assertNotIn("Tester: started; model=gpt-5.6-sol", output)

    def test_an_explicit_control_needs_no_machinery_information_review(self):
        checkpoint = self.checkpoint(
            "PAUSED_TIMEOUT_RECOVERY",
            "Automatic recovery budget exhausted; no further provider will launch.",
            settling=True,
            automatic_recoveries_since_resume=3,
            consecutive_timeout_recoveries=3,
            automatic_timeout_recoveries=[{"stage": "builder", "timeout_reason": "idle watchdog"} for _ in range(3)],
        )
        self.assert_settling(checkpoint)
        self.env.pop("AUTOCODE_FIXTURE_QUOTA_STAGE")
        code, launched, output = self.resume("--grant-recovery", "1")
        self.assertTrue(launched, output)
        self.assertEqual("TASK_COMPLETE", self.status()["status"], output)
        self.assertEqual([], self.evaluations())
        for args in ((), ("--resume-paused",)):
            code, launched, output = self.invoke(*args)
            self.assertEqual((0, False), (code, launched), output)
            self.assertNotIn("re-evaluate", output)
        self.assertIsNone(self.status()["information_review"])
        self.assertEqual([], self.evaluations())

    def test_old_information_never_bypasses_normal_admission_guards(self):
        def earlier_incident(state):
            state.setdefault("resolver", {})["attempts"] = {"earlier-incident": 3}

        cached = self.issue_checkpoint(earlier_incident)
        self.refuse_cached_information(cached)
        _, before = self.saved()
        code, launched, output = self.resume(env={"OPENAI_BASE_URL": "https://example.invalid"})
        self.assertEqual((2, False), (code, launched), output)
        self.assert_blocked(self.status())
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        _, after = self.saved()
        self.assertEqual({"earlier-incident": 3}, after["resolver"]["attempts"])
        for key in (
            "automatic_permission_recoveries",
            "automatic_timeout_recoveries",
            "automatic_recoveries_since_resume",
            "goal_contract",
        ):
            self.assertEqual(before.get(key), after.get(key), key)
        self.assertEqual(before["settings"]["limits"], after["settings"]["limits"])
        self.assertEqual([], self.evaluations())

    def test_refused_information_preserves_report_repair_allowance(self):
        cached = self.issue_checkpoint(self.pending_repair(1))
        _, before = self.saved()
        self.refuse_cached_information(cached)
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        _, after = self.saved()
        self.assertEqual(1, after["pending_report_repair"]["attempts"])
        for key in ("pending_report_repair", "report_repair_archive", "sessions", "settings"):
            self.assertEqual(before.get(key), after.get(key), key)
        self.assertFalse(
            [
                event
                for event in after.get("user_events", [])
                if event.get("kind") in ("resolver_resume_epoch", "report_repair_resume_epoch")
            ]
        )
        self.assertEqual([], self.evaluations())


if __name__ == "__main__":
    unittest.main()

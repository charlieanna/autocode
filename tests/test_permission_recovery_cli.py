"""Permission recurrence through the public CLI, never injected runner state."""
import json
from pathlib import Path
import re

from scenarios.harness.adversarial import AdversarialCase
from scenarios.harness.driver import default_autocode
from scenarios.harness.processes import run_cli

FLAGS = re.compile(r"(--[a-z][a-z0-9-]*)")
NOTE = "Write scratch files only under the diagnostic_directory named in recovery_context."


class PermissionRecoveryCLI(AdversarialCase):
    def operator(self, *extra):
        """What an operator types at the saved run: one action, without the harness's launch and limit flags."""
        command = [*default_autocode(), "--workspace", str(self.project), "--run-dir", str(self.driver.run_dir),
                   "--no-chat", *extra]
        return run_cli(command, env=self.env, cwd=self.root, timeout=60)

    def advertised(self, view):
        """Every flag the stop names: its saved reason and the published request's question and options."""
        need = view.get("needs") or {}
        texts = [str((view.get("recovery") or {}).get("saved_reason") or "")]
        for question in need.get("questions") or []:
            texts += [question.get("question") or "", *(question.get("options") or [])]
        return set(FLAGS.findall(" ".join(texts)))

    def held(self, fault):
        """Drive the run to its first denial hold: the same denial twice."""
        self.set_fault("recovery", fault)
        view = self.driver.drive(self.scenario.brief)
        self.assertEqual(2, len(self.trace("builder_permission_denied")), self.root)
        self.assertHeldWithRetry(view)
        return view

    def assertHeldWithRetry(self, view):
        self.assertFalse(view["done"], view)
        self.assertEqual("PAUSED_REPEATED_FAILURE", (view.get("recovery") or {}).get("cause"), view)
        flags = self.advertised(view)
        # #288: only accepted actions, each exercised by the tests below. A denial never spends the
        # timeout-recovery budget, so the grant is never the way past it.
        self.assertEqual({"--resume-paused", "--retry-failed-stage", "--resolver-response"}, flags, view)

    def launches(self):
        return len(self.trace("builder_permission_handoff"))

    def test_corrected_diagnostic_retains_work_and_reaches_independent_verification(self):
        self.set_fault("recovery", "builder_permission_corrected")
        view = self.driver.drive(self.scenario.brief)
        self.assertEqual(1, len(self.trace("builder_permission_denied")))
        corrected = self.trace("builder_permission_corrected")
        self.assertEqual(1, len(corrected), self.root)
        self.assertEqual("external_directory", corrected[0]["denied_operation"]["capability"])
        self.assertEqual("corrected diagnostic executed\n", Path(corrected[0]["receipt"]).read_text())
        self.assertTrue(self.trace("stage_enter", "sol"), "A diagnostic is not independent validation")
        self.assertTrue(self.trace("stage_enter", "astra_review"), "Completion must run normally")
        self.assertEqual("TASK_COMPLETE", view["status"], view)

    def test_repeated_denial_holds_after_one_retry_and_survives_restart(self):
        self.set_fault("recovery", "builder_permission_repeated")
        view = self.driver.drive(self.scenario.brief)
        self.assertEqual(2, len(self.trace("builder_permission_denied")), self.root)
        self.assertFalse(view["done"])
        self.assertIn("external_directory", json.dumps(view))
        self.assertIn("repeated", json.dumps(view).lower())
        self.assertEqual("# retained partial implementation\n", (self.project / "greet.py").read_text())
        state_before = self.driver.state()  # evidence only, never used to drive the CLI
        recovery_before = state_before.get("automatic_recoveries_since_resume", 0)
        archived = [Path(row["events"]) for row in state_before["stages"] if row.get("abandoned")]
        pins = {str(path): path.read_bytes() for path in archived}
        self.assertEqual(2, len(archived))
        for _ in range(2):
            self.invoke("--resume-paused")
            self.assertFalse(self.status()["done"])
        self.assertEqual(2, len(self.trace("builder_permission_denied")), "Restart minted another retry")
        self.assertEqual(recovery_before, self.driver.state().get("automatic_recoveries_since_resume", 0))
        self.assertEqual(pins, {str(path): path.read_bytes() for path in archived})
        self.assertFalse(self.trace("stage_enter", "sol"))

    def test_distinct_denials_never_exhaust_the_timeout_budget_and_hold_at_their_own_ceiling(self):
        self.set_fault("recovery", "builder_permission_distinct")
        view = self.driver.drive(self.scenario.brief)
        self.assertFalse(view["done"], self.root)
        surfaced = json.dumps(view)
        self.assertNotIn("PAUSED_TIMEOUT_RECOVERY", surfaced,
                         "permission denials must not surface as exhausted timeout recovery")
        state = self.driver.state()
        self.assertEqual(0, state.get("automatic_recoveries_since_resume", 0),
                         "a denial retry must not consume the shared timeout-recovery budget")
        denials = self.trace("builder_permission_denied")
        self.assertGreaterEqual(len(denials), 2, self.root)
        self.assertLessEqual(len(denials), 4, "the permission ceiling must bound distinct-denial retries")
        surfaced_lower = surfaced.lower()
        self.assertTrue("external_directory" in surfaced_lower and
                        ("ceiling" in surfaced_lower or "repeated" in surfaced_lower or
                         "no progress" in surfaced_lower or "no_progress" in surfaced_lower),
                        "the hold must name its actual cause")
        before = self.driver.state().get("automatic_recoveries_since_resume", 0)
        for _ in range(2):
            self.invoke("--resume-paused")
            self.assertFalse(self.status()["done"])
        self.assertEqual(before, self.driver.state().get("automatic_recoveries_since_resume", 0),
                         "restarts must not mint timeout-recovery allowance either")
        self.assertEqual(len(denials), len(self.trace("builder_permission_denied")),
                         "restarts must not launch further denial retries")

    def test_successful_diagnostic_cannot_complete_a_broken_product(self):
        config = json.loads(self.config_path.read_text())
        config["reference"] = str(self.scenario.dir / "broken" / "accepts-two-names")
        self.config_path.write_text(json.dumps(config))
        self.set_fault("recovery", "builder_permission_corrected")
        view = self.driver.drive(self.scenario.brief)
        self.assertTrue(self.trace("builder_permission_corrected"), self.root)
        self.assertTrue(self.trace("stage_enter", "sol"))
        self.assertFalse(view["done"], "Successful scratch work cannot hide failed acceptance tests")

    def test_permission_hold_advertises_one_fresh_attempt_and_it_resumes_the_run(self):
        """#301: corrective information and a plain resume both hold; the advertised retry completes the run."""
        view = self.held("builder_permission_until_retry")
        before = self.driver.state()  # evidence only
        need = view["needs"]
        answered = self.operator("--resolver-request", need["resolver_request_id"], "--resolver-token",
                                 need["resolver_token"], "--resolver-response", "provide_information",
                                 "--resolver-message", NOTE)
        self.assertEqual(0, answered.returncode, answered.stdout + answered.stderr)
        held = self.operator("--resume-paused")
        self.assertEqual(2, held.returncode)
        self.assertNotIn("Input rejected", held.stderr)
        self.assertEqual(2, self.launches(), "information is not authority: a plain resume launches nothing")
        retried = self.operator("--resume-paused", "--retry-failed-stage")
        self.assertNotIn("Input rejected", retried.stderr, retried.stdout + retried.stderr)
        self.assertEqual(1, len(self.trace("builder_permission_corrected")), self.root)
        handoff = self.trace("builder_permission_handoff")[-1]["recovery"]
        self.assertEqual(NOTE, handoff["human_information"]["text"], "the information reaches the attempt")
        view = self.finish()
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        self.assertEqual(3, self.launches(), "exactly one fresh attempt")
        state = self.driver.state()  # evidence only
        self.assertEqual(before["automatic_permission_recoveries"], state["automatic_permission_recoveries"])
        self.assertEqual(0, state.get("automatic_recoveries_since_resume", 0))
        self.assertEqual(1, [e["kind"] for e in state["user_events"]].count("failure_retry_authorized"))

    def test_a_rejected_retry_leaves_the_hold_and_the_same_retry_is_accepted_again(self):
        """A command refused after its authorization was saved lifts nothing for any later command."""
        self.held("builder_permission_until_retry")
        rejected = self.operator("--resume-paused", "--retry-failed-stage", "--retry-builder", "M9")
        self.assertEqual(2, rejected.returncode)
        self.assertIn("Input rejected", rejected.stderr)
        self.assertEqual(2, self.launches(), rejected.stdout + rejected.stderr)
        for command in (("--resume-paused", "--max-seconds", "900"), ("--resume-paused",)):
            with self.subTest(command=command):
                self.assertEqual(2, self.operator(*command).returncode)
                self.assertEqual(2, self.launches(), "a saved, unlaunched authorization is not authority")
                self.assertHeldWithRetry(self.status())
        retried = self.operator("--resume-paused", "--retry-failed-stage")
        self.assertNotIn("Input rejected", retried.stderr, retried.stdout + retried.stderr)
        self.assertEqual(1, len(self.trace("builder_permission_corrected")), self.root)
        self.assertEqual(3, self.launches())
        state = self.driver.state()  # evidence only
        self.assertEqual(1, [e["kind"] for e in state["user_events"]].count("failure_retry_authorized"),
                         "the re-issued retry is the same authorization")

    def test_each_later_hold_resumes_with_its_advertised_retry_and_launches_one_attempt(self):
        """The authorized attempt is denied again: the next hold's retry launches one attempt, not a grant."""
        self.held("builder_permission_repeated")
        recovered = self.driver.state().get("automatic_recoveries_since_resume", 0)  # evidence only
        for denials in (3, 4):
            retried = self.operator("--resume-paused", "--retry-failed-stage")
            self.assertNotIn("Input rejected", retried.stderr, retried.stdout + retried.stderr)
            self.assertEqual(denials, len(self.trace("builder_permission_denied")), retried.stdout + retried.stderr)
            # Three Builder denials reach the no-progress limit and spent()'s estimate of the recovery
            # budget; neither stops the next authorized attempt, and neither replaces the hold.
            self.assertHeldWithRetry(self.status())
        for _ in range(2):
            self.assertEqual(2, self.operator("--resume-paused").returncode)
        self.assertEqual(4, len(self.trace("builder_permission_denied")), "a used authorization launched again")
        self.assertFalse(self.trace("stage_enter", "sol"))
        state = self.driver.state()  # evidence only
        self.assertEqual([1, 2, 3, 4], [row["repeat_count"] for row in state["automatic_permission_recoveries"]])
        self.assertEqual(recovered, state.get("automatic_recoveries_since_resume", 0))
        self.assertEqual(2, [e["kind"] for e in state["user_events"]].count("failure_retry_authorized"))

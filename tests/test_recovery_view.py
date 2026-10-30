"""Public recovery descriptions do not grant authority or omit a next step."""

import ast
import contextlib
import copy
import io
import unittest
from pathlib import Path

import autocode_args
import autocode_configure as configure
import autocode_issue as issue
import autocode_progress_view as progress
import autocode_recovery_novelty as novelty
import autocode_recovery_view as recovery
import autocode_run_finder as finder
import autocode_run_view as run_view


class RecoveryViewTests(unittest.TestCase):
    def test_immutable_caller_binding_requires_fresh_run_in_every_public_reader(self):
        task = (
            "Implement the api component of a larger system: HTTP service. "
            "Own only the directory components/api/; do not create or edit any file outside it."
        )
        ownership = "Own only the directory components/api/; do not create or edit any file outside it."
        for original, root in (
            (task, "components/other"),
            (task.replace(ownership, ""), None),
            (task + " " + ownership, None),
        ):
            with self.subTest(task=original, root=root):
                state = self.state("PAUSED_COMPONENT_PLAN", task=original, stop_reason="Repairable-looking body path")
                state["settings"]["regression"] = {"test_root": root}
                before = copy.deepcopy(state)
                view = run_view.view(state)
                need = view["needs"]
                self.assertEqual("resume", need["kind"])
                self.assertTrue(need.get("new_run_required"))
                self.assertFalse(need.get("edit_required"))
                self.assertEqual("fresh_run", need["action"])
                self.assertIsNone(need["feedback_action"])
                self.assertEqual(["inspect", "new_run"], [row["kind"] for row in view["recovery"]["actions"]])
                record = {
                    "worktree": "/p",
                    "run_dir": "/p/.autocode/runs/old",
                    "owner": "acme",
                    "repo": "api",
                    "number": 7,
                }
                line = progress.progress(state, accepted=(), stage=None, needs=need)["needs_you"]
                texts = [
                    line,
                    finder.continue_hint("/p/.autocode/runs/old", state),
                    "\n".join(issue.next_steps(record, view)),
                    view["recovery"]["actions"][-1]["effect"],
                ]
                for text in texts:
                    self.assertIn("fresh", text.lower())
                    self.assertNotIn("--edit-goal", text)
                    self.assertNotIn("--feedback", text)
                    self.assertNotIn("--resume-paused", text)
                self.assertEqual(before, state)

    def test_valid_legacy_binding_keeps_body_repair_even_if_stop_reason_claims_binding_failure(self):
        state = self.state(
            "PAUSED_COMPONENT_PLAN",
            task=(
                "Implement the api component of a larger system: HTTP service. "
                "Own only the directory components/api/; do not create or edit any file outside it."
            ),
            stop_reason="Model claims the caller root disagrees and requires a new run",
        )
        before = copy.deepcopy(state)
        view = run_view.view(state)
        need = view["needs"]
        self.assertTrue(need["edit_required"])
        self.assertFalse(need.get("new_run_required"))
        self.assertEqual("--edit-goal FILE", need["action"])
        record = {"worktree": "/p", "run_dir": "/p/.autocode/runs/old", "owner": "acme", "repo": "api", "number": 7}
        line = progress.progress(state, accepted=(), stage=None, needs=need)["needs_you"]
        for text in (line, "\n".join(issue.next_steps(record, view))):
            self.assertIn("--edit-goal", text)
            self.assertIn("--feedback", text)
            self.assertNotIn("--resume-paused", text)
        self.assertEqual(before, state)

    def test_component_plan_stop_requires_edit_and_fresh_approval(self):
        state = self.state("PAUSED_COMPONENT_PLAN", stop_reason="Component plan: outside ownership")
        before = copy.deepcopy(state)
        view = run_view.view(state)
        self.assertTrue(view["needs"].get("edit_required"))
        self.assertEqual("--edit-goal FILE", view["needs"]["action"])
        self.assertEqual("--feedback TEXT", view["needs"]["feedback_action"])
        self.assertFalse(finder.resume_acknowledges(state["status"]))
        self.assertEqual("product", novelty.classify(status=state["status"]))
        self.assertEqual(["inspect", "feedback"], [row["kind"] for row in view["recovery"]["actions"]])
        self.assertIn("fresh approval", view["recovery"]["actions"][-1]["effect"])
        self.assertEqual(before, state)

    def state(self, status="PAUSED_REQUESTED", **fields):
        return {
            "status": status,
            "iteration": 4,
            "next_stage": "terra",
            "current_task": {"id": "t", "milestone_id": "M1", "kind": "implement"},
            "settings": {
                "builder_retry": {"enabled": True},
                "roles": {"terra": {"model": "p/builder", "reasoning_effort": "high", "model_pinned": True}},
            },
            "goal_contract": {"hash": "approved", "revision": 1},
            "stages": [],
            **fields,
        }

    def card(self, state):
        return run_view.view(state)["recovery"]

    def test_a_run_autocode_ended_itself_is_a_finished_failed_outcome_with_no_retry(self):
        # #2485: the card shows AutoCode's own end as a stopped run with its reason and evidence, and
        # offers no retry, flag, packet path or token; the generic person's stop keeps its title.
        outcome = {
            "result": "could_not_finish",
            "reason": "the sol check kept failing for the same reason after AutoCode's own repair retry: x.",
            "evidence": {"incident_id": "abc", "packet": {"path": "recovery/1.json"}},
        }
        receipt = {"id": "autocode-outcome-1", "kind": "stop", "text": "", "actor": "autocode", "outcome": outcome}
        state = self.state(
            "PAUSED_INTERVENTION",
            stop_reason="AutoCode could not finish this task: "
            + outcome["reason"]
            + " This run launches no further stages.",
            applied_interventions=[receipt],
        )
        view = run_view.view(state)
        card = view["recovery"]
        self.assertEqual(("stopped", "AutoCode could not finish this task"), (card["category"], card["title"]))
        self.assertIn(outcome["reason"], card["what_happened"])
        self.assertEqual(outcome, card["outcome"])
        self.assertEqual(["inspect", "new_conversation"], [row["kind"] for row in card["actions"]])
        self.assertEqual("autocode", view["pause_category"])
        for machinery in ("--retry-failed-stage", "--resolver-token", "Exact packet"):
            self.assertNotIn(machinery, str(view["needs"]) + str(view["next_command"]) + card["what_happened"])
        person = self.state("PAUSED_INTERVENTION", applied_interventions=[{"id": "s", "kind": "stop", "text": ""}])
        card = self.card(person)
        self.assertEqual(
            ("stopped", "Stopped at your request", None), (card["category"], card["title"], card["outcome"])
        )
        self.assertEqual("your_decision", run_view.view(person)["pause_category"])

    def test_every_declared_stop_and_future_status_has_three_parts_and_a_next_step(self):
        # Coverage grows automatically with the runtime's status vocabulary.
        statuses = {"PAUSED_FUTURE_UNKNOWN", "RESOLVER_PENDING"}
        for path in Path(__file__).resolve().parents[1].joinpath("tools").glob("*.py"):
            if path.name == "autocode_recovery_view.py":
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value.isupper()
                    and not node.value.endswith("_")
                    and node.value.startswith(("PAUSED_", "BLOCKED", "FAILED", "WAITING_", "AWAITING_"))
                ):
                    statuses.add(node.value)
        self.assertGreater(len(statuses), 50)
        for status in statuses:
            with self.subTest(status=status):
                state = self.state(status)
                before = copy.deepcopy(state)
                result = self.card(state)
                for field in ("what_happened", "retained", "actions"):
                    self.assertTrue(result[field])
                self.assertIn("inspect", [row["kind"] for row in result["actions"]])
                self.assertTrue(
                    {"feedback", "decision", "new_conversation"} & {row["kind"] for row in result["actions"]}
                )
                self.assertNotIn("terra", result["what_happened"])
                self.assertEqual(before, state)
        for status in recovery.ACTIVE:
            self.assertIsNone(self.card(self.state(status)))

    def test_token_changes_with_attempt_settings_contract_or_history_but_not_poll_time(self):
        state = self.state()
        original = recovery.token(state)
        state["updated_at"] = "later"
        self.assertEqual(original, recovery.token(state))
        for field, value in [
            ("workspace", "/other"),
            ("run_dir", "/other/run"),
            ("status", "RUNNING"),
            ("iteration", 5),
            ("current_task", {"id": "different"}),
            ("settings", {"roles": {"terra": {"model": "other"}}}),
            ("goal_contract", {"hash": "new"}),
            ("stages", [{"finished_at": "now"}]),
            ("user_events", [{"kind": "feedback"}]),
            ("failure_history", {"new": {"count": 1}}),
        ]:
            changed = {**state, field: value}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "pause changed"):
                recovery.require_token(changed, original)
        recovery.require_token(state, original)
        recovery.require_token(state, None)  # ordinary CLI callers retain compatibility
        with self.assertRaisesRegex(ValueError, "pause changed"):
            recovery.require_token(state, "")

    def test_exact_serial_builder_action_preserves_pins_history_and_scope(self):
        state = self.state("PAUSED_BUILDER_RETRY_LIMIT")
        key = recovery.builder_policy.key(state)
        state["builder_retries"] = {key: {"action": "pause", "failures": ["failure-a", "failure-b"]}}
        before = copy.deepcopy(state)
        action = next(row for row in self.card(state)["actions"] if row["kind"] == "retry_builder")
        self.assertEqual(["M1"], action["milestone_ids"])
        self.assertEqual("retry_builder:M1", action["id"])
        self.assertNotIn("stronger", action["effect"])
        self.assertEqual(before, state)
        for field, value in [
            ("active_stage", {"stage": "terra"}),
            ("active_runner_check", {"stage": "regression_proof"}),
            ("pending_report_repair", {"stage": "terra"}),
            ("parent_run", "/parent"),
        ]:
            with self.subTest(field=field):
                self.assertNotIn(
                    "retry_builder", [row["kind"] for row in self.card({**state, field: value})["actions"]]
                )

    def test_reconciled_operator_stop_never_offers_any_execution_or_feedback(self):
        state = self.state("PAUSED_INTERVENTION", applied_interventions=[{"id": "stop", "kind": "stop"}])
        card = self.card(state)
        self.assertEqual("stopped", card["category"])
        self.assertEqual(["inspect", "new_conversation"], [row["kind"] for row in card["actions"]])

    def test_a_pause_someone_asked_for_resumes_without_asking_for_a_correction(self):
        for status in ("PAUSED_INTERVENTION", "PAUSED_REQUESTED"):
            with self.subTest(status=status):
                resume = next(row for row in self.card(self.state(status))["actions"] if row["kind"] == "resume")
                self.assertEqual("Resume", resume["label"])
                self.assertNotIn("corrected", resume["effect"])
        resume = next(
            row for row in self.card(self.state("PAUSED_INVALID_OUTPUT"))["actions"] if row["kind"] == "resume"
        )
        self.assertEqual("Resume after correction", resume["label"])

    def test_only_a_clean_requested_pause_can_skip_the_second_resume_confirmation(self):
        for status in ("PAUSED_INTERVENTION", "PAUSED_REQUESTED"):
            with self.subTest(status=status):
                state = self.state(status)
                before = copy.deepcopy(state)
                view = run_view.view(state, workspace=Path("/ws"), run_dir=Path("/run"))
                self.assertEqual("resume", view["needs"]["kind"])
                self.assertIs(False, view["needs"]["confirmation_required"])
                self.assertEqual("autocode --run-dir /run --workspace /ws --resume-paused", view["next_command"])
                self.assertEqual(before, state)
        for status, fields in (
            ("PAUSED_INVALID_OUTPUT", {}),
            ("PAUSED_INTERVENTION", {"pause_intent": {"held_pause": {"status": "PAUSED_TIME_LIMIT"}}}),
            ("PAUSED_REQUESTED", {"pause_intent": {"held_pause": {"status": "PAUSED_TIME_LIMIT"}}}),
            (
                "PAUSED_INTERVENTION",
                {"active_stage": {"iteration": 1, "output": "/run/iterations/001/builder-01.json"}},
            ),
            ("PAUSED_REQUESTED", {"active_runner_check": {"stage": "regression_proof"}}),
            ("PAUSED_REQUESTED", {"uncertain_artifacts": ["partial.py"]}),
            ("PAUSED_REQUESTED", {"pending_report_repair": {"original": {"stage": "sol"}}}),
            ("PAUSED_INTERVENTION", {"applied_interventions": [{"kind": "stop"}]}),
        ):
            with self.subTest(status=status, fields=fields):
                view = run_view.view(self.state(status, **fields))
                self.assertIsNot(False, view["needs"].get("confirmation_required"))

    def test_interruption_is_exact_abandon_without_resume_and_missing_attempt_has_no_guess(self):
        state = self.state(
            "PAUSED_PROVIDER_UNCERTAIN",
            active_stage={"iteration": 4, "stage": "terra", "output": "/run/iterations/004/builder-02.json"},
        )
        actions = self.card(state)["actions"]
        selected = next(row for row in actions if row["kind"] == "abandon")
        self.assertEqual("004/builder-02", selected["attempt_id"])
        self.assertIn("Resume is a separate action", selected["effect"])
        self.assertNotIn("resume", [row["kind"] for row in actions])
        state["active_stage"] = {}
        self.assertEqual(["inspect", "feedback"], [row["kind"] for row in self.card(state)["actions"]])

    def test_quota_pause_offers_exact_inspected_abandonment_before_separate_resume(self):
        state = self.state(
            "PAUSED_BUDGET",
            stop_reason="Quota restored; inspect the uncertain attempt.",
            active_stage={"iteration": 4, "stage": "terra", "output": "/run/iterations/004/builder-02.json"},
            failure_history={"quota": {"count": 1, "attempts": ["004/builder-02"]}},
        )
        before = copy.deepcopy(state)
        view = run_view.view(state)
        self.assertEqual("004/builder-02", view["needs"]["abandon_stage"])
        actions = view["recovery"]["actions"]
        selected = next(row for row in actions if row["kind"] == "abandon")
        self.assertEqual(view["needs"]["abandon_stage"], selected["attempt_id"])
        self.assertIn("Resume is a separate action", selected["effect"])
        self.assertNotIn("resume", [row["kind"] for row in actions])
        self.assertEqual(before, state)

    def test_quota_abandonment_requires_matching_public_need_and_saved_attempt(self):
        state = self.state(
            "PAUSED_BUDGET",
            active_stage={"iteration": 4, "stage": "terra", "output": "/run/iterations/004/builder-02.json"},
        )
        for need in (
            {"kind": "resume"},
            {"kind": "resume", "abandon_stage": ""},
            {"kind": "resume", "abandon_stage": "004/builder-03"},
            {"kind": "resume", "abandon_stage": "003/builder-02"},
        ):
            with self.subTest(need=need):
                self.assertEqual(
                    ["inspect", "feedback"], [row["kind"] for row in recovery.project(state, need)["actions"]]
                )
        for active in ({}, {"iteration": 4}, {"output": "/run/builder-02.json"}):
            with self.subTest(active=active):
                changed = {**state, "active_stage": active}
                self.assertNotIn("abandon_stage", run_view.view(changed)["needs"])
                self.assertNotIn("abandon", [row["kind"] for row in self.card(changed)["actions"]])
                self.assertNotIn(
                    "abandon",
                    [
                        row["kind"]
                        for row in recovery.project(changed, {"kind": "resume", "abandon_stage": "004/builder-02"})[
                            "actions"
                        ]
                    ],
                )

    def test_quota_recovery_preserves_decision_source_stop_and_report_precedence(self):
        active = {"iteration": 4, "stage": "terra", "output": "/run/iterations/004/builder-02.json"}
        cases = [
            (
                "answer",
                self.state(
                    "PAUSED_BUDGET", active_stage=active, pending_questions=[{"id": "q", "question": "Which scope?"}]
                ),
            ),
            (
                "review",
                self.state(
                    "PAUSED_BUDGET",
                    active_stage=active,
                    pending_questions=[{"id": "review", "review_criteria": ["C1"], "review_token": "review-token"}],
                ),
            ),
            ("approve_plan", self.state("AWAITING_GOAL_APPROVAL", active_stage=active, displayed_goal="r1:approved")),
            (
                "recover_source",
                self.state(
                    "PAUSED_JOB_FAILURE",
                    active_stage=active,
                    job_failure={
                        "reason": "Source identity unavailable",
                        "stage": "investigate_bug",
                        "attempt_id": "004/builder-02",
                        "job_retry_token": "old-token",
                        "archive": "/run/archive",
                        "source_identity": None,
                        "write_diagnosis": {},
                        "unrestored": ["original source capture"],
                    },
                ),
            ),
        ]
        for kind, state in cases:
            with self.subTest(kind=kind):
                before = copy.deepcopy(state)
                view = run_view.view(state)
                self.assertEqual(kind, view["needs"]["kind"])
                self.assertNotIn("abandon", [row["kind"] for row in view["recovery"]["actions"]])
                # A stale extra hint never overrides the current request kind.
                need = {**view["needs"], "abandon_stage": "004/builder-02"}
                self.assertNotIn("abandon", [row["kind"] for row in recovery.project(state, need)["actions"]])
                self.assertEqual(before, state)
        stopped = self.state(
            "PAUSED_INTERVENTION", active_stage=active, applied_interventions=[{"id": "stop", "kind": "stop"}]
        )
        self.assertEqual(["inspect", "new_conversation"], [row["kind"] for row in self.card(stopped)["actions"]])
        report = self.state(
            "PAUSED_REPEATED_FAILURE",
            active_stage=active,
            pending_report_repair={
                "error": "Check is not supported by an exact executed Validator event",
                "original": {"stage": "sol"},
                "attempts": 2,
                "latest_rejected": {"iteration": 4, "output": "/run/validator-01.json"},
            },
        )
        view = run_view.view(report)
        self.assertEqual("004/builder-02", view["needs"]["abandon_stage"])
        self.assertNotIn("retry_report_attempt", view["needs"])
        self.assertEqual(["inspect", "abandon", "feedback"], [row["kind"] for row in view["recovery"]["actions"]])
        report.pop("active_stage")
        report["settings"]["report_repair"] = {"max_attempts": 2}
        view = run_view.view(report)
        self.assertEqual("004/validator-01", view["needs"]["retry_report_attempt"])
        self.assertEqual(["inspect", "retry_report", "feedback"], [row["kind"] for row in view["recovery"]["actions"]])

    def test_saved_internal_question_does_not_claim_human_authority_or_answer_it(self):
        state = self.state("WAITING_FOR_USER", pending_questions=[{"id": "q", "question": "Which format?"}])
        card = self.card(state)
        self.assertEqual("request", card["category"])
        self.assertEqual("Review checkpoint", card["title"])
        self.assertNotIn("needed", card["what_happened"])
        self.assertEqual(["inspect", "decision", "feedback"], [row["kind"] for row in card["actions"]])

    def test_repeated_failure_groups_keep_sources_counts_and_missing_details_honest(self):
        state = self.state(
            "PAUSED_REPEATED_FAILURE",
            failure_history={
                "a": {
                    "identity": {"stage": "terra", "artifact_hash": "first"},
                    "count": 3,
                    "attempts": ["1", "2", "3"],
                    "last_error": "assertion failed",
                    "last_seen": "2026-10-01",
                },
                "b": {
                    "identity": {"stage": "sol", "artifact_hash": "second"},
                    "count": 1,
                    "attempts": ["4"],
                    "last_seen": "2026-10-02",
                },
            },
        )
        result = self.card(state)
        self.assertEqual(["b", "a"], [row["id"] for row in result["failure_groups"]])
        self.assertEqual("Builder", result["failure_groups"][1]["role"])
        self.assertEqual(3, result["failure_groups"][1]["count"])
        self.assertIsNone(result["failure_groups"][0]["last_reason"])
        self.assertIn("retry_failed_stage", [row["kind"] for row in result["actions"]])

    def test_parallel_retry_targets_only_failed_members_and_never_a_running_batch(self):
        state = self.state(
            "PAUSED_ORCHESTRATOR_WORKER",
            next_stage="orchestrator",
            orchestration_batch={
                "status": "BUILDING",
                "contract_hash": "approved",
                "workers": [
                    {"milestone_id": "M1", "status": "BUILT"},
                    {"milestone_id": "M2", "status": "INTERRUPTED"},
                    {"milestone_id": "M3", "status": "SERIAL_ESCALATION"},
                    # Stopped on their model: asked for another one, never offered the same model again (#465).
                    {"milestone_id": "M4", "status": "PAUSED_CONTENT_FILTER"},
                    {"milestone_id": "M5", "status": "PAUSED_BUDGET"},
                ],
            },
        )
        actions = [row for row in self.card(state)["actions"] if row["kind"] == "retry_builder"]
        self.assertEqual([["M2"]], [row["milestone_ids"] for row in actions])
        state["orchestration_batch"]["workers"][0]["status"] = "RUNNING"
        self.assertFalse(any(row["kind"] == "retry_builder" for row in self.card(state)["actions"]))

    def test_a_member_model_stop_answered_without_a_model_names_its_one_control(self):
        # #541: once its request was answered with information (or left paused), no question is open and
        # needs.action and the card name one control: a quota stop reruns unchanged, a refusal is asked again.
        def answered(status):
            worker = {
                "milestone_id": "M1",
                "run_dir": "/runs/1",
                "workspace": "/work/1",
                "pause_status": status,
                "model": "p/glm",
            }
            return self.state(
                status,
                next_stage="orchestrator",
                orchestration_batch={
                    "status": "BUILDING",
                    "contract_hash": "approved",
                    "workers": [
                        {"milestone_id": "M1", "run_dir": "/runs/1", "workspace": "/work/1", "status": status},
                        {"milestone_id": "M2", "status": "BUILT"},
                    ],
                },
                resolver={
                    "human_response_frontier": {"request_id": "r1", "pause_status": status},
                    "human_escalations": {
                        "r1": {
                            "status": "consumed",
                            "identity": {"proposal": {"origin": {"pause_status": status, "quota_worker": worker}}},
                        }
                    },
                },
            )

        for status, label in (
            ("PAUSED_BUDGET", "Retry Builder task M1 unchanged"),
            ("PAUSED_CONTENT_FILTER", "Ask which model Builder task M1 continues on"),
        ):
            state = answered(status)
            view = run_view.view(state)
            self.assertEqual("--resume-paused --retry-builder M1", view["needs"].get("action"))
            self.assertEqual(
                [(["M1"], label)],
                [
                    (row["milestone_ids"], row["label"])
                    for row in view["recovery"]["actions"]
                    if row["kind"] == "retry_builder"
                ],
            )
            # While a question is open it is the next step, and no retry is offered (#465).
            state["pending_questions"] = [{"id": "route-terra", "question": "Name the model"}]
            self.assertFalse(any(row["kind"] == "retry_builder" for row in self.card(state)["actions"]))

    def test_job_report_and_dependency_actions_are_specific(self):
        state = self.state("PAUSED_JOB_FAILURE")
        selected = recovery.project(state, {"kind": "retry_job", "job_retry_token": "job-token"})["actions"][1]
        self.assertEqual(("retry_job", "job-token"), (selected["kind"], selected["job_retry_token"]))
        selected = recovery.project(
            self.state("PAUSED_REPEATED_FAILURE"), {"retry_report_attempt": "004/validator-01"}
        )["actions"][1]
        self.assertEqual(("retry_report", "004/validator-01"), (selected["kind"], selected["attempt_id"]))
        actions = self.card(self.state("WAITING_FOR_DEPENDENCY"))["actions"]
        self.assertEqual(["inspect", "feedback"], [row["kind"] for row in actions])

    def test_each_execution_action_names_the_exact_cli_flags_it_runs(self):
        # A client runs these after the inspected token instead of mapping an action's kind to flags (#730).
        builder = self.state("PAUSED_BUILDER_RETRY_LIMIT")
        builder["builder_retries"] = {recovery.builder_policy.key(builder): {"action": "pause", "failures": ["a"]}}
        parallel = self.state(
            "PAUSED_ORCHESTRATOR_WORKER",
            next_stage="orchestrator",
            orchestration_batch={
                "status": "BUILDING",
                "contract_hash": "approved",
                "workers": [
                    {"milestone_id": "M2", "status": "INTERRUPTED"},
                    {"milestone_id": "M3", "status": "FAILED"},
                ],
            },
        )
        interrupted = self.state(
            "PAUSED_PROVIDER_UNCERTAIN",
            active_stage={"iteration": 4, "stage": "terra", "output": "/run/iterations/004/builder-02.json"},
        )
        source_edit = self.state(
            "PAUSED_STALE_HANDOFF",
            stop_reason="Repair diagnosis needs the current reviewed source and task",
            resolution_request={"source_revision": "s1"},
        )
        cases = [
            (self.card(self.state("PAUSED_REQUESTED")), "resume", ["--resume-paused"]),
            (self.card(source_edit), "accept_source_edit", ["--resume-paused", "--accept-source-edit"]),
            (self.card(interrupted), "abandon", ["--abandon-stage", "004/builder-02"]),
            (self.card(builder), "retry_builder", ["--resume-paused", "--retry-builder", "M1"]),
            (self.card(parallel), "retry_builder:M3", ["--resume-paused", "--retry-builder", "M3"]),
            (
                recovery.project(self.state("PAUSED_JOB_FAILURE"), {"kind": "retry_job", "job_retry_token": "job-1"}),
                "retry_job",
                ["--resume-paused", "--retry-failed-stage", "--job-retry-token", "job-1"],
            ),
            (
                recovery.project(self.state("PAUSED_REPEATED_FAILURE"), {"retry_report_attempt": "004/validator-01"}),
                "retry_report",
                ["--resume-paused", "--retry-report", "004/validator-01"],
            ),
            (
                self.card(self.state("PAUSED_REPEATED_FAILURE")),
                "retry_failed_stage",
                ["--resume-paused", "--retry-failed-stage"],
            ),
        ]
        for card, ident, flags in cases:
            with self.subTest(action=ident):
                action = next(row for row in card["actions"] if row["id"] == ident or row["id"].startswith(ident + ":"))
                self.assertEqual(flags, action["args"])
                with contextlib.redirect_stderr(io.StringIO()):
                    args = autocode_args.parse(
                        None,
                        ["--run-dir", "/saved/run", "--no-chat", "--expected-recovery-token", card["token"], *flags],
                        configure.DEFAULT_ROLE_MODELS,
                    )[0]
                self.assertEqual(card["token"], args.expected_recovery_token)
        stopped = self.card(self.state("PAUSED_INTERVENTION", applied_interventions=[{"id": "s", "kind": "stop"}]))
        decision = self.card(self.state("PAUSED_REQUESTED", pending_questions=[{"id": "q1", "question": "Which?"}]))
        for card in (stopped, decision, *(case[0] for case in cases)):
            for row in card["actions"]:
                if row["kind"] in ("inspect", "feedback", "decision", "new_conversation", "new_run"):
                    self.assertNotIn("args", row, row["kind"] + " runs no recovery control")

    def test_a_repair_paused_on_an_edited_source_offers_accepting_that_source(self):
        # The run names --resume-paused --accept-source-edit as the one command that continues;
        # without a card action a dashboard reader had no way to continue it (#730).
        reason = "Repair diagnosis needs the current reviewed source and task"
        edited = self.state("PAUSED_STALE_HANDOFF", stop_reason=reason, resolution_request={"source_revision": "s1"})
        view = run_view.view(edited)
        self.assertEqual("--resume-paused --accept-source-edit", view["needs"]["action"])
        self.assertEqual(
            ["inspect", "accept_source_edit", "feedback"], [row["kind"] for row in view["recovery"]["actions"]]
        )
        accept = view["recovery"]["actions"][1]
        self.assertEqual("Accept the edited source", accept["label"])
        self.assertIn("Resolver", accept["effect"])
        resolver_wrote = self.state(
            "PAUSED_STALE_HANDOFF",
            stop_reason="Resolver must leave the reviewed source unchanged",
            resolution_request={"source_revision": "s1"},
        )
        held = recovery.project(edited, {"kind": "resume", "action": "--resume-paused --feedback-review"})
        for card in (self.card(resolver_wrote), held):
            self.assertNotIn("accept_source_edit", [row["kind"] for row in card["actions"]])

    def test_missing_original_job_source_offers_inspection_without_execution(self):
        for status in ("PAUSED_JOB_FAILURE", "PAUSED_STAGE_ABANDONED"):
            with self.subTest(status=status):
                state = self.state(
                    status,
                    next_stage="investigate_bug",
                    job_failure={
                        "reason": "Provider stopped",
                        "stage": "investigate_bug",
                        "attempt_id": "001/bug-investigation-01",
                        "job_retry_token": "old",
                        "archive": "/run/archive",
                        "source_identity": None,
                        "write_diagnosis": {"unrestored": ["original source capture"]},
                        "unrestored": ["original source capture"],
                    },
                )
                before = copy.deepcopy(state)
                card = self.card(state)
                self.assertEqual(["inspect", "feedback"], [row["kind"] for row in card["actions"]])
                self.assertIn("original source identity", card["saved_reason"])
                self.assertEqual(before, state)


class AuthenticationStopTests(unittest.TestCase):
    """A run stopped on the core's own HTTP 401 diagnostic says so in plain words (an expired key)."""

    def test_an_authentication_stop_names_the_credentials_and_the_fix(self):
        import autocode_provider_auth as provider_auth

        state = {
            "status": "PAUSED_PROVIDER_UNCERTAIN",
            "stop_reason": provider_auth.AUTH_FAILED + ". requirements exited 1; reconcile iterations/001/x.jsonl",
            "active_stage": {"stage": "requirements_gather"},
        }
        what = recovery.project(state)["what_happened"]
        self.assertIn("rejected the saved credentials (HTTP 401)", what)
        self.assertIn("/connect NAME login", what)
        self.assertNotIn("reconcile", what, "raw diagnostics stay in the details")

    def test_another_stop_keeps_its_own_explanation(self):
        state = {"status": "PAUSED_PROVIDER_UNCERTAIN", "stop_reason": "requirements exited 1"}
        self.assertNotIn("credentials", recovery.project(state)["what_happened"])

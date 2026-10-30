"""The pause view offers a scoped command accepted by the public CLI."""

import copy
import shlex
import unittest
from pathlib import Path
from unittest import mock

import autocode_args
import autocode_configure
import autocode_run_view as run_view


class PauseViewTests(unittest.TestCase):
    def test_pause_presentation_is_additive_and_keeps_exact_state(self):
        for status, category in (
            ("WAITING_FOR_USER", "your_decision"),
            ("PAUSED_AUTH", "your_environment"),
            ("PAUSED_RATE_LIMIT", "model_or_provider"),
            ("PAUSED_COMPLETION_GATE", "autocode"),
        ):
            with self.subTest(status=status):
                state = {"status": status, "settings": {}, "workspace": "/tmp/my project", "run_dir": "/tmp/my run"}
                original = copy.deepcopy(state)
                view = run_view.view(state)
                self.assertEqual(status, view["status"])
                self.assertEqual(category, view["pause_category"])
                self.assertTrue(view["pause_category_label"])
                self.assertIn("autocode", view["next_command"])
                self.assertIn("--run-dir", view["next_command"])
                self.assertIn("explanation", view)
                self.assertIn("recovery", view)
                self.assertEqual(original, state)

    def test_no_progress_limit_is_only_offered_when_that_bound_caused_the_pause(self):
        for reason, offered in (
            ("Repeated unchanged implementation batches require review: 3 batches", True),
            ("Recovery novelty: the source delta is empty", False),
        ):
            with self.subTest(reason=reason):
                view = run_view.view(
                    {
                        "status": "PAUSED_NO_PROGRESS",
                        "stop_reason": reason,
                        "no_progress_batches": 3,
                        "settings": {"limits": {"no_progress_batches": 3}},
                    }
                )
                self.assertEqual(offered, "--no-progress-limit" in view["next_command"])
                self.assertIn("--resume-paused", view["next_command"])

    def test_running_and_complete_views_offer_no_pause_action(self):
        for status in ("RUNNING", "TASK_COMPLETE", "COMPLETE"):
            with self.subTest(status=status):
                view = run_view.view({"status": status, "settings": {}})
                self.assertIsNone(view["pause_category"])
                self.assertIsNone(view["pause_category_label"])
                if status == "RUNNING":
                    self.assertEqual("autocode --run-dir RUN_DIR --no-chat", view["next_command"])
                else:
                    self.assertIsNone(view["next_command"])


class PauseCommandTests(unittest.TestCase):
    def command(self, need, **kwargs):
        import autocode_pause_command

        return autocode_pause_command.command(need, run_dir=Path("/tmp/run with spaces"), **kwargs)

    def parse(self, text):
        words = shlex.split(text)
        environ = {}
        while words and words[0] != "autocode":
            name, value = words.pop(0).split("=", 1)
            environ[name] = value
        self.assertEqual("autocode", words.pop(0))
        with mock.patch.dict("os.environ", environ):
            args, _ = autocode_args.parse(None, words, autocode_configure.DEFAULT_ROLE_MODELS)
        return args, words

    def test_question_command_carries_the_current_token_privately(self):
        token = "request token 'with quotes'"
        text = self.command({"kind": "answer", "resolver_token": token, "questions": [{"id": "Q1"}]})
        args, argv = self.parse(text)
        self.assertEqual(["Q1=ANSWER"], args.answer)
        self.assertEqual(token, args.resolver_token)
        self.assertNotIn(token, argv)
        self.assertEqual(Path("/tmp/run with spaces"), args.run_dir)

    def test_core_named_deterministic_recovery_is_a_continue_action(self):
        text = self.command(
            {
                "kind": "continue",
                "abandon_stage": "001/requirements-gather-01",
                "action": "--abandon-stage 001/requirements-gather-01 then --resume-paused",
            }
        )
        _, argv = self.parse(text)
        self.assertEqual(
            ["--run-dir", "/tmp/run with spaces", "--abandon-stage", "001/requirements-gather-01"],
            argv,
        )

    def test_operational_request_uses_its_response_consumer(self):
        text = self.command(
            {
                "kind": "answer",
                "resolver_scope": "operational_exhaustion",
                "resolver_request_id": "request-7",
                "resolver_token": "current-token",
            }
        )
        args, _ = self.parse(text)
        self.assertEqual("request-7", args.resolver_request)
        self.assertEqual("provide_information", args.resolver_response)
        self.assertEqual("WHAT CHANGED", args.resolver_message)
        self.assertFalse(args.answer)

    def test_unissued_question_republishes_instead_of_suggesting_a_tokenless_answer(self):
        args, _ = self.parse(self.command({"kind": "answer", "questions": [{"id": "Q1"}]}))
        self.assertFalse(args.chat)
        self.assertFalse(args.answer)
        self.assertIsNone(args.resolver_token)

    def test_approval_uses_the_displayed_plan_token_without_resuming(self):
        args, argv = self.parse(self.command({"kind": "approve_plan", "token": "plan-token"}))
        self.assertEqual("plan-token", args.approve_goal)
        self.assertNotIn("plan-token", argv)
        self.assertFalse(args.resume_paused)

    def test_review_names_each_criterion_and_current_review_token(self):
        args, _ = self.parse(self.command({"kind": "review", "criteria": ["AC1", "AC2"], "token": "review-token"}))
        self.assertEqual(["AC1", "AC2"], args.approve_review)
        self.assertEqual("review-token", args.review_token)

    def test_retry_uses_its_job_token_and_not_a_resolver_token(self):
        args, argv = self.parse(
            self.command(
                {
                    "kind": "retry_job",
                    "job_retry_token": "job-token",
                    "action": "--resume-paused --retry-failed-stage --job-retry-token TOKEN",
                }
            )
        )
        self.assertTrue(args.resume_paused)
        self.assertTrue(args.retry_failed_stage)
        self.assertEqual("job-token", args.job_retry_token)
        self.assertIsNone(args.resolver_token)
        self.assertNotIn("job-token", argv)

    def test_private_retry_actions_are_executable_and_bind_the_displayed_token(self):
        token = "current job token 'with quotes'"
        actions = (
            "--resume-paused --retry-failed-stage --job-retry-token - (reads AUTOCODE_JOB_RETRY_TOKEN)",
            "--answer route-analyst=MODEL --job-retry-token - (reads AUTOCODE_JOB_RETRY_TOKEN)",
            "--resume-paused --retry-failed-stage --job-retry-token -",
        )
        for action in actions:
            with self.subTest(action=action):
                text = self.command({"kind": "retry_job", "job_retry_token": token, "action": action})
                args, argv = self.parse(text)
                self.assertEqual(token, args.job_retry_token)
                self.assertNotIn(token, argv)
                self.assertNotIn("(reads", argv)
                self.assertNotIn("AUTOCODE_JOB_RETRY_TOKEN)", argv)
                self.assertIsNone(args.resolver_token)
                if "--answer" in action:
                    self.assertEqual(["route-analyst=MODEL"], args.answer)
                    self.assertFalse(args.retry_failed_stage)
                else:
                    self.assertTrue(args.resume_paused)
                    self.assertTrue(args.retry_failed_stage)

    def test_a_refused_job_names_another_model_before_retry(self):
        text = self.command(
            {
                "kind": "retry_job",
                "job_retry_token": "job-token",
                "route": {"question_id": "route-analyst", "cause": "content_filter"},
                "action": "--answer route-analyst=MODEL --job-retry-token TOKEN",
            }
        )
        args, _ = self.parse(text)
        self.assertEqual(["route-analyst=MODEL"], args.answer)
        self.assertEqual("job-token", args.job_retry_token)
        self.assertFalse(args.retry_failed_stage)

    def test_unreconciled_attempt_is_abandoned_before_any_resume(self):
        args, _ = self.parse(
            self.command(
                {
                    "kind": "resume",
                    "abandon_stage": "003/builder-01",
                    "action": "--abandon-stage 003/builder-01 then --resume-paused",
                }
            )
        )
        self.assertEqual("003/builder-01", args.abandon_stage)
        self.assertFalse(args.resume_paused)

    def test_missing_source_or_binding_offers_inspection_without_retry(self):
        for need in ({"kind": "recover_source"}, {"kind": "resume", "new_run_required": True}, {"kind": "retry_job"}):
            with self.subTest(need=need):
                args, _ = self.parse(self.command(need))
                self.assertTrue(args.explain)
                self.assertFalse(args.resume_paused)
                self.assertFalse(args.retry_failed_stage)

    def test_existing_recovery_control_precedes_generic_resume(self):
        args, _ = self.parse(
            self.command(
                {"kind": "resume", "action": "--resume-paused --retry-builder M2"},
                resume_flags="--resume-paused --max-seconds N",
            )
        )
        self.assertTrue(args.resume_paused)
        self.assertEqual(["M2"], args.retry_builder)
        self.assertIsNone(args.max_seconds)

    def test_terminal_stop_and_a_dependency_without_a_producer_need_no_recovery_command(self):
        self.assertIsNone(self.command({"kind": "resume"}, terminal=True))
        self.assertIsNone(self.command({"kind": "dependency"}))
        self.assertIsNone(self.command(None))

    def test_a_dependency_names_the_wait_on_its_producer_run(self):
        need = {
            "kind": "dependency",
            "producer_run": "/tmp/producer run",
            "producer_workspace": "/tmp/producer ws",
        }
        self.assertEqual(
            "autocode wait --run-dir '/tmp/producer run' --workspace '/tmp/producer ws'",
            self.command(need),
        )
        # Without the workspace the wait still resolves it from the producer's own checkpoint.
        self.assertEqual(
            "autocode wait --run-dir '/tmp/producer run'",
            self.command({"kind": "dependency", "producer_run": "/tmp/producer run"}),
        )


class RecoverySuffixTests(unittest.TestCase):
    """#909: pause/rejection messages end with the exact recovery command and
    its live token, refreshed at render time; nothing when no recovery exists."""

    def recovery(self, need, **kwargs):
        import autocode_pause_command

        return autocode_pause_command.recovery(need, run_dir=Path("/tmp/run"), **kwargs)

    def test_the_suffix_carries_the_live_token_and_the_answer_command(self):
        suffix = self.recovery({"kind": "answer", "resolver_token": "live-token", "questions": [{"id": "Q1"}]})
        self.assertIn("live-token", suffix)
        self.assertIn("--answer", suffix)
        self.assertIn("Q1=", suffix)
        self.assertIn("--resolver-token", suffix)

    def test_no_recovery_never_emits_an_impossible_command(self):
        cases = (
            (None, {}),
            ({"kind": "answer"}, {"terminal": True}),
            # A dependency without its producer run names nothing rather than a guess.
            ({"kind": "dependency"}, {}),
        )
        for need, kwargs in cases:
            with self.subTest(need=need, kwargs=kwargs):
                self.assertEqual("", self.recovery(need, **kwargs))

    def test_a_dependency_recovery_watches_the_producer(self):
        suffix = self.recovery({"kind": "dependency", "producer_run": "/tmp/producer"})
        self.assertIn("-- recovery: autocode wait --run-dir /tmp/producer", suffix)

    def test_a_changed_token_is_rendered_fresh_not_from_a_stale_copy(self):
        stale = {"kind": "answer", "resolver_token": "stale-token", "questions": [{"id": "Q1"}]}
        fresh = {"kind": "answer", "resolver_token": "fresh-token", "questions": [{"id": "Q1"}]}
        self.assertIn("stale-token", self.recovery(stale))
        rendered = self.recovery(fresh)
        self.assertIn("fresh-token", rendered)
        self.assertNotIn("stale-token", rendered)

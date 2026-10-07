"""A provider's content-filter refusal is a typed stop that names the model and asks for another one.

The fixture is a trimmed copy of a live OpenCode Builder log (ladder-18-durable-lease-queue,
glm-mimo profile, 2026-10-05): after about 17k reasoning tokens on
xiaomi-token-plan-sgp/mimo-v2.6-pro the stream holds the model's text "The request was rejected
because it was considered high risk", a ``content-filter`` step finish and a
``ContentFilterError`` event, and opencode exited 1. Before this the stop was an uncertain exit
that never said the provider had refused the response. The sibling fixture is the same log
without its error event: the ``content-filter`` finish alone is the refusal (#464), and the
stop is the same whether opencode exited 1 or 0. No provider runs: pure functions, and the
runner's own exit handling with the provider process faked.
"""
import contextlib
import io
import json
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from .supervision_fixture import launcher

import autocode as runner
import autocode_dispatch as dispatch
import autocode_goals as goals
import autocode_job_failure as job_failure
import autocode_provider_refusal as provider_refusal
import autocode_quota_route as quota_route
import autocode_run_view as run_view
import autocode_support as support
import autocode_worker_quota as worker_quota
from goal_fixtures import approve_fixture

FIXTURES = Path(__file__).resolve().parents[1] / "tools" / "fixtures"
FIXTURE = FIXTURES / "opencode-content-filter-run.jsonl"
FINISH_ONLY = FIXTURES / "opencode-content-filter-finish-run.jsonl"
MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
GLM = "zai-coding-plan/glm-5.3"
BLOCKED = "The response was blocked by the provider's content filter"
FINISHED = "OpenCode's last step finished with reason content-filter"
REFUSED_ON_MIMO = f"Builder: the provider's content filter refused the response on {MIMO} "


class ContentFilterClassificationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def log(self, rows, name="builder-01.jsonl"):
        path = self.root / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    def test_the_captured_opencode_stream_is_a_content_filter_refusal(self):
        self.assertEqual("PAUSED_CONTENT_FILTER", support.failure_status(FIXTURE))
        rows = support.events(FIXTURE)
        self.assertEqual({"error": "ContentFilterError", "message": BLOCKED}, provider_refusal.refusal(rows))
        self.assertEqual(f"Builder: the provider's content filter refused the response on {MIMO} "
                         f"(ContentFilterError: {BLOCKED}); the same model is likely to refuse it again",
                         provider_refusal.explain(rows, job="Builder", model=MIMO))

    def test_the_models_own_words_never_classify(self):
        raw = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
        # Everything up to and including the model's "rejected ... high risk" text, then a plain exit.
        cut = next(i for i, row in enumerate(raw) if "high risk" in (row.get("part") or {}).get("text", ""))
        path = self.log(raw[:cut + 1] + [{"type": "error", "sessionID": raw[0]["sessionID"],
                                          "error": {"name": "UnknownError", "data": {"message": "exit status 1"}}}])
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(path))
        self.assertIsNone(provider_refusal.explain(support.events(path), job="Builder", model=MIMO))
        # The same words closed by an ordinary "stop" finish are a completed turn, not a refusal.
        finish = raw[cut + 1]
        self.assertEqual("content-filter", finish["part"]["reason"])
        path = self.log(raw[:cut + 1] + [{**finish, "part": {**finish["part"], "reason": "stop"}}])
        self.assertTrue(any(row["type"] == "turn.completed" for row in support.events(path)))
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(path))
        self.assertIsNone(provider_refusal.refusal(support.events(path)))

    def test_a_content_filter_finish_without_an_error_event_is_a_refusal(self):
        self.assertEqual(FIXTURE.read_text().splitlines()[:-1], FINISH_ONLY.read_text().splitlines())
        self.assertEqual("PAUSED_CONTENT_FILTER", support.failure_status(FINISH_ONLY))
        rows = support.events(FINISH_ONLY)
        self.assertEqual({"error": "content_filter", "message": FINISHED}, provider_refusal.refusal(rows))
        self.assertEqual(REFUSED_ON_MIMO + f"(content_filter: {FINISHED}); the same model is likely to refuse it again",
                         provider_refusal.explain(rows, job="Builder", model=MIMO))
        # Both streams account the same reported usage, as a finished (not partial) failed turn.
        for path in (FIXTURE, FINISH_ONLY):
            with self.subTest(path=path.name):
                metrics = support.event_metrics(path)
                self.assertEqual({"input_tokens": 86125, "cached_input_tokens": 58880, "output_tokens": 17081,
                                  "reasoning_output_tokens": 16901}, metrics["provider_tokens"])
                self.assertEqual((False, 0), (metrics["provider_tokens_partial"], metrics["completed_turns"]))

    def test_typed_codes_and_provider_messages_classify_and_other_stops_keep_theirs(self):
        cases = (({"type": "turn.failed", "error": {"code": "content_filter", "message": "filtered"}}, "content_filter"),
                 ({"type": "turn.failed", "error": {"type": "content-filter"}}, "content-filter"),
                 ({"type": "error", "message": "Response blocked by the content filter"}, "content_filter"))
        for row, name in cases:
            with self.subTest(row=row):
                path = self.log([{"type": "thread.started", "thread_id": "t"}, row])
                self.assertEqual("PAUSED_CONTENT_FILTER", support.failure_status(path))
                self.assertEqual(name, provider_refusal.refusal(support.events(path))["error"])
        for message, status in (("subscription usage limit reached", "PAUSED_BUDGET"),
                                ("Selected model is at capacity", "PAUSED_PROVIDER_CAPACITY"),
                                ("rate limit reached (429)", "PAUSED_RATE_LIMIT")):
            with self.subTest(message=message):
                path = self.log([{"type": "error", "error": {"message": message}}])
                self.assertEqual(status, support.failure_status(path))


class ContentFilterRouteTests(unittest.TestCase):
    """The refusal publishes the #184 model question for the refused role, with configured candidates."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.events = self.root / "builder-01.jsonl"

    def state(self, *, source=FIXTURE, exit_code=1, **extra_roles):
        # The glm-mimo scenario profile: MiMo builds and plans, GLM checks.
        shutil.copyfile(source, self.events)
        roles = {"terra": {"engine": "opencode", "model": MIMO}, "astra": {"engine": "opencode", "model": MIMO},
                 "sol": {"engine": "opencode", "model": GLM}, "completion": {"engine": "opencode", "model": GLM},
                 **{role: {"engine": "opencode", "model": model} for role, model in extra_roles.items()}}
        return {"status": "WAITING_FOR_USER", "settings": {"engine": "opencode", "roles": roles},
                "sessions": {"terra": "ses_builder"},
                "active_stage": {"stage": "terra", "role": "terra", "iteration": 1, "exit_code": exit_code,
                                 "output": str(self.root / "builder-01.json"), "events": str(self.events),
                                 "launch_route": {"engine": "opencode", "model": MIMO}}}

    def question(self, state):
        attempt = quota_route.stopped_attempt(state, failure_status=support.failure_status)
        return attempt, quota_route.question(state, attempt, cross_check=dispatch.enforce_cross_model_verification)

    def test_the_refused_builder_is_asked_for_another_model_and_no_configured_one_fits(self):
        attempt, asked = self.question(self.state())
        self.assertEqual(("terra", "001/builder-01", MIMO, "PAUSED_CONTENT_FILTER", True),
                         tuple(attempt[key] for key in ("role", "attempt_id", "model", "pause_status", "active")))
        self.assertEqual("route-terra", asked["id"])
        self.assertTrue(asked["question"].startswith("Builder's model was refused by its provider's content filter"))
        self.assertIn(f"The Builder stopped on {MIMO}", asked["why"])
        self.assertEqual(("content_filter", MIMO, "", [], False),
                         (asked["cause"], asked["stopped_model"], asked["proposed_default"], asked["options"],
                          asked["delegable"]))
        # GLM 5.3 is the Tester's model: as the Builder it would grade its own work.
        self.assertEqual([], asked["candidates"])
        self.assertEqual(f"No other configured model passes the launch rules for the Builder (refused: {GLM}); "
                         "name one from another provider.", asked["recommendation"])
        advice = quota_route.advice(asked, attempt["attempt_id"])
        self.assertIn("--answer route-terra=MODEL --resolver-token TOKEN", advice)
        self.assertIn("--abandon-stage 001/builder-01, then --resume-paused --terra-model MODEL", advice)
        self.assertTrue(advice.endswith(asked["recommendation"]))

    def test_a_finish_only_refusal_at_a_clean_exit_asks_the_same_question(self):
        for source, exit_code in ((FINISH_ONLY, 0), (FINISH_ONLY, 1), (FIXTURE, 0)):
            with self.subTest(source=source.name, exit_code=exit_code):
                attempt, asked = self.question(self.state(source=source, exit_code=exit_code))
                self.assertEqual(("terra", MIMO, "PAUSED_CONTENT_FILTER", True),
                                 tuple(attempt[key] for key in ("role", "model", "pause_status", "active")))
                self.assertEqual(("route-terra", "content_filter", MIMO),
                                 (asked["id"], asked["cause"], asked["stopped_model"]))
                self.assertTrue(asked["question"].startswith("Builder's model was refused by its provider's content filter"))

    def test_candidates_are_other_providers_models_that_pass_the_cross_model_rule(self):
        state = self.state(requirements="anthropic/claude-sonnet-5-5", plan_reviewer="xiaomi-token-plan-sgp/mimo-v2.6")
        _, asked = self.question(state)
        # The refusing provider's other model is left out; the GLM checker model is refused.
        self.assertEqual(["anthropic/claude-sonnet-5-5"], asked["candidates"])
        self.assertEqual("Configured models that pass the launch rules for the Builder: anthropic/claude-sonnet-5-5.",
                         asked["recommendation"])

    def test_a_refused_member_retry_points_only_at_what_is_open(self):
        # #541: --retry-builder of a refused batch member never names a question that is not open. With
        # none open, the answered member's stop is collected again (its question is asked again, nothing
        # launches); another refused member is asked about after it.
        shutil.copyfile(FIXTURE, self.events)
        rows, results = {}, {}
        for mid in ("M1", "M2"):
            directory = self.root / mid
            directory.mkdir()
            (directory / "state.json").write_text(json.dumps({"settings": {"roles": {"terra": {"model": MIMO}}}}))
            rows[mid] = {"milestone_id": mid, "run_dir": str(directory), "workspace": str(directory),
                         "status": "PAUSED_CONTENT_FILTER"}
            results[mid] = {"status": "PAUSED_CONTENT_FILTER", "quota_worker": {
                "role": "terra", "milestone_id": mid, "run_dir": str(directory), "workspace": str(directory),
                "model": MIMO, "events": str(self.events), "attempt_id": "001/builder-01"}}
        origin = {"pause_status": "PAUSED_CONTENT_FILTER",
                  "quota_worker": {**results["M1"]["quota_worker"], "pause_status": "PAUSED_CONTENT_FILTER"}}
        state = {"status": "PAUSED_CONTENT_FILTER", "next_stage": "orchestrator",
                 "settings": {"roles": {"terra": {"model": MIMO}}},
                 "orchestration_batch": {"status": "BUILDING", "workers": list(rows.values())},
                 "resolver": {"human_response_frontier": {"request_id": "r1", "pause_status": "PAUSED_CONTENT_FILTER"},
                              "human_escalations": {"r1": {"status": "consumed",
                                                           "identity": {"proposal": {"origin": origin}}}}}}
        retry = lambda mid, asked: worker_quota.refused_retry(state, rows[mid], results[mid], asked=asked)
        self.assertIn("in answer to its open route-terra question", str(retry("M1", "M1")))
        self.assertIn("once the open request about Builder M1 is answered", str(retry("M2", "M1")))
        again = retry("M1", None)
        self.assertEqual(("PAUSED_CONTENT_FILTER", "M1"), (again.status, again.quota_worker["milestone_id"]))
        self.assertIn("Milestone M1: Builder: the provider's content filter refused the response", str(again))
        first = "continues from Builder M1's stop first, with --resume-paused --retry-builder M1"
        self.assertIn(first, str(retry("M2", None)))
        # A quota-stopped answered member reruns rather than being asked about; the message says only what is true.
        for record in (state, origin, origin["quota_worker"], state["resolver"]["human_response_frontier"]):
            record["pause_status" if record is not state else "status"] = "PAUSED_BUDGET"
        rows["M1"]["status"] = "PAUSED_BUDGET"
        self.assertIn(first, str(retry("M2", None)))
        self.assertNotIn("asks about Builder M1", str(retry("M2", None)))

    def test_a_batch_member_lists_and_accepts_only_models_its_answer_takes(self):
        # #465: Builder M2 of a parallel batch was refused on MiMo after a sibling's answer had moved the
        # Builder route to another provider's model. Its question and answer are about the model M2 ran on,
        # and the answer must also leave that worker's model family (#458): a MiMo elsewhere is not listed.
        moved, mimo_elsewhere = "openai/gpt-6-luna", "openrouter/xiaomi/mimo-v2.6"
        state = self.state(plan_reviewer=mimo_elsewhere)
        state["settings"]["roles"]["terra"]["model"] = moved
        member = {"role": "terra", "stage": "terra", "milestone_id": "M2", "model": MIMO,
                  "pause_status": "PAUSED_CONTENT_FILTER", "active": True}
        asked = worker_quota.question(state, member, cross_check=dispatch.enforce_cross_model_verification,
                                      family=dispatch._model_family)
        self.assertEqual(("Builder (milestone M2)", MIMO, MIMO, [moved]),
                         (asked["job"], asked["current_model"], asked["stopped_model"], asked["candidates"]))
        self.assertEqual([mimo_elsewhere, moved], worker_quota.question(
            state, member, cross_check=dispatch.enforce_cross_model_verification)["candidates"])
        # Every listed model passes the parallel answer path's checks; the unlisted MiMo fails one.
        for model in asked["candidates"]:
            quota_route.validate(state, "terra", model, configured_tool=False, current=MIMO,
                                 cross_check=dispatch.enforce_cross_model_verification)
            worker_quota.validate_model(model, member, dispatch._model_family)
        quota_route.validate(state, "terra", mimo_elsewhere, configured_tool=False, current=MIMO,
                             cross_check=dispatch.enforce_cross_model_verification)
        with self.assertRaisesRegex(ValueError, "choose another model family"):
            worker_quota.validate_model(mimo_elsewhere, member, dispatch._model_family)
        record = quota_route.assign(state, "terra", moved, at="t", via="answer", attempt=member, current=MIMO)
        self.assertEqual((MIMO, moved, "PAUSED_CONTENT_FILTER"), (record["from"], record["to"], record["pause_status"]))

    def test_the_view_answer_and_record_carry_the_refusal(self):
        state = self.state()
        attempt, asked = self.question(state)
        state["pending_questions"] = [asked]
        self.assertEqual({"question_id": "route-terra", "role": "terra", "job": "Builder", "current_model": MIMO,
                          "engine": "opencode", "cause": "content_filter", "stopped_model": MIMO, "candidates": []},
                         run_view.view(state)["needs"]["route"])
        origin = {"pause_status": "PAUSED_CONTENT_FILTER"}
        model = "anthropic/claude-sonnet-5-5"
        self.assertEqual((asked, model), quota_route.parse_answer([f"route-terra={model}"], [asked], origin))
        record = quota_route.assign(state, "terra", model, at="t", via="answer", attempt=attempt)
        self.assertEqual(("Builder", MIMO, model, "PAUSED_CONTENT_FILTER"),
                         (record["job"], record["from"], record["to"], record["pause_status"]))
        self.assertNotIn("terra", state["sessions"])

    def test_a_flag_alone_under_the_uncertain_refusal_is_refused_with_the_cause(self):
        state = self.state()
        changed = {**state["settings"], "roles": {**state["settings"]["roles"],
                                                  "terra": {"engine": "opencode", "model": "anthropic/claude-sonnet-5-5"}}}
        refusal = quota_route.resume_refusal(state, state["settings"], changed,
                                             failure_status=support.failure_status, abandoning=None)
        self.assertIn("The Builder attempt that its provider's content filter refused is still uncertain", refusal)
        self.assertIsNone(quota_route.resume_refusal(state, state["settings"], changed,
                                                     failure_status=support.failure_status,
                                                     abandoning="001/builder-01"))

    def test_the_flag_refusal_offers_the_answer_only_where_the_request_takes_it(self):
        # The saved events classify as a refusal whether or not the published request asks for a model:
        # a stop published as uncertain (a session-ID mismatch, or a run paused before #464 typed a
        # finish-only refusal) asks no route question, and --answer route-terra is refused there.
        state = self.state(source=FINISH_ONLY, exit_code=0)
        model = "anthropic/claude-sonnet-5-5"
        changed = {**state["settings"], "roles": {**state["settings"]["roles"],
                                                  "terra": {"engine": "opencode", "model": model}}}
        _, asked = self.question(state)
        uncertain, refused = {"pause_status": "PAUSED_UNCERTAIN_STAGE"}, {"pause_status": "PAUSED_CONTENT_FILTER"}
        for questions, origin, answerable in (([], uncertain, False), ([asked], uncertain, False),
                                              ([], refused, False), ([asked], refused, True)):
            with self.subTest(questions=[q["id"] for q in questions], origin=origin["pause_status"]):
                refusal = quota_route.resume_refusal(state, state["settings"], changed,
                                                     failure_status=support.failure_status, abandoning=None,
                                                     questions=questions, origin=origin)
                self.assertIn("is still uncertain; --terra-model is not saved", refusal)
                self.assertIn("--abandon-stage 001/builder-01, then --resume-paused --terra-model MODEL", refusal)
                # Every command the refusal names is one the CLI accepts at this request (#288/#301).
                self.assertEqual(answerable, "--answer route-terra=MODEL" in refusal, refusal)
                if answerable:
                    self.assertEqual((asked, model), quota_route.parse_answer([f"route-terra={model}"], questions, origin))
                else:
                    with self.assertRaises(ValueError):
                        quota_route.parse_answer([f"route-terra={model}"], questions, origin)


class ContentFilterAtCleanExitTests(unittest.TestCase):
    """A provider that exits 0 after its content filter refused the response stops the same typed way (#464).

    The runner's own exit handling, with the provider process faked to write a saved log and exit 0;
    an exit-0 stop that is not a refusal stays uncertain, so truncation keeps its own recovery."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.runs = 0

    def build(self, rows, *, sessions=True, saved_session=None, exited=(0, False)):
        """The Builder's run_role, in a fresh run, over a provider that writes ``rows`` and exits 0: (stop, state).

        With ``saved_session`` the provider is a configured tool that prints OpenCode events (as Kilo
        does) and resumes the Builder's saved session; the built-in OpenCode Builder starts a new one.
        ``exited`` is the provider's (exit code, timed out) as the runner's wait returns it."""
        self.runs += 1
        self.run = self.root / ".autocode/runs" / f"fixture-{self.runs}"
        self.run.mkdir(parents=True)
        state = {"version": 2, "workspace": str(self.root), "task": "Fixture", "status": "RUNNING", "iteration": 1,
                 "sessions": {"terra": saved_session} if saved_session else {}, "stages": [], "history": [],
                 "settings": {"engine": "opencode", "roles": {"terra": {"model": MIMO}, "astra": {"model": MIMO},
                                                              "sol": {"model": GLM}, "completion": {"model": GLM}}}}
        approve_fixture(state, goals)

        class Child:
            pid = 987654321

            def __init__(child, command, **kwargs):
                kwargs["stdout"].write("".join(json.dumps(row) + "\n" for row in rows))

        with patch.object(runner.opencode, "launch", return_value=(["fixture-provider"], {}, {})), \
             patch.object(runner.opencode, "SUPPORTS_SESSIONS", sessions, create=True), \
             patch.object(runner.opencode, "CONFIGURED", bool(saved_session), create=True), \
             patch.object(runner.opencode, "NAME", "kilo", create=True), \
             patch.object(runner.readonly_events, "prepare_opencode_snapshots"), \
             patch.object(runner.supervision, "launch", launcher(Child)), \
             patch.object(support, "snapshot", return_value={"head": "h", "files": {}, "revision": "r"}), \
             patch.object(runner.processes, "preflight", return_value=None), \
             patch.object(runner.processes, "wait_for_stage", return_value=exited), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(support.Paused) as caught:
                runner.run_role(role="terra", prompt="Fixture", sandbox="workspace-write", workspace=self.root,
                                run_dir=self.run, state=state, schema=runner.SCHEMA_DIR / "v2/terra-report.schema.json",
                                model=MIMO, allow_write=True, dry_run=False)
        return caught.exception, state

    def reconcile(self, state):
        """What resuming the saved stop says: reconcile_active, which never relaunches the provider."""
        with patch.object(runner, "assert_stage_stopped"), patch.object(runner.subprocess, "Popen") as popen:
            with self.assertRaises(support.Paused) as caught:
                runner.reconcile_active(state, self.run, self.root)
            popen.assert_not_called()
        return caught.exception

    def test_a_refusal_at_exit_0_names_the_job_and_model_with_or_without_an_error_event(self):
        for source, detail in ((FIXTURE, f"ContentFilterError: {BLOCKED}"), (FINISH_ONLY, f"content_filter: {FINISHED}")):
            with self.subTest(fixture=source.name):
                stop, state = self.build([json.loads(line) for line in source.read_text().splitlines()])
                refused = REFUSED_ON_MIMO + f"({detail}); the same model is likely to refuse it again"
                self.assertEqual("PAUSED_CONTENT_FILTER", stop.status)
                self.assertTrue(str(stop).startswith(refused + ". terra exited 0; reconcile "), str(stop))
                self.assertIn("no automatic replay", str(stop))
                self.assertEqual((0, MIMO), (state["active_stage"]["exit_code"],
                                             state["active_stage"]["launch_route"]["model"]))
                resumed = self.reconcile(state)
                self.assertEqual("PAUSED_CONTENT_FILTER", resumed.status)
                self.assertTrue(str(resumed).startswith(refused + ". "), str(resumed))
                self.assertIn("--abandon-stage 001/builder-01", str(resumed))
                attempt = quota_route.stopped_attempt(state, failure_status=support.failure_status)
                self.assertEqual(("terra", MIMO, "PAUSED_CONTENT_FILTER"),
                                 (attempt["role"], attempt["model"], attempt["pause_status"]))

    def test_a_report_file_provider_that_exits_0_without_a_report_names_the_refusal(self):
        refusal = {"type": "turn.failed", "error": {"code": "content_filter", "message": "filtered"}}
        stop, state = self.build([{"type": "thread.started", "thread_id": "t"}, refusal], sessions=False)
        self.assertEqual("PAUSED_CONTENT_FILTER", stop.status)
        self.assertTrue(str(stop).startswith(REFUSED_ON_MIMO + "(content_filter: filtered)"), str(stop))
        self.assertEqual("PAUSED_CONTENT_FILTER", self.reconcile(state).status)
        stop, _ = self.build([{"type": "thread.started", "thread_id": "t"}], sessions=False)
        self.assertEqual(("PAUSED_UNCERTAIN_STAGE", "Process exited without a report file"), (stop.status, str(stop)))

    def test_session_provenance_is_checked_before_the_refusal(self):
        # Refusals from an unexpected or unnamed session are never typed or answered with a model.
        # A supervised attempt without a collected exit first needs verified ownership; legacy
        # attempts and collected exits retain the original session-provenance stop.
        missing = "Provider returned a missing or unexpected session ID"
        recovered = "Recovered response belongs to an unexpected session"
        human = runner.resolver_runtime.human
        for saved, written in self.unexpected_refusals():
            for supervised in (True, False):
                for saved_exit in (0, None, -2, -15):
                    with self.subTest(saved_session=saved, supervised=supervised, saved_exit=saved_exit):
                        stop, state = self.build(written, saved_session=saved)
                        self.assertEqual(("PAUSED_UNCERTAIN_STAGE", missing), (stop.status, str(stop)))
                        self.assertEqual((0, saved), (state["active_stage"]["exit_code"],
                                                      state["active_stage"]["expected_session"]))
                        thread = runner.format_correction.event_thread_id(Path(state["active_stage"]["events"]))
                        self.assertTrue(thread != saved if saved else thread is None, thread)
                        self.assertEqual("PAUSED_CONTENT_FILTER", support.failure_status(state["active_stage"]["events"]))
                        state["active_stage"]["exit_code"] = saved_exit
                        if not supervised:
                            state["active_stage"].pop("supervision")
                            state["active_stage"].pop("owner")
                        resumed = self.reconcile(state)
                        if supervised and saved_exit is None:
                            self.assertEqual("PAUSED_UNCERTAIN_STAGE", resumed.status)
                            self.assertIn("lacks a verified uninterrupted result", str(resumed))
                            self.assertIn("--abandon-stage 001/builder-01", str(resumed))
                            self.assertIn("No provider call or recovered report is automatically accepted", str(resumed))
                        else:
                            self.assertEqual(("PAUSED_UNCERTAIN_STAGE", recovered), (resumed.status, str(resumed)))
                        self.assertIn("active_stage", state)
                        self.assertEqual({"terra": saved} if saved else {}, state["sessions"])
                        # The retained stop asks no model question for either ownership route.
                        self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(runner, state, self.run, resumed))
                        self.assertEqual("PAUSED_UNCERTAIN_STAGE", state[human.PRIVATE]["origin"]["pause_status"])
                        self.assertEqual([], human.internal_questions(state))

    def test_a_resumed_refusal_from_an_unexpected_session_keeps_the_first_stop(self):
        # As in run_role, a timeout names the stop before the session or the response is read, and the
        # refusal is typed for a provider that exited with an error; the resume says the same. A provider
        # that handles the stop signal exits 0, and the timeout still recovers on its own.
        for saved, written in self.unexpected_refusals():
            for exited, status in (((0, True), "PAUSED_PROVIDER_TIMEOUT"), ((-15, True), "PAUSED_PROVIDER_TIMEOUT"),
                                   ((1, False), "PAUSED_CONTENT_FILTER")):
                with self.subTest(saved_session=saved, exited=exited):
                    stop, state = self.build(written, saved_session=saved, exited=exited)
                    self.assertEqual(status, stop.status)
                    resumed = self.reconcile(state)
                    self.assertEqual(status, resumed.status, str(resumed))
                    if status == "PAUSED_PROVIDER_TIMEOUT":
                        self.assertIn(state["active_stage"]["timeout_reason"], str(resumed))
                        with patch.object(support, "snapshot", return_value={"head": "h", "files": {}, "revision": "r"}), \
                             patch.object(runner, "run_role", side_effect=AssertionError("Recovery must not replay")):
                            self.assertTrue(runner.automatically_recover_timed_out_stage(state, self.run, self.root, resumed))
                        self.assertNotIn("active_stage", state)
                        self.assertNotIn("terra", state["sessions"])

    def test_a_timed_out_refusal_resumes_as_its_timeout_from_any_session(self):
        # The resumed stop agrees with run_role's first one whatever the session: the timeout is named before
        # the response is read, and automatic timeout recovery takes it, never a model question (#464).
        rows = [json.loads(line) for line in FINISH_ONLY.read_text().splitlines()]
        for saved in (None, rows[0]["sessionID"], "ses_saved_builder"):
            for exited in ((0, True), (-15, True)):
                with self.subTest(saved_session=saved, exited=exited):
                    stop, state = self.build(rows, saved_session=saved, exited=exited)
                    self.assertEqual("PAUSED_PROVIDER_TIMEOUT", stop.status)
                    resumed = self.reconcile(state)
                    self.assertEqual("PAUSED_PROVIDER_TIMEOUT", resumed.status, str(resumed))
                    with patch.object(support, "snapshot", return_value={"head": "h", "files": {}, "revision": "r"}), \
                         patch.object(runner, "run_role", side_effect=AssertionError("Recovery must not replay")):
                        self.assertTrue(runner.automatically_recover_timed_out_stage(state, self.run, self.root, resumed))

    def test_a_job_refused_from_an_unexpected_session_is_not_named_a_refusal(self):
        # A workflow job's stop is classified from its log by job_failure; when the runner itself stopped on a
        # session it did not expect, the job's stop says so instead of naming a refusal on its model.
        record = {"stage": "review_change", "events": str(FINISH_ONLY), "exit_code": 0, "launch_route": {"model": MIMO}}
        runtime = types.SimpleNamespace(support=support)
        kind, reason = job_failure._reason(runtime, record, support.Paused(
            "PAUSED_UNCERTAIN_STAGE", "Provider returned a missing or unexpected session ID"))
        self.assertEqual("exit", kind)
        self.assertIn("unexpected session", reason)
        self.assertNotIn("content filter", reason)
        refused = support.Paused("PAUSED_CONTENT_FILTER", "Code Reviewer: the provider's content filter refused it")
        self.assertEqual("content_filter", job_failure._reason(runtime, record, refused)[0])

    @staticmethod
    def unexpected_refusals():
        """(saved session, rows): a finish-only refusal from another session, and an error-event one naming none."""
        rows = [json.loads(line) for line in FINISH_ONLY.read_text().splitlines()]
        return (("ses_saved_builder", rows),
                (None, [{"type": "turn.failed", "error": {"code": "content_filter", "message": "filtered"}}]))

    def test_other_clean_exits_without_a_completed_turn_stay_uncertain(self):
        rows = [json.loads(line) for line in FINISH_ONLY.read_text().splitlines()]
        finish = rows[-1]
        for reason, said in (("length", "output token limit"), ("tool-calls", "tool-calls"),
                             ("error", "Process exited without turn.completed")):
            with self.subTest(reason=reason):
                stop, _ = self.build(rows[:-1] + [{**finish, "part": {**finish["part"], "reason": reason}}])
                self.assertEqual("PAUSED_UNCERTAIN_STAGE", stop.status)
                self.assertIn(said, str(stop))
                self.assertNotIn("content filter", str(stop))


if __name__ == "__main__":
    unittest.main()

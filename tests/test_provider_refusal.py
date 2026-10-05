"""A provider's content-filter refusal is a typed stop that names the model and asks for another one.

The fixture is a trimmed copy of a live OpenCode Builder log (ladder-18-durable-lease-queue,
glm-mimo profile, 2026-10-05): after about 17k reasoning tokens on
xiaomi-token-plan-sgp/mimo-v2.6-pro the stream holds the model's text "The request was rejected
because it was considered high risk", a ``content-filter`` step finish and a
``ContentFilterError`` event, and opencode exited 1. Before this the stop was an uncertain exit
that never said the provider had refused the response. Pure functions; no provider runs.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import autocode_dispatch as dispatch
import autocode_provider_refusal as provider_refusal
import autocode_quota_route as quota_route
import autocode_run_view as run_view
import autocode_support as support

FIXTURE = Path(__file__).resolve().parents[1] / "tools" / "fixtures" / "opencode-content-filter-run.jsonl"
MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
GLM = "zai-coding-plan/glm-5.3"
BLOCKED = "The response was blocked by the provider's content filter"


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
        shutil.copyfile(FIXTURE, self.events)

    def state(self, **extra_roles):
        # The glm-mimo scenario profile: MiMo builds and plans, GLM checks.
        roles = {"terra": {"engine": "opencode", "model": MIMO}, "astra": {"engine": "opencode", "model": MIMO},
                 "sol": {"engine": "opencode", "model": GLM}, "completion": {"engine": "opencode", "model": GLM},
                 **{role: {"engine": "opencode", "model": model} for role, model in extra_roles.items()}}
        return {"status": "WAITING_FOR_USER", "settings": {"engine": "opencode", "roles": roles},
                "sessions": {"terra": "ses_builder"},
                "active_stage": {"stage": "terra", "role": "terra", "iteration": 1, "exit_code": 1,
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

    def test_candidates_are_other_providers_models_that_pass_the_cross_model_rule(self):
        state = self.state(requirements="anthropic/claude-sonnet-5-5", plan_reviewer="xiaomi-token-plan-sgp/mimo-v2.6")
        _, asked = self.question(state)
        # The refusing provider's other model is left out; the GLM checker model is refused.
        self.assertEqual(["anthropic/claude-sonnet-5-5"], asked["candidates"])
        self.assertEqual("Configured models that pass the launch rules for the Builder: anthropic/claude-sonnet-5-5.",
                         asked["recommendation"])

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


if __name__ == "__main__":
    unittest.main()

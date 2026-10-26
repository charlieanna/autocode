"""A stage's context ran out, or the provider compacted it: the stop is typed, explained and routable (#1942).

The fixture is a trimmed copy of a live Kilo 7.8.12 Completion Reviewer log (run
20261020-154227-build-a-to-do-cli-named-todo-py-backed-by-a-todo-eaf31819, stage
002/completion-review-20, openai/gpt-6-sol): the first request, resumed from a seventeen-stage
session, was rejected (``ContextOverflowError``, ``context_length_exceeded``); Kilo then summarized
the transcript in two requests (316,805 and 533,595 input tokens), continued from 2,422 tokens and
wrote a BLOCKED report; kilo exited 1. Message texts are placeholders. Before this the stop was
``PAUSED_PROVIDER_UNCERTAIN`` with no word about the context, and a proactive compaction (no error
row at all) was accepted as a whole response. No provider runs: pure functions, the normalizer, the
route question and the launch configuration.
"""

import json
import os
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_context_window as context_window
import autocode_dispatch as dispatch
import autocode_provider_launch as provider_launch
import autocode_quota_route as quota_route
import autocode_request_usage as request_usage
import autocode_support as support
from providers import opencode
from providers.kilocode import KiloProvider

from tests.test_claude_stream import init, result, translate

FIXTURE = Path(__file__).resolve().parents[1] / "tools" / "fixtures" / "kilo-context-overflow-run.jsonl"
OVERFLOW = "Your input exceeds the context window of this model. Please adjust your input and try again."
SOL = "openai/gpt-6-sol"
GLM = "zai-coding-plan/glm-5.3"


def step(session, number, tokens, reason="stop", part_id=None):
    """A completed OpenCode request with ``tokens`` of context, as a step_start/step_finish pair."""
    message = f"msg_{number}"
    return [
        {"type": "step_start", "sessionID": session, "part": {"id": f"start_{number}", "messageID": message}},
        {
            "type": "step_finish",
            "sessionID": session,
            "part": {
                "id": part_id or f"finish_{number}",
                "messageID": message,
                "reason": reason,
                "tokens": {"input": tokens, "output": 10, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            },
        },
    ]


def report(session, number):
    return {
        "type": "text",
        "sessionID": session,
        "part": {"id": f"text_{number}", "messageID": f"msg_{number}", "text": '{"verdict": "PASS"}'},
    }


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def log(self, rows, name="builder-01.jsonl"):
        path = self.root / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    def test_the_captured_kilo_stream_is_a_context_window_stop(self):
        self.assertEqual("PAUSED_CONTEXT_WINDOW", support.failure_status(FIXTURE))
        rows = support.events(FIXTURE)
        self.assertEqual({"error": "ContextOverflowError", "message": OVERFLOW}, context_window.overflow(rows))
        # The two summarizing requests, then the continuation from the summary: the shrink is the signature.
        self.assertEqual({"signal": "request_shrank", "from": 533595, "to": 2422}, context_window.compacted(rows))
        self.assertFalse(any(row["type"] == "turn.completed" for row in rows))
        last = rows[-1]
        self.assertEqual(("turn.failed", "context_compacted"), (last["type"], last["error"]["code"]))
        explained = context_window.explain(rows, job="Completion Reviewer", model=SOL, request_tokens=85418)
        self.assertTrue(
            explained.startswith(f"Completion Reviewer: the request exceeded the model's context window on {SOL} (")
        )
        for words in (
            "ContextOverflowError: " + OVERFLOW,
            "a request held 2422 tokens after the previous one held 533595",
            "last completed request held 85418 input tokens",
            "not accepted",
            "--package",
            "larger context window",
        ):
            self.assertIn(words, explained)
        with self.assertRaisesRegex(RuntimeError, "no successful terminal step"):
            opencode.final_report(FIXTURE)
        # Usage is still accounted, as a failed turn: every completed request, the summarizing ones included.
        metrics = support.event_metrics(FIXTURE)
        self.assertEqual(
            316805 + 533595 + 2422 + 63201 + 6624 + 6186 + 9600 + 72704 + 79232,
            metrics["provider_tokens"]["input_tokens"],
        )
        self.assertEqual((0, False), (metrics["completed_turns"], metrics["provider_tokens_partial"]))

    def test_a_compaction_row_fails_the_turn_without_any_error(self):
        rows = [
            *step("ses_c", 1, 50_000),
            {
                "type": "autocode_compaction",
                "version": 1,
                "sessionID": "ses_c",
                "compaction": {"auto": True, "overflow": False},
            },
            *step("ses_c", 2, 60_000),
            report("ses_c", 2),
        ]
        normalized = opencode.normalized_events(rows)
        self.assertFalse(any(row["type"] == "turn.completed" for row in normalized))
        last = normalized[-1]
        self.assertEqual(("turn.failed", "context_compacted"), (last["type"], last["error"]["code"]))
        self.assertEqual({"signal": "compaction_part", "auto": True, "overflow": False}, last["compaction"])
        self.assertEqual(
            {"input_tokens": 110_000, "cached_input_tokens": 0, "output_tokens": 20, "reasoning_output_tokens": 0},
            last["usage"],
        )
        path = self.log(rows)
        self.assertEqual("PAUSED_CONTEXT_WINDOW", support.failure_status(path))
        explained = context_window.explain(support.events(path), job="Builder", model=GLM)
        self.assertEqual(
            "Builder: the provider compacted the conversation on its own. The response was produced from a "
            "summary, not the whole handoff, so it is not accepted; give the job a smaller task, scope the run "
            "with --package, or name a model with a larger context window",
            explained,
        )
        # A compaction row the bound session did not write is not this session's.
        unbound = {key: value for key, value in rows[2].items() if key != "sessionID"}
        foreign = [*step("ses_c", 1, 50_000), unbound, *step("ses_c", 2, 60_000), report("ses_c", 2)]
        self.assertTrue(any(row["type"] == "turn.completed" for row in opencode.normalized_events(foreign)))

    def test_a_request_that_halves_is_a_compaction_but_smaller_or_small_drops_are_not(self):
        cases = (
            ((40_000, 19_000), {"signal": "request_shrank", "from": 40_000, "to": 19_000}),
            ((40_000, 21_000), None),
            ((20_000, 1_000), None),  # below the floor: small contexts never trip it
            ((50_000, 60_000, 25_000), {"signal": "request_shrank", "from": 60_000, "to": 25_000}),
            ((60_000,), None),
        )
        for sizes, expected in cases:
            with self.subTest(sizes=sizes):
                rows = [row for number, size in enumerate(sizes, 1) for row in step("ses_s", number, size)]
                self.assertEqual(expected, context_window.shrank(rows))
                normalized = opencode.normalized_events(rows + [report("ses_s", len(sizes))])
                self.assertEqual(expected is None, any(row["type"] == "turn.completed" for row in normalized))
        # A repeated update of the same finish part is one request, not a drop and a rise.
        rows = step("ses_r", 1, 40_000) + step("ses_r", 2, 45_000, part_id="finish_1") + step("ses_r", 3, 46_000)
        self.assertEqual([40_000, 46_000], context_window.request_sizes(rows))
        self.assertIsNone(context_window.shrank(rows))

    def test_typed_codes_and_provider_messages_classify_and_the_models_words_never_do(self):
        cases = (
            (
                {"type": "turn.failed", "error": {"code": "context_window_exceeded", "message": "prompt too long"}},
                "context_window_exceeded",
            ),
            (
                {
                    "type": "error",
                    "error": {"message": "Prompt is too long · the request is ~214290 tokens (limit 200000)"},
                },
                "context_window_exceeded",
            ),
            (
                {
                    "type": "error",
                    "error": {
                        "name": "APIError",
                        "data": {
                            "message": "bad request",
                            "responseBody": json.dumps({"error": {"code": "context_length_exceeded"}}),
                        },
                    },
                },
                "context_length_exceeded",
            ),
        )
        for row, name in cases:
            with self.subTest(row=row):
                path = self.log([{"type": "thread.started", "thread_id": "t"}, row])
                self.assertEqual("PAUSED_CONTEXT_WINDOW", support.failure_status(path))
                self.assertEqual(name, context_window.overflow(support.events(path))["error"])
        prose = [
            *step("ses_p", 1, 50_000),
            {
                "type": "text",
                "sessionID": "ses_p",
                "part": {"id": "t", "messageID": "msg_1", "text": "the context window is large; " + OVERFLOW},
            },
        ]
        normalized = opencode.normalized_events(prose)
        self.assertTrue(any(row["type"] == "turn.completed" for row in normalized))
        self.assertIsNone(context_window.found(normalized))
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(self.log(prose)))
        # Other provider stops keep their own status.
        for message, status in (
            ("subscription usage limit reached", "PAUSED_BUDGET"),
            ("rate limit reached (429)", "PAUSED_RATE_LIMIT"),
        ):
            with self.subTest(message=message):
                self.assertEqual(
                    status, support.failure_status(self.log([{"type": "error", "error": {"message": message}}]))
                )

    def test_a_quota_error_naming_the_window_size_is_not_an_overflow(self):
        """A refusal that quotes the model's window size while saying the account is spent stays an account stop."""
        rows = [
            {
                "type": "error",
                "error": {
                    "code": "resource_exhausted",
                    "message": "Your weekly limit is exhausted; this model's context window is 200000 tokens",
                },
            }
        ]
        self.assertIsNone(context_window.overflow(rows))
        self.assertNotEqual("PAUSED_CONTEXT_WINDOW", support.failure_status(self.log(rows)))
        # A provider that says the input exceeded the window still classifies with no typed code at all.
        said = context_window.overflow([{"type": "error", "error": {"message": OVERFLOW}}])
        self.assertEqual(OVERFLOW, said["message"])

    def test_a_shrinking_request_is_a_compaction_only_when_nothing_else_failed(self):
        rows = [*step("ses_k", 1, 120_000), *step("ses_k", 2, 400)]
        self.assertEqual(
            {"overflow": None, "compaction": {"signal": "request_shrank", "from": 120_000, "to": 400}},
            context_window.found(rows),
        )
        explained = [*rows, {"type": "error", "error": {"code": "server_error", "message": "upstream 500"}}]
        self.assertIsNone(context_window.found(explained))
        self.assertNotEqual("PAUSED_CONTEXT_WINDOW", support.failure_status(self.log(explained)))

    def test_claude_compaction_fails_the_call_and_names_the_stop(self):
        boundary = {
            "type": "system",
            "subtype": "compact_boundary",
            "session_id": init()["session_id"],
            "compact_metadata": {"trigger": "auto", "pre_tokens": 150_000, "post_tokens": 20_000},
        }
        rows, code = translate([init(), boundary, result(structured_output={"verdict": "PASS"})], exit_code=0)
        self.assertEqual(1, code)
        last = rows[-1]
        self.assertEqual(("turn.failed", "context_compacted"), (last["type"], last["error"]["code"]))
        self.assertIn("trigger auto, pre_tokens 150000, post_tokens 20000", last["error"]["message"])
        self.assertFalse(any(row.get("item", {}).get("type") == "structured_output" for row in rows))
        self.assertEqual("PAUSED_CONTEXT_WINDOW", support.failure_status(self.log(rows)))


class RouteTests(unittest.TestCase):
    """The stop publishes the model question for the stopped role, asking for a larger window."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.events = self.root / "builder-01.jsonl"
        shutil.copyfile(FIXTURE, self.events)

    def state(self, exit_code=1):
        roles = {
            "terra": {"engine": "opencode", "model": SOL},
            "astra": {"engine": "opencode", "model": SOL},
            "sol": {"engine": "opencode", "model": GLM},
            "completion": {"engine": "opencode", "model": GLM},
        }
        return {
            "status": "WAITING_FOR_USER",
            "settings": {"engine": "opencode", "roles": roles},
            "sessions": {"terra": "ses_builder"},
            "active_stage": {
                "stage": "terra",
                "role": "terra",
                "iteration": 1,
                "exit_code": exit_code,
                "output": str(self.root / "builder-01.json"),
                "events": str(self.events),
                "launch_route": {"engine": "opencode", "model": SOL},
            },
        }

    def test_the_stopped_builder_is_asked_for_a_model_with_a_larger_window(self):
        for exit_code in (1, 0):
            with self.subTest(exit_code=exit_code):
                state = self.state(exit_code)
                attempt = quota_route.stopped_attempt(state, failure_status=support.failure_status)
                self.assertEqual(
                    ("terra", "001/builder-01", SOL, "PAUSED_CONTEXT_WINDOW", True),
                    tuple(attempt[key] for key in ("role", "attempt_id", "model", "pause_status", "active")),
                )
                asked = quota_route.question(state, attempt, cross_check=dispatch.enforce_cross_model_verification)
                self.assertEqual(
                    ("route-terra", "context_window", SOL), (asked["id"], asked["cause"], asked["stopped_model"])
                )
                self.assertEqual(
                    "Builder's request no longer fit its model's context window; name a model with a larger window",
                    asked["question"],
                )
                self.assertIn(f"The Builder stopped on {SOL}", asked["why"])
                self.assertIn("smaller task", asked["why"])
                advice = quota_route.advice(asked, attempt["attempt_id"])
                self.assertIn("--answer route-terra=MODEL --resolver-token - (reads AUTOCODE_RESOLVER_TOKEN)", advice)
                self.assertIn(
                    "ran out of its model's context window", quota_route.stop_description("PAUSED_CONTEXT_WINDOW")
                )

    def test_a_context_window_stop_is_answered_with_a_model(self):
        with self.assertRaisesRegex(ValueError, "context-window"):
            quota_route.parse_answer(["route-terra=x"], [], {"pause_status": "PAUSED_UNCERTAIN_STAGE"})
        asked, model = quota_route.parse_answer(
            ["route-terra=" + GLM],
            [{"id": "route-terra", "route_role": "terra", "category": quota_route.CATEGORY}],
            {"pause_status": "PAUSED_CONTEXT_WINDOW"},
        )
        self.assertEqual(("route-terra", GLM), (asked["id"], model))


class LaunchTests(unittest.TestCase):
    def test_an_ordinary_kilo_launch_turns_automatic_compaction_off(self):
        config = Path(__file__).resolve().parents[1] / "tools/providers/configs/kilocode.toml"
        provider = KiloProvider(tomllib.loads(config.read_text()), config)
        # The native variant preflight asks the installed Kilo; it is not what this test is about.
        self.enterContext(patch.object(provider, "validate_effort"))
        policy = self.enterContext(
            patch(
                "providers.kilocode.kilocode_policy.restrict",
                side_effect=lambda argv, child, *a, **kw: (argv, child),
            )
        )
        for given, expected in (
            (None, {"auto": False}),
            ({"compaction": {"auto": True, "buffer": 1000}, "plugin": []}, {"auto": False, "buffer": 1000}),
            ({"compaction": "odd"}, {"auto": False}),
        ):
            with self.subTest(given=given):
                env = {"PATH": "/usr/bin", **({"KILO_CONFIG_CONTENT": json.dumps(given)} if given else {})}
                _, child, _ = provider.launch(
                    "terra", Path("/workspace"), Path("/run"), None, GLM, "medium", True, env=env
                )
                self.assertEqual(expected, json.loads(child["KILO_CONFIG_CONTENT"])["compaction"])
        # Through the runner's launch preparation too, and the native-isolation route is untouched here.
        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
            _, child, _, worker = provider_launch.prepare(
                engine="opencode",
                adapter=provider,
                role="terra",
                route_role="terra",
                workspace=Path("/workspace"),
                run_dir=Path("/run"),
                session=None,
                model=GLM,
                effort="medium",
                allow_write=True,
                planning=False,
                report=None,
                schema=None,
                prompt_file=None,
                sandbox="workspace-write",
                transport_args=[],
                chatgpt=False,
                provider=None,
                enforce_tool_boundary=False,
            )
        self.assertEqual({"auto": False}, json.loads(child["KILO_CONFIG_CONTENT"])["compaction"])
        self.assertTrue(worker["provider_session"] is None)
        self.assertEqual(4, policy.call_count)
        self.assertTrue(all(call.kwargs["write_stage"] for call in policy.call_args_list))


class ViewTests(unittest.TestCase):
    def test_each_saved_stage_shows_its_largest_request(self):
        state = {
            "stages": [
                {
                    "stage": "terra",
                    "iteration": 1,
                    "metrics": {"request_context": {"peak_input_tokens": 120_000, "complete": True}},
                },
                {
                    "stage": "sol",
                    "iteration": 1,
                    "metrics": {"request_context": {"peak_input_tokens": None, "complete": False}},
                },
                {"stage": "astra_review", "iteration": 1, "metrics": {}},
                "not a record",
            ]
        }
        view = request_usage.view(state)
        self.assertEqual(
            [
                {"stage": "terra", "iteration": 1, "peak_input_tokens": 120_000, "complete": True},
                {"stage": "sol", "iteration": 1, "peak_input_tokens": None, "complete": False},
            ],
            view["stages"],
        )
        self.assertEqual(120_000, view["peak_input_tokens"])
        self.assertEqual([], request_usage.view({})["stages"])


if __name__ == "__main__":
    unittest.main()

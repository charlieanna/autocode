"""#3888: a stop the model access probe found is shown as the access check, its model and its cause.

A blocking probe refuses the run before the stage it checked launches. The person was told "The
Requirements step stopped: it could not finish." on the blocked path, or the provider's raw message
on the question path. The stop is now said from the probe's saved record (its ``preflight_outcome``
and ``preflight_roles``), never from the provider's words, while a spending limit and the run cost
cap still read as such. No model is called: the probe transport is faked.
"""

import contextlib
import copy
import datetime as dt
import io
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode as runner
import autocode_model_preflight as preflight
import autocode_operational_disposition as disposition
import autocode_process as processes
import autocode_provider_auth as provider_auth
import autocode_provider_env as provider_env
import autocode_run_records as records
import autocode_run_status as run_status
import autocode_run_view as run_view
import autocode_support as support
import autocode_util as util

from . import test_resolver_human as fixture

MODEL = "zai-coding-plan/glm-5.3"
CREDIT_REFUSAL = "Insufficient balance or no resource package. Please recharge"
CREDIT_ERROR = f"Model access preflight failed for {MODEL} (role: requirements): {CREDIT_REFUSAL}"
FUNDS = "credit, quota, usage limit or spending limit used up"
QUOTA_WORDS = "The usage limit has been reached"
QUOTA_ERROR = f"Model access preflight failed for gpt-6-sol (role: tester): {QUOTA_WORDS}"
PROBE_MODEL = "fixture/access-model"
CANARY = "fixture-provider-refusal-canary"
UNSUPPORTED = f"{PROBE_MODEL} is not supported when using Codex with a ChatGPT account"


def probe_record(status=None, *, blocking=True, detail=CREDIT_REFUSAL):
    """The runner-owned access probe of the Requirements model, as autocode_preflight_attempts saves it."""
    return {
        "stage": "model_preflight",
        "role": "model_preflight",
        "runner_owned": True,
        "model": MODEL,
        "iteration": 1,
        "launch_route": {"engine": "opencode", "provider": "opencode", "model": MODEL, "effort": "high"},
        "preflight_roles": ["requirements"],
        "preflight_complete": True,
        "preflight_outcome": {
            "model": MODEL,
            "status": status or "PAUSED_BUDGET",
            "blocking": blocking,
            "detail": detail,
        },
    }


def stopped_state(status="PAUSED_BUDGET", outcome_status=None, *, blocking=True, detail=CREDIT_REFUSAL):
    return {
        "status": status,
        "next_stage": "requirements_gather",
        "settings": {},
        "stages": [probe_record(outcome_status or status, blocking=blocking, detail=detail)],
    }


class BlockedMessageTests(unittest.TestCase):
    def test_ac7_preflight_credit_refusal_names_access_check_model_and_funds(self):
        text = disposition.blocked_message("requirements_gather", "PAUSED_BUDGET", CREDIT_ERROR, stopped_state())
        for words in ("model access check", MODEL, "Requirements", FUNDS):
            self.assertIn(words, text)
        for words in ("could not finish", "Insufficient balance", "Please recharge"):
            self.assertNotIn(words, text)

        # The same message with a non-blocking saved outcome: the cause comes from the record, not the text.
        state = stopped_state("PAUSED_BUDGET", "PAUSED_RATE_LIMIT", blocking=False)
        text = disposition.blocked_message("requirements_gather", "PAUSED_BUDGET", CREDIT_ERROR, state)
        self.assertNotIn("model access check", text)

    def test_probe_stop_names_each_checked_role_and_the_tool_it_ran_on(self):
        state = stopped_state()
        record = state["stages"][0]
        record["preflight_roles"] = ["builder", "tester"]
        record["launch_route"]["provider"] = "kilocode"
        text = disposition.blocked_message("builder", "PAUSED_BUDGET", CREDIT_ERROR, state)
        self.assertIn(f"model access check for {MODEL} on kilocode (roles: builder, tester)", text)
        self.assertIn("before the Builder and Tester steps started", text)

        # A probe saved before it recorded its roles names no role.
        record["preflight_roles"] = []
        text = disposition.blocked_message("builder", "PAUSED_BUDGET", CREDIT_ERROR, state)
        self.assertIn(f"model access check for {MODEL} on kilocode was refused before the next step started", text)

        # A blocking status without a fixed cause still names the check, never the provider's words.
        state = stopped_state("PAUSED_PROVIDER_CAPACITY")
        text = disposition.blocked_message("requirements_gather", "PAUSED_PROVIDER_CAPACITY", CREDIT_ERROR, state)
        self.assertIn("the provider refused access to this model", text)
        self.assertNotIn("Please recharge", text)

        # A run waiting on the probe's question, asked about another status, is not that probe's stop.
        state = stopped_state("WAITING_FOR_USER", "PAUSED_BUDGET")
        text = disposition.blocked_message("requirements_gather", "PAUSED_AUTH", CREDIT_ERROR, state)
        self.assertNotIn("model access check", text)

    def test_ac10_budget_stop_without_probe_reads_as_spending_limit(self):
        state = {
            "status": "PAUSED_BUDGET",
            "next_stage": "review_plan",
            "settings": {},
            "stages": [
                {
                    "stage": "review_plan",
                    "role": "task_planner",
                    "route_role": "plan_reviewer",
                    "iteration": 1,
                    "started_at": "2026-11-20T10:00:00+00:00",
                    "finished_at": "2026-11-20T10:01:00+00:00",
                    "exit_code": 0,
                }
            ],
        }
        text = disposition.blocked_message("review_plan", "PAUSED_BUDGET", "The selected boundary is exhausted", state)
        self.assertTrue(text.startswith("The Plan Reviewer step stopped: "), text)
        self.assertIn(FUNDS, text)
        self.assertNotIn("model access check", text)
        self.assertNotIn("could not finish", text)

    def test_ac25_preflight_auth_stop_keeps_autocode_login_guidance(self):
        rationale = f"Model access preflight failed for {MODEL} (role: requirements): {CANARY}"
        own = provider_auth.AUTH_FAILED + ". " + provider_env.withheld_hint(["FIXTURE_API_KEY"])
        state = stopped_state("PAUSED_AUTH", detail=own)
        text = disposition.blocked_message("requirements_gather", "PAUSED_AUTH", rationale, state)
        for words in ("model access check", MODEL, "login", "HTTP 401", "FIXTURE_API_KEY", "AUTOCODE_PASS_ENV"):
            self.assertIn(words, text)
        self.assertNotIn(CANARY, text)
        self.assertNotIn("could not finish", text)

        # A saved detail AutoCode did not write itself is never copied.
        state = stopped_state("PAUSED_AUTH", detail=CANARY)
        text = disposition.blocked_message("requirements_gather", "PAUSED_AUTH", rationale, state)
        self.assertIn("model access check", text)
        self.assertIn("login", text)
        self.assertNotIn(CANARY, text)
        self.assertNotIn("AUTOCODE_PASS_ENV", text)


class ProbeStopViewTests(unittest.TestCase):
    contract = fixture.ResolverHumanTests.contract

    def setUp(self):
        fixture.ResolverHumanTests.setUp(self)
        self.run = Path(self.state["run_dir"])
        self.run.mkdir(parents=True)

    def save(self, state=None):
        state = self.state if state is None else state
        with contextlib.redirect_stdout(io.StringIO()):
            runner.write_json(self.run / "state.json", state)
        return support.read(self.run / "state.json")

    def view(self, saved):
        return run_view.view(saved, run_dir=self.run, workspace=Path(saved["workspace"]))

    def quota_probe_state(self):
        # The Tester's account hit its usage limit and the access probe caught it before the Tester launched.
        self.state.update(
            status="PAUSED_BUDGET",
            next_stage="tester",
            pending_questions=[],
            settings={
                "engine": "codex",
                "roles": {
                    "builder": {"model": "gpt-5.6-terra"},
                    "tester": {"model": "gpt-6-sol"},
                    "completion": {"model": "gpt-6-sol"},
                },
            },
        )
        events = self.run / "model-preflight" / "probe" / "events.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text(json.dumps({"type": "error", "error": {"message": QUOTA_WORDS}}) + "\n")
        self.state["stages"].append(
            {
                "stage": "model_preflight",
                "role": "model_preflight",
                "runner_owned": True,
                "model": "gpt-6-sol",
                "iteration": 1,
                "events": str(events),
                "output": str(events.parent / "report.json"),
                "launch_route": {"engine": "codex", "model": "gpt-6-sol"},
                "preflight_roles": ["tester"],
                "preflight_outcome": {"model": "gpt-6-sol", "status": "PAUSED_BUDGET", "blocking": True},
            }
        )
        error = support.Paused("PAUSED_BUDGET", QUOTA_ERROR)
        self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(runner, self.state, self.run, error))
        saved = self.save()
        return saved, self.view(saved)

    def test_ac8_preflight_credit_refusal_status_view_keeps_stop_route_and_receipts(self):
        self.state.update(status="PAUSED_BUDGET", next_stage="requirements_gather", pending_questions=[])
        self.state["stages"].append(probe_record())
        settings = copy.deepcopy(self.state["settings"])
        before = copy.deepcopy(self.state["stages"])
        error = support.Paused("PAUSED_BUDGET", CREDIT_ERROR)
        self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(runner, self.state, self.run, error))
        saved = self.save()
        shown = self.view(saved)
        activity = [json.loads(line) for line in (self.run / "activity.jsonl").read_text().splitlines() if line.strip()]
        [paused] = [
            entry for entry in activity if entry.get("event") == "transition" and entry.get("status") == "PAUSED_BUDGET"
        ]
        for text in (shown["stop_reason"], paused["stop_reason"]):
            for words in ("model access check", MODEL, FUNDS):
                self.assertIn(words, text)
            self.assertNotIn("could not finish", text)
        whole = json.dumps(shown)
        self.assertIn("recoveries", shown)
        self.assertIn("explanation", shown)
        self.assertIn("needs", shown)
        self.assertNotIn("Please recharge", whole)
        self.assertNotIn("Insufficient balance", whole)
        self.assertEqual("PAUSED_BUDGET", saved["status"])
        self.assertEqual(settings, saved["settings"], "the selected route is kept")
        receipts = saved["resolver"]["operational_receipts"]
        self.assertTrue(receipts)
        self.assertEqual(before, saved["stages"][: len(before)])
        added = saved["stages"][len(before) :]
        self.assertEqual(1, len(added), added)
        self.assertEqual("resolver", added[0]["stage"])
        self.assertTrue(added[0]["runner_owned"])
        self.assertIn(added[0]["receipt"]["id"], receipts)
        self.assertNotIn("command", added[0], "no model launch was recorded")

    def test_ac11_probe_quota_question_stop_reason_omits_provider_text(self):
        _saved, shown = self.quota_probe_state()
        for words in ("model access check", "gpt-6-sol", FUNDS):
            self.assertIn(words, shown["stop_reason"])
        self.assertIn("recoveries", shown)
        self.assertIn("explanation", shown)
        self.assertNotIn(QUOTA_WORDS, json.dumps(shown))
        self.assertEqual(["route-tester"], [question["id"] for question in shown["needs"]["questions"]])

    def test_ac26_probe_quota_recovery_explanation_names_access_check(self):
        _saved, shown = self.quota_probe_state()
        what = shown["recovery"]["what_happened"]
        for words in ("model access check", "gpt-6-sol", FUNDS):
            self.assertIn(words, what)
        self.assertNotIn("configured usage limit", what)
        self.assertNotIn(QUOTA_WORDS, what)

    def test_ac13_run_cost_cap_stop_still_names_the_cost_limit(self):
        self.state.update(
            status="PAUSED_COST_LIMIT",
            next_stage="review_plan",
            pending_questions=[],
            settings={"limits": {"max_cost_usd": 5}, "budget_origins": {"max_cost_usd": "user_explicit"}},
        )
        self.state["stages"].append(probe_record())
        error = support.Paused("PAUSED_COST_LIMIT", "The run reached its cost limit")
        self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(runner, self.state, self.run, error))
        stop_reason = self.view(self.save())["stop_reason"]
        self.assertIn("cost limit you set", stop_reason)
        self.assertNotIn("model access check", stop_reason)


def refusal_events(status_code, message, *, code=None):
    data = {"statusCode": status_code, "message": message, "responseBody": message}
    if code:
        data.update(
            isRetryable=False,
            responseBody=json.dumps({"error": {"type": "invalid_request_error", "code": code, "message": message}}),
        )
    return [{"type": "error", "sessionID": "ses_fixture", "error": {"name": "APIError", "data": data}}]


class ProbeStopPauseTests(unittest.TestCase):
    contract = fixture.ResolverHumanTests.contract

    def setUp(self):
        fixture.ResolverHumanTests.setUp(self)
        self.run = Path(self.state["run_dir"])
        self.run.mkdir(parents=True)
        self.seconds = 0.0
        self.epoch = dt.datetime(2026, 10, 15, tzinfo=dt.UTC)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch.object(
                preflight,
                "time",
                SimpleNamespace(monotonic=lambda: self.seconds, gmtime=time.gmtime, strftime=time.strftime),
            )
        )
        stack.enter_context(patch.object(processes, "live_processes", return_value=[]))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def ownership(self, directory):
        metadata = {
            "schema": 1,
            "nonce": "a" * 32,
            "receipt": str(directory / "supervision.json"),
            "owner": {"pid": 101, "birth_identity": 1},
            "keeper": {"pid": 102, "birth_identity": 2},
            "provider": {"pid": 103, "birth_identity": 3},
        }
        util.atomic_json(
            Path(metadata["receipt"]),
            {
                **metadata,
                "phase": "stopped",
                "cause": "owner_lost",
                "cleanup_error": None,
                "observed_at": (self.epoch + dt.timedelta(seconds=self.seconds + 2)).isoformat(),
                "processes": [metadata["provider"]],
            },
        )
        return metadata

    def paused(self, rows):
        """Probe the Tester's route with the transport faked to answer ``rows``, then pause as run setup does."""
        adapter = SimpleNamespace(NAME="opencode", _config={"auth": {}}, check_subscription_routes=lambda *a: None)
        identity = {"engine": "opencode", "identity_version": 1, "config_sha256": "a" * 64}
        self.state.update(
            next_stage="draft_plan",
            settings={
                "engine": "opencode",
                "roles": {"tester": {"model": PROBE_MODEL, "reasoning_effort": "high"}},
                "effort_origins": {"tester": "user_explicit"},
                "transport_identities": {"opencode": identity},
            },
        )

        def answered(**kwargs):
            directory = kwargs["directory"]
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            kwargs["checkpoint"](self.ownership(directory))
            self.seconds += 2
            return {"exit_code": 1, "timed_out": False, "metrics": support.event_metrics(directory / "events.jsonl")}

        def checkpoint():
            util.atomic_json(self.run / "state.json", self.state)

        probe_runner = SimpleNamespace(opencode=adapter, account_stage=records.account_stage)
        with patch.object(preflight.transport, "execute", side_effect=answered):
            with self.assertRaises(support.Paused) as caught:
                preflight.check(probe_runner, self.state, self.run, self.root, checkpoint=checkpoint)
        error = caught.exception
        # The run's pause handler (autocode_run_setup): save the stop, then offer it to the operational hold.
        run_status.pause(self.state, error.status, str(error))
        self.assertFalse(runner.resolver_runtime.record_operational_exhaustion(runner, self.state, self.run, error))
        with contextlib.redirect_stdout(io.StringIO()):
            runner.write_json(self.run / "state.json", self.state)
        saved = support.read(self.run / "state.json")
        return error.status, run_view.view(saved, run_dir=self.run, workspace=Path(saved["workspace"]))

    def test_ac9_preflight_auth_and_billing_route_stops_name_access_check(self):
        for case, rows, status, words in (
            ("auth", refusal_events(401, CANARY), "PAUSED_AUTH", "login"),
            (
                "billing route",
                refusal_events(400, UNSUPPORTED, code="model_not_found"),
                "PAUSED_BILLING_ROUTE",
                "billing route",
            ),
        ):
            with self.subTest(case=case):
                self.setUp()
                paused, shown = self.paused(rows)
                self.assertEqual(status, paused)
                stop_reason = shown["stop_reason"]
                for expected in ("model access check", PROBE_MODEL, "Tester", words):
                    self.assertIn(expected, stop_reason)
                for unexpected in ("could not finish", CANARY, "not supported when using Codex", "Probe evidence"):
                    self.assertNotIn(unexpected, stop_reason)

        for status, words in (("PAUSED_AUTH", "login"), ("PAUSED_BILLING_ROUTE", "billing route")):
            with self.subTest(formatter=status):
                text = disposition.blocked_message("requirements_gather", status, CREDIT_ERROR, stopped_state(status))
                for expected in ("model access check", MODEL, words):
                    self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()

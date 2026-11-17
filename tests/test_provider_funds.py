"""A provider's "the account has nothing left to pay with" is a quota stop, whoever says it and however.

The cases are the providers' own error bodies as their APIs document or returned them, wrapped the way
the tools report a failed request (OpenCode and Kilo's ``APIError`` event, a Codex ``turn.failed``, a
plain ``Error:`` line). Before, a list of phrases recognized two of them, and Anthropic's wording on
2026-11-16 stopped six runs as PAUSED_PROVIDER_UNCERTAIN, which asks a person to reconcile the attempt.
"""

import json
import tempfile
import unittest
from pathlib import Path

import autocode_provider_funds as funds
import autocode_support as support


def api_error(message, status, body=None, retryable=False):
    data = {"message": message, "statusCode": status, "isRetryable": retryable}
    if body is not None:
        data["responseBody"] = json.dumps(body)
    return {"type": "error", "sessionID": "ses_fixture", "error": {"name": "APIError", "data": data}}


ANTHROPIC = (
    "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or "
    "purchase credits."
)
SPENT = {
    "anthropic, HTTP 400 as Kilo relayed it": api_error(
        ANTHROPIC, 400, {"type": "error", "error": {"type": "invalid_request_error", "message": ANTHROPIC}}
    ),
    "anthropic billing_error": api_error(
        "Your API usage is blocked.", 403, {"type": "error", "error": {"type": "billing_error"}}
    ),
    "openrouter": api_error("Insufficient credits. Add more using https://openrouter.ai/settings/credits", 402),
    "openrouter, a request too large for what is left": api_error(
        "This request requires more credits, or fewer max_tokens. You requested up to 32000 tokens, but can only "
        "afford 1810.",
        402,
    ),
    "deepseek": api_error("Insufficient Balance", 402),
    "xai": api_error(
        "Your team has either used all available credits or reached its monthly spending limit. To continue "
        "making API requests, please purchase more credits or raise your spending limit.",
        429,
        retryable=True,
    ),
    "z.ai, code 1113": api_error("Insufficient balance or no resource package. Please recharge.", 429),
    "moonshot": api_error("Your account is suspended due to insufficient balance, please recharge", 429),
    "openai insufficient_quota": api_error(
        "You exceeded your current quota, please check your plan and billing details.",
        429,
        {"error": {"type": "insufficient_quota", "code": "insufficient_quota"}},
    ),
    "openai billing hard limit": api_error("Billing hard limit has been reached", 400),
    "kilo gateway": api_error(
        "Add credits to continue, or switch to a free model", 402, {"error_type": "usage_limit_exceeded"}
    ),
    "a 402 that says nothing else": api_error("Payment Required", 402),
    "a 402 with no words at all": api_error("", 402),
    "an error code alone": {"type": "turn.failed", "error": {"code": "insufficient_credit", "message": "Refused"}},
    "z.ai plan used up, sent as a 429": api_error(
        "Weekly/Monthly Limit Exhausted. Your limit will reset at 2026-10-09 10:51:41", 429, retryable=True
    ),
    "a used-up plan that names only its reset": api_error(
        "Your limit will reset at 2026-10-09 10:51:41", 429, retryable=True
    ),
    "codex subscription": {"type": "error", "error": {"message": "subscription usage limit reached"}},
    "google, out of prepaid credit": api_error("Your prepayment credits are depleted.", 429),
}
# The look-alikes: each mentions money, a limit or 402, and none says the account is spent.
OTHER = {
    "a rate limit": (api_error("Rate limit exceeded, retry in 20s", 429, retryable=True), "PAUSED_RATE_LIMIT"),
    "a rate limit whose advice is to pay": (
        api_error("Rate limit reached for requests. Add a payment method to increase your rate limit.", 429),
        "PAUSED_RATE_LIMIT",
    ),
    "a timeout": (api_error("The request timed out", 504, retryable=True), "PAUSED_PROVIDER_UNCERTAIN"),
    "an overloaded provider": (api_error("Overloaded", 529, retryable=True), "PAUSED_PROVIDER_UNCERTAIN"),
    "a bad request": (
        api_error("messages.0.content: text content blocks must be non-empty", 400),
        "PAUSED_PROVIDER_UNCERTAIN",
    ),
    "402 as a count, not a status": (
        api_error("Stream ended after 402 events without a final message", 500),
        "PAUSED_PROVIDER_UNCERTAIN",
    ),
    "a rejected key": (api_error("Invalid API key", 401), "PAUSED_AUTH"),
}


class FailureStatusTests(unittest.TestCase):
    def status(self, *lines):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            path.write_text("".join(line + "\n" for line in lines))
            return support.failure_status(path)

    def test_every_provider_s_spent_account_is_a_quota_stop(self):
        for name, row in SPENT.items():
            with self.subTest(name):
                self.assertEqual("PAUSED_BUDGET", self.status(json.dumps(row)))

    def test_the_plain_error_line_a_tool_ends_with_is_read_too(self):
        # Kilo printed the same refusal as a coloured stderr line before its event row.
        self.assertEqual("PAUSED_BUDGET", self.status(json.dumps({"type": "step_start"}), "Error: " + ANTHROPIC))
        self.assertEqual("PAUSED_BUDGET", self.status("\x1b[91m\x1b[1mError: \x1b[0mInsufficient Balance"))

    def test_look_alikes_keep_their_own_stop(self):
        for name, (row, expected) in OTHER.items():
            with self.subTest(name):
                self.assertEqual(expected, self.status(json.dumps(row)))

    def test_a_failed_turn_s_accounting_is_never_its_error(self):
        row = api_error("The request timed out", 504)
        row.update(usage={"input_tokens": 402, "status": 402}, cost_usd=4.02)
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", self.status(json.dumps(row)))


class ExhaustedTests(unittest.TestCase):
    def test_payment_required_is_read_from_the_status_wherever_the_tool_nests_it(self):
        for row in (
            {"error": {"data": {"statusCode": 402}}},
            {"error": {"status_code": "402"}},
            {"error": {"response": {"status": 402}}},
            [{"type": "error", "error": {"http_status": 402}}],
        ):
            with self.subTest(row):
                self.assertTrue(funds.payment_required(row))
        for row in ({"error": {"data": {"statusCode": 429}}}, {"count": 402}, {"status": True}, {"status": "4020"}):
            with self.subTest(row):
                self.assertFalse(funds.payment_required(row))

    def test_what_is_paid_with_must_be_in_the_sentence_that_says_it_is_spent(self):
        self.assertTrue(funds.exhausted([], "Your balance is too low"))
        self.assertTrue(funds.exhausted([], "Not enough funds for this request"))
        self.assertFalse(funds.exhausted([], "The model reached its output limit. See your balance in settings."))
        self.assertFalse(funds.exhausted([], "Request failed. Credits: 12.50 remaining after this call"))
        self.assertFalse(funds.exhausted([], None))


if __name__ == "__main__":
    unittest.main()

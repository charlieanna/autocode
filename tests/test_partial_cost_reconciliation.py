"""A saved cost state prices exactly the attempt it is bound to (#2592, partial-cost reconciliation).

The public stage ledger prices a stage from ``metrics.provider_cost_usd`` or an estimate of
``metrics.provider_tokens``; an interrupted stage carries neither, so both interrupted Gateway
attempts of the DTI-12398 campaign stayed unpriced while the CLI's own saved cost state recorded
their spend. The fixtures are the issue's small saved cost states in the product's saved shape
(``native-acceptance-test-cost-states.json`` on the campaign host, read as "Claude cost-state
through GoCode, not invoice-exact"):

- the first attempt's cost state total is $2.5760102, above its $2.5397672 completed-message
  estimate (correcting the stopped total from about $54.07 to $54.11);
- the second attempt's cost state agrees with its $5.2353066 completed-message estimate, the
  delta against the first attempt's total on the session's cumulative cost state.

All amounts stay provider/CLI observations. A cost record grants no success or test credit, and it
replaces an estimate when it arrives, never adding a native supplement after a stage is priced.
Unknown and incomplete accounting stay unknown; no native request count is invented.
"""

import math
import unittest

import autocode_event_metrics
import autocode_usage

SESSION = "11111111-2222-4333-8444-555555555555"
OTHER_SESSION = "99999999-8888-7777-6666-555555555555"

# DTI-12398 campaign observations (gateway-native-timeout-usage-reconciliation.json and
# gateway-native-second-failure-usage.json); CLI cost states, not invoice verification.
FIRST_TOTAL = 2.5760102
MESSAGE_ESTIMATE = 2.5397672
SECOND_DELTA = 5.2353066
# The cost state is cumulative per session, so the second attempt's record chains from the
# first: its agreed per-attempt amount ($5.2353066) is the delta against the earlier total.
CHAIN_TOTAL = FIRST_TOTAL + SECOND_DELTA

TOKENS = {
    "input_tokens": 48,
    "cached_input_tokens": 2453426,
    "cache_write_tokens": 148830,
    "output_tokens": 65237,
    "reasoning_output_tokens": 0,
    "fresh_input_tokens": 48,
    "visible_output_tokens": 65237,
}


def request(part="req-1", session=SESSION, tokens=None, cost=None):
    tokens = dict(TOKENS if tokens is None else tokens)
    return {"identity": [session, part], "tokens": tokens, "reported_cost_usd": cost}


def receipts(*requests, complete=True):
    return {"accounting": {"requests": list(requests), "complete": complete, "issues": []}}


def stage(name="write_acceptance_tests", metrics=None, **fields):
    return {
        "stage": name,
        "role": "Tester",
        "command": ["autocode", "--model", "claude-opus-5-5"],
        "attempt_id": fields.pop("attempt_id", "003/write-acceptance-tests"),
        "metrics": metrics or {},
        **fields,
    }


def state(*stages):
    return {"stages": list(stages)}


def no_estimate(model, tokens, finished_at):
    return None


def message_estimate(model, tokens, finished_at):
    return MESSAGE_ESTIMATE


class CostStatePricingTests(unittest.TestCase):
    def test_the_cost_state_prices_the_interrupted_attempt_no_public_usage_could_price(self):
        metrics = {
            "provider_tokens": dict.fromkeys(autocode_usage.TOKEN_KEYS),
            "provider_cost_usd": None,
            "request_context": receipts(request()),
            "cost_state": {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL, "updated_at": 2.0},
        }
        row = autocode_usage.stage_rows(state(stage(metrics=metrics, interrupted=True)), message_estimate)[0]
        self.assertAlmostEqual(FIRST_TOTAL, row["cost_usd"])
        self.assertEqual("reported", row["basis"])
        self.assertEqual("interrupted", row["process_outcome"])

    def test_a_record_for_another_session_never_prices_the_attempt(self):
        metrics = {
            "provider_tokens": dict.fromkeys(autocode_usage.TOKEN_KEYS),
            "cost_state": {"session_id": OTHER_SESSION, "total_cost_usd": FIRST_TOTAL},
            "request_context": receipts(request()),
        }
        rows = autocode_usage.stage_rows(state(stage(metrics=metrics, interrupted=True)), message_estimate)
        self.assertAlmostEqual(MESSAGE_ESTIMATE, rows[0]["cost_usd"])
        self.assertEqual("estimated", rows[0]["basis"])  # the receipts' own usage, not the foreign record

        unbound = {
            "provider_tokens": dict.fromkeys(autocode_usage.TOKEN_KEYS),
            "cost_state": {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL},
        }
        rows = autocode_usage.stage_rows(state(stage(metrics=unbound, interrupted=True)), no_estimate)
        self.assertEqual((None, "unknown", "usage_missing"), (rows[0]["cost_usd"], rows[0]["basis"], rows[0]["unpriced_reason"]))

    def test_an_authoritative_amount_replaces_the_completed_message_estimate(self):
        metrics = {
            "provider_tokens": dict.fromkeys(autocode_usage.TOKEN_KEYS),
            "cost_state": {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL},
            "request_context": receipts(request()),
        }
        row = autocode_usage.stage_rows(state(stage(metrics=metrics, interrupted=True)), message_estimate)[0]
        self.assertAlmostEqual(FIRST_TOTAL, row["cost_usd"])  # replaces, never MESSAGE_ESTIMATE + FIRST_TOTAL

    def test_no_native_supplement_is_added_after_the_public_stage_is_priced(self):
        metrics = {
            "provider_tokens": dict.fromkeys(autocode_usage.TOKEN_KEYS),
            "provider_cost_usd": 1.0,
            "cost_state": {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL},
            "request_context": receipts(request()),
        }
        row = autocode_usage.stage_rows(state(stage(metrics=metrics)), message_estimate)[0]
        self.assertEqual((1.0, "reported"), (row["cost_usd"], row["basis"]))

    def test_the_session_chain_deltas_bind_each_attempt_to_its_own_spend(self):
        first = stage(
            "investigate_bug",
            metrics={
                "provider_cost_usd": None,
                "cost_state": {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL},
                "request_context": receipts(request("a")),
            },
            attempt_id="001/investigate-bug",
            interrupted=True,
        )
        second = stage(
            metrics={
                "provider_cost_usd": None,
                "cost_state": {
                    "session_id": SESSION,
                    "total_cost_usd": CHAIN_TOTAL,
                    "previous_total_usd": FIRST_TOTAL,
                },
                "provider_sessions": [SESSION],
            },
            exit_code=1,
            attempt_id="003/write-acceptance-tests",
        )
        rows = autocode_usage.stage_rows(state(first, second), no_estimate)
        self.assertAlmostEqual(FIRST_TOTAL, rows[0]["cost_usd"])
        self.assertAlmostEqual(SECOND_DELTA, rows[1]["cost_usd"])
        combined = autocode_usage.summary(state(first, second))
        self.assertAlmostEqual(round(FIRST_TOTAL + SECOND_DELTA, 6), combined["cost_usd"]["reported"])


class DeduplicationTests(unittest.TestCase):
    def metrics(self, *records, sessions=None):
        metrics = {
            "provider_tokens": dict.fromkeys(autocode_usage.TOKEN_KEYS),
            "cost_state": list(records),
            "request_context": receipts(request()),
        }
        if sessions is not None:
            metrics["provider_sessions"] = list(sessions)
        return metrics

    def test_repeated_reports_of_the_same_record_count_once(self):
        record = {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL}
        stamped = {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL, "updated_at": 1.0}
        rows = autocode_usage.stage_rows(state(stage(metrics=self.metrics(record, record, stamped))), no_estimate)
        self.assertAlmostEqual(FIRST_TOTAL, rows[0]["cost_usd"])

    def test_conflicting_distinct_totals_are_unknown_unless_a_timestamp_orders_them(self):
        low = {"session_id": SESSION, "total_cost_usd": MESSAGE_ESTIMATE}
        high = {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL}
        rows = autocode_usage.stage_rows(state(stage(metrics=self.metrics(low, high))), no_estimate)
        self.assertEqual((None, "unknown"), (rows[0]["cost_usd"], rows[0]["basis"]))

        ordered = [
            {"session_id": SESSION, "total_cost_usd": MESSAGE_ESTIMATE, "updated_at": 1.0},
            {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL, "updated_at": 2.0},
        ]
        rows = autocode_usage.stage_rows(state(stage(metrics=self.metrics(*ordered))), no_estimate)
        self.assertAlmostEqual(FIRST_TOTAL, rows[0]["cost_usd"])

        tied = [
            {"session_id": SESSION, "total_cost_usd": MESSAGE_ESTIMATE, "updated_at": 2.0},
            {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL, "updated_at": 2.0},
        ]
        rows = autocode_usage.stage_rows(state(stage(metrics=self.metrics(*tied))), no_estimate)
        self.assertEqual((None, "unknown"), (rows[0]["cost_usd"], rows[0]["basis"]))


class HonestUnknownTests(unittest.TestCase):
    def test_a_stage_without_a_cost_state_or_usage_stays_unknown(self):
        rows = autocode_usage.stage_rows(state(stage(metrics={"provider_cost_usd": None})), no_estimate)
        self.assertEqual((None, "unknown", "usage_missing"), (rows[0]["cost_usd"], rows[0]["basis"], rows[0]["unpriced_reason"]))

    def test_a_record_that_cannot_bind_or_arithmetize_is_never_a_price(self):
        broken = [
            {"session_id": SESSION, "total_cost_usd": True},
            {"session_id": SESSION, "total_cost_usd": float("nan")},
            {"session_id": SESSION, "total_cost_usd": -1.0},
            {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL, "previous_total_usd": FIRST_TOTAL},
            {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL, "previous_total_usd": FIRST_TOTAL + 1},
            {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL, "previous_total_usd": True},
            "not-a-record",
        ]
        for record in broken:
            with self.subTest(record=record):
                metrics = {
                    "provider_tokens": dict.fromkeys(autocode_usage.TOKEN_KEYS),
                    "cost_state": [record],
                    "provider_sessions": [SESSION],
                }
                rows = autocode_usage.stage_rows(state(stage(metrics=metrics, interrupted=True)), no_estimate)
                self.assertEqual((None, "unknown"), (rows[0]["cost_usd"], rows[0]["basis"]))

    def test_receipt_sums_fill_missing_usage_for_estimation_without_inventing_any(self):
        seen = []

        def estimator(model, tokens, finished_at):
            seen.append(tokens)
            return 0.5

        metrics = {
            "provider_tokens": dict.fromkeys(autocode_usage.TOKEN_KEYS),
            "request_context": receipts(request(), complete=False),
        }
        rows = autocode_usage.stage_rows(state(stage(metrics=metrics, interrupted=True)), estimator)
        self.assertEqual(65237, rows[0]["tokens"]["output_tokens"])
        self.assertEqual((0.5, "estimated"), (rows[0]["cost_usd"], rows[0]["basis"]))
        self.assertTrue(rows[0]["partial"])  # a cut stream's sums are a lower bound

        sparse = request("req-2")
        sparse["tokens"].pop("output_tokens")
        metrics = {
            "provider_tokens": dict.fromkeys(autocode_usage.TOKEN_KEYS),
            "request_context": receipts(request(), sparse),
        }
        rows = autocode_usage.stage_rows(state(stage(metrics=metrics, interrupted=True)), no_estimate)
        self.assertIsNone(rows[0]["tokens"]["output_tokens"])  # never invented from the other request
        self.assertEqual((None, "usage_missing"), (rows[0]["cost_usd"], rows[0]["unpriced_reason"]))


class AttributionTests(unittest.TestCase):
    def test_a_cost_record_grants_no_success_or_request_credit(self):
        metrics = {
            "provider_tokens": {key: 1 for key in autocode_usage.TOKEN_KEYS},
            "cost_state": {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL},
            "request_context": receipts(request()),
        }
        saved = state(stage(metrics=metrics, interrupted=True))
        row = autocode_usage.stage_rows(saved, no_estimate)[0]
        self.assertEqual("interrupted", row["process_outcome"])
        combined = autocode_usage.accounting(saved)
        self.assertEqual(1, combined["provider_requests"]["observed"])
        self.assertEqual(1, combined["attempts"][0]["attributed_requests"])  # the receipts' count, no more

    def test_failed_call_spend_is_retained_in_the_totals(self):
        metrics = {
            "provider_tokens": dict.fromkeys(autocode_usage.TOKEN_KEYS),
            "cost_state": {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL},
            "request_context": receipts(request()),  # the requests never reported a cost of their own
        }
        saved = state(stage(metrics=metrics, exit_code=1))
        row = autocode_usage.stage_rows(saved, no_estimate)[0]
        self.assertEqual(("failed", "reported"), (row["process_outcome"], row["basis"]))
        # The legacy known subtotal is rounded to six places; the accounting below is exact.
        self.assertEqual(round(FIRST_TOTAL, 6), autocode_usage.summary(saved)["cost_usd"]["reported"])
        combined = autocode_usage.accounting(saved)
        self.assertAlmostEqual(FIRST_TOTAL, combined["cost"]["reported_usd"]["value"])


class ProviderTranslationTests(unittest.TestCase):
    def test_the_translation_carries_the_cost_state_and_its_session(self):
        record = {"session_id": SESSION, "total_cost_usd": FIRST_TOTAL}
        rows = [
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1, "reasoning_output_tokens": 0},
                "cost_state": record,
                "claude": {"session_id": SESSION},
            }
        ]
        metrics = autocode_event_metrics.summarize(rows, {"accounting": {"requests": []}}, None)
        self.assertEqual([record], metrics["cost_state"])
        self.assertEqual([SESSION], metrics["provider_sessions"])
        self.assertAlmostEqual(FIRST_TOTAL, autocode_usage.cost_state_cost(metrics))


if __name__ == "__main__":
    unittest.main()

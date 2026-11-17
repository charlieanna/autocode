"""Deterministic model-unavailable / account-unsupported probe classification (#27)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import autocode_model_unavailable as model_unavailable
import autocode_support as support

BORG_SPARK_ERROR = {
    "type": "error",
    "timestamp": 1794836873220,
    "sessionID": "ses_e1b634319ffeRdTmFulSSbNj6A",
    "error": {
        "name": "APIError",
        "data": {
            "message": "The 'gpt-5.3-codex-spark' model is not supported when using Codex with a ChatGPT account.",
            "statusCode": 400,
            "isRetryable": False,
            "responseBody": json.dumps(
                {
                    "detail": (
                        "The 'gpt-5.3-codex-spark' model is not supported when using Codex with a ChatGPT account."
                    ),
                    "error": {
                        "type": "invalid_request_error",
                        "code": "model_not_found",
                        "message": (
                            "The 'gpt-5.3-codex-spark' model is not supported when using Codex with a ChatGPT account."
                        ),
                    },
                }
            ),
        },
    },
}


class ModelUnavailableTests(unittest.TestCase):
    def test_deterministic_model_and_account_rejections_classify_cleanly(self):
        cases = (
            (
                "chatgpt_oauth_spark",
                [BORG_SPARK_ERROR],
                "model_not_found",
                "not supported when using Codex with a ChatGPT account",
            ),
            (
                "openai_404_model_not_found",
                [
                    {
                        "type": "error",
                        "error": {
                            "code": "model_not_found",
                            "statusCode": 404,
                            "message": "The model 'gpt-99' does not exist or you do not have access to it.",
                        },
                    }
                ],
                "model_not_found",
                "does not exist or you do not have access",
            ),
            (
                "anthropic_404_not_found_error",
                [
                    {
                        "type": "error",
                        "error": {
                            "data": {
                                "statusCode": 404,
                                "isRetryable": False,
                                "responseBody": json.dumps(
                                    {
                                        "type": "error",
                                        "error": {"type": "not_found_error", "message": "model: claude-retired"},
                                    }
                                ),
                            }
                        },
                    }
                ],
                "not_found_error",
                "model: claude-retired",
            ),
            (
                "gateway_404_no_endpoints",
                [
                    {
                        "type": "error",
                        "error": {
                            "data": {
                                "statusCode": 404,
                                "isRetryable": False,
                                "message": "No endpoints found for deepseek/deepseek-retired.",
                            }
                        },
                    }
                ],
                "model_unavailable",
                "No endpoints found for deepseek/deepseek-retired.",
            ),
            (
                "plan_403_model_not_available",
                [
                    {
                        "type": "turn.failed",
                        "error": {
                            "statusCode": 403,
                            "isRetryable": False,
                            "message": "The model 'glm-pro-x' is not available on your subscription plan.",
                        },
                    }
                ],
                "model_unavailable",
                "not available on your subscription plan",
            ),
        )
        for label, rows, expected_code, snippet in cases:
            with self.subTest(case=label):
                found = model_unavailable.unavailable(rows)
                self.assertIsNotNone(found)
                self.assertEqual(expected_code, found["code"])
                self.assertIn(snippet, found["message"])

    def test_transient_auth_paywall_and_parameter_errors_never_classify_as_model_unavailable(self):
        negatives = (
            [{"type": "error", "error": {"data": {"statusCode": 401, "message": "Model access denied"}}}],
            [{"type": "error", "error": {"data": {"statusCode": 402, "message": "Add credits for this model"}}}],
            [{"type": "error", "error": {"data": {"statusCode": 429, "message": "Model rate limit exceeded"}}}],
            [{"type": "error", "error": {"data": {"statusCode": 503, "message": "The model is not available now"}}}],
            [
                {
                    "type": "error",
                    "error": {
                        "data": {
                            "statusCode": 400,
                            "isRetryable": True,
                            "code": "model_not_found",
                            "message": "Transient router reload",
                        }
                    },
                }
            ],
            [{"type": "turn.failed", "error": {"message": "connection reset by peer"}}],
            [
                {
                    "type": "error",
                    "error": {
                        "data": {
                            "statusCode": 400,
                            "isRetryable": False,
                            "message": "The model does not support parameter 'reasoning_effort'.",
                        }
                    },
                }
            ],
            [
                {
                    "type": "error",
                    "error": {
                        "data": {
                            "statusCode": 400,
                            "isRetryable": False,
                            "message": "Invalid schema for response_format.",
                        }
                    },
                }
            ],
        )
        for index, rows in enumerate(negatives):
            with self.subTest(index=index):
                self.assertIsNone(model_unavailable.unavailable(rows))

    def test_failure_status_stops_standalone_probe_without_altering_stage_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            events_path.write_text(json.dumps(BORG_SPARK_ERROR) + "\n")
            self.assertEqual("PAUSED_BILLING_ROUTE", support.failure_status(events_path, record=None))
            self.assertEqual(
                "PAUSED_PROVIDER_UNCERTAIN",
                support.failure_status(events_path, record={"stage": "builder"}),
            )


if __name__ == "__main__":
    unittest.main()

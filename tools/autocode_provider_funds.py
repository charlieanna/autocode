"""Whether a provider's failure says the account has nothing left to pay with.

A stage that fails because the account's credit, balance or quota is used up is a quota stop
(``PAUSED_BUDGET``): the run moves the stage to another qualified model or asks the person which
model continues it (autocode_quota_route). Every other unrecognized failure is
``PAUSED_PROVIDER_UNCERTAIN``, which asks a person to inspect the attempt and set it aside.

The quota stop used to be recognized by a list of phrases, one per incident: "quota", "usage limit",
then "add credits" for Kilo's gateway. On 2026-11-16 Anthropic's own wording ("Your credit balance is
too low to access the Anthropic API ... purchase credits", HTTP 400) matched none of them, and six
runs stopped as uncertain. OpenRouter's "Insufficient credits", DeepSeek's "Insufficient Balance",
xAI's "used all available credits or reached its monthly spending limit", Z.AI's "Insufficient
balance or no resource package. Please recharge" and a bare HTTP 402 failed the same way.

``exhausted`` reads what a provider can be observed to say, in this order:

- **HTTP 402 Payment Required** on a failed request, whatever its words;
- an error type or code that names billing (``billing_error``, ``insufficient_quota``, ...), which
  the words below also catch once separators are read as spaces;
- **what is paid with, next to its being used up**: credit, balance, funds or a spending or billing
  limit, within one sentence of insufficient, too low, used up, exceeded, reached, or of the remedy
  the provider names (purchase, add, top up, recharge);
- the phrases the earlier list held (quota, budget, usage limit, a daily/weekly/monthly limit).

A rate limit is not a spent account: "rate limit", alone or asking for a payment method to raise
it, matches nothing here, and the caller classifies it after this.

Pure: text and event rows in, a bool out.
"""

from __future__ import annotations

import re

PAYMENT_REQUIRED = 402
_STATUS_KEYS = frozenset({"statuscode", "status_code", "status", "http_status", "httpstatus"})
# What an account pays with. "billing" alone is not one: a rate limit's advice links to a billing page.
_FUNDS = r"(?:credits?|balance|funds|spending limit|billing (?:hard )?limit|resource package)"
# Used up, or the remedy a provider names for it.
_SPENT = (
    r"(?:insufficient|not enough|too low|r[au]n out|out of|used (?:up|all)|exhausted|depleted|exceeded|"
    r"reached|negative|no (?:remaining|available|more)|requires? more|purchase|buy|add|top ?up|recharge)"
)
_NEAR = r"[^.!?\n]{0,60}"
_FUNDS_SPENT = re.compile(rf"\b{_SPENT}\b{_NEAR}\b{_FUNDS}\b|\b{_FUNDS}\b{_NEAR}\b{_SPENT}\b")
# The earlier list (autocode_support.failure_status), kept whole: a used-up plan can be a 429 that never
# says quota (Z.AI: "Weekly/Monthly Limit Exhausted"); a rate limit is not.
_PHRASES = ("quota", "budget", "usage limit", "billing error", "payment required")
_PLAN_LIMIT = re.compile(r"(?<!rate )limit (exhausted|will reset at \d{4}-)|\b(daily|weekly|monthly) limit\b")
_SEPARATORS = re.compile(r"[_\-]+")


def exhausted(failures, text) -> bool:
    """Whether the failed turn says the account is out of credit, balance or quota.

    ``failures`` are the stage's failed-turn and error event rows, without their accounting, and
    ``text`` everything the provider said about the failure (those rows and its trailing plain-text
    error lines).
    """
    if payment_required(failures):
        return True
    raw = str(text or "").lower()
    # Codes read as words ("billing_error", "top-up"); a reset date keeps its own hyphens.
    said = _SEPARATORS.sub(" ", raw)
    return any(phrase in said for phrase in _PHRASES) or bool(_PLAN_LIMIT.search(raw) or _FUNDS_SPENT.search(said))


def payment_required(value) -> bool:
    """Whether any failed request in ``value`` (event rows, nested as the tool wrote them) returned HTTP 402."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _STATUS_KEYS and _is_402(item):
                return True
            if payment_required(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(payment_required(item) for item in value)
    return False


def _is_402(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value == PAYMENT_REQUIRED
    return isinstance(value, str) and value.strip() == str(PAYMENT_REQUIRED)

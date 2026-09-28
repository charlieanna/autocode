"""Retry policies: which registry errors a client may retry, and how many attempts
one logical command gets."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    retryable_errors: tuple[str, ...]
    max_attempts: int


DEFAULT = RetryPolicy(retryable_errors=("SERVER_BUSY", "RATE_LIMITED"), max_attempts=3)

# DENIC (.de) rejects a repeated command with the same transaction id and may
# suspend the account after repeated duplicates: never retry there.
POLICIES = {
    "de": RetryPolicy(retryable_errors=(), max_attempts=1),
}


def policy_for(tld: str) -> RetryPolicy:
    return POLICIES.get(tld.lower(), DEFAULT)

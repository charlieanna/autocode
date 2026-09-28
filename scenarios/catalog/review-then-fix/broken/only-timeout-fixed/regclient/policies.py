"""Registry-specific retry policies.

Each registry names the errors it allows a client to retry, how many attempts
one logical command gets, and the delay between attempts."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    retryable_errors: tuple[str, ...]
    max_attempts: int
    backoff_seconds: float = 0.0

    def retryable(self, code: str) -> bool:
        return code in self.retryable_errors


def _errors(*groups):
    # Merge error groups into one sorted tuple without duplicates.
    merged = []
    for group in groups:
        for code in group:
            if code not in merged:
                merged.append(code)
    return tuple(sorted(merged))


TRANSIENT = ("SERVER_BUSY", "RATE_LIMITED")
CONNECTION = ("CONNECTION_RESET",)

DEFAULT = RetryPolicy(retryable_errors=_errors(TRANSIENT), max_attempts=3)

# Policies for the registries we currently talk to. Anything not listed here
# gets DEFAULT.
REGISTRIES = {
    "com": RetryPolicy(retryable_errors=_errors(TRANSIENT, CONNECTION), max_attempts=4, backoff_seconds=0.5),
    "net": RetryPolicy(retryable_errors=_errors(TRANSIENT, CONNECTION), max_attempts=4, backoff_seconds=0.5),
    "org": RetryPolicy(retryable_errors=_errors(TRANSIENT), max_attempts=3, backoff_seconds=1.0),
}


def policy_for(tld: str) -> RetryPolicy:
    return REGISTRIES.get(tld.lower(), DEFAULT)

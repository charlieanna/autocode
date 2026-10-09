"""Renew a domain, retrying on timeouts with a longer deadline so replies are not lost."""
import uuid

from .registry import Timeout

# Replies were being lost at the default 5 s deadline; give the registry longer.
RENEW_TIMEOUT_SECONDS = 30.0


class RenewClient:
    def __init__(self, registry, max_attempts: int = 3):
        self.registry = registry
        self.max_attempts = max_attempts

    def renew(self, domain: str, years: int = 1):
        """One logical renew. Returns the registry's Result or raises Timeout."""
        last = None
        for _ in range(self.max_attempts):
            cl_trid = uuid.uuid4().hex
            try:
                return self.registry.renew(domain, years, cl_trid, timeout=RENEW_TIMEOUT_SECONDS)
            except Timeout as error:
                last = error
        raise last

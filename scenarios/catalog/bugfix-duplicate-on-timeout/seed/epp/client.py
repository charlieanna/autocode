"""Renew a domain, retrying on timeouts."""

import uuid

from .registry import Timeout


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
                return self.registry.renew(domain, years, cl_trid)
            except Timeout as error:
                last = error
        raise last

"""Renew a domain. A timeout is reported to the caller; nothing is ever resent."""
import uuid


class RenewClient:
    def __init__(self, registry, max_attempts: int = 3):
        self.registry = registry
        self.max_attempts = max_attempts

    def renew(self, domain: str, years: int = 1):
        """One logical renew. Returns the registry's Result or raises Timeout."""
        return self.registry.renew(domain, years, uuid.uuid4().hex)

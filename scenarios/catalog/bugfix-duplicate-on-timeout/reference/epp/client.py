"""Renew a domain, retrying on timeouts without ever renewing twice."""

import uuid

from .registry import Timeout


class RenewClient:
    def __init__(self, registry, max_attempts: int = 3):
        self.registry = registry
        self.max_attempts = max_attempts

    def renew(self, domain: str, years: int = 1):
        """One logical renew. Returns the registry's Result or raises Timeout.

        A timeout leaves the outcome uncertain: the registry may have processed
        the command and lost the reply. Before resending, ask the registry for
        the transaction's result; resend only when it never saw the command,
        and keep the same cl_trid so a late duplicate is recognized.
        """
        cl_trid = uuid.uuid4().hex
        last = None
        for _ in range(self.max_attempts):
            try:
                return self.registry.renew(domain, years, cl_trid)
            except Timeout as error:
                last = error
                done = self.registry.poll(cl_trid)
                if done is not None:
                    return done
        raise last

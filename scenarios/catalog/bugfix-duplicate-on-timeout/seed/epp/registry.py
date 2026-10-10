"""A fake registry. It processes each command once per client transaction id
and can be told to lose the next reply before or after processing."""

from dataclasses import dataclass


class Timeout(Exception):
    """No reply within the deadline; the registry may or may not have processed the command."""


@dataclass(frozen=True)
class Result:
    code: int
    message: str
    expiry_year: int


class FakeRegistry:
    def __init__(self, expiries: dict[str, int]):
        self.expiries = dict(expiries)
        self.processed: dict[str, Result] = {}  # cl_trid -> result
        self.mutations: list[tuple[str, str]] = []  # (domain, cl_trid), one per renew applied
        self.faults: list[str] = []  # "timeout-before" | "timeout-after", consumed per call

    def fail_next(self, *faults: str) -> None:
        self.faults.extend(faults)

    def renew(self, domain: str, years: int, cl_trid: str, timeout: float = 5.0) -> Result:
        fault = self.faults.pop(0) if self.faults else None
        if fault == "timeout-before":
            raise Timeout()
        if cl_trid in self.processed:
            # Already done: the registry answers with the stored result and does not renew again.
            result = self.processed[cl_trid]
        else:
            self.expiries[domain] += years
            self.mutations.append((domain, cl_trid))
            result = Result(1000, "Command completed successfully", self.expiries[domain])
            self.processed[cl_trid] = result
        if fault == "timeout-after":
            raise Timeout()
        return result

    def poll(self, cl_trid: str) -> Result | None:
        """The stored result of a processed command, or None if the registry never saw it."""
        return self.processed.get(cl_trid)

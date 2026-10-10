"""Processes registry events (create, renew, transfer, delete) for each domain.

Events for one domain must be processed in sequence order: a renew before its
create, or a delete before a transfer completes, corrupts the domain's state.
The current queue is a Postgres table read by a single consumer, so order is
trivially preserved; anything that replaces it must keep this property.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RegistryEvent:
    event_id: str  # unique per event, assigned by the gateway
    registry: str  # "denic", "verisign", ...
    domain: str
    kind: str  # create | renew | transfer | delete
    seq: int  # per-domain sequence number, assigned by the registry


class DomainStateError(Exception):
    pass


class Processor:
    def __init__(self, billing, notifier):
        self.billing = billing
        self.notifier = notifier
        self.last_seq: dict[str, int] = {}

    def process(self, event: RegistryEvent) -> None:
        expected = self.last_seq.get(event.domain, 0) + 1
        if event.seq != expected:
            raise DomainStateError(f"{event.domain}: got seq {event.seq}, expected {expected}")
        if event.kind == "renew":
            self.billing.charge(event.domain, event.event_id)  # charges once per call
        self.notifier.notify(event)
        self.last_seq[event.domain] = event.seq

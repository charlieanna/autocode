"""Processes registry events (create, renew, transfer, delete).

The gateway inserts every event into the queue table `registry_events`, and this
single consumer reads the table in insertion order, so events are handled in the
order they arrived. Which consumers rely on that order, and at what scope (per
domain, per registry, or not at all), has never been written down: with one
consumer the question never came up.

A transfer moves a domain from one registry to another. The transfer event, and
every later event for that domain, carries the gaining registry in `registry`.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RegistryEvent:
    event_id: str  # unique per event, assigned by the gateway
    registry: str  # "denic", "verisign", ... (about 1,400 registries)
    domain: str
    kind: str  # create | renew | transfer | delete
    seq: int  # the registry's own counter; nothing checks it


class Processor:
    def __init__(self, billing, notifier):
        self.billing = billing
        self.notifier = notifier

    def process(self, event: RegistryEvent) -> None:
        if event.kind == "renew":
            self.billing.charge(event)
        self.notifier.notify(event)

"""Send one logical command to a registry, retrying within the registry's policy."""
from dataclasses import dataclass

from .policies import policy_for
from .transport import RegistryError, Response, Timeout


@dataclass(frozen=True)
class Command:
    tld: str
    name: str
    txn_id: str


class RegistryClient:
    def __init__(self, transport):
        self.transport = transport

    def send(self, command: Command) -> Response:
        policy = policy_for(command.tld)
        attempt = 0
        while True:
            attempt += 1
            try:
                return self.transport.send(command)
            except Timeout:
                # The reply may have been lost after the registry executed the
                # command. Ask before resending: an executed command is done.
                if self.transport.status(command.txn_id) == "executed":
                    return Response(True, "OK")
                if attempt >= policy.max_attempts:
                    raise
            except RegistryError as error:
                if error.code not in policy.retryable_errors or attempt >= policy.max_attempts:
                    raise

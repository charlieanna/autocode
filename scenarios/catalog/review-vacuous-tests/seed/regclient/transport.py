"""Transport protocol and a scripted test double."""
from dataclasses import dataclass


class Timeout(Exception):
    """No reply within the deadline. The registry may or may not have executed the command."""


class RegistryError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Response:
    ok: bool
    code: str


class ScriptedTransport:
    """Plays a script of outcomes, one per send: ``ok``, ``timeout`` (lost before the
    registry saw it), ``timeout-after`` (executed, reply lost) or ``error:CODE``.
    Records every transaction the registry executed, so tests can see duplicates."""

    def __init__(self, script=()):
        self.script = list(script)
        self.executed: list[str] = []
        self.calls = 0

    def send(self, command) -> Response:
        self.calls += 1
        outcome = self.script.pop(0) if self.script else "ok"
        if outcome == "timeout":
            raise Timeout()
        if outcome == "timeout-after":
            self.executed.append(command.txn_id)
            raise Timeout()
        if outcome.startswith("error:"):
            raise RegistryError(outcome[len("error:"):])
        self.executed.append(command.txn_id)
        return Response(True, "OK")

    def status(self, txn_id: str) -> str:
        return "executed" if txn_id in self.executed else "unknown"

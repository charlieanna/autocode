"""The typed model request a unit hands the runner; transport and persistence belong to the runner.

It imports nothing, so the planning unit (units/autoplanner) builds one without importing units.common,
which imports the planning unit; units.common re-exports it under its old name.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelRequest:
    role: str
    route_role: str
    prompt: str
    metrics: dict
    schema: dict
    allow_write: bool

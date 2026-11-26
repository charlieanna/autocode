"""How long a test waits on one real AutoCode CLI call before it calls the call hung (#4196).

Such a bound only catches a hang; it decides nothing about what passes. Sized on an idle machine, it
measures the runner instead. The progressive CLI tests had 180 s and failed three at a time on a runner
that ran every test about 1.5x as long (#4194). The 21-node build graph's last review had 300 s and was cut
off mid Completion Reviewer on the nightly runner (#4196). So a bound comes from what CI recorded: at
least ``MARGIN`` times the slowest duration ``tests/suite_durations.json`` holds for the test's module
(refreshed by ``tools/suite_durations.py``), and never below the caller's floor. One call cannot take longer
than the whole module took, so only a runner more than ``MARGIN`` times slower, or a real hang, reaches it.
"""

import json
import math
from pathlib import Path

DURATIONS = Path(__file__).resolve().parent / "suite_durations.json"
MARGIN = 2


def recorded() -> dict[str, float]:
    """The CI durations, by module (``tests.``) or by test (``scenarios.``); empty when the file is missing."""
    try:
        return json.loads(DURATIONS.read_text())["seconds"]
    except (OSError, ValueError, KeyError):
        return {}


def slowest(module: str, durations: dict[str, float] | None = None) -> float | None:
    """The slowest recorded duration of ``module`` itself or of a test in it, or None when none is recorded."""
    durations = recorded() if durations is None else durations
    found = [seconds for name, seconds in durations.items() if name == module or name.startswith(module + ".")]
    return max(found) if found else None


def call_budget(module: str, floor: int, durations: dict[str, float] | None = None) -> int:
    """Seconds one CLI call of a test in ``module`` may take: ``floor``, or more when CI recorded the module."""
    longest = slowest(module, durations)
    return floor if longest is None else max(floor, math.ceil(MARGIN * longest))

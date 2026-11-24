"""Tests that share one name in one file: one identity, failed when any of them failed.

Real suites repeat names: RSpec one-liners (``it { is_expected.to be_truthy }``) describe themselves by
their matcher, and a table of cases (``test.each``, ``it.each``) can print two rows alike. A parser that
refused a repeated name made a whole passing suite unreadable, so the proof found its base "broken"
(setup sweep, docs/setup-sweep.md: httparty's RSpec suite, ufo's Vitest suite). Jest and Cargo already
merged them. Every parser now reads them the same way, here:

- an identity whose tests include a failure failed;
- otherwise one that includes a pass passed;
- otherwise it was skipped.

So a proving test passes only when every test of its name passes, and a pass of one never hides a
failure of another. Pure; imports nothing from AutoCode.
"""

from __future__ import annotations

STATES = ("passed", "failed", "skipped")


def settle(states: dict[str, set[str]]) -> dict[str, set[str]]:
    """``passed``, ``failed`` and ``skipped`` identities from each identity's observed states."""
    failed = {identity for identity, seen in states.items() if "failed" in seen}
    passed = {identity for identity, seen in states.items() if "passed" in seen} - failed
    return {"passed": passed, "failed": failed, "skipped": set(states) - failed - passed}

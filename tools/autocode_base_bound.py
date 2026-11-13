"""How long the proving tests may run on the original code.

The regression proof runs the changed tests with the change and then on the original code, where a test
that shows the change must fail. A test for a speed-up that sets no time limit of its own does not fail
there: it runs for as long as the original code takes. "count_primes(2000000) is too slow, make it fast"
(Claude models, 2026-11-13) got a test that called it with no limit; with the change the test took under a
second, and on the original code it ran until the suite's own limit, thirty minutes, for a proof that then
said only "timed out on base". The person waited half an hour for each such round.

The run with the change says how long these tests take when they finish. On the original code they get
``FACTOR`` times that, and never less than ``FLOOR`` seconds, since tests that fail there may first wait
out their own timeouts. Past that the run is stopped and the proof stays unverified, as it is for any run
that times out on the original code, with ``stopped`` as the Builder's feedback: give the test its own
time limit. Nothing here turns a timeout into a failed test or a proof.

Imports nothing from AutoCode. tests/test_base_bound.py
"""

from __future__ import annotations

import time

FLOOR = 300.0
FACTOR = 30


def timed(run):
    """``run()``'s receipt and the seconds it took."""
    started = time.monotonic()
    receipt = run()
    return receipt, time.monotonic() - started


def limit(on_candidate, candidate_seconds, timeout):
    """Seconds the proving tests get on the original code, or ``timeout`` where the bound does not apply.

    ``on_candidate`` is their receipt with the change and ``candidate_seconds`` how long that run took;
    ``timeout`` is the suite's own limit (None for none). Tests that did not pass with the change say
    nothing about how long a finished run takes, so the suite's limit stands.
    """
    if not on_candidate or on_candidate.get("timed_out") or on_candidate.get("exit_code") != 0:
        return timeout
    bound = max(FLOOR, FACTOR * candidate_seconds)
    return bound if timeout is None or bound < timeout else timeout


def stopped(on_base, candidate_seconds, allowed, timeout) -> str | None:
    """Why the proof has no result on the original code, when this bound stopped the run there."""
    if not on_base or not on_base.get("timed_out") or allowed is None or allowed == timeout:
        return None
    return (
        f"The regression tests pass with the change in {candidate_seconds:.1f} seconds and did not finish on the "
        f"original code in {allowed:.0f} seconds, the longest the proof lets them run there. A test that never "
        "finishes on the original code shows nothing: give the test its own time limit, so that on the original "
        "code it fails instead of running on"
    )


def ran_with_tests(checks, targeted_command):
    """The receipt that already says what the suite does on the original code with the change's tests, or None.

    Without a targeted command the whole suite is what ran there (``regression_on_base``). With one, the
    targeted tests are in the suite: where they timed out on the original code the suite would too, so their
    receipt stands for it and the guards are unproven (first_suite.suite_guards) without a second wait.
    """
    targeted = checks.get("regression_on_base")
    return targeted if targeted and (not targeted_command or targeted.get("timed_out")) else None

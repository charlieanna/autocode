"""The Builder's stronger attempt fails like its ordinary ones, and the run ends at the ladder's end (#612).

In check mode (``run is None``) the oracle scores the greeting fix of completion-rework-direct, so the
controls reject the seed and accept the reference. With a run it judges the stop, never the undelivered
fix: the status view's ``escalations`` (the rows ``autocode --status --json`` prints) list the one stronger
attempt with what it cost and end it ``paused``; no Builder attempt followed; the run ends with AutoCode's
recorded could-not-finish outcome (#2597), its reason in the status view and the charged incident in the
outcome's evidence; and the seed's tests were kept.
"""

import hashlib

from harness import catalog
from harness.oracle import Check, escalation_checks

# The driver's default Builder route (harness/driver.py FAKE_FLAGS), then the Builder retry policy's default
# stronger attempt, GPT-6 Sol at xhigh, as the Codex engine spells it.
CLIMBED = (("gpt-5.6-terra", "medium"), ("gpt-6-sol", "xhigh"))
# The Builder's attempts: the first, its one ordinary retry, then the stronger attempt; then the ladder ends.
BUILDS = 3
# The runner's own stamps (the view's escalation_outcomes): the ordinary retry and the stronger attempt, both
# `paused` at the retry-limit pause, as the row reads.
STAMPED = {"passed": 0, "paused": 2, "open": 0}
# The run ends instead of pausing (#2597): the status view shows the recorded could-not-finish outcome.
ENDED = "PAUSED_INTERVENTION"
# The outcome's own words in the view's stop reason (autocode_stop, builder_policy.EXHAUSTED).
EXHAUSTED = (
    "AutoCode could not finish this task: Implementation remains blocked after the configured retries "
    "and the stronger Builder attempt"
)
# The charged incident the outcome's evidence keeps (harness/escalation_provider.py names the stage and
# the ladder's length: the ordinary retry, the stronger attempt, then the end).
INCIDENT = {"stage": "builder", "attempt": 3}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def product_checks(project):
    sibling = catalog.load("completion-rework-direct")
    return sibling.oracle()(project, sibling)


def check(project, scenario, run=None):
    if run is None:
        return product_checks(project)
    checks = escalation_checks(
        run,
        kind="stronger_attempt",
        role="builder",
        job="Builder",
        climbed=CLIMBED,
        attempts=1,
        outcome="paused",
        basis="recorded",
        stamped=STAMPED,
    )
    builds = (run.get("model_stages") or []).count("builder")
    checks.append(Check("no_builder_attempt_after_the_stronger_one", builds == BUILDS, f"{builds} Builder attempts"))
    view = run.get("view") or {}
    reason = view.get("stop_reason") or ""
    checks.append(
        Check(
            "ended_at_the_ladders_end",
            run.get("status") == ENDED and view.get("done") is False and EXHAUSTED in reason,
            f"status {run.get('status')!r}; done {view.get('done')!r}; reason {reason[:300]!r}",
        )
    )
    outcome = (view.get("recovery") or {}).get("outcome") or {}
    evidence = outcome.get("evidence") or {}
    checks.append(
        Check(
            "the_outcome_keeps_the_incident",
            outcome.get("result") == "could_not_finish"
            and str(evidence.get("failure") or "") != ""
            and evidence.get("stage") == INCIDENT["stage"]
            and evidence.get("attempt") == INCIDENT["attempt"],
            f"outcome {outcome.get('result')!r}; evidence { {k: evidence.get(k) for k in ('stage', 'attempt', 'failure')}!r}",
        )
    )
    checks.append(
        Check(
            "seed_tests_kept",
            digest(project / "test_greet.py") == digest(scenario.seed / "test_greet.py"),
            "test_greet.py against the seed's, by SHA-256",
        )
    )
    return checks

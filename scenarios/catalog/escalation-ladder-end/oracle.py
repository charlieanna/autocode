"""The Builder's stronger attempt fails like its ordinary ones, and the run pauses at the ladder's end (#612).

In check mode (``run is None``) the oracle scores the greeting fix of completion-rework-direct, so the
controls reject the seed and accept the reference. With a run it judges the stop, never the undelivered
fix: the status view's ``escalations`` (the rows ``autocode --status --json`` prints) list the one stronger
attempt with what it cost and end it ``paused``; no Builder attempt followed; the run paused at the retry
limit with the stuck-stage Investigator's diagnosis in its reason; and the seed's tests were kept.
"""

import hashlib

from harness import catalog
from harness.oracle import Check, escalation_checks

# The driver's default Builder route (harness/driver.py FAKE_FLAGS), then the Builder retry policy's default
# stronger attempt, GPT-6 Sol at xhigh, as the Codex engine spells it.
CLIMBED = (("gpt-5.6-terra", "medium"), ("gpt-6-sol", "xhigh"))
# The Builder's attempts: the first, its one ordinary retry, then the stronger attempt; then the ladder ends.
BUILDS = 3
PAUSE = "PAUSED_BUILDER_RETRY_LIMIT"
# The pause's own words, then what the Investigator read in the delivered greet.py (harness/escalation_provider.py).
EXHAUSTED = "remains blocked after configured retry/escalation"
DIAGNOSIS = ("Investigator", "greet.py still guards only the argument count", "empty or whitespace-only name")


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
        role="terra",
        job="Builder",
        climbed=CLIMBED,
        attempts=1,
        outcome="paused",
        basis="recorded",
    )
    builds = (run.get("model_stages") or []).count("terra")
    checks.append(Check("no_builder_attempt_after_the_stronger_one", builds == BUILDS, f"{builds} Builder attempts"))
    view = run.get("view") or {}
    reason = view.get("stop_reason") or ""
    checks.append(
        Check(
            "paused_at_the_ladders_end",
            run.get("status") == PAUSE and view.get("done") is False and EXHAUSTED in reason,
            f"status {run.get('status')!r}; done {view.get('done')!r}; reason {reason[:300]!r}",
        )
    )
    checks.append(Check("the_pause_carries_the_diagnosis", all(part in reason for part in DIAGNOSIS), reason[:600]))
    checks.append(
        Check(
            "seed_tests_kept",
            digest(project / "test_greet.py") == digest(scenario.seed / "test_greet.py"),
            "test_greet.py against the seed's, by SHA-256",
        )
    )
    return checks

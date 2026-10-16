"""The greeting fix of completion-rework-direct, delivered by the Builder's stronger attempt (#612).

In check mode (``run is None``) only the product is scored, with completion-rework-direct's checks, so the
controls reject the seed and the broken overlay and accept the reference. With a run it also reads the
status view's ``escalations`` (the rows ``autocode --status --json`` prints): the Builder escalated once,
from its starting route to the stronger one, that one attempt is listed with what it cost, and it passed;
then no Builder attempt followed, and the run completed.
"""

from harness import catalog
from harness.oracle import Check, escalation_checks

# The driver's default Builder route (harness/driver.py FAKE_FLAGS), then the Builder retry policy's default
# stronger attempt, GPT-6 Sol at xhigh, as the Codex engine spells it.
CLIMBED = (("gpt-5.6-terra", "medium"), ("gpt-6-sol", "xhigh"))
# The Builder's attempts: the first, its one ordinary retry, then the stronger attempt.
BUILDS = 3


def product_checks(project):
    sibling = catalog.load("completion-rework-direct")
    return sibling.oracle()(project, sibling)


def check(project, scenario, run=None):
    checks = product_checks(project)
    if run is None:
        return checks
    checks += escalation_checks(
        run,
        kind="stronger_attempt",
        role="terra",
        job="Builder",
        climbed=CLIMBED,
        attempts=1,
        outcome="passed",
        basis="validation",
    )
    builds = (run.get("model_stages") or []).count("terra")
    checks.append(Check("no_builder_attempt_after_the_stronger_one", builds == BUILDS, f"{builds} Builder attempts"))
    view = run.get("view") or {}
    checks.append(
        Check(
            "completed_on_the_stronger_attempts_work",
            run.get("status") == "TASK_COMPLETE" and view.get("done") is True,
            f"status {run.get('status')!r}; done {view.get('done')!r}",
        )
    )
    return checks

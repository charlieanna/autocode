"""A guard on existing behavior in a project whose tests are not named one by one.

A plan guards behavior that must keep working with ``guard:`` and the test that shows it, and the regression
proof needs that test to pass on the original code and with the change. That takes a runner that names its
tests. Minitest files run one by one, ``make test`` and a shell suite report an exit code, so such a project
had no guard at all: a change that alters no behavior (a refactor) could only be proven with a new test, and
a plan that changed code alone failed its proof with "No regression test was added or changed". On a real
run of a refactor in a Minitest project (2026-11-13, docs/bugs/2026-11-13-task-kind-survey.md) the run then
asked the person to allow new tests in the existing test file and finished after 20 stages.

Owner decision, 2026-11-13: in such a project a plan guards with the command that runs the existing tests,
``guard: run `ruby test/pager_test.rb```. The proof runs that command on the original code and with the change,
and both must pass. When every criterion the proof is given is such a guard and the change touches no test
file, the change is coverage of existing behavior, as a change guarded by named tests is
(autocode_regression.preserve_only): it needs no new test. Changed product lines still need the Tester's
evidence (autocode_changed_code), and a bug fix still needs a test that fails without it.

The command must run tests the project already has: it names a test file of the original code, or it is
the suite AutoCode detects. A project whose runner names its tests keeps naming them:
a command guard there is refused when the plan is drafted and ignored by the proof.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_plan_proof_command as plan_command
    from . import autocode_test_cases as test_cases
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_plan_proof_command as plan_command
    import autocode_test_cases as test_cases

if TYPE_CHECKING:
    from .autocode_run_state import RunState


def plan_guards(body) -> list[dict]:
    """A plan body's criteria that guard with a command, as id, text and command, in criterion order."""
    rows = (body.get("acceptance_criteria") if isinstance(body, dict) else None) or []
    return [
        {"id": row["id"], "text": row.get("criterion", ""), "command": command}
        for row in rows
        if isinstance(row, dict)
        and row.get("id")
        and (command := test_cases.guard_command(row.get("verification_method")))
    ]


def due(state: RunState, *, all_due: bool = False) -> list[dict]:
    """The approved plan's command guards that are due now, as ``test_cases.contract_cases`` scopes cases."""
    if test_cases.design_only(state):
        return []
    scope = None if all_due else test_cases.in_scope(state)
    return [
        guard
        for guard in plan_guards((state.get("goal_contract") or {}).get("body"))
        if scope is None or guard["id"] in scope
    ]


def runs_existing_tests(command, framework, original_tests) -> bool:
    """Whether ``command`` runs tests the original code has: it names one of ``original_tests``, or it is
    ``framework``'s suite. A command that also names a test the change adds fails on the original code."""
    if framework is not None and " ".join(command.split()) == " ".join(str(framework.suite or "").split()):
        return True
    return bool(plan_command.test_files(command, original_tests))


def usable(guards, framework, original_tests, *, per_test: bool | None = None) -> list[dict]:
    """The ``guards`` the proof runs: none where the runner names its tests, and none that runs no test the
    original code has."""
    names_tests = (framework is not None and framework.per_test) if per_test is None else per_test
    if names_tests:
        return []
    return [guard for guard in guards if runs_existing_tests(guard["command"], framework, original_tests)]


def problem(guards, framework, original_tests, *, per_test: bool | None = None) -> str | None:
    """Why the plan's command guards could never be proven, for the Planner, or None."""
    if not guards:
        return None
    named = ", ".join(guard["id"] for guard in guards)
    names_tests = (framework is not None and framework.per_test) if per_test is None else per_test
    if names_tests:
        return (
            f"Criteria {named} guard with a command, but this project's {framework.name} suite names each test "
            "it runs: guard existing behavior with the existing test that shows it, by its name "
            '("guard: test_counts_a_partial_page", or a test\'s title where tests have titles)'
        )
    unusable = [guard for guard in guards if not runs_existing_tests(guard["command"], framework, original_tests)]
    if not unusable:
        return None
    return (
        "A guard's command must run tests the project already has, so it can pass on the original code: "
        + "; ".join(f"{guard['id']} `{guard['command']}`" for guard in unusable)
        + " names no existing test file"
        + (f" and is not the project's suite `{framework.suite}`" if framework is not None else "")
        + '. Write the command that runs the existing test file ("guard: run `ruby test/pager_test.rb`"), or '
        "give the criterion an ordinary verification_method"
    )


def judge(guard, on_base, on_change) -> tuple[list[str], list[str]]:
    """(failures, unverified) from one guard's command on the original code and with the change."""
    what = f"Guard {guard['id']}'s command `{guard['command']}`"
    for receipt, when in ((on_base, "on the original code"), (on_change, "with the change")):
        if not command_receipt.completed(receipt) or receipt.get("timed_out"):
            return [], [f"{what} did not finish {when}, so it does not show the behavior holds"]
    if on_base.get("exit_code") != 0:
        return [
            f"{what} fails on the original code (exit {on_base.get('exit_code')}), so it guards nothing: a guard "
            "is behavior that already works"
        ], []
    if on_change.get("exit_code") != 0:
        return [f"{what} passes on the original code and fails with the change (exit {on_change['exit_code']})"], []
    return [], []


def note(guards) -> str:
    """What the proof records when its guards rest on commands, for the report and the Completion Reviewer."""
    return (
        "This project's runner names no test, so the plan's guards "
        + ", ".join(f"{guard['id']} (`{guard['command']}`)" for guard in guards)
        + " rest on each command's exit code on the original code and with the change"
    )

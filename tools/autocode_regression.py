"""Runner-owned regression proof for bug-fix runs.

When the approved contract's ``task_kind`` is ``"bugfix"``, the runner proves the
fix itself, with no model call, right before the Validator runs: the candidate's
new or changed tests must fail on the run's base commit and pass on the current
source, and no test that passed on base may fail now (``autocode_verify``). The
proof is bound to the exact source revision. The Validator and the Completion
Owner receive it as evidence, and the completion gate refuses a bug fix whose
current source has no passing proof. Every workflow stage still runs.

When the Investigator wrote the regression tests in plain English (its
``test_cases``), the proof also requires each case to have its own test, named
after the case id (``case_tests``, from autocode_test_cases.match_cases). A
restore case (the default, and what a case without a ``kind`` means) is proven
by a test that fails on base and passes now; a preserve case — behavior that
already worked and must keep working — by a test that passes on base and now
(``pass_to_pass``). A preserve case whose test fails on base is mis-tagged and
fails the proof.

A feature gets the same proof when its approved plan marks acceptance criteria
as tests (``verification_method: "test: test_c2_..."``,
autocode_test_cases.contract_cases): each such test must pass with the change
and must not have passed without it (verify's ``new_behavior``). A milestone
whose due criteria are all ``guard:`` (behavior the product already implements)
is coverage: the diff may be test files alone, and each test must pass on the
base and on the candidate (verify's ``preserve_only``). It may also change no
test when those tests already exist, and be empty, as in a
validation-only re-check: each must then pass on base and on the candidate, and
none of its variants may fail. A saved permission
answer that grants a test-only regression-proof exception for the current
contract is the same coverage proof, so a plan that marked that coverage
``test:`` can still finish. With several milestones, a checkpoint proves the
criteria of its own milestone and of those already accepted
(autocode_test_cases.in_scope); a milestone with none due runs no proof.
"""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import contextlib
import functools
import re

try:
    from . import autocode_source_scope as source_scope
    from . import autocode_superseded_tests as superseded_tests
except ImportError:
    import autocode_source_scope as source_scope
    import autocode_superseded_tests as superseded_tests


import subprocess
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_tests as acceptance
    from . import autocode_base_command as base_command
    from . import autocode_base_patch as operator_patch
    from . import autocode_changed_code_continuation as changed_code_continuation
    from . import autocode_changed_code_proof as changed_code
    from . import autocode_changed_code_trigger as changed_code_trigger
    from . import autocode_command_guards as command_guards
    from . import autocode_companion_suites as companion_suites
    from . import autocode_companion_tests as companion_tests
    from . import autocode_component_plan as component_plan
    from . import autocode_diff_coverage as diff_coverage
    from . import autocode_first_suite as first_suite
    from . import autocode_focused_suite as focused_suite
    from . import autocode_follow_up as follow_up
    from . import autocode_follow_up_proof as follow_up_proof
    from . import autocode_goals as goals
    from . import autocode_launch_inputs as launch_inputs
    from . import autocode_mutation_check as mutation_check
    from . import autocode_package_scope as package_scope
    from . import autocode_package_writes as package_writes
    from . import autocode_pending_tests as pending_tests
    from . import autocode_plan_proof_command as plan_command
    from . import autocode_python_test_roles as role_policy
    from . import autocode_recovery_review as recovery_review
    from . import autocode_regression_selection as selection
    from . import autocode_roles as roles
    from . import autocode_run_base as run_base
    from . import autocode_runner_check as runner_check
    from . import autocode_status as status
    from . import autocode_test_cases as test_cases
    from . import autocode_test_role_inventory as role_inventory
    from . import autocode_util as util
    from . import autocode_verification_schedule as schedule
    from . import autocode_verify as verify
    from . import autocode_wrapped_runner as wrapped_runner
except ImportError:
    import autocode_acceptance_tests as acceptance
    import autocode_base_command as base_command
    import autocode_base_patch as operator_patch
    import autocode_changed_code_continuation as changed_code_continuation
    import autocode_changed_code_proof as changed_code
    import autocode_changed_code_trigger as changed_code_trigger
    import autocode_command_guards as command_guards
    import autocode_companion_suites as companion_suites
    import autocode_companion_tests as companion_tests
    import autocode_component_plan as component_plan
    import autocode_diff_coverage as diff_coverage
    import autocode_first_suite as first_suite
    import autocode_focused_suite as focused_suite
    import autocode_follow_up as follow_up
    import autocode_follow_up_proof as follow_up_proof
    import autocode_goals as goals
    import autocode_launch_inputs as launch_inputs
    import autocode_mutation_check as mutation_check
    import autocode_package_scope as package_scope
    import autocode_package_writes as package_writes
    import autocode_pending_tests as pending_tests
    import autocode_plan_proof_command as plan_command
    import autocode_python_test_roles as role_policy
    import autocode_recovery_review as recovery_review
    import autocode_regression_selection as selection
    import autocode_roles as roles
    import autocode_run_base as run_base
    import autocode_runner_check as runner_check
    import autocode_status as status
    import autocode_test_cases as test_cases
    import autocode_test_role_inventory as role_inventory
    import autocode_util as util
    import autocode_verification_schedule as schedule
    import autocode_verify as verify
    import autocode_wrapped_runner as wrapped_runner

STAGE = "regression_proof"
SUMMARY_KEYS = (
    "framework",
    "verdict",
    "failures",
    "unverified",
    "notes",
    "review_reasons",
    "fail_to_pass",
    "pass_to_pass",
    "not_run_on_base",
    "commands",
    "base",
    "base_patch",
    "source_revision",
    "test_files",
    "source_files",
    "case_tests",
    "case_scope",
    "unstable",
    "proof_stability",
    "suite_flakes",
    "diff_coverage",
    "mutation_check",
    "follow_up_baseline",
    "changed_code_inputs",
    "suite_fallback",
    "changed_code_evidence",
    "changed_code_receipts",
)


# What the judging stages' prompts say about the proof (autocode_stage_context): the reviewers use it
# instead of re-running the same tests, and never override it.
PROMPT_NOTES = {
    "passed": {
        "validator": prompts.get("fragments/regression/prompt-notes.md"),
        "owner": prompts.get("fragments/regression/prompt-notes-02.md"),
    },
    "open": {
        "validator": prompts.get("fragments/regression/prompt-notes-03.md"),
        "owner": prompts.get("fragments/regression/prompt-notes-04.md"),
    },
}
# Appended to either note when a mutant survived the proving tests (#699): reported, never a failure.
MUTATION_NOTE = prompts.get("fragments/regression/mutation-note.md")
CHANGED_CODE_NOTE = prompts.get("fragments/regression/changed-code-evidence.md")


def mutation_note(proof) -> str:
    """The judging stages' note on surviving mutants, or "" when the proof has none."""
    if isinstance((proof or {}).get("changed_code_evidence"), dict):
        return CHANGED_CODE_NOTE
    return MUTATION_NOTE if (((proof or {}).get("mutation_check") or {}).get("survivors")) else ""


def required(state: RunState, *, all_due=False, include_changed_code=True, owed=None):
    """Whether a bug fix, due test criterion or cumulative product diff owes proof.

    A Builder self-check precedes the runner's changed-line proof and independent
    audit. It may exclude that new obligation, never a bug fix or named criterion.
    ``owed`` is ``changed_code_trigger.owes(state)`` when the caller already has it.
    """
    return (
        goals.task_kind(state) == "bugfix"
        or bool(test_cases.contract_cases(state, all_due=all_due))
        or include_changed_code
        and (changed_code_trigger.owes(state) if owed is None else owed)
    )


def _test_only_exception(state: RunState):
    """True when the user granted a test-only regression-proof exception on this contract."""
    contract = state.get("goal_contract") or {}
    if not contract.get("hash") or contract.get("revision") is None:
        return False
    try:
        from .autocode_contract_identity import token
    except ImportError:
        from autocode_contract_identity import token
    current = token(contract)
    for answer in (state.get("answers") or {}).values():
        if not isinstance(answer, dict) or answer.get("kind") != "permission_answer":
            continue
        if answer.get("contract_token") != current:
            continue
        text = str(answer.get("text") or "").lower()
        if text.startswith("grant") and "test-only" in text and "regression" in text:
            return True
    return False


def preserve_only(state: RunState, *, all_due=False):
    """Coverage of existing behavior: every due case is a guard, or the user granted that exception.

    A contract of plain ``test:`` cases can still be a coverage build when its diff turns out
    to be tests alone; that diff-dependent rule is applied where the diff is known (_prove,
    passing verify's ``test_only_allowed``, #510).
    """
    due = cases(state, all_due=all_due)
    if not due:
        return False
    if goals.task_kind(state) == "bugfix":
        if (state.get("goal_contract") or {}).get("approval_status") != "approved" or not all(
            case.get("kind") == "preserve" for case in due
        ):
            return False
        try:
            from . import autocode_coordination_context as coordination
        except ImportError:
            import autocode_coordination_context as coordination
        # A preservation-only member does not itself claim to repair the bug.
        # The separate joint gate still requires real fail-before/pass-after.
        return bool(coordination.context(state))
    return all(case.get("kind") == "preserve" for case in due) or _test_only_exception(state)


def cases(state: RunState, *, all_due=False):
    """The English cases this proof must cover: the bug's diagnosis, else the plan's test criteria."""
    return test_cases.proof_cases(state, all_due=all_due)


# The run's base moved to autocode_run_base, so the package check (autocode_package_writes) reads it
# without importing the proof; callers keep reading it here.
base_commit = run_base.base_commit


def head(workspace):
    result = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--verify", "HEAD"], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


LAUNCH_REFS = "refs/autocode/launch/"
LAUNCH_REF_GRACE_SECONDS = 86400  # a run launched in another worktree has saved its run directory by then
MISSING_CASES_REASON = "Completion rejected: the milestone proof still needs all required cases: "
CHANGED_CODE_REASON = (
    "Completion rejected: the runner's regression proof for this source reports no failed case; the "
    "independent changed-line or receipt-custody evidence the completion gate requires is missing or "
    "no longer binds. A fresh independent review of the changed lines is required."
)


def _git(workspace, *args):
    result = subprocess.run(["git", "-C", str(workspace), *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def launch_base(workspace, run_dir):
    """The base_commit of a new in-place run: HEAD, or, when the checkout holds uncommitted or
    untracked files, a child commit of HEAD that holds them (verify.commit_worktree). The proof
    then runs them as original code instead of counting them as the change. A ref named after the
    run keeps that commit from Git's garbage collection; the caller holds the checkout
    (autocode_checkout_lock), so no other run's agents are editing it."""
    commit = verify.commit_worktree(workspace)
    if commit and commit != head(workspace):
        _git(workspace, "update-ref", LAUNCH_REFS + Path(run_dir).name, commit)
    with contextlib.suppress(RuntimeError, ValueError, OSError):
        _drop_finished_launch_refs(workspace)  # housekeeping only: the next launch tries again
    return commit


def start(state: RunState, workspace, run_dir, package=None):
    """Save a new in-place run's base_commit (launch_base); a package run (``package``, else the saved
    one) also records what its checkout holds outside the package (autocode_package_writes.at_launch)."""
    state["base_commit"] = launch_base(workspace, run_dir)
    package_writes.at_launch(state, workspace, package)


def _drop_finished_launch_refs(workspace):
    """Delete the launch refs of runs that no worktree of the repository holds any more (a deleted
    or archived run), so Git can collect their launch-time files. A ref younger than a day is kept:
    its run may be launching in another worktree and not have saved its run directory yet."""
    roots = [
        Path(line[len("worktree ") :])
        for line in _git(workspace, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]
    cutoff = time.time() - LAUNCH_REF_GRACE_SECONDS
    listing = _git(workspace, "for-each-ref", "--format=%(refname)%09%(committerdate:raw)", LAUNCH_REFS)
    for ref, _, date in (line.partition("\t") for line in listing.splitlines()):
        name = ref[len(LAUNCH_REFS) :]
        if int((date.split() or [0])[0]) < cutoff and not any(
            (root / ".autocode/runs" / name).exists() for root in roots
        ):
            _git(workspace, "update-ref", "-d", ref)


def settings(state: RunState):
    options = state.get("settings", {}).get("regression") or {}
    root = component_plan.effective_root(state.get("task", ""), options.get("test_root"))
    # Legacy caller ownership is transient: saved settings also bind handoffs.
    return {**options, "test_root": root} if root is not None and options.get("test_root") is None else options


def rooted(options):
    """Leave unscoped runs' detection and verification calls unchanged."""
    return {"test_root": options["test_root"]} if options.get("test_root") else {}


def _with_plan_command(options, adopted):
    """``options`` with the approved plan's command in the roles ``choose`` gave it."""
    if not adopted:
        return options
    return {
        **options,
        **({"test_command": adopted["suite"]} if adopted["suite"] else {}),
        **({"regression_command": adopted["regression"]} if adopted["regression"] else {}),
    }


def _plan_command(state: RunState, workspace, base):
    """The approved plan's test command and its roles (autocode_plan_proof_command.choose), or None."""
    commands = plan_command.approved_commands(state.get("goal_contract"))
    if not commands:
        return None
    changes = verify.changed_files(workspace, base, source_paths=source_scope.paths(state))
    original = [path for path in role_inventory.entries(workspace, base) if verify.is_test_path(path)]
    changed = [path for path, status in changes.items() if status != "deleted" and verify.is_test_path(path)]
    return plan_command.choose(commands, original_tests=original, changed_tests=changed)


def suite_timeout(state: RunState):
    """Seconds the runner lets one suite run take, or None for no limit.

    An explicit settings.regression.test_timeout wins. Otherwise the proof follows the run's
    own tool-call limit, never below the default, and has none when the run turned that off:
    a full suite routinely outlasts the 900-second default.
    """
    if "test_timeout" in settings(state):
        return settings(state)["test_timeout"]
    limit = (state.get("settings", {}).get("limits") or {}).get("tool_timeout_seconds")
    if limit is None:
        return verify.DEFAULT_TIMEOUT
    return max(limit, verify.DEFAULT_TIMEOUT) if limit else None


def reviewed_patch(state: RunState, workspace):
    """The patch a review follow-up fixes (autocode_follow_up), or None.

    Its findings exist only with that change applied, so the proof's "original code" is the
    base with the patch applied: on the code before it, a test for a finding can pass (the
    behavior was fine before the change) and every test the change rewrote fails.
    """
    reviewed = follow_up.review_findings(state) or {}
    name = str(reviewed.get("change_patch") or "").strip()
    if not name:
        return None
    candidates = [
        Path(workspace) / name,
        *([Path(state["project_workspace"]) / name] if state.get("project_workspace") else []),
    ]
    return next((path for path in candidates if path.is_file()), candidates[0])


def clean_base_run(state: RunState, workspace, run_dir, native=None, progressive=None):
    """The clean replay's base runner (#3160): autocode_base_command.run on the original code the proof
    compares the change with (the run's base revision, with a review follow-up's reviewed change applied),
    under the same launch-input guard as the candidate's clean runner. A planned check that fails is run
    there, and a failure the base has identically is the base's (autocode_base_attribution). None, so
    every failure stays the change's, under a native check owner or progressive verification (whose
    replays stay fail-closed), without a recorded base, or with an operator base patch pinned."""
    base = base_commit(state, workspace)
    if native is not None or progressive or not base or operator_patch.pinned(state):
        return None
    bound = functools.partial(base_command.run, base=base, base_patch=reviewed_patch(state, workspace))
    return launch_inputs.runners(state, workspace, run_dir, bound)[0]


def proof_dependencies(state: RunState, workspace):
    """verify.verify's ``dependencies_from`` and ``independent_dependencies`` for this run.

    The proof trees take dependencies and ignored code from the original checkout of a task
    worktree (``project_workspace``) or, in place, from the workspace itself. That checkout
    is not independent of the candidate when a Builder of the run this one continues worked
    in it: ``project_worked_in_place``, which autocode_checkpoint_continuation.create writes
    for a continuation restored from a checkpoint of an --in-place run (and every later
    restore of it). This is its only reader. None leaves verify to compare the paths.
    """
    return {
        "dependencies_from": state.get("project_workspace") or str(workspace),
        "independent_dependencies": False if state.get("project_worked_in_place") else None,
    }


def _settled_key(binding) -> str:
    """The key of a detected baseline's timeout: the command, the original code and the time limit.

    The runtime identity is left out on purpose. A settled timeout is never completed evidence; it
    decides only not to repeat this original-code command. A different focused command still runs
    under its own binding. Keyed by the runtime too, a timeout was never settled where the runtime
    cannot be bound completely (an editable install), and was missed whenever a shared environment changed
    between proofs, so every proof of the run spent the whole suite's time limit again (#3158).
    """
    return util.digest({key: binding.get(key) for key in ("base", "command", "framework", "base_patch", "timeout")})


def _baseline(
    state: RunState,
    workspace,
    run_dir,
    base,
    framework,
    suite,
    dependencies,
    base_patch=None,
    progress=None,
    inputs=None,
    settled_timeout=False,
):
    """The suite's result on the original code, reused while its binding holds.

    With ``settled_timeout``, a detected suite's timeout is retained as negative evidence. The
    caller can fall back to a focused suite, or remain UNVERIFIED when that also timed out or no
    fallback exists. It never authorizes candidate checks or completion; repeating the same
    original-code check under the same command and limit would only spend the time once more.
    """
    cached = state.get("regression_baseline") or {}
    patch = str(base_patch) if base_patch else None
    binding = {
        "base": base,
        "command": suite,
        "framework": framework.to_dict() if framework else None,
        "base_patch": schedule.tree_identity(base_patch) if base_patch else None,
        "timeout": suite_timeout(state),
        "runtime": verify.baseline_identity(
            workspace, command=suite, dependencies_from=dependencies, ignored_inputs=inputs
        ),
    }
    if framework is not None and framework.name == "junit":
        binding["runtime"]["reuse_supported"] = False
        binding["runtime"]["cache_policy"] = "fresh_junit_runtime_execution"
    if cached.get("binding") == binding and binding["runtime"].get("reuse_supported"):
        try:
            result = util.read(cached["path"])
            if (
                util.file_hash(cached["path"]) == cached.get("sha256")
                and schedule.intact(result["receipt"], root=Path(cached["path"]).parent)
                and not result["receipt"].get("timed_out")
                and (not result["receipt"].get("results_expected") or schedule.complete_results(result["receipt"]))
            ):
                return result
        except (OSError, ValueError, TypeError, KeyError):
            pass
    timeouts = dict(cached.get("settled_timeouts") or {})
    timeout_key = _settled_key(binding)
    settled = timeouts.get(timeout_key) if settled_timeout else None
    # Older runs indexed this negative evidence by the complete runtime binding. Only the
    # last baseline retains that binding, so migrate its entry when the stable inputs match;
    # other historical keys cannot be re-derived safely. The original entry stays readable.
    previous = cached.get("binding")
    if settled_timeout and not settled and isinstance(previous, dict) and _settled_key(previous) == timeout_key:
        settled = timeouts.get(util.digest(previous))
    if settled:
        try:
            result = util.read(settled["path"])
            # A timeout is never completed evidence (command_receipt.completed), so it cannot pass
            # schedule.intact; its pinned record decides only not to repeat this original-code command.
            if util.file_hash(settled["path"]) == settled.get("sha256") and focused_suite.timed_out(result):
                timeouts[timeout_key] = settled
                state["regression_baseline"] = {**cached, "settled_timeouts": timeouts}
                return result
        except (OSError, ValueError, TypeError, KeyError):
            pass
    directory = Path(run_dir) / "regression" / ("baseline-" + uuid.uuid4().hex)
    if progress:
        progress(
            "Testing the original code before comparing the change",
            command=suite,
            output=directory / "baseline" / "suite-on-base.log",
        )
    result = verify.baseline(
        workspace,
        base,
        directory,
        framework=framework,
        suite_command=suite,
        dependencies_from=dependencies,
        timeout=suite_timeout(state),
        base_patch=base_patch,
        ignored_inputs=inputs,
    )
    path = directory / "baseline.json"
    util.atomic_json(path, result)
    if focused_suite.timed_out(result):
        timeouts[_settled_key(binding)] = {"path": str(path), "sha256": util.file_hash(path)}
    state["regression_baseline"] = {
        **({"settled_timeouts": timeouts} if timeouts else {}),
        "base": base,
        "command": suite,
        "path": str(path),
        "health": result["health"],
        "base_patch": patch,
        "binding": binding,
        "sha256": util.file_hash(path),
    }
    return result


def prove(state: RunState, workspace, run_dir, *, all_due=False):
    """Run (or reuse) the proof for the current source; return its summary. Launches no model.

    ``all_due`` proves every milestone's cases, as final completion requires."""
    workspace = Path(workspace)
    schedule.guard(Path(run_dir) / "check-replay" / "obligations")
    current = source_scope.snapshot(workspace, state)["revision"]
    saved = state.get("regression_proof") or {}
    scope = sorted(case["id"] for case in cases(state, all_due=all_due))
    options = settings(state)
    command = options.get("test_command")
    # A trusted explicit collector chooses the result parser and targeted tests; in a package run, so does
    # one written for the package as `cd PACKAGE && RUNNER`, read inside it (autocode_package_scope.own).
    framework = (
        verify.command_framework(command, test_report=options.get("test_report"))
        or package_scope.own(state.get("settings"), command, verify.command_framework)
        if command
        else None
    )
    python = (
        (framework.python if framework and framework.name in ("pytest", "unittest") else None)
        or options.get("python")
        or verify.python_for(state.get("project_workspace") or workspace)
    )
    base = base_commit(state, workspace)
    detection = rooted(options)
    detection["base"] = base
    # A package run's suite is detected in its package and runs from there (autocode_package_scope).
    detection.update(package_scope.detection(state.get("settings")))
    framework = framework or verify.detect_framework(workspace, python=python, **detection)
    adopted = None
    if framework is None and base and not command and not options.get("regression_command"):
        # No runner AutoCode knows and nothing typed: the approved plan's own test command, never a
        # question to the person about flags (autocode_plan_proof_command).
        adopted = _plan_command(state, workspace, base)
        options = _with_plan_command(options, adopted)
        command = options.get("test_command")
    unscoped = "package" not in detection and "test_root" not in detection
    companions: list = []
    companion_notes: list = []
    guard_suites: list = []
    test_suites: list = []
    if base and unscoped and not command and not options.get("regression_command"):
        # Tests the root's suite cannot run are proven with the suite of the project around them, as in a
        # --package run of that directory (autocode_package_scope.for_tests). Changed tests in more than one
        # project are each proven with their own project's suite (autocode_companion_tests). Every other
        # project whose code changed and whose tests that suite cannot run, the root included, runs its own
        # suite on the original code and with the change (autocode_companion_suites, #2889).
        changes = verify.changed_files(workspace, base, source_paths=source_scope.paths(state))
        roles = role_inventory.from_git(workspace, base, verify.is_test_path, changes=changes)
        tests = [path for path, change in changes.items() if roles.is_test(path) and change != "deleted"]
        detect = lambda directory: verify.detect_framework(directory, python=python)  # noqa: E731
        root_framework = framework
        framework, test_suites, test_notes = companion_suites.test_suites(
            workspace, root_framework, tests, detect, is_code=verify.is_code_path
        )
        code = [path for path in changes if verify.is_code_path(path) and not roles.is_test(path)]
        # Every original file, changed or not: one whose role cannot be proven stays protected, as in the
        # protected-test inventory (autocode_verify), instead of stopping the proof as a changed one does.
        original_tests = [
            path
            for path in role_inventory.entries(workspace, base)
            if verify.is_test_path(path) and roles.protected(path)
        ]
        companions, companion_notes = companion_suites.find(
            workspace,
            framework,
            root_framework,
            code,
            original_tests,
            detect,
            proven={suite["project"] for suite in test_suites},
        )
        companion_notes = [*test_notes, *companion_notes]
        # A guard may name a test of another project: that project's own suite runs its file (#2889).
        preserved = [
            case
            for case in test_cases.bound(cases(state, all_due=all_due), framework.name if framework else None)
            if case.get("kind") == "preserve"
        ]
        guards = sorted({case["test_name"] for case in preserved if case.get("test_name")})
        # A guard may also name another project's test by the title its runner knows it by (a Node test
        # beside a Python project), which the proof's own runner does not read as a test name.
        titles = sorted({case["title"] for case in preserved if not case.get("test_name") and case.get("title")})
        guard_suites, guard_notes = companion_suites.guard_suites(
            workspace, framework, root_framework, original_tests, _guards_named(workspace, guards, titles), detect
        )
        companion_notes = [*companion_notes, *guard_notes]
    try:
        from . import autocode_replay_checks as recorded_checks
    except ImportError:
        import autocode_replay_checks as recorded_checks
    if recorded_checks.simulated(state.get("settings")):
        recorded_checks.require_supported(state)
        if reviewed_patch(state, workspace) or operator_patch.pinned(state):
            raise ValueError("Recorded replay does not support reviewed or operator base patches in v1")
        if command_guards.due(state, all_due=all_due):
            raise ValueError("Recorded replay does not support guards that are commands in v1")
        # The same selection the live proof makes (verify.verify): an explicit command, else the
        # detected suite and the regression derived from the changed tests, so a recorded proof
        # whose regression command was derived is compared with the replay's own derivation (#2500).
        changes = verify.changed_files(workspace, base, source_paths=source_scope.paths(state))
        roles = role_inventory.from_git(workspace, base, verify.is_test_path, changes=changes)
        if framework is not None:
            framework.test_roles = roles
        commands = verify.select_commands(
            framework,
            [path for path, status in changes.items() if roles.is_test(path) and status != "deleted"],
            suite_command=options.get("test_command"),
            regression_command=options.get("regression_command"),
        )
        if not commands["suite"]:
            raise ValueError("Recorded replay requires an exact archived suite command")
        return recorded_checks.regression(
            state, workspace, case_ids=scope, suite=commands["suite"], regression_command=commands["regression"]
        )
    operator = operator_patch.pinned(state)
    # The ignored files an in-place run's scratch trees receive: those it started with (#529).
    inputs = launch_inputs.supply(state, workspace, run_dir) if base else None
    turn_baseline = follow_up_proof.context(state, workspace, run_dir, cases(state, all_due=all_due))
    # Stored only in regression_proof; prove reads it before reusing evidence.
    # A repaired test environment must invalidate a prior failure (or PASS)
    # even when the source and acceptance criteria have not changed.
    execution_context = {
        "changed_code_policy": changed_code.VERSION,
        "test_role_policy": role_policy.VERSION,
        "protected_test_binding": ((state.get("settings") or {}).get("protected_tests") or {}).get("binding_hash"),
        "python": python,
        "detected_framework": framework.to_dict() if framework else None,
        "test_command": options.get("test_command"),
        "test_report": options.get("test_report"),
        "regression_command": options.get("regression_command"),
        **({"plan_command": adopted} if adopted else {}),
        "timeout": suite_timeout(state),
        "identity": verify.execution_identity(
            workspace,
            command=options.get("test_command") or (framework.suite if framework else None),
            dependencies_from=state.get("project_workspace"),
            source_paths=source_scope.paths(state),
            ignored_inputs=inputs,
        )
        if base
        else {"source_revision": current, "reuse_supported": False},
        "base": base,
        "base_patch": schedule.tree_identity(reviewed_patch(state, workspace))
        if reviewed_patch(state, workspace)
        else None,
        "operator_base_patch": {"pin": operator, "file": schedule.tree_identity(operator["path"])}
        if operator
        else None,
        "contract_identity": util.digest(state.get("goal_contract")),
        "cases_identity": util.digest(cases(state, all_due=all_due)),
        "preserve_only": preserve_only(state, all_due=all_due),
        "follow_up_baseline": turn_baseline,
        **(
            {"companion_suites": {companion_suites.label(at): found.to_dict() for at, found in companions}}
            if companions
            else {}
        ),
        **({"companion_tests": companion_tests.context(test_suites)} if test_suites else {}),
        **(
            {
                "companion_guards": {
                    companion_suites.label(found["project"]): {
                        "framework": found["framework"].to_dict(),
                        "files": found["files"],
                        "command": found["command"],
                    }
                    for found in guard_suites
                }
            }
            if guard_suites
            else {}
        ),
    }
    if (
        saved.get("source_revision") == current
        and saved.get("case_scope", scope) == scope
        and saved.get("execution_context") == execution_context
        and saved.get("verdict") == verify.PASS
        and execution_context["identity"]["reuse_supported"]
        and not (inputs and inputs.unverified)
    ):
        reusable = False
        try:
            result = util.read(saved["path"])
            if (
                util.file_hash(saved["path"]) == saved.get("receipt_sha256")
                and schedule.proof_intact(result["checks"], root=Path(saved["path"]).parent)
                and changed_code.intact(saved, result)
                and all(
                    (not row.get("results_expected") or schedule.complete_results(row))
                    for row in result["checks"].values()
                )
            ):
                reusable = True
        except (OSError, ValueError, TypeError, KeyError):
            pass
        if reusable:
            return changed_code_continuation.continue_proof(
                state,
                workspace,
                run_dir,
                saved,
                framework,
                inputs,
                lambda: verify.execution_identity(
                    workspace,
                    command=options.get("test_command") or (framework.suite if framework else None),
                    dependencies_from=state.get("project_workspace"),
                    source_paths=source_scope.paths(state),
                    ignored_inputs=inputs,
                ),
            )
    with runner_check.track(state, run_dir, STAGE, "Preparing regression checks", status.persist) as progress:
        proof = _prove(
            state,
            workspace,
            run_dir,
            current,
            scope,
            progress,
            framework,
            execution_context,
            inputs,
            all_due=all_due,
            companions=(companions, companion_notes),
            guard_suites=guard_suites,
            test_suites=test_suites,
        )
        proof["execution_context"] = execution_context
        if proof.get("path"):
            proof["receipt_sha256"] = util.file_hash(proof["path"])
    return changed_code_continuation.continue_proof(
        state,
        workspace,
        run_dir,
        proof,
        framework,
        inputs,
        lambda: verify.execution_identity(
            workspace,
            command=options.get("test_command") or (framework.suite if framework else None),
            dependencies_from=state.get("project_workspace"),
            source_paths=source_scope.paths(state),
            ignored_inputs=inputs,
        ),
    )


def _prove(
    state: RunState,
    workspace,
    run_dir,
    current,
    scope,
    progress,
    framework,
    execution_context,
    inputs,
    *,
    all_due=False,
    companions=((), ()),
    guard_suites=(),
    test_suites=(),
):
    kept: list = []  # the candidate tree verify() leaves for the mutation check (#699), removed here
    try:
        return _proving(
            state,
            workspace,
            run_dir,
            current,
            scope,
            progress,
            framework,
            execution_context,
            inputs,
            kept,
            all_due,
            companions=companions,
            guard_suites=guard_suites,
            test_suites=test_suites,
        )
    except verify.command_receipt.OwnershipUncertain:
        # The supervisor could not establish that every child stopped. Keep
        # the exact tree and command captures for normal ownership recovery.
        kept.clear()
        raise
    finally:
        for tree in kept:
            if tree.exists():
                verify.remove_tree(workspace, tree)


def _protect_companions(proof, result, companions, workspace, base, out, kept, dependencies, patch, inputs, timeout):
    """Run each other project's suite the change reaches on the original code and with it (#2889).

    ``companions`` is autocode_companion_suites.find's (suites, notes). The change's run uses the candidate
    tree verify() kept (``kept[-1]``); the original code's run is a fresh tree of ``base``, as the proof's
    own baseline is. The receipts join the proof's checks, and a finding only ever lowers its verdict.
    """
    suites, notes = companions
    proof["notes"] = [*(proof.get("notes") or []), *notes]
    if not suites or not kept or not kept[-1].is_dir():
        return
    failures, unverified = [], []
    for project, found in suites:
        at, evidence = companion_suites.label(project), out / "companion" / (project.replace("/", "-") or "root")
        on_base = verify.baseline(
            workspace,
            base,
            evidence,
            framework=found,
            suite_command=found.suite,
            dependencies_from=dependencies["dependencies_from"],
            timeout=timeout,
            base_patch=patch,
            ignored_inputs=inputs,
        )["receipt"]
        on_change = verify.run_suite(found, found.suite, kept[-1], evidence, "suite-on-candidate", timeout=timeout)
        result["checks"][f"companion_suite_on_base:{at}"] = on_base
        result["checks"][f"companion_suite_on_candidate:{at}"] = on_change
        failed, missing = companion_suites.judge(project, found.suite, on_base, on_change)
        failures += failed
        unverified += missing
    proof["failures"] = [*(proof.get("failures") or []), *failures]
    proof["unverified"] = [*(proof.get("unverified") or []), *unverified]
    proof["verdict"] = companion_suites.verdict(
        proof["verdict"], failures, unverified, fail=verify.FAIL, unverified_verdict=verify.UNVERIFIED
    )


def _run_guard_commands(
    proof, result, guards, framework, workspace, base, out, kept, dependencies, patch, inputs, timeout
):
    """Run each guard that is a command (autocode_command_guards) on the original code and with the change.

    As a companion suite's run: the change's is in the candidate tree verify() kept, the original code's in a
    fresh tree of ``base``. The receipts join the proof's checks, a finding only ever lowers the verdict, and
    the proof says its guards rest on exit codes. A proof that built no candidate tree ran nothing: its
    guards are unproven.
    """
    if not guards:
        return
    failures, unverified = [], []
    if not kept or not kept[-1].is_dir():
        unverified.append(
            "The plan's guards "
            + ", ".join(guard["id"] for guard in guards)
            + " were not run: the proof built no tree of the change to run their commands in"
        )
    else:
        runs: dict[str, tuple] = {}
        for guard in guards:
            command = guard["command"]
            if command not in runs:
                evidence = out / "guard-commands" / f"{len(runs) + 1:02d}"
                on_base = verify.baseline(
                    workspace,
                    base,
                    evidence,
                    framework=framework,
                    suite_command=command,
                    dependencies_from=dependencies["dependencies_from"],
                    timeout=timeout,
                    base_patch=patch,
                    ignored_inputs=inputs,
                )["receipt"]
                on_change = verify.run_suite(
                    framework, command, kept[-1], evidence, "guard-on-candidate", timeout=timeout
                )
                runs[command] = (on_base, on_change)
                result["checks"][f"guard_command_on_base:{guard['id']}"] = on_base
                result["checks"][f"guard_command_on_candidate:{guard['id']}"] = on_change
            failed, missing = command_guards.judge(guard, *runs[command])
            failures += failed
            unverified += missing
        said = command_guards.note(guards)
        proof["notes"] = [*(proof.get("notes") or []), said]
        proof["review_reasons"] = [*(proof.get("review_reasons") or []), said]
    proof["failures"] = [*(proof.get("failures") or []), *failures]
    proof["unverified"] = [*(proof.get("unverified") or []), *unverified]
    proof["verdict"] = companion_suites.verdict(
        proof["verdict"], failures, unverified, fail=verify.FAIL, unverified_verdict=verify.UNVERIFIED
    )


def _guards_named(workspace, guards, titles=()):
    """``names(path)``: the ``guards`` (exact test names) a test file in ``workspace`` names, and the declared
    ``titles`` it spells."""

    def names(path):
        if not guards and not titles:
            return []
        try:
            text = (Path(workspace) / path).read_text(errors="replace")
        except OSError:
            return []
        return [
            *(name for name in guards if test_cases.named_in(name, text)),
            *(title for title in titles if test_cases.title_in(title, text)),
        ]

    return names


def _run_guard_suites(guard_suites, result, workspace, base, out, kept, dependencies, patch, inputs, timeout):
    """Run each guard suite (autocode_companion_suites.guard_suites) on the original code and with the change.

    Returns, for check_cases, what each shows: its tests that passed in both runs, its failures with the
    change, whether both runs were complete, and where and how it ran. The receipts join the proof's checks.
    """
    others: list = []
    if not guard_suites or not kept or not kept[-1].is_dir():
        return others
    for found in guard_suites:
        project, framework, command = found["project"], found["framework"], found["command"]
        at = companion_suites.label(project)
        evidence = out / "companion-guards" / (project.replace("/", "-") or "root")
        on_base = verify.baseline(
            workspace,
            base,
            evidence,
            framework=framework,
            suite_command=command,
            dependencies_from=dependencies["dependencies_from"],
            timeout=timeout,
            base_patch=patch,
            ignored_inputs=inputs,
        )["receipt"]
        on_change = verify.run_suite(framework, command, kept[-1], evidence, "guards-on-candidate", timeout=timeout)
        result["checks"][f"companion_guards_on_base:{at}"] = on_base
        result["checks"][f"companion_guards_on_candidate:{at}"] = on_change
        others.append(
            {
                **companion_suites.guard_results(on_base, on_change),
                "framework": framework.name,
                "where": companion_suites.where(project),
                "command": command,
                "guards": found["guards"],
                "ids_root": package_scope.ids_root(workspace, project, command),
            }
        )
    return others


def _proving(
    state: RunState,
    workspace,
    run_dir,
    current,
    scope,
    progress,
    framework,
    execution_context,
    inputs,
    kept,
    all_due,
    *,
    companions=((), ()),
    guard_suites=(),
    test_suites=(),
):
    started_at, started = util.now(), time.monotonic()
    base = base_commit(state, workspace)
    adopted = execution_context.get("plan_command")
    options = _with_plan_command(settings(state), adopted)
    base_patch = reviewed_patch(state, workspace)
    operator = operator_patch.pinned(state)
    refused: str | dict = ""
    proving = framework  # the framework the proof runs: the detected one, or its focused fallback below
    if base and operator:
        if base_patch:
            refused = (
                "Both a reviewed change and an operator base patch would change the original code; "
                "the proof applies only one, so it cannot compare the fix with either"
            )
        else:
            base_patch, problem = operator_patch.check(operator, workspace, base)
            refused = problem and f"The original code cannot be prepared: {problem}"
    unappliable = (
        base
        and base_patch
        and not refused
        and ("it is missing" if not base_patch.is_file() else verify.patch_applies(workspace, base, base_patch))
    )
    # Trees that cannot hold the ignored files the run started with prove nothing either way.
    withheld = list(inputs.unverified) if inputs else []
    if not base or unappliable or refused or withheld:
        why = refused or (
            "No base commit is recorded for this run, so the fix cannot be compared with the original code"
            if not base
            else f"The reviewed change {base_patch.name} cannot be applied to the base revision "
            f"({unappliable}), so the fix cannot be compared with the change the review judged"
            if unappliable
            else None
        )
        proof = {
            "verdict": verify.UNVERIFIED,
            "failures": [],
            "notes": list(inputs.notes) if inputs else [],
            "review_reasons": [],
            "unverified": ([why] if why else []) + withheld,
            "fail_to_pass": None,
            "commands": {},
            "base": base,
            "base_patch": str(base_patch) if base_patch and not refused else None,
            "source_revision": current,
            "test_files": [],
            "source_files": [],
        }
        path = None
    else:
        dependencies = proof_dependencies(state, workspace)
        suite = options.get("test_command") or (framework.suite if framework else None)
        changes = verify.changed_files(workspace, base, source_paths=source_scope.paths(state))
        roles = role_inventory.from_git(workspace, base, verify.is_test_path, changes=changes)
        # A detected whole suite that cannot finish on the original code falls back to the tests nearest
        # the change, chosen here and recorded, never asked of a person; an explicit command is kept.
        focus = (
            focused_suite.choose_from_git(framework, workspace, base, changes, roles)
            if suite and not options.get("test_command")
            else None
        )
        base_suite = (
            _baseline(
                state,
                workspace,
                run_dir,
                base,
                framework,
                suite,
                dependencies["dependencies_from"],
                base_patch,
                progress,
                inputs,
                settled_timeout=not options.get("test_command"),
            )
            if suite
            else None
        )
        proving, fallback = framework, None
        if focus and focused_suite.timed_out(base_suite):
            fallback = focused_suite.record(focus, base_suite, suite_timeout(state))
            proving, suite = focused_suite.framework_for(framework, focus["command"]), focus["command"]
            base_suite = _baseline(
                state,
                workspace,
                run_dir,
                base,
                proving,
                suite,
                dependencies["dependencies_from"],
                base_patch,
                progress,
                inputs,
                settled_timeout=True,
            )
        number = len(state.get("regression_proofs", [])) + 1
        # The physical canonical path: measurement pins reads through this root without symlink
        # traversal, and a run directory reached through one (/tmp or /var on macOS) would fail
        # its own authenticity check (autocode_changed_code_measurement.pinned).
        out = Path(run_dir).resolve() / "regression" / f"proof-{number:02d}-{uuid.uuid4().hex}"
        progress("Comparing regression tests and checking the full candidate suite", command=suite, output=out)
        coverage = preserve_only(state, all_due=all_due)
        if coverage and goals.task_kind(state) == "bugfix":
            # Member preservation coverage authorizes tests alone. A product
            # change still needs the ordinary bugfix fail-before/pass-after proof.
            coverage = not any(roles.role(path) != role_policy.TEST for path in changes)
        due = cases(state, all_due=all_due)
        # Guards that are commands, where the runner names no test (autocode_command_guards). With no named
        # case and no changed test file, the change is coverage of what those commands already show.
        original_tests = [path for path in role_inventory.entries(workspace, base) if verify.is_test_path(path)]
        guard_commands = command_guards.usable(command_guards.due(state, all_due=all_due), proving, original_tests)
        if (
            guard_commands
            and not due
            and goals.task_kind(state) != "bugfix"
            and not any(roles.role(path) == role_policy.TEST for path in changes)
        ):
            coverage = True
        # A non-bugfix contract whose due cases are all test criteria may be a coverage
        # or characterization build: the product already implements the behavior and the
        # approved diff is tests alone. verify() decides that from the diff (#510); its
        # cases are then judged like guards, so a test that does not pass on the
        # original code still blocks the proof as mis-tagged.
        test_only = goals.task_kind(state) != "bugfix" and bool(due)
        regression_command = options.get("regression_command")
        if test_only and len(((state.get("goal_contract") or {}).get("body") or {}).get("milestones") or []) > 1:
            regression_command = selection.widen(
                regression_command,
                {path: change for path, change in changes.items() if roles.role(path) == role_policy.TEST},
                framework=framework.name if framework else None,
                test_root=options.get("test_root"),
            )
        if coverage and not regression_command:
            regression_command = options.get("test_command")
        guards = test_cases.guard_names(due, framework.name if framework else None)
        ahead = [] if all_due else pending_tests.withheld(state, workspace, due)
        kept.append(out / "scratch" / "candidate")
        result = verify.verify(
            workspace,
            base,
            out,
            framework=proving,
            roles=roles,
            suite_command=options.get("test_command"),
            regression_command=regression_command,
            reported=None,
            base_suite=base_suite,
            **dependencies,
            timeout=suite_timeout(state),
            new_behavior=goals.task_kind(state) != "bugfix",
            preserve_only=coverage,
            test_only_allowed=test_only,
            suite_repair=fallback is None
            and first_suite.exit_repair_suite(proving, suite, (adopted or {}).get("suite")),
            base_patch=base_patch,
            source_paths=source_scope.paths(state),
            ignored_inputs=inputs,
            guards=guards,
            cases=due,
            candidate_tree=kept[-1],
            package=package_scope.of(state.get("settings")),
            package_exempt=package_writes.exempt(state, workspace, base),
            withheld=ahead,
            replaced=superseded_tests.replaced(state),
            **rooted(options),
        )
        path = out / "verification.json"
        proof = {key: result.get(key) for key in SUMMARY_KEYS}
        if inputs:
            proof["notes"] = [*(proof.get("notes") or []), *inputs.notes]
        if ahead:
            proof["notes"] = [*(proof.get("notes") or []), pending_tests.NOTE + ", ".join(ahead)]
        if operator:
            proof["review_reasons"] = [*(proof.get("review_reasons") or []), operator_patch.review_reason(operator)]
        _protect_companions(
            proof,
            result,
            companions,
            workspace,
            base,
            out,
            kept,
            dependencies,
            base_patch,
            inputs,
            suite_timeout(state),
        )
        others = _run_guard_suites(
            guard_suites, result, workspace, base, out, kept, dependencies, base_patch, inputs, suite_timeout(state)
        )
        # The change's tests in another project, proven with that project's own suite as the proof's are.
        others += companion_tests.prove(
            test_suites,
            proof,
            result,
            workspace=workspace,
            base=base,
            out=out,
            baseline={
                "dependencies_from": dependencies["dependencies_from"],
                "base_patch": base_patch,
                "ignored_inputs": inputs,
            },
            arguments={
                **dependencies,
                "roles": roles,
                "new_behavior": goals.task_kind(state) != "bugfix",
                "preserve_only": coverage,
                "test_only_allowed": test_only,
                "base_patch": base_patch,
                "source_paths": source_scope.paths(state),
                "ignored_inputs": inputs,
                "cases": due,
                "withheld": ahead,
                "replaced": superseded_tests.replaced(state),
            },
            timeout=suite_timeout(state),
        )
        _run_guard_commands(
            proof,
            result,
            guard_commands,
            proving,
            workspace,
            base,
            out,
            kept,
            dependencies,
            base_patch,
            inputs,
            suite_timeout(state),
        )
        if adopted:
            chosen = plan_command.note(adopted)
            proof["notes"] = [*(proof.get("notes") or []), chosen]
            proof["review_reasons"] = [*(proof.get("review_reasons") or []), chosen]
        if fallback:
            proof["suite_fallback"] = fallback
            proof["notes"] = [*(proof.get("notes") or []), fallback["reason"]]
            proof["review_reasons"] = [*(proof.get("review_reasons") or []), focused_suite.review_reason(fallback)]
        if (coverage or (test_only and not result.get("source_files"))) and any(
            case.get("kind") != "preserve" for case in due
        ):
            due = [{**case, "kind": "preserve"} for case in due]
        # Code only: a changed README or note that mentions a test is not a test.
        changed = [
            path
            for path, status in (result.get("changes") or {}).items()
            if status != "deleted" and verify.is_code_path(path)
        ]
        suite = untouched(
            result.get("suite_pass_to_pass") or [],
            [Path(workspace) / path for path in changed]
            + [Path(dependencies["dependencies_from"]) / path for path in result.get("ignored_test_files") or []],
        )
        named = [test for key in ("fail_to_pass", "pass_to_pass", "not_run_on_base") for test in proof.get(key) or []]
        # A node:test id names its file relative to where its command ran: a package's own commands run there.
        package, commands = package_scope.of(state.get("settings")), proof.get("commands") or {}
        targeted = commands.get("regression") or commands.get("suite")
        refused = (
            {
                **wrapped_runner.refusals(package_scope.ids_root(workspace, package, targeted), named),
                **wrapped_runner.refusals(package_scope.ids_root(workspace, package, commands.get("suite")), suite),
            }
            if due
            else {}
        )
        changed_paths = [Path(workspace) / path for path in changed]
        for other in others:
            # As the proof's own suite: a test a changed code file names is not the one that ran on the base.
            other["passing"] = untouched(other["passing"], changed_paths)
            # A companion proof's own tests ran on the original code with the change's test files.
            ran = [*other["passing"], *other.get("proved", []), *other.get("fail_to_pass", [])]
            refused.update(wrapped_runner.refusals(other.pop("ids_root"), ran) if due else {})
        turn_baseline = execution_context.get("follow_up_baseline")
        follow_up_ids = {case["id"] for case in turn_baseline["cases"]} if turn_baseline else set()
        check_cases(
            proof,
            [case for case in due if case["id"] not in follow_up_ids],
            refused,
            suite_passing=suite,
            failing=result.get("failed_on_candidate") or [],
            ignored=result.get("ignored_test_files") or [],
            by_id=bool(result.get("no_test_changed")),
            incomplete=bool(result.get("guard_run_incomplete")),
            others=others,
        )
        if turn_baseline:
            receipt = follow_up_proof.collect(
                turn_baseline,
                workspace,
                out,
                result,
                proving,
                verify=verify,
                dependencies=dependencies["dependencies_from"],
                inputs=inputs,
                timeout=suite_timeout(state),
            )
            follow_up_proof.apply(proof, turn_baseline, receipt, result, refused=refused)
            if receipt.get("command"):
                result["checks"]["follow_up_baseline"] = receipt
        proof["checks"] = {
            label: {
                "command": receipt["command"],
                "exit_code": receipt["exit_code"],
                "timed_out": receipt["timed_out"],
                "output": receipt["output"],
            }
            for label, receipt in result["checks"].items()
        }
    after = (
        verify.execution_identity(
            workspace,
            command=options.get("test_command") or (framework.suite if framework else None),
            dependencies_from=state.get("project_workspace"),
            source_paths=source_scope.paths(state),
            ignored_inputs=launch_inputs.supply(state, workspace, run_dir) if inputs else None,
        )
        if path
        else execution_context["identity"]
    )
    if after != execution_context["identity"]:
        before = execution_context["identity"]
        changed = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
        proof["verdict"] = verify.UNVERIFIED
        proof.setdefault("unverified", []).append(
            "Execution context changed while proving the candidate: " + ", ".join(changed)
        )
    if execution_context.get("follow_up_baseline") != follow_up_proof.context(
        state, workspace, run_dir, cases(state, all_due=all_due)
    ):
        proof["verdict"] = verify.UNVERIFIED
        proof.setdefault("unverified", []).append("Follow-up baseline changed while proving the candidate")
    # Report only (#699): mutants run only once the verdict is final and a pass, so a proof that fails its
    # cases or its context check spends nothing on them; every other proof carries the not-passed note.
    proof["mutation_check"] = (
        changed_code.collect(
            proof,
            result,
            proving,
            workspace,
            out,
            kept[-1],
            execution_context,
            source_paths=source_scope.paths(state),
        )
        if path and kept and kept[-1].is_dir()
        else mutation_check.final(None, proof["verdict"], proof.get("case_tests"))
    )
    if path:
        proof["checks"] = {
            label: {key: receipt.get(key) for key in ("command", "exit_code", "timed_out", "output")}
            for label, receipt in result["checks"].items()
        }
    proof.update(
        case_scope=scope,
        path=str(path) if path else None,
        proved_at=util.now(),
        duration_seconds=round(time.monotonic() - started, 1),
    )
    if path:
        # A suite-level PASS may still miss a required case. Persist the final
        # decision and coverage while retaining the full executed check receipts.
        result.update({key: proof[key] for key in SUMMARY_KEYS if key in proof}, case_scope=scope)
        util.atomic_json(path, result)
    state["regression_proof"] = proof
    state.setdefault("regression_proofs", []).append(
        {
            **{k: proof.get(k) for k in ("verdict", "source_revision", "path", "proved_at", "duration_seconds")},
            "proof_context": util.digest(execution_context),
        }
    )
    # A visible, runner-owned step in the stage history: no provider, no tokens.
    record = {
        "stage": STAGE,
        "role": "runner",
        "iteration": state.get("iteration"),
        "started_at": started_at,
        "finished_at": util.now(),
        "runner_owned": True,
        "engine": "runner",
        "duration_seconds": proof["duration_seconds"],
        "metrics": {
            "provider_tokens": {
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_output_tokens": 0,
            }
        },
        "summary": f"Regression proof {proof['verdict']}",
        "output": proof["path"],
    }
    state.setdefault("stages", []).append(record)
    state.setdefault("history", []).append(record)
    print(
        f"{STAGE}: {proof['verdict']}" + "".join(f"\n  - {r}" for r in proof["failures"] + proof["unverified"]),
        flush=True,
    )
    return proof


def rejection(state: RunState, *, all_due=True):
    """The completion gate's reason when the current source has no passing proof."""
    proof = state.get("regression_proof") or {}
    missing = [
        case["id"]
        for case in test_cases.proof_cases(state, all_due=all_due)
        if case["id"] not in (proof.get("case_scope") or []) or not (proof.get("case_tests") or {}).get(case["id"])
    ]
    if proof.get("verdict") == verify.PASS and missing:
        return MISSING_CASES_REASON + ", ".join(missing)
    if proof.get("verdict") == verify.PASS and not (proof.get("failures") or proof.get("unverified")):
        # Nothing the runner measured failed and every due case is covered, so what the gate lacks is
        # the independent evidence bound to this measurement — not a regression proof for this source.
        return CHANGED_CODE_REASON
    reasons = (
        "; ".join((proof.get("failures") or []) + (proof.get("unverified") or []))
        or "no proof exists for the current source"
    )
    return f"Completion rejected: this change has no passing regression proof for the current source ({reasons})"


def untouched(test_ids, files):
    """The suite's tests whose own name no changed file, and no ignored test file, names.

    A changed test file's tests are the targeted run's to decide (a rewritten guard test that fails on base is
    mis-tagged); a changed product module may define a test (a mixin), whose old content ran on base; an
    ignored file's tests never existed in the base revision.
    """
    texts = []
    for path in files:
        try:
            texts.append(Path(path).read_text(errors="replace"))
        except OSError:
            continue
    text = "\n".join(texts)
    return [test for test in test_ids if not test_cases.named_in(test, text)]


def _variant(test):
    """The test a variant belongs to: its file or module and its own function, without parameters, a Go
    subtest, or the class that runs it (a mixin's test that two classes run is one test)."""
    test = re.sub(r"\[.*\]$", "", test)
    if "::" in test:
        container = test.split("::", 1)[0]
    else:
        parts = test.split(".")
        container = ".".join(parts[:-2] if len(parts) > 2 else parts[:-1])
    return container, test_cases.function_name(test)


def check_cases(
    proof, cases, refused=None, *, suite_passing=(), failing=(), ignored=(), by_id=False, incomplete=False, others=()
):
    """Each English test case needs a test named after it.

    A restore case (the default, and what a case without a kind means) needs a
    test that failed on the original code and passes with the fix. A preserve
    case describes behavior that already worked and must keep working: its test
    must pass on the original code and with the fix. A preserve case whose test
    fails on the original code is mis-tagged: it describes restored behavior.

    ``refused`` maps a test id to why it cannot prove a case: it is in a node:test
    file that runs another test runner, so it passes on that runner's exit code
    (autocode_wrapped_runner.refusals, #380). Such a test is never matched to a
    case, and a case left without a test says why.

    ``suite_passing`` is the whole suite's tests that passed on base and on the
    candidate, in files the change leaves alone (untouched): a preserve case that
    names its test exactly (a plan's guard: test_x) may be proven by one, as a
    final check proves what earlier milestones delivered. A case
    matched by its id alone (a diagnosis's T4) never is: another fix's test_t4_...
    is not its test. ``failing`` is the suite's failures on the candidate: a failing
    variant (another parameter set, a Go subtest, another class running the same
    test) of a test matched to a preserve case breaks it, even when that variant
    failed on base too. ``ignored`` names the git-ignored test files the proof
    copied into its trees; with any, the whole suite is not consulted, and a
    preserve case left without a test says so. ``by_id``: no test changed, so
    there is no targeted run and every preserve case is matched against the
    suite, by its id too (a plan case, never a diagnosis's: such a run is never a
    bug fix). ``incomplete``: the suite run on the original code with the
    change's test files did not finish, so a preserve case that names its test
    and is left without one is unproven rather than refuted.

    ``others`` are the guard suites of other projects (autocode_companion_suites.guard_suites, as
    _run_guard_suites reports them): a preserve case that names its test exactly and has none in the proof's
    own suite may rest on one that passed in such a suite on the original code and with the change, and a
    failing variant there breaks it as in the proof's suite. A case whose name such a suite looked for in a
    run that did not finish is unproven rather than refuted.
    """
    if not cases:
        return
    if proof.get("fail_to_pass") is None:
        # No fail-to-pass list: either the proof already failed for another reason, or the
        # runner reports exit codes only, which cannot tell which test proves which case.
        proof["case_tests"] = {case["id"]: [] for case in cases}
        if proof["verdict"] == verify.PASS:
            proof["unverified"] = list(proof.get("unverified") or []) + [
                "The English test cases could not be matched to tests: the test run reported no per-test results. "
                "Use a supported named-test runner (unittest/pytest, Go, node:test via node --test, RSpec or cargo test). "
                "Printed PASS labels and package-script summaries are not named proof; keep the existing "
                "assertions and suite, and register each approved case with the supported runner."
            ]
            proof["verdict"] = verify.UNVERIFIED
        return
    refused = refused or {}
    declared = {case["id"]: case for case in cases}
    framework = proof.get("framework") or {}
    framework = framework.get("name") if isinstance(framework, dict) else framework
    # A case that declares an existing test's title (node:test, Vitest) is bound to it here, from every
    # test the proof saw, so the suite and the failing tests are then searched for it by that name.
    seen = [
        *(proof.get("fail_to_pass") or []),
        *(proof.get("pass_to_pass") or []),
        *(proof.get("not_run_on_base") or []),
        *suite_passing,
        *failing,
        *refused,
    ]
    cases = test_cases.titled(test_cases.bound(cases, framework), framework, seen)

    def usable(tests):
        return [test for test in tests or [] if test not in refused]

    def refusal(label, case):
        reasons = sorted(
            {refused[test] for test in test_cases.match_cases([case], sorted(refused), framework=framework)[case["id"]]}
        )
        return (
            (
                f"{label} {test_cases.case_text(case)} has a test named after it that cannot prove it: "
                + "; ".join(reasons)
            )
            if reasons
            else ""
        )

    restore = [case for case in cases if case.get("kind", "restore") == "restore"]
    preserve = [case for case in cases if case.get("kind") == "preserve"]
    proof["case_tests"] = test_cases.match_cases(restore, usable(proof["fail_to_pass"]), framework=framework)
    proof["case_tests"].update(test_cases.match_cases(preserve, usable(proof.get("pass_to_pass")), framework=framework))
    for other in others:
        # A criterion's test in another project: that project's suite showed it fail on the original code
        # and pass with the change (autocode_companion_tests), read as that suite names its tests.
        for case in restore:
            if proof["case_tests"][case["id"]] or not other.get("fail_to_pass"):
                continue
            found = test_cases.match_cases(
                [declared[case["id"]]], usable(other["fail_to_pass"]), framework=other["framework"]
            )[case["id"]]
            if found:
                proof["case_tests"][case["id"]] = found
                proof["notes"] = list(proof.get("notes") or []) + [
                    f"Test case {case['id']}'s test {', '.join(found)} did not pass on the original code and passes "
                    f"with the change in the suite of {other['where']} (`{other['command']}`), which the proof's "
                    "suite cannot run"
                ]
    suite_ids = usable(suite_passing)
    for case in preserve:
        if (case.get("test_name") or by_id) and not proof["case_tests"][case["id"]]:
            proof["case_tests"][case["id"]] = test_cases.match_cases([case], suite_ids, framework=framework)[case["id"]]
    for other in others:
        for case in preserve:
            if proof["case_tests"][case["id"]]:
                continue
            # As that suite names its tests: a guard may name another project's test by its title, which the
            # proof's own runner (unittest beside a Node package) does not read as a name.
            own, runner = declared[case["id"]], other["framework"]
            # The other project's changed tests ran on the original code and with the change, as the proof's did.
            found = test_cases.match_cases([own], usable(other.get("proved")), framework=runner)[case["id"]]
            if not found:
                # The rest of its suite: only a test the guard names exactly.
                passing = usable(other["passing"])
                named = test_cases.titled(test_cases.bound([own], runner), runner, passing)[0]
                if named.get("test_name"):
                    found = test_cases.match_cases([named], passing, framework=runner)[case["id"]]
            if found:
                proof["case_tests"][case["id"]] = found
                proof["notes"] = list(proof.get("notes") or []) + [
                    f"Preserve case {case['id']}'s test {', '.join(found)} passed on the original code and with the "
                    f"change in the suite of {other['where']} (`{other['command']}`), which the proof's suite cannot run"
                ]
    failing = [*failing, *(test for other in others for test in other["failing"])]
    incomplete_elsewhere = {name for other in others if not other["complete"] for name in other["guards"]}
    failures = []
    missing = [case for case in restore if not proof["case_tests"][case["id"]]]
    failures += [
        refusal("Test case", case)
        or f"Test case {test_cases.case_text(case)} has no test named "
        f"{test_cases.case_test_name(case['id'], case.get('test_name'))} "
        "that passes with the change and did not pass without it"
        for case in missing
    ]
    # A preserve case (a plan's guard:) whose test could not even import on the original code is not
    # shown to fail there: it counts, with a note that its before-state is unproven.
    unrun = usable(proof.get("not_run_on_base"))
    for case in preserve:
        found = (
            []
            if proof["case_tests"][case["id"]]
            else test_cases.match_cases([case], unrun, framework=framework)[case["id"]]
        )
        if found:
            proof["case_tests"][case["id"]] = found
            proof["notes"] = list(proof.get("notes") or []) + [
                f"Preserve case {case['id']}'s test {', '.join(found)} passes with the change but could not run "
                "on the original code (its test file imports code the change adds), so it is not shown to "
                "have passed before"
            ]
    mistagged = [
        case
        for case in preserve
        if set(test_cases.match_cases([case], usable(proof["fail_to_pass"]), framework=framework)[case["id"]])
        - set(unrun)
    ]
    # One failing variant of a test matched to a guard breaks it, though the suite comparison only notes a
    # failure that was there before too; a failing test that merely shares the guard's name does not. A guard
    # matched by its id alone (naming several tests) also breaks on a failing one beside a test it matched.
    broken = {}
    for case in preserve:
        found = proof["case_tests"][case["id"]]
        variants, files = {_variant(test) for test in found}, {_variant(test)[0] for test in found}
        by_name = (
            test_cases.match_cases([case], failing, framework=framework)[case["id"]]
            if not case.get("test_name")
            else []
        )
        broken[case["id"]] = [
            test for test in failing if _variant(test) in variants or (test in by_name and _variant(test)[0] in files)
        ]
    for case in preserve:
        if broken[case["id"]]:
            proof["case_tests"][case["id"]] = []
    failures += [
        f"Preserve case {test_cases.case_text(case)} has a test named after it that fails: "
        + ", ".join(broken[case["id"]][:5])
        for case in preserve
        if broken[case["id"]]
    ]
    untested = [
        case
        for case in preserve
        if not proof["case_tests"][case["id"]] and case not in mistagged and not broken[case["id"]]
    ]
    limited = (
        (
            " (the whole suite was not consulted: ignored test files are copied into the proof trees: "
            + ", ".join(list(ignored)[:5])
            + ")"
        )
        if ignored
        else ""
    )
    elsewhere = {
        case["id"]: name
        for case in untested
        if (name := case.get("test_name") or declared[case["id"]].get("title")) in incomplete_elsewhere
    }
    unproven = [
        case
        for case in untested
        if ((incomplete and case.get("test_name")) or case["id"] in elsewhere) and not refusal("Preserve case", case)
    ]
    failures += [
        refusal("Preserve case", case)
        or f"Preserve case {test_cases.case_text(case)} has no test named "
        f"{test_cases.case_test_name(case['id'], case.get('test_name'))} "
        "that passes both with the change and on the original code"
        + (limited if case.get("test_name") else "")
        for case in untested
        if case not in unproven
    ]
    if unproven:
        proof["unverified"] = list(proof.get("unverified") or []) + [
            f"Preserve case {test_cases.case_text(case)}: "
            + (
                "the other project's suite that runs its test timed out or did not report every test"
                if case["id"] in elsewhere
                else "the suite run on the original code with the change's test files timed out or did not "
                "report every test"
            )
            + f", so its test {elsewhere.get(case['id']) or case['test_name']} is not shown to pass before and "
            "after the change"
            for case in unproven
        ]
        if proof["verdict"] == verify.PASS:
            proof["verdict"] = verify.UNVERIFIED
    failures += [
        f"Preserve case {test_cases.case_text(case)} has a test that fails on the original code, so it "
        "describes behavior the fix restores: tag it restore, or rewrite the test to assert the behavior "
        "that already worked"
        for case in mistagged
    ]
    if failures:
        proof["failures"] = list(proof.get("failures") or []) + failures
        proof["verdict"] = verify.FAIL


def record_unmeasured(state: RunState, *, owed=None):
    """Record, in the run's evidence, that changed product code went unmeasured and why (#2474).

    ``owed`` is ``changed_code_trigger.owes(state)`` when the caller already has it."""
    if owed is None:
        owed = changed_code_trigger.owes(state)
    reason = changed_code_trigger.unmeasurable(state) if owed else None
    if reason is None:
        state.pop("changed_code_fallback", None)
    elif (state.get("changed_code_fallback") or {}).get("reason") != reason:
        state["changed_code_fallback"] = {"version": 1, "decision": "unmeasured", "reason": reason}


def before_review(state: RunState, stage, workspace, run_dir):
    """Refresh proof before independent validation or completion review in either dispatch path.

    A package run's candidate is first checked for changes outside its package, whether or not a proof
    is owed: they are a runner finding (autocode_package_writes)."""
    schedule.guard(Path(run_dir) / "check-replay" / "obligations")
    runner_check.clear(state, run_dir, status.persist)
    if stage in package_writes.STAGES:
        package_writes.check(state, workspace)
        if acceptance.is_blind(state):
            acceptance.check_candidate(state, workspace, run_dir)
    # A planning stage only reads, and before one the comparison below would only refresh
    # changed_code_fallback, which is evidence recomputed before every review. On a large repository it
    # costs several seconds per call, twice before a chat's first reply, so it waits for a stage that uses it.
    if _planning(stage if stage is not None else state.get("next_stage")):
        return
    # Due-now scope even for review_completion: it also reviews each milestone before advancing, where a later
    # milestone's unbuilt tests would fail the proof and the owner note would demand REWORK. Final
    # completion still needs every case, enforced by complete(all_due=True).
    # One comparison of the candidate with the run's base serves both: it stages the whole working
    # tree in a scratch index (about nine Git commands), and before_review runs before every stage.
    owed = changed_code_trigger.owes(state)
    record_unmeasured(state, owed=owed)
    if not required(state, owed=owed):
        return
    if (
        stage in ("tester", "review_checkpoint")
        or stage == "review_completion"
        and _review_needs_proof(state, workspace)
    ):
        prove(state, workspace, run_dir)


def _planning(stage) -> bool:
    """Whether ``stage`` (or its report repair) plans and builds nothing: Requirements, the Planner, the Plan
    Reviewer and the task Planner (``plan_task``, a new run's first stage), all of them read-only."""
    return roles.base_stage(stage) in (*roles.ALL_PLANNING_STAGES, "plan_task")


def _review_needs_proof(state: RunState, workspace):
    """The Completion Owner reuses a proof of this source, except a failure proved only once.

    A failing replay demands REWORK. Replay it once more within the same proof context,
    because a timing flake can otherwise hold a finished run. Reapproved contracts and
    changed case bindings require fresh execution even when candidate source is unchanged.
    The bound review of an archived Builder timeout first inspects its partial work;
    the Tester/checkpoint and the ordinary completion gate still require fresh proof.
    """
    current = source_scope.snapshot(workspace, state)["revision"]
    if recovery_review.builder_timeout_inspection(state, current):
        return False
    saved = state.get("regression_proof") or {}
    context = saved.get("execution_context") or {}
    if (
        not role_policy.proof_current(
            saved, ((state.get("settings") or {}).get("protected_tests") or {}).get("binding_hash")
        )
        or saved.get("source_revision") != current
        or context.get("contract_identity") != util.digest(state.get("goal_contract"))
        or context.get("cases_identity") != util.digest(cases(state))
        or bool(context.get("preserve_only")) != preserve_only(state)
    ):
        return True
    return saved.get("verdict") == verify.FAIL and (
        sum(
            1
            for row in state.get("regression_proofs") or []
            if isinstance(row, dict)
            and row.get("source_revision") == current
            and row.get("proof_context") == util.digest(context)
        )
        < 2
    )


def complete(state: RunState, current_revision, *, all_due=False, require_changed_code=True, include_changed_code=True):
    """Authenticate a passing proof for this source and its required cases.

    Stage containment uses the current checkpoint; final completion sets all_due.
    Skipping the independent line assessment still requires runner proof of a
    product change. Only the earlier Builder self-check may omit that obligation.
    """
    if (state.get("settings") or {}).get("replay_recording", {}).get("checks") == "recorded":
        # Recorded simulation never grants current proof; the runner's own archived proof is
        # authenticated by autocode_replay_checks for the gates that ask only for it (#2500).
        if require_changed_code or not required(state, all_due=all_due, include_changed_code=include_changed_code):
            return not require_changed_code
        try:
            from . import autocode_replay_checks as recorded_checks
        except ImportError:
            import autocode_replay_checks as recorded_checks
        return recorded_checks.proof_retained(
            state, current_revision, [case["id"] for case in test_cases.proof_cases(state, all_due=all_due)]
        )
    if not required(state, all_due=all_due, include_changed_code=include_changed_code):
        return True
    proof = state.get("regression_proof") or {}
    if (
        proof.get("verdict") != verify.PASS
        or proof.get("source_revision") != current_revision
        or not role_policy.proof_current(
            proof, ((state.get("settings") or {}).get("protected_tests") or {}).get("binding_hash")
        )
    ):
        return False
    try:
        result = util.read(proof["path"])
        if not isinstance(result, dict):
            return False
        names = result.get("case_tests") or {}
        if not isinstance(names, dict):
            return False
        covered = result.get("case_scope", list(names))
        if not isinstance(covered, list):
            return False
        wanted = {case["id"] for case in test_cases.proof_cases(state, all_due=all_due)}
        return (
            util.file_hash(proof["path"]) == proof.get("receipt_sha256")
            and result.get("verdict") == verify.PASS
            and bool(result.get("checks"))
            and wanted <= set(covered)
            and all(names.get(case_id) for case_id in wanted)
            and schedule.proof_intact(result["checks"], root=Path(proof["path"]).parent)
            and (not require_changed_code or changed_code.ready(proof, result, state.get("validation")))
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False


def handoff(state: RunState):
    """What the Validator and Completion Owner see: the runner's executed result, not a claim."""
    proof = state.get("regression_proof")
    if not required(state) or not proof:
        return None
    return {
        **{k: proof.get(k) for k in SUMMARY_KEYS},
        # Only the changed lines' coverage: a proof saved before #3121 holds every measured file.
        "diff_coverage": diff_coverage.bounded(proof.get("diff_coverage")),
        "checks": proof.get("checks", {}),
        "path": proof.get("path"),
        "meaning": "Executed by the runner in clean worktrees against this run's base commit: the new or "
        "changed tests must fail on the original code and pass on the current code, and no test "
        "that passed on base may fail. PASS is required before a bug fix can complete.",
    }

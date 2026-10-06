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
base and on the candidate (verify's ``preserve_only``). A saved permission
answer that grants a test-only regression-proof exception for the current
contract is the same coverage proof, so a plan that marked that coverage
``test:`` can still finish. With several milestones, a checkpoint proves the
criteria of its own milestone and of those already accepted
(autocode_test_cases.in_scope); a milestone with none due runs no proof.
"""
from __future__ import annotations

try:
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_source_scope as source_scope


import subprocess
import time
from pathlib import Path
import uuid

try:
    from . import autocode_util as util, autocode_goals as goals, autocode_verify as verify
    from . import autocode_base_patch as operator_patch
    from . import autocode_workspaces as workspaces
    from . import autocode_test_cases as test_cases
    from . import autocode_follow_up as follow_up
    from . import autocode_runner_check as runner_check, autocode_status as status
    from . import autocode_verification_schedule as schedule
    from . import autocode_wrapped_runner as wrapped_runner
except ImportError:
    import autocode_follow_up as follow_up
    import autocode_test_cases as test_cases
    import autocode_util as util
    import autocode_goals as goals
    import autocode_verify as verify
    import autocode_base_patch as operator_patch
    import autocode_workspaces as workspaces
    import autocode_runner_check as runner_check
    import autocode_status as status
    import autocode_verification_schedule as schedule
    import autocode_wrapped_runner as wrapped_runner

STAGE = "regression_proof"
SUMMARY_KEYS = ("framework", "verdict", "failures", "unverified", "notes", "review_reasons", "fail_to_pass", "pass_to_pass",
                "not_run_on_base",
                "commands", "base", "base_patch", "source_revision", "test_files", "source_files", "case_tests")


# What the judging stages' prompts say about the proof (autocode_stage_context): the reviewers use it
# instead of re-running the same tests, and never override it.
PROMPT_NOTES = {
    "passed": {
        "validator": "\nREGRESSION PROOF: regression_proof records that the runner already ran the new or changed "
                     "tests (failing on the original code, passing now) and the project suite; case_tests names the test for each English test case (the diagnosis's, or the plan's criteria marked test:): read each one and report FAIL if it does not assert exactly the case's given, when and then. Do not re-run the "
                     "whole suite. Run the regression command once as your own executed check, then spend your "
                     "effort on what those tests do not cover in the acceptance criteria. To close a finding the proof settles, cite regression_proof's verdict and source_revision as the evidence.\n",
        "owner": "\nREGRESSION PROOF: regression_proof and the Validator's report are executed evidence for this "
                 "exact source. Do not re-run tests to re-establish them; decide from the recorded evidence.\n"},
    "open": {
        "validator": "\nREGRESSION PROOF: regression_proof is not PASS for this source. The fix is not proven; "
                     "report FAIL and cite its failures or unverified reasons as findings.\n",
        "owner": "\nREGRESSION PROOF: regression_proof is not PASS, so the runner will refuse completion. Do not "
                 "request COMPLETE; return REWORK whose findings are the proof's failures or unverified reasons.\n"},
}


def required(state):
    return goals.task_kind(state) == "bugfix" or bool(test_cases.contract_cases(state))


def _test_only_exception(state):
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


def preserve_only(state):
    """Coverage of existing behavior: every due case is a guard, or the user granted that exception.

    A contract of plain ``test:`` cases can still be a coverage build when its diff turns out
    to be tests alone; that diff-dependent rule is applied where the diff is known (_prove,
    passing verify's ``test_only_allowed``, #510).
    """
    if goals.task_kind(state) == "bugfix":
        return False
    due = cases(state)
    if not due:
        return False
    return all(case.get("kind") == "preserve" for case in due) or _test_only_exception(state)


def cases(state):
    """The English cases this proof must cover: the bug's diagnosis, else the plan's test criteria."""
    return test_cases.proof_cases(state)


def base_commit(state, workspace):
    """The revision the run started from: saved at run creation, else the task worktree's base,
    else the HEAD its first stage recorded (runs created before base_commit was saved)."""
    if state.get("base_commit"):
        return state["base_commit"]
    try:
        saved = (workspaces.metadata(workspace) or {}).get("base_commit")
    except ValueError:
        saved = None
    return saved or _first_recorded_head(state, workspace)


def _first_recorded_head(state, workspace):
    first = next((record["before_ref"] for record in state.get("stages") or [] if record.get("before_ref")), None)
    try:
        start = util.read(first).get("head") if first else None
    except (OSError, ValueError, AttributeError):
        return None
    # A commit the current source no longer descends from (a rebase, a reset) cannot be its base.
    if start and subprocess.run(["git", "-C", str(workspace), "merge-base", "--is-ancestor", start, "HEAD"],
                                capture_output=True).returncode == 0:
        return start
    return None


def head(workspace):
    result = subprocess.run(["git", "-C", str(workspace), "rev-parse", "--verify", "HEAD"],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def launch_base(workspace, run_dir):
    """The base_commit of a new in-place run: HEAD, or, when the checkout holds uncommitted or
    untracked files, a child commit of HEAD that holds them (verify.commit_worktree). The proof
    then runs them as original code instead of counting them as the change. A ref named after the
    run keeps that commit from Git's garbage collection."""
    commit = verify.commit_worktree(workspace)
    if commit and commit != head(workspace):
        subprocess.run(["git", "-C", str(workspace), "update-ref", f"refs/autocode/launch/{Path(run_dir).name}",
                        commit], check=True, capture_output=True)
    return commit


def settings(state):
    return state.get("settings", {}).get("regression") or {}


def suite_timeout(state):
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


def reviewed_patch(state, workspace):
    """The patch a review follow-up fixes (autocode_follow_up), or None.

    Its findings exist only with that change applied, so the proof's "original code" is the
    base with the patch applied: on the code before it, a test for a finding can pass (the
    behavior was fine before the change) and every test the change rewrote fails.
    """
    reviewed = follow_up.review_findings(state) or {}
    name = str(reviewed.get("change_patch") or "").strip()
    if not name:
        return None
    candidates = [Path(workspace) / name, *([Path(state["project_workspace"]) / name]
                                           if state.get("project_workspace") else [])]
    return next((path for path in candidates if path.is_file()), candidates[0])


def _baseline(state, workspace, run_dir, base, framework, suite, dependencies, base_patch=None, progress=None):
    cached = state.get("regression_baseline") or {}
    patch = str(base_patch) if base_patch else None
    binding = {"base": base, "command": suite,
               "framework": framework.to_dict() if framework else None,
               "base_patch": schedule.tree_identity(base_patch) if base_patch else None,
               "timeout": suite_timeout(state),
               "runtime": verify.baseline_identity(workspace, command=suite, dependencies_from=dependencies)}
    if cached.get("binding") == binding and binding["runtime"].get("reuse_supported"):
        try:
            result = util.read(cached["path"])
            if (util.file_hash(cached["path"]) == cached.get("sha256")
                    and schedule.intact(result["receipt"], root=Path(cached["path"]).parent)
                    and not result["receipt"].get("timed_out")
                    and (not result["receipt"].get("results_expected")
                         or schedule.complete_results(result["receipt"]))):
                return result
        except (OSError, ValueError, TypeError, KeyError):
            pass
    directory = Path(run_dir) / "regression" / ("baseline-" + uuid.uuid4().hex)
    if progress:
        progress("Testing the original code before comparing the change", command=suite,
                 output=directory / "baseline" / "suite-on-base.log")
    result = verify.baseline(workspace, base, directory, framework=framework,
                             suite_command=suite, dependencies_from=dependencies, timeout=suite_timeout(state),
                             base_patch=base_patch)
    path = directory / "baseline.json"
    util.atomic_json(path, result)
    state["regression_baseline"] = {"base": base, "command": suite, "path": str(path), "health": result["health"],
                                    "base_patch": patch, "binding": binding, "sha256": util.file_hash(path)}
    return result


def prove(state, workspace, run_dir):
    """Run (or reuse) the proof for the current source; return its summary. Launches no model."""
    workspace = Path(workspace)
    schedule.guard(Path(run_dir) / "check-replay" / "obligations")
    current = source_scope.snapshot(workspace, state)["revision"]
    saved = state.get("regression_proof") or {}
    scope = sorted(case["id"] for case in cases(state))
    options = settings(state)
    command = options.get("test_command")
    # A trusted explicit collector chooses the result parser and targeted tests.
    framework = verify.command_framework(command) if command else None
    python = ((framework.python if framework and framework.name in ("pytest", "unittest") else None)
              or options.get("python") or verify.python_for(state.get("project_workspace") or workspace))
    framework = framework or verify.detect_framework(workspace, python=python)
    base = base_commit(state, workspace)
    operator = operator_patch.pinned(state)
    # Stored only in regression_proof; prove reads it before reusing evidence.
    # A repaired test environment must invalidate a prior failure (or PASS)
    # even when the source and acceptance criteria have not changed.
    execution_context = {
        "python": python,
        "detected_framework": framework.to_dict() if framework else None,
        "test_command": options.get("test_command"),
        "regression_command": options.get("regression_command"),
        "timeout": suite_timeout(state),
        "identity": verify.execution_identity(workspace, command=options.get("test_command") or
                                               (framework.suite if framework else None),
                                               dependencies_from=state.get("project_workspace"), source_paths=source_scope.paths(state)) if base else
                    {"source_revision": current, "reuse_supported": False},
        "base": base,
        "base_patch": schedule.tree_identity(reviewed_patch(state, workspace)) if reviewed_patch(state, workspace) else None,
        "operator_base_patch": {"pin": operator, "file": schedule.tree_identity(operator["path"])} if operator else None,
        "contract_identity": util.digest(state.get("goal_contract")), "cases_identity": util.digest(cases(state)),
        "preserve_only": preserve_only(state),
    }
    if (saved.get("source_revision") == current and saved.get("case_scope", scope) == scope
            and saved.get("execution_context") == execution_context and saved.get("verdict") == verify.PASS
            and execution_context["identity"]["reuse_supported"]):
        try:
            result = util.read(saved["path"])
            if (util.file_hash(saved["path"]) == saved.get("receipt_sha256")
                    and all(schedule.intact(row, root=Path(saved["path"]).parent)
                            and (not row.get("results_expected") or schedule.complete_results(row))
                            for row in result["checks"].values())):
                return saved
        except (OSError, ValueError, TypeError, KeyError):
            pass
    with runner_check.track(state, run_dir, STAGE, "Preparing regression checks", status.persist) as progress:
        proof = _prove(state, workspace, run_dir, current, scope, progress, framework, execution_context)
        proof["execution_context"] = execution_context
        if proof.get("path"):
            proof["receipt_sha256"] = util.file_hash(proof["path"])
        return proof


def _prove(state, workspace, run_dir, current, scope, progress, framework, execution_context):
    started = time.monotonic()
    base = base_commit(state, workspace)
    options = settings(state)
    base_patch = reviewed_patch(state, workspace)
    operator = operator_patch.pinned(state)
    refused = ""
    if base and operator:
        if base_patch:
            refused = ("Both a reviewed change and an operator base patch would change the original code; "
                       "the proof applies only one, so it cannot compare the fix with either")
        else:
            base_patch, problem = operator_patch.check(operator, workspace, base)
            refused = problem and f"The original code cannot be prepared: {problem}"
    unappliable = base and base_patch and not refused and (
        "it is missing" if not base_patch.is_file() else verify.patch_applies(workspace, base, base_patch))
    if not base or unappliable or refused:
        why = refused or ("No base commit is recorded for this run, so the fix cannot be compared with the original code"
               if not base else f"The reviewed change {base_patch.name} cannot be applied to the base revision "
               f"({unappliable}), so the fix cannot be compared with the change the review judged")
        proof = {"verdict": verify.UNVERIFIED, "failures": [], "notes": [], "review_reasons": [],
                 "unverified": [why], "fail_to_pass": None, "commands": {},
                 "base": base, "base_patch": str(base_patch) if base_patch and not refused else None,
                 "source_revision": current, "test_files": [], "source_files": []}
        path = None
    else:
        dependencies = state.get("project_workspace") or str(workspace)
        suite = options.get("test_command") or (framework.suite if framework else None)
        base_suite = (_baseline(state, workspace, run_dir, base, framework, suite, dependencies, base_patch, progress)
                      if suite else None)
        number = len(state.get("regression_proofs", [])) + 1
        out = Path(run_dir) / "regression" / f"proof-{number:02d}-{uuid.uuid4().hex}"
        progress("Comparing regression tests and checking the full candidate suite", command=suite, output=out)
        coverage = preserve_only(state)
        due = cases(state)
        # A non-bugfix contract whose due cases are all test criteria may be a coverage
        # or characterization build: the product already implements the behavior and the
        # approved diff is tests alone. verify() decides that from the diff (#510); its
        # cases are then judged like guards, so a test that does not pass on the
        # original code still blocks the proof as mis-tagged.
        test_only = goals.task_kind(state) != "bugfix" and bool(due)
        regression_command = options.get("regression_command")
        if coverage and not regression_command:
            regression_command = options.get("test_command")
        result = verify.verify(workspace, base, out, framework=framework, suite_command=options.get("test_command"),
                               regression_command=regression_command,
                               reported=None, base_suite=base_suite, dependencies_from=dependencies,
                               timeout=suite_timeout(state),
                               new_behavior=goals.task_kind(state) != "bugfix",
                               preserve_only=coverage, test_only_allowed=test_only, base_patch=base_patch,
                               source_paths=source_scope.paths(state))
        path = out / "verification.json"
        proof = {key: result.get(key) for key in SUMMARY_KEYS}
        if operator:
            proof["review_reasons"] = [*(proof.get("review_reasons") or []), operator_patch.review_reason(operator)]
        if ((coverage or (test_only and not result.get("source_files")))
                and any(case.get("kind") != "preserve" for case in due)):
            due = [{**case, "kind": "preserve"} for case in due]
        named = [test for key in ("fail_to_pass", "pass_to_pass", "not_run_on_base") for test in proof.get(key) or []]
        check_cases(proof, due, wrapped_runner.refusals(workspace, named) if due else {})
        proof["checks"] = {label: {"command": receipt["command"], "exit_code": receipt["exit_code"],
                                   "timed_out": receipt["timed_out"], "output": receipt["output"]}
                           for label, receipt in result["checks"].items()}
    after = verify.execution_identity(workspace, command=options.get("test_command") or
                                       (framework.suite if framework else None),
                                       dependencies_from=state.get("project_workspace"), source_paths=source_scope.paths(state)) if path else execution_context["identity"]
    if after != execution_context["identity"]:
        proof["verdict"] = verify.UNVERIFIED
        proof.setdefault("unverified", []).append("Execution context changed while proving the candidate")
    proof.update(case_scope=scope, path=str(path) if path else None, proved_at=util.now(),
                 duration_seconds=round(time.monotonic() - started, 1))
    if path:
        # A suite-level PASS may still miss a required case. Persist the final
        # decision and coverage while retaining the full executed check receipts.
        result.update({key: proof[key] for key in SUMMARY_KEYS if key in proof}, case_scope=scope)
        util.atomic_json(path, result)
    state["regression_proof"] = proof
    state.setdefault("regression_proofs", []).append(
        {k: proof.get(k) for k in ("verdict", "source_revision", "path", "proved_at", "duration_seconds")})
    # A visible, runner-owned step in the stage history: no provider, no tokens.
    record = {"stage": STAGE, "role": "runner", "iteration": state.get("iteration"), "finished_at": util.now(),
              "runner_owned": True, "engine": "runner", "duration_seconds": proof["duration_seconds"],
              "metrics": {"provider_tokens": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
                                              "reasoning_output_tokens": 0}},
              "summary": f"Regression proof {proof['verdict']}", "output": proof["path"]}
    state.setdefault("stages", []).append(record)
    state.setdefault("history", []).append(record)
    print(f"{STAGE}: {proof['verdict']}" + "".join(f"\n  - {r}" for r in proof["failures"] + proof["unverified"]),
          flush=True)
    return proof


def rejection(state):
    """The completion gate's reason when the current source has no passing proof."""
    proof = state.get("regression_proof") or {}
    reasons = "; ".join((proof.get("failures") or []) + (proof.get("unverified") or [])) or \
        "no proof exists for the current source"
    return f"Completion rejected: this change has no passing regression proof for the current source ({reasons})"


def check_cases(proof, cases, refused=None):
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
                "Use a supported named-test runner (unittest/pytest, Go, or node:test via node --test). "
                "Printed PASS labels and package-script summaries are not named proof; keep the existing "
                "assertions and suite, and register each approved case with the supported runner."]
            proof["verdict"] = verify.UNVERIFIED
        return
    refused = refused or {}
    framework = proof.get("framework") or {}
    framework = framework.get("name") if isinstance(framework, dict) else framework

    def usable(tests):
        return [test for test in tests or [] if test not in refused]

    def refusal(label, case):
        reasons = sorted({refused[test] for test in test_cases.match_cases([case], sorted(refused), framework=framework)[case["id"]]})
        return (f"{label} {test_cases.case_text(case)} has a test named after it that cannot prove it: "
                + "; ".join(reasons)) if reasons else ""

    restore = [case for case in cases if case.get("kind", "restore") == "restore"]
    preserve = [case for case in cases if case.get("kind") == "preserve"]
    proof["case_tests"] = test_cases.match_cases(restore, usable(proof["fail_to_pass"]), framework=framework)
    proof["case_tests"].update(test_cases.match_cases(preserve, usable(proof.get("pass_to_pass")), framework=framework))
    failures = []
    missing = [case for case in restore if not proof["case_tests"][case["id"]]]
    failures += [refusal("Test case", case) or
                 f"Test case {test_cases.case_text(case)} has no test named "
                 f"{test_cases.case_test_name(case['id'], case.get('test_name'))} "
                 "that passes with the change and did not pass without it" for case in missing]
    # A preserve case (a plan's guard:) whose test could not even import on the original code is not
    # shown to fail there: it counts, with a note that its before-state is unproven.
    unrun = usable(proof.get("not_run_on_base"))
    for case in preserve:
        found = [] if proof["case_tests"][case["id"]] else test_cases.match_cases([case], unrun, framework=framework)[case["id"]]
        if found:
            proof["case_tests"][case["id"]] = found
            proof["notes"] = list(proof.get("notes") or []) + [
                f"Preserve case {case['id']}'s test {', '.join(found)} passes with the change but could not run "
                "on the original code (its test file imports code the change adds), so it is not shown to "
                "have passed before"]
    mistagged = [case for case in preserve
                 if set(test_cases.match_cases([case], usable(proof["fail_to_pass"]), framework=framework)[case["id"]]) - set(unrun)]
    untested = [case for case in preserve
                if not proof["case_tests"][case["id"]] and case not in mistagged]
    failures += [refusal("Preserve case", case) or
                 f"Preserve case {test_cases.case_text(case)} has no test named "
                 f"{test_cases.case_test_name(case['id'], case.get('test_name'))} "
                 "that passes both with the change and on the original code" for case in untested]
    failures += [
        f"Preserve case {test_cases.case_text(case)} has a test that fails on the original code, so it "
        "describes behavior the fix restores: tag it restore, or rewrite the test to assert the behavior "
        "that already worked" for case in mistagged]
    if failures:
        proof["failures"] = list(proof.get("failures") or []) + failures
        proof["verdict"] = verify.FAIL


def before_review(state, stage, workspace, run_dir):
    """Called by both dispatch paths just before the Validator (or combined checkpoint) runs."""
    schedule.guard(Path(run_dir) / "check-replay" / "obligations")
    runner_check.clear(state, run_dir, status.persist)
    if stage in ("sol", "astra_checkpoint") and required(state):
        prove(state, workspace, run_dir)


def complete(state, current_revision):
    """True when the run needs no proof, or has a passing proof for exactly this source."""
    if not required(state):
        return True
    proof = state.get("regression_proof") or {}
    if proof.get("verdict") != verify.PASS or proof.get("source_revision") != current_revision:
        return False
    try:
        result = util.read(proof["path"])
        return (util.file_hash(proof["path"]) == proof.get("receipt_sha256")
                and result.get("verdict") == verify.PASS and bool(result.get("checks"))
                and all(schedule.intact(row, root=Path(proof["path"]).parent)
                        for row in result["checks"].values()))
    except (OSError, ValueError, TypeError, KeyError):
        return False


def handoff(state):
    """What the Validator and Completion Owner see: the runner's executed result, not a claim."""
    proof = state.get("regression_proof")
    if not required(state) or not proof:
        return None
    return {**{k: proof.get(k) for k in SUMMARY_KEYS}, "checks": proof.get("checks", {}), "path": proof.get("path"),
            "meaning": "Executed by the runner in clean worktrees against this run's base commit: the new or "
                       "changed tests must fail on the original code and pass on the current code, and no test "
                       "that passed on base may fail. PASS is required before a bug fix can complete."}

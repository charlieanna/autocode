"""The two judgments of model-free verification, over receipts already run (#2753).

``judge_regression`` decides the regression proof (fail-to-pass) from the proving tests' runs on
the candidate and on the base; ``judge_suite`` decides preservation (pass-to-pass) from the suite on
the candidate and the comparable base suite. Neither runs a command or reads the workspace: each
appends to the caller's failure, unverified and note lists, which autocode_verify.verify turns into
its verdict. Missing evidence is UNVERIFIED, never PASS.
"""

from __future__ import annotations

try:
    from . import autocode_cargo_build_evidence as cargo_build
    from . import autocode_cargo_tests as cargo_tests
    from . import autocode_check_outcome as check_outcome
    from . import autocode_command_receipt as command_receipt
    from . import autocode_first_suite as first_suite
    from . import autocode_junit_first_suite as junit_first
    from . import autocode_package_scope as package_scope
    from . import autocode_proof_seam as proof_seam
    from . import autocode_test_root as test_roots
    from . import autocode_test_setup as test_setup
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_cargo_build_evidence as cargo_build
    import autocode_cargo_tests as cargo_tests
    import autocode_check_outcome as check_outcome
    import autocode_command_receipt as command_receipt
    import autocode_first_suite as first_suite
    import autocode_junit_first_suite as junit_first
    import autocode_package_scope as package_scope
    import autocode_proof_seam as proof_seam
    import autocode_test_root as test_roots
    import autocode_test_setup as test_setup
    import autocode_verification_schedule as schedule


# Read by autocode_proof_recognition: on a suite whose results were never parsed, this refusal says
# only that the exit-code fallback could not bound the base definition, not that a test failed.
BASE_DEFINITION_UNKNOWN = (
    "The base suite definition could not be established ({}); preservation of existing behavior is unproven"
)

UNLOADABLE = (
    "The original code could not be loaded by the tests the project already had, so no test can run and fail "
    "there; the change's tests that could not run there and pass with it count"
)


def first_suite_absent(base_suite, base, command, *, document_only=None):
    """Accept only the matching completed baseline, and a base that had no suite to run.

    The base had none when its own receipt says so (``first_suite.no_base_suite``), or when its pinned
    inventory holds no behavior (``document_only``, the observation the preservation check reads,
    ``_document_only_base``): a runner's exit on a project that does not exist yet (npm 254 with no
    package.json, unittest 1 with no test directory) varies by runner and is not what is judged.
    """
    receipt = (base_suite or {}).get("receipt") or {}
    return bool(
        base
        and command
        and base_suite
        and base_suite.get("base") == base
        and base_suite.get("command") == command
        and command_receipt.completed(receipt)
        and (first_suite.no_base_suite(receipt) or (document_only is not None and document_only()))
    )


def compiled_feature_tests(framework, base, passed, changed_tests):
    """Apply the documented build-failure exception only to changed, unobserved tests.

    Go identifies the failed package; Cargo does not currently bind its failed
    target to the file-based passing test identity. That limitation is recorded
    in the proof note and does not establish the cause of a particular failure.
    """
    if framework not in ("go", "cargo") or changed_tests is None:
        return set()
    suffix = "::[build failed]"
    failures = set(base.get("failed") or ())
    targets = {
        name[: -len(suffix)]
        for name in base.get("collection_errors", ())
        if name in failures and name.endswith(suffix) and name[: -len(suffix)]
    }
    if not targets:
        return set()
    observed = set(base.get("passed", ())) | set(base.get("failed", ())) | set(base.get("skipped", ()))
    missing = {name for name in passed - observed if changed_tests(name)}
    if framework == "go":
        return {name for name in missing if name.partition("::")[0] in targets}
    return missing


def judge_regression(
    on_candidate,
    on_base,
    fail,
    unverified,
    notes,
    proof,
    review_reasons,
    *,
    known_failures,
    new_behavior=False,
    preserve_only=False,
    absent_first_go=False,
    absent_junit_base=False,
    seam_names=None,
    changed_tests=None,
    base_suite_passed=None,
    reloaded=None,
    compiled_framework=None,
    absent_first_suite=False,
):
    """Judge the targeted runs of the changed test files.

    With per-test results, the proof is a named test that ran and failed on base
    and ran and passed on the candidate. A module that fails to import on base
    (for example because the test imports a name the fix adds) is not a test that
    ran; ``seam_names(on_base)`` names such added names so the failure can say so.
    A test in the same files that already fails on the pristine base (for
    example one needing a network) neither blocks the fix nor counts as proof.
    Without per-test results, exit codes decide and the change needs review.

    ``changed_tests`` (Go features) limits fail-to-pass to tests the change adds or edits; an unchanged
    test that passed in the pristine base suite (``base_suite_passed()``) is pass-to-pass (#2505).

    ``reloaded()`` (first_suite.reloads; owner decision 2026-11-14): the original code could not be loaded by
    the tests the project already had, and those run and pass with the change. No test can run and fail on
    such code, so a bug fix's tests that could not run there and pass now count, as they do for new behavior.
    """
    candidate = on_candidate.get("results")
    base = (on_base or {}).get("results")
    if not command_receipt.completed(on_candidate) or on_base is not None and not command_receipt.completed(on_base):
        unverified.append("Command ownership was interrupted; no complete regression proof exists")
        failed = set((candidate or {}).get("failed") or [])
        if failed:
            unexplained = sorted(failed - (known_failures() or set()))
            if unexplained:
                fail.append("The regression tests fail on the candidate: " + ", ".join(unexplained[:20]))
        return
    if on_candidate["timed_out"]:
        fail.append("The regression tests timed out on the candidate")
        return
    if on_candidate.get("results_expected"):
        if candidate is None:
            if on_candidate["exit_code"] == 0:
                unverified.append(
                    "The regression run exited 0 without reporting any test result (did the process exit early?)"
                )
            else:
                fail.append("The regression tests fail on the candidate (no test results were reported)")
            return
        failed = set(candidate["failed"])
        if candidate["complete"]:
            passed = set(candidate["passed"])
        else:
            # Incompleteness only makes the absence of a failure unproven (#421): a named
            # failure is judged first, and the fail-to-pass proof is given up after it.
            unverified.append("Per-test results of the regression run were incomplete")
            if failed:
                known = known_failures() or set()
                unexplained = sorted(failed - known)
                if unexplained:
                    fail.append("The regression tests fail on the candidate: " + ", ".join(unexplained[:20]))
                else:
                    notes.append(
                        "Tests in the changed files that already fail on base were not counted: "
                        + ", ".join(sorted(failed)[:20])
                    )
            return
        if candidate["complete"] and not passed:
            fail.append("The regression command ran no passing tests")
        if candidate["complete"] and not failed and on_candidate["exit_code"] not in (0, None):
            # The runner failed for a reason no test names (a coverage floor, a failing session hook): the
            # per-test results alone cannot show the regression run passed (#1113).
            fail.append(
                f"The regression command exited {on_candidate['exit_code']} although every reported test passed, "
                "so its passing results cannot be trusted"
            )
        if failed:
            known = known_failures() or set()
            unexplained = sorted(failed - known)
            if unexplained:
                fail.append("The regression tests fail on the candidate: " + ", ".join(unexplained[:20]))
            else:
                notes.append(
                    "Tests in the changed files that already fail on base were not counted: "
                    + ", ".join(sorted(failed)[:20])
                )
        if absent_first_go and on_base is None and new_behavior and not preserve_only:
            if not failed and not candidate.get("skipped") and on_candidate["exit_code"] == 0 and changed_tests:
                names = sorted(name for name in passed if changed_tests(name))
                if names:
                    proof.update(fail_to_pass=names, pass_to_pass=[], not_run_on_base=names)
                    notes.append("The pinned base has no Go project; these changed tests did not run on base.")
                    return
            unverified.append("The first Go project's changed named tests were not completely established")
            return
        if on_base is None:
            return
        if on_base["timed_out"]:
            unverified.append("The regression tests timed out on base; no complete fail-to-pass proof exists")
            return
        if base is None:
            if new_behavior and not preserve_only and absent_junit_base and passed:
                names = sorted(passed)
                proof["fail_to_pass"] = names
                proof["pass_to_pass"] = []
                proof["not_run_on_base"] = names
                return
            # unittest given a file that cannot be imported names no test either.
            if passed and on_base["exit_code"] not in (0, None) and reloaded and reloaded():
                names = sorted(passed)
                proof.update(fail_to_pass=names, pass_to_pass=[], not_run_on_base=names)
                notes.append(UNLOADABLE)
                return
            # A module that reads a seam while loading can stop the whole run before it reports any test.
            seam = seam_names(on_base) if seam_names and not new_behavior else []
            unverified.append(
                "The regression run on the base code reported no test results"
                + (". " + proof_seam.reason([], seam) if seam else "")
            )
            return
        if not base.get("complete"):
            unverified.append("Per-test results on the base code were incomplete")
            return
        setup_errors = test_setup.not_evidence(base, new_behavior=new_behavior)
        ran_and_failed = (
            set(base["failed"])
            - set(base["collection_errors"])
            - set(base.get("uncollected") or ())
            - set(base.get("skipped") or ())
            - set(setup_errors)
        )
        if setup_errors:
            notes.append(test_setup.proof_note(setup_errors))
        # A skipped, absent or uncollected result is not an observed failure.
        not_run = passed - set(base["passed"]) - set(base["failed"])
        not_run |= passed & (
            set(base.get("uncollected") or ())
            | set(base.get("collection_errors") or ())
            | set(base.get("skipped") or ())
        )
        flipped_set = ran_and_failed & passed
        compiled = (
            compiled_feature_tests(compiled_framework, base, passed, changed_tests)
            if new_behavior and not preserve_only
            else set()
        )
        if compiled:
            flipped_set |= compiled
            notes.append("Changed feature tests were not run on the base because a test build failed.")
            if compiled_framework == "cargo":
                notes.append("Cargo's failed target is not bound to a particular passing test (#4046).")
        if (
            new_behavior
            and not preserve_only
            and absent_first_suite
            and base["collection_errors"]
            and on_candidate["exit_code"] == 0
            and not candidate.get("skipped")
            and not failed
        ):
            observed = set(base["passed"]) | set(base["failed"]) | set(base.get("skipped") or ())
            first = passed - observed
            if first:
                flipped_set |= first
                notes.append("The first suite's named tests were not run on the base because collection failed.")
        flipped = sorted(flipped_set)
        # Tests that ran and passed on the original code and still pass (autocode_regression.check_cases).
        kept = set(base["passed"]) & passed
        # Passing tests that never ran on the original code (their module did not import there): a
        # guard's test there is not shown to fail before, only not shown to pass (check_cases).
        reloadable = not_run - set(base.get("skipped") or ())
        if not new_behavior and reloadable - set(flipped) and reloaded and reloaded():
            flipped = sorted({*flipped, *reloadable})
            notes.append(UNLOADABLE)
        if new_behavior and changed_tests is not None:
            # Preservation reads every test that passes now and did not pass on the targeted base, not only
            # the ones counted as fail-to-pass. An unchanged test the pristine base suite passed can be kept
            # off the targeted base by a changed test's build/collection failure (a Go package that no longer
            # builds); it is still preserved behavior, so it must not depend on entering ``flipped`` (#3981).
            unchanged = {name for name in (passed - set(base["passed"])) if not changed_tests(name)}
            flipped = [name for name in flipped if name not in unchanged]
            preserved = unchanged & (base_suite_passed() if base_suite_passed else set())
            kept |= preserved
            not_run -= preserved
            if unchanged:
                notes.append(f"Not proof (unchanged): {len(unchanged)} test(s); kept from base: {len(preserved)}")
        if new_behavior and cargo_tests.command_words(
            package_scope.within(on_candidate.get("command")), include_owned=True
        ):
            skipped = set(base.get("skipped") or [])
            flipped = [
                name
                for name in flipped
                if name in ran_and_failed or (name not in skipped and cargo_build.was_blocked(base, candidate, name))
            ]
        proof["fail_to_pass"] = flipped
        proof["pass_to_pass"] = sorted(kept)
        proof["not_run_on_base"] = sorted(not_run)
        # Recognized test preparation failures are excluded above. Other missing-name
        # errors may be real product bugs, so preserve the reviewer warning for them.
        seam = seam_names(on_base) if seam_names and (base["collection_errors"] or flipped) and not new_behavior else []
        if not flipped and new_behavior and not preserve_only:
            fail.append(
                "No new or changed test passes with the change and did not pass without it, "
                "so the tests do not show the new behavior"
            )
            if base["collection_errors"]:
                fail.append(
                    "The base test run could not collect " + ", ".join(base["collection_errors"][:5]) + ". "
                    "Make the affected tests load on the base so their named assertions can run there. "
                    "For example, move test-only imports out of module setup when appropriate, or test the "
                    "public command through its output. Keep the behavior assertions and run the proof again."
                )
        elif not flipped and not preserve_only:
            if setup_errors:
                fail.append(test_setup.proof_note(setup_errors))
            elif seam:
                fail.append(proof_seam.reason(base["collection_errors"], seam))
            elif base["collection_errors"]:
                fail.append(
                    "On the unfixed code the new tests only fail to import or collect ("
                    + ", ".join(base["collection_errors"][:5])
                    + "), so no test shows the bug. "
                    "Write the regression test against behavior that exists before the fix."
                )
            else:
                fail.append(
                    "No test fails on the unfixed base code and passes with the fix, "
                    "so the tests do not reproduce the bug"
                )
        elif seam:
            if base["collection_errors"]:
                notes.append(proof_seam.note(base["collection_errors"], seam))
            review_reasons.append(proof_seam.review_reason(flipped, seam))
        return
    # Exit codes only: honest, but weaker, so a person or the Reviewer must read the change.
    review_reasons.append("the regression proof rests on exit codes, not named tests")
    if on_candidate["exit_code"] != 0:
        fail.append("The regression tests fail on the candidate")
    if on_base is None:
        return
    if on_base["exit_code"] == 0 and not preserve_only:
        fail.append("The regression tests also pass on the unfixed base code, so they do not reproduce the bug")
    elif on_base["timed_out"]:
        unverified.append("The regression tests timed out on base; no complete fail-to-pass proof exists")


def judge_suite(
    on_candidate,
    base_suite,
    fail,
    unverified,
    notes,
    *,
    allow_empty_base=False,
    allow_absent_go=False,
    allow_absent_junit=False,
    base_definition=None,
    empty_root=None,
    kept_tests=(),
    untested=False,
    repaired=(),
    quarantined=(),
    replaced=None,
):
    """Nothing that passed on base may fail, be skipped, be deselected or disappear (``kept_tests``: first_suite).

    ``replaced``: whether a test id is a test the approved plan replaces (autocode_superseded_tests). Such a
    test may be gone from the candidate's suite, renamed or removed; one that is there and skipped is not gone.

    ``repaired``: the base's failing tests that pass on the candidate (first_suite.repaired_evidence).
    ``quarantined``: tests the flaky-test recheck showed failing on base too (autocode_suite_flakes); they
    are neither a regression nor a preserved test.

    A suite that timed out on the candidate could not establish a result (autocode_check_outcome): only a
    failure it already recorded of a test that passes on base is a FAIL; otherwise preservation is unproven.
    """
    candidate = on_candidate.get("results")
    base_receipt = (base_suite or {}).get("receipt") or {}
    base_results = base_receipt.get("results")
    outcome = check_outcome.classify(on_candidate)
    if outcome["reason"] == check_outcome.TIMEOUT:
        _judge_timed_out(outcome, base_receipt, fail, unverified, quarantined)
        return
    candidate_owned = command_receipt.completed(on_candidate)
    base_owned = command_receipt.completed(base_receipt)
    if not candidate_owned or not base_owned:
        unverified.append("Command ownership was interrupted; preservation is unproven")
        failed = set((candidate or {}).get("failed") or [])
        if base_owned and (base_results or {}).get("complete"):
            new = sorted(failed - set(base_results["failed"]))
        else:
            new = sorted(failed & set((base_results or {}).get("passed") or []))
        if new:
            fail.append("Tests that pass on base fail on the candidate: " + ", ".join(new[:20]))
        return
    if candidate is not None and not candidate["failed"] and on_candidate["exit_code"] not in (0, None):
        # No test names the nonzero exit (a coverage floor, a failing session hook): complete per-test
        # results do not show the suite passed (#1113).
        problem = f"The project suite exited {on_candidate['exit_code']} on the candidate with no failing test reported"
        if base_receipt.get("exit_code") == 0:
            fail.append(problem + ", and it passes on base")
        else:
            unverified.append(problem + "; preservation is unproven")
    # A first Go suite reports the pattern as a build failure, or no results at all.
    # That is absence of a Go project, not a collection error to preserve (#685).
    absent_go = first_suite.absent_go_suite(base_receipt, on_candidate, no_go_project=allow_absent_go)
    if junit_first.absent_suite(
        base_receipt,
        on_candidate,
        family=junit_first.family(on_candidate.get("command")),
        no_project=allow_absent_junit,
    ):
        notes.append(
            "The pinned base has no project for this JUnit suite; it reports no project on base and passes completely on the candidate"
        )
        return
    # A module that never imported, collected or built is not an executed test; a failed
    # hook or cancellation is (Node and Vitest report both as collection errors, so only
    # ``uncollected`` carries the rule, #503). Keep comparison below so a separately
    # observed regression still wins over incomplete preservation. Results saved by
    # parsers without ``uncollected`` fall back to collection_errors and fail closed.
    for label, results in (("base", base_results), ("candidate", candidate)):
        collection = (results or {}).get("uncollected", (results or {}).get("collection_errors")) or []
        if label == "base" and collection and set(collection) <= set(repaired or ()):
            continue  # every module the base could not load runs and passes now (first_suite.loaded)
        if collection and not (label == "base" and absent_go):
            unverified.append(
                f"The {label} suite has collection errors; preservation is unproven: " + ", ".join(collection[:5])
            )
    # Incompleteness only makes the absence of a failure unproven (#421): a candidate
    # failure that was observed passing on base stays FAIL below.
    if not absent_go and (
        base_receipt.get("timed_out") or (base_results is not None and not base_results.get("complete"))
    ):
        unverified.append("The base suite was incomplete; preservation of its passing tests is unproven")
    base_outcome = check_outcome.classify(base_receipt) if base_results is not None else None
    if base_outcome and base_outcome["reason"] == check_outcome.SESSION_ABORTED:
        # A pytest session that aborted (#3895) names no complete set of base failures to compare with.
        unverified.append(
            check_outcome.describe("The base suite", base_outcome) + "; preservation evidence is incomplete"
        )
    if candidate is not None and not candidate.get("total"):
        unverified.append("The project suite reported zero tests or incomplete per-test results")
        return
    if (
        empty_root is not None
        and test_roots.first_suite(base_receipt, on_candidate)
        and schedule.complete_results(on_candidate)
    ):
        notes.append(
            f"The base holds nothing under {empty_root}/, so the suite rooted there has no existing "
            "behavior to preserve; it ran and passed completely on the candidate"
        )
        return
    if candidate is not None and not candidate.get("complete"):
        unverified.append("The project suite's per-test results were incomplete; only its observed failures are judged")
    if on_candidate.get("results_expected") and candidate is None:
        if on_candidate["exit_code"] == 0:
            unverified.append(
                "The project suite exited 0 without reporting any test result (did the process exit early?)"
            )
        elif base_receipt.get("exit_code") == 0:
            fail.append("The project suite passes on base but fails on the candidate")
        else:
            unverified.append("The project suite reported no test results on the candidate")
        return
    # Matching runner failures do not establish preserved behavior: a broken
    # collector may report a complete set of error placeholders without running
    # the suite. Keep comparing observed failures below so a real regression
    # remains FAIL even when preservation coverage is unverified (#479).
    # Python 3.14 unittest and pytest use exit 5 for honest empty collection.
    # That is usable only when the caller identified a document-only pinned base and
    # the candidate's new suite actually ran and passed in full.
    empty_base = (
        allow_empty_base
        and first_suite.empty_collection(base_receipt)
        and on_candidate["exit_code"] == 0
        and schedule.complete_results(on_candidate)
        and candidate["passed"]
        and not candidate["failed"]
    )
    if empty_base:
        notes.append("The pinned base has no executable source, so its empty suite has no behavior to preserve")
    if absent_go:
        notes.append(
            "The pinned base has no Go project, so this suite has no existing behavior to preserve; "
            "it reports no packages on base and passes completely on the candidate"
        )
        return
    kept = not empty_base and first_suite.untested_base_kept(base_receipt, on_candidate, kept_tests, untested=untested)
    if kept:
        notes.append(first_suite.UNTESTED_BASE_KEPT)
    repaired_all = first_suite.repaired_all(base_results, repaired, notes)
    if base_results is not None:
        if not (empty_base or kept or repaired_all) and (
            not schedule.complete_results(base_receipt) or not base_results["passed"]
        ):
            unverified.append(first_suite.no_base_evidence(untested and first_suite.no_base_suite(base_receipt)))
    elif base_suite is not None and base_suite.get("health") == "broken":
        if first_suite.absent_base_suite(base_receipt, on_candidate, document_only=allow_empty_base):
            notes.append(
                "The pinned documentation-only base has no existing behavior to preserve; "
                "its new suite is absent on base and passes completely on the candidate"
            )
        elif not kept:
            unverified.append(
                first_suite.no_base_evidence(untested and first_suite.no_base_suite(base_receipt), ran=False)
            )
    if candidate is not None and base_results is not None:
        flaky = set(quarantined)
        if base_results.get("complete") and not base_receipt.get("timed_out"):
            new = sorted(set(candidate["failed"]) - set(base_results["failed"]) - flaky)
        else:
            # The base run stopped early, so a test absent from its failures may simply
            # never have run: only a failure that was observed passing on base is a FAIL.
            new = sorted(set(candidate["failed"]) & set(base_results["passed"]) - flaky)
        if new:
            fail.append("Tests that pass on base fail on the candidate: " + ", ".join(new[:20]))
        if candidate["complete"] and base_results["complete"]:
            lost = sorted(set(base_results["passed"]) - set(candidate["passed"]) - set(new) - flaky)
            gone = [test for test in lost if replaced and test not in candidate["skipped"] and replaced(test)]
            if gone:
                lost = [test for test in lost if test not in gone]
                notes.append("Tests the approved plan replaces are no longer in the suite: " + ", ".join(gone[:20]))
            if lost:
                fail.append(
                    "Tests that pass on base did not pass on the candidate (skipped, deselected, "
                    "renamed or missing): " + ", ".join(lost[:20])
                )
        elif candidate.get("complete") and candidate["total"] < base_results["total"]:
            fail.append(
                f"Fewer tests ran on the candidate ({candidate['total']}) than on base ({base_results['total']})"
            )
        if base_results["failed"] and not new and base_results.get("complete") and not base_results.get("aborted"):
            notes.append(f"{len(base_results['failed'])} test(s) already failed on base; none newly fail")
        return
    if on_candidate["exit_code"] == 0:
        # An exit code proves preservation only when both trees ran the same suite. A
        # script-driven command runs each tree's own definition, so the base's definition
        # was also executed over the candidate's product code (#528, #587): when that run
        # fails, the candidate narrowed which tests the suite runs; when it cannot run or
        # complete, or its boundary cannot be established, preservation is unproven. A
        # document-only base has no old behavior, so that first suite may still pass.
        boundary = (base_definition or {}).get("boundary") or ""
        definition_run = (base_definition or {}).get("run")
        if boundary:
            unverified.append(BASE_DEFINITION_UNKNOWN.format(boundary))
        elif definition_run is not None:
            if definition_run["timed_out"]:
                unverified.append(
                    "The base suite definition timed out over the candidate code; "
                    "preservation of existing behavior is unproven"
                )
            elif not command_receipt.completed(definition_run):
                unverified.append(
                    "The base suite definition run over the candidate code did not "
                    "complete; preservation of existing behavior is unproven"
                )
            elif definition_run["exit_code"] in (126, 127):
                unverified.append(
                    "The base suite definition command could not run over the candidate "
                    f"code (exit {definition_run['exit_code']}); preservation of existing "
                    "behavior is unproven"
                )
            elif definition_run["exit_code"] != 0:
                fail.append(
                    "The base suite definition fails against the candidate code: the candidate "
                    "changed which tests the suite runs, so tests the base ran no longer pass"
                )
            else:
                # Exit 0 alone cannot erase the base's passing-test identities (G5).
                # Apply the same completeness, collection and preservation rules to
                # the retained definition as to an ordinary candidate suite.
                definition_fail, definition_unverified, definition_notes = [], [], []
                judge_suite(definition_run, base_suite, definition_fail, definition_unverified, definition_notes)
                if base_results is not None and definition_run.get("results") is None:
                    definition_unverified.append("The base's per-test results are missing from the definition run")
                elif base_results is None and (definition_run.get("results") or {}).get("failed"):
                    definition_fail.append("Tests fail under the base definition despite its zero exit status")
                if definition_run.get("results") is not None and not definition_run["results"].get("passed"):
                    definition_unverified.append("The definition run provided no passing-test evidence")
                prefix = "The base suite definition over the candidate code: "
                fail.extend(prefix + reason for reason in definition_fail)
                unverified.extend(prefix + reason for reason in definition_unverified)
                notes.extend(prefix + reason for reason in definition_notes)
        return
    if base_suite is None:
        unverified.append("The project suite fails on the candidate and there is no base run to compare with")
    elif base_receipt.get("exit_code") == 0:
        fail.append("The project suite passes on base but fails on the candidate")
    else:
        unverified.append(
            "The project suite already fails on base and per-test results are unavailable, "
            "so new failures cannot be ruled out (narrow it with --test-command)"
        )


def _judge_timed_out(outcome, base_receipt, fail, unverified, quarantined):
    """A candidate suite that timed out (#3889): it fails only on a failure it already recorded of a test the
    base shows passing; otherwise it attributes no failure and leaves preservation unproven, with both timings."""
    base_passed = set(((base_receipt.get("results") or {}).get("passed")) or [])
    new = sorted(set(outcome["failed"]) & base_passed - set(quarantined))
    if new:
        fail.append("Tests that pass on base fail on the candidate: " + ", ".join(new[:20]))
    base_outcome = check_outcome.classify(base_receipt) if base_receipt else None
    on_base = (
        " and " + base_outcome["detail"] + " on base"
        if base_outcome and base_outcome["reason"] == check_outcome.TIMEOUT
        else ""
    )
    unverified.append(
        f"The project suite {outcome['detail']} on the candidate{on_base}, so it could not establish a result"
        + ("" if new else "; no failure of a test that passes on base was observed")
        + "; preservation is unproven"
    )


def judge_retained_runner(definition, base_suite, fail, unverified):
    """Judge the candidate's tests run over the base's retained runner inputs (#2966).

    A named test can launch a child suite whose selection a runner input (a manifest
    script, a runner file, a data file it reads) decides, so its identity passes on both
    sides while the child runs other tests. ``definition`` is that run, from
    _run_suite_base_definition with ``keep_tests``, or None when no retained runner input
    changed. A test that passed on base must still pass there; a run that cannot show
    it leaves preservation unproven. A test only the candidate has may fail there, since
    it can need the candidate's own runner inputs.
    """
    if not definition:
        return
    prefix = "The base suite definition's runner inputs over the candidate code"
    if definition["boundary"]:
        unverified.append(
            f"{prefix} could not be established ({definition['boundary']}); preservation of existing behavior is unproven"
        )
        return
    run = definition["run"]
    if run is None:
        return
    results = run.get("results")
    base_results = ((base_suite or {}).get("receipt") or {}).get("results") or {}
    if (
        run["timed_out"]
        or not command_receipt.completed(run)
        or run["exit_code"] in (126, 127)
        or results is None
        or not base_results.get("passed")
    ):
        unverified.append(
            f"{prefix} did not run to completion with per-test results to compare with base; "
            "preservation of existing behavior is unproven"
        )
        return
    base_passed = set(base_results["passed"])
    failed = sorted(base_passed & set(results["failed"]))
    if failed:
        fail.append(
            f"{prefix}: tests that pass on base fail, so the candidate changed what they run: " + ", ".join(failed[:20])
        )
    lost = sorted(base_passed - set(results["passed"]) - set(failed))
    if lost and results.get("complete"):
        fail.append(f"{prefix}: tests that pass on base did not pass: " + ", ".join(lost[:20]))
    elif not results.get("complete") and not failed:
        unverified.append(f"{prefix}: the per-test results were incomplete; preservation is unproven")
    if run["exit_code"] != 0 and not results["failed"]:
        unverified.append(f"{prefix}: the run exited {run['exit_code']} with no failing test reported")

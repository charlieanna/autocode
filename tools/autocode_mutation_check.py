"""Report-only mutation check of named test proof (#699).

Once a named proof's verdict is final and a pass, the runner changes the product lines the change
touched, one small mutation at a time (autocode_mutants), and runs the proving tests against each
mutant in the proof's candidate tree. A mutant those tests still pass is reported beside the test
case they prove: the test passes with that line changed, so it may not pin what the line does. It
complements the check replay's sanity gate against empty test bodies (autocode_test_quality), which
a test that asserts something, but not enough, gets past.

Report only: nothing here changes the verdict, the failures, the unverified reasons or the proof's
receipts, and no mutant run is one of the proof's ``checks``. One deadline, ``BUDGET_SECONDS``,
covers the unmutated run and every mutant: the same limit as one check replay. Python unittest and
pytest proofs are mutated through the AST (autocode_mutants); Go, node:test, Vitest, Jest, Mocha,
Cargo, RSpec and Minitest proofs through tokens (autocode_text_mutants, #3817), and so are Maven, Gradle,
PHPUnit and CTest proofs, whose tests are named by their JUnit XML (OBSERVED, autocode_runner_reports). A text
mutant that is not a program is not a site: a Go or Cargo mutant whose package does not build, a Ruby mutant
``ruby -c`` refuses, and a JUnit runner's mutant that failed without a report, are left out with a file note. Any other runner's record says so in ``unavailable``. Imports nothing
from the runner; the caller owns the scratch tree and runs the commands.
"""

from __future__ import annotations

import copy
import hashlib
import os
import shlex
import time
from pathlib import Path

try:
    from . import autocode_affected_tests as affected_tests
    from . import autocode_command_receipt as command_receipt
    from . import autocode_diff_coverage as diff_coverage
    from . import autocode_go_tests as go_tests
    from . import autocode_mutants as mutants
    from . import autocode_mutation_results as mutation_results
    from . import autocode_package_scope as package_scope
    from . import autocode_proof_stability as proof_stability
    from . import autocode_python_tests as python_tests
    from . import autocode_runner_reports as runner_reports
    from . import autocode_test_cases as test_cases
    from . import autocode_text_mutants as text_mutants
except ImportError:
    import autocode_affected_tests as affected_tests
    import autocode_command_receipt as command_receipt
    import autocode_diff_coverage as diff_coverage
    import autocode_go_tests as go_tests
    import autocode_mutants as mutants
    import autocode_mutation_results as mutation_results
    import autocode_package_scope as package_scope
    import autocode_proof_stability as proof_stability
    import autocode_python_tests as python_tests
    import autocode_runner_reports as runner_reports
    import autocode_test_cases as test_cases
    import autocode_text_mutants as text_mutants

BUDGET_SECONDS = 900
# A mutant that never returns (a negated loop condition) should not spend every other mutant's time:
# each gets this many times the unmutated run's duration, at least the floor, and never more than the
# budget left. No mutant starts with less than the floor left; one the budget cut short is not_run.
MUTANT_FACTOR = 10
MUTANT_FLOOR_SECONDS = 60
PYTHON_FRAMEWORKS = ("unittest", "pytest")
# Runners whose proving tests autocode_proof_stability.narrow can select by name, and the language of the
# sources their mutants are made from (#3817): one table, read for the sources, the narrowing and the parse
# check. Their selection is a name pattern, so a reference or mutant run may report tests beside the selected
# ones (autocode_mutation_results.selected).
TEXT_FRAMEWORKS = {
    "go": "go",
    "node": "js",
    "vitest": "js",
    "jest": "js",
    "mocha": "js",
    "cargo": "rs",
    "rspec": "rb",
    "minitest": "rb",
    # Runners that name their tests only in JUnit XML (autocode_runner_reports): see OBSERVED.
    "maven": "java",
    "gradle": "java",
    "phpunit": "php",
    "ctest": "c",
}
# Runners whose proofs are judged by exit code: their selected tests are the ones the unmutated run of the
# proof's own command reports passing in its JUnit XML, and that command runs unchanged for each mutant.
OBSERVED = frozenset(runner_reports.RUNNERS)
# Runners whose build decides a source is unchanged by its modification time alone: Cargo rebuilds a crate
# only when a source is newer than its last build, as Make and Ninja under CTest and Maven's compiler do. A mutant given an older time than that build (as each
# Python mutant is, for its bytecode cache) would run the binary built before it, unmutated, and survive
# whatever the tests assert. Each of their versions is given a time later than the one before instead.
FORWARD_TIME = frozenset({"cargo", "maven", "gradle", "ctest"})
FRAMEWORKS = PYTHON_FRAMEWORKS + tuple(TEXT_FRAMEWORKS)
# Why a proof's runner can produce no mutation evidence at all. No Builder change closes such a gap, so
# the changed-line repair route reads it (autocode_changed_code_rework.route).
NO_SOURCE = "no_mutation_source"
NO_SELECTION = "runner_cannot_select"
OUTCOMES = ("killed", "survived", "timeout", "not_run")
NOT_PASSED = "not run: the proof did not pass"


def record(note=None, files=(), *, collected=False, unavailable=None) -> dict:
    result = {
        "collected": collected,
        "note": note,
        "limit_per_file": mutants.PER_FILE_LIMIT,
        "budget_seconds": BUDGET_SECONDS,
        "files": list(files),
    }
    if unavailable is not None:
        result["unavailable"] = unavailable
    return result


def runner_name(framework_name, command):
    """The runner the mutation check treats the proof as: its framework's, or, for a framework without a
    mutation source (a CMake suite whose last step is ``ctest``, a command with no framework), the JUnit
    runner its command ends with. An operator-selected JUnit report (``junit``) keeps its own runner."""
    if framework_name in FRAMEWORKS or framework_name == "junit":
        return framework_name
    return runner_reports.runner(package_scope.within(command or "")) or framework_name


def unsupported(framework_name) -> dict:
    """The record of a proof whose runner has no mutation source, saying which runner it was."""
    return record(
        "the mutation check runs Python unittest and pytest, Go, node:test, Vitest, Jest, Mocha, Cargo, "
        "RSpec, Minitest, Maven, Gradle, PHPUnit and CTest proofs; "
        "this proof ran " + (framework_name or "no named-test runner"),
        unavailable=NO_SOURCE,
    )


def proving_tests(cases, proof, framework_name) -> list[str]:
    """The judged tests the due cases name (autocode_proof_stability.proving_tests); unstable ones never count."""
    named = [test for key in ("fail_to_pass", "pass_to_pass", "not_run_on_base") for test in proof.get(key) or []]
    unstable = {row.get("test") for row in proof.get("unstable") or [] if isinstance(row, dict)}
    selected = proof_stability.proving_tests(cases, named, [], framework_name)
    return sorted(test for test in selected if test not in unstable)


def _proof_environment(command) -> str:
    """A Python mutant's command is the proving command, in the proof's own environment.

    The command runner gives every check command the same bytecode cache
    (``autocode_test_environment.bytecode_cache_environment``) and removes the tree's compiles when it
    ends. The collection once set a cache of its own, a new path for each collection: a test that read
    that path ran differently under the mutation check than under the proof, and a continuation kept a
    kill from a window whose path the next window's test never saw (#4062).
    """
    return command


def _uncached(command) -> str:
    """A runner that is not Python keeps its command, except that Go may not answer from its test cache.

    ``go test`` replays a package's earlier result when the test binary and the files the test opened
    are unchanged. A test that builds and runs the package's own command (``go build`` in an external
    ``main_test`` package) links none of the mutated code, so every mutant after the first was answered
    with the unmutated pass (#3817 follow-up): ``-count=1`` makes each mutant's tests run. A command that
    already sets a count keeps it, and a command Go's parser does not read is left as it is.
    """
    found = package_scope.entered(command)
    invocation = go_tests.parse(found[1] if found else command)
    if invocation is None or any(word.partition("=")[0] in ("-count", "-test.count") for word in invocation.arguments):
        return command
    uncached = shlex.join([*invocation.prefix, invocation.executable, "test", "-count=1", *invocation.arguments])
    return package_scope.command(found[0], uncached) if found else uncached


def _later(target) -> int:
    """A modification time a whole second after both the clock and ``target``'s current time (FORWARD_TIME)."""
    return max(time.time_ns(), target.stat().st_mtime_ns) + 1_000_000_000


def _did_not_build(receipt) -> bool:
    """True when a package failed to build. The unmutated reference built, so the mutant's edit broke it:
    the tests of that package never ran, and the other packages' passes say nothing about the edit."""
    results = (receipt or {}).get("results")
    if not isinstance(results, dict) or receipt.get("error") or receipt.get("timed_out"):
        return False
    broken = results.get("collection_errors")
    return (
        isinstance(broken, list)
        and bool(broken)
        and all(isinstance(name, str) and name.endswith("::[build failed]") for name in broken)
    )


def _reporting(run, tree, evidence):
    """``run``, with each command made to write its JUnit results afresh and the receipt given what it wrote
    (autocode_runner_reports). The receipt is the one ``run`` returned and recorded, completed in place."""

    def execute(command, label, limit):
        report = evidence / f"{label}.junit.xml"
        prepared = runner_reports.prepare(package_scope.within(command), tree, report)
        if prepared is None:
            return run(command, label, limit)
        found = package_scope.entered(command)
        receipt = run(package_scope.command(found[0], prepared) if found else prepared, label, limit)
        if receipt.get("results") is None:
            receipt["results"] = runner_reports.results(prepared, tree, report)
        return receipt

    return execute


def _wrote_no_report(receipt) -> bool:
    """True when a JUnit runner's run failed without reporting any test: its build or load failed, so no
    test ran against the mutant (a Java mutant that does not compile)."""
    return (
        isinstance(receipt, dict)
        and receipt.get("results") is None
        and not receipt.get("error")
        and not receipt.get("timed_out")
        and not receipt.get("interrupted")
        and isinstance(receipt.get("exit_code"), int)
        and receipt["exit_code"] != 0
    )


def _parse_command(command, path) -> str:
    """``ruby -c PATH``, through the proof command's ``bundle exec`` when it has one."""
    try:
        bundled = shlex.split(command or "")[:2] == ["bundle", "exec"]
    except ValueError:
        bundled = False
    return shlex.join([*(("bundle", "exec") if bundled else ()), "ruby", "-c", path])


def _did_not_parse(receipt) -> bool:
    """True only when the parse check finished and refused the source. One that timed out, could not start
    or was interrupted says nothing about the mutant, which then runs as any other does."""
    return (
        isinstance(receipt, dict)
        and not receipt.get("error")
        and not receipt.get("timed_out")
        and not receipt.get("interrupted")
        and isinstance(receipt.get("exit_code"), int)
        and receipt["exit_code"] != 0
    )


def judge(receipt, tests) -> tuple[str, list[str]]:
    """(outcome, the proving tests that passed). ``killed`` when a proving test failed or the run failed
    before it could report them; ``survived`` when none failed and some passed; else ``timeout`` or
    ``not_run``."""
    if not receipt or receipt.get("error"):
        return "not_run", []
    if receipt.get("timed_out"):
        return "timeout", []
    seen = [proof_stability.outcome_of(receipt, test) for test in tests]
    if "fail" in seen or receipt.get("exit_code") not in (0, None):
        return "killed", []
    passed = [test for test, outcome in zip(tests, seen, strict=True) if outcome == "pass"]
    return ("survived", passed) if passed and receipt.get("exit_code") == 0 else ("not_run", [])


def proved_outcome(receipt, tests) -> tuple[str, list[str]]:
    """A completed named test must observe a mutation; a broken runner proves nothing.

    Advisory studies keep their historical outcome vocabulary. Completion enforcement
    additionally requires complete results for exactly the selected tests and refuses
    collection/setup failures, missing tests and skipped tests.
    """
    return mutation_results.outcome(receipt, tests)


def collect(
    framework_name,
    command,
    cases,
    proof,
    tree,
    evidence_dir,
    files,
    diff_text,
    run,
    *,
    clock=time.monotonic,
    enforcement=False,
    progress=None,
):
    """Mutate the changed product lines in ``tree`` and run the proving tests against each mutant.

    ``proof`` holds the judged test lists (fail_to_pass, pass_to_pass, not_run_on_base, unstable);
    ``files`` is the changed product code as ``(path, status)``; ``diff_text(files)`` is their
    zero-context diff against the base; ``run(command, label, timeout)`` runs a command in ``tree``
    and returns its receipt with per-test results. Each mutated file gets its exact bytes and
    modification time back after its mutant runs (a FORWARD_TIME runner's a later one), whatever happens, and an error becomes the record's
    note (a file's own, when only that file could not be read or mutated) instead of reaching the
    proof. The proving command is narrowed to the proving tests; when the runner cannot narrow it,
    nothing runs (the whole suite is never the fallback).
    """
    framework_name = runner_name(framework_name, command)
    if framework_name not in FRAMEWORKS:
        return unsupported(framework_name)
    try:
        tests = proving_tests(cases, proof, framework_name)
        return _collect(
            framework_name,
            command,
            tests,
            tree,
            Path(evidence_dir),
            files,
            diff_text,
            run,
            clock,
            strict=enforcement,
            enforcement=enforcement,
            progress=progress,
        )
    except command_receipt.OwnershipUncertain as error:
        if enforcement:
            raise
        return record(f"the mutation check stopped: {type(error).__name__}: {error}")
    except Exception as error:  # report only: whatever goes wrong here, the proof itself stands
        return record(f"the mutation check stopped: {type(error).__name__}: {error}")


def collect_selected_tests(
    framework_name,
    command,
    test_ids,
    tree,
    evidence_dir,
    files,
    diff_text,
    run,
    *,
    clock=time.monotonic,
    deadline=None,
    enforcement=False,
    previous=None,
    progress=None,
):
    """Report mutations against independently observed passing tests, without inventing named cases.

    A review supplies exact test IDs observed in the change's own tests. Every selected ID must
    pass again, with complete results, before any mutant runs. An optional absolute deadline includes
    the caller's discovery and initial test run in the same 900-second allowance. Unknown command
    ownership is never converted into a report-only note or permission to clean up its tree.
    """
    framework_name = runner_name(framework_name, command)
    if framework_name not in FRAMEWORKS:
        return unsupported(framework_name)
    try:
        tests = list(test_ids)
        if any(not isinstance(test, str) or not test for test in tests):
            return record("the selected tests do not contain ordinary test IDs")
        tests = sorted(set(tests))
        maximum = clock() + BUDGET_SECONDS
        outer = deadline
        deadline = min(deadline, maximum) if deadline is not None else maximum
        return _collect(
            framework_name,
            command,
            tests,
            tree,
            Path(evidence_dir),
            files,
            diff_text,
            run,
            clock,
            deadline=deadline,
            strict=True,
            enforcement=enforcement,
            previous=previous,
            progress=progress,
            outer_deadline=outer,
        )
    except command_receipt.OwnershipUncertain:
        raise
    except Exception as error:
        return record(f"the mutation check stopped: {type(error).__name__}: {error}")


def _collect(
    framework_name,
    command,
    tests,
    tree,
    evidence,
    files,
    diff_text,
    run,
    clock,
    *,
    deadline=None,
    strict=False,
    enforcement=False,
    previous=None,
    progress=None,
    outer_deadline=None,
) -> dict:
    observe = framework_name in OBSERVED
    if not tests and not observe:
        return record("no judged test is named after a due case, so the mutation check ran no test")
    python = framework_name in PYTHON_FRAMEWORKS
    narrowed = command if observe else proof_stability.narrow(framework_name, command, tests)
    if not narrowed or (python and python_tests.parse(narrowed, include_owned=True) is None):
        return record(
            "this runner cannot select the proving tests alone, so the mutation check ran no test",
            unavailable=NO_SELECTION,
        )
    make = mutants.python_mutants if python else text_mutants.text_mutants
    prepare = _proof_environment if python else _uncached
    suffixes: tuple[str, ...] = (
        (".py", ".pyi")
        if python
        else tuple(suffix for suffix, kind in text_mutants.LANGUAGES.items() if kind == TEXT_FRAMEWORKS[framework_name])
    )
    forward = framework_name in FORWARD_TIME
    parse_check = TEXT_FRAMEWORKS.get(framework_name) == "rb"
    root = Path(tree).resolve()
    rows, planned = [], []
    for path, status in files:
        if not path.endswith(suffixes):
            continue
        row: dict = {"path": path, "mutants": [], "notes": []}
        rows.append(row)
        target = Path(tree) / path
        if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(root):
            row["notes"].append(f"{path}: not a regular file inside the proof tree; not mutated")
            continue
        try:
            source, times = target.read_bytes(), target.stat()
            changed = mutants.changed_lines(diff_coverage.hunks(diff_text([(path, status)])))
            made, notes = make(
                path,
                source,
                set().union(*changed.values()),
                **({"limit": max(1, len(source))} if enforcement else {}),
            )
        except Exception as error:  # this file's note; the other files are still mutated
            if strict and isinstance(error, command_receipt.OwnershipUncertain):
                raise
            row["notes"].append(f"{path}: not mutated: {type(error).__name__}: {error}")
            continue
        row["notes"] += notes
        if enforcement:
            row["sha256"] = hashlib.sha256(source).hexdigest()
        planned += [(row, target, source, times, mutant) for mutant in made]
    if not planned:
        return record("no changed product line has a mutation site, so the mutation check ran no test", rows)
    retained = {}
    if previous is not None:
        # The caller authenticates source, command, runtime and capture custody.
        # Rebuild the entire site inventory too: no ordinal or display snippet
        # may transfer a completed observation to another mutation.
        old = [item for file in previous.get("files", []) for item in file.get("mutants", [])]
        if (
            not enforcement
            or previous.get("selected_tests") != tests
            or previous.get("execution_policy") != mutation_results.STOP_POLICY
            or [(file.get("path"), file.get("sha256")) for file in previous.get("files", [])]
            != [(file.get("path"), file.get("sha256")) for file in rows]
            or [item.get("site_id") for item in old] != [mutant.id for *_, mutant in planned]
        ):
            return record("The retained mutation study does not match the selected tests and exact sites", rows)
        retained = {item["site_id"]: item for item in old}
    evidence.mkdir(parents=True, exist_ok=True)
    if deadline is None:
        deadline, reference_limit = clock() + BUDGET_SECONDS, BUDGET_SECONDS
    else:
        reference_limit = min(BUDGET_SECONDS, deadline - clock())
        if reference_limit <= 0:
            failed = record("the 900-second mutation budget expired before the unmutated run", rows)
            if previous is not None:
                failed["reference_attempt"] = {
                    "output": None,
                    "timeout_seconds": 0,
                    "budget_limited": True,
                    "timed_out": False,
                    "not_started": True,
                    "outcome": "not_run",
                }
            return failed
    started_wall = time.monotonic()
    total_mutants = len(planned)
    if progress is not None:
        elapsed = round(max(0.0, time.monotonic() - started_wall), 1)
        noun = "mutant" if total_mutants == 1 else "mutants"
        progress(
            f"Checking changed-line mutations: verifying unmutated tests ({total_mutants} {noun} planned, {int(elapsed)}s elapsed)",
            command=narrowed,
            output=evidence,
            phase="mutation_reference",
            current=0,
            total=total_mutants,
            elapsed_seconds=elapsed,
        )
    if observe:
        run = _reporting(run, Path(tree), evidence)
    reference_started = clock()
    reference = run(prepare(narrowed), "unmutated", reference_limit)
    if observe:
        tests = sorted(((reference.get("results") or {}).get("passed")) or [])
        if not tests:
            return record("the proof's command reported no passing test unmutated, so no mutant ran", rows)
    observed_reference = reference if python else mutation_results.selected(reference, tests)
    outcome, passed = (proved_outcome if enforcement else judge)(observed_reference, tests)
    if outcome != "survived" or (strict and set(passed) != set(tests)):
        failed = record(
            "the proving tests did not pass unmutated in the mutation check's own run, so no mutant ran", rows
        )
        if previous is not None:
            failed["reference_attempt"] = {
                "output": reference.get("output"),
                "timeout_seconds": reference_limit,
                "budget_limited": reference_limit < BUDGET_SECONDS,
                "timed_out": reference.get("timed_out") is True,
                "outcome": outcome,
            }
        return failed
    if previous is not None:
        # A continuation window runs the unmutated reference again in its new tree, which is right, but
        # its time is not the mutants' budget. Charged to it, a reference over half the window left no
        # mutant that runs every selected test enough time, in this window or any later one, so a slow
        # suite cut the same kind of site short in every window (#3945 run). The caller's total
        # allowance still bounds the window.
        extended = deadline + max(0.0, clock() - reference_started)
        deadline = min(outer_deadline, extended) if outer_deadline is not None else extended
    bound = max(MUTANT_FLOOR_SECONDS, MUTANT_FACTOR * float(reference.get("duration_seconds") or 0))
    # A runner selected by name pattern runs to the end: its early stop would leave tests beside the
    # selected ones unreported in an order nothing here controls.
    mutant_command = mutation_results.stop_command(narrowed) if enforcement and python else narrowed
    selection_by_site: dict[tuple[str, int], dict] = {}
    selection_summary = None
    if enforcement:
        raw_selection = affected_tests.select(
            framework_name,
            command,
            tests,
            root,
            [row["path"] for row in rows],
            sources={row["path"]: source for row, _, source, _, _ in planned},
        )
        selection_by_site, selection_summary = affected_tests.plan_for_mutants(
            raw_selection, [(row["path"], mutant.line) for row, _, _, _, mutant in planned]
        )
    unrun = 0
    for number, (row, target, source, times, mutant) in enumerate(planned, 1):
        entry = {"line": mutant.line, "operator": mutant.operator, "before": mutant.before, "after": mutant.after}
        site_selection = selection_by_site.get((row["path"], mutant.line)) if enforcement else None
        mutant_tests = site_selection["tests"] if site_selection is not None else tests
        if enforcement:
            entry.update(
                site_id=mutant.id,
                end_line=mutant.end_line,
                source_sha256=mutant.source_sha256,
                selected_tests=list(mutant_tests),
                selection_rule=site_selection["rule"] if site_selection is not None else affected_tests.RULE_ALL,
                failed_tests=[],
                evidence=[],
            )
        prior = retained.get(mutant.id)
        if prior is not None and (prior.get("outcome") != "not_run" or prior.get("evidence")):
            # Even a failed, incomplete or timed-out attempt is retained. Only
            # sites the prior window never launched get another command.
            row["mutants"].append(copy.deepcopy(prior))
            continue
        if enforcement and not mutant_tests:
            # Proven unobserved by any selected test after passing the unmutated reference:
            # retain as survived with no kill rather than running unrelated tests.
            row["mutants"].append(
                {**entry, "outcome": "survived", "survived_tests": [], "evidence": [reference["output"]]}
            )
            continue
        remaining = deadline - clock()
        if remaining < MUTANT_FLOOR_SECONDS:
            unrun += 1
            row["mutants"].append({**entry, "outcome": "not_run", "survived_tests": []})
            continue
        label, limit = f"mutant-{number:02d}", min(remaining, bound)
        active_command = mutant_command
        if enforcement and python and site_selection is not None and site_selection.get("command"):
            active_command = mutation_results.stop_command(site_selection["command"])
        if progress is not None:
            elapsed = round(max(0.0, time.monotonic() - started_wall), 1)
            progress(
                f"Checking changed-line mutations ({number}/{total_mutants}, {int(elapsed)}s elapsed): {row['path']}:{mutant.line}",
                command=active_command,
                output=evidence,
                phase="mutation",
                current=number,
                total=total_mutants,
                elapsed_seconds=elapsed,
            )
        ownership_uncertain = False
        unparsed = False
        try:
            target.write_bytes(mutants.apply(source, mutant))
            # Each version its own whole second, so a cached compile keyed by modification time and size
            # (a test's own Python, started with a minimal environment, -E or -I) is never reused.
            os.utime(
                target,
                ns=(times.st_atime_ns, _later(target) if forward else times.st_mtime_ns - number * 1_000_000_000),
            )
            if parse_check:
                unparsed = _did_not_parse(run(_parse_command(command, row["path"]), f"parse-{number:02d}", limit))
            if not unparsed:
                receipt = run(prepare(active_command), label, limit)
        except command_receipt.OwnershipUncertain:
            ownership_uncertain = strict
            raise
        finally:
            if not ownership_uncertain:
                target.write_bytes(source)
                # A forward-time runner's next build must see the restored source as newer than the mutant's.
                os.utime(target, ns=(times.st_atime_ns, _later(target) if forward else times.st_mtime_ns))
        if unparsed:
            # Ruby refused the mutant's syntax, so no test could load it: not a mutation of the product, as a Go
            # mutant that does not build is not (#3817).
            row["notes"].append(f"{row['path']}:{mutant.line}: the {mutant.operator} mutant did not parse; not a site")
            continue
        if not python and (_did_not_build(receipt) or (observe and _wrote_no_report(receipt))):
            # The text mutator's edit was not a program (Go's "declared and not used" when a dropped call
            # was a name's only use): no test ran, so the site is not a mutation of the product (#3817).
            row["notes"].append(f"{row['path']}:{mutant.line}: the {mutant.operator} mutant did not build; not a site")
            continue
        expected_tests = mutant_tests if enforcement else passed
        judged = receipt if python else mutation_results.selected(receipt, expected_tests)
        outcome, observed = (
            mutation_results.outcome(
                judged,
                expected_tests,
                stopped=(python or observe) and mutation_results.stops_after_failure(receipt.get("command", "")),
            )
            if enforcement
            else judge(judged, passed)
        )
        survived = observed if outcome == "survived" else []
        if enforcement:
            entry.update(failed_tests=observed if outcome == "killed" else [], evidence=[receipt["output"]])
        if strict and outcome == "survived" and set(survived) != set(expected_tests):
            outcome, survived = "not_run", []
        if outcome == "timeout" and limit < bound:  # the budget cut it short, not the mutant
            outcome, unrun = "not_run", unrun + 1
        row["mutants"].append({**entry, "outcome": outcome, "survived_tests": survived})
    note = (
        f"the {BUDGET_SECONDS}-second budget ran out with {unrun} of {len(planned)} mutants not run" if unrun else None
    )
    result = record(note, rows, collected=True)
    if enforcement:
        result.update(
            selected_tests=tests,
            reference_output=reference["output"],
            selection="all_eligible_changed_sites",
            execution_policy=mutation_results.STOP_POLICY,
            test_selection=selection_summary,
            limit_per_file=max(len(source) for _, _, source, _, _ in planned),
        )
    return result


def survivors(check, case_tests) -> dict:
    """``{case id: rows}``: each mutant a due test case's proving test passed with. The keys are the
    test-case ids of ``case_tests``: a diagnosis's ``T1`` for a bug fix, the acceptance criterion's id
    for a planned build. Only cases with one appear, so ``{}`` means every mutant that ran was killed. A row says which
    test passed with which change."""
    found: dict[str, list] = {}
    for case_id, names in (case_tests or {}).items():
        for item in check.get("files") or []:
            for mutant in item.get("mutants") or []:
                if mutant.get("outcome") != "survived":
                    continue
                for test in names or []:
                    if test not in (mutant.get("survived_tests") or []):
                        continue
                    where = f"{item['path']}:{mutant['line']}"
                    found.setdefault(case_id, []).append(
                        {
                            "test": test,
                            "path": item["path"],
                            "line": mutant["line"],
                            "operator": mutant["operator"],
                            "before": mutant["before"],
                            "after": mutant["after"],
                            "text": f"{test_cases.function_name(test)} passes with {where} changed "
                            f"(`{mutant['before']}` -> `{mutant['after']}`)",
                        }
                    )
    return found


def final(check, verdict, case_tests):
    """The record a proof keeps once its verdict is final: every proof that did not pass, including one
    refused before any test ran, has the ``NOT_PASSED`` note and no mutant; a collected check gains
    ``survivors``, keyed by the final ``case_tests``; a passing proof that ran no check (no due case)
    keeps None."""
    if verdict != "PASS":
        return record(NOT_PASSED)
    if not isinstance(check, dict) or not check.get("collected"):
        return check
    return {**check, "survivors": survivors(check, case_tests)}

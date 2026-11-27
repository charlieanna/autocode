"""Original test gates retained before a new code run dispatches a model.

reconcile alone writes settings.protected_tests. Replay and completion read it;
user_events and content-addressed bundles preserve explicit revisions. The
caller supplies test classification, framework discovery and scratch execution.
A run scoped to one package (settings.package) retains that package's tests only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


try:
    from . import autocode_source_scope as source_scope
    from . import autocode_stop as stop
except ImportError:
    import autocode_source_scope as source_scope
    import autocode_stop as stop

import copy
import json
import re
import subprocess
import uuid
from pathlib import Path, PurePosixPath

try:
    from . import autocode_base_attribution as base_attribution
    from . import autocode_check_outcome as check_outcome
    from . import autocode_command_receipt as command_receipt
    from . import autocode_focused_suite as focused_suite
    from . import autocode_package_scope as package_scope
    from . import autocode_plan_command_roles as plan_roles
    from . import autocode_plan_proof_command as plan_command
    from . import autocode_protected_collateral as collateral
    from . import autocode_protected_commands as commands
    from . import autocode_protected_paths as paths
    from . import autocode_protected_project_suite as project_suite
    from . import autocode_protected_store as store
    from . import autocode_python_test_roles as test_roles
    from . import autocode_python_tests as python_tests
    from . import autocode_scratch_suites as scratch_suites
    from . import autocode_superseded_tests as superseded_tests
    from . import autocode_test_homes as test_homes
    from . import autocode_test_role_correction as role_correction
    from . import autocode_test_role_inventory as role_inventory
    from . import autocode_test_support as test_support
    from . import autocode_util as util
    from . import autocode_verification_schedule as schedule
    from .autocode_protected_paths import identity, path_in
except ImportError:
    import autocode_base_attribution as base_attribution
    import autocode_check_outcome as check_outcome
    import autocode_command_receipt as command_receipt
    import autocode_focused_suite as focused_suite
    import autocode_package_scope as package_scope
    import autocode_plan_command_roles as plan_roles
    import autocode_plan_proof_command as plan_command
    import autocode_protected_collateral as collateral
    import autocode_protected_commands as commands
    import autocode_protected_paths as paths
    import autocode_protected_project_suite as project_suite
    import autocode_protected_store as store
    import autocode_python_test_roles as test_roles
    import autocode_python_tests as python_tests
    import autocode_scratch_suites as scratch_suites
    import autocode_superseded_tests as superseded_tests
    import autocode_test_homes as test_homes
    import autocode_test_role_correction as role_correction
    import autocode_test_role_inventory as role_inventory
    import autocode_test_support as test_support
    import autocode_util as util
    import autocode_verification_schedule as schedule
    from autocode_protected_paths import identity, path_in


def body(record):
    return {key: record[key] for key in ("version", "files", "command")}


def verify_binding(record):
    if record.get("version") not in (1, 2) or record.get("binding_hash") != util.digest(body(record)):
        raise ValueError("Protected test binding changed; a model cannot revise the original gate")
    if record.get("inventory_path") and util.read_object(record["inventory_path"]) != body(record):
        raise ValueError("Retained protected-test inventory changed")
    archive = store.archive_path(record)
    if archive is not None:
        store.read(record, archive)
    else:
        for name, expected in record["files"].items():
            if identity(path_in(record["root"], name, allow_link=record["version"] == 2)) != expected:
                raise ValueError(f"Original protected test bundle changed: {name}")
        if record["version"] == 2:
            paths.verify_links(record["root"], record["files"])
    return record


def retain(workspace, run_dir, files, command):
    workspace = Path(workspace).resolve()
    version = 2 if any("symlink" in value for value in files.values()) else 1
    record = {"version": version, "files": copy.deepcopy(files), "command": command}
    record["binding_hash"] = util.digest(body(record))
    root = Path(run_dir) / "protected-tests" / (record["binding_hash"] + ".zip")
    record["root"] = str(root)
    store.capture(record, workspace, root)
    inventory_path = root.parent / (record["binding_hash"] + ".json")
    if not inventory_path.exists():
        util.atomic_json(inventory_path, body(record))
    record["inventory_path"] = str(inventory_path)
    return verify_binding(record)


def inventory(workspace, is_test_path, *, package=None):
    """Every eligible test, or only those inside ``package``; a test's link targets come with it."""
    snapshot = util.snapshot(workspace)["files"]
    roles = role_inventory.from_workspace(workspace, is_test_path, snapshot=snapshot)
    names = [
        name
        for name, value in snapshot.items()
        if roles.test_path(name) and value != "deleted" and roles.protected(name)
    ]
    if package:
        names = [name for name in names if package_scope.contains(package, name)]
    files = paths.collect(workspace, names, snapshot)
    return {name: value for name, value in files.items() if roles.protected(name)}


def reconcile(
    state: RunState, settings, args, workspace, run_dir, *, is_test_path, discover_command, original_inputs=None
):
    """New runs bind all eligible existing tests; saved runs never silently rebind.

    A CLI revision declares the exact old binding, new inventory, command and
    rationale. It is admitted only at a reconciled paused validation boundary.
    Neither a Builder report nor a repaired verification command is authority.
    """
    revision = getattr(args, "revise_protected_tests", None)
    if (
        getattr(args, "run_dir", None)
        and getattr(args, "resume_paused", False)
        and not getattr(args, "revise_protected_tests", None)
        and not any(getattr(args, name, False) for name in ("status", "explain", "dry_run"))
        and str(state.get("status", "")).startswith("PAUSED_")
        and state.get("next_stage") in ("tester", "review_checkpoint")
        and stop.applied_stop(state) is None
    ):
        role_correction.reconcile(
            state,
            settings,
            workspace,
            run_dir,
            is_test=is_test_path,
            verify=verify_binding,
            retain=retain,
            opened=store.opened,
            original_inputs=original_inputs,
        )
    previous = settings.get("protected_tests")
    if revision:
        command_only = str(revision) == commands.SELECTOR
        if not getattr(args, "run_dir", None) or not getattr(args, "resume_paused", False):
            raise ValueError("--revise-protected-tests requires a saved pause and --resume-paused")
        if (
            not str(state.get("status", "")).startswith("PAUSED_")
            or state.get("next_stage") not in ("tester", "review_checkpoint")
            or any(
                state.get(key)
                for key in (
                    "active_stage",
                    "uncertain_artifacts",
                    "active_runner_check",
                    "runner_check",
                )
            )
            or state.get("pending_report_repair")
            and not (command_only and commands.completed_gate_repair(state))
        ):
            raise ValueError("Reconcile the attempt and stop before independent validation to revise protected tests")
        if not previous:
            raise ValueError("This saved run has no original protected-test binding; start a new run")
        verify_binding(previous)
        proposal = commands.proposal(state, previous) if command_only else util.read_object(revision)
        if proposal is None:
            raise ValueError("No approved command-only protected-test correction is available")
        if (
            set(proposal) != {"previous_hash", "files", "command", "reason"}
            or proposal["previous_hash"] != previous["binding_hash"]
            or not isinstance(proposal["reason"], str)
            or not proposal["reason"].strip()
            or not isinstance(proposal["command"], str)
            or not proposal["command"].strip()
        ):
            raise ValueError("A protected-test revision needs the exact previous hash, command and user rationale")
        files = (
            previous["files"]
            if command_only
            else inventory(workspace, is_test_path, package=package_scope.of(settings))
        )
        if proposal["files"] != files or not files:
            raise ValueError("Protected-test revision must identify every current eligible test by hash, size and mode")
        if command_only:
            with store.opened(previous, workspace) as original_root:
                current = retain(original_root, run_dir, files, proposal["command"])
        else:
            current = retain(workspace, run_dir, files, proposal["command"])
        state.setdefault("user_events", []).append(
            {
                "kind": "protected_tests_revised",
                "actor": "user_cli",
                "at": util.now(),
                "previous": previous,
                "current": current,
                "reason": proposal["reason"],
                "proposal_sha256": util.digest(proposal) if command_only else util.file_hash(revision),
            }
        )
        settings["protected_tests"] = current
        if command_only and state.get("pending_report_repair"):
            state.setdefault("report_repair_archive", []).append(
                {
                    "reason": "Explicit original-suite command correction requires fresh validation, not report formatting",
                    "repair": state.pop("pending_report_repair"),
                }
            )
    elif not getattr(args, "run_dir", None):
        files = inventory(workspace, is_test_path, package=package_scope.of(settings))
        explicit = (settings.get("regression") or {}).get("test_command")
        command = explicit or commands.initial_command(files, discover_command() if files else None)
        settings["protected_tests"] = retain(workspace, run_dir, files, command)
    if getattr(args, "run_dir", None) and not any(state.get(key) for key in ("active_stage", "active_runner_check")):
        retained = [settings.get("protected_tests")]
        for event in state.get("user_events", []):
            if event.get("kind") in ("protected_tests_revised", "protected_tests_roles_corrected"):
                retained.extend((event.get("previous"), event.get("current")))
        for record in retained:
            if record:
                verify_binding(record)
                store.compact(record, run_dir)
    return settings


# A stage handoff lists the protected tests in its affected paths, but never more than this: an affected
# directory can hold tens of thousands of them, and copying them all once made a 7.8 MB prompt (#1140). The
# full inventory stays at inventory_path, and the binding and the original-test replay cover every file.
MAX_ASSIGNED_TESTS = 200
MAX_ASSIGNED_BYTES = 32_000


def context(settings, affected_paths=(), state: RunState | None = None, *, current=None):
    record = settings.get("protected_tests")
    correction = (
        test_roles.latest_correction(state.get("user_events"), record.get("binding_hash"))
        if state is not None and record
        else None
    )
    if not record or not record["files"] and not correction:
        return None
    verify_binding(record)
    matching = [
        name
        for name in record["files"]
        if any(name == path.rstrip("/") or name.startswith(path.rstrip("/") + "/") for path in affected_paths)
    ]
    assigned, size = {}, 0
    for name in matching[:MAX_ASSIGNED_TESTS]:
        size += len(json.dumps({name: record["files"][name]})) + 2
        if size > MAX_ASSIGNED_BYTES:
            break
        assigned[name] = copy.deepcopy(record["files"][name])
    result = {key: copy.deepcopy(record.get(key)) for key in ("binding_hash", "root", "inventory_path", "command")}
    result |= {"total_files": len(record["files"]), "assigned_tests": assigned}
    instruction = prompts.get("fragments/protected-oracles/context.md")
    if correction:
        result["role_correction"] = correction
        instruction += "\nThe runner removed only proven original product entries from a legacy test inventory. Original test bytes and command remain protected; the retired proof is historical and fresh proof is required. See the bound archive references and correction reason."
    replaced = superseded_tests.approved(state) if state is not None else []
    if replaced:
        result["superseded_tests"] = [{"file": row["file"], "test": row["test"]} for row in replaced]
        instruction += "\n\n" + prompts.get("fragments/protected-oracles/superseded.md")
    if len(assigned) < len(matching):
        result |= {"assigned_total": len(matching), "assigned_omitted": len(matching) - len(assigned)}
        instruction += "\n\n" + prompts.get("fragments/protected-oracles/omitted.md")
    if current and current.get("revision"):
        matches = {}
        for name, original in assigned.items():
            digest = current["files"].get(name, "").removeprefix("executable:")
            if "sha256" in original and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
                matches[name] = digest == original["sha256"]
        if matches:
            result["content_comparison"] = {"source_revision": current["revision"], "matches_original": matches}
            instruction += (
                "\nThe runner compared the retained test bytes with the current source snapshot for the listed files. "
                "Use that comparison for file contents. Inspect changed assertions in the diff and consult the "
                "retained original when the diff does not answer a question. This byte comparison does not check modes, "
                "links or test results. Keep the independent checks and the runner's protected test gate."
            )
    return result | {"instruction": instruction}


def replay_command(command, changed, workspace, *, detected=True):
    """The bound original-suite command narrowed to the changed test modules, or ``command`` unchanged.

    The pair exists to prove that a changed protected test did not weaken an original assertion:
    only the changed files differ between the candidate and the original overlay, and unchanged
    originals use the ordinary independent gates. Running the whole bound suite twice for that
    could not finish on a large repository (1,518 test files under a 900-second replay limit), and
    both halves timed out identically. So a Python suite is narrowed to the changed test modules,
    through the same ``Invocation.targeted`` the regression proof uses. The whole suite still runs
    when that cannot be shown safe: a changed file that is not a test module (a conftest, a helper,
    a data file), a deleted or replaced test, or a command that is not one Python test runner.

    The bound command is the suite of the repository's root. Where that is a Python suite, or there
    is none, and the changed tests are another language's in a project below the root (``web/test/``
    beside ``api/``), it cannot run them: they run with that project's own suite
    (autocode_protected_project_suite), as in the regression proof. Python tests in a project below a
    root that is no project of its own run inside that project, with the bound runner, when the
    command is one AutoCode ``detected``: a command the person typed runs where they typed it.
    """
    invocation = python_tests.parse(command) if command else None
    names = sorted(changed)
    if not names or command and invocation is None:
        return command
    for name in names:
        try:
            path_in(workspace, name)  # a deleted test, or one replaced by a link, keeps the whole suite
        except (OSError, ValueError):
            return command
    if not any(name.endswith((".py", ".pyi")) for name in names):
        return project_suite.command(names, workspace) or command
    if invocation is None or not all(python_tests.TEST_MODULE.match(PurePosixPath(name).name) for name in names):
        return command
    nested = project_suite.python_command(invocation, command, names, workspace) if detected else None
    return nested or invocation.targeted(names) or command


def replay_parts(command, changed, workspace, *, detected=True, homes=None) -> list:
    """The commands that together run the changed tests: ``replay_command``'s one, and before it the
    command of each changed file the regression proof saw load somewhere other than the root (``homes``,
    autocode_test_homes.away), so the gate runs such a test where the proof ran it. A command the person
    typed runs everything where they typed it."""

    def intact(name):
        try:
            path_in(workspace, name)
        except (OSError, ValueError):
            return False
        return True

    placed = test_homes.replay_commands(homes, sorted(changed), intact) if detected and homes else {}
    rest = {name for name in changed if name not in placed}
    groups = project_suite.groups(sorted(rest), workspace) if detected else []
    if len(groups) > 1:
        # Changed tests of more than one runner (Python beside a Node project's tests): no one command runs
        # them all, so each group runs with its own, as when only one of them changed. Any group that needs
        # the whole bound suite (a changed helper, a deleted test) keeps the whole suite for all of them.
        split = [replay_command(command, group, workspace, detected=detected) for group in groups]
        if command not in split and None not in split:
            return [*split, *sorted(set(placed.values()) - set(split))]
    single = replay_command(command, rest, workspace, detected=detected) if rest or not placed else None
    return [*([single] if single else []), *sorted(set(placed.values()))]


ATTEMPTS = ("candidate", "original", "original_recheck")


def _bounded_replay(state: RunState, original, changed, workspace, timeout):
    """A bounded command for the protected pair when the whole bound suite cannot finish under ``timeout``.

    The regression proof already observes a detected whole suite that times out on the original code and
    records the bounded focused selection it fell back to (``autocode_focused_suite``,
    ``proof["suite_fallback"]``). The protected pair reads that one recorded answer rather than spending the
    whole suite's bound on both halves and pausing for the person (#3963): it runs the changed original test
    modules together with the proof's recorded bounded subset, through the same ``Invocation.targeted`` the
    replay already uses. None when there is no such recorded evidence, the bound command is the person's own
    (``project_suite.detected`` is False), a changed test was deleted, or the suite cannot be targeted — the
    whole suite then runs as before. This never claims a full-suite pass: the caller records the selection and
    its verification gap in the receipt, and both halves must still authenticate and pass.
    """
    if not project_suite.detected(state):
        return None
    fallback = (state.get("regression_proof") or {}).get("suite_fallback")
    if not isinstance(fallback, dict) or fallback.get("whole_suite") != original:
        return None
    if fallback.get("whole_suite_outcome") != focused_suite.TIMED_OUT:
        return None
    limit = fallback.get("timeout_seconds")
    if isinstance(limit, (int, float)) and limit < timeout:
        return None  # a shorter recorded bound does not show this bound cannot finish
    invocation = python_tests.parse(original)
    if invocation is None:
        return None
    modules = []
    for name in sorted(changed):
        try:
            path_in(workspace, name)
        except (OSError, ValueError):
            return None  # a deleted or replaced test keeps the whole suite
        if name.endswith((".py", ".pyi")) and python_tests.TEST_MODULE.match(PurePosixPath(name).name):
            modules.append(name)
    tests = sorted(set(fallback.get("tests") or ()) | set(modules))
    if not tests:
        return None
    targeted = invocation.targeted(tests)
    if not targeted or targeted == original:
        return None
    scope = fallback.get("selection_scope") or focused_suite.SCOPE_FULL_CLOSURE
    gap = (
        "the whole bound suite was not re-run: it timed out on the original code in the regression proof, "
        "so the protected pair ran the changed original tests and the proof's recorded bounded subset"
    )
    return {
        "version": 1,
        "decision": "bounded_protected_replay",
        "source": "regression_suite_fallback",
        "whole_suite": original,
        "whole_suite_outcome": fallback.get("whole_suite_outcome"),
        "timeout_seconds": limit,
        "command": targeted,
        "tests": tests,
        "selection_scope": scope,
        "verification_gap": gap,
        "reason": (
            f"The whole bound suite timed out on the original code in the regression proof, so the protected "
            f"pair ran the bounded {scope} selection (changed original tests and the proof's recorded subset): "
            + ", ".join(tests)
            + f". Verification gap: {gap}."
        ),
    }


def replay(state: RunState, workspace, out, scratch_run, *, timeout, run_check=None, base_run=None):
    """Run the changed original tests on the candidate and with the original tests overlaid.

    ``run_check(label, execute, policy=..., accepted_failure=...)`` lets the caller schedule each run
    under its own obligation (#3040); ``execute(directory)`` performs it. Whatever it returns, the
    gate below interprets the receipt afresh under the current approved supersedes list.

    ``base_run(workspace, folder, *, command, timeout)`` runs the same command once in a clean copy of
    the run's recorded base revision (autocode_base_command.run, bound by the caller). With it, an
    original half that fails is compared with that authenticated independent execution of the UNCHANGED
    base (#3944): a failure the base has identically (whole output, not matching text) is pre-existing
    and excused, never sent to a Builder repair or a person question about the host. Without it, or when
    the base does not fail identically, every failure stays the change's, as before.
    """
    record = state.get("settings", {}).get("protected_tests")
    if not record:
        return None  # compatibility: do not invent an original binding for old runs
    verify_binding(record)
    revision = source_scope.snapshot(workspace, state)["revision"]
    data_links = paths.data_links(record["files"])
    changed = []
    for name, expected in record["files"].items():
        try:
            actual = identity(path_in(workspace, name, allow_link=record["version"] == 2))
        except (OSError, ValueError):
            actual = None
        if actual != expected:
            changed.append(name)
    result = {
        "binding_hash": record["binding_hash"],
        "source_revision": revision,
        "changed_tests": changed,
        "verdict": "PASS",
        "candidate": None,
        "original": None,
    }
    if not changed:
        result["meaning"] = "Original test files unchanged; ordinary independent checks remain required."
        return result
    directory = Path(out) / "protected-tests" / uuid.uuid4().hex
    original = record["command"] or _plan_suite(state, record)
    parts = replay_parts(
        original, changed, workspace, detected=project_suite.detected(state), homes=test_homes.away(state)
    )
    command = " ; ".join(parts)
    if original and command == original:
        # The whole bound suite would run on both halves. When the regression proof already recorded that it
        # cannot finish under this bound, run the bounded selection it chose instead of timing out and pausing
        # for the person (#3963); the decision and its gap are recorded in the receipt.
        bounded = _bounded_replay(state, original, changed, workspace, timeout)
        if bounded:
            command = bounded["command"]
            parts = [command]
            result["bounded_selection"] = bounded
    if not command:
        result.update(
            verdict="NOT_VERIFIED", error="No original suite command was available; explicit user revision required"
        )
    else:
        if original and not record["command"]:
            # Nothing was typed or detected at launch, before the plan existed (autocode_plan_proof_command).
            result["plan_command"] = original
        replaced = superseded_tests.approved(state)
        if command != original:
            result["replayed_command"] = command
        # Test support the proof showed defines no test (autocode_test_support): its candidate bytes stay, so
        # the original tests' own assertions run on the repaired fixture or helper.
        support = [name for name in test_support.proven(state.get("regression_proof"), revision) if name in changed]
        proven_tests = test_support.proven_tests(state.get("regression_proof"), revision)
        policy = {
            "binding_hash": record["binding_hash"],
            "command": command,
            "changed_tests": sorted(changed),
            "superseded": _listed(replaced, changed),
            **({"test_support": support} if support else {}),
        }

        def run_part(part, folder, label, overlays, timeout_seconds=timeout):
            def execute(folder):
                return scratch_run(
                    workspace,
                    folder,
                    command=part,
                    timeout=timeout_seconds,
                    source_paths=source_scope.paths(state),
                    expected_source_revision=revision,
                    protected_test_snapshot_links=True,
                    protected_data_links=data_links,
                    **overlays,
                )

            if run_check is None:
                return execute(folder)
            return run_check(
                label,
                execute,
                policy={
                    **policy,
                    "command": part,
                    **({"timeout_seconds": timeout_seconds} if label == "original-recheck" else {}),
                },
                accepted_failure=lambda row: (
                    superseded_tests.excused(row, replaced, changed) is not None
                    or test_support.added_only(row, record["files"], proven_tests) is not None
                ),
            )

        def run(label, **overlays):
            if len(parts) == 1:
                return run_part(command, directory / label, label, overlays)
            # One run for each project's changed tests; the half fails when any of them does.
            return scratch_suites.merged(
                run_part(part, directory / label / str(index), label, overlays) for index, part in enumerate(parts)
            )

        result["candidate"] = run("candidate")
        with store.opened(record, workspace) as original_root:
            overlays = {
                "files": {
                    name: str(path_in(original_root, name))
                    for name, value in record["files"].items()
                    if "symlink" not in value and name not in support
                }
            }
            links = {
                name: value["symlink"]
                for name, value in record["files"].items()
                if "symlink" in value and name not in support
            }
            if links:
                overlays["links"] = links
            result["original"] = run("original", **overlays)
            if (
                len(parts) == 1
                and replaced
                and check_outcome.classify(result["candidate"])["outcome"] == check_outcome.PASSED
            ):
                duration = result["original"].get("duration_seconds")
                remaining = timeout - duration if isinstance(duration, (int, float)) else 0
                if remaining >= 1 and (
                    retry_command := collateral.offer(
                        command, result["original"], replaced, changed, directory, workspace
                    )
                ):
                    result["original_recheck"] = run_part(
                        retry_command, directory / "original-recheck", "original-recheck", overlays, remaining
                    )
        # One reading of each half (autocode_check_outcome): a genuine failure of either half wins; a half
        # that could not establish a result keeps completion blocked without directing a product edit.
        outcomes = {half: check_outcome.classify(result[half]) for half in ("candidate", "original")}
        retry = result.get("original_recheck")
        if retry:
            outcome = check_outcome.classify(retry)
            if outcome["outcome"] != check_outcome.PASSED:
                outcomes["original"] = outcome
        if any(outcome["outcome"] == check_outcome.FAILED for outcome in outcomes.values()):
            result["verdict"] = "FAIL"
        elif undetermined := {
            half: {"reason": outcome["reason"], "detail": outcome["detail"]}
            for half, outcome in outcomes.items()
            if outcome["outcome"] == check_outcome.UNDETERMINED
        }:
            result["verdict"] = "NOT_VERIFIED"
            result["undetermined"] = undetermined
        if support:
            result["test_support"] = support
        if (
            replaced
            and outcomes["candidate"]["outcome"] == check_outcome.PASSED
            and command_receipt.completed(result["original"])
        ):
            # The approved plan replaces these original tests (#2748): only their failures are accepted.
            excused = superseded_tests.excused(result["original"], replaced, changed)
            if not excused and (retry := result.get("original_recheck")):
                excused = collateral.recovered(
                    command, result["original"], retry, replaced, changed, directory, workspace
                )
            if excused:
                result["verdict"] = "PASS"
                result["superseded"] = {"tests": _listed(replaced, changed), "excused_failures": excused}
        if (
            result["verdict"] == "FAIL"
            and outcomes["candidate"]["outcome"] == check_outcome.PASSED
            and not retry
            and (added := test_support.added_only(result["original"], record["files"], proven_tests))
        ):
            # Only tests the change adds failed with the original test files restored: no original test did.
            result["verdict"] = "PASS"
            result["new_test_failures"] = added
        if (
            base_run is not None
            and len(parts) == 1
            and result["verdict"] == "FAIL"
            and outcomes["original"]["outcome"] == check_outcome.FAILED
            and outcomes["candidate"]["outcome"] == check_outcome.FAILED
        ):
            # An original test that fails identically on the unchanged base is the base's failure, not the
            # change's (#3944). Grounded in a fresh authenticated execution of the base, never matching text.
            # The candidate half must STILL fail on those same tests: a candidate that passes them has
            # weakened, removed or skipped the assertion, which is the change's failure, never excused.
            preexisting = _preexisting(result, base_run, workspace, directory, command, timeout)
            if preexisting:
                result["verdict"] = "PASS"
                result["preexisting_failures"] = preexisting
    verify_binding(record)
    if source_scope.snapshot(workspace, state)["revision"] != revision:
        result.update(verdict="NOT_VERIFIED", error="Candidate changed during protected-test replay")
    directory.mkdir(parents=True, exist_ok=True)
    receipt = directory / "receipt.json"
    util.atomic_json(receipt, result)
    result["receipt"] = str(receipt)
    result["receipt_sha256"] = util.file_hash(receipt)
    if result["verdict"] == "NOT_VERIFIED" and result.get("undetermined") and not result.get("error"):
        reasons = "; ".join(
            check_outcome.describe(f"the {half} half", outcome) for half, outcome in result["undetermined"].items()
        )
        raise commands.OriginalGateUndetermined(
            f"The protected-test check could not establish a result: {reasons}. No result was accepted and "
            "completion stays blocked. The finished Tester answer was set aside; --resume-paused runs the Tester "
            f"again, which re-runs this check. Receipt: {receipt}",
            outcome=result["undetermined"],
            receipt=result,
        )
    if result["verdict"] != "PASS":
        seen = result.get("original_recheck") or result.get("original") or {}
        tail = seen.get("tail", "")[-500:]
        failing = (seen.get("results") or {}).get("failed") or []
        raise commands.OriginalGateFailure(
            commands.ORIGINAL_GATE_FAILED + " on this implementation. "
            "Repair the implementation; a modified assertion is not proof. "
            + (f"Failing original tests: {', '.join(failing[:10])}. " if failing else "")
            + "Only an approved plan criterion's supersedes list can replace an original test that asserts the "
            "behavior being changed. " + result.get("error", "") + f" Receipt: {receipt}. {tail}",
            result,
        )
    return result


def _plan_suite(state: RunState, record):
    """The approved plan's command that runs only original protected tests, for a binding retained with no
    command: no runner was typed or detected when the run started, before any plan existed. The command was
    approved before any Builder ran, and the binding still fixes which original test bytes it runs."""
    chosen = plan_command.choose(
        plan_command.approved_commands(state.get("goal_contract")),
        original_tests=list(record["files"]),
        changed_tests=[],
    )
    # Where no rule knows the plan's command, the one the proof saw run the project's existing tests.
    return chosen["suite"] if chosen else plan_roles.suite(state, state.get("goal_contract"))


def _listed(replaced, changed):
    return [
        {"criterion": row["criterion"], "file": row["file"], "test": row["test"]}
        for row in replaced
        if superseded_tests.edited(row, changed)
    ]


def _failed_names(row):
    return set(((row or {}).get("results") or {}).get("failed") or [])


# A test runner's own timing summary varies between two runs of the same failing test, but it is not a
# failure detail. Erase only these runner-owned summary lines (unittest's ``Ran N tests in 0.001s`` and
# pytest's framed ``... in 0.00s ...`` footer), never a failure message: a changed assertion, value or
# exception still moves the output and keeps the failure the change's (#3944). A runner whose summary is
# not recognized is left as-is, so its comparison fails closed rather than excusing anything.
_RUNNER_TIMING = (
    re.compile(rb"(?m)^(Ran \d+ tests? in )\d+\.\d+s[ \t]*$"),
    re.compile(rb"(?m)^(=+[^\n]*? in )\d+\.\d+s([^\n]*?=+)[ \t]*$"),
)


def _strip_runner_timing(data: bytes) -> bytes:
    for pattern in _RUNNER_TIMING:
        data = pattern.sub(rb"\1<duration>\2" if pattern.groups == 2 else rb"\1<duration>", data)
    return data


# A candidate may add passing coverage beside a pre-existing failure (#3944). The runner's own status
# lines for tests that did NOT fail (unittest's ``... ok`` / ``... skipped`` and pytest's ``PASSED`` /
# ``SKIPPED`` verbose lines) and its pass/skip counts are runner-owned noise, not failure detail: erasing
# them lets a candidate that adds passing tests compare equal to the base that lacks them. A failure
# status line (``FAIL`` / ``ERROR`` / ``FAILED``) and every failure block are never erased, so a changed
# assertion, value or exception still moves the output and keeps the failure the change's. A runner whose
# format is not recognized is left as-is, so its comparison fails closed rather than excusing anything.
_PASSING_STATUS = (
    re.compile(rb"(?m)^.* \.\.\. ok[ \t]*\n?"),
    re.compile(rb"(?m)^.* \.\.\. skipped[^\n]*\n?"),
    re.compile(rb"(?m)^.* (?:PASSED|SKIPPED|XFAIL|XPASS)[^\n]*\n?"),
)
_RAN_COUNT = re.compile(rb"Ran \d+ tests?")
_PYTEST_PASS_PHRASE = re.compile(rb"(?:, )?\d+ (?:passed|skipped|xfailed|xpassed)(?:, )?")


def _strip_passing_noise(data: bytes) -> bytes:
    data = _strip_runner_timing(data)
    for pattern in _PASSING_STATUS:
        data = pattern.sub(b"", data)
    data = _RAN_COUNT.sub(b"Ran <count> tests", data)
    return _PYTEST_PASS_PHRASE.sub(b"", data)


def _preexisting(result, base_run, workspace, directory, command, timeout):
    """The original half's failures when an authenticated execution of the UNCHANGED base has them
    identically (#3944), else None.

    The base runner executes the same command in a clean copy of the run's base revision; its receipt
    must be runner-owned and its capture intact. ``base_attribution.judge`` then requires the original
    half to fail with the SAME nonzero exit and the SAME whole output once each copy's directory is
    replaced — a changed assertion, value, parameter, exception chain or execution source moves the
    output and keeps the failure the change's. Matching text alone never excuses: the exemption rests on
    the independent base execution, not on the message. The candidate half must STILL fail identically on
    exactly those tests: a candidate that passes, removes or skips them has weakened the oracle, and a new
    or differently-failing test (another value or exception) is the change's failure too — neither is
    excused. The base receipt is retained so completion re-verifies the same evidence.
    """
    original, candidate = result["original"], result["candidate"]
    if not (command_receipt.completed(original) and command_receipt.completed(candidate)):
        return None
    original_failed = _failed_names(original)
    # The candidate must fail on EXACTLY the original's failing tests: a passing, removed or skipped case
    # (fewer names) or a newly-failing test (more names) is the change's failure, never a pre-existing one.
    if not original_failed or _failed_names(candidate) != original_failed:
        return None
    try:
        base = base_run(workspace, directory / "base", command=command, timeout=timeout)
    except util.Paused:
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return None
    if not isinstance(base, dict) or not base.get("tree") or not command_receipt.completed(base):
        return None
    if not schedule.intact(base, root=directory / "base"):
        return None
    attribution = base_attribution.judge(original, base, normalize=_strip_runner_timing)
    if attribution.get("kind") != base_attribution.BASE:
        return None
    # The candidate half's failure must itself be the authenticated base failure, not a different failure
    # of the same-named test (another value or exception). Only runner-owned passing noise and counts are
    # erased, never failure content, so a changed assertion that still fails moves the output and refuses.
    if base_attribution.judge(candidate, base, normalize=_strip_passing_noise).get("kind") != base_attribution.BASE:
        return None
    return {
        "tests": sorted(original_failed),
        "reason": attribution["reason"],
        "base_exit_code": attribution.get("base_exit_code"),
        "base_output_sha256": attribution.get("base_output_sha256"),
        "normalized_output_sha256": attribution.get("normalized_output_sha256"),
        "base": base,
    }


def _preexisting_ready(state: RunState, result):
    """Re-verify a PASS receipt's excused pre-existing failures from the retained base evidence."""
    accepted = result.get("preexisting_failures")
    original, candidate = result.get("original"), result.get("candidate")
    if not isinstance(accepted, dict) or not isinstance(original, dict) or not isinstance(candidate, dict):
        return False
    base = accepted.get("base")
    if not (
        isinstance(base, dict)
        and base.get("tree")
        and command_receipt.completed(original)
        and command_receipt.completed(candidate)
        and command_receipt.completed(base)
    ):
        return False
    original_failed = _failed_names(original)
    if not original_failed or _failed_names(candidate) != original_failed:
        return False
    if check_outcome.classify(candidate)["outcome"] != check_outcome.FAILED:
        return False
    if sorted(original_failed) != accepted.get("tests"):
        return False
    try:
        if not schedule.intact(base, root=Path(base["output"]).parent):
            return False
    except (OSError, KeyError, TypeError, ValueError):
        return False
    return (
        base_attribution.judge(original, base, normalize=_strip_runner_timing).get("kind") == base_attribution.BASE
        and base_attribution.judge(candidate, base, normalize=_strip_passing_noise).get("kind") == base_attribution.BASE
    )


def ready(state: RunState, current_revision):
    record = state.get("settings", {}).get("protected_tests")
    if not record:
        return True
    try:
        verify_binding(record)
        proof = (state.get("validation") or {}).get("check_replay") or {}
        if proof.get("verdict") != "PASS" or proof.get("source_revision") != current_revision:
            return False
        result = proof.get("protected_tests") or {}
        if result.get("receipt"):
            receipt = Path(result["receipt"])
            if (
                not receipt.is_file()
                or util.file_hash(receipt) != result.get("receipt_sha256")
                or util.read_object(receipt)
                != {key: value for key, value in result.items() if key not in ("receipt", "receipt_sha256")}
            ):
                return False
        if result.get("changed_tests") or any(result.get(name) is not None for name in ("candidate", "original")):
            preexisting_ok = _preexisting_ready(state, result)
            for name in ("candidate", "original"):
                row = result.get(name)
                if not isinstance(row, dict) or type(row.get("exit_code")) is not int:
                    return False
                if check_outcome.classify(row)["outcome"] == check_outcome.PASSED:
                    continue
                if name == "original" and _superseded_ready(state, result):
                    continue
                if preexisting_ok:
                    continue
                return False
        elif result.get("superseded") or result.get("preexisting_failures"):
            return False
        return (
            result.get("verdict") == "PASS"
            and result.get("binding_hash") == record["binding_hash"]
            and result.get("source_revision") == current_revision
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _superseded_ready(state: RunState, result):
    """The receipt's accepted original failures are, today, exactly the approved plan's superseded tests."""
    row, accepted = result.get("original"), result.get("superseded")
    if not isinstance(accepted, dict) or not isinstance(row, dict) or not command_receipt.completed(row):
        return False
    replaced = superseded_tests.approved(state)
    changed = result.get("changed_tests") or []
    excused = superseded_tests.excused(row, replaced, changed)
    if not excused and (retry := result.get("original_recheck")) and result.get("receipt"):
        record = (state.get("settings") or {}).get("protected_tests") or {}
        command = result.get("replayed_command") or result.get("plan_command") or record.get("command")
        excused = collateral.recovered(
            command, row, retry, replaced, changed, Path(result["receipt"]).parent, state.get("workspace")
        )
    return bool(excused) and accepted == {"tests": _listed(replaced, changed), "excused_failures": excused}

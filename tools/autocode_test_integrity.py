"""Runner-owned guard: a change may not weaken a test the run started with (GitHub issue #229).

A test file that existed at the run's base commit is protected. When the current source modifies one,
the runner runs it twice in scratch worktrees over the same current product code: once as edited and once
as it was at base. A test that fails in its original form but not in its edited form was weakened: its
expected value was changed to match the code, an assertion was dropped, the case was skipped, renamed or
removed, or it was made unconditional. Deleting a protected test file weakens it too. New test files, tests
added to a protected file, and rewrites that keep each test's meaning pass both ways.

Only the user lifts the guard, for one exact edit: ``--approve-test-change PATH`` accepts the file's
contents as the runner checked them (or its deletion). Any later edit to that file is checked again.
The completion gate (``complete``) refuses a source with an unapproved weakening, and a weakening that
survives a rework is held for the user (``hold``) instead of being retried.

All of it is saved under ``state["protected_tests"]``: ``latest`` is the result for one source revision
(the completion gate and the Validator's and Completion Owner's context read it), ``flagged`` maps each
path to the revisions where it was weakened (``hold`` reads it), and ``approvals`` holds the user's
approvals (also appended to ``user_events``).
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

try:
    from . import autocode_util as util, autocode_verify as verify
except ImportError:
    import autocode_util as util, autocode_verify as verify

KEY = "protected_tests"
STAGE = "protected_tests"
HOLD = "PAUSED_PROTECTED_TEST_CHANGED"

NOTES = {
    "validator": "\nPROTECTED TESTS: protected_tests records that the runner ran the original version of test files "
                 "that existed when the run started against the current code, and some fail there while their "
                 "edited versions pass. Report FAIL with one finding per weakened test: the product code must be "
                 "fixed to satisfy the original test, not the test changed to accept the code.\n",
    "owner": "\nPROTECTED TESTS: protected_tests lists weakened tests the user has not approved, so the runner "
             "will refuse completion. Do not request COMPLETE; return REWORK whose findings restore each "
             "original test and fix the product code.\n"}


def _current_hash(workspace, path):
    target = Path(workspace) / path
    return util.file_hash(target) if target.is_file() and not target.is_symlink() else None


def _owners(test, paths):
    """The protected files a failing test id belongs to; every file when the id does not say."""
    if len(paths) == 1:
        return paths
    found = [path for path in paths
             if test.startswith(str(PurePosixPath(path).with_suffix("")).replace("/", ".")) or path in test]
    return found or paths


def check(state, workspace, run_dir, *, base, framework, suite_command=None, dependencies_from=None,
          timeout=verify.DEFAULT_TIMEOUT, track=None):
    """Compare each edited protected test file with its original on the current code; save and return it.

    ``framework`` may be a zero-argument function, called only when an edited file has to be run."""
    workspace = Path(workspace)
    revision = util.snapshot(workspace)["revision"]
    record = state.setdefault(KEY, {})
    saved = record.get("latest") or {}
    if saved.get("source_revision") == revision and saved.get("base") == base:
        return saved
    result = {"source_revision": revision, "base": base, "checked_at": util.now(), "checks": {},
              "files": {}, "weakened": {}, "unverified": {}, "notes": []}
    try:
        changes = verify.changed_files(workspace, base)
    except RuntimeError as error:
        # Nothing is known to be edited, so nothing is blocked; the note says why nothing was checked.
        result.update(verdict=verify.UNVERIFIED, notes=[f"The base commit could not be compared: {error}"])
        record["latest"] = result
        return result
    protected = {path: kind for path, kind in changes.items() if kind != "added" and verify.is_test_path(path)}
    result["files"] = {path: _current_hash(workspace, path) for path in sorted(protected)}
    result["weakened"] = {path: ["the file was deleted"] for path, kind in sorted(protected.items())
                          if kind == "deleted"}
    runnable = sorted(path for path, kind in protected.items() if kind != "deleted")
    if runnable:
        framework = framework() if callable(framework) else framework
        out = Path(run_dir) / "protected-tests" / revision[:12]
        if track:
            with track("Running edited protected tests in their original form"):
                _compare(result, workspace, base, changes, runnable, out, framework, suite_command,
                         dependencies_from, timeout)
        else:
            _compare(result, workspace, base, changes, runnable, out, framework, suite_command,
                     dependencies_from, timeout)
    result["verdict"] = verify.FAIL if result["weakened"] else verify.UNVERIFIED if result["unverified"] \
        else verify.PASS
    for path in (*result["weakened"], *result["unverified"]):
        revisions = record.setdefault("flagged", {}).setdefault(path, [])
        if revision not in revisions:
            revisions.append(revision)
    record["latest"] = result
    return result


def _compare(result, workspace, base, changes, paths, out, framework, suite_command, dependencies_from, timeout):
    command = (framework.targeted(paths) if framework else None) or suite_command \
        or (framework.suite if framework else None)
    if not command:
        for path in paths:
            result["unverified"][path] = "no test command was found to run it in its original form"
        return
    # Identical product code in both trees: the only difference is the protected test files themselves.
    originals = {path: kind for path, kind in changes.items() if path not in paths}
    trees = {}
    try:
        trees["edited"] = verify.make_tree(workspace, base, out / "edited", workspace, changes,
                                           dependencies_from=dependencies_from)
        trees["original"] = verify.make_tree(workspace, base, out / "original", workspace, originals,
                                             dependencies_from=dependencies_from)
        edited = verify.run_suite(framework, command, trees["edited"], out, "edited", timeout=timeout)
        original = verify.run_suite(framework, command, trees["original"], out, "original", timeout=timeout)
    except (OSError, RuntimeError, ValueError) as error:
        for path in paths:
            result["unverified"][path] = f"the original version could not be run: {error}"
        return
    finally:
        for tree in trees.values():
            verify.remove_tree(workspace, tree)
    result["checks"] = {label: {key: receipt[key] for key in ("command", "exit_code", "timed_out", "output")}
                        for label, receipt in (("edited", edited), ("original", original))}
    if edited["timed_out"] or original["timed_out"]:
        for path in paths:
            result["unverified"][path] = "a test run timed out"
        return
    before, after = original.get("results"), edited.get("results")
    if before and after and before.get("complete") and after.get("complete"):
        still_failing = set(after["failed"])
        for test in before["failed"]:
            if test not in still_failing:
                for path in _owners(test, paths):
                    result["weakened"].setdefault(path, []).append(
                        f"{test} fails in its original form but not as edited")
    elif original["exit_code"] != 0 and edited["exit_code"] == 0:
        for path in paths:
            result["weakened"][path] = ["the original version fails on the current code; the edited version passes"]
    elif original["exit_code"] != 0:
        for path in paths:
            result["unverified"][path] = ("both versions fail and the run reported no per-test results, "
                                          "so a weakened test cannot be ruled out")


def approved(state, path, digest):
    return any(row.get("path") == path and row.get("sha256") == digest
               for row in (state.get(KEY) or {}).get("approvals", []))


def outstanding(state, result=None):
    """Flagged protected test files the user has not approved at the contents the runner checked."""
    result = result if result is not None else (state.get(KEY) or {}).get("latest") or {}
    files = result.get("files") or {}
    return sorted(path for path in {*(result.get("weakened") or {}), *(result.get("unverified") or {})}
                  if not approved(state, path, files.get(path)))


def complete(state, current_revision):
    """True when no check ran, or the check for exactly this source has nothing unapproved.

    A check of an older source blocks only when it saw edited protected tests, as the regression
    proof does: the Validator, and so this check, always runs between a Builder and completion."""
    latest = (state.get(KEY) or {}).get("latest")
    if not latest:
        return True
    if latest.get("source_revision") != current_revision:
        return not latest.get("files")
    return not outstanding(state, latest)


def message(state, result=None):
    result = result if result is not None else (state.get(KEY) or {}).get("latest") or {}
    paths = outstanding(state, result)
    if not paths:
        return "the protected-test check has not run for the current source"
    details = "; ".join(f"{path}: " + ", ".join((result.get("weakened") or {}).get(path)
                                                  or [(result.get("unverified") or {}).get(path, "")])
                        for path in paths)
    return (f"Tests that existed when the run started were weakened or could not be re-checked ({details}). "
            "Restore the original tests and fix the product code. If the change to a test is intended, "
            "approve that exact edit with --approve-test-change PATH, then resume with --resume-paused")


def hold(state, result):
    """A pause when a flagged test is still unapproved after an earlier source revision was flagged too."""
    flagged = (state.get(KEY) or {}).get("flagged") or {}
    repeated = [path for path in outstanding(state, result)
                if any(revision != result["source_revision"] for revision in flagged.get(path, []))]
    if not repeated:
        return None
    return util.Paused(HOLD, "The rework left protected tests weakened again: " + message(state, result))


def rejection(state):
    return "Completion rejected: " + message(state)


def handoff(state):
    """What the Validator and Completion Owner see, when any protected test file was edited."""
    latest = (state.get(KEY) or {}).get("latest")
    if not latest or not latest.get("files"):
        return None
    files = latest["files"]
    return {"verdict": latest["verdict"], "source_revision": latest["source_revision"], "base": latest["base"],
            "edited_files": sorted(files), "weakened": latest["weakened"], "unverified": latest["unverified"],
            "approved_by_user": sorted(path for path in files if approved(state, path, files[path])),
            "outstanding": outstanding(state, latest), "checks": latest.get("checks", {}),
            "meaning": "Executed by the runner: each edited test file that existed at the run's base commit "
                       "was run in its original and edited forms over the current code. A test that fails "
                       "only in its original form was weakened. Outstanding entries block completion."}


def note(state, role):
    latest = (state.get(KEY) or {}).get("latest")
    return NOTES[role] if latest and outstanding(state, latest) else ""


def approve(state, path, workspace, at):
    """The user accepts one edited protected test file exactly as the runner last checked it."""
    workspace = Path(workspace).resolve()
    given = Path(path)
    if given.is_absolute():
        given = given.resolve().relative_to(workspace)
    relative = PurePosixPath(given.as_posix()).as_posix()
    latest = (state.get(KEY) or {}).get("latest") or {}
    files = latest.get("files") or {}
    if relative not in files:
        raise ValueError(f"{relative} is not an edited protected test in the runner's latest check")
    digest = _current_hash(workspace, relative)
    if digest != files[relative]:
        raise ValueError(f"{relative} changed after the runner checked it; resume so it is checked again first")
    event = {"kind": "test_change_approval", "actor": "user_cli", "path": relative, "sha256": digest, "at": at,
             "source_revision": latest.get("source_revision")}
    state.setdefault(KEY, {}).setdefault("approvals", []).append(event)
    state.setdefault("user_events", []).append(event)
    return event

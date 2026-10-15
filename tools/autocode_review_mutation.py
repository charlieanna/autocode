"""Owned, report-only mutation study of tests delivered by a reviewed patch.

The patch and its current preimages are captured before copying. Only effective
changed Python product lines are mutated, and only passing tests observed in
added/modified ordinary Python test files from that patch are selected. This is
an observation, never named-proof authority or a review verdict.
"""

from __future__ import annotations

import difflib
import hashlib
import math
import os
import shlex
import stat
import time
import uuid
from pathlib import Path, PurePosixPath

try:
    from . import autocode_command_receipt as receipts
    from . import autocode_diff_coverage as diff_coverage
    from . import autocode_mutants as mutants
    from . import autocode_mutation_check as mutation
    from . import autocode_python_tests as python_tests
    from . import autocode_util as util
    from . import autocode_verification_preparation as preparation
except ImportError:
    import autocode_command_receipt as receipts
    import autocode_diff_coverage as diff_coverage
    import autocode_mutants as mutants
    import autocode_mutation_check as mutation
    import autocode_python_tests as python_tests
    import autocode_util as util
    import autocode_verification_preparation as preparation

BUDGET_SECONDS = 900
MAX_PATCH_BYTES = 2 * 1024 * 1024
MAX_INPUT_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_PATHS = 64
PRIVATE_PARTS = frozenset({".git", ".autocode", ".scenario-runs", ".codex", ".claude", ".kilo"})
NO_RUN_REASONS = frozenset(
    {"no_patch", "invalid_input", "no_tests", "no_product", "no_framework", "unselectable", "no_sites", "budget"}
)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _path(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("patch paths must be ordinary relative POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(part in {".", ".."} | PRIVATE_PARTS for part in path.parts):
        raise ValueError("patch path is outside the public project")
    return value


def _read(root, relative, *, missing=False, limit=MAX_INPUT_BYTES):
    """Read a bounded physical regular input through no-follow directory FDs."""
    root, relative = Path(root), _path(relative)
    if root.is_symlink() or root != root.resolve():
        raise ValueError("input root is not physical and canonical")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        try:
            leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        except FileNotFoundError:
            if missing:
                return None
            raise
        try:
            before = os.fstat(leaf)
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError("input is not a bounded regular file")
            with os.fdopen(leaf, "rb", closefd=False) as stream:
                data = stream.read(limit + 1)
            after = os.fstat(leaf)
            if len(data) > limit or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("input changed while being captured")
            return data
        finally:
            os.close(leaf)
    except FileNotFoundError:
        if missing:
            return None
        raise
    finally:
        os.close(descriptor)


def _patch_files(data):
    """Conservatively accept ordinary Git text additions/modifications/deletions."""
    text = data.decode("utf-8")
    sections, row = [], None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            words = shlex.split(line)
            if len(words) != 4 or not words[2].startswith("a/") or not words[3].startswith("b/"):
                raise ValueError("unsupported patch header")
            old, new = _path(words[2][2:]), _path(words[3][2:])
            if old != new or any(item["path"] == new for item in sections):
                raise ValueError("renamed or duplicate patch path")
            row = {"path": new, "status": "modified", "old_header": False, "new_header": False}
            sections.append(row)
        elif line.startswith(
            ("rename ", "copy ", "similarity ", "dissimilarity ", "Binary files ", "GIT binary patch")
        ):
            raise ValueError("only ordinary text patches are supported")
        elif line.startswith(("new file mode ", "deleted file mode ", "old mode ", "new mode ")):
            if line.split()[-1] not in ("100644", "100755"):
                raise ValueError("patch changes a nonregular file")
        elif row is not None and line.startswith(("--- ", "+++ ")):
            old_header = line.startswith("--- ")
            name = line[4:]
            expected = ("a/" if old_header else "b/") + row["path"]
            if name not in (expected, "/dev/null"):
                raise ValueError("patch headers disagree")
            row["old_header" if old_header else "new_header"] = True
            if name == "/dev/null":
                row = {**row, "status": "added" if old_header else "deleted"}
                sections[-1] = row
    if not sections or len(sections) > MAX_PATHS or not all(r["old_header"] and r["new_header"] for r in sections):
        raise ValueError("patch has no bounded ordinary text changes")
    return [{"path": row["path"], "status": row["status"]} for row in sections]


def capture_inputs(workspace, patch, out):
    """Capture the explicit patch and all preimages before creating a scratch copy."""
    workspace, out = Path(workspace).resolve(), Path(out).resolve()
    supplied = Path(patch)
    relative = supplied.relative_to(workspace).as_posix() if supplied.is_absolute() else supplied.as_posix()
    relative = _path(relative)
    data = _read(workspace, relative, limit=MAX_PATCH_BYTES)
    changes = _patch_files(data)
    before, size = {}, 0
    for row in changes:
        value = _read(workspace, row["path"], missing=True)
        if row["status"] == "added" and value is not None:
            raise ValueError("added patch path already exists")
        if row["status"] != "added" and value is None:
            raise ValueError("patch preimage is missing")
        size += len(value or b"")
        if size > MAX_TOTAL_BYTES:
            raise ValueError("patch preimages exceed the capture bound")
        before[row["path"]] = value
    captured = out / ("reviewed-" + uuid.uuid4().hex + ".patch")
    with captured.open("xb") as stream:
        stream.write(data)
    binding = {
        "workspace": workspace,
        "patch_path": relative,
        "patch_sha256": _sha(data),
        "captured_patch": captured,
        "changes": changes,
        "before": before,
    }
    verify_inputs(binding, workspace)
    util.atomic_json(
        out / "review-inputs.json",
        {
            "patch_path": relative,
            "patch_sha256": binding["patch_sha256"],
            "captured_patch": str(captured),
            "inputs": [
                {**row, "sha256": _sha(before[row["path"]]) if before[row["path"]] is not None else None}
                for row in changes
            ],
        },
    )
    return binding


def verify_inputs(binding, root):
    """Bookend the raw patch, captured bytes and copied/current preimages."""
    if _sha(_read(binding["workspace"], binding["patch_path"], limit=MAX_PATCH_BYTES)) != binding["patch_sha256"]:
        raise ValueError("reviewed patch changed after capture")
    captured = binding["captured_patch"]
    if _sha(_read(captured.parent, captured.name, limit=MAX_PATCH_BYTES)) != binding["patch_sha256"]:
        raise ValueError("captured patch changed")
    for path, data in binding["before"].items():
        if _read(Path(root).resolve(), path, missing=True) != data:
            raise ValueError("review input changed before or during copying: " + path)


def _check(note=None):
    return {
        **mutation.record(note),
        "subject": "reviewed_patch_tests",
        "patch_path": None,
        "patch_sha256": None,
        "selected_files": [],
        "selected_tests": [],
        "survivors": {},
        "command_receipts": [],
    }


def _envelope(check, reason, started=0, *, held=False):
    return {
        "schema": 1,
        "outcome": "held" if held else "collected" if started else "not_run",
        "reason_code": reason,
        "project_commands_started": started,
        "check": check,
    }


def _compact(receipt):
    keys = (
        "command",
        "exit_code",
        "timed_out",
        "duration_seconds",
        "error",
        "interrupted",
        "output",
        "output_sha256",
        *receipts.OWNERSHIP_FIELDS,
    )
    return {key: receipt[key] for key in keys if key in receipt}


def known_command(receipt, out):
    """Admit normal collected exits or a closed deadline as a nonpositive observation."""
    if not isinstance(receipt, dict) or not all(key in receipt for key in receipts.OWNERSHIP_FIELDS):
        raise receipts.OwnershipUncertain("Review mutation command has no authenticated ownership receipt")
    output = receipt.get("output")
    try:
        relative = Path(output).relative_to(Path(out).resolve()).as_posix()
        data = _read(Path(out).resolve(), relative, limit=64 * 1024 * 1024)
        if _sha(data) != receipt.get("output_sha256"):
            raise ValueError("command output changed")
    except (TypeError, ValueError, OSError) as error:
        raise receipts.OwnershipUncertain("Review mutation command output is unbound") from error
    if receipts.completed({**receipt, "error": ""}, root=out):
        return True
    terminal = receipts.load(receipt.get("supervision"), root=out, pin=receipt.get("supervision_sha256"))
    if (
        terminal
        and terminal["phase"] == "stopped"
        and not terminal["cleanup_error"]
        and terminal["cause"] == "stage_deadline"
        and receipt.get("timed_out") is True
        and receipt.get("exit_code") is None
        and receipt.get("supervision_errors") == []
        and not receipt.get("interrupted")
    ):
        return True
    raise receipts.OwnershipUncertain("Review mutation command ownership is not collected or terminal")


def validate(envelope, out):
    """Validate a bound worker outcome, including every executed command's custody."""
    if not isinstance(envelope, dict) or envelope.get("schema") != 1:
        raise receipts.OwnershipUncertain("Review mutation worker did not return a typed result")
    check, started = envelope.get("check"), envelope.get("project_commands_started")
    if envelope.get("outcome") == "held":
        raise receipts.OwnershipUncertain("Review mutation worker reported uncertain command cleanup")
    if (
        not isinstance(check, dict)
        or check.get("subject") != "reviewed_patch_tests"
        or type(check.get("collected")) is not bool
        or type(started) is not int
    ):
        raise receipts.OwnershipUncertain("Review mutation result is malformed")
    commands = check.get("command_receipts")
    if (
        not isinstance(commands, list)
        or not 0 <= started <= MAX_PATHS * mutants.PER_FILE_LIMIT + 2
        or len(commands) != started
    ):
        raise receipts.OwnershipUncertain("Review mutation result omits an executed command")
    if envelope.get("outcome") == "not_run":
        if started or check.get("collected") is not False or envelope.get("reason_code") not in NO_RUN_REASONS:
            raise receipts.OwnershipUncertain("Review mutation no-run result is not an absence observation")
    elif envelope.get("outcome") == "collected" and started:
        for command in commands:
            known_command(command, out)
    else:
        raise receipts.OwnershipUncertain("Review mutation outcome is unknown")
    provenance = envelope.get("preparation")
    if provenance is not None:
        if not isinstance(provenance, dict):
            raise receipts.OwnershipUncertain("Review mutation preparation provenance is malformed")
        known_command(provenance.get("receipt"), out)
        for name in ("request", "result"):
            try:
                path = Path(provenance[name])
                data = _read(
                    Path(out).resolve(), path.relative_to(Path(out).resolve()).as_posix(), limit=12 * 1024 * 1024
                )
                if _sha(data) != provenance[name + "_sha256"]:
                    raise ValueError("preparation evidence changed")
            except (OSError, TypeError, ValueError, KeyError) as error:
                raise receipts.OwnershipUncertain("Review mutation preparation evidence changed") from error
        check["preparation"] = provenance
    return check


def _targeted(framework, files):
    command = framework.targeted(files)
    parsed = python_tests.parse(command)
    if parsed is None or parsed.kind != framework.name:
        return None
    # Invocation.targeted can fall back to a whole suite for unknown option arity.
    # Only exact file selection is admissible for this separate review observation.
    flags = {
        "-q",
        "-v",
        "-vv",
        "-s",
        "-x",
        "--verbose",
        "--quiet",
        "--disable-warnings",
        "--strict-markers",
        "--strict-config",
        "--continue-on-collection-errors",
    }
    values = {
        "-p",
        "-o",
        "-c",
        "--override-ini",
        "--rootdir",
        "--confcutdir",
        "--import-mode",
        "--tb",
        "--assert",
        "--maxfail",
        "--color",
        "--capture",
    }
    targets, index = [], 0
    args = parsed.arguments
    while index < len(args):
        word = args[index]
        if word in values:
            if index + 1 >= len(args):
                return None
            index += 2
            continue
        if word in flags or ("=" in word and word.split("=", 1)[0] in values):
            index += 1
            continue
        if word.startswith("-"):
            return None
        targets.append(word)
        index += 1
    return command if sorted(targets) == sorted(files) else None


def _selected_names(results, framework, files):
    if not isinstance(results, dict) or results.get("complete") is not True:
        return []
    if any(results.get(key) for key in ("failed", "skipped", "collection_errors", "uncollected")):
        return []
    names = results.get("passed")
    if not isinstance(names, list) or not names or any(not isinstance(name, str) or not name for name in names):
        return []
    modules = [path[:-3].replace("/", ".") for path in files]
    prefixes = (
        [prefix for module in modules for prefix in (module + "::", module + ".")]
        if framework == "pytest"
        else [module + "." for module in modules]
    )
    if any(not any(name.startswith(prefix) for prefix in prefixes) for name in names):
        return []
    return sorted(set(names))


def run_scratch(workspace, out, *, patch, deadline, prepare, detect, execute, remove, is_test, clock=time.monotonic):
    """Worker implementation; callbacks keep this module below the verification runner.

    prepare(captured_patch, before_patch) invokes before_patch(tree) before applying
    the captured patch; execute is verify.run_suite's public callback signature.
    An uncertain result deliberately retains the tree and its current mutant.
    """
    workspace, out = Path(workspace).resolve(), Path(out).resolve()
    maximum = clock() + BUDGET_SECONDS
    if not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or not math.isfinite(deadline):
        deadline = maximum
    deadline = min(deadline, maximum)
    check, tree, uncertain, started = _check(), None, False, 0
    reason = "invalid_input"
    try:
        if not patch:
            return _envelope(
                _check("No explicit patch was supplied; the review mutation check ran no test."), "no_patch"
            )
        if deadline <= clock():
            return _envelope(_check("The 900-second review mutation budget expired before preparation."), "budget")
        binding = capture_inputs(workspace, patch, out)
        check.update(patch_path=binding["patch_path"], patch_sha256=binding["patch_sha256"])
        tree = prepare(binding["captured_patch"], lambda copy: verify_inputs(binding, copy))
        tree = Path(tree).resolve()
        verify_inputs(binding, workspace)
        after, diffs, product, selected = {}, {}, [], []
        for row in binding["changes"]:
            path = row["path"]
            current = _read(tree, path, missing=True)
            if row["status"] == "deleted" and current is not None or row["status"] != "deleted" and current is None:
                raise ValueError("applied patch has an unexpected file status")
            after[path] = current
            before = binding["before"][path]
            if before == current or row["status"] == "deleted" or path.startswith("review/"):
                continue
            if python_tests.TEST_MODULE.fullmatch(PurePosixPath(path).name):
                selected.append(path)
            elif path.endswith((".py", ".pyi")) and not is_test(path):
                text = (
                    "diff --git a/"
                    + path
                    + " b/"
                    + path
                    + "\n"
                    + "".join(
                        difflib.unified_diff(
                            (before or b"").decode("utf-8").splitlines(keepends=True),
                            current.decode("utf-8").splitlines(keepends=True),
                            fromfile="a/" + path,
                            tofile="b/" + path,
                            n=0,
                        )
                    )
                )
                diffs[path] = text
                product.append((path, row["status"]))
        selected.sort()
        check["selected_files"] = selected
        if not selected:
            return _envelope(
                {**check, "note": "No added or modified standard Python test file occurs in the patch."}, "no_tests"
            )
        if not product:
            return _envelope({**check, "note": "No changed Python product file occurs in the patch."}, "no_product")
        framework = detect(tree)
        if framework is None or framework.name not in mutation.FRAMEWORKS:
            return _envelope(
                {**check, "note": "The review mutation check supports named unittest and pytest results only."},
                "no_framework",
            )
        command = _targeted(framework, selected)
        if not command:
            return _envelope(
                {**check, "note": "The project command cannot select only the patch's test files."}, "unselectable"
            )
        planned = False
        for path, _ in product:
            changed = mutants.changed_lines(diff_coverage.hunks(diffs[path]))
            made, _ = mutants.python_mutants(path, after[path], set().union(*changed.values()))
            planned |= bool(made)
        if not planned:
            return _envelope({**check, "note": "No effective changed product line has a mutation site."}, "no_sites")

        def run(command, label, timeout):
            nonlocal started, uncertain
            remaining = deadline - clock()
            if remaining <= 0:
                raise ValueError("the 900-second review mutation budget expired")
            command_inputs = {path: _read(tree, path, missing=True) for path in after}
            started += 1
            try:
                receipt = execute(framework, command, tree, out, label, timeout=min(timeout, remaining))
                known_command(receipt, out)
            except BaseException as error:
                uncertain = True
                if isinstance(error, receipts.OwnershipUncertain):
                    raise
                raise receipts.OwnershipUncertain("Review mutation command did not return owned collection") from error
            check["command_receipts"].append(_compact(receipt))
            if any(_read(tree, path, missing=True) != value for path, value in command_inputs.items()):
                raise ValueError("the candidate or selected tests changed during a review mutation command")
            return receipt

        if deadline <= clock():
            return _envelope(
                {**check, "note": "The 900-second review mutation budget expired before discovery."}, "budget"
            )
        first = run(command, "review-selection", deadline - clock())
        selected_tests = _selected_names(first.get("results"), framework.name, selected)
        check["selected_tests"] = selected_tests
        if first.get("exit_code") != 0 or first.get("timed_out") or first.get("error") or not selected_tests:
            check["note"] = "The patch's test files did not supply complete passing named results; no mutant ran."
            reason = "selection_not_passed"
        else:
            observed = mutation.collect_selected_tests(
                framework.name,
                command,
                selected_tests,
                tree,
                out,
                product,
                lambda files: "".join(diffs[path] for path, _ in files),
                run,
                clock=clock,
                deadline=deadline,
            )
            check.update(observed)
            check["survivors"] = mutation.survivors(check, {name: [name] for name in selected_tests})
            reason = "observed"
        verify_inputs(binding, workspace)
        if any(_read(tree, path, missing=True) != value for path, value in after.items()):
            raise ValueError("the candidate changed during the review mutation check")
        return _envelope(check, reason, started)
    except receipts.OwnershipUncertain:
        uncertain = True
        check.update(
            collected=False, note="Review mutation command cleanup is uncertain; its tree is retained.", survivors={}
        )
        return _envelope(check, "ownership_uncertain", started, held=True)
    except util.Paused:
        uncertain = True
        raise
    except Exception as error:
        check.update(collected=False, note="The review mutation check stopped: " + str(error), files=[], survivors={})
        return _envelope(check, reason, started)
    finally:
        if tree is not None and not uncertain:
            remove(workspace, tree)


def collect(workspace, evidence_dir, patch, clean_run, *, clock=time.monotonic):
    """Run one bounded study through the existing scratch-copy/owned-worker path."""
    deadline = clock() + BUDGET_SECONDS
    out = Path(evidence_dir).resolve() / ("review-mutation-" + uuid.uuid4().hex)
    admission = preparation.allocate(
        out, {"subject": "reviewed_patch_tests", "patch": str(patch) if patch else None}, uuid.uuid4().hex, util.now()
    )
    try:
        with preparation.admitted(admission):
            answer = clean_run(
                workspace,
                out,
                patch=patch,
                timeout=max(0.001, deadline - clock()),
                review_mutation=True,
                review_mutation_deadline=deadline,
            )
        return validate(answer.get("review_mutation") if isinstance(answer, dict) else None, out)
    except util.Paused:
        raise
    except Exception as error:
        return _check("The review mutation check stopped: " + str(error))

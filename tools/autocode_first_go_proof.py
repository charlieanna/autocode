"""Bind the existing first-Go-project exception to its actual no-suite and test runs."""

import json
import os
import re
from pathlib import Path

try:
    from . import autocode_changed_code_measurement as measurement
    from . import autocode_command_receipt as command_receipt
    from . import autocode_first_suite as first_suite
    from . import autocode_go_tests as go_tests
    from . import autocode_package_scope as package_scope
    from . import autocode_verification_recovery as recovery
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_changed_code_measurement as measurement
    import autocode_command_receipt as command_receipt
    import autocode_first_suite as first_suite
    import autocode_go_tests as go_tests
    import autocode_package_scope as package_scope
    import autocode_verification_recovery as recovery
    import autocode_verification_schedule as schedule


def inputs_safe(tree, supply):
    """Refuse unrecorded ignored inputs and any extra copied or linked dependencies."""
    if not isinstance(tree, (str, Path)):
        return False
    if (
        supply is None
        or getattr(supply, "recorded", None) is not True
        or getattr(supply, "unverified", None) != []
        or getattr(supply, "generated", None) != {}
        or getattr(supply, "vendored", None) != {}
    ):
        return False

    def raise_walk_error(error):
        raise error

    try:
        tree = Path(tree)
        if tree.is_symlink() or not tree.is_dir():
            return False
        for directory, dirs, files in os.walk(tree, followlinks=False, onerror=raise_walk_error):
            for name in (*dirs, *files):
                if name in ("node_modules", ".venv", "venv", "vendor") or (Path(directory) / name).is_symlink():
                    return False
        return True
    except OSError:
        return False


def _command(framework, command):
    package, inner = package_scope.split(framework, command)
    parsed = go_tests.parse(inner)
    if parsed is None:
        return None
    result = parsed.instrument()
    return package_scope.command(package, result) if package else result


def _only_no_project_output(text):
    """Reject an unrelated tool failure even when it quotes a no-project phrase."""
    marker = False
    for raw in text.splitlines():
        if not raw.strip():
            continue
        line = raw
        if raw.startswith("{"):
            try:
                event = json.loads(raw)
            except ValueError:
                return False
            if (
                not isinstance(event, dict)
                or event.get("Action") not in ("build-output", "build-fail", "start", "output", "fail")
                or event.get("Test")
                or event.get("Package", "") not in ("", ".", "./...")
                or event.get("ImportPath", "") not in ("", ".", "./...")
                or event.get("FailedBuild", "") not in ("", ".", "./...")
            ):
                return False
            line = str(event.get("Output") or "").strip()
            if not line:
                continue
        line = line.strip()
        if (
            line in ('go: warning: "./..." matched no packages', "no packages to test")
            or line
            in (
                "go: go.mod file not found in current directory or any parent directory; see 'go help modules'",
                "go: cannot find main module; see 'go help modules'",
            )
            or re.fullmatch(
                r"pattern \./\.\.\.: directory prefix .* does not contain main module(?: or its selected dependencies)?",
                line,
            )
        ):
            marker = True
        elif line not in ("# ./...", "FAIL", "FAIL\t./... [setup failed]", "FAIL\t./... [build failed]"):
            return False
    return marker


def _capture(receipt, expected, root):
    if (
        not isinstance(receipt, dict)
        or set(command_receipt.OWNERSHIP_FIELDS) - set(receipt)
        or receipt.get("command") != expected
        or receipt.get("output_truncated")
        or not schedule.intact(receipt, root=root)
    ):
        return None
    try:
        execution_root = Path(receipt["execution_root"])
        scratch_root = execution_root.parent
        if scratch_root.name != "scratch":
            return None
        bound = recovery.bind_command(
            Path(receipt["output"]).parent,
            {**receipt, "processes": []},
            preparation_cwd=execution_root,
            scratch_root=scratch_root,
        )
        if (
            bound["pins"].get(receipt["supervision"]["receipt"]) != receipt["supervision_sha256"]
            or bound["output_sha256"] != receipt["output_sha256"]
        ):
            return None
        data = measurement.pinned(receipt.get("output"), receipt.get("output_sha256"), root)
        text = data.decode("utf-8", "replace")
        if receipt.get("tail") != data[-4000:].decode("utf-8", "replace"):
            return None
        observed = go_tests.results(text)
        if observed != receipt.get("results"):
            return None
        return {**receipt, "tail": text[-4000:], "results": observed}, text
    except (OSError, TypeError, ValueError, KeyError, recovery.receipts.OwnershipUncertain):
        return None


def _started_passes(text, passed):
    """Each credited named pass must have an earlier native run event."""
    started, observed = set(), set()
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict) or not event.get("Test"):
            continue
        key = f"{event.get('Package') or ''}::{event['Test']}"
        if event.get("Action") == "run":
            started.add(key)
        elif event.get("Action") == "pass":
            if key not in started:
                return False
            observed.add(key)
            started.remove(key)
    return bool(passed) and set(passed) <= observed


def absent(base_suite, base, suite_command, candidate, regression_command, framework, root, *, no_go_project):
    """Require both exact captured Go runs and the pinned inventory's no-project fact.

    This authorizes only the already-approved first-Go-project comparison, not a
    source-read boundary or any observation from a targeted original-code run.
    """
    if (
        no_go_project is not True
        or not base
        or not isinstance(base_suite, dict)
        or base_suite.get("base") != base
        or base_suite.get("command") != suite_command
        or getattr(framework, "name", None) != "go"
    ):
        return False
    before_command = _command(framework, suite_command)
    after_command = _command(framework, regression_command)
    if before_command is None or after_command is None:
        return False
    before = _capture(base_suite.get("receipt"), before_command, root)
    after = _capture(candidate, after_command, root)
    return bool(
        before
        and after
        and _only_no_project_output(before[1])
        and first_suite.absent_go_suite(before[0], after[0], no_go_project=True)
        and _started_passes(after[1], after[0]["results"]["passed"])
    )


def qualify(run, trees, candidate, no_go_project):
    """Hold, never execute the contaminated base, when first-Go evidence is unavailable."""
    framework = run.framework
    first_go = bool(no_go_project and run.new_behavior and not run.preserve and framework and framework.name == "go")
    if not first_go:
        return False, False
    proved = inputs_safe(trees.get("base_with_tests"), run.ignored_inputs) and absent(
        run.base_suite,
        run.base,
        run.commands["suite"],
        candidate,
        run.commands["regression"],
        framework,
        Path(run.run_dir).parent,
        no_go_project=True,
    )
    if not proved:
        run.unverified.append("The first Go project's pinned no-project baseline or candidate check is unavailable")
    return True, proved

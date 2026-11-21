"""Observed Python import failures and their candidate-side origin probes.

An import error is not by itself proof of new behavior. The runner records the
test that failed and probes the candidate's actual import origin before the
shared setup classifier can distinguish project code from a test dependency.
"""

from __future__ import annotations

import json
import re
import shlex
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from . import autocode_dependency_dirs as dependency_dirs
    from . import autocode_failure_provenance as failure_provenance
    from . import autocode_package_scope as package_scope
    from . import autocode_python_tests as python_tests
    from . import autocode_source_snapshot as source_snapshot
    from . import autocode_test_setup as test_setup
    from . import autocode_util as util
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_dependency_dirs as dependency_dirs
    import autocode_failure_provenance as failure_provenance
    import autocode_package_scope as package_scope
    import autocode_python_tests as python_tests
    import autocode_source_snapshot as source_snapshot
    import autocode_test_setup as test_setup
    import autocode_util as util
    import autocode_verification_schedule as schedule

_MISSING_NAME = re.compile(
    r"(?m)^\s*(?:E\s+)?ImportError: cannot import name '([A-Za-z_]\w*)' from '([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)'"
)
_MISSING_MODULE = re.compile(
    r"(?m)^\s*(?:E\s+)?ModuleNotFoundError: No module named '([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)'"
)

# This program runs only in the owned candidate scratch process. The controller
# never imports project modules; it verifies the reported origins itself.
_PROGRAM = """\
import importlib, json, sys
token, requests = sys.argv[1], json.loads(sys.argv[2])
for module, symbol in requests:
    origin = None
    try:
        loaded = importlib.import_module(module)
        if symbol:
            getattr(loaded, symbol)
        origin = getattr(getattr(loaded, '__spec__', None), 'origin', None)
    except Exception:
        pass
    print(token + json.dumps([module, symbol, origin]), flush=True)
"""


def _target(detail):
    names = [(module, name) for name, module in _MISSING_NAME.findall(detail)]
    names += [(module, "") for module in _MISSING_MODULE.findall(detail)]
    return names[0] if len(names) == 1 else None


def targets(receipt, tree, framework):
    """Observed failing test ID -> (module, export), from authenticated runner bytes."""
    try:
        root = Path(receipt["output"]).parent
        results = receipt.get("results") or {}
        failures = set(results.get("failed") or ())
        if (
            not schedule.intact(receipt, root=root)
            or not failures
            or receipt.get("timed_out")
            or receipt.get("error")
            or type(receipt.get("exit_code")) is not int
            or receipt["exit_code"] == 0
        ):
            return {}
        found = {}
        seen = set()
        if framework == "pytest":
            capture = receipt["results_capture"]
            data = failure_provenance.read_pinned(capture["path"], capture["sha256"])
            if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                return {}
            report = ET.fromstring(data)
            if report.tag not in ("testsuite", "testsuites"):
                return {}
            for test, case in python_tests.pytest_case_names(report.iter("testcase"), tree):
                errors = [node for node in case if node.tag in ("failure", "error")]
                if test in failures:
                    if test in seen:
                        return {}
                    seen.add(test)
                    if len(errors) == 1 and (target := _target(errors[0].text or "")):
                        found[test] = target
        elif framework == "unittest":
            data = failure_provenance.read_pinned(receipt["output"], receipt["output_sha256"])
            for name, owner, detail in test_setup.failure_details(data.decode(errors="replace")):
                test = python_tests._unittest_id(name, owner)
                if test in failures:
                    if test in seen:
                        return {}
                    seen.add(test)
                    if target := _target(detail):
                        found[test] = target
        return found
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ET.ParseError, RecursionError, OverflowError):
        return {}


def command(interpreter, requests, token, *, package=None):
    """The argument-quoted probe command; module and export names remain data."""
    from_command = shlex.join([interpreter, "-c", _PROGRAM, token, json.dumps(sorted(set(requests.values())))])
    return f"cd {shlex.quote(package)} && {from_command}" if package else from_command


def identity_command(framework, test_command, tree):
    """The exact runner interpreter, resolved from its test working directory."""
    if framework is None or framework.name not in ("pytest", "unittest"):
        return None
    package, inner = package_scope.split(framework, test_command)
    invocation = python_tests.parse(python_tests.verbose_unittest(inner))
    if invocation is None or invocation.kind != framework.name or invocation.prefix:
        return None
    interpreter = invocation.python
    path = Path(interpreter)
    if path.parent != Path(".") and not path.is_absolute():
        interpreter = str((Path(tree) / (package or "") / path).resolve())
    return shlex.join([interpreter, "-m", invocation.kind])


def project_targets(
    receipt, tree, requests, token, *, is_product, expected_environment, expected_source, source_before, source_after
):
    """Named failures whose probe resolves to an actual project Python source file."""
    try:
        output = Path(receipt["output"])
        if (
            receipt.get("exit_code") != 0
            or receipt.get("timed_out")
            or receipt.get("error")
            or not isinstance(expected_environment, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_environment)
            or receipt.get("execution_environment_sha256") != expected_environment
            or not isinstance(expected_source, str)
            or not expected_source
            or source_before != expected_source
            or source_after != expected_source
            or not schedule.intact(receipt, root=output.parent)
        ):
            return {}
        data = failure_provenance.read_pinned(output, receipt["output_sha256"]).decode(errors="replace")
        wanted = set(requests.values())
        origins = {}
        for line in data.splitlines():
            if not line.startswith(token):
                continue
            module, symbol, origin = json.loads(line[len(token) :])
            pair = (module, symbol)
            if pair not in wanted or pair in origins:
                return {}
            origins[pair] = origin
        if set(origins) != wanted:
            return {}
        root = Path(tree).resolve()
        proven = {}
        for test, pair in requests.items():
            if not isinstance(origins[pair], str):
                continue
            path = Path(origins[pair]).resolve()
            if not path.is_relative_to(root) or not path.is_file() or path.suffix not in (".py", ".pyi"):
                continue
            relative = path.relative_to(root).as_posix()
            if is_product(relative):
                proven[test] = {"module": pair[0], "symbol": pair[1], "origin": relative}
        return proven
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        return {}


def observe_candidate(
    base_receipt,
    base_tree,
    candidate_tree,
    framework,
    selected,
    test_command,
    out,
    *,
    run_suite,
    run_command,
    snapshot,
    identity,
    expected_source,
    is_product,
    timeout,
):
    """Rerun selected candidate tests and inspect imports in the same owned source and environment.

    Only this recorded observation may exempt a test-origin import failure. The
    caller owns the scratch trees and removes them after retaining the receipts.
    """
    if framework is None or framework.name not in ("pytest", "unittest"):
        return {}
    package, inner = package_scope.split(framework, test_command)
    invocation = python_tests.parse(python_tests.verbose_unittest(inner))
    if invocation is None or invocation.kind != framework.name or invocation.prefix:
        return {}
    requests = targets(base_receipt, base_tree, framework.name)
    if not requests:
        return {}
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    before = snapshot(candidate_tree)
    binding = identity(candidate_tree)
    attempt = {"imports": {}, "requests": requests, "source_revision": before, "execution_identity": binding}

    def finish(reason):
        attempt["reason"] = reason
        attempt["path"] = str(out / "observation.json")
        util.atomic_json(out / "observation.json", attempt)
        return attempt

    if before != expected_source or not isinstance(binding, dict) or binding.get("cache_binding_complete") is not True:
        return finish("Candidate source or runtime identity is not fully bound")
    candidate = run_suite(framework, test_command, candidate_tree, out, "candidate", timeout=timeout)
    attempt["candidate"] = candidate
    results = candidate.get("results") or {}
    if (
        not schedule.intact(candidate, root=out)
        or not schedule.complete_results(candidate)
        or candidate.get("exit_code") != 0
        or results.get("failed")
        or results.get("skipped")
        or not set(selected) <= set(results.get("passed") or ())
    ):
        return finish("Selected candidate tests did not produce complete passing evidence")
    between = snapshot(candidate_tree)
    if identity(candidate_tree) != binding:
        return finish("Candidate execution identity changed after the selected tests")
    token = "AUTOCODE_IMPORT_" + uuid.uuid4().hex + ":"
    probe = run_command(
        command(invocation.python, requests, token, package=package),
        candidate_tree,
        out / "probe.log",
        timeout=timeout,
    )
    attempt["probe"] = probe
    after = snapshot(candidate_tree)
    if identity(candidate_tree) != binding:
        return finish("Candidate execution identity changed after the import probe")
    project = project_targets(
        probe,
        candidate_tree,
        requests,
        token,
        is_product=is_product,
        expected_environment=candidate.get("execution_environment_sha256"),
        expected_source=before,
        source_before=between,
        source_after=after,
    )
    attempt["imports"] = project
    return finish("Project import origins observed" if project else "No project import origin was established")


def for_regression(run, trees, on_candidate, on_base, candidate_source, runners):
    """One project-import answer and retained checks for the final regression judgment."""
    run_suite, run_command, execution_identity = runners
    framework = run.framework
    if (
        not run.new_behavior
        or on_base is None
        or framework is None
        or framework.name not in ("pytest", "unittest")
        or "base_with_tests" not in trees
    ):
        return {}, None
    base_results = on_base.get("results") or {}
    selected = (on_candidate.get("results") or {}).get("passed") or []
    test_command = run.commands["regression"]
    candidate_tree = trees["candidate"]
    identity_cmd = identity_command(framework, test_command, candidate_tree)
    if not identity_cmd or not targets(on_base, trees["base_with_tests"], framework.name):
        return {}, ()
    snapshot = lambda tree: source_snapshot.snapshot(tree, paths=list(run.changes))["revision"]
    identity = lambda tree: execution_identity(
        tree,
        command=identity_cmd,
        dependencies_from=run.dependencies_from,
        source_paths=list(run.changes),
        generated_record=run.generated_record,
        generated_unrecorded=run.generated_unrecorded,
        ignored_inputs=run.ignored_inputs,
    )
    observed = observe_candidate(
        on_base,
        trees["base_with_tests"],
        candidate_tree,
        framework,
        selected,
        test_command,
        Path(run.run_dir) / "project-import-observation",
        run_suite=run_suite,
        run_command=run_command,
        snapshot=snapshot,
        identity=identity,
        expected_source=candidate_source,
        is_product=lambda path: not run.roles.is_test(path) and not dependency_dirs.installed(path),
        timeout=run.timeout,
    )
    if observed:
        run.proof["project_import_observation"] = observed
        for key in ("candidate", "probe"):
            if key in observed:
                run.checks["project_import_" + key] = observed[key]
    project_imports = observed.get("imports") or {}
    unloaded = test_setup.unloaded_failures(
        base_results,
        run.runnable_tests,
        selected,
        new_behavior=True,
        directory=getattr(framework, "directory", None),
        project_imports=project_imports,
    )
    return project_imports, unloaded or ()

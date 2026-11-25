"""Fail-closed source-resolution observations for named proof receipts."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import sys
from functools import partial
from pathlib import Path

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_go_tests as go_tests
    from . import autocode_node_tests as node_tests
    from . import autocode_npm_scripts as npm_scripts
    from . import autocode_project_kinds as project_kinds
    from . import autocode_python_tests as python_tests
    from . import autocode_util as util
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_go_tests as go_tests
    import autocode_node_tests as node_tests
    import autocode_npm_scripts as npm_scripts
    import autocode_project_kinds as project_kinds
    import autocode_python_tests as python_tests
    import autocode_util as util

VERSION = 1
FRAMEWORKS = frozenset({"go", "node", "pytest", "unittest"})
MAX_CAPTURE_BYTES = 2 * 1024 * 1024
PROBE_FILES = (
    "autocode_source_probe.py",
    "autocode_python_tests.py",
    "autocode_project_kinds.py",
    "autocode_shell_syntax.py",
    "autocode_test_setup.py",
)


# A binding whose observer could not see where the runner resolved source, while it saw nothing
# resolve outside the owning copy (owner decision 2026-11-25, "hold only observed escapes"). It is
# disclosed in the receipt and keeps the proof's existing admission: only an observed escape, an
# incomplete probe, or a missing, malformed or changed observation holds a proof.
UNOBSERVED = "unobserved"
INCOMPLETE = "the source-resolution probe did not complete normally"
# What the observer reports when it saw source resolve outside the owning copy, or saw an input it
# classified change before or while it observed it. Every other observer error says what it could not see.
HOLDS = ("live workspace", "outside its owning copy", "outside its family", "changed after", "changed during")


def holds(error):
    """Whether one observation error reports an observed escape or a changed input, not a blind spot."""
    return error == INCOMPLETE or any(marker in error for marker in HOLDS)


def selected(label):
    return label.startswith(
        (
            "regression-",
            "suite-",
            "stability-",
            "guard-",
            "guards-",
            "plan-guards",
            "listed-tests-",
            "scratch-tests",
            "scratch-command",
            "flake-",
            "pre-existing-",
            "follow-up-baseline",
            "original-reference",
            "unmutated",
            "mutant-",
            "selected-suite",
        )
    ) or label in ("candidate", "original", "original-recheck")


def admission_required(command):
    """Whether ``command`` names a runner whose source resolution this detector observes."""
    return _command_framework(command, None) is not None


def applicable(binding):
    """Whether a binding belongs to a runner this phase is authorized to judge."""
    if not isinstance(binding, dict):
        return True
    if binding.get("status") == UNOBSERVED:
        return False
    children = binding.get("children")
    if isinstance(children, list):
        return any(applicable(child) for child in children)
    if "framework" not in binding:
        return True
    return binding["framework"] in FRAMEWORKS


def _owner(receipt):
    if not isinstance(receipt, dict):
        return None
    try:
        return {
            "execution_root": str(Path(receipt["execution_root"]).resolve()),
            "command": receipt.get("command"),
            "output": str(Path(receipt["output"]).resolve()),
            "output_sha256": receipt.get("output_sha256"),
        }
    except (KeyError, OSError, TypeError, ValueError):
        return None


def attach(receipt, binding):
    """Bind one observation to the exact owned invocation it accompanies."""
    if binding is None:
        return receipt
    owner = _owner(receipt)
    if not isinstance(binding, dict) or "tree" not in binding or owner is None:
        raise ValueError("source-resolution binding has no owning proof receipt")
    try:
        belongs = Path(binding["tree"]).resolve() == Path(owner["execution_root"])
    except (OSError, TypeError, ValueError):
        belongs = False
    if not belongs:
        raise ValueError("source-resolution binding does not belong to its proof receipt")
    attached = dict(binding, owner=owner)
    receipt["source_binding"] = attached
    receipt["source_binding_required"] = True
    return receipt


def attach_observation(
    receipt,
    framework,
    command,
    tree,
    root,
    evidence_dir,
    label,
    *,
    workspace,
    execute,
    timeout,
    classification,
):
    binding = observe(
        framework,
        command,
        tree,
        root,
        evidence_dir,
        label,
        workspace=workspace,
        execute=execute,
        timeout=timeout,
        classification=classification,
    )
    return attach(receipt, binding)


def attach_execution(
    receipt,
    framework,
    command,
    tree,
    root,
    evidence_dir,
    label,
    *,
    workspace,
    execute,
    timeout,
    classification,
    native,
):
    if native:
        binding = unsupported(framework, command, tree, workspace) if selected(label) else None
        return attach(receipt, binding)
    return attach_observation(
        receipt,
        framework,
        command,
        tree,
        root,
        evidence_dir,
        label,
        workspace=workspace,
        execute=execute,
        timeout=timeout,
        classification=classification,
    )


def established(receipt):
    if not isinstance(receipt, dict):
        return False
    if "source_binding" not in receipt:
        return not receipt.get("source_binding_required")
    binding = receipt["source_binding"]
    if not isinstance(binding, dict) or binding.get("status") != "established":
        return False
    if receipt.get("source_binding_required") and binding.get("owner") != _owner(receipt):
        return False
    if "children" in binding:
        children = binding["children"]
        suites = receipt.get("suites")
        return (
            isinstance(children, list)
            and isinstance(suites, list)
            and len(children) == len(suites) == binding.get("suite_count")
            and all(established(child) for child in suites)
        )
    probe = binding.get("probe")
    return command_receipt.completed(probe) and not probe.get("timed_out") and probe.get("exit_code") == 0


def unsupported(framework, command, tree, workspace):
    name = getattr(framework, "name", None) or "unknown"
    return {
        "version": VERSION,
        "framework": name,
        "command": command,
        "status": "unverified",
        "reason": f"source resolution is not observed for the {name} proof runner",
        "tree": str(Path(tree).resolve()),
        "workspace": str(Path(workspace).resolve()) if workspace else None,
        "observations": [],
    }


def merged(receipts):
    receipts = list(receipts)
    if not any("source_binding" in receipt for receipt in receipts):
        return None
    failed = next((run for run in receipts if run.get("exit_code") or run.get("timed_out")), receipts[0])
    children = [receipt.get("source_binding") for receipt in receipts]
    first = failed.get("source_binding") or {}
    unsettled = [index for index in range(1, len(receipts) + 1) if not established(receipts[index - 1])]
    reasons = [
        f"suite {index}: "
        + (children[index - 1].get("reason") if isinstance(children[index - 1], dict) else "source binding is missing")
        for index in unsettled
    ]
    held = any(applicable(children[index - 1]) for index in unsettled)
    return {
        "version": VERSION,
        "framework": "merged",
        "command": " ; ".join(receipt.get("command") or "" for receipt in receipts),
        "status": ("unverified" if held else UNOBSERVED) if reasons else "established",
        "reason": "; ".join(reasons),
        "tree": failed.get("execution_root") or first.get("tree"),
        "workspace": first.get("workspace") if isinstance(first, dict) else None,
        "observations": [],
        "suite_count": len(receipts),
        "children": children,
    }


def _inside(path, root):
    return root is not None and path.is_relative_to(root)


def assess(result, tree, workspace):
    """One pure answer shared by observation, judgment and receipt authentication."""
    tree = Path(tree).resolve()
    workspace = Path(workspace).resolve() if workspace else None
    errors, unseen, roots = [], [], []
    if not isinstance(result, dict):
        result = {}
        errors.append("source-resolution capture is malformed")
    raw_errors = result.get("errors")
    if isinstance(raw_errors, list):
        for error in map(str, raw_errors):
            (errors if holds(error) else unseen).append(error)
    else:
        errors.append("source-resolution errors are malformed")
    raw_observations = result.get("observations")
    observations = raw_observations if isinstance(raw_observations, list) else []
    if not isinstance(raw_observations, list) or any(not isinstance(row, dict) for row in observations):
        errors.append("source-resolution observations are malformed")
        observations = [row for row in observations if isinstance(row, dict)]
    if not observations and not errors and not unseen:
        unseen.append("the runner resolved no observable source")
    allowed_roles = {
        "go_root": {"root"},
        "go_module": {"root"},
        "go_package": {"source"},
        "go_dependency": {"dependency"},
        "node_root": {"root"},
        "node_package": {"root"},
        "node_self": {"source"},
        "node_test": {"test"},
        "node_import": {"source", "dependency"},
        "node_source": {"source"},
        "python_root": {"root"},
        "python_test": {"test"},
        "python_source": {"source"},
        "python_module": {"source", "dependency"},
        "pytest_rootdir": {"root"},
        "pytest_pythonpath": {"root"},
    }
    for row in observations:
        kind, role = row.get("kind"), row.get("role")
        if (
            not isinstance(kind, str)
            or not isinstance(role, str)
            or kind not in allowed_roles
            or role not in allowed_roles[kind]
        ):
            errors.append(f"{kind or 'source'} has an invalid {role or 'role'} role")
            continue
        value = row.get("path")
        if value is None:
            if kind not in {"python_module", "node_self", "node_import"} or role != "source":
                unseen.append(f"{kind} resolved without an observable origin")
            continue
        if value in ("built-in", "frozen") and kind == "python_module" and role == "dependency":
            continue
        if str(value).startswith("node:") and kind == "node_import" and role == "dependency":
            continue
        try:
            path = Path(value).resolve()
        except (OSError, TypeError, ValueError):
            unseen.append(f"{kind} path could not be resolved: {value}")
            continue
        if _inside(path, tree):
            continue
        if workspace is not None and _inside(path, workspace):
            (errors if role != "root" else roots).append(f"{kind} resolved into the live workspace: {path}")
        elif role != "dependency":
            (errors if role != "root" else roots).append(f"{kind} resolved outside its owning copy: {path}")
    # A root outside the copy is an escape once the runner loaded a test or source under it. When it
    # loaded none (Go's "matched no packages" under an ancestor module), no outside code ran, and the
    # root is disclosed beside what the observer could not see.
    loaded = any(row.get("role") in ("source", "test") and row.get("path") is not None for row in observations)
    (errors if loaded else unseen).extend(roots)
    return {
        "version": VERSION,
        "status": "unverified" if errors else UNOBSERVED if unseen else "established",
        "reason": "; ".join(dict.fromkeys([*errors, *unseen])),
        "tree": str(tree),
        "workspace": str(workspace) if workspace else None,
        "observations": observations,
    }


def _capture(path, *, digest=None, root=None):
    path = Path(path)
    if digest is not None:
        data = command_receipt.pinned_bytes(str(path), digest, root, limit=MAX_CAPTURE_BYTES)
    else:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CAPTURE_BYTES:
            raise ValueError("source-resolution capture is not a bounded regular file")
        data = path.read_bytes()
    if len(data) > MAX_CAPTURE_BYTES:
        raise ValueError("source-resolution capture is too large")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("source-resolution capture is not an object")
    return value, hashlib.sha256(data).hexdigest()


def runner(run_suite, workspace):
    """Bind every proof command in one workspace to its live-source exclusion boundary."""
    return partial(run_suite, source_workspace=workspace)


def _command_framework(command, root):
    if go_tests.parse(command) is not None:
        return "go"
    if node_tests.command_words(command) is not None or npm_scripts.test_invocation(command) is not None:
        return "node"
    invocation = python_tests.parse(command, include_owned=True)
    return invocation.kind if invocation is not None and invocation.kind in ("pytest", "unittest") else None


def retain_probe(evidence_dir, label):
    """Retain the observer and its import closure before project code executes."""
    directory = Path(evidence_dir).resolve() / f"{label}.source-runtime"
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in PROBE_FILES:
        source = Path(__file__).with_name(name)
        if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_CAPTURE_BYTES:
            raise ValueError(f"source-resolution observer is not a bounded regular file: {name}")
        data = source.read_bytes()
        target = directory / name
        target.unlink(missing_ok=True)
        target.write_bytes(data)
        files[name] = {"path": str(target), "sha256": hashlib.sha256(data).hexdigest()}
    return files


def probe_intact(files, *, root):
    if not isinstance(files, dict) or set(files) != set(PROBE_FILES):
        return False
    try:
        for row in files.values():
            command_receipt.pinned_bytes(row["path"], row["sha256"], root, limit=MAX_CAPTURE_BYTES)
    except (KeyError, OSError, TypeError, ValueError):
        return False
    return True


def _isolated_python_runtime(command, workspace):
    invocation = python_tests.parse(command, include_owned=True)
    if invocation is None or invocation.kind not in ("pytest", "unittest"):
        return False, []
    executable = Path(invocation.python)
    roots = []
    found = False
    for name in dict.fromkeys((executable.name, "python", "python3")):
        path = executable.with_name(name + "._pth")
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_CAPTURE_BYTES:
                continue
            found = True
            for line in path.read_text().splitlines():
                entry = line.strip()
                if entry and entry != "import site":
                    roots.append((path.parent / entry).resolve())
        except OSError:
            return True, roots
    return found, roots


def plan(framework, command, tree, root, label, *, workspace, evidence_dir=None):
    if not selected(label):
        return None
    declared = getattr(framework, "name", None) or "unknown"
    name = _command_framework(command, root) or declared
    if name not in FRAMEWORKS:
        return {"binding": unsupported(framework, command, tree, workspace)}
    probe_command, npm_test, package_sha256 = command, False, None
    if name == "node":
        resolved = node_tests.npm_test(command, root)
        package_path = Path(root) / "package.json"
        if resolved and resolved[0] is None:
            try:
                if package_path.is_symlink() or package_path.stat().st_size > MAX_CAPTURE_BYTES:
                    raise ValueError("package.json is not a bounded regular file")
                scripts = json.loads(package_path.read_text()).get("scripts") or {}
                if not isinstance(scripts, dict) or not ({"pretest", "posttest"} & set(scripts)):
                    probe_command, npm_test = resolved[1], True
                    package_sha256 = util.file_hash(package_path)
            except (OSError, ValueError, AttributeError):
                pass
        elif (
            resolved is None
            and npm_scripts.test_invocation(command) == ([], ".")
            and not any(word == "--prefix" or word.startswith("--prefix=") for word in npm_scripts.words(command) or [])
            and not package_path.exists()
            and not package_path.is_symlink()
            and not (Path(root) / ".npmrc").exists()
            and not (Path(root) / ".npmrc").is_symlink()
        ):
            probe_command = "node --test"
    if name == "node" and node_tests.command_words(probe_command) is None:
        # The literal invocations were resolved above. A command that names node or npm and hides
        # what it would run (a lifecycle hook, .npmrc, --prefix, extra arguments, a raw wrapper or
        # a build step running repo files) keeps holding through the probe; a script that names
        # another runner only records the boundary — dependency-runner custody is phase 2 of #4152 —
        # and a command that names no runner at all has no Node surface this phase observes.
        script = node_tests.npm_test_declaration(command, root)
        steps = script.split(" && ") if isinstance(script, str) else []

        def inner_words(text, depth=0):
            try:
                words = shlex.split(text) if depth < 2 else []
            except ValueError:
                return ["node"]  # unparsable quoting: the step may run repo files, so hold
            if len(words) > 2 and words[0] in ("sh", "bash", "dash", "zsh") and words[1] == "-c":
                return inner_words(words[2], depth + 1)
            return words

        def runs_the_test_itself(arguments):
            for word in arguments:
                if word.startswith("-"):
                    continue
                name = Path(word).name
                return bool(
                    python_tests.TEST_MODULE.match(name)
                    or project_kinds.TEST_FILE.match(name)
                    or project_kinds.ROOT_TEST_FILE.match(name)
                )
            return False

        runs_repo_files = any(
            "$" in part  # shell expansion decides what the step runs
            or (
                words[:1]
                and (
                    words[0] in ("npm", "pnpm", "yarn")  # re-enters the project's own script table
                    or (
                        Path(words[0]).name in ("node", "node.exe", "python", "python3", "python.exe")
                        and (
                            # The runner form with flags the runner cannot own stays opaque.
                            ("--test" in words and node_tests.command_words(shlex.join(words)) is None)
                            or (
                                "--test" not in words
                                # the exit-code proof owns a script that is the test
                                and not runs_the_test_itself(words[1:])
                                and not any(
                                    word.startswith("node_modules") or "/node_modules/" in word for word in words[1:]
                                )
                            )
                        )
                    )
                )
            )
            for part in steps
            if (words := inner_words(part))
        )
        names_runner = bool(
            re.search(r"\b(?:node|npm|npx|pnpm|yarn)(?:\.(?:exe|cmd))?\b", command)
            or re.search(r"\b(?:sh|bash|zsh|dash)\s+\S", command)
        )
        if not names_runner or (script is not None and not runs_repo_files):
            return {
                "binding": {
                    "version": VERSION,
                    "framework": "node-command",
                    "command": command,
                    "status": "unverified",
                    "reason": "the npm test script names a runner whose source resolution this phase does not observe",
                    "tree": str(Path(tree).resolve()),
                    "workspace": str(Path(workspace).resolve()) if workspace else None,
                    "observations": [],
                }
            }
    if name in ("pytest", "unittest") and python_tests.parse(command, include_owned=True) is None:
        return {
            "binding": {
                "version": VERSION,
                "framework": "python-command",
                "command": command,
                "status": "unverified",
                "reason": "the Python command is not a literal pytest or unittest invocation",
                "tree": str(Path(tree).resolve()),
                "workspace": str(Path(workspace).resolve()) if workspace else None,
                "observations": [],
            }
        }
    if name in ("pytest", "unittest"):
        isolated, runtime_roots = _isolated_python_runtime(command, workspace)
        boundary = Path(workspace).resolve() if workspace else None
        if isolated and any(boundary is not None and root.is_relative_to(boundary) for root in runtime_roots):
            return {
                "binding": {
                    **unsupported(framework, command, tree, workspace),
                    "reason": "an isolated Python runtime path enters the live workspace",
                }
            }
        if isolated:
            return {
                "binding": {
                    "version": VERSION,
                    "framework": "isolated-python",
                    "command": command,
                    "status": "unverified",
                    "reason": "isolated Python runtime custody is phase 2 of #4152",
                    "tree": str(Path(tree).resolve()),
                    "workspace": str(boundary) if boundary else None,
                    "observations": [],
                }
            }
    classification = {
        "framework": name,
        "command": probe_command,
        "npm_test": npm_test,
        "package_sha256": package_sha256,
    }
    if evidence_dir is not None:
        try:
            classification["probe_files"] = retain_probe(evidence_dir, label)
        except (OSError, ValueError) as error:
            return {
                "binding": {
                    **unsupported(framework, command, tree, workspace),
                    "reason": f"source-resolution observer could not be retained: {error}",
                }
            }
    return classification


def observe(framework, command, tree, root, evidence_dir, label, *, workspace, execute, timeout, classification=None):
    """Retain one owned probe beside the test receipt and bind its exact capture."""
    classification = (
        plan(framework, command, tree, root, label, workspace=workspace, evidence_dir=evidence_dir)
        if classification is None
        else classification
    )
    if classification is None:
        return None
    if "binding" in classification:
        return classification["binding"]
    tree, root = Path(tree).resolve(), Path(root).resolve()
    name = classification["framework"]
    probe_command, npm_test = classification["command"], classification["npm_test"]
    capture = (Path(evidence_dir) / f"{label}.source.json").resolve()
    request = (Path(evidence_dir) / f"{label}.source-request.json").resolve()
    request.parent.mkdir(parents=True, exist_ok=True)
    capture.unlink(missing_ok=True)
    payload = {
        "framework": name,
        "command": probe_command,
        "tree": str(tree),
        "root": str(root),
        "workspace": str(Path(workspace).resolve()) if workspace else None,
        "capture": str(capture),
        "npm_test": npm_test,
        "package_sha256": classification["package_sha256"],
        "observer_root": str(Path(__file__).resolve().parent),
    }
    util.atomic_json(request, payload)
    launch = [sys.executable]
    if name in ("pytest", "unittest"):
        invocation = python_tests.parse(command, include_owned=True)
        if invocation is not None:
            launch = [*invocation.prefix, invocation.python]
    probe_files = classification.get("probe_files")
    probe = (
        Path(probe_files["autocode_source_probe.py"]["path"])
        if isinstance(probe_files, dict) and "autocode_source_probe.py" in probe_files
        else Path(__file__).with_name("autocode_source_probe.py").resolve()
    )
    if probe_files is not None and not probe_intact(probe_files, root=Path(evidence_dir).resolve()):
        return {
            "version": VERSION,
            "framework": name,
            "command": probe_command,
            "status": "unverified",
            "reason": "retained source-resolution observer changed before observation",
            "tree": str(tree),
            "workspace": str(Path(workspace).resolve()) if workspace else None,
            "observations": [],
            "probe_files": probe_files,
        }
    receipt = execute(
        shlex.join([*launch, str(probe), str(request)]),
        str(root),
        Path(evidence_dir) / f"{label}.source.log",
        timeout=90,
    )
    try:
        if probe_files is not None and not probe_intact(probe_files, root=Path(evidence_dir).resolve()):
            raise ValueError("retained source-resolution observer changed during observation")
        result, digest = _capture(capture)
        if not command_receipt.completed(receipt) or receipt.get("timed_out") or receipt.get("exit_code") != 0:
            result["errors"] = [*result.get("errors", []), INCOMPLETE]
        assessment = assess(result, tree, workspace)
    except (OSError, ValueError, TypeError) as error:
        result = {"observations": [], "errors": [str(error)]}
        if not command_receipt.completed(receipt) or receipt.get("timed_out") or receipt.get("exit_code") != 0:
            result["errors"].append(INCOMPLETE)
        digest = None
        assessment = assess(result, tree, workspace)
    return {
        "version": VERSION,
        "framework": name,
        "command": probe_command,
        "npm_test": npm_test,
        "package_sha256": classification["package_sha256"],
        "observer_root": payload["observer_root"],
        "request_root": str(root),
        "probe": receipt,
        "request": {"path": str(request), "sha256": util.file_hash(request)},
        "capture": {"path": str(capture), "sha256": digest},
        "probe_files": probe_files,
        **assessment,
    }


def reasons(receipts):
    """Why observed base/candidate receipts cannot supply named proof."""
    found = []
    for label, receipt in receipts.items():
        if not isinstance(receipt, dict):
            found.append(f"{label}: the source-resolution receipt is malformed")
            continue
        if "source_binding" not in receipt:
            if receipt.get("source_binding_required"):
                found.append(f"{label}: the source-resolution binding is missing")
            continue
        binding = receipt["source_binding"]
        if not applicable(binding):
            continue
        reason = None
        if not isinstance(binding, dict):
            reason = "the source-resolution binding is malformed"
        elif not established(receipt):
            reason = binding.get("reason") or "runner source resolution was not established"
        elif not intact(binding, root=None, receipt=receipt):
            reason = "source-resolution probe evidence is missing, malformed or changed"
        if reason:
            found.append(f"{label}: {reason}")
    return found


def intact(binding, *, root, receipt=None):
    """Authenticate the owned probe and recompute its source-boundary answer."""
    if not isinstance(binding, dict):
        return False
    try:
        if receipt is not None and (
            not receipt.get("source_binding_required") or binding.get("owner") != _owner(receipt)
        ):
            return False
        if "children" in binding:
            suites = receipt.get("suites") if receipt is not None else None
            return (
                binding.get("version") == VERSION
                and established(receipt)
                and isinstance(suites, list)
                and len(suites) == len(binding["children"])
                and all(
                    intact(child, root=root, receipt=suite)
                    for child, suite in zip(binding["children"], suites, strict=True)
                )
            )
        if binding.get("version") != VERSION or not established(receipt):
            return False
        if root is None:
            root = Path(binding["probe"]["output"]).parent
        root = Path(root).resolve()
        probe_files = binding.get("probe_files")
        if receipt is not None and receipt.get("source_binding_required"):
            if not probe_intact(probe_files, root=root):
                return False
            if shlex.split(binding["probe"]["command"])[-2:-1] != [probe_files["autocode_source_probe.py"]["path"]]:
                return False
        capture = binding["capture"]
        path = Path(capture["path"])
        result, _ = _capture(path, digest=capture["sha256"], root=root)
        request_path = binding["request"]["path"]
        request = json.loads(
            command_receipt.pinned_bytes(request_path, binding["request"]["sha256"], root, limit=MAX_CAPTURE_BYTES)
        )
        expected = {
            "framework": binding["framework"],
            "command": binding["command"],
            "tree": binding["tree"],
            "root": binding["request_root"],
            "workspace": binding.get("workspace"),
            "capture": str(path),
            "npm_test": binding.get("npm_test") is True,
            "package_sha256": binding.get("package_sha256"),
            "observer_root": binding.get("observer_root"),
        }
        if request != expected or shlex.split(binding["probe"]["command"])[-1:] != [request_path]:
            return False
        output = Path(binding["probe"]["output"])
        command_receipt.pinned_bytes(str(output), binding["probe"]["output_sha256"], root, limit=MAX_CAPTURE_BYTES)
        return assess(result, binding["tree"], binding.get("workspace"))["status"] == "established"
    except (OSError, KeyError, TypeError, ValueError):
        return False

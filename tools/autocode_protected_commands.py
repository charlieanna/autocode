"""Explicit command-only recovery that retains every sealed original test byte."""

import shlex
from pathlib import Path, PurePosixPath

try:
    from . import autocode_contract_identity as contracts
    from . import autocode_python_tests as python_tests
    from . import autocode_verification_plan as plan
except ImportError:
    import autocode_contract_identity as contracts
    import autocode_python_tests as python_tests
    import autocode_verification_plan as plan


SELECTOR = "@approved-suite"


def initial_command(files, command):
    """Bind nested originals explicitly; later new tests belong to independent checks."""
    invocation = python_tests.parse(command)
    if (
        not invocation
        or invocation.kind != "unittest"
        or invocation.arguments != ("discover", "-v")
        or (files.get("tests/__init__.py") or {}).get("size") != 0
        or any(
            "symlink" in value or name.endswith("/__init__.py") and value.get("size") != 0
            for name, value in files.items()
        )
    ):
        return command
    modules = sorted(name for name in files if python_tests.TEST_MODULE.match(Path(name).name))
    if not any(name.startswith("repos/") for name in modules):
        return command
    return shlex.join([*invocation.prefix, invocation.python, "-m", "unittest", "-v", *modules])


def proposal(state, record):
    """Offer only an approved unfiltered collector covering all original test paths.

    This creates no binding and grants no authority. Only an explicit CLI action
    at a reconciled validation pause can install the returned command.
    """
    if (
        not record
        or state.get("status")
        not in ("PAUSED_INTERVENTION", "PAUSED_REQUESTED", "PAUSED_OPERATOR_REQUEST", "PAUSED_AFTER_STAGE")
        or state.get("next_stage") not in ("sol", "astra_checkpoint")
        or any(
            state.get(key)
            for key in (
                "active_stage",
                "pending_report_repair",
                "uncertain_artifacts",
                "runner_check",
                "active_runner_check",
            )
        )
        or not contracts.approved(state)
    ):
        return None
    old = python_tests.parse(record.get("command"))
    files = record.get("files") or {}
    marker = files.get("tests/__init__.py") or {}
    workspace = Path(state.get("workspace") or ".")
    if (
        not old
        or old.kind != "unittest"
        or old.arguments != ("discover", "-v")
        or marker.get("size") != 0
        or (workspace / "tests/__init__.py").exists()
        or not any(name.startswith("repos/") and Path(name).name.startswith("test_") for name in files)
    ):
        return None
    choices = []
    for command in plan.approved_commands(state):
        invocation = python_tests.parse(command)
        if not invocation or invocation.kind != "pytest":
            continue
        roots, valid, index = [], True, 0
        args = invocation.arguments
        while index < len(args):
            word = args[index]
            if word == "-p" and args[index : index + 2] == ("-p", "no:cacheprovider"):
                index += 2
                continue
            if word in ("-q", "-v", "-vv", "--continue-on-collection-errors"):
                index += 1
                continue
            path = PurePosixPath(word)
            if word.startswith("-") or path.is_absolute() or ".." in path.parts or "::" in word:
                valid = False
                break
            roots.append(path)
            index += 1
        if valid and roots and all(any(PurePosixPath(name).is_relative_to(root) for root in roots) for name in files):
            choices.append(command)
    choices = list(dict.fromkeys(choices))
    if len(choices) != 1:
        return None
    return {
        "previous_hash": record["binding_hash"],
        "files": files,
        "command": choices[0],
        "reason": "Person selected the approved combined suite to repair empty root discovery; every sealed original test remains protected.",
    }


def offer(state):
    record = (state.get("settings") or {}).get("protected_tests")
    revision = proposal(state, record)
    if not revision:
        return None
    return {
        "kind": "resume",
        "action": "--resume-paused --revise-protected-tests " + SELECTOR,
        "reason": "Original test discovery no longer collects the approved workspace. Explicitly replace only its command: "
        + str(record["command"])
        + " → "
        + revision["command"]
        + ". Keep every original test byte and assertion, retain the old binding, and require fresh independent replay.",
    }

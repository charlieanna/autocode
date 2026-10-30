"""Compose pinned repositories for a real joint regression through task-run/verify.

The integration Builder works on copies. Its production bytes must match every
member before its result can authorize publication; deviations are suggestions
sent back through those members' ordinary follow-up workflow.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

try:
    from . import autocode_coordination_repositories as repos
    from . import autocode_util as util
    from .autocode_coordination_store import CoordinationError
except ImportError:
    import autocode_coordination_repositories as repos
    import autocode_util as util
    from autocode_coordination_store import CoordinationError


def copy_tree(source, destination):
    destination = Path(destination)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for name in repos.source_files(source):
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(source) / name, path)


def base_tree(member, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for entry in repos.git(member["origin"], "ls-tree", "-rz", member["base"], binary=True).split(b"\0"):
        if not entry:
            continue
        metadata, raw = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        name = os.fsdecode(raw)
        if name.startswith((".autocode/", ".autocode-ui/")):
            continue
        path = destination / name
        if (
            mode not in ("100644", "100755")
            or kind != "blob"
            or not path.resolve().is_relative_to(destination.resolve())
        ):
            raise CoordinationError("Unsupported linked source mode/path: " + name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(repos.git(member["origin"], "cat-file", "blob", oid, binary=True))
        path.chmod(0o755 if mode == "100755" else 0o644)


def prepare(document, directory):
    directory = Path(directory)
    if directory.exists():
        raise CoordinationError("Joint workspace already exists without a completed preparation receipt")
    directory.mkdir(parents=True)
    manifest = {}
    for member in document["members"]:
        relative = "repos/" + member["slug"]
        base_tree(member, directory / relative)
        manifest[member["target"]] = {"path": relative, "base_commit": member["base"]}
    (directory / ".gitignore").write_text(".autocode/\n__pycache__/\n*.pyc\n")
    # Give baseline discovery a valid empty suite before the Builder adds the
    # joint regression. A missing tests directory is a setup error, not proof.
    (directory / "tests").mkdir()
    (directory / "tests" / "__init__.py").write_text("")
    util.atomic_json(directory / "repositories.json", manifest)
    repos.git(directory, "init", "-q")
    repos.git(directory, "add", "--all")
    repos.git(
        directory,
        "-c",
        "user.name=AutoCode",
        "-c",
        "user.email=autocode@localhost",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "Pinned original repositories for joint regression",
    )
    baseline = repos.git(directory, "rev-parse", "HEAD")
    for member in document["members"]:
        copy_tree(member["workspace"], directory / ".autocode" / "candidate-inputs" / member["slug"])
    util.atomic_json(
        directory / ".autocode" / "candidate-inputs.json",
        {
            member["target"]: {
                "source": ".autocode/candidate-inputs/" + member["slug"],
                "destination": manifest[member["target"]]["path"],
                "fingerprint": repos.fingerprint(member["workspace"]),
            }
            for member in document["members"]
        },
    )
    return baseline


def brief(document):
    return (
        "Implement a joint regression for this original bug report:\n"
        + document["task"]
        + "\n\nThis is an isolated integration workspace. Every repository in repositories.json is already "
        "present under repos/. Treat them as local components of this workspace, not external repositories. "
        "The committed baseline contains their original broken code. .autocode/candidate-inputs.json names "
        "exact candidate copies from independently verified repository runs. Replace each corresponding repos/ "
        "subtree with its candidate input (including deletions), then implement real joint regression tests in tests/. "
        "The candidate copies are task inputs, not source: read them to compose the work, but cite only the lasting "
        "repos/ source in source_refs and code_refs — the runner rejects a citation of .autocode/ or any other "
        "runner-owned path. A repos/ file still holds the original broken code until its candidate is installed; "
        "do not describe baseline content as the candidate fix. "
        "Derive the test and its executable command from the original symptom and actual interfaces; do not ask "
        "the person to supply a test command. Exercise actual code from EVERY listed repository together. "
        "No mocked production boundary, scripted PASS, file-existence assertion or independent per-repository "
        "suite alone establishes the fix. The same regression must fail against the committed broken baseline "
        "and pass against the composed candidate. Name that executable regression in the acceptance criteria. "
        "Independent review and the proof runner will check it. If the candidates disagree, make the smallest "
        "necessary correction in the copied component, preserving the intended behavior; the coordinator will "
        "send that correction back to its owning run and require new joint verification. Do not commit or push. "
        "Keep repositories.json unchanged. Do not omit any repository from the test. "
        "The independent runner also collects changed tests inside the copied repositories from this combined "
        "root. Make their imports and test setup work here as well as in each original repository; setup errors "
        "do not count as reproducing the bug."
        " The first acceptance criterion's command must run the combined project suite, including existing "
        "member tests and the new cross-repository regression. It is replayed on the original baseline and "
        "candidate to establish preservation as well as the fix."
        + (
            "\n\nPrevious independent verification failed. Correct the actual cause, retaining the checks:\n"
            + document["joint_feedback"]
            if document.get("joint_feedback")
            else ""
        )
    )


def failure_feedback(verification):
    proof = ((verification.get("evidence_report") or {}).get("document") or {}).get("regression_proof") or {}
    messages = [
        *verification.get("reasons", []),
        *proof.get("failures", []),
        *proof.get("notes", []),
        *proof.get("unverified", []),
    ]
    return (
        "\n".join(str(message) for message in messages)[:30_000]
        or "Independent verification did not establish the fix; inspect the retained verification record."
    )


def mismatches(document, workspace):
    result = []
    for member in document["members"]:
        # source_files needs the joint Git root, so compare its relative inventory.
        prefix = "repos/" + member["slug"] + "/"
        actual = {
            name[len(prefix) :]: value
            for name, value in repos.source_files(workspace).items()
            if name.startswith(prefix)
        }
        if actual != repos.source_files(member["workspace"]):
            result.append(member)
    return result


def correction(member, workspace):
    source = Path(workspace) / "repos" / member["slug"]
    changed = []
    original = repos.source_files(member["workspace"])
    prefix = "repos/" + member["slug"] + "/"
    candidate = {
        name[len(prefix) :]: value for name, value in repos.source_files(workspace).items() if name.startswith(prefix)
    }
    import difflib

    for name in sorted(set(original) | set(candidate)):
        if original.get(name) == candidate.get(name):
            continue
        before = (Path(member["workspace"]) / name).read_text() if name in original else ""
        after = (source / name).read_text() if name in candidate else ""
        changed.extend(
            difflib.unified_diff(
                before.splitlines(True), after.splitlines(True), fromfile="a/" + name, tofile="b/" + name
            )
        )
    patch = "".join(changed)
    if len(patch.encode()) > 100_000:
        raise CoordinationError("Joint correction exceeds the bounded follow-up size; inspect the integration run")
    return (
        "The actual joint regression found an interface mismatch. Correct this repository's part and reverify it against the original bug. The integration run proposes this patch; independently check it rather than blindly applying it:\n\n"
        + patch
    )


def criteria(document, view):
    body = (view.get("approved_contract") or {}).get("body") or {}
    checks = body.get("acceptance_criteria") or []
    if not checks or any(row.get("human_review") for row in checks):
        raise CoordinationError(
            "Joint verification needs an approved executable regression with no outstanding human review"
        )
    return {
        "intended_outcome": document["task"]
        + "\nProve the original symptom is fixed with actual code from all repositories: "
        + ", ".join(row["target"] for row in document["members"]),
        "proof_kind": "bugfix",
        "acceptance_criteria": [
            {key: row[key] for key in ("id", "criterion", "verification_method", "human_review")} for row in checks
        ],
    }

"""Verification records are external-change verdicts, never task approvals."""

from __future__ import annotations

try:
    from . import autocode_review_attribution as review_attribution
except ImportError:
    import autocode_review_attribution as review_attribution

import copy
import json
import os
import re
import subprocess
from pathlib import Path

try:
    from . import autocode_agent_env as agent_env
    from . import autocode_command_receipt as command_receipt
    from . import autocode_completion as completion
    from . import autocode_evidence_aggregate as aggregate
    from . import autocode_evidence_document as document
    from . import autocode_evidence_export as export
    from . import autocode_source_snapshot as source_snapshot
    from . import autocode_util as util
except ImportError:
    import autocode_agent_env as agent_env
    import autocode_command_receipt as command_receipt
    import autocode_completion as completion
    import autocode_evidence_aggregate as aggregate
    import autocode_evidence_document as document
    import autocode_evidence_export as export
    import autocode_source_snapshot as source_snapshot
    import autocode_util as util


# Shared snapshot/proof helpers inherit the process environment. Refuse ambient
# redirection instead of changing that environment or authenticating another repo.
# Only the `git -c` parameters (CONFIG_PARAMETER) are cleared from it instead.
CONFIG_PARAMETER = re.compile(r"GIT_CONFIG_(?:COUNT|KEY_[0-9]+|VALUE_[0-9]+)")
GIT_REDIRECTIONS = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_QUARANTINE_PATH",
        "GIT_NAMESPACE",
        "GIT_SHALLOW_FILE",
        "GIT_GRAFT_FILE",
        "GIT_REPLACE_REF_BASE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    }
)


def clear_git_config_parameters(environment=None):
    """Remove the ``git -c`` parameters an editor's terminal exports and return their names.

    Cursor and VS Code set GIT_CONFIG_COUNT, GIT_CONFIG_KEY_n and GIT_CONFIG_VALUE_n. Verification runs
    its own Git with a scrubbed environment and gives every child GIT_CONFIG_COUNT=0
    (autocode_pr_checkout.task_environment), so it clears them rather than refusing (#2504); the
    caller records the names. Repository and config-file redirections are still refused.
    """
    environment = os.environ if environment is None else environment
    names = sorted(name for name in environment if CONFIG_PARAMETER.fullmatch(name))
    for name in names:
        del environment[name]
    return names


def require_git_environment(environment=None):
    """Refuse repository/config overrides; diagnostics contain names only."""
    environment = os.environ if environment is None else environment
    names = sorted(
        name
        for name in environment
        if name in GIT_REDIRECTIONS or name == "GIT_CONFIG" or name.startswith("GIT_CONFIG_")
    )
    if names:
        raise ValueError(
            "Verification refuses ambient Git overrides: "
            + ", ".join(names)
            + ". Unset them and run again: unset "
            + " ".join(names)
        )


def source_identity(workspace):
    """Snapshot exactly the requested repository, refusing local worktree redirects."""
    require_git_environment()
    workspace = Path(workspace).resolve(strict=True)
    environment = {name: value for name, value in agent_env.scrubbed(os.environ).items() if not name.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(workspace), "rev-parse", "--show-toplevel"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode or Path(result.stdout.rstrip("\n")).resolve(strict=True) != workspace:
        raise ValueError("Verification workspace differs from the requested Git top-level")
    return source_snapshot.snapshot(workspace)


def review_support():
    """Preflight the public authenticated review and output-custody dependency."""
    try:
        from . import autocode_pr_checkout as checkout_policy
        from . import autocode_pr_review as review_policy
    except ImportError:
        try:
            import autocode_pr_checkout as checkout_policy
            import autocode_pr_review as review_policy
        except ImportError as error:
            raise ValueError("Authenticated review artifact and checkout support is unavailable") from error
    if not callable(getattr(review_policy, "findings", None)) or any(
        not callable(getattr(checkout_policy, name, None))
        for name in (
            "create_root",
            "follow_system_links",
            "protect_python_caches",
            "require_python_caches",
            "task_environment",
        )
    ):
        raise ValueError("Authenticated review artifact and checkout support is unavailable")
    return review_policy, checkout_policy


def require_review_inputs(workspace):
    """Refuse selected source that legacy source/proof snapshots deliberately omit."""
    _, policy = review_support()
    names = policy.git(workspace, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
    for name in names:
        if not name:
            continue
        parts = Path(name).parts
        if parts[0].casefold() in {".autocode", ".autocode-ui", "review"}:
            raise ValueError("The selected change uses reserved .autocode/, .autocode-ui/ or review/ paths")
        if any(part.casefold() == "__pycache__" for part in parts) or name.casefold().endswith(".pyc"):
            raise ValueError("The selected change contains cache input omitted by source snapshots")


class ReviewSource:
    """The exact input source in an owned, independent review checkout."""

    def __init__(self, workspace):
        self.workspace = Path(workspace)
        require_review_inputs(self.workspace)
        self.snapshot = source_identity(workspace)
        self.index = index_identity(workspace)
        _, self.checkout_policy = review_support()
        sources = [
            name
            for name in self.snapshot["files"]
            if name.endswith(".py") and not (self.workspace / name).is_symlink() and (self.workspace / name).is_file()
        ]
        self.cache_directories = self.checkout_policy.protect_python_caches(self.workspace, sources)
        with (self.workspace.parent / "python-cache-protection.json").open("x") as stream:
            json.dump(
                {
                    "version": 1,
                    "directories": [
                        {"path": name, "identity": list(identity)} for name, identity in self.cache_directories
                    ],
                },
                stream,
            )
            stream.write("\n")

    def unchanged(self):
        self.checkout_policy.require_python_caches(self.workspace, self.cache_directories)
        # Reuse the strict checkout inventory: ignore rules cannot hide a new payload.
        others = []
        for flags in (("--others", "--exclude-standard"), ("--others", "--ignored", "--exclude-standard")):
            others.extend(self.checkout_policy.git(self.workspace, "ls-files", *flags, "-z").split("\0"))
        stray = sorted(
            {
                name
                for name in others
                if name and name not in self.snapshot["files"] and not name.startswith((".autocode/", "review/"))
            }
        )
        if stray:
            raise ValueError("Independent review left unexpected source files: " + ", ".join(stray))
        after = source_identity(self.workspace)
        # The existing review workflow writes findings and delivered tests here.
        # Source using this reserved directory was refused before dispatch.
        after["files"] = {name: value for name, value in after["files"].items() if not name.startswith("review/")}
        if (
            after["head"] != self.snapshot["head"]
            or after["files"] != self.snapshot["files"]
            or index_identity(self.workspace) != self.index
        ):
            raise ValueError("Independent review changed the selected source or index")


def review_findings(run, source):
    review_policy, _ = review_support()
    return review_policy.findings(run, source)


def binding(receipt):
    return util.digest(
        {
            "contract": receipt["contract"],
            "current": receipt["candidate_snapshot"],
            "original": receipt["original"],
            "original_index": receipt["original_index"],
            "pins": receipt["pins"],
            "verdict": receipt["verdict"],
        }
    )


def evidence_pins(proof, replay, child, mutation_check=None):
    pins = {child["json_path"]: child["json_sha256"], child["markdown_path"]: child["markdown_sha256"]}
    receipts = [*proof.get("checks", {}).values(), *replay.get("checks", [])]
    # The runner's mutation check ran its own project commands; their captures are evidence too (#2506).
    receipts += [row for row in (mutation_check or {}).get("command_receipts") or [] if isinstance(row, dict)]
    for row in receipts:
        pins.update(command_receipt.pins(row))
        if row.get("output") and row.get("output_sha256"):
            pins[row["output"]] = row["output_sha256"]
    return pins


def publish(
    root,
    *,
    contract,
    current,
    proof,
    replay,
    child,
    review,
    review_workspace,
    reasons,
    original,
    original_index,
    candidate,
    mutation_check=None,
):
    """Use the unchanged completion predicate over explicitly runner-derived facts.

    This assertion is not a model Completion Reviewer report, a goal approval or
    an ordinary TASK_COMPLETE. The review child remains a findings-only review.
    External regression proof is mandatory even when the generic gate has no
    regression obligation for a legacy state. ``mutation_check`` is the runner's
    own review mutation check of the change (autocode_verify_task.mutation_check),
    recorded with the review's result as report-only evidence (#2506).
    """
    root = Path(root)
    artifact = child["document"].get("workflow_result") or {}
    artifact_path = Path(review_workspace) / "review/findings.json"
    reviewed = review.get("verdict") == "approve" and not any(
        review_attribution.blocking(row) for row in review["findings"]
    )
    reasons = list(reasons)
    if not reviewed:
        reasons.append("Independent review did not approve the external change")
    if proof.get("verdict") != "PASS":
        reasons.append("External-change proof: " + str(proof.get("verdict") or "UNVERIFIED"))
    criteria = contract["acceptance_criteria"]
    human = [row["id"] for row in criteria if row["human_review"]]
    if human:
        reasons.append("Human acceptance is not established: " + ", ".join(human))
    pins = evidence_pins(proof, replay, child, mutation_check)
    pins[str(artifact_path)] = artifact["artifact_sha256"]
    proof_path = root / "proof.json"
    util.atomic_json(proof_path, proof)
    pins[str(proof_path)] = util.file_hash(proof_path)
    for name in ("criteria.json", "check-request.json"):
        pins[str(root / name)] = util.file_hash(root / name)
    accepted = not reasons and bool(replay.get("checks")) and replay.get("verdict") == "PASS"
    results = [
        {"id": row["id"], "status": "PASS" if accepted else "NOT_VERIFIED", "evidence_refs": list(pins)}
        for row in criteria
    ]
    assertion = {
        "status": "TASK_COMPLETE",
        "acceptance_criteria": [
            {
                "id": row["id"],
                "criterion": row["criterion"],
                "status": "verified" if accepted else "unverified",
                "evidence": "Runner checks and external-change proof; independent review approval",
            }
            for row in criteria
        ],
        "findings": [],
    }
    state = {
        "version": 2,
        "settings": {},
        "criteria_revision": contract["sha256"],
        "acceptance_criteria": [{"id": row["id"], "criterion": row["criterion"]} for row in criteria],
        "validation": {
            "verdict": "PASS" if accepted else "NOT_VERIFIED",
            "source_revision": current["revision"],
            "criteria_revision": contract["sha256"],
            "checks": replay.get("checks", []),
            "criterion_results": results,
            "check_replay": replay,
            "evidence_hashes": pins,
        },
    }
    ready = accepted and completion.completion_ready(state, assertion, current)
    if accepted and not ready:
        reasons.append("Existing completion evidence predicate refused verification")
    verdict = (
        "PASS"
        if ready
        else "FAIL"
        if review.get("verdict") == "request_changes" or proof.get("verdict") == "FAIL"
        else "UNVERIFIED"
    )
    bound = util.digest(
        {
            "contract": contract,
            "current": current,
            "original": original,
            "original_index": original_index,
            "pins": pins,
            "verdict": verdict,
        }
    )
    facts = {
        "status": "VERIFIED" if ready else verdict,
        "workflow": "verify",
        "evidence": {
            "created_at": original["started_at"],
            "outcome": contract["intended_outcome"],
            "base_commit": proof.get("base"),
            "validator_source_revision": current["revision"],
            "acceptance": [
                {
                    "id": row["id"],
                    "criterion": row["criterion"],
                    "status": "verified" if ready else "unverified",
                    "validator_status": "PASS" if ready else "NOT_VERIFIED",
                    "human_reviewed": False,
                    "evidence": assertion["acceptance_criteria"][i]["evidence"],
                }
                for i, row in enumerate(criteria)
            ],
            "check_replay": replay,
            "regression_proof": proof,
            "workflow_result": {
                "outcome": verdict,
                "artifact": str(artifact_path),
                "artifact_sha256": artifact["artifact_sha256"],
                "summary": "Runner external-change verification; independent review: " + str(review.get("verdict")),
                **({"mutation_check": mutation_check} if mutation_check is not None else {}),
            },
        },
        "verification": {
            "criteria_revision": contract["sha256"],
            "coverage": [
                {
                    "id": row["id"],
                    "verification_method": row["verification_method"],
                    "human_review": row["human_review"],
                    "evidence_refs": list(pins),
                }
                for row in criteria
            ],
        },
        "usage": {
            "accounting": {"attempts": copy.deepcopy(child["document"]["attempts"]), **aggregate.combine([child])}
        },
    }
    value = document.build(
        facts,
        kind="verification",
        run_identity=root.name,
        completed_at=util.now(),
        provenance=child["document"]["provenance"],
        binding=bound,
        children=[
            {
                "id": "independent-review",
                "json_path": child["json_path"],
                "json_sha256": child["json_sha256"],
                "markdown_sha256": child["markdown_sha256"],
                "source_revision": child["document"]["revision"]["source_revision"],
                "provenance": child["document"]["provenance"],
            }
        ],
    )
    value["unverified"] += reasons
    anchor = export.write(root, value)
    receipt = {
        "version": 1,
        "verdict": verdict,
        "contract": contract,
        "candidate": str(candidate),
        "candidate_snapshot": current,
        "original": original,
        "original_index": original_index,
        "pins": pins,
        "anchor": anchor,
        "binding": bound,
        "reasons": reasons,
    }
    util.atomic_json(root / "verification.json", receipt)
    return read(root)


def index_identity(workspace):
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(workspace), "ls-files", "--stage", "-z"], capture_output=True, check=True, timeout=30
    )
    return util.digest(result.stdout.hex())


def read(root):
    clear_git_config_parameters()
    require_git_environment()
    root = Path(root).resolve()
    # The public verification result names this file. Accept that exact
    # reference as well as the output directory used by older callers.
    if root.name == "verification.json" and root.is_file():
        root = root.parent
    receipt = util.read_object(root / "verification.json")
    reasons = []
    try:
        if binding(receipt) != receipt["binding"]:
            raise ValueError("Verification receipt differs from its bound facts")
        original = receipt["original"]
        if (
            source_identity(original["workspace"]) != original["snapshot"]
            or index_identity(original["workspace"]) != receipt["original_index"]
        ):
            reasons.append("Original checkout source or index changed since verification")
        if source_identity(receipt["candidate"]) != receipt["candidate_snapshot"]:
            reasons.append("Verified candidate source changed")
        if any(not Path(path).is_file() or util.file_hash(path) != digest for path, digest in receipt["pins"].items()):
            reasons.append("Verification evidence changed or is unavailable")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        reasons.append("Verification source or evidence is unavailable")
    report = export.read(
        root, receipt["anchor"], expected_binding=receipt["binding"], current=not reasons, include=True
    )
    if not report.get("document") or report.get("availability") not in ("recorded", "current"):
        reasons.append("Canonical verification report is unavailable")
    return {
        "verdict": "UNVERIFIED" if reasons else receipt["verdict"],
        "availability": "stale" if reasons else "current",
        "reasons": [*receipt.get("reasons", []), *reasons],
        "record": str(root / "verification.json"),
        "evidence_report": report,
    }

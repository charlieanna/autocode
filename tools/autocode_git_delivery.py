"""Controller-owned atomic local delivery, followed by fresh source verification.

Only a sealed approved ``git_delivery`` contract authorizes a write. No model
worker commits. The durable intent/receipt makes a lost controller response
recoverable without replaying a successful commit. Existing hooks are honored.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_code_checkpoints as trees
    from . import autocode_contract_identity as contracts
    from . import autocode_grader_process as processes
    from . import autocode_source_scope as scope
    from . import autocode_util as util
    from .autocode_git_delivery_policy import requirement as requirement
except ImportError:
    import autocode_code_checkpoints as trees
    import autocode_contract_identity as contracts
    import autocode_grader_process as processes
    import autocode_source_scope as scope
    import autocode_util as util
    from autocode_git_delivery_policy import requirement as requirement


def git(workspace, *args, env=None, data=None):
    """Bound Git plus its hooks to an owned process tree; preserve Git configuration."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_COMMON_DIR",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        )
    }
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as source:
        source.write(data or b"")
        source.seek(0)
        child = subprocess.Popen(
            ["git", "-C", str(workspace), *args],
            stdin=source,
            stdout=output,
            stderr=errors,
            env={**environment, **(env or {})},
            start_new_session=True,
        )
        code, expired, _ = processes.wait(child, 60)
        if expired:
            raise ValueError("Git delivery timed out; inspect the durable receipt and Git state before retrying")
        if code:
            errors.seek(0)
            raise ValueError("Git delivery refused: " + errors.read().decode(errors="replace").strip())
        output.seek(0)
        return output.read()


def ready(state: RunState, current):
    """Completion needs the exact delivered commit, tree, plan and unchanged source."""
    try:
        spec = requirement((state.get("goal_contract") or {}).get("body") or {})
        if spec is None:
            return True
        saved = state.get("git_delivery") or {}
        path = Path(state["run_dir"]) / "git-delivery.json"
        if path.is_symlink() or saved.get("receipt_sha256") != util.file_hash(path):
            return False
        receipt = util.read(path)
        return bool(
            contracts.approved(state)
            and receipt.get("phase") == "committed"
            and receipt.get("contract_hash") == cast(dict, state["goal_contract"])["hash"]
            and receipt.get("workspace") == str(Path(state["workspace"]).resolve())
            and receipt.get("spec") == spec
            and receipt.get("delivered_source") == current
            and saved.get("commit") == receipt.get("commit") == current["head"]
            and git(state["workspace"], "rev-parse", "HEAD^", "HEAD^{tree}").decode().splitlines()
            == [receipt["parent"], receipt["tree"]]
            and trees.tree_files(state["workspace"], current["head"]) == trees.content_files(current)
            and not git(state["workspace"], "diff", "--cached", "--name-only", "-z")
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def commit(workspace, current, spec, path, intent):
    """Commit the checked scoped tree, holding the real index lock until publication."""
    index = Path(os.fsdecode(git(workspace, "rev-parse", "--git-path", "index")).strip())
    if not index.is_absolute():
        index = Path(workspace) / index
    lock = index.with_name(index.name + ".lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with (
            os.fdopen(descriptor, "wb") as locked,
            tempfile.TemporaryDirectory(prefix="git-delivery-", dir=path.parent) as tmp,
        ):
            if git(workspace, "diff", "--cached", "--name-only", "-z"):
                raise ValueError("Staged work must be reconciled before atomic Git delivery")
            environment = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            git(workspace, "read-tree", "--empty", env=environment)
            files = trees.content_files(current)
            git(
                workspace,
                "--literal-pathspecs",
                "add",
                "-f",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
                env=environment,
                data=b"\0".join(os.fsencode(name) for name in files) + b"\0",
            )
            tree = git(workspace, "write-tree", env=environment).decode().strip()
            if (
                trees.tree_files(workspace, tree) != files
                or scope.snapshot(workspace, intent["state_scope"]) != current
            ):
                raise ValueError("Source changed during Git delivery or Git filters changed the checked bytes")
            durable = {key: value for key, value in intent.items() if key != "state_scope"}
            durable.update(tree=tree, phase="prepared")
            util.atomic_json(path, durable)
            if git(workspace, "rev-parse", "HEAD").decode().strip() != current["head"]:
                raise ValueError("Git HEAD changed before atomic delivery")
            git(workspace, "commit", "-m", spec["message"], env=environment)
            head = git(workspace, "rev-parse", "HEAD").decode().strip()
            actual = git(workspace, "rev-parse", "HEAD^", "HEAD^{tree}").decode().splitlines()
            if actual != [current["head"], tree]:
                raise ValueError("Git delivery changed unexpectedly; reconcile HEAD before continuing")
            # Git committed through the separate index. Publish that exact index
            # under the lock so the ordinary worktree is clean, not reverse-staged.
            locked.write(Path(environment["GIT_INDEX_FILE"]).read_bytes())
            locked.flush()
            os.fsync(locked.fileno())
            os.replace(lock, index)
            after = scope.snapshot(workspace, intent["state_scope"])
            durable.update(phase="committed", commit=head, delivered_source=after)
            util.atomic_json(path, durable)
            if trees.content_files(after) != files:
                raise ValueError("Source changed in a Git hook; fresh work requires reconciliation before completion")
            return durable
    finally:
        lock.unlink(missing_ok=True)


def prepare(state: RunState, workspace, current):
    """Deliver once after passing evidence; True requests fresh independent validation."""
    spec = requirement((state.get("goal_contract") or {}).get("body") or {})
    if spec is None:
        return False
    if not contracts.approved(state):
        raise ValueError("Git delivery needs the exact approved plan")
    if (state.get("workflow") or {}).get("kind") not in (None, "build", "bugfix") or state.get("settings", {}).get(
        "workflow", {}
    ).get("mode") == "glm_final_audit_v2":
        raise ValueError(
            "Atomic Git delivery currently requires the ordinary build review workflow or the bugfix workflow"
        )
    if scope.snapshot(workspace, state) != current:
        raise ValueError("Source changed before Git delivery")
    path = Path(state["run_dir"]) / "git-delivery.json"
    if path.is_symlink():
        raise ValueError("Git delivery receipt must not be a symlink")
    if path.exists():
        receipt = util.read(path)
        if receipt.get("contract_hash") != cast(dict, state["goal_contract"])["hash"] or receipt.get("spec") != spec:
            raise ValueError("The Git delivery plan changed; reconcile the saved receipt")
        if receipt.get("phase") != "committed":
            raise ValueError("A prior Git delivery is uncertain; inspect its receipt and Git state before retrying")
        state["git_delivery"] = {"commit": receipt["commit"], "receipt_sha256": util.file_hash(path)}
        if not ready(state, current):
            raise ValueError("Source, index or Git HEAD changed after delivery; reconcile before completion")
        return (state.get("validation") or {}).get("source_revision") != current["revision"]
    if state.get("base_commit") != current["head"]:
        raise ValueError("Preexisting dirty work or moved HEAD requires reconciliation before atomic delivery")
    baseline = trees.tree_files(workspace, current["head"])
    files = trees.content_files(current)
    changed = {name for name in baseline.keys() | files.keys() if baseline.get(name) != files.get(name)}
    selected = scope.paths(state)
    if not changed or any(
        not any(name == root or name.startswith(root + "/") for root in selected) for name in changed
    ):
        raise ValueError("Atomic Git delivery requires changes wholly within the approved affected paths")
    intent = {
        "version": 1,
        "contract_hash": cast(dict, state["goal_contract"])["hash"],
        "workspace": str(Path(workspace).resolve()),
        "parent": current["head"],
        "spec": spec,
        "tested_source": current,
        "state_scope": state,
    }
    receipt = commit(workspace, current, spec, path, intent)
    state["git_delivery"] = {"commit": receipt["commit"], "receipt_sha256": util.file_hash(path)}
    return True

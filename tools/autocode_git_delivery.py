"""Controller-owned atomic local delivery, followed by fresh source verification.

Only a sealed approved ``git_delivery`` contract authorizes a write. Accidental
Builder commits are reconciled from runner evidence. The durable intent/receipt
makes a lost controller response recoverable without replaying a successful
commit. Existing hooks are honored.
"""

from __future__ import annotations

import hashlib
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
    from . import autocode_git_reconcile as reconcile
    from . import autocode_grader_process as processes
    from . import autocode_source_scope as scope
    from . import autocode_util as util
    from .autocode_git_delivery_policy import requirement as requirement
    from .autocode_runner_files import runner_files
except ImportError:
    import autocode_code_checkpoints as trees
    import autocode_contract_identity as contracts
    import autocode_git_reconcile as reconcile
    import autocode_grader_process as processes
    import autocode_source_scope as scope
    import autocode_util as util
    from autocode_git_delivery_policy import requirement as requirement
    from autocode_runner_files import runner_files


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


def _benign_index(workspace, staged, files):
    """A staged index passes only for delivered paths holding exactly the delivered bytes."""
    entries = {}
    for item in git(workspace, "ls-files", "--stage", "-z").split(b"\0"):
        if not item:
            continue
        meta, name = item.split(b"\t", 1)
        mode, oid, stage = meta.decode().split()
        entries.setdefault(os.fsdecode(name), []).append((mode, stage, oid))
    for name in staged:
        rows = entries.get(name) or []
        if len(rows) != 1 or rows[0][1] != "0" or rows[0][0] not in ("100644", "100755", "120000"):
            return False
        identity = files.get(name)
        if identity is None:
            return False
        try:
            blob = git(workspace, "cat-file", "blob", rows[0][2])
        except ValueError:
            return False
        digest = hashlib.sha256(blob).hexdigest()
        if identity not in (digest, "executable:" + digest) and identity != "symlink:" + blob.decode(errors="replace"):
            return False
    return True


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
            files = trees.content_files(current)
            staged = [
                os.fsdecode(name)
                for name in git(workspace, "diff", "--cached", "--name-only", "-z").split(b"\0")
                if name
            ]
            if staged and not _benign_index(workspace, staged, files):
                raise ValueError("Staged work must be reconciled before atomic Git delivery")
            environment = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            git(workspace, "read-tree", "--empty", env=environment)
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
            if intent["reconciled_commits"]:
                # Move only the ref, with compare-and-swap, under the index lock.
                # The checked files and both indexes retain their original bytes.
                git(workspace, "update-ref", "HEAD", intent["parent"], current["head"])
            try:
                git(workspace, "commit", "-m", spec["message"], env=environment)
            except ValueError:
                if (
                    intent["reconciled_commits"]
                    and git(workspace, "rev-parse", "HEAD").decode().strip() == intent["parent"]
                ):
                    git(workspace, "update-ref", "HEAD", current["head"], intent["parent"])
                raise
            head = git(workspace, "rev-parse", "HEAD").decode().strip()
            actual = git(workspace, "rev-parse", "HEAD^", "HEAD^{tree}").decode().splitlines()
            if actual != [intent["parent"], tree]:
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


def _changed_in_scope(state: RunState, workspace, baseline, files):
    """A non-empty delivered diff wholly inside the approved affected paths, or it is refused."""
    # A name the source inventory excludes (bytecode caches, run state) is not source and
    # leaves the delivered tree, so a tracked one is no delivery change whether or not a
    # run rebuilt it. A runner-written file whose content the run recorded and still holds
    # (a bugfix diagnosis note, a review report) is the runner's own investigation evidence
    # in the delivered diff, not a workspace change to refuse (autocode_runner_files).
    _, runner_kept = runner_files(state, workspace)
    changed = {
        name
        for name in baseline.keys() | files.keys()
        if baseline.get(name) != files.get(name)
        and (name in files or not util.inventory_excluded(name))
        and name not in runner_kept
    }
    selected = scope.paths(state)
    return bool(changed) and all(
        any(name == root or name.startswith(root + "/") for root in selected) for name in changed
    )


def _recorded_follow_up(state: RunState, workspace, receipt, current):
    """The commits of AutoCode's recorded Builder work since the delivery, or None when unwitnessed."""
    delivered = receipt.get("delivered_source")
    if not isinstance(delivered, dict) or delivered.get("head") != receipt.get("commit"):
        return None
    captures = reconcile.builder_captures(state)
    steps, walked, index = [], delivered, -1
    while walked.get("head") != current.get("head") or walked.get("files") != current.get("files"):
        for order in range(index + 1, len(captures)):
            before, after = captures[order]
            if before.get("head") == walked.get("head") and before.get("files") == walked.get("files"):
                break
        else:
            return None
        index = order
        steps.append(captures[order])
        walked = captures[order][1]
    if not steps or not _changed_in_scope(
        state, workspace, trees.content_files(delivered), trees.content_files(current)
    ):
        return None
    chain = [delivered["head"]]
    for row in (
        line.split()
        for line in git(workspace, "rev-list", "--reverse", "--parents", delivered["head"] + ".." + current["head"])
        .decode()
        .splitlines()
    ):
        if row[1:] != [chain[-1]]:
            return None
        chain.append(row[0])
    if chain[-1] != current.get("head"):
        return None
    witnessed: set[int] = set()
    for before, after in steps:
        try:
            start, stop = chain.index(before["head"]), chain.index(after["head"])
        except ValueError:
            return None
        if stop < start:
            return None
        witnessed.update(range(start + 1, stop + 1))
    return chain[1:] if witnessed == set(range(1, len(chain))) else None


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
        if ready(state, current):
            return (state.get("validation") or {}).get("source_revision") != current["revision"]
        reconciled = _recorded_follow_up(state, workspace, receipt, current)
        if reconciled is None:
            raise ValueError("Source, index or Git HEAD changed after delivery; reconcile before completion")
        redelivery = {
            "version": 1,
            "contract_hash": cast(dict, state["goal_contract"])["hash"],
            "workspace": str(Path(workspace).resolve()),
            "parent": receipt["commit"],
            "reconciled_commits": reconciled,
            "spec": spec,
            "tested_source": current,
            "state_scope": state,
            "superseded": receipt,
        }
        delivered = commit(workspace, current, spec, path, redelivery)
        state["git_delivery"] = {"commit": delivered["commit"], "receipt_sha256": util.file_hash(path)}
        return True
    reconciled = reconcile.builder_commits(state, workspace, current, git)
    baseline = trees.tree_files(workspace, state["base_commit"])
    files = trees.content_files(current)
    if reconciled and files != {
        name: value
        for name, value in trees.tree_files(workspace, current["head"]).items()
        if not util.inventory_excluded(name)
    }:
        raise ValueError("Builder commit reconciliation requires the exact clean committed source")
    if not _changed_in_scope(state, workspace, baseline, files):
        raise ValueError("Atomic Git delivery requires changes wholly within the approved affected paths")
    intent = {
        "version": 1,
        "contract_hash": cast(dict, state["goal_contract"])["hash"],
        "workspace": str(Path(workspace).resolve()),
        "parent": state["base_commit"],
        "reconciled_commits": reconciled,
        "spec": spec,
        "tested_source": current,
        "state_scope": state,
    }
    receipt = commit(workspace, current, spec, path, intent)
    state["git_delivery"] = {"commit": receipt["commit"], "receipt_sha256": util.file_hash(path)}
    return True

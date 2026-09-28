"""Build a multi-component system from an architecture record.

Given the design one AutoCode task can already produce (components, contracts
and a dependency graph — see scenarios/catalog/architecture-two-services), this
drives one independent AutoCode task run per component, in parallel within
each dependency batch, then combines the finished components' changes into
one workspace.

docs/task-lanes.md already runs several tasks; its own limit is the gap this
fills: "Autocode does not guess how to merge parallel source changes." Here,
combining is safe because every component builds against the same committed
contracts, not against another component's code — components with no
dependency between them touch disjoint directories by construction (each
owns `components/<id>/`, checked after the fact, not merely requested), so
parallel work cannot collide, and a dependent component sees its
dependency's *contract*, never its implementation.

AutoCode itself never commits a Builder's changes (see README: "dirty source
changes matter, not only Git commits"), so a component's work exists only as
an uncommitted diff in its own worktree. Integration here uses the same
technique AutoCode's own internal parallel-milestone orchestrator uses
(tools/autocode_dispatch.py): snapshot a worktree's current state into a
detached commit without touching its index or HEAD, diff that against the
worktree's starting commit, and apply the diff elsewhere. It is reimplemented
here, independently, rather than imported, so this module stays outside the
core's import cycle (AGENTS.md rule 5) and is testable on its own.

This is a new layer, not a modification of the single-task engine: it drives
task runs only through `autocode_taskrun.TaskRun` and never imports the
runner's internals or reads state.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

try:
    from .autocode_taskrun import TaskRun, TaskRunError
except ImportError:
    from autocode_taskrun import TaskRun, TaskRunError

_WORKTREE_LOCK = threading.Lock()
GIT_IDENTITY = ("-c", "user.name=AutoCode", "-c", "user.email=autocode@localhost")
EXCLUDE = (":(exclude).autocode", ":(exclude).autocode-ui", ":(exclude,glob)**/__pycache__/**",
          ":(exclude,glob)**/*.pyc")
# Component ids and contract names become path segments (a worktree directory, a
# branch name, a contract filename); an architecture file is data a model wrote,
# not trusted input, so reject anything that could escape its intended directory
# (a slash, a leading dot, ".."). Same pattern already used for task-lane ids in
# autocode_tasks.py.
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _check_safe_name(kind: str, name: str) -> str:
    if not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or ".." in name:
        raise ArchitectureError(f"{kind} {name!r} must be a plain name (letters, digits, '.', '_', '-', "
                                f"no '..', max 64 chars) — it becomes a directory, branch and file name")
    return name


class ArchitectureError(ValueError):
    """The architecture record is malformed or has no valid build order."""


@dataclass(frozen=True)
class Component:
    id: str
    description: str
    requirements: tuple[str, ...]
    depends_on: tuple[str, ...]
    publishes_contracts: tuple[str, ...]
    consumes_contracts: tuple[str, ...]

    @property
    def owned_prefix(self) -> str:
        return f"components/{self.id}/"


@dataclass
class ComponentResult:
    component: Component
    workspace: Path
    base_commit: str | None = None
    run: TaskRun | None = None
    view: dict | None = None
    error: str | None = None
    branch: str | None = None
    resumed: bool = False  # picked up from an earlier invocation's manifest, not started by this one

    @property
    def ready_to_integrate(self) -> bool:
        return self.view is not None and self.view.get("done", False) and not self.error

    @property
    def status(self) -> str:
        if self.ready_to_integrate:
            return "done"
        return "error" if self.view is None else "needs_input"


@dataclass
class Architecture:
    components: dict[str, Component] = field(default_factory=dict)
    contracts_dir: Path | None = None
    directory: Path | None = None

    @classmethod
    def load(cls, directory: Path) -> "Architecture":
        directory = Path(directory)
        raw = _read_json(directory / "components.json")
        if not isinstance(raw, list) or not raw:
            raise ArchitectureError(f"{directory}/components.json must be a nonempty array")
        components = {}
        for row in raw:
            if not isinstance(row, dict) or not row.get("id"):
                raise ArchitectureError("each component needs an id")
            component_id = _check_safe_name("component id", row["id"])
            publishes = tuple(_check_safe_name("contract name", name) for name in row.get("publishes_contracts", []))
            consumes = tuple(_check_safe_name("contract name", name) for name in row.get("consumes_contracts", []))
            components[component_id] = Component(
                id=component_id, description=row.get("description", ""),
                requirements=tuple(row.get("requirements", [])), depends_on=tuple(row.get("depends_on", [])),
                publishes_contracts=publishes, consumes_contracts=consumes)
        for component in components.values():
            unknown = [dep for dep in component.depends_on if dep not in components]
            if unknown:
                raise ArchitectureError(f"{component.id} depends_on unknown component(s) {unknown}")
        return cls(components=components, contracts_dir=directory / "contracts", directory=directory)

    def fingerprint(self) -> str | None:
        """A hash of the files every component is built against: components.json and
        the contract schemas. A saved build is only resumable against the same ones."""
        if self.directory is None:
            return None
        digest = hashlib.sha256()
        files = [self.directory / "components.json"]
        if self.contracts_dir and self.contracts_dir.is_dir():
            files += sorted(self.contracts_dir.glob("*.schema.json"))
        for path in files:
            digest.update(path.relative_to(self.directory).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
        return digest.hexdigest()

    def batches(self) -> list[list[Component]]:
        """Components grouped so a batch's members share no dependency between them,
        and every earlier batch is complete before the next one is considered."""
        remaining = dict(self.components)
        done: set[str] = set()
        result: list[list[Component]] = []
        while remaining:
            ready = [c for c in remaining.values() if all(dep in done for dep in c.depends_on)]
            if not ready:
                raise ArchitectureError(f"dependency cycle among {sorted(remaining)}")
            result.append(sorted(ready, key=lambda c: c.id))
            for component in ready:
                done.add(component.id)
                del remaining[component.id]
        return result


def component_brief(component: Component, architecture: Architecture) -> str:
    """The brief given to the task run building one component.

    Embeds the actual contract schemas, not just their names, so the Builder
    has ground truth for both what it must publish and what it may assume
    about a dependency's data, without reading another component's code.
    """
    lines = [
        f"Implement the {component.id} component of a larger system: {component.description}",
        f"Requirements this component is responsible for: {', '.join(component.requirements) or '(none declared)'}.",
        f"Own only the directory {component.owned_prefix}; do not create or edit any file outside it.",
    ]
    for name in component.publishes_contracts:
        schema = _read_json(architecture.contracts_dir / f"{name}.schema.json")
        lines.append(f"This component publishes the `{name}` contract. Other components will send or store data "
                     f"matching this JSON Schema: {json.dumps(schema)}")
    for name in component.consumes_contracts:
        schema = _read_json(architecture.contracts_dir / f"{name}.schema.json")
        lines.append(f"This component consumes the `{name}` contract, published by another component being built "
                     f"separately. Assume only this JSON Schema about it, nothing about its implementation: "
                     f"{json.dumps(schema)}")
    lines.append("Do not implement or stub another component's directory; integration happens separately.")
    return " ".join(lines)


class MultiComponentBuild:
    """Drives one task run per component of an architecture, then combines them.

    ``repo`` is an existing Git repository with a committed HEAD; the
    architecture files should already be committed there (as, for example,
    the output of an architecture-only task run). Each component gets its own
    worktree under ``repo/.autocode-components/<id>``, created fresh from HEAD.

    Progress is saved to ``repo/.autocode-components/manifest.json`` as each
    component starts and stops, so a later invocation (a new process) picks up
    where this one left off instead of recreating worktrees; see ``build``.
    """

    MANIFEST_VERSION = 1

    def __init__(self, repo: Path, architecture: Architecture, *, options: tuple[str, ...] = (),
                 env: dict | None = None, timeout: float | None = None, max_advances: int = 20):
        self.repo = Path(repo).resolve()
        self.architecture = architecture
        self.options, self.env, self.timeout, self.max_advances = options, env, timeout, max_advances
        self.results: dict[str, ComponentResult] = {}
        self._manifest_lock = threading.Lock()

    @property
    def manifest_path(self) -> Path:
        return self.repo / ".autocode-components" / "manifest.json"

    def saved_components(self) -> dict[str, dict]:
        """The manifest's per-component entries from an earlier invocation, or {} if
        there is none. Refuses a manifest recorded against a different architecture:
        its components were built against contracts that no longer hold."""
        if not self.manifest_path.is_file():
            return {}
        saved = _read_json(self.manifest_path)
        if not isinstance(saved, dict) or saved.get("version") != self.MANIFEST_VERSION:
            raise ArchitectureError(f"{self.manifest_path} is not a version {self.MANIFEST_VERSION} build manifest")
        if saved.get("architecture_fingerprint") != self.architecture.fingerprint():
            raise ArchitectureError(f"the architecture changed since the build saved in {self.manifest_path}; "
                                    f"its components were built against the old contracts. Remove "
                                    f".autocode-components/ to rebuild from scratch.")
        return {cid: entry for cid, entry in saved.get("components", {}).items()
                if cid in self.architecture.components}

    def build(self, *, auto_approve: bool = False) -> dict[str, ComponentResult]:
        """Run every component that is not already done, in dependency batches, in
        parallel within a batch.

        A component this object, or an earlier invocation's saved manifest, already
        started is continued rather than recreated: its existing run is advanced
        from wherever it stopped. A component that is done is left untouched; to
        rebuild one, remove its worktree and manifest entry.

        With ``auto_approve``, plan approval, review acceptance and clarifying
        questions are answered automatically with AutoCode's own proposed
        defaults — appropriate only when a person has delegated that decision
        to this build, as scenario runs do. Without it, a component that needs
        a decision stops with its ``view`` reporting what it needs; the caller
        resolves it through the returned ``ComponentResult.run`` (or the printed
        run directory) and calls ``build`` again, in this process or a new one,
        to continue.
        """
        # A worktree directory removed without `git worktree remove`/`prune` (by hand,
        # or by a crashed earlier attempt) leaves its registration behind; the next
        # `git worktree add` for that path then fails outright. Any registration whose
        # directory is gone is exactly that stale case, safe to clear first.
        _git(self.repo, "worktree", "prune")
        for cid, entry in self.saved_components().items():
            if cid not in self.results and Path(entry["workspace"]).is_dir():
                self.results[cid] = ComponentResult(
                    self.architecture.components[cid], Path(entry["workspace"]), entry.get("base_commit"),
                    branch=entry.get("branch"), error=entry.get("error"), resumed=True)
        for batch in self.architecture.batches():
            pending = [c for c in batch if not (c.id in self.results and self.results[c.id].ready_to_integrate)]
            if not pending:
                continue
            with ThreadPoolExecutor(max_workers=len(pending)) as pool:
                for result in pool.map(lambda c: self._build_one(c, auto_approve), pending):
                    self._record(result)
        return self.results

    def _build_one(self, component: Component, auto_approve: bool) -> ComponentResult:
        previous = self.results.get(component.id)
        result = previous or ComponentResult(component, self.repo / ".autocode-components" / component.id)
        try:
            if not result.workspace.is_dir():
                self._new_worktree(result)
                self._record(result)
            if result.run is None:
                result.run = TaskRun.attach(result.workspace, options=self.options, env=self.env,
                                            timeout=self.timeout)
            if result.run is None:
                result.run = TaskRun.start(result.workspace, component_brief(component, self.architecture),
                                           options=self.options, env=self.env, timeout=self.timeout)
                self._record(result)
            result.view = self._drive(result.run, auto_approve)
            result.error = None if result.view["done"] else f"stopped needing {result.view['needs']}"
        except TaskRunError as error:
            result.view, result.error = None, str(error)
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or str(error)).strip()
            result.view, result.error = None, f"could not prepare a worktree: {detail}"
        return result

    def _drive(self, run: TaskRun, auto_approve: bool) -> dict:
        view = run.status()
        while not view["done"]:
            kind = view["needs"]["kind"]
            if kind == "continue":
                view = run.advance_until_input(self.max_advances)
            elif auto_approve and kind != "resume":
                view = _serve(run, view["needs"])
            else:
                break
        return view

    def _new_worktree(self, result: ComponentResult) -> None:
        result.workspace.parent.mkdir(parents=True, exist_ok=True)
        result.branch = f"components/{result.component.id}-{uuid.uuid4().hex[:8]}"
        # Components in one batch start in parallel threads, but `git worktree add` on one
        # repository is not safe to run concurrently (ref and worktree-metadata locks), so
        # only the git setup is serialized; the component runs themselves stay parallel.
        with _WORKTREE_LOCK:
            result.base_commit = _git(self.repo, "rev-parse", "HEAD")
            _git(self.repo, "worktree", "add", "-b", result.branch, str(result.workspace), result.base_commit)

    def _record(self, result: ComponentResult) -> None:
        """Save progress as soon as a component gains a worktree or a run, not only when
        it stops: a crash in between must not leave a worktree nobody can resume.
        Called from the batch's worker threads, so the results table changes only
        under the same lock that writes it out."""
        with self._manifest_lock:
            self.results[result.component.id] = result
            entries = {cid: {"workspace": str(r.workspace), "branch": r.branch, "base_commit": r.base_commit,
                             "run_dir": str(r.run.run_dir) if r.run else None, "status": r.status,
                             "error": r.error}
                       for cid, r in sorted(self.results.items())}
            document = {"version": self.MANIFEST_VERSION, "architecture": str(self.architecture.directory),
                        "architecture_fingerprint": self.architecture.fingerprint(), "components": entries}
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            scratch = self.manifest_path.with_name(f".manifest-{uuid.uuid4().hex}.json")
            scratch.write_text(json.dumps(document, indent=2) + "\n")
            os.replace(scratch, self.manifest_path)

    def integrate(self, target: Path) -> dict:
        """Apply every finished component's changes into ``target``, an existing
        worktree of the same repository checked out at (or ahead of) the commit
        every component was built from. Ownership is re-checked here, not just
        requested in the brief: a component whose diff touches anything outside
        its own directory is refused rather than silently combined. Stops at the
        first patch that fails ownership or fails to apply, leaving earlier
        components already applied; nothing is committed, matching how AutoCode
        leaves a single task's own work for review before it is committed.
        """
        finished = sorted((r for r in self.results.values() if r.ready_to_integrate), key=lambda r: r.component.id)
        if not finished:
            return {"target": str(target), "integrated": [], "failed": None, "detail": "no finished component"}
        integrated = []
        for result in finished:
            snapshot = _snapshot_commit(result.workspace)
            changed = _git(result.workspace, "diff", "--name-only", result.base_commit, snapshot).splitlines()
            outside = [path for path in changed if not path.startswith(result.component.owned_prefix)]
            if outside:
                return {"target": str(target), "integrated": integrated, "failed": result.component.id,
                        "detail": f"changed files outside {result.component.owned_prefix}: {outside}"}
            patch = _git_bytes(result.workspace, "diff", "--binary", result.base_commit, snapshot)
            if patch:
                check = subprocess.run(["git", "apply", "--check", "--binary", "-"], cwd=target,
                                       input=patch, capture_output=True)
                if check.returncode != 0:
                    return {"target": str(target), "integrated": integrated, "failed": result.component.id,
                            "detail": check.stderr.decode(errors="replace")[-800:]}
                subprocess.run(["git", "apply", "--binary", "-"], cwd=target, input=patch, check=True)
            integrated.append(result.component.id)
        return {"target": str(target), "integrated": integrated, "failed": None}


def _serve(run: TaskRun, need: dict) -> dict:
    kind = need["kind"]
    if kind == "approve_plan":
        return run.approve_plan(need["token"])
    if kind == "answer":
        for question in need["questions"]:
            options = question.get("options") or []
            answer = question.get("proposed_default") or (options[0] if options else "yes")
            run.answer(question["id"], answer)
        return run.status()
    if kind == "review":
        for criterion in need["criteria"]:
            run.approve_review(criterion, need["token"])
        return run.status()
    if kind == "planning_budget":
        return run.feedback("The previous planning cycle used up its review budget. "
                            "Produce a complete final plan now and finalize it.")
    raise TaskRunError(f"no automatic way to serve a {kind!r} need")


def _snapshot_commit(workspace: Path) -> str:
    """A commit capturing the worktree's full current state — including uncommitted
    and untracked changes — without touching its actual index or HEAD. The scratch
    index lives outside the workspace: inside it, `git add -A` would pick up the
    index file itself as an untracked change before it could be removed."""
    index = Path(tempfile.gettempdir()) / f"autocode-multicomponent-index-{uuid.uuid4().hex}"
    env = {"GIT_INDEX_FILE": str(index), "GIT_AUTHOR_NAME": "AutoCode", "GIT_AUTHOR_EMAIL": "autocode@localhost",
          "GIT_COMMITTER_NAME": "AutoCode", "GIT_COMMITTER_EMAIL": "autocode@localhost"}
    try:
        _git(workspace, "read-tree", "HEAD", env=env)
        _git(workspace, "add", "-A", "--", ".", *EXCLUDE, env=env)
        tree = _git(workspace, "write-tree", env=env)
        return _git(workspace, "commit-tree", tree, "-p", "HEAD", "-m", "Component snapshot", env=env)
    finally:
        index.unlink(missing_ok=True)


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    full_env = {**os.environ, **env} if env else {**os.environ}
    return subprocess.run(["git", *GIT_IDENTITY, *args], cwd=cwd, check=True, env=full_env,
                          capture_output=True, text=True).stdout.strip()


def _git_bytes(cwd: Path, *args: str) -> bytes:
    return subprocess.run(["git", *GIT_IDENTITY, *args], cwd=cwd, check=True, capture_output=True).stdout


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise ArchitectureError(f"{path}: {error}") from None

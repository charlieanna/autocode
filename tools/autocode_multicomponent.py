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
    from .autocode_component_design import ComponentDesign, validate_engine
    from .autocode_component_runtime import ComponentRuntime, brief_lines, near_miss_runtime_key, start_layers
    from .autocode_taskrun import TaskRun, TaskRunError
    from .autocode_workspaces import keep_out_of_git
except ImportError:
    from autocode_component_design import ComponentDesign, validate_engine
    from autocode_component_runtime import ComponentRuntime, brief_lines, near_miss_runtime_key, start_layers
    from autocode_taskrun import TaskRun, TaskRunError
    from autocode_workspaces import keep_out_of_git

_WORKTREE_LOCK = threading.Lock()
GIT_IDENTITY = ("-c", "user.name=AutoCode", "-c", "user.email=autocode@localhost")
EXCLUDE = (
    ":(exclude).autocode",
    ":(exclude).autocode-ui",
    ":(exclude,glob)**/__pycache__/**",
    ":(exclude,glob)**/*.pyc",
)
# Explicit --whitespace, so the user's or repository's apply.whitespace (error, fix)
# can neither refuse nor silently rewrite a component's lines.
APPLY = ("git", "apply", "--binary", "--whitespace=nowarn")
# Component ids and contract names become path segments (a worktree directory, a
# branch name, a contract filename); an architecture file is data a model wrote,
# not trusted input, so reject anything that could escape its intended directory
# (a slash, a leading dot, ".."). Same pattern already used for task-lane ids in
# autocode_tasks.py.
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _check_safe_name(kind: str, name: str) -> str:
    if not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or ".." in name:
        raise ArchitectureError(
            f"{kind} {name!r} must be a plain name (letters, digits, '.', '_', '-', "
            f"no '..', max 64 chars) — it becomes a directory, branch and file name"
        )
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
    design: ComponentDesign | None = None
    runtime: ComponentRuntime | None = None  # how it runs in the combined system (autocode_component_runtime)

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
    def load(cls, directory: Path) -> Architecture:
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
            try:
                design = ComponentDesign.load(row, directory)
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                raise ArchitectureError(f"component {component_id} design: {error}") from None
            misspelled = near_miss_runtime_key(row)
            if misspelled is not None:
                raise ArchitectureError(f"component {component_id}: did you mean runtime? (found {misspelled!r})")
            try:
                runtime = ComponentRuntime.load(row, component_id)
            except ValueError as error:
                raise ArchitectureError(f"component {component_id} runtime: {error}") from None
            components[component_id] = Component(
                id=component_id,
                description=row.get("description", ""),
                requirements=tuple(row.get("requirements", [])),
                depends_on=tuple(row.get("depends_on", [])),
                publishes_contracts=publishes,
                consumes_contracts=consumes,
                design=design,
                runtime=runtime,
            )
        for component in components.values():
            unknown = [dep for dep in component.depends_on if dep not in components]
            if unknown:
                raise ArchitectureError(f"{component.id} depends_on unknown component(s) {unknown}")
        try:
            start_layers({cid: component.runtime for cid, component in components.items()})
        except ValueError as error:
            raise ArchitectureError(f"runtime: {error}") from None
        return cls(components=components, contracts_dir=directory / "contracts", directory=directory)

    def fingerprint(self) -> str | None:
        """A hash of the files every component is built against: components.json and
        the contract schemas and any accepted UI handoff. Saved builds require
        the same inputs; legacy text-only fingerprints remain unchanged. A runtime
        block lives inside components.json, so adding or editing one changes this
        identity too, as it should: it changes that component's brief. smoke.json,
        beside components.json, is deliberately left out: it checks the combined
        system and no brief mentions it, so editing it never forces a rebuild."""
        if self.directory is None and not any(c.design is not None for c in self.components.values()):
            return None
        digest = hashlib.sha256()
        if self.directory is not None:
            files = [self.directory / "components.json"]
            if self.contracts_dir and self.contracts_dir.is_dir():
                files += sorted(self.contracts_dir.glob("*.schema.json"))
            for path in files:
                digest.update(path.relative_to(self.directory).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
        for component in sorted(self.components.values(), key=lambda c: c.id):
            if component.design is not None:
                try:
                    pin = component.design.fingerprint()
                except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                    raise ArchitectureError(f"component {component.id} design: {error}") from None
                if pin is None and self.directory is None:
                    pin = component.design.figma_file
                if pin is not None:
                    kind = b"ui_run" if component.design.ui_run is not None else b"figma_file"
                    digest.update(component.id.encode() + b"\0" + kind + b"\0" + pin.encode() + b"\0")
        return digest.hexdigest()

    @property
    def runtimes(self) -> dict[str, ComponentRuntime]:
        """The components that declare how they run, by id."""
        return {cid: component.runtime for cid, component in self.components.items() if component.runtime is not None}

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
    about a dependency's data, without reading another component's code. A
    component with a runtime block is also told how it will run; one without
    gets exactly the brief it always did.
    """
    lines = [
        f"Implement the {component.id} component of a larger system: {component.description}",
        f"Requirements this component is responsible for: {', '.join(component.requirements) or '(none declared)'}.",
        f"Own only the directory {component.owned_prefix}; do not create or edit any file outside it.",
    ]
    # Before the contract lines, each of which ends in raw JSON with no full stop: placed
    # after one, the first runtime sentence would merge into its cue sentence, and quoting
    # the schema sentence alone would count as tracing it (autocode_requirement_cues).
    lines += brief_lines(component.id, architecture.runtimes)
    for name in component.publishes_contracts:
        assert architecture.contracts_dir is not None
        schema = _read_json(architecture.contracts_dir / f"{name}.schema.json")
        lines.append(
            f"This component publishes the `{name}` contract. Other components will send or store data "
            f"matching this JSON Schema: {json.dumps(schema)}"
        )
    for name in component.consumes_contracts:
        assert architecture.contracts_dir is not None
        schema = _read_json(architecture.contracts_dir / f"{name}.schema.json")
        lines.append(
            f"This component consumes the `{name}` contract, published by another component being built "
            f"separately. Assume only this JSON Schema about it, nothing about its implementation: "
            f"{json.dumps(schema)}"
        )
    lines.append("Do not implement or stub another component's directory; integration happens separately.")
    lines.append(
        "The child's mandatory end_to_end_flow must be executable and verified in this component run: "
        "start this component locally, exercise its public interface, and check its result. "
        "Keep a service's real subprocess health and request/response flow local to this component. "
        "The integration owner separately builds containers, runs Compose and checks cross-service smoke; "
        "do not put those deferred integration operations in the child's mandatory flow. "
        "Deliver the component's Dockerfile when requested, without requiring an image build in a child "
        "whose permissions prohibit it. Read-only design and interface inputs outside the owned directory "
        "remain inputs, not deliverable or affected paths."
    )
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

    def __init__(
        self,
        repo: Path,
        architecture: Architecture,
        *,
        options: tuple[str, ...] = (),
        env: dict | None = None,
        timeout: float | None = None,
        max_advances: int = 20,
    ):
        self.repo = Path(repo).resolve()
        self.architecture = architecture
        self.options, self.env, self.timeout, self.max_advances = options, env, timeout, max_advances
        if any(c.design is not None for c in architecture.components.values()):
            try:
                validate_engine(options)
            except ValueError as error:
                raise ArchitectureError(str(error)) from None
        # Never bless changed inputs when recording a component's later progress.
        self._architecture_fingerprint = architecture.fingerprint()
        self.results: dict[str, ComponentResult] = {}
        self._manifest_lock = threading.Lock()

    @property
    def manifest_path(self) -> Path:
        return self.repo / ".autocode-components" / "manifest.json"

    def saved_components(self) -> dict[str, dict]:
        """The manifest's per-component entries from an earlier invocation, or {} if
        there is none. Refuses a manifest recorded against a different architecture:
        its components were built against contracts that no longer hold."""
        self._check_architecture()
        if not self.manifest_path.is_file():
            return {}
        saved = _read_json(self.manifest_path)
        if not isinstance(saved, dict) or saved.get("version") != self.MANIFEST_VERSION:
            raise ArchitectureError(f"{self.manifest_path} is not a version {self.MANIFEST_VERSION} build manifest")
        if saved.get("architecture_fingerprint") != self._architecture_fingerprint:
            raise ArchitectureError(
                f"the architecture changed since the build saved in {self.manifest_path}; "
                f"its components were built against the old contracts or designs. Remove "
                f".autocode-components/ to rebuild from scratch."
            )
        return {cid: entry for cid, entry in saved.get("components", {}).items() if cid in self.architecture.components}

    def _check_architecture(self) -> None:
        if self.architecture.fingerprint() != self._architecture_fingerprint:
            raise ArchitectureError(
                "the architecture or accepted component design changed during this build; "
                "saved component runs still use the original inputs"
            )

    def record_evidence_report(self, anchor: dict) -> None:
        """Attach canonical report digests to this coordinator's owned manifest."""
        try:
            from . import autocode_evidence_export as evidence_export
            from . import autocode_util as util
        except ImportError:
            import autocode_evidence_export as evidence_export
            import autocode_util as util
        with self._manifest_lock:
            self._check_architecture()
            saved = _read_json(self.manifest_path)
            evidence_export.record(saved, anchor)
            util.atomic_json(self.manifest_path, saved)

    def evidence_report(self) -> dict:
        """Read the saved pair without changing a child run or this manifest."""
        try:
            from . import autocode_evidence_export as evidence_export
        except ImportError:
            import autocode_evidence_export as evidence_export
        self._check_architecture()
        saved = _read_json(self.manifest_path) if self.manifest_path.is_file() else {}
        anchor = saved.get("evidence_export")
        return (evidence_export.read(self.manifest_path.parent, anchor, include=True)
                if isinstance(anchor, dict) else evidence_export.unavailable())

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
        saved = self.saved_components()
        _git(self.repo, "worktree", "prune")
        for cid, entry in saved.items():
            if cid not in self.results and Path(entry["workspace"]).is_dir():
                self.results[cid] = ComponentResult(
                    self.architecture.components[cid],
                    Path(entry["workspace"]),
                    entry.get("base_commit"),
                    branch=entry.get("branch"),
                    error=entry.get("error"),
                    resumed=True,
                )
        for batch in self.architecture.batches():
            pending = [c for c in batch if not (c.id in self.results and self.results[c.id].ready_to_integrate)]
            if not pending:
                continue
            with ThreadPoolExecutor(max_workers=len(pending)) as pool:
                for result in pool.map(lambda c: self._build_one(c, auto_approve), pending):
                    self._record(result)
        self._check_architecture()
        return self.results

    def _build_one(self, component: Component, auto_approve: bool) -> ComponentResult:
        previous = self.results.get(component.id)
        result = previous or ComponentResult(component, self.repo / ".autocode-components" / component.id)
        try:
            self._check_architecture()
            if not result.workspace.is_dir():
                self._new_worktree(result)
                self._record(result)
            if result.run is None:
                result.run = TaskRun.attach(result.workspace, options=self.options, env=self.env, timeout=self.timeout)
            if result.run is None:
                result.run = TaskRun.start(
                    result.workspace,
                    component_brief(component, self.architecture),
                    options=self.options,
                    start_options=(
                        "--test-root",
                        component.owned_prefix,
                        *(component.design.start_options() if component.design else ()),
                    ),
                    env=self.env,
                    timeout=self.timeout,
                )
                self._record(result)
            result.view = self._drive(result.run, auto_approve)
            result.error = None if result.view["done"] else f"stopped needing {result.view['needs']}"
        except (TaskRunError, ArchitectureError) as error:
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
        keep_out_of_git(self.repo, ".autocode-components")
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
            entries = {
                cid: {
                    "workspace": str(r.workspace),
                    "branch": r.branch,
                    "base_commit": r.base_commit,
                    "run_dir": str(r.run.run_dir) if r.run else None,
                    "status": r.status,
                    "error": r.error,
                }
                for cid, r in sorted(self.results.items())
            }
            document = {
                "version": self.MANIFEST_VERSION,
                "architecture": str(self.architecture.directory),
                "architecture_fingerprint": self._architecture_fingerprint,
                "components": entries,
            }
            keep_out_of_git(self.repo, ".autocode-components")
            scratch = self.manifest_path.with_name(f".manifest-{uuid.uuid4().hex}.json")
            scratch.write_text(json.dumps(document, indent=2) + "\n")
            os.replace(scratch, self.manifest_path)

    def integrate(self, target: Path) -> dict:
        """Apply every finished component's changes into ``target``, the top
        directory of an existing worktree of the same repository checked out at
        (or ahead of) the commit every component was built from; any other
        ``target`` raises ArchitectureError before anything is written.
        Ownership is re-checked here, not just requested in the brief: a
        component whose diff touches anything outside its own directory (moving
        a file in from elsewhere included) is refused rather than silently
        combined. Stops at the first component that fails ownership or fails to
        apply, leaving earlier components already applied; nothing is committed,
        matching how AutoCode leaves a single task's own work for review before
        it is committed.

        Integrating into the same ``target`` again is safe. A component is
        ``already_applied`` (and also listed in ``integrated``) when ``target``
        holds exactly its result: the same content and file mode at every path
        it changed, and none of the paths it deleted. Its patch is applied only
        when ``target`` holds none of that result yet, so a rerun never applies
        a change twice. Any other target fails at that component, with the
        paths that differ and the remedy that would work.
        """
        self._check_architecture()
        target = Path(target)
        _check_target(self.repo, target)
        finished = sorted((r for r in self.results.values() if r.ready_to_integrate), key=lambda r: r.component.id)
        integrated: list[str] = []
        already_applied: list[str] = []
        outcome = {"target": str(target), "integrated": integrated, "already_applied": already_applied, "failed": None}
        if not finished:
            return {**outcome, "detail": "no finished component"}
        for result in finished:
            assert result.base_commit is not None
            cid, prefix = result.component.id, result.component.owned_prefix
            snapshot = _snapshot_commit(result.workspace)
            changed = _changed_paths(result.workspace, result.base_commit, snapshot)
            outside = [path for path in changed if not path.startswith(prefix)]
            if outside:
                return {**outcome, "failed": cid, "detail": f"changed files outside {prefix}: {outside}"}
            unheld = _differences(target, snapshot, changed, prefix) if changed else []
            if changed and not unheld:
                already_applied.append(cid)
            elif unheld and len(unheld) < len(changed):
                held = sorted(set(changed) - set(unheld))
                return {
                    **outcome,
                    "failed": cid,
                    "detail": self._conflict(
                        result, target, changed, f"{target} already holds {cid}'s version of {held} but not of {unheld}"
                    ),
                }
            elif unheld:
                patch = _git_bytes(
                    result.workspace, "diff-tree", "-r", "-p", "--binary", "--no-renames", result.base_commit, snapshot
                )
                check = subprocess.run([*APPLY, "--check", "-"], cwd=target, input=patch, capture_output=True)
                if check.returncode != 0:
                    reason = check.stderr.decode(errors="replace")[-800:].strip() or "git apply --check failed"
                    return {**outcome, "failed": cid, "detail": self._conflict(result, target, changed, reason)}
                subprocess.run([*APPLY, "-"], cwd=target, input=patch, check=True, capture_output=True)
            integrated.append(cid)
        return outcome

    def _conflict(self, result: ComponentResult, target: Path, changed: list[str], reason: str) -> str:
        """``reason`` plus the remedy that would work. A target holding what the
        component was built on at every path it changed is not a version
        mismatch, so git's own reason stands alone. Otherwise a new target, made
        from HEAD, helps only while HEAD still holds what the component was
        built on there; once HEAD has changed those paths, only rebuilding the
        component on the current HEAD does."""
        cid, prefix, base = result.component.id, result.component.owned_prefix, result.base_commit
        assert base is not None
        off_base = _differences(target, base, changed, prefix)
        if not off_base:
            return reason
        moved = sorted(set(changed) & set(_changed_paths(self.repo, base, "HEAD", prefix)))
        if moved:
            return (
                f"{reason}; HEAD changed {moved} since {cid} was built, so a new target made from HEAD "
                f"would not take {cid} either: rebuild it on the current HEAD by removing its worktree "
                f".autocode-components/{cid} and running the build again"
            )
        return (
            f"{reason}; {target} differs at {off_base} from the commit {cid} was built on (changed there, "
            f"or checked out at another commit): integrate into a new target"
        )


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
        return run.feedback(
            "The previous planning cycle used up its review budget. Produce a complete final plan now and finalize it."
        )
    raise TaskRunError(f"no automatic way to serve a {kind!r} need")


def _snapshot_commit(workspace: Path) -> str:
    """A commit capturing the worktree's full current state — including uncommitted
    and untracked changes — without touching its actual index or HEAD. The scratch
    index lives outside the workspace: inside it, `git add -A` would pick up the
    index file itself as an untracked change before it could be removed."""
    index = Path(tempfile.gettempdir()) / f"autocode-multicomponent-index-{uuid.uuid4().hex}"
    env = {
        "GIT_INDEX_FILE": str(index),
        "GIT_AUTHOR_NAME": "AutoCode",
        "GIT_AUTHOR_EMAIL": "autocode@localhost",
        "GIT_COMMITTER_NAME": "AutoCode",
        "GIT_COMMITTER_EMAIL": "autocode@localhost",
    }
    try:
        _git(workspace, "read-tree", "HEAD", env=env)
        _git(workspace, "add", "-A", "--", ".", *EXCLUDE, env=env)
        tree = _git(workspace, "write-tree", env=env)
        return _git(workspace, "commit-tree", tree, "-p", "HEAD", "-m", "Component snapshot", env=env)
    finally:
        index.unlink(missing_ok=True)


def _check_target(repo: Path, target: Path) -> None:
    """Refuse an integration ``target`` that is not the top directory of a
    worktree of ``repo``. From a plain subdirectory, `git apply` skips every
    path outside it and still succeeds, so nothing would be written while the
    integration reported success."""
    try:
        top = _git(target, "rev-parse", "--show-toplevel")
        common = Path(target, _git(target, "rev-parse", "--git-common-dir"))
        ours = Path(repo, _git(repo, "rev-parse", "--git-common-dir"))
        usable = os.path.samefile(top, target) and os.path.samefile(common, ours)
    except (OSError, subprocess.CalledProcessError):
        usable = False
    if not usable:
        raise ArchitectureError(f"integration target {target} is not the top directory of a worktree of {repo}")


def _changed_paths(cwd: Path, old: str, new: str, *pathspec: str) -> list[str]:
    """Every path that differs between two commits, as is: a rename is listed as
    its deletion and its addition (so the source is ownership-checked too), and
    names are not quoted. Plumbing, so the user's diff settings do not apply."""
    listing = _git_bytes(cwd, "diff-tree", "-r", "-z", "--no-renames", "--name-only", old, new, "--", *pathspec)
    return [os.fsdecode(name) for name in listing.split(b"\0") if name]


def _differences(target: Path, commit: str, paths: list[str], prefix: str) -> list[str]:
    """The ``paths`` (all under ``prefix``) where ``target``'s working tree does
    not hold exactly what ``commit`` holds: other content, another file mode or
    type, a file ``commit`` does not have, or a missing one. Compared through a
    scratch index outside the worktree, so Git's own rules (clean filters,
    core.fileMode, core.symlinks) decide what counts as the same file, as they
    did when the component's snapshot was taken."""
    wanted = set(paths)
    entries = {}
    for entry in _git_bytes(target, "ls-tree", "-r", "-z", commit, "--", prefix).split(b"\0"):
        path = os.fsdecode(entry.partition(b"\t")[2])
        if path in wanted:
            entries[path] = entry
    differ = [path for path in wanted - entries.keys() if os.path.lexists(target / path)]
    if entries:
        index = Path(tempfile.gettempdir()) / f"autocode-multicomponent-index-{uuid.uuid4().hex}"
        env = {"GIT_INDEX_FILE": str(index)}
        try:
            records = b"".join(entry + b"\0" for entry in entries.values())
            _git_bytes(target, "update-index", "-z", "--index-info", env=env, input=records)
            _git_bytes(target, "update-index", "-q", "--refresh", env=env)
            listing = _git_bytes(target, "diff-files", "--name-only", "-z", env=env)
            differ += [os.fsdecode(name) for name in listing.split(b"\0") if name]
        finally:
            index.unlink(missing_ok=True)
    return sorted(differ)


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    full_env = {**os.environ, **env} if env else {**os.environ}
    return subprocess.run(
        ["git", *GIT_IDENTITY, *args], cwd=cwd, check=True, env=full_env, capture_output=True, text=True
    ).stdout.strip()


def _git_bytes(cwd: Path, *args: str, env: dict | None = None, input: bytes | None = None) -> bytes:
    full_env = {**os.environ, **env} if env else None
    return subprocess.run(
        ["git", *GIT_IDENTITY, *args], cwd=cwd, check=True, env=full_env, input=input, capture_output=True
    ).stdout


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise ArchitectureError(f"{path}: {error}") from None

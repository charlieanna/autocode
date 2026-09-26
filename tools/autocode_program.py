"""Program-level decomposition: parallel workstreams merged onto an integration branch.

A large requirement does not fit one bounded run. A **program manifest**
(version 1) declares workstreams with explicit ``depends_on`` and literal
``owns`` paths. Each workstream is an ordinary AutoCode run (planning, exact
approval, Builders, independent validation, completion) in its own Git
worktree, branched from the program's integration branch so downstream work
always starts from merged upstream results. Completed workstreams are
committed on their branch and merged ``--no-ff``; a conflict pauses for a
human. The integration workstream runs on the integration branch itself.
Deployment workstreams never launch without ``--authorize-deployment``.

What this module does **not** do: it never approves a child plan, never
merges the integration branch into the project's default branch, never
resolves conflicts, and never treats a child's exit code as completion. The
child run's saved ``state.json`` is the only source of a workstream's status.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import uuid

try:
    from . import autocode_support as support, autocode_workspaces as workspaces
    from . import autocode_goals as goals, autocode_planning_graph as graph
except ImportError:
    import autocode_support as support
    import autocode_workspaces as workspaces
    import autocode_goals as goals
    import autocode_planning_graph as graph


KINDS = ("code", "ui", "integration", "deployment")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
TERMINAL_CODE = {"TASK_COMPLETE"}
STATE_LOCK = threading.Lock()  # worker threads update records; the main thread serializes state
TERMINAL_UI = {"COMPLETE"}
WAITING_CODE = {"WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL"}
GIT_IDENTITY = ("-c", "user.name=Autocode", "-c", "user.email=autocode@localhost")
SHARED_LISTS = ("constraints", "permission_boundaries", "end_to_end_flow", "technical_approach", "deliverables")

PLAN_PREAMBLE = (
    "PROGRAM PLANNING. This request is too large for one bounded task. Plan it as a program: "
    "propose milestones that are independently deliverable workstreams, each with a disjoint set "
    "of affected_paths it exclusively owns, explicit depends_on, and acceptance criteria that can be "
    "verified on that workstream's own merged result. Put shared interface contracts (schemas, API "
    "shapes, module boundaries) in the earliest milestone so dependents inherit them. Prefer an "
    "early thin end-to-end slice over finishing subsystems one at a time. Deployment, credentials "
    "and external systems are outside every milestone; describe deployment as a separate, later, "
    "separately authorized step. The user approves this plan; then each milestone becomes its own "
    "reviewed AutoCode run in its own worktree, merged onto one integration branch.\n\nREQUEST:\n"
)


# --- manifest ---------------------------------------------------------------

def _string_list(value, where):
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{where} must be a list of nonempty strings")
    return list(value)


def _owned_path(value, where):
    if not isinstance(value, str) or not value.strip() or any(c.isspace() for c in value):
        raise ValueError(f"{where}: ownership paths must be nonblank repository-relative paths")
    path = Path(value)
    parts = path.parts
    if path.is_absolute() or ".." in parts or not parts or parts[0] in (".git", ".autocode"):
        raise ValueError(f"{where}: {value!r} is not an allowed repository-relative path")
    return path.as_posix().rstrip("/")


def _reachable(edges, start, target):
    todo, seen = [start], set()
    while todo:
        node = todo.pop()
        if node == target:
            return True
        if node not in seen:
            seen.add(node)
            todo.extend(edges[node])
    return False


def validate_manifest(value):
    """Check a manifest dict; return it unchanged. Raises ValueError with the first defect."""
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Program manifest needs version 1")
    for key in ("name", "brief"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"Program manifest needs a nonempty {key}")
    shared = value.get("shared", {})
    if not isinstance(shared, dict):
        raise ValueError("shared must be an object")
    for key in SHARED_LISTS:
        if key in shared:
            _string_list(shared[key], f"shared.{key}")
    for row in shared.get("interfaces", []):
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip()
                or not isinstance(row.get("summary"), str)):
            raise ValueError("shared.interfaces entries need id and summary")
        for path in row.get("paths", []):
            _owned_path(path, f"interface {row['id']}")
    rows = value.get("workstreams")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Program manifest needs at least one workstream")
    allowed = {"id", "kind", "brief", "owns", "depends_on", "acceptance_criteria", "engine", "figma_file"}
    ids = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - allowed:
            raise ValueError(f"Workstream fields must be within {sorted(allowed)}")
        if not isinstance(row.get("id"), str) or not ID_RE.fullmatch(row["id"]):
            raise ValueError("Every workstream needs an id of letters, digits, dot, underscore or dash")
        if row.get("kind") not in KINDS:
            raise ValueError(f"Workstream {row['id']}: kind must be one of {', '.join(KINDS)}")
        if not isinstance(row.get("brief"), str) or not row["brief"].strip():
            raise ValueError(f"Workstream {row['id']}: brief must be a nonempty string")
        if row.get("engine") not in (None, "codex", "opencode"):
            raise ValueError(f"Workstream {row['id']}: engine must be codex or opencode")
        if row.get("engine") and row["kind"] == "ui":
            raise ValueError(f"Workstream {row['id']}: engine applies only to code runs")
        if row.get("figma_file") and row["kind"] != "ui":
            raise ValueError(f"Workstream {row['id']}: figma_file applies only to ui workstreams")
        row["owns"] = [_owned_path(p, f"workstream {row['id']}") for p in _string_list(row.get("owns", []), f"workstream {row['id']}.owns")]
        if not row["owns"] and row["kind"] in ("code", "ui"):
            raise ValueError(f"Workstream {row['id']}: code and ui workstreams must declare the paths they own")
        row["depends_on"] = _string_list(row.get("depends_on", []), f"workstream {row['id']}.depends_on")
        if "acceptance_criteria" in row:
            _string_list(row["acceptance_criteria"], f"workstream {row['id']}.acceptance_criteria")
        ids.append(row["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("Workstream ids must be unique")
    edges = {row["id"]: row["depends_on"] for row in rows}
    for node, deps in edges.items():
        if len(deps) != len(set(deps)) or node in deps or any(dep not in edges for dep in deps):
            raise ValueError(f"Workstream {node} depends on an unknown, duplicate or self workstream")
    visiting, done = set(), set()

    def visit(node):
        if node in visiting:
            raise ValueError("Workstream dependencies contain a cycle")
        if node not in done:
            visiting.add(node)
            for dep in edges[node]:
                visit(dep)
            visiting.remove(node)
            done.add(node)

    for node in edges:
        visit(node)
    # Ownership must be disjoint between workstreams that may run at the same time.
    for index, left in enumerate(rows):
        for right in rows[index + 1:]:
            if _reachable(edges, left["id"], right["id"]) or _reachable(edges, right["id"], left["id"]):
                continue
            for a in left["owns"]:
                for b in right["owns"]:
                    if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                        raise ValueError(f"Workstreams {left['id']} and {right['id']} can run together but both own {a!r}/{b!r}; "
                                         "add a dependency or split the ownership")
    integrations = [row["id"] for row in rows if row["kind"] == "integration"]
    if len(integrations) > 1:
        raise ValueError("At most one integration workstream is allowed")
    deployments = {row["id"] for row in rows if row["kind"] == "deployment"}
    for row in rows:
        if row["kind"] != "deployment" and any(dep in deployments for dep in row["depends_on"]):
            raise ValueError(f"Workstream {row['id']} cannot depend on a deployment workstream")
    if integrations:
        integration = integrations[0]
        for row in rows:
            if row["id"] == integration or row["kind"] == "deployment":
                continue
            if not _reachable(edges, integration, row["id"]):
                raise ValueError(f"Integration workstream {integration} must (transitively) depend on {row['id']}")
        for dep in deployments:
            if not _reachable(edges, dep, integration):
                raise ValueError(f"Deployment workstream {dep} must (transitively) depend on the integration workstream")
    return value


def load_manifest(path):
    source = Path(path).resolve()
    value = json.loads(source.read_text())
    return source, validate_manifest(value)


def program_key(manifest):
    """Programs are keyed by name: a saved program's manifest is frozen, not silently replaced."""
    slug = re.sub("[^a-z0-9]+", "-", manifest["name"].lower()).strip("-")[:40] or "program"
    return slug + "-" + hashlib.sha256(manifest["name"].encode()).hexdigest()[:6]


# --- derive a manifest from an approved plan ---------------------------------

def derive_manifest(state, *, name=None, source_run=None):
    """Turn an approved AutoCode contract into a program manifest, one workstream per milestone.

    The approved plan is the program plan: nothing is re-planned here. Every
    milestone's declared ``affected_paths`` become its ownership, its
    ``depends_on`` become workstream dependencies, and one integration
    workstream depending on every sink milestone validates the whole flow.
    """
    if not goals.approved(state):
        raise ValueError("Derive a program only from an approved plan (approve the displayed goal first)")
    contract = state["goal_contract"]
    body = contract["body"]
    graph.derive(body)
    criteria = {row["id"]: row for row in body.get("acceptance_criteria", [])}
    milestones = body["milestones"]
    workstreams = []
    for milestone in milestones:
        lines = [milestone["objective"]]
        for cid in milestone.get("acceptance_criteria", []):
            row = criteria.get(cid, {})
            lines.append(f"{cid}: {row.get('criterion', '')} (verify: {row.get('verification_method', '')})")
        workstreams.append({
            "id": milestone["id"], "kind": "code", "brief": "\n".join(lines),
            "owns": list(milestone.get("affected_paths") or []),
            "depends_on": list(milestone.get("depends_on", [])),
            "acceptance_criteria": list(milestone.get("acceptance_criteria", [])),
        })
    depended = {dep for milestone in milestones for dep in milestone.get("depends_on", [])}
    sinks = [milestone["id"] for milestone in milestones if milestone["id"] not in depended]
    flow = body.get("end_to_end_flow", [])
    workstreams.append({
        "id": "integration", "kind": "integration", "owns": [], "depends_on": sinks,
        "brief": ("Validate the complete approved flow on the merged result of every workstream:\n- "
                  + "\n- ".join(flow) + "\nRepair only integration defects between merged workstreams; "
                  "do not reimplement a workstream or widen scope. Every acceptance criterion of the "
                  "program must pass on this branch."),
        "acceptance_criteria": sorted(criteria),
    })
    manifest = {
        "version": 1,
        "name": name or body.get("intended_outcome", "program")[:60],
        "brief": body.get("intended_outcome", ""),
        "contract": {"task_id": contract.get("task_id"), "revision": contract["revision"], "hash": contract["hash"]},
        "shared": {key: list(body.get(key, [])) for key in SHARED_LISTS if body.get(key)},
        "workstreams": workstreams,
    }
    manifest["shared"]["interfaces"] = []
    if source_run:
        manifest["source_run"] = str(source_run)
    try:
        return validate_manifest(manifest)
    except ValueError as error:
        raise ValueError(f"The approved plan cannot run as a program without changes: {error}") from error


# --- state ------------------------------------------------------------------

def new_state(source, manifest, project, key):
    return {"version": 1, "name": manifest["name"], "key": key, "manifest": str(source),
            "manifest_sha256": support.file_hash(source), "project_workspace": str(project),
            "created_at": support.now(), "status": "RUNNING", "integration": None,
            "workstreams": {row["id"]: {"status": "PENDING", "kind": row["kind"]} for row in manifest["workstreams"]},
            "events": []}


def note(state, event, **detail):
    state.setdefault("events", []).append({"at": support.now(), "event": event, **detail})


def _worktree(project, name, branch, base):
    parent = project / ".autocode/worktrees"
    parent.mkdir(parents=True, exist_ok=True)
    workspace = parent / name
    workspaces.git(project, "worktree", "add", "-b", branch, str(workspace), base)
    data = {"version": 1, "project_workspace": str(project), "workspace": str(workspace),
            "branch": branch, "base_commit": base}
    artifact = workspace / ".autocode/task-workspace.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(data, indent=2) + "\n")
    return data


def ensure_integration(project, state):
    if state.get("integration"):
        return state["integration"]
    base = workspaces.git(project, "rev-parse", "--verify", "HEAD")
    key = state["key"]
    data = _worktree(project, f"program-{key}-integration", f"autocode/program-{key}/integration", base)
    state["integration"] = data
    note(state, "integration_branch_created", branch=data["branch"], base=base)
    return data


def integration_head(state):
    return workspaces.git(state["integration"]["workspace"], "rev-parse", "--verify", "HEAD")


# --- briefs -----------------------------------------------------------------

def compose_brief(manifest, workstream, state):
    shared = manifest.get("shared", {})
    others = {row["id"]: row for row in manifest["workstreams"] if row["id"] != workstream["id"]}
    merged = [wid for wid, record in state["workstreams"].items() if record.get("status") == "MERGED" and wid in others]
    lines = [f"PROGRAM WORKSTREAM {workstream['id']} ({workstream['kind']}) of program \"{manifest['name']}\".",
             "", "Program outcome: " + manifest["brief"].strip(), ""]
    for key, label in (("constraints", "Shared constraints"), ("permission_boundaries", "Permission boundaries"),
                       ("technical_approach", "Shared technical approach"), ("end_to_end_flow", "Program end-to-end flow")):
        if shared.get(key):
            lines += [label + ":"] + [f"- {item}" for item in shared[key]] + [""]
    if shared.get("interfaces"):
        lines.append("Shared interfaces (read-only unless this workstream owns their paths):")
        lines += [f"- {row['id']}: {row['summary']} [{', '.join(row.get('paths', [])) or 'no paths'}]" for row in shared["interfaces"]]
        lines.append("")
    if merged:
        lines.append("Prerequisite workstreams already merged on this branch (use their results; do not redo them):")
        lines += [f"- {wid}: {others[wid]['brief'].splitlines()[0]}" for wid in merged]
        lines.append("")
    lines += ["This workstream's objective:", workstream["brief"].strip(), ""]
    if workstream.get("acceptance_criteria"):
        lines += ["Acceptance criteria for this workstream:"] + [f"- {item}" for item in workstream["acceptance_criteria"]] + [""]
    if workstream["owns"]:
        lines += ["Ownership: create or modify files only under: " + ", ".join(workstream["owns"]) + "."]
    else:
        lines += ["Ownership: unbounded within this workspace; change other workstreams' files only to fix integration defects."]
    foreign = sorted({p for row in others.values() for p in row["owns"]})
    if foreign and workstream["owns"]:
        lines += ["Paths owned by other workstreams (do not modify): " + ", ".join(foreign) + "."]
    lines += ["Do not deploy, do not access external systems, and do not merge branches; the program "
              "controller integrates completed workstreams."]
    return "\n".join(lines)


# --- child runs -------------------------------------------------------------

def refresh(record):
    run = record.get("run_dir")
    if not run:
        return
    path = Path(run) / "state.json"
    if not path.is_file():
        return
    saved = json.loads(path.read_text())
    status = saved.get("status")
    record["run_status"] = status
    terminal = TERMINAL_UI if record["kind"] == "ui" else TERMINAL_CODE
    if record["status"] == "MERGED":
        return
    if status in terminal:
        if record["status"] != "COMPLETE":
            record.update(status="COMPLETE", finished_at=support.now())
    elif status in WAITING_CODE:
        record["status"] = "WAITING"
    elif status not in ("RUNNING", None):
        record["status"] = "PAUSED"


def launch(project, program_dir, manifest, workstream, record, state, options):
    """Run one workstream as an ordinary AutoCode run; never approve anything on its behalf."""
    runner = Path(__file__).with_name("autocode.py")
    if workstream["kind"] == "integration":
        workspace = Path(state["integration"]["workspace"])
        record.update(workspace=str(workspace), branch=state["integration"]["branch"])
    elif not record.get("workspace"):
        base = integration_head(state)
        suffix = uuid.uuid4().hex[:8]
        data = _worktree(project, f"program-{state['key']}-{workstream['id']}-{suffix}",
                         f"autocode/program-{state['key']}/{workstream['id']}-{suffix}", base)
        record.update(workspace=data["workspace"], branch=data["branch"], base_commit=base)
        workspace = Path(data["workspace"])
    else:
        workspace = Path(record["workspace"])
    brief = compose_brief(manifest, workstream, state)
    artifact = program_dir / workstream["id"]
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "brief.md").write_text(brief + "\n")
    if record.get("run_dir"):
        command = [sys.executable, str(runner), "--workspace", str(workspace), "--run-dir", record["run_dir"], "--no-chat"]
    elif workstream["kind"] == "ui":
        run = artifact / "ui"
        command = [sys.executable, str(runner), "ui", brief, "--workspace", str(workspace), "--run-dir", str(run)]
        if workstream.get("figma_file"):
            command += ["--figma-file", workstream["figma_file"]]
    else:
        command = [sys.executable, str(runner), brief, "--workspace", str(workspace), "--in-place", "--no-chat"]
        engine = workstream.get("engine") or options.get("engine")
        if engine:
            command += ["--engine", engine]
        command += list(options.get("passthrough", []))
    with STATE_LOCK:
        record.update(status="RUNNING", started_at=support.now(), command=command)
    before = set(workspace.glob(".autocode/runs/*"))
    result = subprocess.run(command, cwd=workspace, capture_output=True, text=True)
    (artifact / "stdout.log").write_text(result.stdout)
    (artifact / "stderr.log").write_text(result.stderr)
    with STATE_LOCK:
        if workstream["kind"] == "ui":
            record["run_dir"] = str(artifact / "ui")
        elif not record.get("run_dir"):
            created = set(workspace.glob(".autocode/runs/*")) - before
            if len(created) == 1:
                record["run_dir"] = str(created.pop().resolve())
        record.update(exit_code=result.returncode, last_invocation_at=support.now())
        refresh(record)
        if record["status"] == "RUNNING":
            record["status"] = "FAILED" if result.returncode not in (0, 2) or not record.get("run_dir") else "WAITING"
    return record


# --- integration ------------------------------------------------------------

def _commit_all(workspace, message):
    """Commit every change except runner metadata; return the new commit or None when clean."""
    workspaces.git(workspace, "add", "-A", "--", ".", ":!.autocode")
    if not workspaces.git(workspace, "diff", "--cached", "--name-only"):
        return None
    workspaces.git(workspace, *GIT_IDENTITY, "commit", "-q", "-m", message)
    return workspaces.git(workspace, "rev-parse", "--verify", "HEAD")


def integrate(state, workstream, record):
    """Merge one completed workstream onto the integration branch; pause on conflict."""
    integration = Path(state["integration"]["workspace"])
    if workspaces.git(integration, "status", "--porcelain", "--untracked-files=no"):
        raise support.Paused("PAUSED_INTEGRATION_DIRTY",
                             f"The integration worktree {integration} has uncommitted tracked changes; commit or restore them")
    title = workstream["brief"].strip().splitlines()[0][:72]
    if workstream["kind"] == "integration":
        commit = _commit_all(integration, f"Program {state['name']}: {workstream['id']} - {title}")
        record.update(status="MERGED", merged_commit=commit or integration_head(state), merged_at=support.now(),
                      merge_note="committed on the integration branch" if commit else "no source changes")
        note(state, "workstream_merged", workstream=workstream["id"], commit=record["merged_commit"])
        return
    workspace = Path(record["workspace"])
    commit = _commit_all(workspace, f"Program {state['name']}: {workstream['id']} - {title}")
    if commit is None and workspaces.git(workspace, "rev-parse", "--verify", "HEAD") == record.get("base_commit"):
        record.update(status="MERGED", merged_commit=integration_head(state), merged_at=support.now(),
                      merge_note="no source changes")
        note(state, "workstream_merged", workstream=workstream["id"], commit=record["merged_commit"], changes=False)
        return
    result = subprocess.run(["git", "-C", str(integration), *GIT_IDENTITY, "merge", "--no-ff", "--no-edit",
                             "-m", f"Merge workstream {workstream['id']} into program {state['name']}", record["branch"]],
                            capture_output=True, text=True)
    if result.returncode:
        subprocess.run(["git", "-C", str(integration), "merge", "--abort"], capture_output=True, text=True)
        record.update(status="CONFLICT", conflict=(result.stdout + result.stderr).strip()[-2000:])
        note(state, "merge_conflict", workstream=workstream["id"], branch=record["branch"])
        raise support.Paused(
            "PAUSED_MERGE_CONFLICT",
            f"Merging workstream {workstream['id']} ({record['branch']}) into {state['integration']['branch']} conflicts. "
            f"Resolve it by hand in {integration} (git merge --no-ff {record['branch']}), commit, then rerun the program.")
    record.update(status="MERGED", merged_commit=integration_head(state), merged_at=support.now())
    note(state, "workstream_merged", workstream=workstream["id"], commit=record["merged_commit"])


def adopt_manual_merge(state, workstream, record):
    """A human resolved a conflict and committed: accept the branch as merged when its tip is an ancestor."""
    integration = Path(state["integration"]["workspace"])
    result = subprocess.run(["git", "-C", str(integration), "merge-base", "--is-ancestor", record["branch"], "HEAD"],
                            capture_output=True, text=True)
    if result.returncode == 0:
        record.update(status="MERGED", merged_commit=integration_head(state), merged_at=support.now(),
                      merge_note="conflict resolved manually")
        record.pop("conflict", None)
        note(state, "workstream_merged", workstream=workstream["id"], commit=record["merged_commit"], manual=True)
        return True
    return False


# --- scheduling -------------------------------------------------------------

def resumable(record):
    """A child run a human has already acted on (approved, answered, resumed) and that now
    waits only for an ordinary invocation. Runs still at a human gate or a PAUSED_* status
    are never touched: their next action belongs to a person."""
    return record["status"] in ("WAITING", "PAUSED") and record.get("run_status") == "RUNNING"


def ready(manifest, state, *, authorize_deployment):
    rows, blocked = [], []
    for workstream in manifest["workstreams"]:
        record = state["workstreams"][workstream["id"]]
        if resumable(record):
            rows.append((workstream, record))
            continue
        if record["status"] != "PENDING":
            continue
        if not all(state["workstreams"][dep]["status"] == "MERGED" for dep in workstream["depends_on"]):
            continue
        if workstream["kind"] == "deployment" and not authorize_deployment:
            record["blocked_reason"] = "deployment workstreams run only with --authorize-deployment"
            blocked.append(workstream["id"])
            continue
        record.pop("blocked_reason", None)
        rows.append((workstream, record))
    return rows, blocked


def summarize(manifest, state, state_path):
    rows = []
    for workstream in manifest["workstreams"]:
        record = state["workstreams"][workstream["id"]]
        rows.append({"id": workstream["id"], "kind": workstream["kind"], "depends_on": workstream["depends_on"],
                     **{k: v for k, v in record.items() if k != "command"}})
    statuses = [row["status"] for row in rows]
    pending_deploy_only = all(
        row["status"] == "MERGED" or (row["kind"] == "deployment" and row["status"] == "PENDING") for row in rows)
    if all(value == "MERGED" for value in statuses):
        status = "COMPLETE"
    elif any(value == "CONFLICT" for value in statuses):
        status = "PAUSED_MERGE_CONFLICT"
    elif any(value == "FAILED" for value in statuses):
        status = "BLOCKED"
    elif pending_deploy_only:
        status = "AUTHORIZATION_REQUIRED"
    elif any(value in ("WAITING", "PAUSED") for value in statuses):
        status = "WAITING"
    else:
        status = "RUNNING"
    state["status"] = status
    integration = state.get("integration") or {}
    return {"program": state["name"], "status": status, "state_file": str(state_path),
            "integration_branch": integration.get("branch"), "integration_workspace": integration.get("workspace"),
            "workstreams": rows,
            "next": {
                "COMPLETE": f"Review {integration.get('branch')} and merge it into your default branch yourself.",
                "PAUSED_MERGE_CONFLICT": "Resolve the recorded conflict in the integration worktree, commit, then rerun.",
                "BLOCKED": "Inspect the failed workstream's stdout/stderr logs and run directory, then rerun.",
                "AUTHORIZATION_REQUIRED": "Rerun with --authorize-deployment to start the deployment workstream(s).",
                "WAITING": "Answer questions or approve plans in the listed run directories, then rerun.",
                "RUNNING": "Rerun to continue.",
            }[status]}


def execute(options, source, manifest, project, program_dir, state_path):
    state = json.loads(state_path.read_text()) if state_path.is_file() else new_state(
        source, manifest, project, program_key(manifest))
    if state["manifest_sha256"] != support.file_hash(source) or state["project_workspace"] != str(project):
        raise ValueError(f"Program manifest or project differs from its saved checkpoint {state_path}; "
                         "a saved program's manifest is frozen. Use a new program name to start over")
    ensure_integration(project, state)
    save = lambda: _save(state_path, state)
    save()
    by_id = {row["id"]: row for row in manifest["workstreams"]}
    launched: set[str] = set()  # each workstream is invoked at most once per pass
    try:
        while True:
            for wid, record in state["workstreams"].items():
                if record["status"] in ("RUNNING", "WAITING", "PAUSED", "COMPLETE"):
                    refresh(record)
                if record["status"] == "CONFLICT":
                    adopt_manual_merge(state, by_id[wid], record)
            save()
            for wid, record in state["workstreams"].items():
                if record["status"] == "COMPLETE":
                    integrate(state, by_id[wid], record)
                    save()
            rows, _blocked = ready(manifest, state, authorize_deployment=options["authorize_deployment"])
            rows = [(workstream, record) for workstream, record in rows if workstream["id"] not in launched]
            save()
            if not rows:
                break
            batch = rows[:options["max_parallel"]]
            launched.update(workstream["id"] for workstream, _ in batch)
            with ThreadPoolExecutor(max_workers=options["max_parallel"]) as pool:
                futures = {pool.submit(launch, project, program_dir, manifest, workstream, record, state, options): workstream
                           for workstream, record in batch}
                for future in as_completed(futures):
                    workstream = futures[future]
                    try:
                        future.result()
                    except Exception as error:  # noqa: BLE001 - keep the program state honest
                        with STATE_LOCK:
                            state["workstreams"][workstream["id"]].update(status="FAILED", error=str(error),
                                                                           finished_at=support.now())
                    save()
    except support.Paused as pause:
        note(state, "paused", status=pause.status, reason=str(pause))
        result = summarize(manifest, state, state_path)
        result["status"] = state["status"] = pause.status
        result["next"] = str(pause)
        save()
        print(json.dumps(result, indent=2))
        return 2
    result = summarize(manifest, state, state_path)
    save()
    print(json.dumps(result, indent=2))
    return 0 if state["status"] == "COMPLETE" else 2


def _save(state_path, state):
    with STATE_LOCK:
        support.atomic_json(state_path, state)


# --- CLI ------------------------------------------------------------------------

def _project_root(workspace):
    project = Path(workspace).resolve()
    if Path(workspaces.git(project, "rev-parse", "--show-toplevel")).resolve() != project:
        raise ValueError("Select the root of a Git checkout")
    workspaces.git(project, "rev-parse", "--verify", "HEAD")
    return project


def cli_plan(argv):
    parser = argparse.ArgumentParser(prog="autocode program plan",
                                     description="Plan a large request as a program with the ordinary planning units")
    parser.add_argument("brief")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--engine", choices=["codex", "opencode"])
    args, passthrough = parser.parse_known_args(argv)
    try:
        project = _project_root(args.workspace)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    runner = Path(__file__).with_name("autocode.py")
    command = [sys.executable, str(runner), PLAN_PREAMBLE + args.brief, "--workspace", str(project),
               "--in-place", "--no-chat", "--unit", "autoplanner"]
    if args.engine:
        command += ["--engine", args.engine]
    command += passthrough
    result = subprocess.run(command, cwd=project)
    print("\nWhen the displayed plan is approved (autocode --run-dir RUN --approve-goal TOKEN), derive the program:\n"
          "  autocode program derive --run-dir RUN --output program.json\n"
          "then review program.json and run it:\n"
          "  autocode program run program.json --workspace " + str(project), file=sys.stderr)
    return result.returncode


def cli_derive(argv):
    parser = argparse.ArgumentParser(prog="autocode program derive",
                                     description="Write a program manifest from an approved plan's milestones")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="manifest path (default: print to stdout)")
    parser.add_argument("--name", help="program name (default: the approved outcome)")
    args = parser.parse_args(argv)
    try:
        state = json.loads((args.run_dir / "state.json").read_text())
        manifest = derive_manifest(state, name=args.name, source_run=args.run_dir.resolve())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.error(str(error))
    text = json.dumps(manifest, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
        print(f"wrote {args.output} with {len(manifest['workstreams'])} workstreams; review it before running")
    else:
        print(text, end="")
    return 0


def cli_run(argv, *, status_only=False):
    parser = argparse.ArgumentParser(prog="autocode program " + ("status" if status_only else "run"),
                                     description="Run workstreams in parallel worktrees and merge them onto the integration branch")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--max-parallel", type=int, default=2)
    parser.add_argument("--authorize-deployment", action="store_true",
                        help="allow deployment workstreams to start (their runs still need plan approval)")
    parser.add_argument("--engine", choices=["codex", "opencode"], help="engine for child code runs")
    parser.add_argument("--dry-run", action="store_true", help="validate and preview without creating worktrees")
    args, passthrough = parser.parse_known_args(argv)
    if args.max_parallel < 1:
        parser.error("--max-parallel must be positive")
    try:
        source, manifest = load_manifest(args.manifest)
        project = _project_root(args.workspace)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    key = program_key(manifest)
    program_dir = project / ".autocode/programs" / key
    state_path = program_dir / "state.json"
    if args.dry_run or status_only:
        state = json.loads(state_path.read_text()) if state_path.is_file() else new_state(source, manifest, project, key)
        if status_only:
            for record in state["workstreams"].values():
                if record["status"] in ("RUNNING", "WAITING", "PAUSED"):
                    refresh(record)
        preview = summarize(manifest, state, state_path)
        if not state_path.is_file():
            preview["status"] = "NOT_STARTED"
            preview["next"] = "Run without --dry-run to create the integration branch and start the first wave."
        print(json.dumps(preview, indent=2))
        return 0
    program_dir.mkdir(parents=True, exist_ok=True)
    options = {"max_parallel": args.max_parallel, "authorize_deployment": args.authorize_deployment,
               "engine": args.engine, "passthrough": passthrough}
    try:
        with support.workspace_lock(program_dir):
            return execute(options, source, manifest, project, program_dir, state_path)
    except (support.Paused, ValueError) as error:
        parser.error(str(error))


def cli(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"plan": cli_plan, "derive": cli_derive, "run": cli_run,
                "status": lambda rest: cli_run(rest, status_only=True)}
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in commands:
        print("usage: autocode program {plan|derive|run|status} ...\n"
              "  plan BRIEF --workspace DIR      plan a large request with the ordinary planning units\n"
              "  derive --run-dir RUN [--output] write a program manifest from the approved plan\n"
              "  run MANIFEST --workspace DIR    run workstreams in parallel worktrees; merge onto the integration branch\n"
              "  status MANIFEST --workspace DIR read the saved program state without launching anything",
              file=sys.stderr if argv and argv[0] not in ("-h", "--help") else sys.stdout)
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    return commands[argv[0]](argv[1:])


if __name__ == "__main__":
    raise SystemExit(cli())

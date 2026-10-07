"""Program-level decomposition: parallel workstreams merged onto an integration branch.

A large requirement does not fit one bounded run. A **program manifest**
(version 1) declares workstreams with explicit ``depends_on`` and literal
``owns`` paths. The manifest is the program's **agreement**
(autocode_program_agreement): a person approves it by exact token before any
workstream starts, and approves every later revision of it. Each workstream is
an ordinary AutoCode run (planning, exact approval, Builders, independent
validation, completion) in its own Git worktree, branched from the program's
integration branch so downstream work always starts from merged upstream
results. Completed workstreams are committed on their branch and merged
``--no-ff``; a conflict pauses for a human. The integration workstream runs on
the integration branch itself. Deployment workstreams never launch without
``--authorize-deployment``.

What the agreement adds to that schedule:

- Every launched workstream records the fingerprint of the part of the
  agreement it was built from. A revision that changes it makes the
  workstream stale: its child run's approval no longer counts, and a fresh
  child run plans, asks for approval and verifies again. Unaffected
  workstreams keep their approval.
- A child plan that drops an inherited requirement is rejected: a draft gets
  feedback before anyone approves it; an approved one is replaced by a fresh run.
  A workstream merges only under its current run's approved plan, once the
  program has read and checked it.
- Interfaces change only through a change request and a new version, which
  the person approves as an agreement revision.
- The walking-skeleton workstream is merged and verified before any other
  workstream starts, and every merge re-runs the cumulative checks of all
  merged workstreams on the integrated result; a failing merge is undone.
- The final check is the integration workstream, which verifies every user
  journey by name.

What this module does **not** do: it never approves a child plan, never
merges the integration branch into the project's default branch, never
resolves conflicts, and never treats a child's exit code as completion. The
child's status view (``autocode --status``, read through autocode_taskrun by
autocode_program_children) is the only source of a workstream's status, never
the run's private state.
"""
from __future__ import annotations

import argparse
import copy
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
    from . import autocode_util as util, autocode_workspaces as workspaces
    from . import autocode_planning_graph as graph, autocode_program_agreement as agreement
    from . import autocode_program_children as children, autocode_taskrun as taskrun
    from . import autocode_verify as verify_runner
except ImportError:
    import autocode_util as util
    import autocode_workspaces as workspaces
    import autocode_planning_graph as graph
    import autocode_program_agreement as agreement
    import autocode_program_children as children
    import autocode_taskrun as taskrun
    import autocode_verify as verify_runner


KINDS = ("code", "integration", "deployment")
ID_RE = agreement.ID_RE
STATE_LOCK = threading.Lock()  # worker threads update records; the main thread serializes state
# Workstreams in one batch launch in parallel threads, but `git worktree add` on one repository
# is not safe to run concurrently (ref and worktree-metadata locks): a collision fails one
# workstream and blocks the program. Only the git setup is serialized; the runs stay parallel.
WORKTREE_LOCK = threading.Lock()
GIT_IDENTITY = ("-c", "user.name=Autocode", "-c", "user.email=autocode@localhost")
SHARED_LISTS = agreement.SHARED_LISTS
STATE_VERSION = 2
# A child plan that drops an inherited requirement is sent back this many times in all;
# after that the program pauses for a person instead of looping.
MAX_PLAN_REJECTIONS = 2
CHECK_TIMEOUT = 900
PASSED = {"verified", "passed", "pass", "PASS", "VERIFIED"}

PLAN_PREAMBLE = (
    "PROGRAM PLANNING. This request is too large for one bounded task. Plan it as a program: "
    "propose milestones that are independently deliverable workstreams, each with a disjoint set "
    "of affected_paths it exclusively owns, explicit depends_on, and acceptance criteria that can be "
    "verified on that workstream's own merged result. The first milestone is the walking skeleton: "
    "the thinnest version that works from start to finish across every layer, walking the main user "
    "journey, with the shared interface contracts (schemas, API shapes, module boundaries) it needs; "
    "every other milestone depends on it and extends it. Prefer early thin end-to-end slices over "
    "finishing subsystems one at a time. Deployment, credentials "
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
    if (path.is_absolute() or ".." in parts or not parts or parts[0] in (".git", ".autocode")
            or any(c in value for c in "*?[]\\")):
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
    """Check a manifest dict, including its agreement rules; return it. Raises ValueError with the first defect."""
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Program manifest needs version 1")
    for key in ("name", "brief"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"Program manifest needs a nonempty {key}")
    contract = value.get("contract", {})
    if not isinstance(contract, dict) or not isinstance(contract.get("body", {}), dict):
        raise ValueError("contract must be an object, and its body an object")
    shared = value.get("shared", {})
    if not isinstance(shared, dict):
        raise ValueError("shared must be an object")
    for key in SHARED_LISTS:
        if key in shared:
            _string_list(shared[key], f"shared.{key}")
    if not isinstance(shared.get("interfaces", []), list):
        raise ValueError("shared.interfaces must be a list")
    for row in shared.get("interfaces", []):
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip()
                or not isinstance(row.get("summary"), str)):
            raise ValueError("shared.interfaces entries need id and summary")
        where = f"interface {row['id']}"
        if row.get("producer") is not None and not isinstance(row["producer"], str):
            raise ValueError(f"{where}: producer must be a workstream id")
        _string_list(row.get("consumers", []), f"{where}.consumers")
        row["paths"] = [_owned_path(path, where) for path in _string_list(row.get("paths", []), f"{where}.paths")]
    rows = value.get("workstreams")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Program manifest needs at least one workstream")
    allowed = {"id", "kind", "brief", "owns", "depends_on", "acceptance_criteria", "engine",
               "skeleton", "skeleton_exempt", "checks"}
    ids = []
    for row in rows:
        if isinstance(row, dict) and row.get("kind") == "ui":
            raise ValueError("Program UI workstreams are not supported until UI checkpoint recovery is available; "
                             "use autocode ui separately")
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
        row["owns"] = [_owned_path(p, f"workstream {row['id']}") for p in _string_list(row.get("owns", []), f"workstream {row['id']}.owns")]
        if not row["owns"] and row["kind"] != "integration":
            raise ValueError(f"Workstream {row['id']}: non-integration workstreams must declare the paths they own")
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
    if len(integrations) != 1:
        raise ValueError("A program needs exactly one integration workstream: the final check of the whole "
                         "product on the merged result")
    deployments = {row["id"] for row in rows if row["kind"] == "deployment"}
    for row in rows:
        if row["kind"] != "deployment" and any(dep in deployments for dep in row["depends_on"]):
            raise ValueError(f"Workstream {row['id']} cannot depend on a deployment workstream")
    integration = integrations[0]
    for row in rows:
        if row["id"] == integration or row["kind"] == "deployment":
            continue
        if not _reachable(edges, integration, row["id"]):
            raise ValueError(f"Integration workstream {integration} must (transitively) depend on {row['id']}")
    for dep in deployments:
        if not _reachable(edges, dep, integration):
            raise ValueError(f"Deployment workstream {dep} must (transitively) depend on the integration workstream")
    return agreement.validate(value)


def load_manifest(path):
    source = Path(path).resolve()
    value = json.loads(source.read_text())
    return source, validate_manifest(value)


def program_key(manifest):
    """Programs are keyed by name: a saved program keeps its workstream graph; its agreement changes only by approved revision."""
    slug = re.sub("[^a-z0-9]+", "-", manifest["name"].lower()).strip("-")[:40] or "program"
    return slug + "-" + hashlib.sha256(manifest["name"].encode()).hexdigest()[:6]


# --- derive a manifest from an approved plan ---------------------------------

def derive_manifest(approved, *, name=None, source_run=None):
    """Turn an approved AutoCode contract into a program manifest, one workstream per milestone.

    ``approved`` is a run's ``approved_contract`` status-view field (revision,
    hash, body). The approved plan is the program plan: nothing is re-planned
    here. Every milestone's declared ``affected_paths`` become its ownership,
    its ``depends_on`` become workstream dependencies, and one integration
    workstream depending on every sink milestone validates the whole flow. The
    first milestone without dependencies is the walking skeleton; any other
    milestone without dependencies is made to depend on it, which the derived
    notes say. The approved end-to-end flow becomes the main user journey.
    """
    if not isinstance(approved, dict) or not isinstance(approved.get("body"), dict):
        raise ValueError("Derive a program only from an approved plan (approve the displayed goal first)")
    body = approved["body"]
    graph.derive(body)
    criteria = {row["id"]: row for row in body.get("acceptance_criteria", [])}
    milestones = body["milestones"]
    base = next(milestone["id"] for milestone in milestones if not milestone.get("depends_on"))
    notes = []
    workstreams = []
    for milestone in milestones:
        lines = [milestone["objective"]]
        for cid in milestone.get("acceptance_criteria", []):
            if cid not in criteria:
                raise ValueError(f"Milestone {milestone['id']} lists {cid}, which the approved plan does not define")
            row = criteria[cid]
            lines.append(f"{cid}: {row.get('criterion', '')} (verify: {row.get('verification_method', '')})")
        depends_on = list(milestone.get("depends_on", []))
        if milestone["id"] != base and not depends_on:
            depends_on = [base]
            notes.append(f"{milestone['id']} had no dependencies; it now depends on the walking skeleton {base}")
        workstreams.append({
            "id": milestone["id"], "kind": "code", "brief": "\n".join(lines),
            "owns": list(milestone.get("affected_paths") or []),
            "depends_on": depends_on,
            "acceptance_criteria": list(milestone.get("acceptance_criteria", [])),
            **({"skeleton": True} if milestone["id"] == base else {}),
        })
    depended = {dep for row in workstreams for dep in row["depends_on"]}
    sinks = [row["id"] for row in workstreams if row["id"] not in depended]
    flow = body.get("end_to_end_flow", [])
    integration_id = "integration"
    while integration_id in {row["id"] for row in workstreams}:
        integration_id += "-final"
    journey_id = "J1"
    while journey_id in criteria:
        journey_id = "journey-" + journey_id
    workstreams.append({
        "id": integration_id, "kind": "integration", "owns": [], "depends_on": sinks,
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
        "contract": {"task_id": approved.get("task_id"), "revision": approved["revision"], "hash": approved["hash"],
                     "body": copy.deepcopy(body)},
        "shared": {key: list(body.get(key, [])) for key in SHARED_LISTS if body.get(key)},
        "journeys": [{"id": journey_id, "name": "Main user journey",
                      "steps": list(flow) or [body.get("intended_outcome") or "The approved outcome"]}],
        "workstreams": workstreams,
    }
    manifest["shared"]["interfaces"] = []
    if notes:
        manifest["derivation_notes"] = notes
    if source_run:
        manifest["source_run"] = str(source_run)
    try:
        return validate_manifest(manifest)
    except ValueError as error:
        raise ValueError(f"The approved plan cannot run as a program without changes: {error}") from error


# --- state ------------------------------------------------------------------

def new_state(source, manifest, project, key):
    return {"version": STATE_VERSION, "name": manifest["name"], "key": key, "manifest": str(source),
            "project_workspace": str(project), "created_at": util.now(), "status": "WAITING_AGREEMENT_APPROVAL",
            "agreement": {"revision": 0, "digest": None, "approved": None, "approved_at": None, "history": [],
                          "pending": None},
            "integration": None, "skeleton": None, "interfaces": {}, "change_requests": [], "verifications": [],
            "workstreams": {row["id"]: {"status": "PENDING", "kind": row["kind"]} for row in manifest["workstreams"]},
            "events": []}


def load_state(state_path, source, manifest, project):
    if not state_path.is_file():
        return new_state(source, manifest, project, program_key(manifest))
    state = json.loads(state_path.read_text())
    if state.get("version") != STATE_VERSION or "agreement" not in state:
        raise ValueError(f"{state_path} was saved before program agreements; use a new program name to start over")
    if state["project_workspace"] != str(project):
        raise ValueError(f"Program project differs from its saved checkpoint {state_path}")
    return state


def note(state, event, **detail):
    state.setdefault("events", []).append({"at": util.now(), "event": event, **detail})


def approved_manifest(state):
    """The agreement in force: the last revision a person approved."""
    return state["agreement"]["approved"]


def sync_agreement(state, manifest):
    """Record the manifest as the next revision when it differs from the approved agreement.

    The workstream graph is frozen and interfaces change only with a new version;
    anything else becomes a pending revision that a person approves by exact token.
    """
    record = state["agreement"]
    value = agreement.digest(manifest)
    approved = record["approved"]
    if approved is not None and record["digest"] == value:
        record["pending"] = None
        return
    if approved is not None:
        problems = agreement.revision_problems(approved, manifest)
        # A version may never go down, even for an interface removed since its producer delivered it.
        kept = {row["id"] for row in agreement.interfaces(approved)}
        for row in agreement.interfaces(manifest):
            delivered = state["interfaces"].get(row["id"])
            if row["id"] not in kept and delivered and row["version"] <= delivered["version"]:
                problems.append(f"interface {row['id']} was delivered as version {delivered['version']} and removed "
                                f"since; it may come back only as version {delivered['version'] + 1} or later")
        if problems:
            raise ValueError("The manifest is not a revision of the approved program agreement: " + "; ".join(problems)
                             + ". Restore it, or use a new program name to start over")
    pending = record.get("pending")
    if pending and pending["digest"] == value:
        return
    revision = record["revision"] + 1
    record["pending"] = {"revision": revision, "digest": value, "token": agreement.token(revision, value),
                         "recorded_at": util.now(),
                         "affected": agreement.affected(approved, manifest) if approved else
                         [row["id"] for row in manifest["workstreams"]],
                         "changes": agreement.changes(approved, manifest) if approved else []}


def approve_agreement(state, manifest, selected):
    """Approve the pending revision by its exact token; open change requests on re-versioned interfaces are
    accepted, and those on interfaces the revision removes are withdrawn."""
    sync_agreement(state, manifest)
    record = state["agreement"]
    pending = record["pending"]
    if not pending:
        raise ValueError(f"Agreement revision {record['revision']} is already approved; nothing is waiting for approval")
    if selected != pending["token"]:
        raise ValueError("Approve only the exact token that `autocode program show` displays for the current "
                         f"manifest: {pending['token']}")
    previous = record["approved"]
    record.update(revision=pending["revision"], digest=pending["digest"], approved=copy.deepcopy(manifest),
                  approved_at=util.now(), pending=None)
    record["history"].append({"revision": pending["revision"], "digest": pending["digest"],
                              "approved_at": record["approved_at"], "affected": pending["affected"],
                              "changes": pending["changes"]})
    for iid, (old, new) in (agreement.bumped_interfaces(previous, manifest) if previous else {}).items():
        for request in state["change_requests"]:
            if request["interface"] == iid and request["status"] == "open":
                request.update(status="accepted", accepted_in_revision=pending["revision"], version=new,
                               resolved_at=util.now())
    present = {row["id"] for row in agreement.interfaces(manifest)}
    for request in open_requests(state):
        if request["interface"] not in present:
            request.update(status="withdrawn", withdrawn_in_revision=pending["revision"], resolved_at=util.now())
    note(state, "agreement_approved", revision=pending["revision"], affected=pending["affected"])


def _worktree(project, name, branch, base):
    parent = workspaces.keep_out_of_git(project) / "worktrees"
    parent.mkdir(parents=True, exist_ok=True)
    workspace = parent / name
    with WORKTREE_LOCK:
        workspaces.git(project, "worktree", "add", "-b", branch, str(workspace), base)
    data = {"version": 1, "project_workspace": str(project), "workspace": str(workspace),
            "branch": branch, "base_commit": base}
    artifact = workspaces.keep_out_of_git(workspace) / "task-workspace.json"
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

def _journey_lines(manifest):
    """Each user journey with its steps, and what a simulated one does not prove."""
    lines = []
    for row in agreement.journeys(manifest):
        lines.append(f"- {row['id']} {row['name']}: " + " -> ".join(row["steps"]))
        if row.get("simulated"):
            lines.append(f"  This journey is simulated. Say in its evidence that it does not prove: "
                         f"{row['does_not_prove']}")
    return lines


def _criterion_line(cid, criterion):
    """One inherited criterion as the user wrote it: its literals unescaped, so the child can quote them."""
    extra = [f"verify: {criterion['verification_method']}"] if criterion.get("verification_method") else []
    extra += [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in criterion.items()
              if key not in ("id", "criterion", "verification_method")]
    return f"- {cid}: {criterion.get('criterion', '')}" + (f" ({'; '.join(extra)})" if extra else "")


def compose_brief(manifest, workstream, state, *, from_head=False):
    """The workstream's brief. ``from_head``: its new run is proven against the head it starts from (the final
    check, or a fresh worktree), so code already on the branch is in its base and a re-check may guard it."""
    shared = manifest.get("shared", {})
    others = {row["id"]: row for row in manifest["workstreams"] if row["id"] != workstream["id"]}
    merged = [wid for wid, record in state["workstreams"].items() if record.get("status") == "MERGED" and wid in others]
    record = state["workstreams"].get(workstream["id"], {})
    lines = [f"PROGRAM WORKSTREAM {workstream['id']} ({workstream['kind']}) of program \"{manifest['name']}\".", ""]
    if record.get("stale_reason"):
        lines += [f"RE-CHECK: {record['stale_reason']}. An earlier plan for this workstream no longer applies: plan "
                  "against the agreement below, keep what still conforms, change what does not, and verify again. "
                  "Where the files already conform, plan a validation-only task (kind validate) that proves it, not "
                  "an implementation with nothing to change." + (
                      " Mark a criterion the code on this branch already satisfies guard:, naming the test that "
                      "already proves it; keep test: for what this re-check adds or changes, since a test: "
                      "criterion needs a change that makes its test pass." if from_head else ""), ""]
    lines += ["Program outcome: " + manifest["brief"].strip()]
    if state.get("agreement", {}).get("revision"):
        lines.append(f"Program agreement revision {state['agreement']['revision']}, approved by the user.")
    lines.append("")
    # The whole product's flow and deliverables are context: a live skeleton's Validator read the program flow as
    # its own, found search and export unbuilt, and left its end-to-end result unverified (2026-10-06).
    for key, label in (("constraints", "Shared constraints"), ("permission_boundaries", "Permission boundaries"),
                       ("technical_approach", "Shared technical approach"),
                       ("end_to_end_flow", "The whole product's end-to-end flow (context: later workstreams build the "
                                           "rest; this workstream's own flow is the part its objective delivers)"),
                       ("deliverables", "The whole product's deliverables (context: this workstream delivers only "
                                        "what its objective and ownership name)")):
        if shared.get(key):
            lines += [label + ":"] + [f"- {item}" for item in shared[key]] + [""]
    body = manifest.get("contract", {}).get("body")
    if body:
        # Fenced, so the parent's examples stay context: a child keeps the literals of its own criteria
        # (below), never every other workstream's, and never JSON-escaped ones (autocode_brief_literals).
        lines += ["Approved parent contract (complete context, not authorization to expand this workstream):",
                  "```json", json.dumps(body, indent=2, ensure_ascii=False), "```",
                  "Preserve its requirements, exclusions, permission boundaries and human_review obligations "
                  "in your child plan. Approving the parent approves neither this child plan nor any human_review "
                  "obligation in it.", ""]
    if shared.get("interfaces"):
        lines.append("Shared interfaces (read-only unless this workstream produces them):")
        for row in shared["interfaces"]:
            who = (f"; produced by {row['producer']}" if row.get("producer") else "") + (
                f", used by {', '.join(row['consumers'])}" if row.get("consumers") else "")
            lines.append(f"- {row['id']}: {row['summary']} (version {row['version']}{who}) "
                         f"[{', '.join(row.get('paths', [])) or 'no paths'}]")
            for key in ("schema", "behavior"):
                if row.get(key) is not None:
                    text = row[key] if isinstance(row[key], str) else json.dumps(row[key], sort_keys=True,
                                                                                 ensure_ascii=False)
                    lines.append(f"  {key}: {text}")
        lines += ["An interface changes only through the program. If one is wrong or insufficient, do not change it "
                  "or work around it: stop and describe the change you need in a question to the user, who raises "
                  "it with `autocode program request-change`.", ""]
    if merged:
        lines.append("Workstreams already merged on the integration branch (use their results; do not redo them):")
        lines += [f"- {wid}: {others[wid]['brief'].splitlines()[0]}" for wid in merged]
        lines.append("")
    if workstream.get("skeleton"):
        lines += ["This workstream is the walking skeleton: the thinnest version that works from start to finish "
                  "across every layer, walking the user journey(s) below. It is merged and verified on the "
                  "integration branch before any other workstream starts, and every other workstream extends it. "
                  "Its own end-to-end flow is the part of each journey its objective covers: later workstreams add "
                  "the other steps, and the final check verifies each whole journey. Leave runnable checks (tests) "
                  "that prove its part end to end: they are re-run after every later merge."]
        lines += _journey_lines(manifest) + [""]
    elif agreement.needs_skeleton(manifest, workstream["id"]):
        base = agreement.skeleton(manifest)
        lines += [f"The walking skeleton ({base}) is merged and verified on this branch: extend it; do not rebuild "
                  "or bypass it. Its checks and every merged workstream's checks are re-run after you merge.", ""]
    if workstream["kind"] == "code":
        # A live skeleton checked that `export` was an unknown command; the export workstream then made it valid,
        # and the re-run check undid a correct merge (2026-10-06).
        lines += ["Your checks are re-run after every later workstream merges, once your work is committed, so "
                  "check only what the finished product keeps: never git status or uncommitted files, and never "
                  "that a command, option or file a later workstream adds is missing, unknown or refused.", ""]
    lines += ["This workstream's objective:", workstream["brief"].strip(), ""]
    criteria = agreement.requirements(manifest)
    inherited = agreement.inherited(manifest, workstream["id"])
    # The final check inherits requirements its own row need not list; it is told to keep each by id, so it is told
    # what each one says too.
    listed = list(dict.fromkeys(workstream.get("acceptance_criteria", [])
                                + [cid for cid in inherited if cid in criteria]))
    if listed:
        lines.append("Acceptance criteria for this workstream:")
        for item in listed:
            criterion = criteria.get(item)
            lines.append(_criterion_line(item, criterion) if criterion else f"- {item}")
        lines.append("")
    if workstream["kind"] == "integration":
        lines.append("User journeys (the final product check; verify each one end to end on the merged product):")
        lines += _journey_lines(manifest) + [""]
    if inherited:
        lines += ["Inherited requirements: keep each as an acceptance criterion of your plan with exactly this id "
                  f"({', '.join(inherited)}). A plan that drops one is rejected by the program.", ""]
    if workstream["kind"] == "integration":
        # A live final check copied the parent's test: marks, which its regression proof cannot pass on a
        # base that already delivers them (2026-10-06).
        lines += ["Proof marks: every code workstream is merged on this branch, so the code this run starts from "
                  "already delivers the criteria above, and their test: marks record how each workstream proved "
                  "new behavior on its own base. In your plan, mark each criterion the merged product already "
                  "satisfies guard:, naming the test a merged workstream already has, so the runner checks that it "
                  "passes before and after your change; a journey test you add without changing the product is a "
                  "guard: too. Keep test: for an integration defect you repair, whose test fails on this branch "
                  "before your fix.", ""]
    if workstream["kind"] == "integration":
        lines += ["Ownership exception: you may repair integration defects across merged workstreams, "
                  "including tracked files outside your declared paths; do not expand the approved scope "
                  "and do not change shared interfaces."]
    elif workstream["owns"]:
        lines += ["Ownership: create or modify files only under: " + ", ".join(workstream["owns"]) + "."]
    foreign = sorted({p for row in others.values() for p in row["owns"]
                      if not any(p == own or p.startswith(own + "/") or own.startswith(p + "/")
                                 for own in workstream["owns"])})
    if foreign and workstream["kind"] != "integration":
        lines += ["Paths owned by other workstreams (do not modify): " + ", ".join(foreign) + "."]
    if workstream["kind"] == "deployment":
        lines += ["Deployment scheduling was explicitly authorized, but external actions still require "
                  "permission in your own approved plan and must obey the parent permission boundaries."]
    else:
        lines += ["Do not deploy or access external systems."]
    lines += ["The program controller integrates completed workstreams; do not merge branches."]
    return "\n".join(lines)


# --- child runs -------------------------------------------------------------

def refresh(record):
    """Bring a workstream record up to date from its child's status view (autocode_program_children); return the view."""
    return children.refresh(record)


def abandon(record, reason, *, new_worktree=False):
    """Retire a workstream's child run: its plan's approval no longer counts. A fresh run will start.

    With ``new_worktree`` the fresh run starts from the current integration head in a new worktree;
    otherwise it plans in the same worktree. The retired run and its worktree stay on disk. Its
    ``approved_plan`` goes with it into ``retired_runs``: the fresh run merges only under its own.
    """
    # A resolution merged by hand is on the integration branch, as a merged workstream is: start from there.
    new_worktree = new_worktree or bool(record.get("manual_merge"))
    entry = {"at": util.now(), "reason": reason, **{key: record[key] for key in
             ("run_dir", "run_status", "merged_commit", "workspace", "branch", "pin", "approved_plan")
             if record.get(key)}}
    record.setdefault("retired_runs", []).append(entry)
    children.detach(record)
    for key in ("finished_at", "conflict", "plan_check", "approved_plan", "integration_check", "error",
                "merged_commit", "merged_at", "merge_note", "checks", "journeys", "verification", "pin",
                "blocked_reason", "manual_merge"):
        record.pop(key, None)
    if new_worktree:
        for key in ("workspace", "branch", "base_commit"):
            record.pop(key, None)
    record.update(status="STALE", stale_reason=reason)


def _upstream(manifest, wid):
    """Every workstream ``wid`` depends on, directly or transitively."""
    edges = {row["id"]: row["depends_on"] for row in manifest["workstreams"]}
    found, todo = set(), list(edges.get(wid, []))
    while todo:
        dep = todo.pop()
        if dep not in found:
            found.add(dep)
            todo += edges.get(dep, [])
    return found


def mark_stale(manifest, state):
    """Workstreams built from a part of the agreement that a later approved revision changed."""
    revision = state["agreement"]["revision"]
    records = state["workstreams"]
    stale = [wid for wid, record in records.items() if record.get("pin") and record["status"] not in ("PENDING", "STALE")
             and record["pin"]["scope"] != agreement.scope_digest(manifest, wid)]
    rechecked = set(stale) | {wid for wid, record in records.items() if record["status"] == "STALE"}
    for wid in stale:
        record = records[wid]
        reason = (f"agreement revision {revision} changed what workstream {wid} is built from "
                  f"(it was built under revision {record['pin']['revision']})")
        # A merged workstream, or one built on a workstream that is re-checked too, restarts from the
        # integration head that will hold the re-checked result; otherwise it keeps its worktree.
        abandon(record, reason, new_worktree=record["status"] == "MERGED" or bool(_upstream(manifest, wid) & rechecked))
        if wid == agreement.skeleton(manifest):
            state["skeleton"] = None
        note(state, "workstream_stale", workstream=wid, revision=revision)


def inspect(manifest, state, wid, record, view):
    """Hold a child plan to the agreement: pin it, reject one that drops an inherited requirement, keep its checks."""
    if view is None or record["status"] in ("MERGED", "STALE"):
        return
    approved = view.get("approved_contract")
    if approved:
        criteria, plan = approved["body"].get("acceptance_criteria") or [], approved
    elif view.get("status") == "AWAITING_GOAL_APPROVAL" and view.get("displayed_plan"):
        criteria, plan = view["displayed_plan"].get("acceptance_criteria") or [], view["displayed_plan"]
    else:
        criteria, plan = None, None
    if plan is not None:
        missing = agreement.dropped(manifest, wid, criteria)
        if approved and missing:
            record.pop("approved_plan", None)  # the plan the run now holds is not one the program accepted
        check = record.get("plan_check") or {}
        if not missing:
            record["plan_check"] = {"token": plan["token"], "dropped": [], "approved": bool(approved)}
        elif check.get("token") != plan["token"]:
            rejections = record.get("plan_rejections", 0) + 1
            record["plan_rejections"] = rejections
            record["plan_check"] = {"token": plan["token"], "dropped": missing, "approved": bool(approved),
                                    "exhausted": rejections > MAX_PLAN_REJECTIONS}
            note(state, "plan_rejected", workstream=wid, dropped=missing, approved=bool(approved))
            text = ("The program agreement rejects this plan: it drops inherited requirement(s) "
                    + ", ".join(missing) + ". Keep each inherited requirement as an acceptance criterion with exactly "
                    "its id (" + ", ".join(agreement.inherited(manifest, wid)) + "); a workstream may not drop a "
                    "requirement it inherited.")
            if rejections <= MAX_PLAN_REJECTIONS:
                if approved:
                    abandon(record, "its approved plan dropped inherited requirement(s) " + ", ".join(missing))
                    return
                try:
                    children.apply_view(record, children.feedback(record, text))
                except taskrun.TaskRunError as error:
                    # The child refused the program's feedback: a person decides, and the rejection stays counted.
                    record["plan_check"].update(exhausted=True, feedback_error=str(error)[-500:])
                    note(state, "plan_feedback_refused", workstream=wid, error=str(error)[-500:])
                    return
                record["status"] = "WAITING"  # the child re-plans when the program next advances it
                return
        else:
            record["plan_check"].pop("replanning", None)  # the child shows the rejected plan again
        if approved and not missing:
            record["approved_plan"] = {"token": approved["token"], **(record.get("pin") or {})}
    elif (record.get("plan_check") or {}).get("dropped") and view.get("status") == "RUNNING":
        # The child no longer shows the rejected plan: a person acted on it (feedback), and the child
        # re-plans when advanced. That plan is checked again; nothing merges before one keeps every
        # inherited id.
        record["plan_check"]["replanning"] = True
    if view.get("status") in children.TERMINAL_CODE:
        if not approved:
            record.pop("approved_plan", None)  # a run that completes showing no plan in force merges under none
        replay = (view.get("evidence") or {}).get("check_replay") or {}
        commands = [row["command"] for row in replay.get("checks") or []
                    if isinstance(row, dict) and isinstance(row.get("command"), str) and row.get("exit_code") == 0]
        record["checks"] = list(dict.fromkeys(commands))
        if record["kind"] == "integration":
            rows = {row.get("id"): row for row in (view.get("evidence") or {}).get("acceptance") or []}
            record["journeys"] = [{"id": row["id"], **{key: rows.get(row["id"], {}).get(key) for key in
                                                       ("status", "validator_status", "evidence")}}
                                  for row in agreement.journeys(manifest)]


def plan_blocked(record, *, merging=False):
    """Why a workstream's current child plan may not be resumed (or, with ``merging``, merged), or None.

    A child whose rejected plan a person has acted on may be resumed to re-plan; it never
    merges while the last plan seen drops an inherited requirement.
    """
    check = record.get("plan_check") or {}
    resumable = not (check.get("approved") or check.get("exhausted")) or check.get("replanning")
    if check.get("dropped") and (merging or not resumable):
        return f"its plan drops inherited requirement(s) {', '.join(check['dropped'])}"
    return None


def launch(project, program_dir, manifest, workstream, record, state, options):
    """Run one workstream as an ordinary AutoCode run; never approve anything on its behalf.

    Returns the child's status view after the invocation, or None when it cannot be read.
    """
    from_head = workstream["kind"] == "integration"  # its new run's base is rewritten below
    if workstream["kind"] == "integration":
        workspace = Path(state["integration"]["workspace"])
        with STATE_LOCK:
            record.update(workspace=str(workspace), branch=state["integration"]["branch"])
            record.setdefault("base_commit", integration_head(state))
            if not record.get("run_dir"):
                # A new run takes its regression-proof base from the worktree's task-workspace.json
                # (autocode_run_setup): the integration head it starts from, not the commit this shared
                # worktree was made from. A live final check proved the merged product against the
                # seed (2026-10-06). The program's own checks keep record["base_commit"].
                util.atomic_json(workspaces.keep_out_of_git(workspace) / "task-workspace.json",
                                 {**state["integration"], "base_commit": integration_head(state)})
    elif not record.get("workspace"):
        base = integration_head(state)
        suffix = uuid.uuid4().hex[:8]
        data = _worktree(project, f"program-{state['key']}-{workstream['id']}-{suffix}",
                         f"autocode/program-{state['key']}/{workstream['id']}-{suffix}", base)
        with STATE_LOCK:
            record.update(workspace=data["workspace"], branch=data["branch"], base_commit=base)
        workspace, from_head = Path(data["workspace"]), True
    else:
        workspace = Path(record["workspace"])
    artifact = program_dir / workstream["id"]
    artifact.mkdir(parents=True, exist_ok=True)
    if not record.get("run_dir"):
        # brief.md is the brief that started the workstream's current run; resuming it leaves that alone.
        brief = compose_brief(manifest, workstream, state, from_head=from_head)
        (artifact / "brief.md").write_text(brief + "\n")
    with STATE_LOCK:
        if not record.get("run_dir"):
            # The part of the agreement this run is built from; a revision that changes it retires the run.
            record["pin"] = {"revision": state["agreement"]["revision"],
                             "scope": agreement.scope_digest(manifest, workstream["id"])}
        record.update(status="RUNNING", started_at=util.now())
        record.pop("error", None)
        record.pop("command", None)  # rewritten from the invocation itself
        children.prepare_start(record, workspace)
        # Save the worktree and the runs already in it before launch, so an interrupted
        # controller reattaches to the child run it started instead of starting a duplicate.
        util.atomic_json(program_dir / "state.json", state)
    if record.get("run_dir"):
        return children.advance(record, log_dir=artifact, lock=STATE_LOCK)
    # Engine and pass-through flags configure the new run once; resuming it uses its saved settings.
    # A workstream is an ordinary build job by construction, never left to the recognizer to route.
    engine = workstream.get("engine") or options.get("engine")
    start_options = ["--workflow", "build", *(["--engine", engine] if engine else []), *options.get("passthrough", [])]
    return children.start(record, workspace, brief, start_options, log_dir=artifact, lock=STATE_LOCK)


# --- integration ------------------------------------------------------------

def check_ownership(workstream, record):
    """Check the delivered diff, including prior commits, deletions and new files; return its paths."""
    workspace = Path(record["workspace"])
    if workspaces.git(workspace, "rev-parse", "--abbrev-ref", "HEAD") != record["branch"]:
        raise util.Paused("PAUSED_OWNERSHIP", f"Workstream {workstream['id']} is not on its recorded branch; "
                             "restore the worktree to its recorded branch before integrating")
    paths = set()
    for args in (("diff", "--name-only", "--no-renames", "-z", record["base_commit"], "HEAD", "--"),
                 ("diff", "--name-only", "--no-renames", "-z", record["base_commit"], "--"),
                 ("ls-files", "--others", "--exclude-standard", "-z")):
        result = subprocess.run(["git", "-C", str(workspace), *args], capture_output=True, text=True, check=True)
        found = {p for p in result.stdout.split("\0") if p}
        if args[0] == "ls-files":
            found = {p for p in found if not p.startswith(".autocode/") and not _bytecode(p)}
        paths.update(found)
    outside = sorted(p for p in paths if not any(p == own or p.startswith(own + "/") for own in workstream["owns"]))
    if outside:
        raise util.Paused("PAUSED_OWNERSHIP", f"Workstream {workstream['id']} changed paths outside owns: "
                             f"{', '.join(outside)}. Remove those changes from both its branch and worktree, "
                             "then rerun; nothing was merged")
    return sorted(paths)


def check_interfaces(manifest, state, workstream, paths):
    """Refuse a delivery that changes an interface in place instead of through a change request."""
    published = {iid: row["version"] for iid, row in state["interfaces"].items()}
    found = agreement.quiet_interface_changes(manifest, workstream["id"], paths, published)
    if found:
        raise util.Paused("PAUSED_INTERFACE_CHANGE", "Workstream " + workstream["id"] + " changes a shared interface "
                          "without an approved change: " + "; ".join(f"{iid}: {why}" for iid, why in found)
                          + ". Nothing was merged. Remove that change, or raise it with `autocode program "
                          "request-change` and approve the new interface version as an agreement revision")


def _bytecode(path):
    """Python's bytecode cache, which running a workstream's tests leaves behind; never part of a delivery."""
    return "/__pycache__/" in f"/{path}" or path.endswith(".pyc")


def _commit_all(workspace, message):
    """Commit every change except runner metadata and bytecode caches; return the new commit or None when clean."""
    if workspaces.git(workspace, "diff", "--cached", "--name-only", "--", ".autocode"):
        raise util.Paused("PAUSED_METADATA", "Runner metadata is staged; unstage .autocode before integrating")
    workspaces.git(workspace, "add", "-A", "--", ".", ":!.autocode", ":(exclude)*__pycache__*", ":(exclude)*.pyc")
    if not workspaces.git(workspace, "diff", "--cached", "--name-only"):
        return None
    workspaces.git(workspace, *GIT_IDENTITY, "commit", "-q", "-m", message)
    return workspaces.git(workspace, "rev-parse", "--verify", "HEAD")


def _uncommitted(workspace):
    """The changes _commit_all would commit: anything but runner metadata and bytecode caches."""
    return workspaces.git(workspace, "status", "--porcelain", "--untracked-files=all", "--", ".", ":!.autocode",
                          ":(exclude)*__pycache__*", ":(exclude)*.pyc")


def _owned_checks(manifest, state, wid):
    """(owner, command) for every cumulative check: owner None for a program check, else the workstream.

    A workstream counts once it is merged, or while a person's resolution of its conflict is on the integration
    branch and its run's later work has yet to merge on top (``manual_merge``, written by adopt_manual_merge). That
    resolution is held to the checks it passed: the run's own may change before its later work merges.
    """
    integration = state["integration"]["workspace"]
    found = [(None, command) for command in manifest.get("checks", [])]
    for row in manifest["workstreams"]:
        record = state["workstreams"][row["id"]]
        if record["status"] == "MERGED" or record.get("manual_merge") or row["id"] == wid:
            child = record.get("workspace")
            own = record.get("checks", [])
            if record.get("manual_merge") and row["id"] != wid:
                own = record["manual_merge"]["checks"]
            for command in list(row.get("checks", [])) + list(own):
                # A child's check names its own worktree; on the integration branch it runs there.
                if child and child != integration:
                    command = command.replace(str(Path(child).resolve()), integration).replace(child, integration)
                found.append((row["id"], command))
    return found


def cumulative_checks(manifest, state, wid):
    """Program checks plus the checks of every merged workstream and of ``wid``, which is landing now."""
    return list(dict.fromkeys(command for _owner, command in _owned_checks(manifest, state, wid)))


def check_owners(manifest, state, wid, command):
    """Who left a cumulative check: workstream ids, in manifest order, and None for the program's own."""
    return list(dict.fromkeys(owner for owner, found in _owned_checks(manifest, state, wid) if found == command))


def verify_integration(manifest, state, program_dir, wid, timeout):
    """Re-run the cumulative checks in a clean copy of the integration branch; return the receipt."""
    commands = cumulative_checks(manifest, state, wid)
    number = state["verification_count"] = state.get("verification_count", len(state["verifications"])) + 1
    out = program_dir / "verify" / f"{number:03d}-{wid}"
    rows = []
    for number, command in enumerate(commands, 1):
        receipt = verify_runner.scratch_run(Path(state["integration"]["workspace"]), out / f"check-{number:02d}",
                                            command=command, timeout=timeout)
        rows.append({"command": command, "exit_code": receipt.get("exit_code"),
                     "timed_out": bool(receipt.get("timed_out")), "error": receipt.get("error") or "",
                     "tail": (receipt.get("tail") or "")[-600:]})
    failed = [row for row in rows if row["error"] or row["timed_out"] or row["exit_code"] != 0]
    result = {"workstream": wid, "head": integration_head(state), "at": util.now(), "checks": rows,
              "verdict": "FAIL" if failed else "PASS" if rows else "NO_CHECKS", "receipts": str(out)}
    state["verifications"] = (state["verifications"] + [result])[-50:]
    return result


def _failed_row(result):
    return next(row for row in result["checks"] if row["error"] or row["timed_out"] or row["exit_code"] != 0)


def _failed(result):
    row = _failed_row(result)
    what = row["error"] or ("timed out" if row["timed_out"] else f"exited {row['exit_code']}")
    return f"`{row['command']}` {what}" + (f"; its output ended: {row['tail'].strip()[-300:]}" if row["tail"].strip() else "")


def _check_advice(manifest, state, wid, record, command):
    """What to do about a failed cumulative check, naming who left it: the landing workstream may have broken
    it, or an earlier workstream's check may hold only for the product before this merge."""
    fix = (f"fix {wid} (for example autocode --workspace {record.get('workspace')} --run-dir {record.get('run_dir')} "
           "--follow-up \"The integrated product fails: ...\")")
    earlier = [owner for owner in check_owners(manifest, state, wid, command) if owner != wid]
    if not earlier:
        return f"The check is {wid}'s own: {fix}, then rerun. "
    who = ", ".join("the program's checks" if owner is None else f"workstream {owner}" for owner in earlier)
    retire = ("revise the agreement's top-level checks" if earlier == [None] else
              "revise the agreement so " + " and ".join(owner for owner in earlier if owner) + " is re-checked "
              "(its run is retired and its checks are replaced), and approve that revision")
    return (f"The check was left by {who}. Either {wid} broke what it checks: {fix}. Or the check holds only for "
            f"the product before {wid} (for example it checks that something {wid} adds is missing or refused): "
            f"{retire}. Then rerun. ")


def land(manifest, state, workstream, record, program_dir, options, *, before, branch_head, manual=False, final=True):
    """Verify the integrated result after a merge; undo the program's merge and pause when it fails.

    ``before`` is the integration head before the program merged the workstream (None when it made
    no merge); a merge a person made (``manual``) is never undone here. Unless ``final``, a pass
    records nothing: the workstream's later work still merges on top, and that merge is the one
    that publishes its interfaces, verifies the skeleton and records the verification.
    """
    wid = workstream["id"]
    result = verify_integration(manifest, state, program_dir, wid, options.get("check_timeout", CHECK_TIMEOUT))
    is_skeleton = workstream.get("skeleton")
    if result["verdict"] == "FAIL" or (is_skeleton and result["verdict"] == "NO_CHECKS"):
        integration = state["integration"]["workspace"]
        undone = workstream["kind"] != "integration" and before is not None and before != integration_head(state)
        if undone:
            workspaces.git(integration, "reset", "-q", "--hard", before)
        record.pop("merge_from", None)
        branch = state["integration"]["branch"]
        kept = f"Your merge is still on the integration branch {branch}: undo or repair it there. " if manual else ""
        status = "PAUSED_SKELETON_UNVERIFIED" if result["verdict"] == "NO_CHECKS" else "PAUSED_INTEGRATION_CHECK"
        record.update(status="COMPLETE" if record["status"] != "CONFLICT" else "CONFLICT",
                      integration_check={"verdict": result["verdict"], "branch_head": branch_head,
                                         "integration_head": integration_head(state), "status": status,
                                         "checks": util.digest(cumulative_checks(manifest, state, wid)),
                                         "receipts": result["receipts"]})
        note(state, "integration_check_failed", workstream=wid, verdict=result["verdict"])
        if status == "PAUSED_SKELETON_UNVERIFIED":
            message = (f"The walking skeleton {wid} has no runnable check to verify it on the integration branch, "
                       + ("so its merge was undone" if undone else "so it is not accepted as merged")
                       + " and nothing else starts. " + kept + "Add program checks (top-level checks) that "
                       "walk the journey, approve that agreement revision, then rerun")
        else:
            undid = ("The merge of its later work was undone; your merge by hand stays. " if record.get("manual_merge")
                     else "The merge was undone. ")
            message = (f"After merging workstream {wid}, the integrated product fails its cumulative checks: "
                       + _failed(result) + ". " + (undid if undone else kept)
                       + _check_advice(manifest, state, wid, record, _failed_row(result)["command"])
                       + f"Receipts: {result['receipts']}")
        record["integration_check"]["message"] = message
        raise util.Paused(status, message)
    record.pop("integration_check", None)
    record.pop("merge_from", None)
    if not final:
        return
    record.pop("manual_merge", None)
    record.update(verification={key: result[key] for key in ("verdict", "head", "at", "receipts")},
                  verified_checks=len(result["checks"]), merged_under=record.get("pin"))
    record.pop("stale_reason", None)
    if is_skeleton:
        state["skeleton"] = {"workstream": wid, "commit": result["head"], "checks": len(result["checks"]),
                             "verified_at": result["at"]}
    for row in agreement.interfaces(manifest):
        if row.get("producer") == wid:
            state["interfaces"][row["id"]] = {"version": row["version"], "commit": result["head"], "by": wid}


def _repeat_failure(manifest, state, wid, record, branch_head):
    """The same delivery, checks and integration head already failed: do not merge it again."""
    check = record.get("integration_check")
    if (check and check.get("branch_head") == branch_head and check.get("integration_head") == integration_head(state)
            and check.get("checks") == util.digest(cumulative_checks(manifest, state, wid))):
        raise util.Paused(check.get("status", "PAUSED_INTEGRATION_CHECK"), check.get("message", "Integration check failed"))


def require_checked_plan(workstream, record):
    """Fail closed: a workstream of any kind merges only under its current run's approved plan, which inspect()
    recorded from the run's status view once it kept every inherited id, and drops when the completed run shows no
    plan in force or one that drops an id (abandon() drops it with the run). A run whose view never showed one, such
    as a run completed by hand, is not merged on its status alone; nor is a conflict a person resolved by hand."""
    if not record.get("approved_plan"):
        raise util.Paused("PAUSED_INHERITANCE", (
            f"Workstream {workstream['id']} completed (run {record.get('run_dir')}) without showing an approved plan, "
            "so the program could not check that it keeps every inherited requirement, and it was not merged. "
            f"Follow up its run (autocode --workspace {record.get('workspace')} --run-dir {record.get('run_dir')} "
            "--follow-up \"...\") so it plans again, approve that plan when it asks, then rerun the program"))


def integrate(manifest, state, workstream, record, program_dir, options):
    """Merge one completed workstream onto the integration branch, verify it, or pause."""
    require_checked_plan(workstream, record)
    integration = Path(state["integration"]["workspace"])
    if workspaces.git(integration, "rev-parse", "--abbrev-ref", "HEAD") != state["integration"]["branch"]:
        raise util.Paused("PAUSED_INTEGRATION_DIRTY", "Restore the integration worktree to its recorded branch before merging")
    title = workstream["brief"].strip().splitlines()[0][:72]
    if workstream["kind"] == "integration":
        unverified = unverified_journeys(manifest, record)
        if unverified:
            # Nothing is committed or merged: a follow-up reopens the run, and its next completion is checked again.
            raise util.Paused("PAUSED_JOURNEY_UNVERIFIED", (
                f"The final check {workstream['id']} completed without verifying user journey(s) "
                + ", ".join(unverified) + f", so it was not merged. Follow up its run (autocode --workspace "
                f"{record.get('workspace')} --run-dir {record.get('run_dir')} --follow-up \"Verify journey ...\"), "
                "then rerun the program"))
        base = record.get("base_commit", state["integration"]["base_commit"])
        if workspaces.git(integration, "diff", "--name-only", base, "HEAD", "--", ".autocode"):
            raise util.Paused("PAUSED_METADATA", "Integration committed runner metadata; remove that metadata diff before resuming")
        changed = workspaces.git(integration, "diff", "--name-only", "--no-renames", base, "--").splitlines()
        changed += workspaces.git(integration, "ls-files", "--others", "--exclude-standard").splitlines()
        check_interfaces(manifest, state, workstream, [p for p in changed if p and not p.startswith(".autocode/")])
        commit = _commit_all(integration, f"Program {state['name']}: {workstream['id']} - {title}")
        head = integration_head(state)
        _repeat_failure(manifest, state, workstream["id"], record, head)
        land(manifest, state, workstream, record, program_dir, options, before=None, branch_head=head)
        record.update(status="MERGED", merged_commit=commit or head, merged_at=util.now(),
                      merge_note="committed on the integration branch" if commit else "no source changes")
        note(state, "workstream_merged", workstream=workstream["id"], commit=record["merged_commit"])
        return
    if workspaces.git(integration, "status", "--porcelain", "--untracked-files=no"):
        raise util.Paused("PAUSED_INTEGRATION_DIRTY",
                             f"The integration worktree {integration} has uncommitted tracked changes; commit or restore them")
    paths = check_ownership(workstream, record)
    check_interfaces(manifest, state, workstream, paths)
    workspace = Path(record["workspace"])
    commit = _commit_all(workspace, f"Program {state['name']}: {workstream['id']} - {title}")
    check_ownership(workstream, record)
    branch_head = workspaces.git(workspace, "rev-parse", "--verify", "HEAD")
    _repeat_failure(manifest, state, workstream["id"], record, branch_head)
    before = integration_head(state)
    if commit is None and branch_head == record.get("base_commit"):
        land(manifest, state, workstream, record, program_dir, options, before=None, branch_head=branch_head)
        record.update(status="MERGED", merged_commit=integration_head(state), merged_at=util.now(),
                      merge_note="no source changes")
        note(state, "workstream_merged", workstream=workstream["id"], commit=record["merged_commit"], changes=False)
        return
    # Saved before merging: a controller interrupted before the verdict leaves a merge nobody verified,
    # which the next pass takes back (undo_unverified_merges).
    record["merge_from"] = {"integration_head": before, "branch_head": branch_head}
    _save(program_dir / "state.json", state)
    result = subprocess.run(["git", "-C", str(integration), *GIT_IDENTITY, "merge", "--no-ff", "--no-edit",
                             "-m", f"Merge workstream {workstream['id']} into program {state['name']}", record["branch"]],
                            capture_output=True, text=True)
    if result.returncode:
        subprocess.run(["git", "-C", str(integration), "merge", "--abort"], capture_output=True, text=True)
        record.pop("merge_from", None)
        record.update(status="CONFLICT", conflict=(result.stdout + result.stderr).strip()[-2000:])
        note(state, "merge_conflict", workstream=workstream["id"], branch=record["branch"])
        raise util.Paused(
            "PAUSED_MERGE_CONFLICT",
            f"Merging workstream {workstream['id']} ({record['branch']}) into {state['integration']['branch']} conflicts. "
            f"Resolve it by hand in {integration} (git merge --no-ff {record['branch']}), commit, then rerun the program.")
    land(manifest, state, workstream, record, program_dir, options, before=before, branch_head=branch_head)
    record.update(status="MERGED", merged_commit=integration_head(state), merged_at=util.now())
    note(state, "workstream_merged", workstream=workstream["id"], commit=record["merged_commit"])


def undo_unverified_merges(state):
    """Take back a merge an interrupted pass made but never verified (``merge_from``, saved before merging).

    Only a merge still at the integration head is reset; the workstream merges again, and is verified,
    when it is next integrated. Without such a merge (it never happened, or was undone) the record is cleared.
    """
    integration = state["integration"]["workspace"]
    for wid, record in state["workstreams"].items():
        pending = record.get("merge_from")
        if not pending:
            continue
        if workspaces.git(integration, "rev-parse", "HEAD^@").split() == [pending["integration_head"],
                                                                           pending["branch_head"]]:
            if workspaces.git(integration, "status", "--porcelain", "--untracked-files=no"):
                raise util.Paused("PAUSED_INTEGRATION_DIRTY", (
                    f"An interrupted pass merged workstream {wid} without verifying it, and the integration "
                    f"worktree {integration} has uncommitted changes: commit or discard (git restore) them, then "
                    "rerun to undo that merge and verify it again"))
            workspaces.git(integration, "reset", "-q", "--hard", pending["integration_head"])
            note(state, "unverified_merge_undone", workstream=wid, reset_to=pending["integration_head"])
        record.pop("merge_from")


def adopt_manual_merge(manifest, state, workstream, record, program_dir, options):
    """A human resolved a conflict and committed: accept the branch as merged when its tip is an ancestor
    and the integrated result passes the cumulative checks."""
    integration = Path(state["integration"]["workspace"])
    if (workspaces.git(integration, "rev-parse", "--abbrev-ref", "HEAD") != state["integration"]["branch"]
            or workspaces.git(integration, "status", "--porcelain", "--untracked-files=no")):
        raise util.Paused("PAUSED_INTEGRATION_DIRTY", "Finish the resolution on the recorded integration branch before resuming")
    paths = check_ownership(workstream, record)
    check_interfaces(manifest, state, workstream, paths)
    result = subprocess.run(["git", "-C", str(integration), "merge-base", "--is-ancestor", record["branch"], "HEAD"],
                            capture_output=True, text=True)
    if result.returncode == 0:
        # The run may have changed since its conflict (a person followed it up): its plan is held as integrate() holds it.
        # A resolved conflict is no longer what holds it, so the pause names what does.
        reason = plan_blocked(record, merging=True)
        if reason:
            record["blocked_reason"] = reason
            raise util.Paused("PAUSED_INHERITANCE", (
                f"The conflict of workstream {workstream['id']} was resolved by hand, but {reason}, so it was not "
                f"merged. Give its run (--run-dir {record.get('run_dir')}) feedback so it plans again, approve a plan "
                "that keeps every inherited id, then rerun the program"))
        require_checked_plan(workstream, record)
        branch_head = workspaces.git(record["workspace"], "rev-parse", "--verify", "HEAD")
        _repeat_failure(manifest, state, workstream["id"], record, branch_head)
        # Its run may have delivered more since the conflict (a person followed it up): that work is not on the branch
        # the person merged (#626). The resolution is checked on its own first, publishing nothing yet.
        later = bool(_uncommitted(record["workspace"]))
        land(manifest, state, workstream, record, program_dir, options, before=None, branch_head=branch_head,
             manual=True, final=not later)
        record.pop("conflict", None)
        if later:
            # The later work merges on top as an ordinary merge, with every check a merge has. Until it lands, the
            # resolution on the branch is guarded like a merged workstream: its checks run on every other merge.
            record.update(status="COMPLETE", manual_merge={"commit": integration_head(state), "branch_head": branch_head,
                                                           "checks": list(record.get("checks", []))})
            note(state, "manual_merge_with_later_work", workstream=workstream["id"], commit=integration_head(state))
            reason = held(manifest, state, workstream["id"], merging=True)
            if reason:
                record["blocked_reason"] = reason
            else:
                integrate(manifest, state, workstream, record, program_dir, options)
            return True
        record.update(status="MERGED", merged_commit=integration_head(state), merged_at=util.now(),
                      merge_note="conflict resolved manually")
        note(state, "workstream_merged", workstream=workstream["id"], commit=record["merged_commit"], manual=True)
        return True
    return False


# --- change requests ------------------------------------------------------------

def open_requests(state):
    return [row for row in state.get("change_requests", []) if row["status"] == "open"]


def held_by_request(manifest, state, wid):
    """An open change request holds the start and merge of every workstream the agreement binds to its
    interface: its producer and consumers, every workstream when it has no producer, and always the final
    check (the integration workstream)."""
    kind = state["workstreams"][wid]["kind"]
    for request in open_requests(state):
        row = next((item for item in agreement.interfaces(manifest) if item["id"] == request["interface"]), None)
        if row is not None and agreement._involved(row, wid, kind):
            return f"change request {request['id']} is open on interface {request['interface']}"
    return None


def request_change(manifest, state, *, interface, by, reason, proposal=None):
    rows = {row["id"]: row for row in agreement.interfaces(manifest)}
    if interface not in rows:
        raise ValueError(f"Unknown interface {interface!r}; the agreement has {', '.join(sorted(rows)) or 'none'}")
    if by not in state["workstreams"]:
        raise ValueError(f"Unknown workstream {by!r}")
    if not reason.strip():
        raise ValueError("A change request needs the reason the interface is wrong")
    request = {"id": f"CR-{len(state['change_requests']) + 1}", "interface": interface,
               "from_version": rows[interface]["version"], "by": by, "reason": reason.strip(),
               "proposal": (proposal or "").strip(), "status": "open", "raised_at": util.now()}
    state["change_requests"].append(request)
    note(state, "change_requested", request=request["id"], interface=interface, by=by)
    return request


def reject_request(state, request_id, reason):
    request = next((row for row in state["change_requests"] if row["id"] == request_id), None)
    if request is None or request["status"] != "open":
        raise ValueError(f"No open change request {request_id}")
    request.update(status="rejected", resolution=reason.strip(), resolved_at=util.now())
    note(state, "change_rejected", request=request_id)
    return request


# --- scheduling -------------------------------------------------------------

def resumable(record):
    """A child run a human has already acted on (approved, answered, resumed) and that now
    waits only for an ordinary invocation. Runs still at a human gate or a PAUSED_* status
    are never touched: their next action belongs to a person."""
    return record["status"] in ("RUNNING", "WAITING", "PAUSED") and record.get("run_status") == "RUNNING"


def held(manifest, state, wid, *, merging=False):
    """Why a workstream may not start or resume (or, with ``merging``, merge) now, or None."""
    record = state["workstreams"][wid]
    kind = record["kind"]
    if agreement.needs_skeleton(manifest, wid) and not state.get("skeleton"):
        return "waiting for the walking skeleton to be merged and verified"
    if kind == "integration":
        behind = [row["id"] for row in manifest["workstreams"] if row["kind"] == "code"
                  and state["workstreams"][row["id"]]["status"] != "MERGED"]
        if behind:
            return "the final check waits until every workstream is merged: " + ", ".join(behind)
    return plan_blocked(record, merging=merging) or held_by_request(manifest, state, wid)


def ready(manifest, state, *, authorize_deployment):
    rows, blocked = [], []
    for workstream in manifest["workstreams"]:
        record = state["workstreams"][workstream["id"]]
        if record["status"] not in ("PENDING", "STALE") and not resumable(record):
            continue
        if not all(state["workstreams"][dep]["status"] == "MERGED" for dep in workstream["depends_on"]):
            continue
        reason = (held(manifest, state, workstream["id"])
                  or ("deployment workstreams run only with --authorize-deployment"
                      if workstream["kind"] == "deployment" and not authorize_deployment else None))
        if reason:
            record["blocked_reason"] = reason
            blocked.append(workstream["id"])
            continue
        record.pop("blocked_reason", None)
        rows.append((workstream, record))
    return rows, blocked


def unverified_journeys(manifest, record):
    """The journey ids the integration workstream's completed run did not verify, in agreement order."""
    found = {row["id"]: row for row in record.get("journeys") or []}
    return [row["id"] for row in agreement.journeys(manifest)
            if not (found.get(row["id"], {}).get("status") in PASSED
                    or found.get(row["id"], {}).get("validator_status") == "PASS")]


def journey_report(manifest, state):
    """The final product check, by journey name: verified only by the merged integration workstream's run."""
    integration = next(row["id"] for row in manifest["workstreams"] if row["kind"] == "integration")
    record = state["workstreams"][integration]
    found = {row["id"]: row for row in record.get("journeys") or []}
    report = []
    for row in agreement.journeys(manifest):
        result = found.get(row["id"], {})
        verified = record["status"] == "MERGED" and row["id"] not in unverified_journeys(manifest, record)
        report.append({"id": row["id"], "name": row["name"], "steps": row["steps"],
                       "status": "verified" if verified else "failed" if record["status"] == "MERGED" else "pending",
                       "verified_by": integration, "run_dir": record.get("run_dir"),
                       "evidence": result.get("evidence"), "simulated": bool(row.get("simulated")),
                       **({"does_not_prove": row["does_not_prove"]} if row.get("simulated") else {})})
    return report


NEXT = {
    "COMPLETE": "Review {branch} and merge it into your default branch yourself.",
    "WAITING_AGREEMENT_APPROVAL": ("Read the agreement with `autocode program show MANIFEST --workspace PROJECT`, then approve "
                                   "it with `autocode program approve MANIFEST --workspace PROJECT --token {token}`."),
    "WAITING_CHANGE_REQUEST": ("Decide the open change request(s): publish a new interface version in the manifest and "
                               "approve that revision, or reject with `autocode program resolve-change`. Answer or approve "
                               "any listed child runs too, then rerun."),
    "PAUSED_MERGE_CONFLICT": "Resolve the recorded conflict in the integration worktree, commit, then rerun.",
    "PAUSED_INHERITANCE": ("A workstream's plan still drops an inherited requirement after the automatic rejections: "
                           "give its run feedback yourself, or revise the agreement, then rerun."),
    "PAUSED_JOURNEY_UNVERIFIED": ("The final check completed without verifying every user journey and was not merged; "
                                  "follow up its run so each journey is verified, then rerun."),
    "BLOCKED": "Inspect the failed workstream's logs, then rerun with --retry-workstream ID. Child gates remain enforced.",
    "AUTHORIZATION_REQUIRED": "Rerun with --authorize-deployment to start the deployment workstream(s).",
    "WAITING": "Answer questions or approve plans in the listed run directories, then rerun.",
    "RUNNING": "Rerun to continue.",
}


def summarize(manifest, state, state_path):
    rows = []
    for workstream in manifest["workstreams"]:
        record = state["workstreams"][workstream["id"]]
        rows.append({"id": workstream["id"], "kind": workstream["kind"], "depends_on": workstream["depends_on"],
                     **({"skeleton": True} if workstream.get("skeleton") else {}),
                     **{k: v for k, v in record.items() if k not in children.INTERNAL_FIELDS}})
    statuses = [row["status"] for row in rows]
    journeys = journey_report(manifest, state)
    pending_deploy_only = all(
        row["status"] == "MERGED" or (row["kind"] == "deployment" and
            (row["status"] in ("PENDING", "STALE") or row.get("blocked_reason"))) for row in rows)
    exhausted = any(check.get("exhausted") and check.get("dropped") and not check.get("replanning")
                    for check in (row.get("plan_check") or {} for row in rows))
    pending = state["agreement"]["pending"]
    if pending:
        status = "WAITING_AGREEMENT_APPROVAL"
    elif all(value == "MERGED" for value in statuses) and not open_requests(state):
        status = "COMPLETE" if all(row["status"] == "verified" for row in journeys) else "PAUSED_JOURNEY_UNVERIFIED"
    elif any(value == "CONFLICT" for value in statuses):
        status = "PAUSED_MERGE_CONFLICT"
    elif any(value == "FAILED" for value in statuses):
        status = "BLOCKED"
    elif exhausted:
        status = "PAUSED_INHERITANCE"
    elif open_requests(state):
        status = "WAITING_CHANGE_REQUEST"
    elif pending_deploy_only:
        status = "AUTHORIZATION_REQUIRED"
    elif any(value in ("WAITING", "PAUSED") for value in statuses):
        status = "WAITING"
    else:
        status = "RUNNING"
    if state.get("pause"):
        status = state["pause"]["status"]
    state["status"] = status
    integration = state.get("integration") or {}
    record = state["agreement"]
    result = {"program": state["name"], "status": status, "state_file": str(state_path),
              "integration_branch": integration.get("branch"), "integration_workspace": integration.get("workspace"),
              "agreement": {"revision": record["revision"], "approved": record["approved"] is not None,
                            "token": agreement.token(record["revision"], record["digest"]) if record["digest"] else None,
                            "pending": pending},
              "skeleton": state.get("skeleton"), "journeys": journeys,
              "change_requests": state.get("change_requests", []),
              "workstreams": rows,
              "next": state["pause"]["reason"] if state.get("pause") else NEXT[status].format(
                  branch=integration.get("branch"), token=(pending or {}).get("token"))}
    if status == "COMPLETE":
        result["final_check"] = {
            "workstream": next(row["id"] for row in rows if row["kind"] == "integration"),
            "journeys": [f"{row['id']} {row['name']}" for row in journeys],
            "not_proven": [f"{row['id']} {row['name']}: {row['does_not_prove']}" for row in journeys if row["simulated"]]}
    return result


def execute(options, source, manifest, project, program_dir, state_path):
    state = load_state(state_path, source, manifest, project)
    sync_agreement(state, manifest)
    by_id = {row["id"]: row for row in manifest["workstreams"]}
    fresh = {}  # workstream id -> its status view, read after every other child had run
    for wid in options.get("retry_workstreams", []):
        if wid not in by_id or state["workstreams"][wid]["status"] != "FAILED":
            raise ValueError(f"--retry-workstream requires a failed workstream: {wid}")
        record = state["workstreams"][wid]
        fresh[wid] = refresh(record)
        if record["status"] == "FAILED":
            if record.get("run_dir") and record.get("run_status") is None:
                raise ValueError(f"Saved child checkpoint for {wid} cannot be read ({record.get('error')}); "
                                 "restore it before retrying")
            if record.get("error", "").startswith(children.MULTIPLE):
                raise ValueError(record["error"])
            # Without a run, the retry starts one in the same worktree.
            record["status"] = "WAITING" if record.get("run_dir") else "PENDING"
        note(state, "workstream_retry_requested", workstream=wid)
    state.pop("pause", None)
    save = lambda: _save(state_path, state)
    if state["agreement"]["pending"]:
        # Nothing starts, resumes or merges under an agreement the person has not approved.
        result = summarize(manifest, state, state_path)
        save()
        print(json.dumps(result, indent=2))
        return 2
    manifest = approved_manifest(state)
    ensure_integration(project, state)
    save()
    launched: set[str] = set()  # each workstream is invoked at most once per pass
    try:
        undo_unverified_merges(state)
        while True:
            views = {}
            for wid, record in state["workstreams"].items():
                # A fresh record was read after the previous batch's other children had finished.
                if wid in fresh:
                    views[wid] = fresh[wid]
                elif record["status"] in ("RUNNING", "WAITING", "PAUSED", "COMPLETE", "CONFLICT", "FAILED"):
                    views[wid] = refresh(record)
                if record["status"] == "RUNNING" and not record.get("run_dir"):
                    record.update(status="FAILED", error="Controller interrupted before a child checkpoint was saved; retry explicitly")
            mark_stale(manifest, state)
            for wid, record in state["workstreams"].items():
                inspect(manifest, state, wid, record, views.get(wid))
                if record["status"] == "CONFLICT":
                    adopt_manual_merge(manifest, state, by_id[wid], record, program_dir, options)
            fresh.clear()
            save()
            for wid, record in state["workstreams"].items():
                if record["status"] != "COMPLETE":
                    continue
                reason = held(manifest, state, wid, merging=True)
                if reason:
                    record["blocked_reason"] = reason
                    continue
                record.pop("blocked_reason", None)
                integrate(manifest, state, by_id[wid], record, program_dir, options)
                save()
            rows, _blocked = ready(manifest, state, authorize_deployment=options["authorize_deployment"])
            rows = [(workstream, record) for workstream, record in rows if workstream["id"] not in launched]
            save()
            if not rows:
                break
            batch = rows[:options["max_parallel"]]
            for workstream, record in batch:
                if (workstream["kind"] == "integration" and record["status"] in ("PENDING", "STALE")
                        and workspaces.git(state["integration"]["workspace"], "status", "--porcelain", "--untracked-files=no")):
                    raise util.Paused("PAUSED_INTEGRATION_DIRTY", (
                        "The final check starts on the integration worktree, which has uncommitted changes: commit "
                        f"or discard (git restore) the uncommitted changes in {state['integration']['workspace']}; "
                        "they may be a retired integration run's. Then rerun"))
            launched.update(workstream["id"] for workstream, _ in batch)
            with ThreadPoolExecutor(max_workers=options["max_parallel"]) as pool:
                futures = {pool.submit(launch, project, program_dir, manifest, workstream, record, state, options): workstream
                           for workstream, record in batch}
                last = None
                for future in as_completed(futures):
                    workstream = futures[future]
                    last = None
                    try:
                        last = (workstream["id"], future.result())
                    except Exception as error:  # noqa: BLE001 - keep the program state honest
                        with STATE_LOCK:
                            failed = state["workstreams"][workstream["id"]]
                            failed.update(status="FAILED", error=str(error), finished_at=util.now())
                            children.forget_view(failed)
                    save()
                # Only the last launch read its child's view after the whole batch ran; an earlier
                # one may be stale (a person can act on its child while a sibling still runs).
                if last:
                    fresh[last[0]] = last[1]
    except util.Paused as pause:
        note(state, "paused", status=pause.status, reason=str(pause))
        state["pause"] = {"status": pause.status, "reason": str(pause)}
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
        util.atomic_json(state_path, state)


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
          "then read the program agreement and approve it:\n"
          "  autocode program show program.json --workspace " + str(project) + "\n"
          "  autocode program approve program.json --workspace " + str(project) + " --token TOKEN\n"
          "and run it:\n"
          "  autocode program run program.json --workspace " + str(project), file=sys.stderr)
    return result.returncode


def cli_derive(argv):
    parser = argparse.ArgumentParser(prog="autocode program derive",
                                     description="Write a program manifest from an approved plan's milestones")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, help="the plan run's workspace (default: the run directory's project)")
    parser.add_argument("--output", type=Path, help="manifest path (default: print to stdout)")
    parser.add_argument("--name", help="program name (default: the approved outcome)")
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    workspace = args.workspace or (run_dir.parents[2] if len(run_dir.parents) > 2 else run_dir)
    try:
        view = taskrun.TaskRun(Path(workspace).resolve(), run_dir).status()
        manifest = derive_manifest(view.get("approved_contract"), name=args.name, source_run=run_dir)
    except (OSError, ValueError, KeyError, taskrun.TaskRunError) as error:
        parser.error(str(error))
    text = json.dumps(manifest, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
        print(f"wrote {args.output} with {len(manifest['workstreams'])} workstreams; read it with "
              f"`autocode program show {args.output} --workspace PROJECT` and approve it before running")
    else:
        print(text, end="")
    return 0


def _common(parser):
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())


def _open(parser, args):
    try:
        source, manifest = load_manifest(args.manifest)
        project = _project_root(args.workspace)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    program_dir = project / ".autocode/programs" / program_key(manifest)
    return source, manifest, project, program_dir, program_dir / "state.json"


def cli_show(argv):
    parser = argparse.ArgumentParser(prog="autocode program show",
                                     description="Print the program agreement and the exact token that approves it")
    _common(parser)
    args = parser.parse_args(argv)
    source, manifest, project, _program_dir, state_path = _open(parser, args)
    try:
        state = load_state(state_path, source, manifest, project)
        sync_agreement(state, manifest)
    except ValueError as error:
        parser.error(str(error))
    record = state["agreement"]
    if record["pending"]:
        print(agreement.render(manifest, revision=record["pending"]["revision"], value=record["pending"]["digest"],
                               previous=record["approved"]))
    else:
        print(agreement.render(manifest, revision=record["revision"], value=record["digest"]))
        print(f"\nRevision {record['revision']} is approved ({record['approved_at']}).")
    return 0


def _mutate(parser, args, change):
    source, manifest, project, program_dir, state_path = _open(parser, args)
    workspaces.keep_out_of_git(project)
    program_dir.mkdir(parents=True, exist_ok=True)
    try:
        with util.workspace_lock(program_dir):
            state = load_state(state_path, source, manifest, project)
            output = change(state, manifest)
            _save(state_path, state)
    except (util.Paused, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(output, indent=2))
    return 0


def cli_approve(argv):
    parser = argparse.ArgumentParser(prog="autocode program approve",
                                     description="Approve the displayed program agreement revision by its exact token")
    _common(parser)
    parser.add_argument("--token", required=True)
    args = parser.parse_args(argv)

    def change(state, manifest):
        approve_agreement(state, manifest, args.token)
        record = state["agreement"]
        return {"approved": agreement.token(record["revision"], record["digest"]),
                "affected": record["history"][-1]["affected"],
                "next": "Run `autocode program run` to continue. Affected workstreams that already started lose "
                        "their approval: each plans again and asks for approval in a fresh run."}
    return _mutate(parser, args, change)


def cli_request_change(argv):
    parser = argparse.ArgumentParser(prog="autocode program request-change",
                                     description="Raise a change request on a shared interface; nobody changes it quietly")
    _common(parser)
    parser.add_argument("--interface", required=True)
    parser.add_argument("--by", required=True, metavar="WORKSTREAM", help="the workstream that found the problem")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--proposal", help="the change you propose")
    args = parser.parse_args(argv)

    def change(state, manifest):
        if state["agreement"]["approved"] is None:
            raise ValueError("Approve the program agreement before raising change requests on it")
        request = request_change(approved_manifest(state), state, interface=args.interface, by=args.by,
                                 reason=args.reason, proposal=args.proposal)
        return {"change_request": request,
                "next": "While it is open, the workstreams bound to this interface (its producer and consumers; every "
                        "workstream when it has no producer) and the final check do not start, resume or merge. "
                        f"To accept it, publish interface {args.interface} as version {request['from_version'] + 1} "
                        "in the manifest and approve that agreement revision (the producer and every consumer then "
                        "plan, are approved and are checked again); to reject it, run `autocode program "
                        f"resolve-change MANIFEST --request {request['id']} --reject --reason TEXT`."}
    return _mutate(parser, args, change)


def cli_resolve_change(argv):
    parser = argparse.ArgumentParser(prog="autocode program resolve-change",
                                     description="Reject an open change request (accepting one is an approved agreement revision)")
    _common(parser)
    parser.add_argument("--request", required=True)
    parser.add_argument("--reject", action="store_true", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    return _mutate(parser, args, lambda state, manifest: {"change_request": reject_request(state, args.request, args.reason)})


def cli_run(argv, *, status_only=False):
    parser = argparse.ArgumentParser(prog="autocode program " + ("status" if status_only else "run"),
                                     description="Run workstreams in parallel worktrees and merge them onto the integration branch")
    _common(parser)
    parser.add_argument("--max-parallel", type=int, default=2)
    parser.add_argument("--authorize-deployment", action="store_true",
                        help="allow deployment workstreams to start (their runs still need plan approval)")
    parser.add_argument("--engine", choices=["codex", "opencode"], help="engine for child code runs")
    parser.add_argument("--retry-workstream", action="append", default=[], metavar="ID",
                        help="retry a failed workstream in its existing worktree without bypassing child gates")
    parser.add_argument("--check-timeout", type=int, default=CHECK_TIMEOUT,
                        help="seconds each cumulative integration check may take")
    parser.add_argument("--dry-run", action="store_true", help="validate and preview without creating worktrees")
    args, passthrough = parser.parse_known_args(argv)
    if args.max_parallel < 1:
        parser.error("--max-parallel must be positive")
    source, manifest, project, program_dir, state_path = _open(parser, args)
    if args.dry_run or status_only:
        try:
            state = load_state(state_path, source, manifest, project)
            sync_agreement(state, manifest)
        except ValueError as error:
            parser.error(str(error))
        if status_only:
            # A read: the children's status views, without the lock and without saving.
            for record in state["workstreams"].values():
                if record["status"] in ("RUNNING", "WAITING", "PAUSED"):
                    children.read(record)
        preview = summarize(approved_manifest(state) or manifest, state, state_path)
        if not state_path.is_file():
            preview["status"] = "NOT_STARTED"
            preview["next"] = ("Read the agreement with `autocode program show`, approve it with `autocode program "
                               "approve --token " + state["agreement"]["pending"]["token"] + "`, then run without "
                               "--dry-run to create the integration branch and start the walking skeleton.")
        print(json.dumps(preview, indent=2))
        return 0
    workspaces.keep_out_of_git(project)
    program_dir.mkdir(parents=True, exist_ok=True)
    options = {"max_parallel": args.max_parallel, "authorize_deployment": args.authorize_deployment,
               "engine": args.engine, "passthrough": passthrough, "retry_workstreams": args.retry_workstream,
               "check_timeout": args.check_timeout}
    try:
        with util.workspace_lock(program_dir):
            return execute(options, source, manifest, project, program_dir, state_path)
    except (util.Paused, ValueError, taskrun.TaskRunError) as error:
        parser.error(str(error))


def cli(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"plan": cli_plan, "derive": cli_derive, "run": cli_run, "show": cli_show, "approve": cli_approve,
                "request-change": cli_request_change, "resolve-change": cli_resolve_change,
                "status": lambda rest: cli_run(rest, status_only=True)}
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in commands:
        print("usage: autocode program {plan|derive|show|approve|run|status|request-change|resolve-change} ...\n"
              "  plan BRIEF --workspace DIR        plan a large request with the ordinary planning units\n"
              "  derive --run-dir RUN [--output]   write a program manifest from the approved plan\n"
              "  show MANIFEST --workspace DIR     print the program agreement and its approval token\n"
              "  approve MANIFEST --workspace DIR --token TOKEN\n"
              "                                    approve that agreement revision (nothing runs before this)\n"
              "  run MANIFEST --workspace DIR      run workstreams in parallel worktrees; merge onto the integration branch\n"
              "  status MANIFEST --workspace DIR   read the saved program state without launching anything\n"
              "  request-change MANIFEST --workspace DIR --interface ID --by WORKSTREAM --reason TEXT\n"
              "                                    raise a change request on a shared interface\n"
              "  resolve-change MANIFEST --workspace DIR --request CR-N --reject --reason TEXT",
              file=sys.stderr if argv and argv[0] not in ("-h", "--help") else sys.stdout)
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    return commands[argv[0]](argv[1:])


if __name__ == "__main__":
    raise SystemExit(cli())

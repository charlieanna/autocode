"""Runner-owned orchestration of independent milestone Builders.

Planning and approval own scope. This stage owns scheduling, isolated workers and
integration. Builder reports never accept milestones: the combined artifact still
passes through the ordinary Sol and completion-owner gates.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

try:
    from . import autocode_support as s, autocode_goals as goals
    from . import autocode_milestones as milestones, autocode_process as processes
    from . import autocode_interventions as interventions
except ImportError:
    import autocode_support as s
    import autocode_goals as goals
    import autocode_milestones as milestones
    import autocode_process as processes
    import autocode_interventions as interventions


DEFAULTS = {"enabled": True, "max_parallel": 2}


def enabled(state):
    return (state.get("settings", {}).get("orchestration", {}).get("enabled") is True
            and milestones.enabled(state) and not state["settings"].get("workflow"))


def build_stage(state):
    return "orchestrator" if enabled(state) else "terra"


def valid_path(path):
    return (isinstance(path, str) and bool(path.strip()) and not path.startswith("/")
            and not any(c in path for c in "*?[]\\\x00\n")
            and all(p not in ("", ".", "..", ".git", ".autocode", ".autocode-ui")
                    for p in path.rstrip("/").split("/")))


def contains(root, path):
    return path == root.rstrip("/") or path.startswith(root.rstrip("/") + "/")


def disjoint(a, b):
    return not any(contains(x, y.rstrip("/")) or contains(y, x.rstrip("/")) for x in a for y in b)


def select(state):
    """Pick a bounded ready wave; absent ownership information stays sequential."""
    goals.execution_guard(state)
    task = state.get("current_task", {})
    if task.get("milestone_ids") or task.get("kind") != "implement" or task.get("decision") == "REWORK":
        return []
    body = state["goal_contract"]["body"]
    rows = {m["id"]: m for m in body.get("milestones", [])}
    primary = rows.get(task.get("milestone_id"))
    accepted = milestones.accepted_ids(state)
    limit = state["settings"]["orchestration"]["max_parallel"]
    if type(limit) is not int or limit < 1:
        raise ValueError("orchestration.max_parallel must be positive")
    def eligible(row):
        previous = state.get("milestone_progress", {}).get(f"{state['goal_contract']['hash']}:{row['id']}", {})
        return (row["id"] not in accepted and "depends_on" in row
                and set(row["depends_on"]) <= accepted and row.get("affected_paths")
                and not previous.get("seconds") and not previous.get("reviews") and not previous.get("replans")
                and all(valid_path(p) for p in row["affected_paths"]))
    if (not primary or not eligible(primary)
            or set(task["acceptance_criteria"]) != set(primary["acceptance_criteria"])
            or any(not any(contains(p, t.rstrip("/")) for p in primary["affected_paths"])
                   for t in task["affected_paths"])):
        return []
    selected = [primary]
    for row in rows.values():
        if len(selected) >= limit:
            break
        if row == primary or not eligible(row):
            continue
        if all(disjoint(row["affected_paths"], old["affected_paths"])
               and not set(row["acceptance_criteria"]) & set(old["acceptance_criteria"])
               for old in selected):
            selected.append(row)
    return selected if len(selected) > 1 else []


def git(workspace, *args, env=None, data=None):
    result = subprocess.run(["git", "-C", str(workspace), *args], input=data,
                            capture_output=True, env=env)
    if result.returncode:
        raise s.Paused("PAUSED_ORCHESTRATOR_GIT", result.stderr.decode(errors="replace").strip())
    return result.stdout


def snapshot_commit(workspace, directory):
    """Save the complete current source without touching the user's HEAD/index."""
    if git(workspace, "ls-files", "-u"):
        raise s.Paused("PAUSED_ORCHESTRATOR_GIT", "Resolve the existing Git conflict before parallel dispatch")
    if any(v.startswith(("submodule:", "uninitialized-submodule")) for v in s.snapshot(workspace)["files"].values()):
        raise s.Paused("PAUSED_ORCHESTRATOR_GIT", "Parallel Builder snapshots do not yet support submodules")
    index = directory / ("index-" + uuid.uuid4().hex)
    env = {**os.environ, "GIT_INDEX_FILE": str(index), "GIT_AUTHOR_NAME": "Autocode",
           "GIT_AUTHOR_EMAIL": "autocode@localhost", "GIT_COMMITTER_NAME": "Autocode",
           "GIT_COMMITTER_EMAIL": "autocode@localhost"}
    try:
        git(workspace, "read-tree", "HEAD", env=env)
        git(workspace, "add", "-A", "--", ".", ":(exclude).autocode", ":(exclude).autocode-ui",
            ":(exclude)tools/__pycache__", env=env)
        tree = git(workspace, "write-tree", env=env).decode().strip()
        return git(workspace, "commit-tree", tree, "-p", "HEAD", env=env,
                   data=b"Autocode orchestration snapshot\n").decode().strip()
    finally:
        index.unlink(missing_ok=True)


def task_for(state, milestone, baseline):
    criteria = {c["id"]: c for c in state["goal_contract"]["body"]["acceptance_criteria"]}
    return {"id": "task-" + uuid.uuid4().hex[:12], "kind": "implement",
            "milestone_id": milestone["id"], "objective": milestone["objective"],
            "affected_paths": milestone["affected_paths"],
            "requirements": [criteria[c]["criterion"] for c in milestone["acceptance_criteria"]],
            "validation_plan": [criteria[c]["verification_method"] for c in milestone["acceptance_criteria"]],
            "acceptance_criteria": milestone["acceptance_criteria"],
            "contract_revision": state["goal_contract"]["revision"],
            "contract_hash": state["goal_contract"]["hash"], "assigned_at": s.now(),
            "source_revision": baseline["revision"], "decision": "CONTINUE"}


def prepare(state, workspace, run_dir, selected):
    batch_id = uuid.uuid4().hex[:12]
    directory = run_dir / "orchestration" / batch_id
    directory.mkdir(parents=True)
    baseline = s.snapshot(workspace)
    base = snapshot_commit(workspace, directory)
    if s.snapshot(workspace) != baseline:
        raise s.Paused("PAUSED_ORCHESTRATOR_DRIFT", "Source changed while capturing the Builder baseline")
    batch = {"id": batch_id, "contract_hash": state["goal_contract"]["hash"],
             "baseline": baseline, "base_commit": base, "directory": str(directory),
             "status": "PREPARING", "workers": [], "started_at": s.now()}
    for i, milestone in enumerate(selected):
        worker_workspace = workspace / ".autocode" / "builders" / batch_id / str(i + 1)
        if not worker_workspace.resolve().is_relative_to(workspace.resolve()):
            raise ValueError("Builder worktree storage must remain within the parent workspace")
        worker_dir = worker_workspace / ".autocode" / "runs" / f"builder-{batch_id}-{i + 1}"
        branch = f"autocode/builder-{batch_id}-{i + 1}"
        task = task_for(state, milestone, baseline)
        row = {"milestone_id": milestone["id"], "task": task, "workspace": str(worker_workspace),
               "run_dir": str(worker_dir), "branch": branch, "status": "PREPARING"}
        batch["workers"].append(row)
    state["orchestration_batch"] = batch
    s.atomic_json(run_dir / "state.json", state)
    finish_preparation(state, workspace, run_dir, batch)
    return batch


def finish_preparation(state, workspace, run_dir, batch):
    """Recover worktree setup before any provider launch; membership is already saved."""
    for row in batch["workers"]:
        if row["status"] == "PENDING":
            continue
        worker_workspace, worker_dir = Path(row["workspace"]), Path(row["run_dir"])
        worker_workspace.parent.mkdir(parents=True, exist_ok=True)
        branch, base, task = row["branch"], batch["base_commit"], row["task"]
        if (worker_workspace / ".git").exists():
            if (git(worker_workspace, "rev-parse", "HEAD").decode().strip() != base
                    or git(worker_workspace, "rev-parse", "--path-format=absolute", "--git-common-dir")
                    != git(workspace, "rev-parse", "--path-format=absolute", "--git-common-dir")):
                raise s.Paused("PAUSED_ORCHESTRATOR_DRIFT", "Prepared Builder worktree changed")
        elif git(workspace, "branch", "--list", branch).strip():
            if git(workspace, "rev-parse", branch).decode().strip() != base:
                raise s.Paused("PAUSED_ORCHESTRATOR_DRIFT", "Prepared Builder branch changed")
            git(workspace, "worktree", "add", str(worker_workspace), branch)
        else:
            git(workspace, "worktree", "add", "-b", branch, str(worker_workspace), base)
        expected_files = {p: value for p, value in batch["baseline"]["files"].items() if value != "deleted"}
        if s.snapshot(worker_workspace)["files"] != expected_files:
            raise s.Paused("PAUSED_ORCHESTRATOR_DRIFT",
                           "Prepared Builder checkout is incomplete or modified; restore its saved baseline before resuming. "
                           "Existing files were left untouched")
        worker_dir.mkdir(parents=True, exist_ok=True)
        child = {k: copy.deepcopy(state[k]) for k in (
            "version", "task", "task_id", "goal_contract", "user_events", "settings", "acceptance_criteria",
            "criteria_revision", "answers", "brief_feedback", "milestone_progress") if k in state}
        child.update(workspace=str(worker_workspace), current_task=task, affected_paths=task["affected_paths"],
                     next_action=task["objective"], iteration=1, status="RUNNING", phase="EXECUTING",
                     next_stage="terra", sessions={}, stages=[], history=[],
                     parent_run=str(run_dir), parent_batch=batch["id"])
        child["settings"]["orchestration"] = {"enabled": False, "max_parallel": 1}
        if (worker_dir / "state.json").exists():
            if s.read(worker_dir / "state.json") != child:
                raise s.Paused("PAUSED_ORCHESTRATOR_DRIFT", "Prepared Builder state changed before launch")
        else:
            s.atomic_json(worker_dir / "state.json", child)
        row["status"] = "PENDING"
        s.atomic_json(run_dir / "state.json", state)
    batch["status"] = "BUILDING"
    s.atomic_json(run_dir / "state.json", state)


def run_workers(state, run_dir, batch):
    """One provider supervisor process per Builder, with durable launch intent."""
    processes.process_table()  # Refuse launch if process ownership cannot be supervised.
    active = []
    for row in batch["workers"]:
        if row.get("processes") and processes.live_processes(row["processes"]):
            raise s.Paused("PAUSED_ORCHESTRATOR_WORKERS", "A saved Builder is still running; no duplicate will be launched")
        s.assert_no_legacy_process(Path(row["run_dir"]), Path(row["workspace"]))
    try:
        with processes.interruption_handler():
            for row in batch["workers"]:
                directory = Path(row["run_dir"])
                result = s.read(directory / "result.json") if (directory / "result.json").exists() else {}
                if row.get("retry_requested"):
                    mode = "retry"
                elif row["status"] == "PENDING":
                    mode = "start"
                elif result.get("status") == "BUILT":
                    continue
                elif not row.get("recovery_attempted"):
                    mode = "recover"
                else:
                    continue
                with interventions.admission(run_dir):
                    if (run_dir / "pause-requested").exists():
                        raise s.Paused("PAUSED_REQUESTED", "Pause requested before Builder launch")
                    row["status"] = "RUNNING"
                    row.pop("retry_requested", None)
                    if mode == "recover":
                        row["recovery_attempted"] = True
                    s.atomic_json(run_dir / "state.json", state)
                    with (directory / "worker.log").open("ab") as log:
                        child = subprocess.Popen([sys.executable, str(Path(__file__).with_name("autocode_builder_worker.py")),
                                                  str(directory), mode], stdout=log, stderr=subprocess.STDOUT,
                                                 start_new_session=True)
                def checkpoint(owned, row=row):
                    row["processes"] = owned
                tree = processes.ProcessTree(child.pid, checkpoint)
                active.append((row, child, tree))
                tree.sample(initial=True)
                s.atomic_json(run_dir / "state.json", state)
            while any(child.poll() is None for _, child, _ in active):
                for row, child, tree in active:
                    tree.sample()
                    if child.poll() is not None:
                        row["exit_code"] = child.returncode
                        result_path = Path(row["run_dir"]) / "result.json"
                        row["status"] = s.read(result_path).get("status", "PAUSED_ORCHESTRATOR_WORKER") if result_path.is_file() else "INTERRUPTED"
                s.atomic_json(run_dir / "state.json", state)
                time.sleep(.2)
            for row, child, tree in active:
                row["exit_code"] = child.returncode
                if tree.sample():
                    tree.stop(child)
    except KeyboardInterrupt as error:
        raise s.Paused("PAUSED_INTERRUPTED", "Orchestrator interrupted; Builder work and logs retained") from error
    finally:
        cleanup_errors = []
        for _, child, tree in active:
            try:
                if child.poll() is None:
                    tree.stop(child)
            except processes.ProcessError as error:
                cleanup_errors.append(str(error))
        s.atomic_json(run_dir / "state.json", state)
        if cleanup_errors:
            raise s.Paused("PAUSED_PROCESS_CLEANUP", "; ".join(cleanup_errors))


def account_workers(state, run_dir, batch):
    """Charge finished attempts once, even when another member blocks integration."""
    accounted = set(batch.get("accounted_attempts", []))
    for row in batch["workers"]:
        child = s.read(Path(row["run_dir"]) / "state.json")
        records = child.get("stages", []) + ([child["active_stage"]] if child.get("active_stage") else [])
        for record in records:
            if not record.get("accounted"):
                continue
            identity = f"{batch['id']}:{row['milestone_id']}:{record['iteration']}:{record['stage']}:{record['started_at']}"
            entry = {**record, "worker_milestone": row["milestone_id"], "batch_id": batch["id"], "worker_attempt": identity}
            if identity in accounted:
                for records in (state.get("stages", []), state.get("history", [])):
                    for old in records:
                        if old.get("worker_attempt") == identity:
                            old.update(entry)  # An explicit abandonment may archive the artifact paths.
                continue
            state.setdefault("stages", []).append(entry)
            state.setdefault("history", []).append(entry)
            state["active_seconds"] = state.get("active_seconds", 0) + (record.get("duration_seconds") or 0)
            accounted.add(identity)
        key = f"{batch['contract_hash']}:{row['milestone_id']}"
        if key in child.get("milestone_progress", {}):
            state.setdefault("milestone_progress", {})[key] = copy.deepcopy(child["milestone_progress"][key])
    batch["accounted_attempts"] = sorted(accounted)
    s.atomic_json(run_dir / "state.json", state)


def collect(state, workspace, run_dir, batch):
    account_workers(state, run_dir, batch)
    patches = []
    changed = set()
    expected = copy.deepcopy(batch["baseline"])
    tracked = set(git(workspace, "ls-files", "-z", "--cached").decode().split("\0"))
    for row in batch["workers"]:
        if row.get("processes") and processes.live_processes(row["processes"]):
            raise s.Paused("PAUSED_ORCHESTRATOR_WORKERS", "A saved Builder is still running; no duplicate will be launched")
        directory = Path(row["run_dir"])
        result_path = directory / "result.json"
        if not result_path.is_file():
            raise s.Paused("PAUSED_ORCHESTRATOR_WORKER", f"Builder {row['milestone_id']} needs inspection: {directory}")
        result = s.read(result_path)
        if result.get("status") != "BUILT":
            raise s.Paused("PAUSED_ORCHESTRATOR_WORKER", f"Builder {row['milestone_id']}: {result.get('reason', 'paused')}; {directory}")
        child = s.read(directory / "state.json")
        goals.execution_guard(child, child["implementation"])
        if (child["goal_contract"]["hash"] != batch["contract_hash"] or child["current_task"] != row["task"]
                or child["implementation"].get("user_request", {}).get("kind") != "none"):
            raise s.Paused("PAUSED_ORCHESTRATOR_WORKER", "Builder changed its assignment or requested a user decision")
        current = s.snapshot(Path(row["workspace"]))
        if current["head"] != batch["base_commit"] or current["revision"] != child["implementation"]["source_revision"]:
            raise s.Paused("PAUSED_ORCHESTRATOR_DRIFT", "Builder source changed after its recorded result")
        commit = snapshot_commit(Path(row["workspace"]), directory)
        if s.snapshot(Path(row["workspace"])) != current:
            raise s.Paused("PAUSED_ORCHESTRATOR_DRIFT", "Builder source changed while capturing its result")
        # Enforce ownership on the complete tree delta, including edits made
        # before the provider's own before-snapshot (or retained from retries).
        paths = list(filter(None, git(workspace, "diff", "--name-only", "--no-renames", "-z",
                                      batch["base_commit"], commit).decode().split("\0")))
        if changed.intersection(paths) or any(not any(contains(p, name) for p in row["task"]["affected_paths"]) for name in paths):
            raise s.Paused("PAUSED_ORCHESTRATOR_OWNERSHIP", "Builder changes overlap or exceed declared milestone paths; worktrees retained")
        changed.update(paths)
        for name in paths:
            if current["files"].get(name) == "deleted" and name not in tracked:
                expected["files"].pop(name, None)
            elif name in current["files"]:
                expected["files"][name] = current["files"][name]
            else:
                expected["files"].pop(name, None)
        patch = git(workspace, "diff", "--binary", batch["base_commit"], commit)
        patches.append(patch)
        row.update(status="BUILT", result=str(result_path), result_hash=s.file_hash(result_path),
                   commit=commit, changed_files=paths, implementation=child["implementation"],
                   stages=child["stages"])
    expected["revision"] = s.digest({"head": expected["head"], "files": expected["files"]})
    batch.update(expected=expected, changed_files=sorted(changed))
    patch_file = Path(batch["directory"]) / "combined.patch"
    patch_file.write_bytes(b"".join(patches))
    batch.update(patch=str(patch_file), patch_hash=s.file_hash(patch_file), status="READY_TO_INTEGRATE")
    s.atomic_json(run_dir / "state.json", state)


def integrate(state, workspace, run_dir, batch):
    if s.file_hash(batch["patch"]) != batch["patch_hash"]:
        raise s.Paused("PAUSED_ORCHESTRATOR_DRIFT", "Saved integration patch changed")
    current = s.snapshot(workspace)
    if current == batch["baseline"]:
        patch = Path(batch["patch"]).read_bytes()
        with interventions.admission(run_dir):
            if (run_dir / "pause-requested").exists():
                raise s.Paused("PAUSED_REQUESTED", "Pause requested before integration")
            if patch:
                git(workspace, "apply", "--check", "--binary", "-", data=patch)
                if s.snapshot(workspace) != batch["baseline"]:
                    raise s.Paused("PAUSED_ORCHESTRATOR_DRIFT", "Source changed before integration")
                batch["status"] = "INTEGRATING"
                s.atomic_json(run_dir / "state.json", state)
                git(workspace, "apply", "--binary", "-", data=patch)
    elif current != batch["expected"]:
        raise s.Paused("PAUSED_ORCHESTRATOR_DRIFT", "Integration workspace changed; Builder branches and patch retained")
    current = s.snapshot(workspace)
    if current != batch["expected"]:
        raise s.Paused("PAUSED_ORCHESTRATOR_DRIFT", "Integration does not match saved Builder output; inspect retained patch")
    tasks = [row["task"] for row in batch["workers"]]
    combined = copy.deepcopy(tasks[0])
    combined.update(id="task-" + batch["id"], milestone_ids=[t["milestone_id"] for t in tasks],
                    objective="Integrate and verify: " + "; ".join(t["objective"] for t in tasks))
    for field in ("requirements", "acceptance_criteria", "validation_plan", "affected_paths"):
        combined[field] = list(dict.fromkeys(v for task in tasks for v in task[field]))
    state.setdefault("task_archive", []).append(state["current_task"])
    state.update(current_task=combined, affected_paths=combined["affected_paths"], next_action=combined["objective"],
                 changed_files=batch["changed_files"], diff_ref=batch["patch"], next_stage="sol", phase="EXECUTING")
    snapshot_path = Path(batch["directory"]) / "integrated.json"
    s.atomic_json(snapshot_path, current)
    state["source_snapshot"] = str(snapshot_path)
    state["implementation"] = {"summary": combined["objective"], "source_revision": current["revision"],
                               "workspace": str(workspace), "builder_reports": [r["result"] for r in batch["workers"]],
                               "task_id": combined["id"], "contract_revision": state["goal_contract"]["revision"],
                               "contract_hash": batch["contract_hash"]}
    goals.invalidate(state, "Parallel Builders integrated; validate the combined artifact")
    state["sessions"] = {k: v for k, v in state.get("sessions", {}).items() if k not in ("terra", "sol", "completion")}
    batch.update(status="INTEGRATED", finished_at=s.now())
    state.setdefault("orchestration_history", []).append(copy.deepcopy(batch))
    state.pop("orchestration_batch")
    progress = milestones.progress(state)
    progress["seconds"] = sum(state["milestone_progress"].get(f"{batch['contract_hash']}:{t['milestone_id']}", {}).get("seconds", 0)
                              for t in tasks)
    progress["seconds_by_role"] = {"terra": progress["seconds"]}


def dispatch(state, workspace, run_dir):
    goals.execution_guard(state)
    if not enabled(state):
        raise ValueError("Orchestration is not enabled for this run")
    batch = state.get("orchestration_batch")
    if batch and batch["contract_hash"] != state["goal_contract"]["hash"]:
        for row in batch["workers"]:
            if row.get("processes") and processes.live_processes(row["processes"]):
                raise s.Paused("PAUSED_ORCHESTRATOR_WORKERS", "Wait for the previous plan's Builders before scheduling its replacement")
            s.assert_no_legacy_process(Path(row["run_dir"]), Path(row["workspace"]))
            if (Path(row["workspace"]) / ".git").exists():
                with s.workspace_lock(Path(row["workspace"])):
                    pass
        if batch["status"] != "PREPARING":
            account_workers(state, run_dir, batch)
        batch.update(superseded_status=batch["status"], status="SUPERSEDED", finished_at=s.now())
        state.setdefault("orchestration_history", []).append(copy.deepcopy(batch))
        state.pop("orchestration_batch")
        s.atomic_json(run_dir / "state.json", state)
        batch = None
    if batch is None:
        selected = select(state)
        if not selected:
            state["next_stage"] = "terra"
        else:
            batch = prepare(state, workspace, run_dir, selected)
    if batch:
        if batch["status"] == "PREPARING":
            finish_preparation(state, workspace, run_dir, batch)
        if batch["status"] == "BUILDING":
            if s.snapshot(workspace) != batch["baseline"]:
                raise s.Paused("PAUSED_ORCHESTRATOR_DRIFT", "Parent source changed before Builder dispatch")
            account_workers(state, run_dir, batch)
            run_workers(state, run_dir, batch)
            # A saved RUNNING worker is never replayed; collect only terminal evidence.
            collect(state, workspace, run_dir, batch)
        integrate(state, workspace, run_dir, batch)
    record = {"stage": "orchestrator", "role": "orchestrator", "iteration": state["iteration"],
              "finished_at": s.now(), "runner_owned": True, "batch_id": batch["id"] if batch else None,
              "engine": "runner",
              "duration_seconds": 0, "metrics": {"provider_tokens": {"input_tokens": 0, "output_tokens": 0}},
              "summary": "Integrated independent Builders; awaiting validation" if batch else "Dispatched one Builder"}
    output = run_dir / "orchestration" / ("dispatch-" + uuid.uuid4().hex[:12] + ".json")
    record["output"] = str(output)
    s.atomic_json(output, record)
    state.setdefault("stages", []).append(record)
    state.setdefault("history", []).append(record)
    s.atomic_json(run_dir / "state.json", state)
    return record


def request_retry(state, run_dir, selected):
    """Explicitly retry named stopped members while retaining successful siblings."""
    goals.execution_guard(state)
    batch = state.get("orchestration_batch")
    if (not batch or batch["status"] != "BUILDING" or state.get("next_stage") != "orchestrator"
            or batch["contract_hash"] != state["goal_contract"]["hash"]):
        raise ValueError("Builder retry requires the current approved batch at its build checkpoint")
    rows = {row["milestone_id"]: row for row in batch["workers"]}
    if not set(selected) <= set(rows):
        raise ValueError("--retry-builder must name a milestone in the current batch")
    for row in batch["workers"]:
        if row.get("processes") and processes.live_processes(row["processes"]):
            raise s.Paused("PAUSED_ORCHESTRATOR_WORKERS", "A Builder is still running; wait before retrying")
        s.assert_no_legacy_process(Path(row["run_dir"]), Path(row["workspace"]))
    for mid in selected:
        result = Path(rows[mid]["run_dir"]) / "result.json"
        if result.exists() and s.read(result).get("status") == "BUILT":
            raise ValueError(f"Builder {mid} already completed; its work will be retained")
    for mid in selected:
        rows[mid]["retry_requested"] = True
    state.setdefault("user_events", []).append({"kind": "builder_retry", "actor": "user_cli",
        "at": s.now(), "batch_id": batch["id"], "milestone_ids": list(selected)})
    s.atomic_json(run_dir / "state.json", state)

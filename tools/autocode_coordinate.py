"""Automatic multi-repository bug delivery over public task-run interfaces.

One advancing invocation performs one durable step. The TUI renders this
controller's named actions; it never resolves repositories or drives child runs
itself. No member state.json is read or written here.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

try:
    from . import autocode_changeset as changesets
    from . import autocode_changeset_store as sets
    from . import autocode_client_actions as actions
    from . import autocode_coordination_access as access
    from . import autocode_coordination_budget as budget
    from . import autocode_coordination_joint as joint
    from . import autocode_coordination_publish as publication
    from . import autocode_coordination_repositories as repos
    from . import autocode_coordination_store as store
    from . import autocode_github as github
    from . import autocode_taskrun as tasks
    from . import autocode_util as util
except ImportError:
    import autocode_changeset as changesets
    import autocode_changeset_store as sets
    import autocode_client_actions as actions
    import autocode_coordination_access as access
    import autocode_coordination_budget as budget
    import autocode_coordination_joint as joint
    import autocode_coordination_publish as publication
    import autocode_coordination_repositories as repos
    import autocode_coordination_store as store
    import autocode_github as github
    import autocode_taskrun as tasks
    import autocode_util as util

MAX_REPOSITORIES = 12
MAX_JOINT_ATTEMPTS = 3


def handle(row, *, command=tasks.AUTOCODE):
    if "origin" in row:
        repos.require_worktree(row, row["workspace"])
    return tasks.TaskRun(Path(row["workspace"]), Path(row["run_dir"]), command=command)


def anchor_view(run_dir, *, command=tasks.AUTOCODE):
    return changesets.source_view(Path(run_dir), command=command)[1]


def initialize(run_dir, *, command=tasks.AUTOCODE):
    view = anchor_view(run_dir, command=command)
    if (view.get("execution") or {}).get("mode") != "owned":
        raise store.CoordinationError(
            "Automatic coordination currently requires an owned terminal chat; this run's execution policy was not changed"
        )
    scope = view.get("scope") or {}
    if view.get("workflow") != "bugfix" or not scope.get("external_parts") or scope.get("decision"):
        raise store.CoordinationError("This run has no unresolved multi-repository bug to coordinate")
    directory = store.root(run_dir)
    profile = view.get("related_profile")
    if not profile:
        raise store.CoordinationError("This core does not expose the original run's routing and limits")
    util.atomic_json(directory / "profile.json", profile)
    document = {
        "version": 1,
        "id": directory.name,
        "anchor": str(Path(run_dir).resolve()),
        "task": view["task"],
        "anchor_turn": view["turn"],
        "anchor_scope": scope["identity"],
        "budget": budget.initial(view),
        "created_at": util.now(),
        "phase": "discovering",
        "profile": str(directory / "profile.json"),
        "profile_sha256": util.file_hash(directory / "profile.json"),
        "members": [],
        "choices": {},
        "pending": None,
        "joint_attempts": [],
        "initial_scope": scope["external_parts"],
    }
    origin = repos.identity(view["workspace"])
    add_member(document, Path(view["workspace"]).name, origin, view["task"])
    store.save(document)
    return document


def add_member(document, target, entry, brief, *, reported_work=None):
    repos.require_clean_product(entry)
    if len(document["members"]) >= MAX_REPOSITORIES:
        raise store.CoordinationError("Automatic repository limit reached; review the discovered scope")
    if any(row["git_dir"] == entry["git_dir"] for row in document["members"]):
        raise store.CoordinationError("Two target names resolve to the same repository; reconcile the catalog names")
    repository = repos.publication_repository(entry["remote"])
    if not repository:
        raise store.CoordinationError(
            "Repository needs an origin remote identifying its GitHub repository before automatic delivery"
        )
    base_branch = repos.git(entry["origin"], "symbolic-ref", "--quiet", "--short", "HEAD", accepted=(0, 1))
    if not base_branch:
        raise store.CoordinationError("Repository has a detached base; select its delivery base branch in the catalog")
    slug = repos.slug(target)
    workspace = store.root(document["anchor"]) / "worktrees" / slug
    access.check({**entry, "repository": list(repository), "base_branch": base_branch})
    document["members"].append(
        {
            **entry,
            "target": target,
            "slug": slug,
            "brief": brief,
            **({"reported_work": reported_work} if reported_work else {}),
            "workspace": str(workspace),
            "run_dir": str(workspace / ".autocode" / "runs" / "coordinated-fix"),
            "base_branch": base_branch,
            "repository": list(repository),
            "branch": "autocode/fix-" + document["id"][:12] + "-" + slug,
            "started": False,
        }
    )


def observe(document, *, command=tasks.AUTOCODE):
    if document.get("anchor_turn"):
        current_anchor = anchor_view(document["anchor"], command=command)
        if current_anchor.get("turn") != document["anchor_turn"] or current_anchor.get("task") != document["task"]:
            raise store.CoordinationError(
                "The anchor request changed; its previous coordination cannot certify this new request"
            )
        if document.get("anchor_scope") and (
            (current_anchor.get("scope") or {}).get("identity") != document["anchor_scope"]
            or (current_anchor.get("scope") or {}).get("decision")
        ):
            raise store.CoordinationError(
                "The anchor scope decision changed; review the updated request before coordinating"
            )
    views = {}
    for member in document["members"]:
        if member["started"]:
            views[member["target"]] = handle(member, command=command).status()
    return views


def observe_dependencies(document, views):
    graph = document.setdefault("dependencies", {})
    aliases = repos.member_aliases(document["members"], document["choices"])
    for target, view in views.items():
        found = {part["target"] for part in (view.get("scope") or {}).get("external_parts", [])}
        graph[target] = sorted({aliases.get(name, name) for name in set(graph.get(target, [])) | found})


def requests(document, views):
    requests = {part["target"]: part.get("work", "") for part in document["initial_scope"]}
    for view in views.values():
        for part in (view.get("scope") or {}).get("external_parts") or []:
            requests.setdefault(part["target"], part.get("work", ""))
    existing = {row["target"] for row in document["members"]}
    existing.update(repos.member_aliases(document["members"], document["choices"]))
    return {target: work for target, work in requests.items() if target not in existing}


def resolve_request(document, views):
    wanted = requests(document, views)
    if not wanted:
        return None
    target, work = next(iter(wanted.items()))
    candidates = repos.candidates(target, repos.catalog())
    choice = document["choices"].get(target)
    chosen = next((row for row in candidates if row["origin"] == choice), None) if choice else None
    if chosen is None and len(candidates) == 1:
        chosen = candidates[0]
    if chosen:
        member = next((row for row in document["members"] if row["git_dir"] == chosen["git_dir"]), None)
        if member:
            return {"kind": "alias", "target": target, "canonical": member["target"], "origin": member["origin"]}
        return {"kind": "resolve", "target": target, "work": work, "entry": chosen}
    return {
        "kind": "repository",
        "target": target,
        "work": work,
        "reason": "Repository match is ambiguous"
        if candidates
        else "Repository is not available in the authorized local catalog",
        "candidates": [{"workspace": row["origin"], "base": row["base"]} for row in candidates],
    }


def sync_set(document):
    members = document["members"]
    starting = (
        (document.get("pending") or {}).get("target")
        if (document.get("pending") or {}).get("operation") == "start"
        else None
    )
    if len(members) < 2 or not (members[0]["started"] or starting == members[0]["target"]):
        return
    source = members[0]
    record = sets.find(source["run_dir"])
    for member in members[1:]:
        if not record or member["target"] not in {row["target"] for row in sets.members(record)}:
            record = sets.reserve(
                source_workspace=source["workspace"],
                source_run_dir=source["run_dir"],
                target=member["target"],
                workspace=member["workspace"],
                brief=member["brief"],
            )
        saved = next(row for row in sets.members(record) if row["target"] == member["target"])
        if (member["started"] or starting == member["target"]) and not saved.get("run_dir"):
            sets.bind(record["id"], member["target"], member["run_dir"])
            record = sets.find(source["run_dir"])
    if record:
        sets.set_fields(
            record["id"],
            {"source": {**record["source"], "target": source["target"]}, "automatic_anchor": document["anchor"]},
        )
        document["changeset"] = record["id"]


def next_step(document, views, *, command=tasks.AUTOCODE):
    if store.cancelled(document["anchor"]):
        return {"kind": "cancelled"}
    if document.get("pending"):
        return {"kind": "reconcile", **document["pending"]}
    if document.get("rework"):
        return {"kind": "rework", "targets": document["rework"]["targets"]}
    if document.get("phase") == "complete":
        if any(repos.fingerprint(row["workspace"]) != row.get("tested_fingerprint") for row in document["members"]):
            return {"kind": "blocked", "reason": "A published candidate changed after verification"}
        try:
            require_joint_evidence(document, command=command)
            for row in document["members"]:
                publication.authenticate(row, command=command)
        except (ValueError, tasks.TaskRunError) as error:
            return {"kind": "blocked", "reason": str(error)}
        return {"kind": "complete"}
    if document.get("phase") == "publishing":
        return {"kind": "publish"}
    discovery = resolve_request(document, views)
    if discovery:
        return discovery
    for member in document["members"]:
        if not member["started"]:
            return {"kind": "start", "target": member["target"]}
    by_target = {row["target"]: row for row in document["members"]}
    aliases = repos.member_aliases(document["members"], document["choices"])
    by_target.update({alias: by_target[canonical] for alias, canonical in aliases.items()})
    for target, view in views.items():
        scope = view.get("scope") or {}
        if scope.get("external_parts") and not scope.get("decision"):
            required = {part["target"] for part in scope["external_parts"]}
            if required <= by_target.keys():
                return {
                    "kind": "assign",
                    "target": target,
                    "identity": scope["identity"],
                    "members": {name: by_target[name]["run_dir"] for name in required},
                    **(
                        {"aliases": {name: aliases[name] for name in required if name in aliases}}
                        if required & aliases.keys()
                        else {}
                    ),
                }
        if (view.get("needs") or {}).get("kind") == "continue":
            if actions.working(view):
                return {"kind": "watch", "target": target}
            return {"kind": "advance", "target": target}
    if all(view.get("done") for view in views.values()):
        active = document.get("joint")
        if not active:
            return {"kind": "joint_start"}
        view = handle(active, command=command).status()
        if view.get("done"):
            mismatched = joint.mismatches(document, active["workspace"])
            if mismatched:
                if len(document["joint_attempts"]) >= MAX_JOINT_ATTEMPTS:
                    return {
                        "kind": "blocked",
                        "reason": "Joint repair limit reached; repository candidates still disagree",
                    }
                return {"kind": "rework", "targets": [row["target"] for row in mismatched]}
            if not active.get("verification"):
                return {"kind": "verify"}
            if active["verification"].get("verdict") != "PASS":
                if len(document["joint_attempts"]) < MAX_JOINT_ATTEMPTS:
                    return {"kind": "joint_retry"}
                return {
                    "kind": "blocked",
                    "reason": "Joint regression did not pass independent fail-before/pass-after verification",
                    "verification": active["verification"],
                }
            return {"kind": "publish"}
        if (view.get("needs") or {}).get("kind") == "continue":
            return (
                {"kind": "joint_advance"}
                if not actions.working(view)
                else {"kind": "watch", "target": "Joint regression"}
            )
        plan = actions.plan_for_approval(view)
        return (
            {"kind": "joint_approval", "plan": plan}
            if plan
            else {"kind": "input", "target": "Joint regression", "view": view}
        )
    if all((view.get("needs") or {}).get("kind") == "approve_plan" for view in views.values()):
        summary = changesets.status(document["members"][0]["run_dir"], command=command)
        return {"kind": "approval", "summary": summary}
    for target, view in views.items():
        if not view.get("done") and (view.get("needs") or {}).get("kind") != "approve_plan":
            return {"kind": "input", "target": target, "view": view}
    completed = [target for target, view in views.items() if view.get("done")]
    if completed:
        return {"kind": "refresh", "targets": completed}
    return {
        "kind": "blocked",
        "reason": "Some members completed while others need a new coordinated plan; inspect their current gates",
    }


AUTOMATIC = {
    "resolve",
    "start",
    "alias",
    "assign",
    "advance",
    "joint_start",
    "joint_advance",
    "joint_retry",
    "rework",
    "verify",
    "publish",
    "reconcile",
    "refresh",
}


def status(run_dir, *, command=tasks.AUTOCODE):
    document = store.read(run_dir)
    if not document:
        view = anchor_view(run_dir, command=command)
        if not view.get("coordination"):
            raise store.CoordinationError("No automatic coordination is offered for this run")
        stopped = store.cancelled(run_dir)
        return {
            "version": 1,
            "anchor": str(Path(run_dir).resolve()),
            "phase": "cancelled" if stopped else "discovering",
            "done": False,
            "members": [],
            "needs": {"kind": "cancelled" if stopped else "continue"},
            "next_command": None if stopped else store.command(run_dir, "advance", "--json"),
        }
    views = observe(document, command=command)
    step = next_step(document, views, command=command)
    output = {
        "version": 1,
        "anchor": document["anchor"],
        "revision": document["revision"],
        "phase": document["phase"],
        "done": step["kind"] == "complete",
        "needs": step,
        "verification": (document.get("joint") or {}).get("verification"),
        "integration": {key: document["joint"][key] for key in ("workspace", "run_dir")}
        if document.get("joint")
        else None,
        "joint_attempts": len(document["joint_attempts"]),
        "members": [
            {
                "target": row["target"],
                "workspace": row["workspace"],
                "run_dir": row["run_dir"],
                "base": row["base"],
                "status": views.get(row["target"], {}).get("status", "STARTING"),
                "pr_url": row.get("publication", {}).get("url"),
            }
            for row in document["members"]
        ],
        "next_command": store.command(run_dir, "advance", "--json") if step["kind"] in AUTOMATIC else None,
        "cancel_command": store.command(run_dir, "cancel", "--json"),
    }
    if step["kind"] in ("approval", "joint_approval"):
        binding = {
            "revision": document["revision"],
            "step": step,
            "members": [
                {key: row[key] for key in ("target", "base", "branch", "repository", "run_dir")}
                for row in document["members"]
            ],
        }
        token = util.digest(binding)
        output["approval"] = {
            "token": token,
            "next_command": store.command(run_dir, "approve", "--token", token, "--json"),
            "publication": "After all member gates and the joint regression pass, publish one linked PR per changed repository. No automatic merge.",
        }
    if step["kind"] == "repository":
        output["needs"]["candidates"] = [
            {
                **row,
                "next_command": store.command(
                    run_dir, "choose", "--target", step["target"], "--workspace", row["workspace"], "--json"
                ),
            }
            for row in step["candidates"]
        ]
    return output


def options(document):
    return ("--related-profile", document["profile"], "--related-profile-sha256", document["profile_sha256"])


def budget_options(document, views, current=None, *, command=tasks.AUTOCODE):
    history = [*views.values()]
    for row in document["joint_attempts"]:
        if row.get("started"):
            history.append(handle(row, command=command).status())
        if row.get("verification"):
            history.append(budget.verification_view(row["verification"]))
    return tuple(budget.envelope(document.get("budget") or {}, history, current)["options"])


def require_joint_evidence(document, *, command=tasks.AUTOCODE):
    active = document["joint"]
    reply = tasks.run_process([*command, "verify", "--record", active["verification"]["record"], "--json"])
    if (
        reply.returncode
        or json.loads(reply.stdout).get("verdict") != "PASS"
        or joint.mismatches(document, active["workspace"])
    ):
        raise store.CoordinationError("Joint evidence is stale; publication is not authorized")


def finish_rework_step(document, target):
    progress = document["rework"]
    if progress["remaining"][0]["target"] != target:
        raise store.CoordinationError("Interrupted follow-up no longer matches the repair sequence")
    progress["remaining"].pop(0)
    document["pending"] = None
    if not progress["remaining"]:
        if document.get("joint"):
            document["joint"]["mismatch_targets"] = progress["targets"]
            document["joint_attempts"][-1] = document["joint"]
            document.pop("joint")
        document.pop("rework")
    document["phase"] = progress.get("phase", "correcting_mismatch")


def follow_up_received(view, previous_turn, message):
    submitted = [row for row in (view.get("chat") or {}).get("messages", []) if row.get("role") == "user"]
    # Follow-up input trims surrounding whitespace before recording the event.
    return view.get("turn") == previous_turn + 1 and bool(submitted) and submitted[-1].get("text") == message.strip()


def start_member(document, member, *, command=tasks.AUTOCODE, limits=()):
    repos.prepare(member, member["workspace"])
    document["pending"] = {"operation": "start", "target": member["target"]}
    store.save(document)
    # Bind the reserved identity before its first Requirements prompt is built.
    # The pending start remains uncertain until the public task interface returns.
    sync_set(document)
    tasks.TaskRun.start(
        member["workspace"],
        member["brief"],
        start_options=(*options(document), *limits, "--workflow", "bugfix"),
        command=command,
        run_dir=member["run_dir"],
    )
    member["started"] = True
    document["pending"] = None
    sync_set(document)


def advance(run_dir, *, command=tasks.AUTOCODE):
    with store.locked(run_dir) as document:
        if not document and store.cancelled(run_dir):
            return status(run_dir, command=command)
        document = document or initialize(run_dir, command=command)
        views = observe(document, command=command)
        step = next_step(document, views, command=command)
        kind = step["kind"]
        if kind not in AUTOMATIC:
            return status(run_dir, command=command)
        document["member_statuses"] = {target: view["status"] for target, view in views.items()}
        observe_dependencies(document, views)
        document["active"] = step.get("target") or kind.replace("_", " ")
        store.save(document)
        member = next((row for row in document["members"] if row["target"] == step.get("target")), None)
        if kind == "resolve":
            add_member(
                document,
                step["target"],
                step["entry"],
                document["task"],
                reported_work=step["work"],
            )
            sync_set(document)
        elif kind == "alias":
            document["choices"][step["target"]] = step["origin"]
        elif kind == "start":
            start_member(document, member, command=command, limits=budget_options(document, views, command=command))
        elif kind == "reconcile":
            if step.get("operation") == "follow_up" and member:
                fresh = handle(member, command=command).status()
                if not follow_up_received(fresh, step["turn"], step["message"]):
                    raise store.CoordinationError("Follow-up outcome is uncertain; it was not submitted twice")
                finish_rework_step(document, member["target"])
                store.save(document)
                return status(run_dir, command=command)
            if step.get("operation") == "joint_start":
                handle(document["joint"], command=command).status()
                document["joint"]["started"] = True
                document["joint_attempts"][-1] = document["joint"]
                document["pending"] = None
                store.save(document)
                return status(run_dir, command=command)
            if step.get("operation") != "start" or member is None:
                raise store.CoordinationError("Interrupted operation needs inspection; it was not replayed")
            handle(member, command=command).status()
            member["started"] = True
            document["pending"] = None
            sync_set(document)
        elif kind == "assign":
            record = sets.find(member["run_dir"])
            assignments = dict(record.get("scope_assignments") or {})
            assignments[member["run_dir"]] = {"identity": step["identity"], "members": step["members"]}
            if step.get("aliases"):
                assignments[member["run_dir"]]["aliases"] = step["aliases"]
            sets.set_fields(record["id"], {"scope_assignments": assignments})
            handle(member, command=command).assign_linked_scope(step["identity"])
        elif kind == "advance":
            run = handle(member, command=command)
            run.options = budget_options(document, views, member["run_dir"], command=command)
            run.advance()
            document["phase"] = "fixing"
        elif kind == "joint_start":
            limits = budget_options(document, views, command=command)
            if len(document["joint_attempts"]) >= MAX_JOINT_ATTEMPTS:
                raise store.CoordinationError("Joint repair limit reached; inspect the retained mismatch evidence")
            directory = store.root(run_dir) / f"joint-{len(document['joint_attempts']) + 1}"
            baseline = joint.prepare(document, directory)
            entry = {
                "workspace": str(directory),
                "run_dir": str(directory / ".autocode/runs/joint-regression"),
                "base": baseline,
                "inputs": {row["target"]: repos.fingerprint(row["workspace"]) for row in document["members"]},
            }
            document["joint"] = entry
            document["joint_attempts"].append(entry)
            document["phase"] = "joint_regression"
            document["pending"] = {"operation": "joint_start"}
            store.save(document)
            tasks.TaskRun.start(
                directory,
                joint.brief(document),
                start_options=(*options(document), *limits, "--workflow", "build"),
                command=command,
                run_dir=entry["run_dir"],
            )
            entry["started"] = True
            document["pending"] = None
        elif kind == "joint_advance":
            run = handle(document["joint"], command=command)
            run.options = budget_options(document, views, run.run_dir.as_posix(), command=command)
            run.advance()
        elif kind == "joint_retry":
            document["joint_feedback"] = joint.failure_feedback(document["joint"]["verification"])
            document["joint_attempts"][-1] = document.pop("joint")
            document["phase"] = "correcting_joint_regression"
        elif kind == "refresh":
            # A newly discovered member invalidates the old cohort. Completed
            # peers need fresh plans too; the core already holds unfinished
            # approved members for reapproval against the expanded set.
            document["rework"] = {
                "targets": step["targets"],
                "phase": "refreshing_scope",
                "remaining": [
                    {
                        "target": target,
                        "message": "The affected repository set expanded to "
                        + ", ".join(row["target"] for row in document["members"])
                        + ". Recheck this repository's part of the original bug against the full set, retaining correct work. Review the revised coordinated plans before implementation and test all candidates together again.",
                    }
                    for target in step["targets"]
                ],
            }
        elif kind == "rework":
            active = document.get("joint")
            if not document.get("rework"):
                if not active:
                    raise store.CoordinationError("No retained joint mismatch is available to repair")
                document["rework"] = {
                    "targets": step["targets"],
                    "remaining": [
                        {
                            "target": row["target"],
                            "message": joint.correction(row, active["workspace"])
                            if row["target"] in step["targets"]
                            else "A joint regression found a mismatch in another repository. Recheck this repository's part of the original bug against the coordinated interfaces, retaining correct existing work. If no edits are needed, plan a validation task and run the existing checks; do not schedule a no-op Builder implementation. The whole revised set will be reviewed and tested together again.",
                        }
                        for row in document["members"]
                    ],
                }
                store.save(document)
            item = document["rework"]["remaining"][0]
            row = next(row for row in document["members"] if row["target"] == item["target"])
            document["pending"] = {
                "operation": "follow_up",
                "target": row["target"],
                "turn": views[row["target"]]["turn"],
                "message": item["message"],
            }
            store.save(document)
            fresh = handle(row, command=command).follow_up(item["message"])
            if not follow_up_received(fresh, document["pending"]["turn"], item["message"]):
                raise store.CoordinationError("Follow-up receipt is unconfirmed; inspect before continuing")
            finish_rework_step(document, row["target"])
        elif kind == "verify":
            active = document["joint"]
            view = handle(active, command=command).status()
            contract = joint.criteria(document, view)
            directory = store.root(run_dir)
            criteria = directory / f"joint-criteria-{len(document['joint_attempts'])}.json"
            util.atomic_json(criteria, contract)
            output = directory / f"verification-{len(document['joint_attempts'])}"
            limits = json.loads(Path(document["profile"]).read_text())["settings"]["limits"]
            argv = [
                *command,
                "verify",
                "--workspace",
                active["workspace"],
                "--base",
                active["base"],
                "--criteria-file",
                str(criteria),
                "--suite-command",
                contract["acceptance_criteria"][0]["verification_method"],
                "--out",
                str(output),
                *options(document),
                *budget_options(document, views, command=command),
                "--max-seconds",
                str(limits.get("max_seconds") or 900),
                "--max-stage-seconds",
                str(limits.get("stage_timeout_seconds") or 600),
                "--check-timeout",
                str(min(limits.get("tool_timeout_seconds") or 900, limits.get("max_seconds") or 900)),
                "--json",
            ]
            reply = tasks.run_process(argv, advancing=True)
            if reply.returncode not in (0, 2):
                raise store.CoordinationError("Joint verification refused: " + (reply.stderr or reply.stdout)[-2000:])
            active["verification"] = json.loads(reply.stdout)
            if active["verification"].get("verdict") == "PASS":
                if joint.mismatches(document, active["workspace"]):
                    raise store.CoordinationError("Member code changed during joint verification")
                for row in document["members"]:
                    row["tested_fingerprint"] = repos.fingerprint(row["workspace"])
                document["phase"] = "publishing"
        elif kind == "publish":
            require_joint_evidence(document, command=command)
            for row in document["members"]:
                publication.prepare(document, row, command=command)
            for row in document["members"]:
                if store.cancelled(run_dir):
                    raise store.CoordinationError("Publication cancelled; already published PRs are retained")
                require_joint_evidence(document, command=command)
                publication.publish_one(document, row)
            publication.crosslink(document)
            document["phase"] = "complete"
        if document.get("joint"):
            document["joint_attempts"][-1] = document["joint"]
        store.save(document)
    return status(run_dir, command=command)


def approve(run_dir, token, *, command=tasks.AUTOCODE):
    with store.locked(run_dir) as document:
        shown = status(run_dir, command=command)
        if token != (shown.get("approval") or {}).get("token"):
            raise store.CoordinationError("Coordinated plans changed after display; review the current plans")
        step = shown["needs"]
        if step["kind"] == "approval":
            changesets.approve(
                document["members"][0]["run_dir"], step["summary"]["approval"]["set_token"], command=command
            )
        elif step["kind"] == "joint_approval":
            handle(document["joint"], command=command).approve_plan(step["plan"]["token"])
        else:
            raise store.CoordinationError("No coordinated approval is offered")
        store.save(document)
    return status(run_dir, command=command)


def parser():
    result = argparse.ArgumentParser(
        prog="autocode coordinate", description="Discover, fix, jointly verify and publish linked repository changes"
    )
    commands = result.add_subparsers(dest="operation", required=True)
    commands.add_parser("access").add_argument("--json", action="store_true")
    for name in ("status", "advance", "approve", "choose", "cancel"):
        child = commands.add_parser(name)
        child.add_argument("--run-dir", required=True, type=Path)
        child.add_argument("--json", action="store_true")
        if name == "approve":
            child.add_argument("--token", required=True)
        if name == "choose":
            child.add_argument("--target", required=True)
            child.add_argument("--workspace", required=True)
    return result


def cancel(run_dir, *, command=tasks.AUTOCODE):
    """A lock-independent boundary request; never interrupt or replay an agent."""
    document = store.read(run_dir)
    util.atomic_json(store.root(run_dir) / "cancel-request.json", {"requested_at": util.now()})
    receipts = []
    if not document:
        return {"cancelled": True, "pause_receipts": receipts}
    rows = [row for row in document["members"] if row["started"]]
    if document.get("joint"):
        rows.append(document["joint"])
    for row in rows:
        view = handle(row, command=command).status()
        if (view.get("pause") or {}).get("next_command"):
            argv = actions.pause(view, "coordination-" + uuid.uuid4().hex, Path(row["workspace"]), Path(row["run_dir"]))
            reply = tasks.run_process([*command, *argv])
            if reply.returncode:
                raise store.CoordinationError("Coordination is cancelled, but a member pause receipt is unconfirmed")
            receipts.append(row["run_dir"])
    return {"cancelled": True, "pause_receipts": receipts}


def cli(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.operation == "access":
            print(json.dumps(access.readiness()))
            return 0
        if args.run_dir.parent.name != "runs" or args.run_dir.parent.parent.name != ".autocode":
            raise store.CoordinationError("Use a task run directory under WORKSPACE/.autocode/runs")
        # The anchor's normal default, fixed for every related run and status call.
        os.environ.setdefault("AUTOCODE_HOME", str(args.run_dir.resolve().parents[2] / ".autocode" / "registry"))
        if args.operation == "status":
            result = status(args.run_dir)
        elif args.operation == "advance":
            result = advance(args.run_dir)
        elif args.operation == "approve":
            result = approve(
                args.run_dir, os.environ.get("AUTOCODE_COORDINATION_TOKEN", "") if args.token == "-" else args.token
            )
        elif args.operation == "cancel":
            result = cancel(args.run_dir)
        else:
            with store.locked(args.run_dir) as document:
                if not document:
                    raise store.CoordinationError("Coordination has not started")
                if args.operation == "choose":
                    fresh = status(args.run_dir)
                    need = fresh["needs"]
                    if (
                        need.get("kind") != "repository"
                        or need["target"] != args.target
                        or args.workspace not in [row["workspace"] for row in need["candidates"]]
                    ):
                        raise store.CoordinationError("Repository choice is not one of the current catalog candidates")
                    document["choices"][args.target] = args.workspace
                store.save(document)
            result = status(args.run_dir)
        print(json.dumps(result, indent=2))
        return 0
    except (
        ValueError,
        OSError,
        RuntimeError,
        tasks.TaskRunError,
        changesets.LinkRefused,
        sets.ChangesetError,
        github.GitHubError,
    ) as error:
        print(json.dumps({"error": str(error)}))
        return 2

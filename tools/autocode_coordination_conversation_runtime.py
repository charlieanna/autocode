"""Execute one coordinator-owned conversation step through public task actions."""

import json
import os
from copy import deepcopy

try:
    from . import autocode_changeset_store as sets
    from . import autocode_client_actions as actions
    from . import autocode_coordination_conversation as policy
    from . import autocode_coordination_request as request
    from . import autocode_coordination_store as store
    from . import autocode_taskrun as tasks
    from . import autocode_util as util
except ImportError:
    import autocode_changeset_store as sets
    import autocode_client_actions as actions
    import autocode_coordination_conversation as policy
    import autocode_coordination_request as request
    import autocode_coordination_store as store
    import autocode_taskrun as tasks
    import autocode_util as util


def observation(run):
    """Combine public status parts for policy without inventing a status-view field."""
    public = run.intervention_status()
    return {**public["view"], "interventions": public["interventions"]}


def submit(run, argv, expected):
    """An uncertain submission keeps its stable identity; only an exact receipt succeeds."""
    response = tasks.run_process([*run.command, *argv], env={**os.environ, **(run.env or {})}, timeout=30)
    try:
        output = json.loads(response.stdout)
        receipt = output["receipt"]
        if (
            response.returncode
            or output.get("accepted") is not True
            or any(receipt.get(key) != value for key, value in expected.items())
        ):
            raise ValueError("receipt mismatch")
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise store.CoordinationError(
            "Task conversation submission has no confirmed exact receipt; retain its request identity"
        ) from error
    return receipt


def execute(document, selected, requests, views, source, *, run_for, limits_for):
    """Apply one durable intent while the caller holds the coordination advance lock."""
    operation = selected["operation"]
    conversation = document.setdefault("conversation", {"results": []})
    active = conversation.get("active")
    identifier = selected.get("request_id") or (active or {}).get("id")
    message = next((row for row in requests if row["id"] == identifier), None)
    target = selected.get("target")
    if operation == "begin":
        if source.get("run_dir") != document["anchor"] or not source.get("workspace"):
            raise store.CoordinationError("Task conversation source identity does not match its anchor")
        saved = document["budget"]
        if saved.get("source_run_dir") not in (None, document["anchor"]):
            raise store.CoordinationError("Task conversation source identity conflicts with retained accounting")
        snapshot = policy.context(document, views)
        saved["source_run_dir"] = document["anchor"]
        conversation["active"] = {
            "id": message["id"],
            "source_request_id": policy.request_id(document, message, "Task conversation", "message"),
            "context": snapshot,
            "started_at": util.now(),
        }
    elif operation == "pause":
        run = run_for(target)
        identifier = policy.request_id(document, message, target, "pause")
        argv = actions.pause(observation(run), identifier, run.workspace, run.run_dir)
        submit(run, argv, {"id": identifier, "kind": "pause", "text": ""})
    elif operation in ("prepare_source", "source_advance", "consume", "resume"):
        target = "Task conversation" if operation in ("prepare_source", "source_advance") else target
        run = run_for(target)
        if operation == "resume":
            owned = {
                policy.request_id(document, row, target, kind) for row in requests for kind in ("pause", "feedback")
            }
            fresh = observation(run)
            if not policy.resumable(fresh, owned):
                raise store.CoordinationError("The conversation no longer has an exclusively owned pause")
            named = actions.named(fresh, run.workspace, run.run_dir)
            if named.argv != ["--resume-paused"] or named.env:
                raise store.CoordinationError("The owned pause names a different recovery action")
        run.options = limits_for(str(run.run_dir))
        if operation == "prepare_source":
            active["prepared_source"] = True
            store.save(document)
        if operation == "resume":
            run.resume_paused()
        else:
            run.advance()
    elif operation in ("submit", "feedback"):
        target = "Task conversation" if operation == "submit" else target
        run = run_for(target)
        kind = "message" if operation == "submit" else "feedback"
        identifier = (
            active["source_request_id"] if operation == "submit" else policy.request_id(document, message, target, kind)
        )
        expected = {"id": identifier, "kind": kind, "text": message["text"]}
        argv = [
            "intervention",
            "submit",
            "--workspace",
            str(run.workspace),
            "--run-dir",
            str(run.run_dir),
            "--request-id",
            identifier,
            "--kind",
            kind,
            "--text",
            message["text"],
            "--json",
        ]
        if operation == "submit":
            path = store.root(document["anchor"]) / (identifier + "-context.json")
            util.atomic_json(path, active["context"])
            argv.extend(["--context-file", str(path)])
            expected["context"] = active["context"]
        submit(run, argv, expected)
    elif operation == "capture":
        answer = policy.reply(active, source)
        if answer is None or policy.context(document, views) != active["context"]:
            raise store.CoordinationError("No current Requirements reply can be captured")
        active["reply"] = deepcopy(answer)
        active["answered_at"] = util.now()
    elif operation == "invalidate":
        active["targets"] = policy.affected(document, active["reply"]["conversation"]["affected_targets"])
        if document.get("joint"):
            joint = document["joint"]
            if document.get("changeset"):
                sets.remove_joint(document["members"][0]["run_dir"], joint["run_dir"])
            joint["superseded_by_message"] = message["id"]
            document["joint_attempts"][-1] = document.pop("joint")
        if "approved_contracts" in document:
            conversation.setdefault("superseded_approvals", []).append(
                {"request_id": message["id"], "record": document.pop("approved_contracts"), "at": util.now()}
            )
        active.update(invalidated=True, delivered=[])
        document["phase"] = "fixing"
    elif operation in ("delivered", "unstarted"):
        member = next(row for row in document["members"] if row["target"] == target)
        if operation == "delivered":
            identifier = policy.request_id(document, message, target, "feedback")
            receipts = (views[target].get("interventions") or {}).get("applied_receipts", [])
            if not any(
                row.get("id") == identifier and row.get("kind") == "feedback" and row.get("text") == message["text"]
                for row in receipts
            ):
                raise store.CoordinationError("The member feedback receipt does not bind the original message")
        sources = member.setdefault("message_sources", [])
        if not any(row["request_id"] == message["id"] for row in sources):
            sources.append({"request_id": message["id"], "text": message["text"]})
        member["brief"] = request.brief(document, target=target)
        if target not in active["delivered"]:
            active["delivered"].append(target)
    elif operation == "finish":
        conversation.setdefault("results", []).append(
            {
                "request_id": message["id"],
                "text": message["text"],
                "reply": active["reply"],
                "context": active["context"],
                "affected_targets": active.get("targets", []),
                "answered_at": active.get("answered_at") or util.now(),
            }
        )
        conversation["active"] = None
    else:
        raise store.CoordinationError("Unknown task conversation operation")
    store.save(document)

"""Pending-only coordinated messages bound to the public anchor offer."""

import json
import secrets

try:
    from . import autocode_client_actions as actions
except ImportError:
    import autocode_client_actions as actions


def command(summary, anchor, text, request_id):
    """Fill only the two placeholders of the exact task-message action."""
    offered = (summary.get("conversation") or {}).get("input") or {}
    expected = [
        "autocode",
        "coordinate",
        "message",
        "--run-dir",
        str(anchor),
        "--request-id",
        "REQUEST_ID",
        "--text",
        "MESSAGE",
        "--json",
    ]
    if (
        summary.get("anchor") != str(anchor)
        or not summary.get("id")
        or offered.get("kind") != "coordination_message"
        or offered.get("next_command") != expected
        or not isinstance(text, str)
        or not text.strip()
        or not request_id
    ):
        raise ValueError("No matching task message offer; your draft is kept")
    return [text if value == "MESSAGE" else request_id if value == "REQUEST_ID" else value for value in expected[1:]]


class Sender:
    def __init__(self, client):
        self.client = client
        self.pending = {}

    def send(self, text, shown):
        """Re-read the anchor offer once; uncertain receipts reuse the exact request."""
        client = self.client
        anchor = str(client.run_dir.resolve())
        if shown.get("anchor") != anchor:
            raise ValueError("The displayed task belongs to another chat; your draft is kept")
        key = (anchor, shown.get("id"), text)
        if key not in self.pending:
            view = client.status()
            if actions.own(view, client.workspace, client.run_dir) != (client.workspace, client.run_dir):
                raise ValueError("The source chat changed")
            expected = ["autocode", "coordinate", "status", "--run-dir", anchor, "--json"]
            if (view.get("coordination") or {}).get("status_command") != expected:
                raise ValueError("No public task status offer")
            response = client.cli(expected[1:])
            fresh = json.loads(response.stdout)
            if response.returncode or fresh.get("version") != 1 or fresh.get("id") != shown.get("id"):
                raise ValueError("Task identity changed")
            request = "chat-task-message-" + secrets.token_hex(16)
            command(shown, anchor, text, request)
            self.pending[key] = (request, command(fresh, anchor, text, request))
        request, argv = self.pending[key]
        response = client.cli(argv)
        value = json.loads(response.stdout)
        receipt = value.get("receipt") or {}
        if (
            response.returncode
            or value.get("version") != 1
            or value.get("accepted") is not True
            or value.get("consumer") != "pending_only"
            or receipt.get("id") != request
            or receipt.get("kind") != "message"
            or receipt.get("text") != text
            or receipt.get("coordination_id") != shown["id"]
        ):
            raise ValueError("Receipt does not confirm the exact task message")
        del self.pending[key]
        client.seen.add("task-message-" + request)
        return receipt


def show(client, summary):
    """Display saved requests and Requirements answers once using public identities."""
    conversation = summary.get("conversation") or {}
    for row in conversation.get("requests", []):
        identity = "task-message-" + row["id"]
        if identity not in client.seen:
            client.seen.add(identity)
            client.say("\nYou: " + row["text"])
    for row in conversation.get("results", []):
        answer = row["reply"]
        identity = answer.get("id") or "task-answer-" + row["request_id"]
        if identity not in client.seen:
            client.seen.add(identity)
            client.say("\nRequirements: " + answer["text"])

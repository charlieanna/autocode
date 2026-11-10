"""Pending-only message delivery for a thin chat client, with exact receipt retries."""

import json
import secrets

try:
    from . import autocode_chat_task_messages as task_messages
    from . import autocode_client_actions as actions
except ImportError:
    import autocode_chat_task_messages as task_messages
    import autocode_client_actions as actions


class MessageError(ValueError):
    """The composer must keep the draft until its exact receipt is confirmed."""


class MessageClosed(MessageError):
    """The step that offered the message ended before it was sent: nothing was submitted, and the
    run's next question (or step) takes the text instead. A retry would only be refused again."""


class Sender:
    def __init__(self, client):
        self.client = client
        self.pending = {}
        self.task_sender = task_messages.Sender(client)

    def send(self, text, shown):
        """Submit once, or reconcile the same request after an uncertain response."""
        if "anchor" in shown and "conversation" in shown:
            try:
                return self.task_sender.send(text, shown)
            except (actions.ClientGap, OSError, ValueError, TypeError, AttributeError) as error:
                raise MessageError(
                    "Task message receipt is unconfirmed; your draft is kept. Retry reconciles the same request."
                ) from error
        client = self.client
        workspace, run = actions.own(shown, client.workspace, client.run_dir)
        if (workspace, run) != (client.workspace, client.run_dir):
            raise MessageError("The displayed conversation belongs to another run; your draft is kept.")
        key = (str(workspace), str(run), text)
        try:
            if key not in self.pending:
                fresh = client.status()
                if actions.own(fresh, client.workspace, client.run_dir) != (workspace, run):
                    raise ValueError("The fresh view belongs to another conversation")
                if ((shown.get("chat") or {}).get("input") or {}).get("kind") != "message":
                    raise ValueError("The core offers no pending-only message here")
                if ((fresh.get("chat") or {}).get("input") or {}).get("kind") != "message":
                    # Nothing was submitted: the offer closed when the step ended (it reached a question).
                    raise MessageClosed("AutoCode finished this step before your message was sent.")
                request = "chat-message-" + secrets.token_hex(16)
                command = actions.message(
                    fresh,
                    text,
                    (shown.get("chat") or {}).get("context"),
                    workspace,
                    run,
                    request_id=request,
                )
                self.pending[key] = (request, command)
            request, command = self.pending[key]
            reply = client.cli([*command.argv, "--workspace", str(workspace), "--run-dir", str(run)], env=command.env)
            value = json.loads(reply.stdout)
            receipt = value.get("receipt") or {}
            if (
                reply.returncode
                or value.get("version") != 1
                or value.get("operation") != "submit"
                or value.get("accepted") is not True
                or receipt.get("id") != request
                or receipt.get("kind") != "message"
                or receipt.get("text") != text
                or not isinstance(receipt.get("message_id"), str)
                or not receipt["message_id"]
            ):
                raise ValueError("Receipt does not confirm this message")
        except MessageClosed:
            raise
        except (actions.ClientGap, OSError, ValueError, TypeError, AttributeError) as error:
            raise MessageError(
                "Message receipt is unconfirmed; your draft is kept. Sending it again reconciles the same request."
            ) from error
        del self.pending[key]
        client.seen.add(receipt["message_id"])  # the composer echoes this confirmed public message once
        return receipt

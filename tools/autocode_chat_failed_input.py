"""Pending-only task replies at a failed-command prompt, never recovery authority."""

try:
    from . import autocode_chat_coordination as coordination
    from . import autocode_chat_task_messages as task_messages
    from . import autocode_client_actions as actions
except ImportError:
    import autocode_chat_coordination as coordination
    import autocode_chat_task_messages as task_messages
    import autocode_client_actions as actions


def reply(client) -> bool:
    """Recheck after explicit input; continue only on Enter or an exact message receipt."""
    text = client.ask("Type a message or press Enter to check current status; /runs, /open or /quit: ").strip()
    if not text:
        return True
    try:
        view = client.status()
        summary = coordination.read(client, view) if view.get("coordination") else {}
    except (actions.ClientGap, OSError, ValueError, TypeError, AttributeError):
        summary = {}
    if ((summary.get("conversation") or {}).get("input") or {}).get("kind") != "coordination_message":
        client.say("Nothing sent. This run cannot accept a message right now. Press Enter to check it again.")
        return False
    sender = getattr(client, "_task_message_sender", None)
    if sender is None:
        sender = client._task_message_sender = task_messages.Sender(client)
    try:
        sender.send(text, summary)
    except (actions.ClientGap, OSError, ValueError, TypeError, AttributeError):
        client.say(
            "Message save is unconfirmed. Send the same text again to check it; the failed action was not retried."
        )
        return False
    client.say("Message saved. Requirements will reply after the current step is saved.")
    return True

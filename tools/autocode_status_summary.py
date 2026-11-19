"""The run in one sentence: needs you, working, done, stopped or blocked, and the exact next action (#2049).

A pure projection over the public status payload `--status` assembles, for a person who
glances at a run (`autocode status --summary`) and for clients that want one line. It
decides nothing and names no action of its own: ``next_command`` is the view's own
``next_command``. Raw detail (``status``, ``needs``, ``stop_reason``) stays in the full
payload.

Built work reads ``done`` only when completion is verified against the current checkout.
An accepted plan or a discussion answer has no built-code evidence and reads ``done``
under the core job classification. A finished build whose evidence no longer matches the checkout, or cannot be checked,
reads ``stopped``, as does a run whose saved status says it is running while nothing
supervises the retained attempt: neither is presented as delivered or as progress.
"""

from __future__ import annotations

try:
    from . import autocode_controller_owner as controller_owner
    from . import autocode_person_text as person_text
except ImportError:
    import autocode_controller_owner as controller_owner
    import autocode_person_text as person_text

NEEDS_YOU = "needs_you"
WORKING = "working"
DONE = "done"
STOPPED = "stopped"
BLOCKED = "blocked"
STATES = (NEEDS_YOU, WORKING, DONE, STOPPED, BLOCKED)
LABELS = {NEEDS_YOU: "Needs you", WORKING: "Working", DONE: "Done", STOPPED: "Stopped", BLOCKED: "Blocked"}
# A controller does the work in these statuses; the person is not yet asked for anything.
WORKING_STATUSES = ("RUNNING", "DISCOVERING", "WAITING_FOR_USER")
# Needs a run continues past on its own, or that another run answers.
NOT_A_PERSON = (None, "continue", "dependency")
NOTHING_NEEDED = "nothing needed from you"  # the progress view's needs_you when no one is waited on


def _sentence(text) -> str:
    text = str(text or "").strip()
    return text if not text or text.endswith((".", "?", "!")) else text + "."


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else ""


_plain = person_text.for_person


def _plain_asked(asked: object, raw_reason: object, recovery: dict) -> str:
    """``progress.needs_you``, which the progress view writes in words only, or the plain summary of
    leaked internal prompt/schema machinery when that is all the pause says (#3733)."""
    if person_text.internal_machinery(raw_reason) and (
        not asked or asked == NOTHING_NEEDED or str(asked).startswith("inspect the pause")
    ):
        return person_text.human_summary(raw_reason) or str(recovery.get("what_happened") or "")
    if not isinstance(asked, str) or not asked or asked == NOTHING_NEEDED:
        return ""
    return asked


def project(
    view: dict, *, stale: bool = False, completion_current=None, next_action=None, completed_without_building=False
) -> dict:
    """``view`` is the public status view with its ``progress``; ``stale`` is True when the saved
    status says running but no supervised result exists; ``completion_current`` is the completion
    gate's verdict on the current checkout (None when not checked); ``next_action`` is the status
    payload's own instruction for a stale checkpoint. ``completed_without_building`` comes from the
    core's job classification, never from display text or a model's completion claim."""
    status = str(view.get("status") or "")
    need = view.get("needs") or {}
    kind = need.get("kind") if isinstance(need, dict) else None
    progress = view.get("progress") or {}
    asked = progress.get("needs_you")
    command = view.get("next_command")
    if (view.get("recovery") or {}).get("category") == "blocked":
        state, sentence, command = (
            BLOCKED,
            _sentence(view["recovery"].get("what_happened") or "Internal cleanup is unconfirmed"),
            None,
        )
    elif stale:
        state = STOPPED
        sentence = _sentence(_plain(next_action)) or "The run stopped without saving a result."
        sentence = f"The run is not progressing: nothing supervises its retained attempt. {sentence}"
    elif view.get("done"):
        if completed_without_building:
            state, sentence, command = (
                DONE,
                _sentence(progress.get("headline") or "Response complete") + " Nothing was built or changed.",
                None,
            )
        elif completion_current is True:
            state, sentence, command = DONE, "Complete, and verified against the current checkout.", None
        else:
            state = STOPPED
            sentence = (
                "Finished earlier, but its verification does not cover the current checkout, "
                "so it is not shown as delivered."
            )
    elif kind not in NOT_A_PERSON or status == "AWAITING_GOAL_APPROVAL":
        state = NEEDS_YOU
        raw_reason = need.get("reason") or view.get("stop_reason")
        cleaned_asked = _plain_asked(asked, raw_reason, view.get("recovery") or {})
        reason = cleaned_asked if cleaned_asked else _plain(raw_reason) or view.get("pause_category_label")
        sentence = _sentence(_upper_first(str(reason or status.replace("_", " ").lower())))
    elif status == "RUNNING" and (view.get("controller") or {}).get("kind") == "ready" and command:
        # The controller exited on purpose after a saved answer, approval or follow-up: the run moves
        # only when someone runs the command, so "Working" would have the person wait for nothing (#2332).
        state = NEEDS_YOU
        sentence = "Saved and ready to continue; nothing runs it until you do."
    elif (
        status in WORKING_STATUSES
        or (status == "PAUSED_SOURCE_UNAVAILABLE" and kind == "continue")
        or kind == "dependency"
        or controller_owner.continuing(
            {"status": status, "execution": view.get("execution") or {}}, view.get("controller")
        )
    ):
        state = WORKING
        sentence = _sentence(progress.get("headline") or "Working")
    else:
        state = STOPPED
        sentence = _sentence(_plain(view.get("stop_reason")) or f"Stopped at {status or 'an unknown state'}")
    return {"state": state, "label": LABELS[state], "sentence": sentence, "next_command": command}


def text(summary: dict) -> str:
    """The summary as a person reads it: the state and sentence, then the command when there is one."""
    lines = [f"{summary['label']}: {summary['sentence']}"]
    if summary.get("next_command"):
        lines.append(f"Next: {summary['next_command']}")
    return "\n".join(lines)

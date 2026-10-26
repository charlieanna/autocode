"""Turning a run's gates into channel messages, and a channel reply into one action.

Pure functions over the status view and the bridge record, in the shape of
``autocode_client_actions``: the bridge renders what the view carries (questions
with their ids and options, the complete plan a token approves, a pause's reason)
and reduces a person's reply to the one action the view names. It never derives
commands of its own, never approves on free text, and never needs a control
token to cross the channel: the caller takes each action against a fresh view,
whose needs carry the current token.

The channel shows plain Markdown; ids are stable, so a reply can name
``q-1: yes`` in a multi-question gate, or simply be the answer when only one
question is open.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

try:
    from . import autocode_client_actions as actions
except ImportError:
    import autocode_client_actions as actions

APPROVE_WORDS = ("approve", "approved", "y", "yes", "lgtm")
APPROVE_SET = "approve set"  # the reply that takes a change set's named set approval (#2066)
RESUME_WORDS = ("resume", "y", "yes", "continue", "retry")
MODEL_PREFIX = re.compile(r"(?:use\s+)?model\s+(.+)$", re.IGNORECASE)
ANSWER_LINE = re.compile(r"^(?P<id>[\w.-]+)\s*[:=]\s*(?P<text>.+)$")
PERSON_ONLY_SCOPES = ("blocker", "operational_exhaustion")


@dataclass
class RelayResult:
    """What one status view means for the channel, and what to remember."""

    messages: list[str] = field(default_factory=list)
    gate: dict = field(default_factory=dict)
    # The view carries questions but no issued request yet: an advance publishes them.
    publish_questions: bool = False


@dataclass
class ReplyAction:
    """The one action a reply names, checked against the gate the person saw."""

    kind: str  # answers approve approve_set feedback follow_up resume retry model operational review none
    answers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    model: str = ""
    # The plan/review (or change-set) token the person was shown with this gate, for the caller's fresh check.
    shown_token: str = ""


def _reason(need: dict) -> str:
    return str(need.get("reason") or need.get("guidance") or "the run stopped").strip()


def gate_key(view: dict, changeset: dict | None = None) -> str:
    """A stable identity for the current gate, so an unchanged stop is relayed once. A plan gate in a
    change set also changes when the set becomes approvable (its set token); a set that is still
    waiting, or could not be read, keeps the plain plan key."""
    if view.get("done"):
        return "done"
    need = view.get("needs") or {}
    kind = need.get("kind") or "working"
    if kind == "answer":
        if need.get("route"):
            route = need["route"]
            return f"route:{route.get('question_id')}:{route.get('stopped_model')}"
        ids = ",".join(str(row.get("id")) for row in need.get("questions") or ())
        scope = need.get("resolver_scope") or ""
        return (
            f"answer:{need.get('resolver_request_id')}:{scope}:{ids}"
            if need.get("resolver_token")
            else f"unpublished:{ids}"
        )
    set_token = ((changeset or {}).get("approval") or {}).get("set_token")
    if kind == "approve_plan" and set_token:
        return f"{kind}:{need.get('token')}:set:{set_token}"
    if kind in ("approve_plan", "review"):
        return f"{kind}:{need.get('token')}"
    return f"{kind}:{_reason(need)}:{need.get('job_retry_token') or ''}"


def plan_markdown(plan: dict) -> str:
    """The complete plan a token approves, as the person must be able to read it."""
    lines = ["**Plan for approval** (reply `approve` to accept, or reply with feedback to change it)", ""]
    for row in plan.get("acceptance_criteria") or []:
        review = " (human review)" if row.get("human_review") else ""
        lines.append(f"- **{row['id']}**{review}: {row['criterion']}")
        lines.append(f"  - checked by: {row['verification_method']}")
    for title, rows in (
        ("Constraints", plan.get("constraints")),
        ("Permission boundaries", plan.get("permission_boundaries")),
    ):
        if rows:
            lines += ["", f"**{title}**", *(f"- {row}" for row in rows)]
    delivery = plan.get("git_delivery")
    if isinstance(delivery, dict) and delivery:
        lines += ["", "**Delivery**", f"- branch: {delivery.get('branch') or 'the task worktree branch'}"]
    return "\n".join(lines)


def changeset_markdown(summary: dict, view: dict) -> str:
    """A plan gate's change-set note: the other runs and, once the set approval is named, their plans."""
    here = str(view.get("run_dir") or "")
    runs = [row for row in summary.get("runs") or [] if isinstance(row, dict)]
    others = [row for row in runs if str(row.get("run_dir")) != here] or runs[1:]
    names = ", ".join(f"{row.get('target')} ({row.get('status') or 'starting'})" for row in others)
    approval = summary.get("approval") or {}
    if not approval.get("next_command"):
        waiting = ", ".join(approval.get("waiting_on") or []) or "the other runs"
        return (
            f"**This run is one of change set {summary.get('changeset')}**, with {names}. Once every run waits "
            f"with a complete plan (waiting on: {waiting}), reply `{APPROVE_SET}` to approve them together. "
            "`approve` releases this run alone."
        )
    lines = [
        f"**This run is one of change set {summary.get('changeset')}**, with {names}. Reply `{APPROVE_SET}` to "
        "approve the plan above and each plan below together; `approve` releases this run alone."
    ]
    for row in others:
        body = plan_markdown(row.get("plan") or {}).split("\n", 2)
        lines += ["", f"**{row.get('target')}'s plan**", *(body[2:] if len(body) > 2 else [])]
    return "\n".join(lines)


def questions_markdown(need: dict) -> str:
    """The questions of one answer gate, with the ids and options a reply names."""
    questions = need.get("questions") or []
    lines = ["**AutoCode has questions for you**", ""]
    for row in questions:
        lines.append(f"- **[{row.get('id')}]** {row.get('question')}")
        if row.get("why"):
            lines.append(f"  - why: {row['why']}")
        if row.get("options"):
            lines.append(f"  - options: {', '.join(str(option) for option in row['options'])}")
        if row.get("proposed_default"):
            lines.append(f"  - proposed: {row['proposed_default']}")
    lines += [""]
    if len(questions) == 1:
        lines.append(
            "Reply with the answer" + (" (one of the options above)" if questions[0].get("options") else "") + "."
        )
    else:
        lines.append("Reply with one line per question as `ID: your answer`.")
    return "\n".join(lines)


def route_markdown(need: dict) -> str:
    route = need.get("route") or {}
    lines = [
        f"**AutoCode stopped: {route.get('role')} model {route.get('stopped_model')} ({route.get('cause')})**",
        "",
        "Reply `model NAME` to continue on another model"
        + (f", for example `model {route['candidates'][0]}`" if route.get("candidates") else "")
        + ".",
    ]
    if route.get("candidates"):
        lines.append("Candidates: " + ", ".join(f"`{candidate}`" for candidate in route["candidates"]))
    return "\n".join(lines)


def _same_plan_in_unread_set(view: dict, shown: dict) -> bool:
    """A linked run's plan gate whose set could not be read this poll, already relayed for this plan."""
    need = view.get("needs") or {}
    if need.get("kind") != "approve_plan" or not (view.get("scope") or {}).get("linked"):
        return False
    key = f"approve_plan:{need.get('token')}"
    shown_key = str(shown.get("key") or "")
    return shown_key == key or shown_key.startswith(key + ":set:")


def relay(view: dict, record: dict, changeset: dict | None = None) -> RelayResult:
    """The channel messages this view calls for, and the gate state to remember. ``changeset`` is
    ``autocode changeset status --json`` for a run in a change set (owner decision 2026-10-25)."""
    result = RelayResult(gate={"key": gate_key(view, changeset)})
    shown = record.get("gate") or {}
    if shown.get("key") == result.gate["key"]:
        return result  # an unchanged stop was already relayed
    if changeset is None and _same_plan_in_unread_set(view, shown):
        return result  # a set that could not be read this poll is not a new gate; the shown one stands
    if view.get("done"):
        outcome = (view.get("evidence") or {}).get("outcome") or view.get("status") or "complete"
        result.messages = [f"**Task complete** ({outcome}). Preparing the pull request with the run's evidence."]
        return result
    need = view.get("needs") or {}
    kind = need.get("kind")
    if kind == "answer" and need.get("route"):
        result.messages = [route_markdown(need)]
    elif kind == "answer" and need.get("resolver_scope") in PERSON_ONLY_SCOPES:
        result.messages = [
            f"**AutoCode is blocked and needs a person**: {_reason(need)}\n\n"
            "Reply with the information or correction to pass to the Resolver. This stop is never resolved automatically."
        ]
    elif kind == "answer":
        questions = need.get("questions") or []
        if questions and not need.get("resolver_token"):
            result.publish_questions = True  # advance the run so it publishes the request a reply can answer
        elif questions:
            result.messages = [questions_markdown(need)]
            result.gate["request_id"] = need.get("resolver_request_id")
        # questions without a request and without a token relaunch; the next view relays them
    elif kind == "approve_plan":
        plan = actions.plan_for_approval(view)
        if plan is None:
            result.messages = [
                "**AutoCode is waiting for plan approval**, but the view does not carry the complete plan.\n\n"
                "The bridge will not ask a person to approve terms it cannot show. This is a gap to report "
                "against the CLI or the status view; the run waits untouched."
            ]
            result.gate["showable"] = False
        else:
            result.gate["token"] = actions.approval_token(view)
            result.messages = [plan_markdown(plan)]
            if changeset:
                result.messages.append(changeset_markdown(changeset, view))
                result.gate["set_token"] = (changeset.get("approval") or {}).get("set_token")
    elif kind == "review":
        criteria = actions.review_criteria(view)
        if criteria is None:
            result.messages = [
                "**AutoCode is waiting for a review decision**, but the criteria cannot be shown completely.\n\n"
                "This is a gap to report; the run waits untouched."
            ]
            result.gate["showable"] = False
        else:
            result.gate["token"] = actions.approval_token(view)
            listed = ", ".join(row["id"] for row in criteria)
            result.messages = [
                f"**AutoCode asks you to accept criteria**: {listed}\n\n"
                + "\n".join(f"- **{row['id']}**: {row['criterion']}" for row in criteria)
                + "\n\nReply `approve` to accept, or reply with a correction (it becomes review feedback)."
            ]
    elif kind in actions.PERSON_ONLY_KINDS:
        if need.get("new_run_required"):
            result.messages = [
                f"**The run stopped and cannot be repaired in place**: {_reason(need)}\n\n"
                + str(need.get("recovery_hint") or "Inspect the retained run; a new run is needed.")
            ]
            result.gate["terminal"] = True
        elif need.get("edit_required"):
            result.messages = [
                f"**The plan needs correction before the run can continue**: {_reason(need)}\n\n"
                "Reply with the correction; it goes to the Requirements Gatherer as feedback."
            ]
        else:
            result.messages = [
                f"**The run paused**: {_reason(need)}\n\nReply `resume` to continue it once the cause is resolved."
            ]
    elif kind:
        # Any other stop a person steers with words (a planning budget, for example): plain text is feedback.
        result.messages = [
            f"**AutoCode stopped**: {_reason(need)}\n\nReply with direction for the run; it reaches the planning stage as feedback."
        ]
    return result


def validate_answer(question: dict, text: str) -> str | None:
    """Why this text cannot answer this question, or None. Options match exactly apart from spaces."""
    options = [str(option) for option in question.get("options") or ()]
    if not options:
        return None
    wanted = text.strip()
    allowed = options + ([str(question["proposed_default"])] if question.get("proposed_default") else [])
    return None if wanted in allowed else f"[{question.get('id')}] accepts only: {', '.join(allowed)}"


def parse_answers(questions: list[dict], text: str) -> tuple[dict[str, str], str | None]:
    """A reply's answers for the open questions, or the reason it cannot be read."""
    if len(questions) == 1:
        error = validate_answer(questions[0], text)
        return ({questions[0]["id"]: text.strip()}, error) if error is None else ({}, error)
    answers = {}
    for line in (part.strip() for part in text.splitlines()):
        if not line:
            continue
        match = ANSWER_LINE.match(line)
        if not match:
            return {}, "Reply with one line per open question, as `ID: your answer`."
        answers[match.group("id")] = match.group("text").strip()
    wanted = {str(row.get("id")): row for row in questions}
    unknown = [key for key in answers if key not in wanted]
    if unknown:
        return {}, f"No open question {', '.join(sorted(unknown))}; open questions: {', '.join(sorted(wanted))}."
    missing = [key for key in wanted if key not in answers]
    if missing:
        return {}, f"Missing answers for {', '.join(sorted(missing))}."
    for key, value in answers.items():
        error = validate_answer(wanted[key], value)
        if error:
            return {}, error
    return answers, None


def interpret(record: dict, view: dict, text: str) -> ReplyAction:
    """Reduce one channel reply to the action it names, against the gate the person saw."""
    text = text.strip()
    gate = record.get("gate") or {}
    need = view.get("needs") or {}
    if view.get("done"):
        return ReplyAction("follow_up", text=text)
    kind = need.get("kind")
    if kind == "approve_plan":
        if " ".join(text.lower().split()) == APPROVE_SET:
            return ReplyAction("approve_set", shown_token=str(gate.get("set_token") or ""))
        if text.lower() in APPROVE_WORDS:
            return ReplyAction("approve", shown_token=str(gate.get("token") or ""))
        return ReplyAction("feedback", text=text)
    if kind == "review":
        if text.lower() in APPROVE_WORDS:
            return ReplyAction("review", shown_token=str(gate.get("token") or ""))
        return ReplyAction("feedback", text=text)
    if kind == "answer" and need.get("route"):
        match = MODEL_PREFIX.match(text)
        candidates = [str(row) for row in (need["route"].get("candidates") or ())]
        model = match.group(1).strip() if match else (text.strip() if text.strip() in candidates else "")
        if model:
            return ReplyAction("model", model=model)
        return ReplyAction(
            "none",
            text=f"Reply `model NAME` to continue{'; candidates: ' + ', '.join(candidates) if candidates else ''}.",
        )
    if kind == "answer" and need.get("resolver_scope") in PERSON_ONLY_SCOPES:
        return ReplyAction("operational", text=text)
    if kind == "answer":
        answers, error = parse_answers(need.get("questions") or [], text)
        if error:
            return ReplyAction("none", text=error)
        return ReplyAction("answers", answers=answers)
    if kind == "retry_job" and not need.get("route"):
        if text.lower() in RESUME_WORDS:
            return ReplyAction("retry")
        return ReplyAction(
            "none", text="Reply `retry` to retry the stopped job, or comment `pause` and inspect the run."
        )
    if kind in actions.PERSON_ONLY_KINDS:
        if need.get("edit_required") and text:
            return ReplyAction("feedback", text=text)
        if text.lower() in RESUME_WORDS and not need.get("new_run_required"):
            return ReplyAction("resume")
        if need.get("new_run_required"):
            return ReplyAction("none", text="This stop cannot be resumed; start a fresh issue instead.")
        return ReplyAction("none", text="Reply `resume` to continue the run.")
    return (
        ReplyAction("feedback", text=text) if text else ReplyAction("none", text="Nothing to do on this run right now.")
    )

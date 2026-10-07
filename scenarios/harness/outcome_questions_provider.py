"""Script the fault "outcome_questions" (design-alerting-outcomes, issue #450): only the model is fake.

The scripted Requirements and Planner do what their prompts tell them, and nothing more. With AutoCode's
Requirements rule in its prompt (``REQUIREMENTS_HEADING``) the Requirements stage asks about outcomes and
constraints: what counts as a problem and how soon the team must hear, which channel and whether an existing
integration or restriction binds, and what an alert may contain. Without it, it asks what a live run asked
on 2026-10-03, naming three Slack integration mechanisms. With the Planner rule (``PLANNER_HEADING``) the
Planner recommends mechanisms with their tradeoffs as its own proposal, keeps the reliability promises the
person's rules imply (an uncertain delivery, a delivery deadline) as blocking questions until the person
decides them, and says that approving the plan deploys nothing. Without it, it asks which integration to use
although the person said they have no preference, and records the answer as the person's decision.

So the scenario's oracle, which reads the questions the driver answered and the approved plan, fails when
either rule stops reaching its stage. It proves that the rules reach the stages and that the runner keeps the
questions as blockers until the person answers them and asks for exact plan approval; it says nothing about
how well a real model follows the rules. tests/test_outcome_questions.py checks that these headings are
AutoCode's (scenarios/ may not import tools/).
"""
from __future__ import annotations

import copy

REQUIREMENTS_HEADING = 'ASK ABOUT OUTCOMES AND CONSTRAINTS.'
PLANNER_HEADING = 'RECOMMEND MECHANISMS.'
REVIEWER_HEADING = 'CHECK OUTCOME QUESTIONS.'  # the Plan Reviewer's; the scripted reviewer raises no concern


def question(qid, text, why, *, options=(), default="", category="requested_outcome"):
    return {"id": qid, "question": text, "why": why, "options": list(options), "proposed_default": default,
            "kind": "decision", "category": category, "delegable": False}


OUTCOME_QUESTIONS = [
    question("Q1", "What should count as a problem worth an alert (any message in the dead-letter queue, a number "
                   "of messages, or a message older than some age), and how soon after that must the team hear "
                   "about it?",
             "The alert rule decides what is watched; neither the request nor the empty workspace says it.",
             default="Alert when any message has waited in the dead-letter queue for 5 minutes; the team hears "
                     "within 10 minutes."),
    question("Q2", "Which Slack channel should receive the alerts, and must we use an existing Slack integration or "
                   "follow an organizational restriction (approved services, accounts, who may install apps)? "
                   "With no restriction, the Planner recommends how alerts reach Slack.",
             "Where alerts go is yours to say; how they get there is ours to recommend unless a restriction binds it.",
             default="#orders-oncall, with no existing integration and no restriction."),
    question("Q3", "What may an alert contain? Dead-lettered orders can hold customer data: may any of a message's "
                   "contents leave AWS in a Slack alert?",
             "Privacy decides what an alert can show and so how it is built.",
             default="Counts and message IDs only, never message bodies."),
]
# What the Requirements stage of the live run asked instead of Q2 (issue #450).
MECHANISM_QUESTION = question(
    "Q2", "Which Slack destination and integration: incoming webhook, Slack app with chat:write, or AWS Chatbot?",
    "The design needs a delivery mechanism.",
    options=["Incoming webhook", "Slack app with chat:write", "AWS Chatbot", "No preference"], default="No preference")
RELIABILITY_QUESTIONS = [
    question("Q4", "Slack can fail or time out after it has already posted an alert, so the sender cannot always "
                   "tell whether an alert arrived. When that happens, should it send the alert again (the team "
                   "may see it twice) or not (the team may miss it)? Never a repeated alert and never a missed "
                   "alert cannot both be promised.",
             "Your rule asks for one alert per incident; an uncertain delivery result forces a choice between a "
             "duplicate and a miss.",
             options=["Send it again: a duplicate is better than a missed alert",
                      "Do not send it again: a missed alert is better than a duplicate"],
             default="Send it again: a duplicate is better than a missed alert"),
    question("Q5", "Is the delivery time you gave a target while AWS and Slack are healthy, or a guarantee that must "
                   "also hold while AWS or Slack is having an outage?",
             "No design can meet a delivery deadline while Slack itself is down; a guarantee would need another "
             "channel and changes the design.",
             options=["A target while AWS and Slack are healthy", "A guarantee, even during an outage"],
             default="A target while AWS and Slack are healthy"),
]
# What a Planner without the rule asks once the person has said they have no preference.
REASKED_MECHANISM = question(
    "Q4", "Which integration should deliver the alerts to Slack: an incoming webhook, a Slack app with chat:write, "
          "or AWS Chatbot?",
    "The design needs a delivery mechanism.",
    options=["Incoming webhook", "Slack app with chat:write", "AWS Chatbot"], default="AWS Chatbot",
    category="technical")

RECOMMENDED_APPROACH = [
    "Detect: options are a CloudWatch alarm on the queue's ApproximateAgeOfOldestMessage (no code to run, measures "
    "message age directly, one-minute granularity) or a scheduled Lambda that polls the queue (any rule, but code "
    "to deploy and operate). Recommended: the CloudWatch alarm, because the person's rule is about message age.",
    "Deliver to Slack: options are an incoming webhook (simplest, one channel, a secret URL to store), a Slack app "
    "with chat:write (any invited channel, needs a workspace admin and a token) or AWS Chatbot (managed, no code, "
    "needs a workspace admin, fixed format). Recommended: AWS Chatbot, since the person has no existing "
    "integration and no restriction and it needs no code to operate.",
    "Write the design to design/alerting.json; nothing is deployed.",
]
RECOMMENDATIONS = [
    {"text": "Recommendation, not a user decision: detect the condition with a CloudWatch alarm on "
             "ApproximateAgeOfOldestMessage.", "basis": "agent_proposed", "answer_id": ""},
    {"text": "Recommendation, not a user decision: deliver alerts to Slack through AWS Chatbot.",
     "basis": "agent_proposed", "answer_id": ""},
]
NO_DEPLOYMENT = ("No deployment: no AWS or Slack calls and no infrastructure created or changed; approving this "
                 "plan authorizes only writing the design document")


def answered(data) -> set:
    return set((data.get("saved_answers") or {}).keys())


def requirements_questions(prompt: str) -> list[dict]:
    rows = copy.deepcopy(OUTCOME_QUESTIONS)
    if REQUIREMENTS_HEADING not in prompt:
        rows[1] = copy.deepcopy(MECHANISM_QUESTION)
    return rows


def planner_questions(prompt: str) -> list[dict]:
    return copy.deepcopy(RELIABILITY_QUESTIONS if PLANNER_HEADING in prompt else [REASKED_MECHANISM])


def open_questions(data, prompt: str) -> list[dict]:
    """The blocking questions a Planner draft carries: the handoff's unanswered ones, then, once those are
    answered, its own until the person answers them too."""
    done = answered(data)
    handoff = ((data.get("requirements_handoff") or {}).get("report") or {}).get("open_questions") or []
    still = [row for row in handoff if row["id"] not in done]
    if still:
        return still
    return [row for row in planner_questions(prompt) if row["id"] not in done]


def report_for(stage: str, data: dict, prompt: str, report: dict) -> dict:
    """The scripted report: the fake's ordinary one with this scenario's questions and plan."""
    report = copy.deepcopy(report)
    if stage == "requirements_gather":
        report["open_questions"] = [row for row in requirements_questions(prompt) if row["id"] not in answered(data)]
        return report
    if "contract" not in report:
        return report
    body = report["contract"]
    questions = open_questions(data, prompt)
    body["open_blocking_questions"] = questions
    if questions:
        # Clarification only: no approach, milestones or first task until the person answers.
        body["technical_approach"], body["milestones"] = [], []
        body["initial_task"] = {"kind": "none", "milestone_id": "", "objective": "", "affected_paths": [],
                                "requirements": [], "acceptance_criteria": [], "validation_plan": []}
        return report
    done = answered(data)
    users = [{"text": f"The person's answer to {qid}", "basis": "user_answer", "answer_id": qid}
             for qid in sorted(done)]
    if PLANNER_HEADING in prompt:
        body["technical_approach"] = list(RECOMMENDED_APPROACH)
        body["accepted_assumptions"] = [*copy.deepcopy(RECOMMENDATIONS), *users]
        body["permission_boundaries"] = [*body.get("permission_boundaries", []), NO_DEPLOYMENT]
    else:
        # Without the rule the re-asked mechanism stands as the person's decision, and nothing says that the
        # plan deploys nothing.
        body["technical_approach"] = ["Deliver to Slack through the integration the person chose (Q4)."]
        body["accepted_assumptions"] = [*users, {
            "text": "Alerts reach Slack through the integration the person chose in Q4 (an incoming webhook, a "
                    "Slack app with chat:write or AWS Chatbot)", "basis": "user_answer", "answer_id": "Q4"}]
    return report

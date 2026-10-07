"""Ask a person about outcomes and constraints; recommend the mechanisms yourself (issue #450).

A live design run asked the user "Which Slack destination and integration: incoming webhook, Slack
app with chat:write, or AWS Chatbot?". The destination was theirs to say; the mechanism was the
Planner's to recommend. These rules tell each planning job its part:

- Requirements (``REQUIREMENTS_RULE``) asks about outcomes, thresholds, deadlines, destinations,
  privacy and organizational restrictions, after reading the repository's conventions; it asks
  whether a mechanism is a binding constraint rather than asking which mechanism to use, keeps
  parameterizable identities (a channel, an account) out of the blocking questions, and raises
  reliability constraints (an uncertain delivery, a latency promise) early.
- The Planner (``PLANNER_RULE``) recommends mechanisms with their tradeoffs, records the choice as
  its own proposal (never a user decision, never permission to deploy), and keeps missing
  consequential facts and unsupported guarantees as blocking questions.
- The Plan Reviewer (``REVIEWER_RULE``) checks both.

The Builder, the Tester and the completion stages get none of them: by then the plan is approved.
Each rule starts with its own heading, so a reader of a prompt (the scenario harness's scripted
model, ``scenarios/harness/outcome_questions_provider.py``) can tell which rule reached a stage.

Imports nothing from AutoCode; the planning unit and the stage context append ``rule(stage)``.
"""
from __future__ import annotations

REQUIREMENTS_HEADING = "ASK ABOUT OUTCOMES AND CONSTRAINTS."
PLANNER_HEADING = "RECOMMEND MECHANISMS."
REVIEWER_HEADING = "CHECK OUTCOME QUESTIONS."

REQUIREMENTS_RULE = "\n" + REQUIREMENTS_HEADING + """ Inspect the accessible repository conventions first
(existing integrations, configuration, deployment and alerting code, documentation) and cite what you
read; ask only what they do not settle. Ask the person about outcomes and constraints: what must happen
and when (conditions, thresholds, deadlines), where results go (a destination such as a channel, team or
mailbox), privacy and data-handling limits, and organizational restrictions (approved services, an
existing integration that must be used, accounts, regions, budgets). Do not ask the person to choose an
API, service, library or integration mechanism, and do not offer mechanisms as answer options, unless a
binding constraint makes that choice theirs. Ask whether such a constraint exists instead, for example
"Which channel should receive alerts, and must we use an existing integration?". Without a restriction,
the Planner recommends the mechanism. A mechanism the person named is theirs: keep it as stated.
Separate parameterizable identities from true blockers. A channel, account, region, queue, table or other
resource name that a provisional design can take as a named configuration parameter is not a blocking
question. Block only on facts that change the outcome or the design (what triggers an alert, which records
matter, what data may leave the organization).
Surface reliability constraints early, as decision questions: what "no repeated alert" means after an
uncertain delivery result (retrying may send a duplicate, not retrying may miss one), and whether a latency
or delivery requirement is a healthy-path target or an unconditional guarantee. A guarantee the design
cannot support stays a blocking question until the person accepts a weaker promise.
Keep assumptions explicit: a proposed default or a recommendation is never the person's answer.
"""

PLANNER_RULE = "\n" + PLANNER_HEADING + """ Inspect the accessible repository conventions before choosing a
mechanism, and recommend technical mechanisms yourself instead of asking the person to pick one: in
technical_approach name the viable options with their tradeoffs (setup, permissions, cost, reliability,
operations) and your recommendation with its reason, and record that choice as an accepted_assumptions row
with basis=agent_proposed. Ask the person to choose a mechanism only when it is a binding constraint (an
approved-services policy, an existing integration that must be used, a privacy or account restriction);
a mechanism the person named or a constraint fixes is not yours to replace. When the person has given
outcome rules and no integration preference, do not ask again which API, service or integration to use.
Any question you add asks about outcomes and constraints, never about a mechanism.
A recommendation is never a user decision (never basis user_answer or delegated) and never permission to
deploy, provision infrastructure, call an external service or spend: producing a plan or a design document
authorizes none of that. Whenever the work names external systems the person has not explicitly asked you
to change, say so in constraints (for example "Approving this plan deploys nothing and calls no external
service"); do not widen permission_boundaries.
Keep parameterizable identities (channel, account, region, resource names) as named configuration
parameters, not blocking questions. Keep missing consequential facts and unsupported delivery or latency
guarantees in open_blocking_questions until the person decides them. State each latency requirement as a
healthy-path target or an unconditional guarantee, and never promise exactly-once delivery, or no repeated
alert after an uncertain delivery result, without the person's decision.
"""

REVIEWER_RULE = "\n" + REVIEWER_HEADING + """ Raise a concern (at final review, correct the contract) when
the plan asks the person to choose an API, service or integration mechanism without a binding constraint,
records a mechanism recommendation as a user decision, treats a plan or a design document as permission to
deploy or to call external services, blocks on a parameterizable identity, or drops a missing consequential
fact or an unsupported delivery or latency guarantee from open_blocking_questions.
"""

# Stage code names (AGENTS.md "Names"), today's joint pipeline and the v2 planning flow alike.
REQUIREMENTS_STAGES = ("requirements_gather", "requirements")
PLANNER_STAGES = ("astra_discovery", "glm_revise", "plan", "plan_revise")
REVIEWER_STAGES = ("astra_challenge", "astra_finalize", "plan_review", "plan_finalize")


def rule(stage: str, *, joint: bool = True) -> str:
    """The outcome-question rule for a planning stage, or "" for every other stage.

    Without joint planning ``astra_discovery`` is one stage that gathers requirements and drafts the
    plan (autocode_goals.DISCOVERY_PROMPT), so it gets both the Requirements and the Planner rule."""
    if stage == "astra_discovery" and not joint:
        return REQUIREMENTS_RULE + PLANNER_RULE
    if stage in REQUIREMENTS_STAGES:
        return REQUIREMENTS_RULE
    if stage in PLANNER_STAGES:
        return PLANNER_RULE
    if stage in REVIEWER_STAGES:
        return REVIEWER_RULE
    return ""

# Requirements asked the user to pick a Slack integration mechanism (#450)

**Seen:** run `20261003-015741-design-an-aws-solution-we-want-a-dlq-to-be-monit-78a7323a` (2026-10-03), a
design-only request, in an empty workspace, to monitor an AWS dead-letter queue and selected DynamoDB rows with
Slack alerts. Requirements rightly blocked on the alert conditions and the row predicate, but its third question
was "Which Slack destination and integration: incoming webhook, Slack app with chat:write, or AWS Chatbot?". The
destination was the user's to give; the mechanism was the Planner's to recommend. No prompt separated the two:
Requirements was told to ask "genuinely blocking questions", the Planner to propose "alternatives".

**Fix:** `tools/autocode_outcome_questions.py` holds one rule per planning job, each under its own heading.
Requirements (`requirements_gather`, v2 `requirements`) reads the repository's conventions first, then asks
about outcomes, thresholds, deadlines, destinations, privacy and organizational restrictions; it asks whether a
binding constraint exists instead of which mechanism to use, keeps parameterizable identities (a channel, an
account) out of the blocking questions, and raises reliability constraints early (what "no repeated alert"
means after an uncertain delivery, whether a deadline is a healthy-path target or a guarantee). The Planner
(`astra_discovery`, `glm_revise`, v2 `plan`, `plan_revise`) recommends mechanisms with their tradeoffs as an
`agent_proposed` assumption, never a user decision and never permission to deploy, and keeps missing facts and
unsupported guarantees blocking until the user decides them. The Plan Reviewer (`astra_challenge`,
`astra_finalize`, v2 `plan_review`, `plan_finalize`) checks both. Whenever the plan names external systems,
the Planner states what approval does not authorize (every deployment, provisioning step and external call the
user has not explicitly asked for, so a request that does ask for a call or a deployment is not contradicted by
its own plan) in `constraints`, not in `permission_boundaries`: the contract guard
(`autocode_contract_revision`) refuses a boundary that a revision adds without the user's backing, whereas a
constraint may be added and then cannot be dropped without it. `units/autoplanner.context` appends the
rule for the stage; without joint planning `astra_discovery` does both jobs and gets both rules
(`autocode_stage_context`). Execution stages (Builder, Tester, completion, `astra_plan`) get none.

**Coverage:** `tests/test_outcome_questions.py` (which prompt gets which rule, and that execution prompts get
none). The scenario `design-alerting-outcomes` drives the CLI: its scripted Requirements and Planner follow the
rules only when the rules are in their prompts, and its oracle reads the questions asked and the approved plan
without requiring any one AWS architecture. Run against the tools from before this change it was
`FALSE_COMPLETE` on seven checks (it asked the 2026-10-03 question, re-asked the integration after "no
preference", never put the uncertain-delivery and deadline promises to the user, and approved a plan with the
mechanism as the user's decision and no word on deployment); with the change it passes all 19.
`scenarios/test_harness.py` runs it and checks the saved prompt of every stage. The oracle accepts any named
mechanisms (AWS services, Slack's, or third-party monitors) and the usual wordings of the uncertain-delivery
question (sent again, retried or duplicated versus missed, lost or dropped).

**Not covered:** whether real models follow the rules (no live run was authorized). The Designer
(`review_design`) prompt is unchanged: a request for a new design goes on to this pipeline, which has the rules.

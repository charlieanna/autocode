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
means after an uncertain delivery, whether a deadline is a healthy-path target or a guarantee). The stages
that draft and revise the plan (`astra_discovery`, `glm_revise`, v2 `plan`, `plan_revise`; their prompts call
them the Planner, while the screen names `astra_discovery` and `glm_revise` Requirements) recommend mechanisms
with their tradeoffs as an `agent_proposed` assumption, never a user decision and never permission to deploy,
and keep missing facts and unsupported guarantees blocking until the user decides them. The Plan Reviewer
(`astra_challenge`, `astra_finalize`, v2 `plan_review`, `plan_finalize`) checks both. Whenever the plan names
external systems,
the plan states what approval does not authorize (every deployment, provisioning step and external call the
user has not explicitly asked for, so a request that does ask for a call or a deployment is not contradicted by
its own plan) in `constraints`, not in `permission_boundaries`: the contract guard
(`autocode_contract_revision`) refuses a boundary that a revision adds without the user's backing, whereas a
constraint may be added and then cannot be dropped without it. `units/autoplanner.context` appends the
rule for the stage; without joint planning `astra_discovery` does both jobs and gets both rules
(`autocode_stage_context`). The stages after approval (Builder, Tester, completion, and `astra_plan`, the
build unit's milestone planner that the screen calls Planner) get none.

**Coverage:** `tests/test_outcome_questions.py` (which prompt gets which rule, and that execution prompts get
none). The scenario `design-alerting-outcomes` drives the CLI: its scripted Requirements and Planner follow the
rules only when the rules are in their prompts, and its oracle reads the questions asked and the approved plan
without requiring any one AWS architecture. Run against the tools from before this change it was
`FALSE_COMPLETE` on seven checks (it asked the 2026-10-03 question, re-asked the integration after "no
preference", never put the uncertain-delivery and deadline promises to the user, and approved a plan with the
mechanism as the user's decision and no word on deployment); with the change it passes all 19.
`scenarios/test_harness.py` runs it and checks the saved prompt of every stage. The oracle accepts any named
mechanisms (AWS services, Slack's, or third-party monitors) and the usual wordings of the uncertain-delivery
question (sent again, retried or duplicated versus missed, lost or dropped). After review it also fails a
design or plan that records any mechanism the person never named as their decision, binding constraint or
answer (named or not: "the integration the person picked"); a delivery promise opposite to the side the person
chose; a plan or design note that grants deployment in any wording ("authorizes deploying", "lets the Builder
deploy"), or whose only no-deployment line is not about approval (the brief no longer says "do not deploy", so
that line must be the plan's); and a question that puts a mechanism to the person anywhere in the batch,
whether it names one ("through AWS Chatbot?") or only asks for one ("which Slack API?"). A question that asks
whether an existing integration or a restriction binds may name mechanisms as examples. Seven broken designs
and `OutcomeQuestionsOracleTests` hold these cases.

**Live run (2026-10-07):** with every stage on a live model, Requirements asked only about outcomes, the
destination and reliability (no mechanism question), and the approved plan recorded its CloudWatch, SNS and
Lambda recommendation as `agent_proposed` with "Approving this plan deploys nothing". The oracle still scored it
`FALSE_COMPLETE` 15/19 through four false negatives: the trigger question asked what counts as "something goes
wrong" without the word "alert"; no answer said "no preference" (live, each answer is the model's own default),
which the re-ask check required; the Builder listed the account, region, queue ARN and channel in
`open_blockers` while declaring each a parameter, as the approved plan asked, both reading the brief's
`open_blockers` (facts missing before deployment) literally; and the assumptions restated each recommendation
in other words than the option's name. Now
the trigger question may say that something goes wrong or what should page someone instead of "alert" (and no
one word, such as "threshold", counts as both the trigger and the condition); an answer to a question about
mechanisms that no constraint binds them ("no existing integration constraint") counts as "no preference", and
without such an answer only a question that puts a mechanism to the person counts as re-asked; the brief defines
`open_blockers` as missing facts "that no parameter can stand in for", and the oracle holds an identity in
`open_blockers` against the design only when no parameter's name declares it (a description counts only for a
parameter whose name names no identity, so an identity mentioned in passing is not declared), so a run under the
old wording is judged on whether the identity was taken as configuration, not on how it read a field name; and a
recommendation counts as stated when one statement of a proposed assumption quotes it or names one of its
mechanisms that not every option of that choice names, unless the statement denies the mechanisms ("no
CloudWatch alarm, SNS topic or Lambda function is created") or also names one that only the choice's other
options name (a list of the options without the pick). A mechanism another recommendation names is not counted
as an alternative, so one statement may put several recommendations forward, as the live plan's did. The run's
questions, answers, approved plan and design are kept in the scenario's `tests/live-2026-10-07.json`;
`OutcomeQuestionsOracleTests` scores them (all 19 checks pass) and, for each of the four checks, copies with the
failures that check exists to catch, including the wordings a review found the first loosening let through. The
seven broken designs fail the same checks as before.

**Second live run (2026-10-07, on the oracle above):** again every stage on a live model, and again the planning
followed the rules: Requirements asked about the trigger, the uncertain-delivery and deadline promises and
whether an existing integration or a restriction binds, with no mechanism put to the person; the plan recorded
its recommendations as `agent_proposed` and said that approving it deploys nothing; nothing was deployed. The
oracle scored it `FALSE_COMPLETE` 16/19 through three more false negatives, all in the document checks: the person
said "empty to non-empty", "above zero" and "when it empties", and the design's user rules wrote "goes from 0 to
>0", which the rule check read as a number the person never gave; `reliability.delivery` said it retries and
accepts a duplicate, then "Exactly-once delivery is not guaranteed", which the delivery check read as a promise of
exactly-once delivery; and an `open_blockers` row, "Verification that Lambda functions can be deployed with
sufficient IAM permissions ...", read as a grant because of "can ... deployed". Now numbers written as words, up
to ninety-nine, count in the person's words and in a rule alike ("seven", "fifteen", "twenty-five"), and the person's
"empty to non-empty" and "when it empties" give 0; a passing "none" ("Other constraints: none") gives nothing, and
the brief's own "one option" still makes 1 always the person's. Each mention of exactly-once is read in its own
clause and promises nothing only when a negation denies the promise of it, with nothing but words of promising
between ("no exactly-once guarantee", "no guarantee of exactly-once", "cannot be guaranteed to be exactly-once"),
or follows it to say it is not promised ("is not guaranteed", "cannot be promised"); a negation of something else
("Retries never break exactly-once delivery", "without duplicates thanks to exactly-once delivery", "We do not
compromise on exactly-once") leaves the promise standing, and so does a promise in another clause. A clause that
asks to verify, check or confirm that or whether something can be deployed states a fact to check, not a
permission, but only when the check opens the clause (or follows a comma), led at most by who must do it ("The
team must verify") or what still waits on it ("Deployment requires verification", "It is unclear whether"; never
approval or a permitting word), and governs the permitting word directly: at most six words between, none of them a word that starts another clause
("then", "so", "after which", "once", "or"), and no comma or such word between the permitting word and "deploy".
So a check inside a declaration or a condition still grants ("Approving this design is confirmation that the team
may deploy", "Once you confirm that the secret exists you may deploy", "Approval will establish that the Builder
can provision", "We confirm that the team may deploy"), as do "Verify that IAM is in place then the Builder may
deploy", "Verify that you can deploy, then deploy", "Whether the review passes or fails the team may deploy", a
check reported done ("Confirmation that the team may deploy has been received") and "Once the IAM permissions are
verified, you may deploy", in the note, in a blocker and in the approved plan alike. The run is kept as
`tests/live-2026-10-07-b.json`; `OutcomeQuestionsOracleTests` scores it (all 19 checks pass) and, for each of the
three checks, copies that should fail and do: an invented threshold in digits or words (15, a small one, 2, or
"seven"), a 0 once the person's answer has no word for it, a design that does promise exactly-once delivery
(including the wordings above), and a note, blocker or plan line that really grants deployment (including those
above). Re-scored offline with this oracle, both live runs pass 19/19; the seven broken designs fail the same
checks as before.

**Not covered:** the person's premise live: the person's answers are `[fake] answers`, so live the driver
answers with the model's own defaults, and the "no preference" order rule applies only when a default says so.
The checks, on the document and on the process alike, read the model's wording with patterns (regular
expressions), not its meaning. Each live run so far surfaced wording they misread, and a future live run may
still: read a live verdict before citing it, and keep a run the oracle misjudged as a fixture beside these two. A
recommended option that names no listed mechanism needs only some proposed assumption. The rule check pools the
person's numbers, so a rule that rewrites their 5 minutes as 10 passes when they gave 10 for something else, and
the brief's "one option" makes an invented 1 theirs. "Exactly-once delivery is not guaranteed by Slack, so the
dedupe key provides it" promises it only through "it", which the delivery check cannot follow. The deployment
check splits a clause at "and", so a check of two things ("Verify that the alarm and the topic can be deployed")
reads as a grant of the second; and a negation anywhere earlier in a clause reads as denying its deployment, so
"Without waiting for review, the team may deploy" or "Whether or not review passes, the team may deploy" is not
read as a grant. The Designer (`review_design`) prompt is unchanged: a request for a new design goes on to
this pipeline, which has the rules.

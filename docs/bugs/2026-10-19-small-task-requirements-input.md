# Small-task Requirements input and timing (#1630)

The retained Flash attempt did not produce a final Requirements report or plan.
Its cause remains unresolved. The safe change from this investigation compacts
ordinary joint-planning JSON handoffs; it does not establish that planning will
finish sooner or complete the queue task.

The incident is retained in the #1528 qualification bundle with manifest SHA-256
`db72716780589cf8df0f65dadbb8a918eb11f18437f68bc8336bd2f7e9e66437`.
The archived discovery-07 prompt has SHA-256
`e1326bb6ac2e55cad058818a62b14791f475cea20fb539655a49ed71f45d3310`;
its runtime was `0a8acccdc350c377ceacb196ed657f2656101cec`. Measurements below
read the preserved prompt and public event metadata without modifying that run.

## What the timing shows

The attempt lasted 900.505 seconds and stopped at its approved stage deadline.
Its first native step-start timestamp is 14.980 seconds after the attempt start;
the first completed tool timestamp is 342.273 seconds after the start. Seven
completed tools report a total of **0.633 seconds** of execution (six reads and
one shell command). Four native steps began; three ended with tool calls and the
last did not return a final report. The known completed requests report 26,643
output tokens, including 26,250 reasoning tokens. One request remains unpriced
and incomplete, so these counters do not establish complete usage or cost.

The reported tool durations account for little of the elapsed time. The gaps
between tool execution and reports do not separate remote model work, network
latency, local transport or report assembly. Progress events show activity, not
health or success. These timing artifacts alone establish neither a deadlock nor
a causal latency explanation.

## What changed and what it saves

| Archived input portion | Bytes |
| --- | ---: |
| Stage/shared instructions | 46,391 |
| Provider output contract and inline schema | 12,386 |
| Original indented handoff | 36,308 |
| Compact handoff with identical parsed values | 33,093 |
| Original complete prompt | 95,131 |
| Complete prompt with compact handoff | 91,916 |

The measured saving is **3,215 bytes (3.38%)**. This is a byte measurement, not a
provider tokenizer count, billed-input reduction or measured latency improvement.
All keys, ordering, exact task/source quotes, saved answers, user events, diagnosis,
commands, schema and instruction bytes remain the same. The existing prompt-token
estimate is recomputed from the resulting bytes. Routing, effort, caps, recovery,
report validation and user approval semantics do not change.

The actual stage is `astra_discovery`: despite the Requirements screen label, it
also drafts a contract and may propose an initial task or progressive plan.
Removing build, proof, progressive, source-binding or approval rules solely because
the project has two Python files would discard applicable obligations. Those
rules remain. The current evidence does not justify a broader policy reduction.

## Verification and remaining work

A subsequent source audit found two report-instruction contradictions. The
shared rejected-assumption policy told Discovery and Revise to return both
`remediation_records` and `obligation_decisions` as empty lists; their schemas
permit only the former. The first Plan Reviewer received that same instruction
although its schema permits only the latter. Its progressive policy also said
the report schema "always includes progressive_proposal" and directed it to
return an empty proposal, although this concern-only schema forbids that field.
Following those return instructions produces a schema-invalid report.

Planner remediation instructions and independent review instructions now follow
their existing stage schemas. The first review checks the saved progressive
proposal and raises concerns rather than generating one. Product-check,
retirement, command and approval rules remain present. No schema or validation
gate changes. V2 stages keep the common rejected-assumption rule without legacy
report-field instructions their schemas do not support.

Three prompt/schema regressions fail on the preceding runtime and pass with the
correction. A negative regression retains rejection of fields owned by another
role. All 48 affected golden renderings were checked against only the intended
policy replacements; planning handoff bytes and unrelated renders are unchanged.
The fake-provider dispatch consumer is mapped for these resources in Ubuntu CI.
These checks establish a consistent report contract, not that a live model will
return a report faster. No retained response establishes that either contradiction
caused discovery-07's timeout, so #1630 remains open for live qualification.

The rendering regression preserves literal Unicode, newlines, quotes, nested
answers and command strings across planning and provider formatting, checks the
prompt metric, and verifies the saved state is unchanged. All 50 affected golden
planning renderings were compared with the original: their instruction bytes and
parsed handoff values match exactly. Unaffected golden renderings remain unchanged.
The existing public fake-provider planning-cycle CLI regression covers planning,
feedback, independent review and the approval boundary in Ubuntu CI.

No provider retry was performed for this change. The separate qualification
session retains ownership of its live trial and its approved routes and limits.
Issue #1630 stays open for actual latency/report-delivery qualification; this
input-size reduction alone does not prove the reported stall fixed.

## Live reproduction on current master (2026-10-21)

A fresh two-file priority-queue repository with an 841-character reprioritization brief was run on
`2bda9561b` through Kilo 7.8.12 with direct provider logins (no Kilo Gateway): Requirements and Planner
on `xiaomi-token-plan-sgp/mimo-v2.6-flash` (medium), Plan Reviewer and Investigator on
`zai-coding-plan/glm-5.3` (high), a 900-second stage deadline and `--no-chat`. The recognizer chose
the bug-fix workflow.

| Stage | Seconds | Result |
| --- | ---: | --- |
| Job recognizer | 12.6 | bugfix |
| Investigator | 331.7 | reproduced; about 57 s were refused `capture` calls |
| Requirements (`astra_discovery`) | 193.3 | complete report, 63,513-byte input |
| Plan Reviewer (`astra_challenge`) | 171.2 | rejected: `$.concerns[0]: missing id` |
| Report repair 1 | 24.9 | rejected: concern IDs must be nonempty and unique |
| Report repair 2 | 22.4 | rejected: the contract does not cite the brief's first sentence |

The run paused `PAUSED_INVALID_OUTPUT` after 830 seconds with no reviewable plan, the symptom this issue
reports, though here Requirements itself finished. One attempt does not establish whether the historical
Requirements timeout recurs.

**Plan Reviewer output rejected for the Planner's contract.** The Planner's criteria cited only part of
the brief; discovery drafts are not held to full sentence coverage. The Reviewer raised that as its only,
non-blocking concern, so adaptive planning tried to approve the draft from the first review, and sealing
the source trace failed. The exception rejected the review as invalid output. Report repair cannot change
the Planner's contract, so both repairs were spent and the run paused. An approval the runner cannot
install now sends the draft to the Planner to revise, which is what every first review does outside
adaptive planning; the reason is saved in `planning.adaptive.approval_refused` and the revise prompt
names it. Failed approval attempts restore the state saved before them.

**Investigator `capture` path.** The Investigator works in `.autocode/investigation/bug-*` while the shared
provider rule tells it to save receipts with `--output .autocode/evidence/...` and to copy the handoff
`workspace` for absolute paths. `capture` accepted only `<cwd>/.autocode`, so the absolute project path
was refused four times before the model guessed a path relative to the scratch copy. From an investigation
scratch copy, `capture` now also accepts the owning project's `.autocode`; it accepts nothing further up,
so a task worktree still cannot write into its outer project's `.autocode`.

Within the 193-second Requirements stage, tools took about 7 seconds and one model step took about
173 seconds to write a 4,497-token report. The 55,383 bytes of planner instructions were unchanged here;
their largest parts are the example-criteria (7.3 KB), contract-reference (5.1 KB), progressive (4.0 KB),
evidence-facts (3.9 KB) and bug-diagnosis (2.9 KB) rules.

## Why the Requirements stage ran to its deadline (2026-10-21)

The discovery-07 run directory is no longer on the qualification host. Its sibling native run of the same
queue task (`20261019-110001-vscode-abfc35`) kept its discovery prompts. Its discovery-01 prompt (98,884 bytes
with the provider output contract and saved schema, close to discovery-07's 95,131) was replayed through Kilo's
direct Xiaomi and Z.AI logins, never the Kilo Gateway. The replay used a read-only-tools agent, a 900-second
limit and a copy of the project restored to the files that discovery-01 saw.

| Route | Seconds | Reasoning tokens | Output tokens | Result |
| --- | ---: | ---: | ---: | --- |
| MiMo Flash, medium | 827 | 34,701 | 6,134 | report, all but 10 s in one model step |
| MiMo Flash, medium (source not yet restored) | 900 | 24,281 | 1,562 | stopped, no report; first tool at 276 s |
| GLM-5.3, low | 98 | 940 | 4,831 | schema-valid report, every brief sentence cited |

The stage did not stall. Throughput was steady: MiMo produced 30–50 tokens per second in every retained
Kilo session examined, so a stage's time is close to its token count divided by that rate. On MiMo Flash at
medium effort, this discovery prompt needs about 35,000 reasoning tokens, which puts it at the 900-second limit
the trial set; the new-run default is 3,600 seconds. Most of that reasoning reconciles overlapping obligations:
which brief sentence each criterion quotes, and whether each requirement row, diagnosis case and required
behavior needs its own `test:` or `guard:` criterion. The same prompt on GLM-5.3 low took 98 seconds.

The GLM-5.3 low draft left one source uncited: the person's `plan` reply to the spec-first Requirements review,
which the source log records as a `user_answer`. The Plan Reviewer's seal refuses any uncited source sentence, so
that draft would have been sent back. A go-ahead is not requirement text; `source_trace.quotable` now leaves it
out of the sources a criterion must quote and out of the inventory models are shown. The source log, inventory
hashes and contracts that already cite it are unchanged. Any other review answer is still a traced source.

A live spec-first run of the same reprioritization brief with the fix, run
`20261021-024734-fix-the-reprioritization-bug-in-pqueue-py-priori-fab18778`, put Requirements and Planner on
GLM-5.3 low and the Plan Reviewer on GPT-5.6 Sol. AutoCode refuses a Plan Reviewer on the Planner's own model,
and the bug Investigator follows the Plan Reviewer route. After the person answered `plan`, the run reached
goal approval with plan r3. Requirements took 40 seconds and discovery 70; total model time across nine calls
was 773 seconds. The review's four blocking concerns were real plan defects, and none of them was the `plan`
reply. One revision repair was refused for changing a criterion the review had asked to change: "Planner
revision drops or changes 'AC6' without a user-backed contract change". That conflict is outside this fix.

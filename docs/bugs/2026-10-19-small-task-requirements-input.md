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
health or success. There is no demonstrated deadlock, output-protocol contradiction,
or causal latency explanation in these artifacts.

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

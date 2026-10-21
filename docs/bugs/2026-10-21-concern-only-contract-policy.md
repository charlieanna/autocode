# Contract-author commands in concern-only review (#1808)

The TODO qualification's same-chat ID-integrity follow-up timed out in Plan
Reviewer after 900.437 seconds, without a final review. Its retained bundle manifest
is `56da12f0e12cfe377cd945b2bb142570fab7abd89792276255276aaadfc0f30e`:
run `20261021-055151-build-a-to-do-cli-named-todo-py-backed-by-a-todo-683feec4`,
turn 2, attempt `003/plan-challenge-03`, runtime `3a70df80c`. This was a Linux
TaskRun trial using Kilo 7.8.3, with Plan Reviewer on Xiaomi Singapore MiMo
V2.6 Flash Medium. The prompt was 124,811 characters. The timeout's cause
remains unproven; prompt size and streaming progress do not establish it.

A source audit found a deterministic protocol conflict, also present on master
`463fdf558`. The first review's schema owns concerns, not a contract, but its
active instructions said to return `delegated_decisions=[]`, use
`contract_changes=[]`, edit `contract.open_blocking_questions`, and set
`contract.initial_task.kind=none`. Shared example and milestone instructions
also told this reviewer to write criteria and perform an execution handoff.
The closed review schema correctly refuses those contract fields.

Planning context now selects the instruction scope from the report schema's
fields. A concern-only report receives the complete author policies under
`contract_author_rules`, as material to audit, and instructions to report
violations as concerns. Every policy byte is retained. A report owning a contract
keeps its existing author instructions. No saved state, source quotes, contract,
schema, approval token, provider, effort, limit or recovery allowance changes.

The regressions cover both legacy and V2 concern-only schemas and retain rejection
of forbidden fields. The public CLI regression uses an offline provider that follows
the active writer instructions. On unchanged master it returned forbidden contract
fields, spent report repair and stopped before approval (80.599 seconds). With the
correction it reached the actual plan-approval boundary without a review repair
or any Builder dispatch (17.884 seconds on the final wording). The command was:

```sh
python3 -m unittest tests.test_stage_repair_cli.StageRepairCLI.test_concern_only_review_reaches_approval_without_contract_field_repairs
```

It ran in the existing supported offline Linux boundary. Another 86 focused
schema, prompt-policy, planning, architecture, rendering, identity and CI-selection
checks passed in 22.943 seconds. Fake-provider duration is not a model-latency measurement.

The ten affected golden reviewer renderings each gain 1,593 bytes from the explicit
audit scope and JSON encoding. Their existing handoff values are identical; all
other golden renderings, including contract-author stages, are byte-identical.
This fixes instruction ownership; it is not an input-size reduction or measured
live speed improvement. The paid run stays paused on its saved route. The proposed
thinking-off override was not approved or applied.

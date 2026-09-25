# Bug 001: Plan Reviewer stuck in tool-calling loop, produces no structured output

**Severity:** High
**Found:** 2026-09-25 stress test on bounded-repair-tasks feature
**Run:** `20260925-021232-add-bounded-repair-task-generation-from-findings-34320863`

## Summary

The `astra_discovery` stage (Plan Reviewer) repeatedly enters a tool-calling loop
— reading files, grepping, reading more files — without ever producing a structured
text response. The stage is correctly flagged as "uncertain" by AutoCode's safety
mechanism, but this creates a pause loop: each resume triggers another uncertain
stage with the same behavior.

## Reproduction

1. Run `autocode` with a complex multi-module feature request (e.g., "add bounded
   repair task generation from findings")
2. Use `--no-chat` mode
3. Answer the requirements gathering questions
4. Approve the goal
5. The `astra_discovery` stage runs and produces only `tool_use` events (read, grep)
   with no `text` output
6. AutoCode pauses with `PAUSED_PROVIDER_UNCERTAIN`
7. Abandon stage and resume → same behavior repeats (astra_discovery-02, -03, ...)

## Evidence

From `astra_discovery-03.jsonl`: 6 tool calls (read, grep, read, read, read, grep),
0 text events. The model explored the codebase but never produced a plan or analysis.

## Expected behavior

The Plan Reviewer should produce a structured output (plan, critique, or analysis)
after exploring the codebase. If the model can't produce structured output after
N tool calls, the system should either:
- Force a text response before more tools
- Switch to a stronger model (escalation)
- Surface a clear error instead of looping

## Suggested fix

1. Add a max-tool-calls-without-text threshold for planning stages
2. Auto-escalate the model when the threshold is hit (instead of just marking uncertain)
3. Consider injecting an explicit "produce your analysis now" reminder after N tool calls

## Related

- The bug ironically mirrors the product's own goal: "no more retry loops"
- The uncertain-stage safety mechanism works correctly (good)
- But the abandon → resume cycle is a manual retry loop

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

**Confirmed systemic (not model-specific):** Reproduced with both GLM-5.3 and
`openai/gpt-6-sol`. Both models explore the correct files (`autopilot.py`,
`autocode_findings.py`, `autocode_milestones.py`) but never produce text output.

**Root cause confirmed:** The planner prompt is **61KB / 1030 lines (~15K tokens)**
before the model does anything. Breakdown:
- Template: ~200 tokens
- Policy (DECISION_PROVENANCE + CONTRACT_REFERENCES + MILESTONE_POLICY): ~2000 tokens
- Workspace inventory (120 files): ~2200 tokens
- Handoff data (requirements, contract, history as JSON): ~10000 tokens

The model reads this massive prompt, starts exploring (which is correct behavior),
but never transitions from exploration to structured output. The `read` tool calls
add even more content to the context, making it worse.

**Key insight:** The prompt says "Explore relevant source and return code_refs,
alternatives and uncertainties" but gives no explicit signal when to STOP exploring
and START writing. The model keeps finding more things to read.

## Expected behavior

The Plan Reviewer should produce a structured output (plan, critique, or analysis)
after exploring the codebase. If the model can't produce structured output after
N tool calls, the system should either:
- Force a text response before more tools
- Switch to a stronger model (escalation)
- Surface a clear error instead of looping

## Suggested fix

1. **Trim the prompt**: The workspace inventory (120 files) and handoff JSON
   (~10K tokens) should be summarized, not dumped raw. A file list of 120 paths
   is navigation noise — the model can use `glob`/`read` to find files.
2. **Add a stop-exploring signal**: After N tool calls (e.g., 5), inject
   "You have explored enough. Produce your structured analysis now."
3. **Max-tool-calls-without-text threshold**: If the model does >N tool calls
   with 0 text output, auto-escalate or force a text response.
4. **Two-phase approach**: First pass = explore and summarize findings (text output).
   Second pass = produce structured contract from the summary.

## Related

- The bug ironically mirrors the product's own goal: "no more retry loops"
- The uncertain-stage safety mechanism works correctly (good)
- But the abandon → resume cycle is a manual retry loop

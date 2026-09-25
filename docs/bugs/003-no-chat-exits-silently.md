# Bug 003: --no-chat mode exits on WAITING_FOR_USER instead of waiting

**Severity:** Medium
**Found:** 2026-09-25 stress test on bounded-repair-tasks feature

## Summary

When running with `--no-chat`, the process exits silently when it reaches
`WAITING_FOR_USER` status. This is expected for non-interactive use, but it means
multi-step flows (answer questions → approve goal → execute) require multiple
CLI invocations. The user gets no output explaining why the process exited.

## Reproduction

1. `autocode "complex task" --no-chat`
2. Requirements gatherer asks questions
3. Process exits with code 0 (or 2 in some cases)
4. No output indicating what it's waiting for

## Expected behavior

Either:
- Print the pending questions and exit with a clear status message
- Accept answers and approval via flags in the same invocation
- Provide a `--batch` mode that auto-delegates with proposed defaults

## Suggested fix

When `--no-chat` hits `WAITING_FOR_USER`, print the questions and suggested
`--answer` invocations to stdout before exiting. This makes the multi-step
workflow discoverable.

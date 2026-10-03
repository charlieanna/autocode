# Saved regression proof disagreed with required-case coverage

Two fresh Codex-only OpenCode TODO trials on merged master `79dffe2b` completed
with all ten independent oracle checks passing. One reproduced weak malformed-store
tests: they also passed without the application. AutoCode correctly rejected that
proof, repaired the tests and completed with eight fail-to-pass tests.

The final evidence audit found a separate reporting bug. Its first proof receipt
and handoff said FAIL, but the cited `regression/proof-01/verification.json` said
PASS and omitted the required-case failures. The runner saved the low-level suite
result before checking each approved case. That check changed only the in-memory
proof. Completion remained blocked correctly; the saved evidence was misleading.

A real-repository reproduction through the public `regression.prove` API has one
passing feature test and another approved case with no corresponding test. It
returns FAIL while the saved artifact says PASS on the old code. The runner now
persists after required-case evaluation, including the final verdict, failures,
case-to-test mapping, scope and coverage notes. Full command receipts and the
lower-level execution diagnostics remain in the same artifact.

The existing proof tests now check both successful and missing-case artifacts and
preserve-case coverage notes. The fix changes saved evidence, not the completion
policy. Original live evidence is retained unchanged under the ignored
`.scenario-runs/20261002-post221-todo-live/` directory.

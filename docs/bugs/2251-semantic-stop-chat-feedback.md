# Permitted corrective feedback was unavailable in terminal chat

The native coordinated checkout run stopped at `RESOLVER_PENDING`, while History
and Tax stopped at `PAUSED_COMPLETION_GATE`. The core's pause-authority check
permitted feedback for all three. Their public chat input was nevertheless null,
so terminal chat offered only Continue even when the stop explicitly advised
giving corrective feedback. This prevented normal TUI replanning of the local
flow issue in #2244.

The chat projection now names its existing `--feedback MESSAGE` action at those
semantic resume stops when pause authority permits it. No client constructs a
new command. The terminal's existing confirmation consumes that named message
offer; it sends neither a resume nor an approval with the correction. The CLI
still checks admission and a changed plan still needs approval. Held operational
pauses, blockers, human review and non-resume checkpoints keep their gates.

Both public projection and chat-consumption regressions fail before the fix for
both statuses. The focused view/conversation selection passes 64 tests. Additional
controls retain operational-cost, blocker, human-review and public-question
refusals. No status field, CLI option or authority is added.
The supported-Linux changed-test selection passes **331 tests in 17 modules
(65 seconds)**, including 102 chat-client cases, public CLI consumers and the
architecture check. Ruff, formatting, mypy and diff checks pass.

The actual curses TUI exited safely at its input gate. Its three member states,
the anchor, prior proofs, model routes and limits remain saved under
`.scenario-runs/coordination/live-glm-openai-20261027T182900Z`, coordination
`07ccefc3b825ad0c7efd0e0c`. It must consume the named action in the resumed TUI;
direct state edits or a separate feedback CLI invocation do not count as this
verification. Native joint proof and real fixture publication remain pending.

# Continuous requirements and Planner boundary

A conversation has two independent outputs: the Requirements Gatherer replies
to the human, and the Planner produces a structured draft for the exact human
turn and requirements revision. A Gatherer reply does not make a plan fresh.
The repository-aware Plan Reviewer must still review the final contract before
the human can approve it and choose Build.

## Durable transfer to a task

`autocode_conversation` defines the version-1 handoff and runner journal. A
handoff contains messages, unsent drafts, requirements history, structured plan
history, configured routes, and available Gatherer/Planner dispatch receipts.
Messages may include validated `execution` attribution (`role`, `model`, and
optional `engine`, `provider`, `reasoning_effort`) plus a list of `event_refs`.
These optional fields are absent from older handoffs, keeping their canonical
digests unchanged. Dispatch receipts retain their original states and history.

A client stages a digest-bound receipt inside the selected project's
`.autocode/conversation-handoffs/` directory and supplies its path using
`--conversation-handoff`. The task accepts it under its run lock, verifies
project ownership even when execution uses a child worktree, and writes
`conversation-journal.json`. A repeated identical transfer is harmless; a
different conversation or receipt cannot replace the journal. The single
`conversation_handoff` state pointer records its ID, digest, staged path and
journal path. The client reads the journal after attachment.

Transferred drafts and receipts grant no approval. Attached product changes
append conversation history and make the displayed draft stale; the runner's
existing contract-change process controls replanning, review and reapproval.

## Explicit Build boundary

Approval and Build are separate actions. After approval the client sends
`--expected-goal-token` with Build. Under the run lock, the CLI requires the
same sealed approved contract, no unresolved questions, and the matching
Architect final token for joint planning. It checks again before continuing.
A successful Build request records one `build_start` user event for that token,
distinct from `goal_approval`. A stale token does not launch a provider or
record Build; retrying the same approved Build does not duplicate its event.

## Scoped model profile

The continuous profile applies only when creating a new task from a handoff.
Ordinary CLI defaults and already saved task routes retain their configuration.

| Role | Model | Reasoning |
| --- | --- | --- |
| Requirements Gatherer | OpenAI GPT-6 Sol | Low |
| Planner, Builder and Builder retry | OpenAI GPT-6 Sol | High |
| Resolver | OpenAI GPT-6 Sol | Max |
| Plan Reviewer, Architect, Validator, Completion Owner | Z.AI GLM 5.3 | High |
| Visual review | OpenAI GPT-6 Astra | High |

Explicit incompatible routes are rejected before dispatch. The continuous
profile keeps those routes during recovery: generic reasoning escalation does
not change them, while the authorized Builder retry still runs on Sol High.
The continuous
profile disables default usage/time/idle/iteration caps; explicit CLI budgets
remain authoritative. Verification and contract approval gates still apply.

Offline tests exercise the actual CLI with isolated temporary repositories.
They do not claim an installed live-provider result.

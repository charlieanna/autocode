# Queued interventions

[← Back to README](../README.md)

The browser can submit a durable change request without competing for the workspace
writer lock or changing `state.json`:

```text
["autocode", "intervention", "submit", "--workspace", "/project", "--run-dir", "/project/.autocode/runs/run", "--request-id", "request-123", "--kind", "feedback", "--text", "Keep the partial implementation", "--json"]
["autocode", "intervention", "submit", "--workspace", "/project", "--run-dir", "/project/.autocode/runs/run", "--request-id", "pause-124", "--kind", "pause", "--json"]
["autocode", "intervention", "inspect", "--workspace", "/project", "--run-dir", "/project/.autocode/runs/run", "--json"]
```

## Submission

`submit` writes only the canonical run's `.autocode/runs/<run>/interventions.json`
under a separate short-held inbox lock. It validates the Git workspace, canonical run
containment, direct regular `state.json`, checkpoint workspace pointer, and rejects
symlinked inbox or lock paths. The versioned receipt has `id`, `kind`, original `text`,
monotonic `order`, `submitted_at`, `observed_goal_token`, and
`boundary_pause_requested`. Both feedback and pause requests set that boundary intent;
they never start a provider or write `state.json` from the submitting process.

Request IDs are idempotency keys over `id`, `kind`, and original `text`: an identical
retry returns the original durable receipt without another record, while changed text or
kind under the same ID returns `request_conflict`. Concurrent submissions are serialized
by the inbox lock and receive durable order values. Corrupt, unsupported, unavailable,
locked, invalid, or failed-write inboxes return JSON errors and exit 2 without claiming a
receipt. `inspect` is read-only, creates neither inbox nor lock, and reports only pending
requests; it does not claim that a currently running older binary can consume them.

## Consumption

The workspace-lock owner checks the inbox after recovery and before every provider
admission, and after every saved stage result, including question, approval, review and
completion exits. Inbox acceptance and admission are serialized by the short inbox lock:
a request accepted before admission is consumed first; one accepted after admission waits
for that stage's saved boundary. The inbox lock is never held while a provider runs.
Consumption writes the applied receipt ledger and feedback event to authoritative
`state.json` before removing inbox records. A crash before that state write leaves the
request pending; a crash after it is recovered by recognizing the applied ID and retrying
only inbox acknowledgement. If interruption follows inbox acknowledgement but precedes
the final state write, owner recovery clears the obsolete acknowledgement marker only
when every marked ID is already applied. Receipts retain their original IDs, text and
order.

Applied feedback saves a `brief_feedback` provenance event, preserves stage artifacts and
partial edits, archives stale validation/review authorization, invalidates goal approval,
and pauses with `astra_discovery` selected. It never starts Requirements discovery automatically: invoke
the existing explicit Continue action as an argument array, for example
`["autocode", "--workspace", "/project", "--run-dir", "/project/.autocode/runs/run", "--resume-paused"]`.
The revised brief still requires its exact displayed approval token. Pending feedback also
blocks goal approval, artifact approval and completion until the owner consumes it.

A pause-only request preserves the selected next stage and any valid goal approval. It
pauses at the next safe boundary with a `pause_intent`; `--resume-paused` records its
acknowledgement and resumes that selected stage. `--pause-after-stage` and the existing
run-local `pause-requested` file continue to stop at saved boundaries. `--status` is
read-only and adds `interventions` with inspector versus recorded-runner capability,
pending IDs/count, pause intent, applied receipts, inbox errors and blocked conditions.

Use `--astra-model`, `--terra-model`, `--sol-model`, or `--completion-model` to
explicitly override a role. Resuming keeps the saved engine, provider mapping and
limits unless overridden.

## Intervention ordering and recovery

Each request ID retains its original receipt after application. An identical explicit retry returns it without requeueing; changed text or kind under that ID is a conflict. Receipt order increases across consumed batches. The observed goal token is read under the inbox lock when accepting the request.

Submission and provider admission share the short inbox lock. Preparation occurs before admission; a request accepted first prevents the next provider launch. A request accepted after admission is queued while that stage finishes. The lock is released before waiting for the provider or asking for terminal input. Pending requests also prevent a prepared answer, goal approval, artifact approval or operator completion from committing. Stage results and recovery preserve completed work and consume earlier requests before committing any completion authorization.

The owner commits pause effects, feedback invalidation and applied receipt IDs in one authoritative state write before removing inbox requests. Restart recovers both acknowledgement crash windows without applying an ID twice. Pause confirmation requires the saved paused state, not merely a receipt. Explicit `--resume-paused` acknowledges a saved pause; feedback still requires Requirements discovery and a newly displayed exact-token brief approval before implementation can resume.

Applied receipts include `applied_at`; explicit continuation adds `resumed_at` to previously applied receipts. Status exposes these durable timestamps. Submission retries omit these owner-only fields and continue to return the original acceptance receipt.

See also: [Dashboard](dashboard.md) · [Workflow](workflow.md) · [Registry API](registry-api.md)

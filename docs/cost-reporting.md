# Measuring the cost of issue solving

## Every task, continuously

Each run keeps its token and cost totals current while it works. Nothing needs to be
switched on and no model is called.

- **Status view.** `usage` in the view (`autocode --run-dir RUN --status`, [Task run](task-run.md))
  holds the run's totals so far: stages, tokens, `cost_usd` (`reported`, `estimated`, `complete`),
  `unknown_stages` and the same by role.
- **Project ledger.** `PROJECT/.autocode/usage.jsonl` has one row per run, rewritten at every
  checkpoint save, so it is right during a task and after it pauses, completes or is killed. It
  is under `.autocode/`, which Git ignores.
- **Report.** `python3 tools/autocode_usage.py [PROJECT]` prints one line per run and a total;
  `--json` prints the rows.

Cost has three bases, always kept apart: **reported** is the provider's own `cost_usd` (the Claude
example provider writes it; a provider that reports none, or only a zero for a subscription plan,
has none); **estimated** is tokens at the flat comparison rates below, and only for models listed
there (today the GLM route); anything else is **unknown, not zero**. `complete` is false while a
stage has no cost or is still running, so a total is then a floor. Stages the runner executes
itself (the regression proof, the orchestrator) cost nothing. The OpenCode/Codex route reports
tokens but no price, so its cost shows as unknown until a rate is added to
`autocode_usage.REFERENCE_PRICES`.

Parallel Builders are counted once, in their parent. A worker run keeps no ledger of its own: when a
batch finishes, the parent copies each worker's finished stages, cost included, into its own record
(`autocode_dispatch.account_workers`), so the parent's row holds them. While a batch is still running its
workers' spend is not yet in the parent's row; it appears when the batch is accounted.

## Outcome-linked status

The public status view also exposes `usage.accounting` and `efficiency`. These are
additive measurement projections, not new acceptance gates or model calls.

- Accounting keeps individual failed, interrupted, active and report-repair attempts.
  Native requests are deduplicated by session and part identity. Missing finishes,
  conflicting duplicates and absent token components remain explicitly incomplete;
  a known subtotal is not a complete total, even when another component is priced.
- Observed execution intervals, summed execution durations and provider-request
  intervals are separate. Parallel overlap is not added to wall time twice.
  Unobserved waiting, setup or visual-inspection time is not inferred from gaps.
  OpenCode request starts are paired within the same session and message. Multiple
  pending starts for one message leave its timing unknown and coverage explicitly
  uncertain; known finish-token receipts are retained. Message-free events use
  legacy FIFO only in entirely message-free sessions, and cannot consume another
  message's start. Conflicting message bindings in a replay invalidate timing for
  both affected messages without duplicating usage.
- Runner check observations distinguish execution, repeated execution, reuse and
  suppression, with reasons and original receipt provenance. A required independent
  or canonical execution is not classified as wasted merely because it repeats a command.
  Recovery after a checkpoint crash imports a retained check's original duration
  once; reusing that receipt does not execute or charge those seconds again.
- `autocode_efficiency.aggregate` consumes supplied public task-run views, not other
  runs' private state. It keeps root deliveries distinct from worker completions,
  deduplicates imported request identities, and reports missing worker coverage.
- Accepted-task unit metrics require the current checkout's full completion gate,
  not just a historical `TASK_COMPLETE`. A stale checkout contributes no current
  delivery; a state-only view without a fresh gate result keeps acceptance unknown.
  Unit metrics remain undefined without an accepted root outcome or complete usage.
  Experiment reports must additionally apply their external
  delivery oracle; the status projection cannot establish untested product quality.

Visual acceptance remains a separate, stronger requirement. A manifest case,
produced screenshot, model assertion or green functional suite does not establish
that an independent reviewer inspected the reference and current candidate images.
Until the runner supplies authoritative current-bound visual acceptance receipts,
accepted-frame/state counts, first-acceptance times and visual unit costs are
`null` with an explanation. Do not substitute zero, count mapped criteria as
accepted screens, or advertise visual savings from a generic `TASK_COMPLETE`.

Failed ledger writes emit a visible warning without changing the task outcome.
That warning is a measurement gap, not evidence that the missing usage was free.

The sections below describe the scorer and sampler, which inspect a saved run after the fact.

The existing scorer and live sampler can inspect a saved run without launching
providers or changing its state:

```sh
python3 tools/score_autocode_run.py --run-dir /path/to/run \
  --out /tmp/run-score.md --json-out /tmp/run-score.json
python3 tools/live_token_sampler.py --run-dir /path/to/run \
  --json-out /tmp/run-steps.json
```

Both also work as Python modules (`python3 -m tools.score_autocode_run` and
`python3 -m tools.live_token_sampler`) from the repository root.

## How to interpret the numbers

The scorer's `token_usage.estimated_api_equivalent_usd` uses the existing
historical comparison rates, recorded in `pricing_basis`. These are **not
verified current prices or actual charges**. The calculation prices inclusive
input and output once at flat rates, without a cache discount. Subscription
fees, exhausted quotas, and human time require separate accounting.

- Runner-normalized input already includes cached tokens; output includes
  reasoning. The scorer does not add either subset again.
- Raw OpenCode steps report separate input, cache reads, cache writes, visible
  output, and reasoning. All five buckets contribute once.
- Unknown models have no fallback price. Missing or invalid usage is unknown,
  not zero. Explicit zero remains zero.
- A missing cost in any recorded stage makes the complete estimate `null`.
  `known_estimated_api_equivalent_usd` remains available as a subtotal, with
  `unpriced_stages` reporting its coverage gap. An active stage also prevents
  a complete estimate, even if it has partial usage.
- Failed, timed-out, and abandoned stages remain in the accounting. A valid
  runner-owned transition has no inference cost.
- Historical models come from the saved launch command, falling back to a
  model explicitly saved on the attempt. Today's role configuration cannot
  establish which model ran yesterday.

The sampler reads only event paths recorded in that run's state and located
inside its directory. It ignores unregistered copies and deduplicates repeated
updates by session and part ID within each log. Records without IDs cannot be
deduplicated reliably. Conflicting model identities for one log remain unknown.
Only OpenCode step events are sampled; normalized provider usage still appears
in the scorer's stage table for other transports.

Sampler totals always describe **observed steps only**. They do not establish
complete coverage: a crashed request, missing log, or unfinished call may consume
additional tokens. Do not add step totals to stage totals; they describe
overlapping usage. JSON consumers must accept nullable counts and estimates.
The sampler now exposes `models` per stage because retries can change models.

## Evaluation milestone

Use this accounting to establish a baseline before changing orchestration or
model selection. The next issue-evaluation work should record:

1. A frozen repository revision, issue requirements, and reproducible setup.
2. Every attempt, including setup and provider failures, with stable identities.
3. Independent candidate-bound checks and patch review; a runner completion
   claim or this scorer's heuristic dimension scores are not an acceptance oracle.
4. Assisted versus unassisted delivery, elapsed time, and human intervention time.
5. Total campaign cost divided by independently verified fixes, including failed
   attempts in the numerator. With missing cost data, publish a known subtotal
   and coverage gap instead of a complete cost-per-fix claim.

Start with a small development set and keep separate held-out issues. Compare
against a single-agent baseline with the same models, tools, and budget. Expand
only after the common failure causes are understood. This baseline and model
effectiveness comparison have not yet been established by the accounting tests.

## Regression checks

```sh
.venv/bin/python -m unittest tests.test_cost_reporting tests.test_efficiency \
  tests.test_usage tests.test_autocode_request_usage tests.test_opencode tests.test_opencode_length -q
```

These checks use synthetic provider events and local fixture executables. They
cover cost arithmetic against the actual provider normalizer, missing data,
retry model identity, repeated events, failed attempts, and both CLI entry paths.
They make no live model calls. The subprocess fixture needs local process-table
access for its process-supervision checks.

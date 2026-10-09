# Engineering scenario ladder

This catalog tests AutoCode through its CLI in isolated repositories. A run only
passes when the independent oracle accepts its delivery and any required workflow
behavior. A completed provider request or `TASK_COMPLETE` alone is insufficient.

## Scope and evidence

The September 29 expansion adds 24 bounded engineering tasks to the existing 24.
The advanced rungs exercise production-relevant invariants in small, reproducible
projects: transactions, replay, concurrency, authorization and dependency graphs.
They do not establish production-scale performance, real cloud deployment,
operational security, or unlimited project complexity.

Each added rung provides a specified public interface, seed, reference solution,
at least two deliberately defective implementations, project tests, and hidden oracle
checks. The hidden checks run on a scratch copy of the delivery. No live model
sees the reference or hidden tests through the supplied task workspace.

Keep these evidence levels separate:

1. `check`: the oracle rejects the seed and broken controls and accepts the
   reference. This tests the benchmark, without running AutoCode.
2. `run --fake`: AutoCode runs its real workflow while a scripted provider
   supplies reference code. This tests orchestration and completion gates.
3. `run --profile codex-only --i-authorize-live-model-spend`: real OpenAI models
   perform the engineering work. These outcomes measure model-driven delivery.
4. Repeat fresh live runs before claiming reliability. One successful run per
   scenario is a coverage sweep, not a stable pass-rate estimate.

The driver records proposed-default answers and exact plan approvals. When a
question explicitly has no default, it selects the first concrete offered option
or stops if none exists; it never submits the "No default" placeholder. It leaves
operational pauses as blockers; the sweep does not manually repair a paused
project or relabel it as a pass. A known failure remains visible in the counts.

## Existing GitHub qualification

The existing ladder is tracked in [#110](https://github.com/charlieanna/autocode/issues/110).
Its historical mixed-provider results are separate from the Codex-only campaign.

| Step | Scenarios | Tracking |
| --- | --- | --- |
| 0 | Stuck planner recovery with a live Investigator | [#111](https://github.com/charlieanna/autocode/issues/111) |
| 1 | Greeting CLI | [#112](https://github.com/charlieanna/autocode/issues/112) |
| 2 | Trivial pagination fix; ISO-week correctness | [#113](https://github.com/charlieanna/autocode/issues/113) |
| 3 | Non-reproducible bug: leave working code alone | [#114](https://github.com/charlieanna/autocode/issues/114) |
| 4 | Planted regressions; vacuous tests; clean PR | [#115](https://github.com/charlieanna/autocode/issues/115) |
| 5 | Add a per-project timesheet breakdown | [#116](https://github.com/charlieanna/autocode/issues/116) |
| 6 | Refuse an implementation that conflicts with a frozen design | [#117](https://github.com/charlieanna/autocode/issues/117) |
| 7 | Money precision; stale prices; design review; cache analysis | [#118](https://github.com/charlieanna/autocode/issues/118) |
| 8 | Two-service architecture; parallel diamond; C# to Go port | [#119](https://github.com/charlieanna/autocode/issues/119) |

The catalog also covers locked-design implementation, durable JSON todo storage,
refund-window rules, timeout idempotency, sound-design review, repository
investigation, and a review-to-fix conversation.

## Added rungs

The numbered rungs progress from small CLI behavior through stateful components
to advanced system invariants. See each linked brief for the exact contract.

| Rung | Scenario | Job |
| --- | --- | --- |
| 01 | [ladder-01-temperature-cli](catalog/ladder-01-temperature-cli/brief.md) | Convert temperatures with explicit input validation |
| 02 | [ladder-02-word-frequency-cli](catalog/ladder-02-word-frequency-cli/brief.md) | Count and rank text tokens deterministically |
| 03 | [ladder-03-csv-validation-cli](catalog/ladder-03-csv-validation-cli/brief.md) | Validate CSV records with structured row errors |
| 04 | [ladder-04-json-config-merge](catalog/ladder-04-json-config-merge/brief.md) | Merge JSON configurations recursively without aliasing |
| 05 | [ladder-05-interval-coalescing](catalog/ladder-05-interval-coalescing/brief.md) | Fix interval coalescing at touching boundaries |
| 06 | [ladder-06-csv-export-quoting](catalog/ladder-06-csv-export-quoting/brief.md) | Fix CSV export escaping while preserving field contents |
| 07 | [ladder-07-stable-record-query](catalog/ladder-07-stable-record-query/brief.md) | Add stable filtering sorting and pagination to a record API |
| 08 | [ladder-08-strict-calendar-dates](catalog/ladder-08-strict-calendar-dates/brief.md) | Add strict calendar date parsing and inclusive ranges |
| 09 | [ladder-09-sqlite-inventory](catalog/ladder-09-sqlite-inventory/brief.md) | SQLite inventory CLI with durable quantity updates |
| 10 | [ladder-10-atomic-schema-migration](catalog/ladder-10-atomic-schema-migration/brief.md) | Transactional SQLite schema migration and data normalization |
| 11 | [ladder-11-optimistic-documents](catalog/ladder-11-optimistic-documents/brief.md) | Durable versioned documents with optimistic concurrency |
| 12 | [ladder-12-idempotent-job-store](catalog/ladder-12-idempotent-job-store/brief.md) | Persistent idempotent job submission and lifecycle |
| 13 | [ladder-13-cursor-pagination](catalog/ladder-13-cursor-pagination/brief.md) | Stable cursor pagination across timestamp ties |
| 14 | [ladder-14-transactional-csv-import](catalog/ladder-14-transactional-csv-import/brief.md) | Atomic CSV inventory import with validation and upserts |
| 15 | [ladder-15-ttl-lru-cache](catalog/ladder-15-ttl-lru-cache/brief.md) | Bounded TTL and LRU cache with an injected clock |
| 16 | [ladder-16-tenant-http-api](catalog/ladder-16-tenant-http-api/brief.md) | HTTP JSON CRUD API with tenant isolation and strict validation |
| 17 | [ladder-17-transaction-ledger](catalog/ladder-17-transaction-ledger/brief.md) | Concurrent durable ledger with idempotent transfers |
| 18 | [ladder-18-durable-lease-queue](catalog/ladder-18-durable-lease-queue/brief.md) | Recoverable queue leases with fencing and deterministic time |
| 19 | [ladder-19-transactional-outbox](catalog/ladder-19-transactional-outbox/brief.md) | Transactional outbox with replay after publisher failure |
| 20 | [ladder-20-incremental-build-graph](catalog/ladder-20-incremental-build-graph/brief.md) | Incremental build graph with durable cache and failed-build rollback |
| 21 | [ladder-21-event-replay-snapshots](catalog/ladder-21-event-replay-snapshots/brief.md) | Deterministic event replay and snapshot corruption recovery |
| 22 | [ladder-22-safe-archive-extraction](catalog/ladder-22-safe-archive-extraction/brief.md) | Transactional ZIP extraction with traversal and size defenses |
| 23 | [ladder-23-tenant-authorization](catalog/ladder-23-tenant-authorization/brief.md) | Persistent tenant authorization and last-admin invariants |
| 24 | [ladder-24-deployment-config-plan](catalog/ladder-24-deployment-config-plan/brief.md) | Validated multi-service deployment plans with atomic CLI output |

## Reproduce

Run from the repository root with a Python virtualenv containing psutil. Git and
process inspection are required; the Go port also requires Go. HTTP scenarios
bind only loopback ephemeral ports. Do not silently skip missing prerequisites.

```sh
PY=.venv/bin/python
$PY scenarios/run.py check
$PY scenarios/run.py run --fake
$PY -m unittest scenarios.test_harness
$PY scenarios/run.py run SCENARIO_ID --profile codex-only --i-authorize-live-model-spend \
  --max-seconds 1200 --max-stage-seconds 480 \
  --max-iterations 6
$PY scenarios/run.py stats --mode codex-only
```

The live profile pins GPT-5.6 Terra for requirements/planning/building and GPT-5.6
Sol for independent review/validation/completion and recovery. Every stage uses
OpenCode's OpenAI OAuth route. Pins prevent retry policy from moving the checkers
to GLM. Model independence here means different GPT models, not different vendors.
The profile allows one builder at a time. The diamond scenario therefore checks
dependency delivery and integration in this campaign; actual parallel scheduling
is covered by the fake-provider harness tests, not these live results.

Results and command output stay under ignored `.scenario-runs/`. A `result.json`
contains verdict, individual oracle checks, timings, answers, revision and model
route evidence. Preserve failures, skips and unknowns alongside successes. The
`stuck-planner-citation` scenario is hybrid: its other stages are scripted, so its
real Investigator pass must not be counted as a full live engineering build.

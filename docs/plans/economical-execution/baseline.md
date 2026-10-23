# A. Cost and delivery-quality baseline (#1889)

Planning input for #1867. Read-only audit of master `08ddeb2a3` (2026-10-22). No paid run
was made for this record, and none is authorized by it. Source findings are not a new
live measurement.

## What already exists

The metric definitions this task asks for are already written down. This record maps
each metric to its existing field and names where coverage stops, rather than adding a
second metric table.

- `docs/cost-reporting.md` defines three cost bases: **reported** (the provider's own
  `cost_usd`), **estimated** (tokens at a rate table) and **unknown, not zero**. It
  separates the dated price table that drives `cost` and the money cap from the frozen
  comparison rates (`autocode_usage.REFERENCE_PRICES`) that evidence reports and
  campaigns are bound to. Its "Evaluation milestone" section already prescribes the
  baseline protocol: frozen revision, every attempt with stable identities,
  candidate-bound independent checks, assisted versus unassisted time, and total cost
  divided by independently verified fixes with failed attempts in the numerator. The
  same section says that baseline "has not yet been established".
- `autocode_efficiency.summary` decides acceptance. A run counts as an accepted
  delivery only when the current checkout passes the full completion gate
  (`completion_current`, computed in `autocode_status_command` against the current
  snapshot). A historical `TASK_COMPLETE` that fails today's gate is a completed task
  with zero verified deliveries and `null` unit metrics. Replay runs are never accepted.
- `usage.accounting` keeps every attempt as a row, including failed, interrupted,
  active and report-repair attempts, with `complete` false while any quantity is
  unknown. `efficiency.time` takes the union of intervals, so parallel work is not
  counted twice in wall time while summed execution stays separate.
- `tools/reliability_ledger.py` and `tools/reliability_import.py` turn harness results
  into per-attempt rows (oracle checks, elapsed seconds, decisions and repairs, cost)
  and a summary with `usd_per_verified_delivery`. `docs/reliability-table.md` is
  generated from `docs/reliability-sweeps.json`.
- `tools/score_autocode_run.py` and `tools/live_token_sampler.py` score one run's
  repair loops and token use at the frozen comparison rates. Neither is an acceptance
  oracle.

## Metric definitions mapped to existing fields

| Metric | Existing source | Where coverage stops |
| --- | --- | --- |
| Cost per accepted task | `efficiency.unit_metrics.reported_usd`; campaign `usd_per_verified_delivery` | The view's unit metric divides **provider-reported** dollars only. Coding-plan and token-plan routes report no cost, so GLM, MiMo and most OpenCode runs always get `null`. The ledger takes `cost.spent_usd` only when `cost.complete` is true. |
| Completion rate | Passed / Runs in the reliability table; `efficiency.delivery.verified_deliveries` per run | Aggregation exists only in the ledger. `autocode_efficiency.aggregate` has no production caller. |
| False completion | Scenario verdict `FALSE_COMPLETE`; the table's "False completions" column | Needs an independent oracle, so it exists only for harness runs. |
| Human interventions | Ledger `interventions_decision_or_repair`: each harness answer and gate | Harness only. Saved state records a person's actions in `user_events`, but no status-view field counts them. |
| Model calls | `usage.accounting.attempt_count`; `provider_requests` | Exact requests exist only for OpenCode (`autocode_request_usage`). Other transports count stages. |
| Tokens | `usage.tokens`; `usage.accounting.tokens` with `known`/`complete` | Reasoning and cache fields depend on what each provider reports. |
| Elapsed time | `efficiency.time` (`wall_union_seconds`, `summed_execution_seconds`, `provider_request_union_seconds`); `interaction_timing` | The `waiting` category has no emitter, so human waiting is never measured. Orchestration overhead is not separated from stage time. `interaction_timing` durations include human answer time. |
| Accounting coverage | `cost.complete`, `cost.unpriced_stages`, `cost.breakdown.unpriced`, `usage.accounting.complete` | Complete for what is recorded. See [accounting](accounting.md) for in-flight and late usage. |

### Rules for the scenarios

- **A1, correct fix never accepted.** Only `verified_deliveries` counts. A correct
  patch whose run never passes the current gate adds its cost to the numerator and
  nothing to the denominator. This is current behavior.
- **A2, failed attempt then passing retry.** Both attempts stay rows in
  `usage.accounting` and in the ledger. Cost per accepted task divides their sum by one.
- **A3, unknown price, usage or interval.** Report the known subtotal and the gap
  (`unpriced_stages`, `missing_intervals`). Never present the subtotal as the total.
- **A4, unmatched trials.** Two runs that differ in models, task or starting code
  cannot isolate a policy change. Compare only rows with the same revision, profile,
  task and starting source.
- **A5, overlapping parallel intervals.** Report `wall_union_seconds` and
  `summed_execution_seconds` side by side. Neither replaces the other.
- **A6, only old or declared evidence.** Finish the inventory with the gaps marked.
  A campaign declaration is not a run.

## Evidence inventory

Every row below predates Requirements-first (#1860, merged 2026-10-21) unless marked,
so none of it measures current master. Revisions are as recorded in each note.

| Date | Source | Revision | What was measured | Headline | Comparable today? |
| --- | --- | --- | --- | --- | --- |
| 2026-10-02 | `docs/bugs/2026-10-02-adaptive-completion-comparison.md` | `7c598602` | 16 live attempts, fixed versus adaptive planning, frozen API rates | Fixed 2/8 completed, 114 stages, 25 report repairs, known $12.61. Adaptive 4/8, 97 stages, 10 repairs, known $13.76. Planning was 48.9% of $26.37 known usage; report-repair stages 13.4%; two Astra Resolver calls 34% and 37% of their attempts. Totals unknown: 5 of 16 attempts unpriced. | No: pre-Requirements-first, adaptive planning was opt-in |
| 2026-10-02 | `docs/adaptive-planning.md` | — | Live plan-compare, 11 requests | First plan: calls 112 to 95. Feedback round: calls 85 to 50, model time 267 to 157 min, blind plan quality even | No: planning only, no delivery |
| 2026-10-04 | `docs/bugs/2026-10-04-claude-reliability-cases.md` | `e8366ad` | 9 runs, claude-tiers | 8 of 9 passed, $26.84 in all; 8 of 9 had a Validator report sent back for one cause, since fixed | No |
| 2026-10-07 | `docs/bugs/2026-10-07-default-profile-reliability-cases.md` | `4e7e6895` | 3 runs, default profile | $0.30, $0.80, $0.37 estimated; 7, 12 and 9 stages; one report repair in two runs | No |
| 2026-10-05 | `docs/bugs/2026-10-05-autoresolver-live-diagnosis.md` | `599b6a9` | Resolver diagnosis batches | $15.63 to $26.99 per batch | No |
| 2026-10-08 to 10 | `docs/reliability-table.md` | `a8852cf4`, `e18cf660` | Latest live rows | Spend column blank in every row; prompts hash not recorded | No |
| 2026-10-10 | `docs/bugs/2026-10-10-first-interaction-latency.md` | `e18cf660` | Setup and first-plan latency | Duplicate model roster 0.9 s of 2.1 s setup; first plan 1017 s to 563 s on a candidate | Partly: setup code unchanged since |
| 2026-10-20 | `tools/autocode_light_builder.py` docstring (#1710) | — | One-file Builder on MiMo Pro medium | 22.6k reasoning tokens, about 555 of 786 s | Fixed by the low-effort first attempt; not re-measured |
| 2026-10-21 | `docs/plans/planning-obligations.md` | `2bda9561b` | Requirements replay; one live planning run | MiMo Flash medium 827 s against GLM-5.3 low 98 s on one prompt. Run fab18778: 773 s model time to approval, 521 s of it one review round | Closest to current; one prompt, one run |
| — | `arena/campaign.json` | `8375b73c` | Declared 13 tasks × 2 repetitions | Status `PLANNED`. 10 of its 11 pinned input hashes no longer match master | No: never executed |

**Missing for current master:** any matched delivery measurement per task class,
cost per accepted task on any route, human waiting time, and the reliability table's
spend column. These go to [qualification](qualification.md) as the measurement backlog.

## Avoidable-cost hypotheses, ranked

Ranked by the share of known spend or time they explain in the evidence above, then by
how directly current master still exhibits them. Each is a hypothesis until the
matched comparison in [qualification](qualification.md) runs.

| Rank | Hypothesis | Evidence | Uncertainty |
| --- | --- | --- | --- |
| 1 | Planning and review rounds dominate spend on small tasks | 48.9% of known cost (2026-10-02); one review round was 521 of 773 s (fab18778); adaptive feedback rounds used 41% fewer calls | Pre-Requirements-first. #1564, #1780 and #1858 have since reduced review depth; not re-measured |
| 2 | Structured-report repair adds calls without new work | 25 against 10 repairs and 13.4% of known cost (2026-10-02); a 24 s repair per GLM-low Requirements report (2026-10-21) | Several causes fixed since. Today's rate per run is unknown |
| 3 | Routine recovery on the strongest route | Two Astra Resolver calls were 34% and 37% of their attempts | The owner already restricted Astra to the Resolver. Whether routine repair reaches it today is unmeasured |
| 4 | Reasoning-heavy routes on small work | 827 s against 98 s for one Requirements prompt; 555 of 786 s Builder reasoning | Partly addressed: live runs route Requirements and the Planner to GLM-5.3 low (owner instruction); the low-effort first Builder attempt. The fixed defaults are unchanged |
| 5 | Verification repeated after it was already current | A Completion Owner follow-up asked for validation current evidence already covered (2026-10-02); 21 capture round trips in the #1710 breakdown | `efficiency.repeats` now records repeats, reuse and suppression, but no saved current sample exists |
| 6 | Whole stages lost to timeouts | A 900 s reviewer timeout then a 716 s recovery; two 900 s stages lost to protocol discovery | Specific causes fixed; recurrence rate unknown |
| 7 | Prompt-cache loss mid-stage | 106k uncached tokens at one Tester step (#1710 line in #1844) | One observation, cause unexplained |
| 8 | Setup overhead | 0.9 s duplicate roster of 2.1 s | Small dollar effect; latency only |

Repeated execution is not automatically waste. A second run of a check that supplies
current or independent proof is required work; only a repeat whose inputs and result
are already bound to valid evidence is a candidate for reuse ([evidence reuse](evidence-reuse.md)).

## Representative comparison tasks

One existing catalog entry per class, so the comparison reuses existing oracles,
reference solutions and broken variants. [Qualification](qualification.md) owns the
final selection and the live scope.

| Class | Candidate entry | Why |
| --- | --- | --- |
| Clear localized bug | `bugfix-trivial` | A three-line off-by-one through the full bug-fix path |
| Unclear bug | `bugfix-iso-weeks` | The root cause sits in another module; already in the campaign declaration |
| Small feature | `feature-timesheet-by-project` | Already in the campaign declaration |
| Sensitive small diff | None exists | `bugfix-cent-drift` and `ladder-23-tenant-authorization` are sensitive but not small by design. A new entry is needed ([qualification](qualification.md)) |
| Same-chat follow-up | `review-then-fix` | One conversation moves from a review to "Fix them."; already in the campaign declaration |

## Measurement backlog for #1896

1. **Instrument gaps first; they need code, not spend.** An estimated-dollar unit
   metric beside `reported_usd`, so coding-plan routes get a cost per accepted task. An
   emitter for human waiting time. The price basis stored per stage ([accounting](accounting.md)).
   Fresh pinned inputs for `arena/campaign.json`.
2. **A deterministic current-master row.** Done 2026-10-23 on `518254b7e`: see
   [qualification](qualification.md), "Current-master deterministic row". It records
   stage sequences per class with no spend; it cannot measure cost or model time.
3. **A matched live baseline.** The representative set on current master with one
   pinned profile, unchanged routes, at least the campaign's two repetitions, every
   failed attempt kept. Scope and spend are decided at the second owner checkpoint.

No numerical savings target is set here. One needs the matched baseline first.

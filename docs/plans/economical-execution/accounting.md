# E. Cost accounting, estimates and spending policy (#1893)

Planning input for #1867. Read-only audit of master `08ddeb2a3` (2026-10-22). This is a
source inventory, not a check of any provider's billing accuracy. It changes no
allowance, route or provider setting. Display belongs to #1884; the public contract
fields belong to [the contract](contract.md).

## What already exists

- **One basis per finished stage.** `autocode_usage.stage_rows` gives each stage
  exactly one basis, in this order: runner-owned work costs 0; a provider-reported
  cost is **reported**; otherwise tokens at the dated table (`autocode_prices.json`)
  are **estimated**; otherwise the stage is **unknown**. Reported wins over estimated.
- **`cost` in the status view** (`autocode_cost.view`): `spent_usd` is reported plus
  estimated plus `inherited_usd` (spend carried from a checkpoint continuation).
  `complete` is true only with no unpriced or partial stage, no active stage and no
  pending parallel workers. `breakdown.unpriced` names why a stage is unknown:
  `usage_missing`, `model_unknown` or `rate_unavailable`.
- **A zero is not a price.** `autocode_usage.reported_cost` treats a provider total
  of zero as not reported, because a subscription plan reports 0 for work that was
  not free. Such a stage falls back to the dated table, which `docs/cost-reporting.md`
  calls API-equivalent, "not the plan's bill".
- **Attempts and requests counted once.** `usage.accounting` keeps failed,
  interrupted, timed-out, active and report-repair attempts. OpenCode requests are
  paired by session and part (`autocode_request_usage`). Parallel workers are imported
  into the parent once (`autocode_dispatch.account_workers`, then
  `combine_accounting` deduplicates attempt and request identities).
- **Partial usage is a floor.** An interrupted stream sets
  `provider_tokens_partial`; known tokens count and `complete` turns false.
- **Escalation cost is split out.** Each row of `escalations` carries its own
  `cost` with reported, estimated and unpriced attempts.
- **The money cap** (`--max-cost-usd`, `docs/execution.md#money-cap`) is checked at
  every stage boundary before a launch, and before a stronger-route climb using the
  role's last attempt's tokens at the climbed model's rate (`autocode_climb_cap`). The
  doc states its own limit: a run can end above its cap "by up to one stage, or by one
  parallel batch", and the projection "is an estimate, not a bound".
- **A cumulative token limit was retired** on 2026-10-01 (`eec0d088b`,
  `autocode_retired_token_budget`). The only stated reason is that cumulative token
  counts do not stop execution. Nothing here proposes bringing it back.

## Vocabulary and completeness rules

These are the existing names. The two marked "proposed" are additions this record
asks the contract to carry.

| Term | Meaning | Field |
| --- | --- | --- |
| Reported charge | The provider's own positive `cost_usd` for a stage | `cost.reported_usd`; stage basis `reported` |
| API-equivalent estimate | Tokens at the dated table's metered rate. For a subscription route this is not the bill | `cost.estimated_usd` |
| Comparison estimate | Tokens at the frozen comparison rates evidence reports are bound to. Covers two models | `usage.cost_usd.estimated`, `accounting.cost.historical_estimated_usd` |
| Inherited spend | Spend carried from the run a checkpoint continued | `cost.inherited_usd` |
| Unknown | A stage with no usage, no model or no rate. Never zero | `cost.unpriced_stages`, `cost.breakdown.unpriced` |
| Partial | Known tokens from a stream that never reported final usage. A floor | `usage.partial_stages` |
| Active | The running stage. Excluded from `spent_usd` | `usage.active_stage` |
| Subscription consumption (proposed) | Quota or credit a coding plan consumed. Not modelled today | placeholder `accounting.cost.subscription_invoice_usd: null` |
| Rate basis (proposed) | The rate, table date and source in force when the stage finished | none today |

**Completeness rules.**

1. A dollar figure is a total only when `cost.complete` is true. Otherwise it is "at
   least" the known amount, shown with the unpriced and partial counts.
2. An API-equivalent figure on a subscription route is labelled as such. It is never
   presented as the provider's bill.
3. Cost per accepted task uses a complete cost, or reports the known subtotal and
   the gap ([baseline](baseline.md)). A null numerator is not zero.
4. Every attempt counts, including failed, repaired, cancelled and active ones.
   Retries stay charged.

### Example records

E1, three priced stages, one with no usage and one with no rate:

```json
{"cost": {"spent_usd": 0.84, "reported_usd": null, "estimated_usd": 0.84,
  "complete": false, "unpriced_stages": 2,
  "breakdown": {"unpriced": {"usage_missing": 1, "model_unknown": 0,
    "rate_unavailable": 1, "models_without_rate": ["vendor/new-model"]}}}}
```

Read as "at least $0.84; two stages unpriced". This is current behavior.

E2, a coding-plan route reports zero cost and known tokens: the stage's basis is
estimated at the dated table. Today nothing in the record says the route is a
subscription, so a reader cannot tell an API-equivalent figure from a pay-per-token
estimate. The proposed per-stage `billing` marker closes that gap:

```json
{"stage": "terra", "model": "zai-coding-plan/glm-5.3", "basis": "estimated",
 "estimated_usd": 0.31, "billing": "subscription"}
```

## Scenarios against current behavior

| Scenario | Current behavior | Gap |
| --- | --- | --- |
| E1 some priced, one missing usage, one missing rate | Subtotal plus reasons; `complete` false | None in the data. The money cap does not count the unpriced stages |
| E2 zero subscription cost, known tokens | Zero treated as not reported; API-equivalent estimate used | No per-stage subscription marker; quota consumption not modelled |
| E3 timed-out request later reports usage | Known tokens count; the stage stays partial; `unobserved_usage_possible` is set | Late usage is never imported, so the partial figure has no upper bound |
| E4 parallel workers and a retried report repair | Workers imported once; every repair attempt counted | Exact request identity exists only for OpenCode; other transports deduplicate by stage |
| E5 new rate, alias or tariff update | `cost.view`, `exceeded` and `crossing` re-price every stage from the current table | A tariff change rewrites past runs' `spent_usd` and can change whether a resumed run is over its cap. No rate basis is stored per stage |
| E6 crash while a request is in flight | The attempt stays as a row with unknown or partial usage | No statement of possible unseen spend beyond the floor |
| E7 cap approached while parallel calls run | Nothing stops running workers; the next boundary pauses | A climbed parallel Builder batch is not priced before launch |
| E8 no configured cap | All accounting still shown; no limit invented | None |

## Gap list

Each gap names the existing public field it extends and the provider evidence it
needs.

1. **No rate basis per stage (E5).** Store `{as_of, rate, source}` from the dated
   table on each finished stage and price from it. Keep "at today's rates" as a
   separate, labelled recomputation. Needs no provider evidence.
2. **Subscription versus API-equivalent (E2).** Carry the route's billing kind
   (already known during model selection, then discarded) onto each stage. Quota
   consumption needs per-provider evidence: a provider that reports remaining quota
   can fill `subscription_invoice_usd`; one that does not leaves it null.
3. **Late usage after a timeout (E3, E6).** Import final usage only where the
   provider keeps a session record AutoCode can read. Otherwise keep the floor and say
   the true figure may be higher.
4. **Active and in-flight exposure (E6, E7).** `cost` excludes the active stage.
   Add a labelled in-flight estimate (the projection already computed for climbs) so
   a reader sees possible exposure, not just the floor.
5. **Parallel batch not priced before launch (E7).** Price the batch as worker count
   times the projected attempt before launching, as the serial climb check does.
6. **The money cap fails open on unknown spend.** The retired token guard paused on
   unknown usage (`PAUSED_USAGE_UNKNOWN`); the money cap ignores it. See the policy
   levels below.
7. **Cap changes are already recorded.** An earlier draft listed this as a gap. A
   resume that changes any setting, the cap included, appends the previous and selected
   settings to `configuration_changes` (`autocode_run_setup`). It is deliberately not a
   `user_events` entry: AutoResolver requests bind a digest of that list, and the
   deferred-approval bound counts its entries as the person's input.
8. **Cost per accepted task uses reported dollars only.** Covered in [baseline](baseline.md).
9. **No pre-run estimate.** `spend_confirmation` shows fixed text and the plan
   approval brief shows the cap only when one is set. See below.
10. **Documentation drift.** `docs/cost-reporting.md` pointed to comparison rates
    "below" that live in `autocode_usage.REFERENCE_PRICES`, and named one route where
    two are listed. Fixed in #1906.

## Visibility versus enforcement

**Recommendation for the first owner checkpoint:** visibility is the default and
needs only gaps 1 to 4 to be truthful. Enforcement is opt-in company policy,
and each level states the guarantee it actually gives.

| Level | What it does | Actual guarantee | Status |
| --- | --- | --- | --- |
| 0 Visibility | Accounting with gaps, no limit | None needed | Exists (E8) |
| 1 Boundary cap | `--max-cost-usd`: no stage launches once known spend reaches the cap | Known spend can exceed the cap by one stage or one parallel batch. Unknown spend is not counted | Exists |
| 2 Fail-closed cap (proposed) | Level 1, plus: an unpriced or partial stage pauses before the next launch, and a parallel batch is priced before launch | Overshoot bounded by the largest single unit already running, on priced routes. Still not a billing ceiling | Needs gaps 4 to 6 |
| 3 Billing ceiling | A spend limit on the provider account or key | The provider's own guarantee | Provider configuration, outside AutoCode |

Local accounting cannot promise a hard dollar ceiling: a request in flight can
consume more than its projection, and a provider can report late or never. AutoCode
should document Level 3 as the only hard guarantee and point company deployments to
it. No token budget is reintroduced.

## Estimates

A proposal, not a commitment to accuracy.

- **Source.** The project ledger `.autocode/usage.jsonl` already holds every run's
  `summary()` with per-attempt rows. Group finished attempts by role and model.
- **Before planning.** Estimate the planning stages only: the middle half of prior
  attempts' tokens for each planning role on its configured model, at today's dated
  rates. Show it as a range with the sample size.
- **At plan approval.** Add Builder and verification from prior attempts scaled by
  plan size (`planning_depth.plan_size`: milestones and affected paths).
- **Uncertainty.** Fewer than five prior attempts for a role and model gives "no
  estimate" rather than a guess. A subscription route shows tokens and a labelled
  API-equivalent range. An estimate never feeds enforcement except the Level 2 batch
  pricing.
- **Rates change during a run (E5).** Each stage keeps the rate in force when it
  finished (gap 1). A route change under an approved policy is visible in
  `escalations[].cost` and `route_assignments`; its later stages use the new route's
  rate.

## Owner decisions for the checkpoints

1. Visibility only, or Level 2 as an opt-in company policy?
2. Should a project's cap become a non-overridable company setting, or stay a
   default a person can change on resume?
3. Is a pre-run range wanted before planning, at plan approval, or both?
4. Which providers' quota reporting is worth integrating for subscription
   consumption, if any?

## Regressions to keep green

`tests/test_cost*.py`, `tests/test_usage.py`, `tests/test_efficiency.py`,
`tests/test_autocode_request_usage.py` and the escalation view tests.

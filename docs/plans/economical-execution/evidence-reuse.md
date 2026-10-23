# D. Evidence reuse and invalidation (#1892)

Planning input for #1867. Read-only audit of master `08ddeb2a3` (2026-10-22). This record
owns which retained results may cover later work and what invalidates them. Saved-run
migration belongs to [the contract](contract.md); worker lifecycle and receipts after a
crash belong to #1879. No historical state is changed.

## Rules that apply to every type

1. **The runner decides eligibility.** A model's text never grants reuse.
2. **Every reused item names its bindings**, and each binding is checked against the
   current value before reuse. A binding that cannot be checked counts as changed.
3. **Missing provenance means not reusable.** An item from an old run without a
   required identity is re-produced, not trusted.
4. **No time-to-live.** The provider-preflight cache's one hour does not extend to
   verification evidence. Age never makes evidence valid or invalid; its bindings do.
5. **Reuse never resets accounting.** The original attempt's usage and time stay
   counted once; the reuse is recorded as a reuse, not as new work.
6. **Reuse never carries approval.** A retained contract still gets a new revision and
   a new token (`install_draft` always increments the revision), so an old token cannot
   approve it.

## Per-type table

| Evidence type | Exists today | Bindings checked today | Coverage limit | Bindings needed | Proposed extension |
| --- | --- | --- | --- | --- | --- |
| Executed check, regression baseline | Yes (`autocode_regression`) | Base commit, suite command, framework, base-patch tree, timeout, runtime identity (dependencies, environment, interpreter, shell, AutoCode runtime, prompt hashes, platform); file hash and completed receipt verified on reuse | Only the baseline side. JUnit, node commands and unbound editable installs are never reused | As today | None |
| Executed check, clean replay receipt | Yes (`autocode_check_replay`) | The original events' hash, for report repair; approved-plan commands always execute | Reused only within one execution (report repair) | As today | None |
| Provider preflight | Yes (`providers/env_prep`) | Command, directory, full environment, executable identity; one hour | Tool checks only | — | Not a model for verification evidence |
| Report repair | Yes (`autocode_report_repair_queue`) | Original events, before and after refs, schema and output hashes, contract hash; refused if the source or any pin changed | Repairs prose only; reruns no command | As today | None. Already covers D1 |
| Contract and criteria text | Yes, narrow (`autocode_plan_retention`) | A criterion's row digest; a contract's `r{revision}:{hash}`; the trace from the exact producing report | Retains text only; the final review, every guard and the person's approval still run | Criterion digest plus the requirement rows it traces | Retain unchanged criteria across a follow-up turn under the same binding |
| Requirements answers | **No** general reuse | — | A follow-up restarts at Requirements | Question text digest, the source sentences it answered, contract revision it fed | Carry a settled answer forward when its question and sources are unchanged; a changed brief sentence invalidates it |
| Investigator diagnosis | Same turn only | Source revision and output hash | The previous turn's diagnosis is not used as instructions | Source revision of `affected_paths` and `test_paths`, the reproduction's test identity | Reuse within a turn as today; across turns only as context |
| Plan decisions (milestones, scope) | **No** | — | Re-planned every turn | Contract revision, `affected_paths` source revision | Out of scope for the first slice |
| Authored acceptance tests | Partly | Coverage keyed by the whole contract hash | Every due case is rewritten under a new contract, even for unchanged criteria | Criterion digest plus the test file's content hash | Key coverage per criterion, so unchanged criteria keep their tests |
| Review findings | Yes, ledger | Runner-owned identity; only the raiser resolves | Findings persist across tasks; a report-only repair never closes one | As today | None |
| Saved permission answers | Yes | Identical request and contract token; once | — | As today | None |
| Completion evidence | **Never reused** | The gate rechecks source, contract, pins and receipts every time | Correct by design | — | None |

## Same-chat follow-up

What a follow-up re-runs today (`autocode_follow_up.accept`): a `brief_feedback` receipt
records the previous contract token; the run restarts at Requirements; planning
produces a new contract revision, which archives the validation; the person approves
again; acceptance tests are rewritten for every due case; the Builder runs; the full
Tester and the regression proof run again. The regression baseline can be reused,
because its binding does not include the contract.

| Example | Proposed treatment |
| --- | --- |
| D6 small follow-up, original checks still pass | The original passing tests become `guard:` cases. Unchanged criteria keep their text (plan retention) and their acceptance tests (per-criterion key). The new behavior needs its own red-then-green proof |
| D4 requirement changes, source unchanged | Every answer and criterion that traces to the changed sentence is invalidated. Unrelated answers carry forward |
| D2 one module changes | After the Builder changes source, executed checks are not reused: AutoCode cannot prove which behavior a change leaves unaffected. Reuse is limited to planning-side evidence |
| D5 a dependency outside the named files changes behavior | The same rule: source changed, so verification re-executes |
| D3 lockfile, environment or shared fixture changes | The regression baseline's runtime identity already covers dependencies and environment, so the baseline is re-run. Planning-side reuse is unaffected |

## Report repair and retries

| Retry kind | Evidence kept | Evidence invalidated |
| --- | --- | --- |
| Transport retry before any output | Nothing was produced | — |
| Cut stream after tools ran | Not retried automatically for a writing role; the run pauses (`PAUSED_PROVIDER_UNCERTAIN`) and nothing from the attempt is reused | Everything from that attempt |
| Report-format repair | The original execution, through its pinned events | Only the report text |
| Source or requirement change | Planning-side items whose bindings still hold | Validation, replay receipts, regression proof |

## Scenarios

| Scenario | Treatment | Exists? |
| --- | --- | --- |
| D1 wording-only report correction, same proof | Report repair pins the original events | Yes |
| D2 one module changes | Verification re-executes | Yes, by default |
| D3 lockfile or environment change | Baseline runtime identity changes, baseline re-runs | Yes |
| D4 requirement changes, source unchanged | Trace-linked answers and criteria invalidated | Proposed |
| D5 dependency outside named files | Verification re-executes | Yes, by default |
| D6 follow-up keeps passing checks as guards | Per-criterion test reuse plus `guard:` cases | Partly; per-criterion key proposed |
| D7 crash after execution, before the receipt persists | Uncertain; not reused (`autocode_command_receipt`) | Yes |
| D8 nondeterministic or external data | Not reusable; recorded as such | Proposed: a check may declare itself non-reusable |
| D9 retention refused on stale hash | Plan retention refuses a changed digest; review still runs | Yes |
| D10 old run lacks an identity | Not reusable; cumulative usage untouched | Proposed; migration in [contract](contract.md) |

## Explanation vocabulary

The status view already records check observations in `efficiency.observations` with
the kinds `activity`, `reuse` and `suppressed`, and `repeat`, which #1914 records for a
check executed again after the same obligation had a completed receipt. Proposed public
words, mapped onto those kinds:

| Word | Meaning | Existing kind |
| --- | --- | --- |
| reused | Bindings unchanged; the original receipt verified | `reuse` |
| invalidated | A named binding changed; the item was discarded | none; proposed |
| re-executed | Run again because it was invalidated or never reusable | `repeat` when the same obligation already had a receipt (#1914), else `activity` |
| unknown | Provenance missing; treated as invalidated | none; proposed |
| suppressed | Held by a recovery decision | `suppressed` |

Every "invalidated" entry names which binding changed, so a person can see why work
repeated.

## Handoffs

- **To [the contract](contract.md):** items from runs saved before a binding existed
  are not reusable; how such runs present that is a migration question.
- **To #1879:** reuse assumes a receipt is persisted before the item is reused. A
  worker that dies between execution and persistence leaves an uncertain item that is
  re-executed, never reused.

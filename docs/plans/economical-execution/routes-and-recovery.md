# F. Model effort, escalation and routine recovery (#1894)

Planning input for #1867. Read-only audit of master `08ddeb2a3` (2026-10-22). This record
makes no model decision and authorizes no retry or substitution. Selected routes stay
authoritative until the owner agrees a different policy.

**Owner decisions already recorded, kept as inputs:** GPT-6 Astra is used for the
Resolver only, and other roles are not moved to GPT-6 Sol as a blanket choice
(2026-09-28). Live runs and replays route Requirements and the Planner to GLM-5.3 at low
effort (owner instruction, 2026-10-21). Automatic selection runs a GLM-5.3 Tester at
medium effort (`CHANGELOG.md`, 2026-10-21).

## Routes today

**Automatic selection is the default for new runs** (`--auto-select`, `docs/models.md`
"Choosing models automatically"). Each role gets the cheapest qualified route on the
tool's connected accounts, checkers kept independent, per-role flags winning.
`--no-auto-select` keeps the fixed defaults:

| Role | Fixed default | Effort | Ladder |
| --- | --- | --- | --- |
| Requirements | GLM-5.3 | medium | none |
| Planner | GLM-5.3 | high | none |
| Plan Reviewer | GPT-6 Sol | high | none |
| Builder | GLM-5.3 | medium | none; one GPT-6 Sol xhigh attempt when stuck |
| Tester | GPT-6 Sol | high | Sol high, xhigh, max |
| Completion Reviewer | GPT-6 Sol | medium | Sol medium, high, max |
| Resolver | GPT-6 Astra | high | Astra high, xhigh, max |

- **Ladders** (`autocode_route_ladder.EFFORT_LADDERS`) exist for four roles and climb
  one rung from the current one. A route stays put when it is pinned, on another
  provider, off its ladder, on a fixed profile, already climbed for the same struggle,
  or at the end. GLM routes and most automatically selected routes have no ladder.
- **Climb triggers** (`autocode_escalation`): a rejected report after repair is
  exhausted, an abandoned attempt (not after quota or content-filter stops), no
  progress on a non-implement task, and a failing validation. Each climb records its
  trigger, previous and selected route, and outcome (`escalation_outcomes`).
- **Builder retry policy** (`autocode_builder_policy`): one ordinary retry, then one
  stronger attempt, then `PAUSED_BUILDER_RETRY_LIMIT`. The evidence ledger skips plain
  retries on a repeated failure.
- **The only low-first rule** is the low-effort first Builder attempt on small tasks
  (`autocode_light_builder`). Every ladder starts at the configured rung.
- **Route qualification** (`tools/autocode_route_qualification.json`, 2026-10-20) has 16
  entries: 12 qualified and 4 provisional. Of the qualified, 8 cite named AutoCode runs
  and 4 are qualified because they are today's default route. Of the provisional, 3
  rest on owner decisions and 1 on a benchmark alone. Only the route selector reads it.

## Failure classes

| Failure | Recovery | Bound | Human boundary |
| --- | --- | --- | --- |
| Transport error before any output | Automatic retry of the same route | One for native OpenCode; two in a row for a command-provider chain, the second after 30 s; restarts do not renew it | `PAUSED_PROVIDER_UNCERTAIN`, resume |
| Cut stream with partial output | Retried only for read-only planning roles; never for a writer whose tools ran | Same chain | `PAUSED_PROVIDER_UNCERTAIN` |
| Malformed or invalid report | Report-only repair: prose may change, commands are not rerun | Two per cycle; three identical failures stop sooner | `PAUSED_REPEATED_FAILURE`, `--retry-failed-stage` |
| Timeout or crash without progress | Automatic recovery | Three in a row, or the no-progress limit; a saved stage clears the streak | `PAUSED_TIMEOUT_RECOVERY`, `--grant-recovery N` |
| Same incident again with nothing new | Held by the novelty rule | — | A request for new information or a bounded change |
| Quota, content filter, output cap | **Automatic switch** to the next qualified, permitted model on an already permitted account, checkers kept independent (`autocode_route_recovery`), for runs that saved a permitted set. Pinned roles, native runs and workflow jobs ask | Next permitted route | `PAUSED_BUDGET`, `PAUSED_CONTENT_FILTER`, `PAUSED_OUTPUT_CAP`; `--answer route-ROLE=MODEL` |
| Truncated checker report | Report repair | As report repair | As report repair |
| Authentication | None | — | `PAUSED_AUTH`; fix the login, resume |
| Fallback would break independence | Refused | — | `PAUSED_CROSS_MODEL` |
| Lack of causal progress on the Builder | Retry, then the stronger attempt | Builder retry policy | `PAUSED_BUILDER_RETRY_LIMIT` |

**Resets.** A resume restarts the per-cycle report-repair and Resolver attempt counts;
it grants no new recovery allowance or budget (`docs/cli.md`). Network chains, the
recovery streak, exhausted Builder lanes and unchanged repeated failures survive
resumes. A new contract or milestone opens a fresh Builder lane.

## Scenarios against current behavior

| Scenario | Today | Gap |
| --- | --- | --- |
| F1 disconnect before output versus cut stream after tools ran | Different classes: the first retries, the second pauses for a writer | None |
| F2 correct code, missing or invalid report | Report repair keeps the edits and pins the original execution | None |
| F3 repeated wording-only repair | Report repair; never restarts planning | Each resume buys two more repairs; visible only in `user_events` |
| F4 repeated timeout, no progress | Recovery streak and novelty hold stop the loop | None |
| F5 quota failure with no approved alternative | Pauses with the route question | The docs say no automatic switch exists; for automatically selected roles one does |
| F6 fallback would break independence | Refused | None |
| F7 cheap attempt succeeds | No rule adds a review because the attempt was cheap; verification runs because it is an obligation ([verification](verification.md)) | None |
| F8 sensitive scope appears | Nothing escalates on sensitivity | No sensitivity input ([stage policy](stage-policy.md)) |

## Principles for any extension

1. **Fewer stages and cheaper models are separate changes**, measured separately.
2. **Low-first for producers, never for verifiers.** A producer's weak attempt is
   caught by an independent verifier and the runner. A weaker verifier misses defects,
   and nothing downstream catches that. Verifier effort is never lowered below its
   configured rung without its own qualification.
3. **A new call needs a reason that changes the next action.** Each recovery or climb
   records what evidence triggered it and what result would let the run continue.
4. **Nothing replenishes silently.** Failed attempts, selected routes, cost history
   and explicit limits survive restarts and follow-ups.
5. **No unrelated account.** An unknown outcome or provider rejection is never a reason
   to move to an account the run was not permitted to bill.

## Candidate changes, in priority order

| Rank | Change | Why | Owner decision needed |
| --- | --- | --- | --- |
| 1 | Correct `docs/models.md` to describe the automatic switch, and its limits | The docs contradicted the runtime on a cost-relevant behavior | Done in #1906 |
| 2 | A start rung per producer role: Requirements, Planner draft and report repair start at low effort and climb on the existing triggers | The 98 s against 827 s replay; the owner's GLM-low choice for live runs; the light Builder precedent | Which roles, and whether GLM-low becomes the fixed default |
| 3 | Report-format repair at low effort on the same model | A repair rewrites prose against a schema; no new reasoning about the code | Whether a repair may run at a lower effort than its stage |
| 4 | A cheaper first route for routine Resolver work, climbing to Astra on evidence | Two Astra calls were 34% and 37% of their attempts (2026-10-02) | Revisits the recorded "Astra for the Resolver for now" decision |
| 5 | Show the per-cycle repair reset in the status view | F3: repeated resumes spend repairs without a visible count | None |
| 6 | Escalate on sensitivity once [stage policy](stage-policy.md)'s input exists | F8 | Depends on that decision |

## Per-role example

Under candidates 2 and 3, with fixed defaults and nothing pinned:

| Role | Start | Climbs on | Never |
| --- | --- | --- | --- |
| Requirements | GLM-5.3 low | A rejected report after repair, or no progress | Lower than low; another provider |
| Planner draft | GLM-5.3 low | The same, or a Plan Reviewer blocking concern repeated in revision | — |
| Report repair | Its stage's model, low | A repeated identical failure | Rerun commands |
| Builder | As today (light first attempt, then configured, then the stronger attempt) | As today | — |
| Tester, Completion Reviewer | Configured rung | As today | Below the configured rung |

## Comparison design for #1896

- Matched tasks, starting code and providers; change one role's start rung at a time.
- Report per role: calls, reasoning tokens, seconds, repair count, climbs and whether
  each climb passed; per task: completion, false completion and cost per accepted task.
- A cheaper route counts as an improvement only if completion and false completion
  hold and the cost difference exceeds the run-to-run spread on the unchanged route.

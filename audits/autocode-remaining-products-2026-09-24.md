# Remaining product checks — 2026-09-24

Test-only pass, on `codex/autocode-blackbox-scenarios`. No additional production fixes,
merges, or pushes. Earlier production fixes remain unchanged in this worktree.

## C — integrated dashboard: PASS

Tested the actual previously integrated live-Luna candidate, not the earlier orphan
worker UI. Source revision was rechecked after browsing and is unchanged:
`fb805dbb751f742631f452bdab30f0eca461f4107b0eafb8eb9fca2d5c12716a`.
`web/index.html` SHA-256:
`92b4c9178cc9d16082cb6331cc4482b743b8f3b6eadb4a343486a9513bb13c8c`.

Browser assertions were declared before execution: status sequence RUNNING → PAUSED
→ RUNNING → CANCELLED; enabled controls respectively `[true,false,true]`,
`[false,true,true]`, `[true,false,true]`, `[false,false,false]` in Pause/Resume/Cancel
order; no horizontal page overflow or clipped status/buttons/disclaimer.

All **28 checks passed**: four states/actions at widths 320, 375, 390, 480, 768,
1024 and 1440, height 900. Visual screenshots additionally inspected at 320×812
(RUNNING), 375×812 (CANCELLED), and 1440×900 (PAUSED); no clipping observed.
The tab/viewport override and loopback server were cleaned up.

This verifies this small local-simulation dashboard. It does not claim 21 distinct
application screens or a new orchestrator checkpoint-progress UI.

## F — live impossible plan: PASS (safe refusal)

Handwritten plan requires native PostgreSQL LISTEN/NOTIFY on Python sqlite3
`:memory:`, prohibits extensions/emulation/polling/other infrastructure, and requires
an evidence-backed infeasibility report rather than a workaround. Independent README
worker also runs. Actual Luna-medium Builders; no scripted refusal response.

Result in 63 seconds: Builder executed the unsupported SQL and retained
`OperationalError: near "LISTEN": syntax error`. Structured request is `infeasible`;
worker status `PAUSED_ORCHESTRATOR_WORKER`; requested replanning. No integrated
candidate, no self-approved contract changes, no replacement architecture. A local
attempt/proof in an isolated worker is retained, not represented as working software.

Artifacts:
`/private/tmp/autocode-live-impossible-20260924/test_live_impossible_sqlite_plan_refuses_without_substituting_architecture-r3oc_9ft/`.

## D — executed C# vs live Go: PASS after correcting test adapter

The handwritten C# reference executes BEFORE AutoCode. Fixed expected outputs:
`["A:de","B:ca","A:com","B:com","A:","B:DE","A:co.uk"]`.
These cover registry selection, DE/CA overrides, default TLD, empty TLD, case
preservation and multi-label input. Reference files are excluded from Builder scope.
Final Go comparison uses a separate external consumer, not a post-hoc alteration of
the candidate's tests or source.

Microsoft's .NET 8 SDK container runs with networking disabled and only the fixture
directory mounted. SDK image digest:
`sha256:78235e09001f52b6592c458ac010775ebac6725422e80cd0c1650590f67b2743`.
The C# reference executed successfully with the exact expected outputs. All six live
Go tasks built in dependency order (M1 → parallel M2/M3 → parallel M4/M5 → M6), and
the final independent consumer produced exactly the same seven outputs.

**Test-harness issue found:** the initial external consumer assumed exported type
`B`, but the plan only specified Registry B behavior; the Builder validly exported
`RegistryB`. The initial unittest therefore failed after the successful build (~203s).
Its compiler-failure receipt remains in `go-execution.json`. Only the external adapter
was corrected to discover the exported implementation names from their assigned
files. The expected values and product source were not changed. Rerunning the
comparison on the same candidate passed (`go-parity-execution.json`); the entire live
build was not rerun just to change the adapter.

Candidate revision verified unchanged before/after comparison:
`16dfb5d885ad53a2ebca86cdce14484285bdc0ebed8822aca53693427e6426ce`.
Artifacts (including executed C# output, original adapter error, corrected comparison,
all six Builder prompts/reports and integrated project):
`/private/tmp/autocode-live-registry-parity-20260924/test_live_registry_matches_executed_csharp_reference-yj83ka4k/`.

## Additional timeout checks: 4 PASS

Real-process tests passed in 8.8s: tool-deadline cleanup of detached writers, timeout
cleanup of detached processes, hard-cap escalation when a provider ignores SIGTERM,
and watchdog enforcement while process sampling stalls. These complement, but do not
replace, the exact timeout/still-alive public-CLI coverage limit below.

## This pass's result

All three outstanding product checks were executed. No new AutoCode product defect
was demonstrated by them; one false-positive test-adapter assumption was found and
corrected, preserving its original failure evidence. The previously collected open
issues below are not fixed or erased by these passing product checks.

## Collected open issues and coverage limits

1. **No-progress result semantics:** worker may be labelled BUILT with zero changes
   and enter an unverified candidate. No-progress accounting exists; product completion
   remains gated. Existing scenario 15 reproduces this.
2. **Requested escalation policy absent:** the configured Terra ladder is not the
   requested ordinary Luna retry → configured Sol High → pause policy. Scenarios
   21/22 remain capability gaps, not passing implementations of that policy.
3. **Named intra-milestone progress UI not verified/delivered by these tests:** raw
   durable tool checkpoints and retention are verified, but not a user-facing queue
   of implementation/failure-state/screen-matrix/regression sub-checkpoints.
4. **Exact timeout-and-still-alive public-CLI injection remains partial:** real
   orphan ownership and watchdog cleanup are tested separately. Do not relabel
   supporting process tests as that exact controller lifecycle reproduction.

The earlier report-repair/history and pre-spawn-recovery bugs were fixed before this
pass. This pass collects observations without further production edits. Live
Builders use Luna medium; plan import and intermediate review are scripted, not
live evaluations of AutoPlanner or AutoReview. These are synthetic product fixtures,
not evidence of full production-registry migration parity.

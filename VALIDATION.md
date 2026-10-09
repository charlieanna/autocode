# Final no-exclusion campaign gate - 2026-09-28

The exact final source passed the canonical gate with **1260 selected tests,
1247 passed, 13 skipped, zero failures and zero errors** in 1245.814 seconds.
Both exclusion manifests are `{}`; the canonical runner reads
`tools/suite_exclusions.json`. Planning-v2's former 21 methods plus its added
compatibility coverage are selected and passing, not excluded. The final run used
the fresh installed wheel and retained browser/package evidence under
`evidence-campaign-no-exclusions/` and `gate-campaign-no-exclusions.log`.

Final wheel:
`wheels-campaign-final4/autocode_supervisor-0.7.1-py3-none-any.whl`, SHA-256
`42f35aa9f9afc250cced94679b138ce278d68e55566ff7d0aaff9eca8fca956f`.
Packaging fingerprint:
`033f98aa2f5b7f4856435b6d509b08bdd48612ee4069055d561d159edfc4c261`.
All 190 packaged source files matched the 241-input snapshot. The 13 skips remain
native-provider live tests and are not passes.

The final gate itself caught one legacy empty-events edge in new rate-limit
reconciliation; `reconcile_active` now checks that an events path exists and is a
file before classifying it. The focused rejected-active and real rate-limit tests
passed before the successful rerun.

# No-exclusion planning integration and final gate - 2026-09-27

Planning-v2/artifact integration is now explicit opt-in (`--planning-v2` with
joint planning). The default stage/role/model configuration remains unchanged;
Requirements Gatherer, technical Planner and independent reviewer remain separately
configured. Current AutoResolver-only human publication is preserved. Transactional
artifact/delta generation, predecessor verification, rollback-safe persistence,
orphan reconciliation and goal-edit invalidation are connected to the real CLI.

The historical **21 excluded tests now pass**, plus a compatibility regression;
`tests/suite_exclusions.json` is `{}`. The final canonical gate selected **1216
tests: 1203 passed, 13 skipped, zero failures and zero errors** in 1189.510 seconds.
The 13 skips remain explicit native-provider live tests and are not passes. Separate
dashboard Python discovery passed **213 tests** in 29.829 seconds. Real-browser and
fresh isolated-wheel checks were included in the canonical gate.

A bounded reviewer-route fallback was added and tested offline. It preserves all
failed-call/counter evidence and accepts only an already-configured OpenCode route
using GLM-5.3 or MiMo 2.6 Pro, with source/contract/input/settings hashes and at
most one switch per planning cycle. Explicit pins, provider/engine differences,
changed evidence and same-family producer/reviewer pairings fail closed. Under the
actual campaign pairing (GLM Planner, MiMo Reviewer), no switch is eligible: the
only alternate is GLM and would create self-review. AutoResolver therefore escalates
rather than silently changing a route. **11 fallback tests passed** plus existing
activity/model-routing compatibility suites.

The separately named hybrid OpenCode stage router passed its offline forwarding,
allowlist, recursion, repair-owner, receipt and non-laundering checks. It enables
future supported-model equivalents but does not retroactively qualify the 13 skipped
native Codex transport tests. Those remain `NATIVE_CODEX_TRANSPORT_UNVERIFIED`.

Final current-source wheel:
`wheels-campaign-green/autocode_supervisor-0.7.1-py3-none-any.whl`, SHA-256
`174b0491c1e721a52e0c2061dbbf45e1bfd81408a797455cee7fdecd805c4f3f`.
Packaging fingerprint:
`e8275a916345bca4d6e30bfea568644e274c84665c15da8d0b8798e837a9edc7`.
All 189 packaged source files matched the 240-input frozen snapshot. Empty exclusion
file SHA-256: `ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356`.

# Remaining live campaign continuation - 2026-09-27

The remaining campaign resumed against the dirty local checkout, eight commits
behind the tracked remote at inspection. No pull/merge, unrelated self-build run,
or existing failure evidence was changed.

## New live evidence

- **T02 / BUGFIX-01, GLM/MiMo low/low:** the saved plan was checked against the
  frozen synthetic fixture and approved through the actual CLI as a documented
  **simulated test-operator action**, not genuine human acceptance. Only `greet.py`
  and `test_regression.py` were authorized to change. The subsequent real-model
  Builder, Validator, and Completion Owner reached `TASK_COMPLETE`.
- The independent BUGFIX oracle passed **30/30**, including exact stdout/stderr,
  Unicode and arity edges, original/protected file hashes and modes, stdlib-only
  scope, regressions passing the candidate and failing the seed, and no mutation
  of the candidate during scoring. Candidate revision:
  `ed75d6410816d770ea5a1ebcd1e7c94dddc47b005d601d558739e264a6b3805a`.
- This is an **assisted continuation**, following prior runner fixes and stopped
  attempts. It is not one of three fresh unassisted qualification passes. The
  checker used the unchanged oracle hash
  `c91b57fee816b3f6dde00e44d661d435a36fa0a081dab568256bf00f1c76f750`.
- **T02 high/high comparison:** AutoResolver used its next bounded recovery call,
  but MiMo again produced no terminal review and hit the idle watchdog. Three
  retained recovery failures led to a resolver-issued operational request. No
  budget was reset or raised; this comparison is blocked, not delivered.
- **T02 fresh low/low qualification attempt 1:** GLM requirements completed, then
  the GLM Planner exited with `PAUSED_RATE_LIMIT` during `astra_discovery`. No
  Builder ran and no candidate was delivered. The provider-side rate-limit cause
  was retained at `live-evidence/BUGFIX-01/03`; no route, billing, limit or model
  was changed automatically. This counts as a failed fresh qualification attempt,
  not a pass and not evidence about artifact quality. No owned process survived.
- **T02 fresh low/low qualification attempt 2:** the provider returned a concrete
  weekly/monthly 429 exhaustion during `requirements_gather`, including a reset
  timestamp in retained events. The run initially preserved an active stopped
  response and asked the operator to abandon it; this violated resolver ownership.
  AutoResolver now verifies that the process stopped, the event is nonterminal and
  classified as rate limit, source is unchanged and no terminal race exists; it
  archives/accounts the attempt and issues the precise provider/spending request
  without replaying or switching routes. Changed-source and terminal-turn negatives
  fail closed. The actual saved attempt was reconciled with no provider launch;
  its current request is retained in `bugfix-low-fresh-rate-limit-autoresolver2.log`.
- **T03 / FEATURE-01 low/low:** a fresh bounded trial reached a malformed
  requirements report, then its report-only repair timed out. It initially stopped
  at `PAUSED_PROVIDER_TIMEOUT` without consuming the remaining safe repair allowance
  or issuing a resolver-owned request. That gap is now fixed: the original and
  timed-out repair are retained, consumed attempts stay charged, and only the
  remaining format-only repair may run after quiescence/pin/source checks.
- The live repair follow-up succeeded: `requirements_gather_report_repair-02`
  recovered the report, without replaying the original requirements stage or
  implementation. A subsequent accepted discovery report was itself repaired.
  Two MiMo review timeouts then exposed a second lookup bug: recovery grants did
  not recognize an accepted `astra_discovery_report_repair` as the cycle's discovery.
  That semantic-stage lookup is corrected, with negative controls against rejected,
  abandoned or wrong-owner repairs.
- A narrowly bound reconsideration path now lets AutoResolver withdraw its own
  unanswered obsolete planning-budget ask only when existing credit is proved.
  It commits withdrawal and the grant together; it cannot override material,
  answered, paused, stale or explicit-cap requests. Live verification admitted
  `astra_challenge-03`, but MiMo stalled again. The third recorded recovery failure
  correctly produced an AutoResolver human request. **T03 remains blocked before
  implementation**, not delivered. No allowance, role or route was silently changed.
  The oracle summary's generic scope-failure wording includes missing deliverables;
  it is not evidence that protected files changed.

Evidence under `autocode-campaign.VVUDqY` includes
`bugfix-low-operator-review.json`, `bugfix-low-delivery-followup.log`,
`bugfix-low-delivery-oracle.json`, `bugfix-high-planning-followup.log`,
`feature-low-01.log`, `feature-low-repair-followup.log`,
`feature-low-planning-followup.log`, `feature-low-reconsideration.log`, and
`live-evidence/FEATURE-01/01/`. No GPT/Codex model was
invoked; live roles retained only GLM-5.3 and MiMo 2.6 Pro.

The new repair fix passed 72 repair/activity/operational tests and a separate
46-test resolver/intervention/process run. The reconsideration change passed
115 targeted tests; the parent's latest combined repair/reconsideration/operational
rerun passed 60 tests. These runs overlap and must not be summed into a campaign
total. A new full canonical gate after these latest changes is still pending.

## Supported-provider equivalents

The 13 opt-in native Codex cases remain `NATIVE_CODEX_TRANSPORT_UNVERIFIED`;
they were not enabled with a different executable or with GPT models. A static
qualification map identified reusable assertions for separately named OpenCode
hybrid equivalents (scripted setup plus real GLM/MiMo review/build). The adapter
and those live equivalent runs remain pending, not passed. Go 1.25.5 and
`agent-browser` are present. The Docker CLI is present but its daemon is unavailable;
the existing .NET SDK image could not be inspected, and no image was pulled.
Executed C# reference parity therefore has an additional prerequisite blocker.

## Excluded-case assessment

Both previously excluded planning-v2 modules were explicitly executed offline:
**21 methods, 0 passed, 18 errored, 3 failing methods, 0 skipped**. Unittest emitted
5 failure records because one method contains three failing subtests. The deeper
artifact-integrity assertions mostly were not reached due to missing runtime APIs.

Historical planning-v2 implementation exists in `4ff40ca`; the exclusion rationale
was corrected without changing selection. Integrating it as-is would change fresh
planning defaults, tie two independently configured planner roles together, and
restore direct human publication that conflicts with the current resolver policy.
Compatible stage/default/approval integration therefore remains a scope decision,
not a green test result or a reason to weaken the assertions. The other authorized
live and offline campaign work continues independently.

# AutoResolver-owned budget and human escalation - 2026-09-27

Implemented the revised policy: roles stage private requests, AutoResolver
evaluates permitted recovery, and only its bound publication becomes actionable
in CLI/chat/dashboard/task aggregation. Initial/final plan approval, permission
and artifact-review safeguards remain human decisions; asking does not grant
execution authority. Project-free intake now uses the request-only resolver adapter.

New runs distinguish runner defaults from explicit CLI bounds, including
abbreviated options. AutoResolver may extend a proven productive internal default
once under finite policy ceilings, recording an additive extension rather than
resetting usage or failure history. Explicit/unmarked saved caps, provider/spending
restrictions, unknown usage, stalled work and pending interventions remain protected.
The CLI integration regression uses a deliberately small trusted deployment default
to force the boundary: verified milestone progress completes after one extension,
whereas the identical CLI-explicit bound produces a resolver-owned escalation.

Operational responses require the exact request ID/token. Corrective information
returns to AutoResolver without implying another call, goal approval, permission
or a budget increase. Repeated launch on the unchanged answered frontier does not
repeat the same question. Separately explicit administrative bound/worker/failure
retry actions are validated and supersede only their corresponding operational ask.

## Verification

| Check | Result |
| --- | --- |
| Final canonical gate | **1151 selected; 1138 passed; 13 skipped; 0 failures; 0 errors**, 1003.949 seconds |
| Separate full dashboard Python discovery | **213 passed**, 27.370 seconds |
| Existing planning-v2 exclusions | **21 tests across 2 modules**, unchanged |
| Real-browser and installed-wheel checks | Included in the canonical gate |
| Browser scope | 21-screen accessibility matrix and lifecycle checks; forced-colors browser emulation remains NOT_VERIFIED |
| Hosted model requests for this change | None |

Evidence remains beneath
`/var/folders/n2/dv1vdhq91fj3dzzffg704bjh0000gn/T/kilo/autocode-campaign.VVUDqY/`:
`gate-human-budget-final.log`, `evidence-human-budget-final/`, and
`a11y-resolver-fixtures/m2-scenario-matrix-manifest.json`.
The failed intermediate `gate-human-budget.log` is retained and is not passing evidence.
Most intermediate errors were old fixtures that attempted approval/publication
without the new boundary. Assertions were migrated to genuine issued receipts;
budget, evidence, source, permissions and late-input invariants were not disabled.

Final wheel SHA-256:
`80c9bf756d3dd7b924ca8c320291885c80fac17c4d507b340add8e4734ea115a`.
The 233-file packaging-input manifest fingerprint is
`a0aa6262a79e18b4dcf78fd4a50af7105200b78aed5ee62979f0545eb732ffa6`.
All 182 packaged source files matched the frozen snapshot. The current source
manifest was rechecked after the successful gate.

These results qualify the core policy and its supported publication surfaces, not
the unfinished live T02-T11 campaign. The 13 skipped live cases and 21 exclusions
are not passes. Legacy standalone design still emits an internal blocked/rework
result because it has no persisted human-response/resume API; it does not fabricate
a code contract or publish an unanswerable human request. Changes are uncommitted
until explicitly requested otherwise.

# AutoResolver recovery verification - 2026-09-27

The low/high MiMo traces were inspected, including correlated local provider
message/part records. All observed tool calls finished with allowed read/glob/grep
permissions. Each stalled response ended at a new step with empty reasoning and
no terminal report or recorded provider error. This locates the observed silence
at the provider/client streaming boundary; it does not prove a service outage,
network failure, permission denial or context-limit rejection.

A confirmed runner defect was that planning retry prompts omitted the saved
`recovery_context`. Another gap let operational timeouts consume the ordinary
two-call planning allowance without an AutoResolver-owned recovery path before
goal approval. Both paths are now corrected with bounded, pinned, single-use
recovery grants. Ordinary counts and limits are retained; completed recovery does
not erase the failures that funded it. Runtime stage ownership and diagnostic
receipts also cover automatic timeout/capacity/workspace-path recovery.

**Live verification:** the preserved low/low BUGFIX-01 run was invoked normally
with `--no-chat`, without `--resume-paused`, a review-limit override or manual state
edits. AutoResolver admitted the third MiMo challenge using a recovery grant;
GLM revised the plan; a second grant admitted final independent MiMo review.
All three stages completed. The run is now **AWAITING_GOAL_APPROVAL**, with
**four attempted review calls: two ordinary slots plus two consumed recovery
grants**. Original failed attempts remain archived. No implementation or protected
application-file change occurred; only owned `.autocode` artifacts were written.
No owned provider/runner process survived the completed invocation.

The displayed draft is revision 4, token
`r4:a2089d4393d59baaf1a1791ec9e998839683c25794149fe5d289f670c20e0b16`.
The remaining gate concerns the actual proposed plan, not an operational retry or
budget decision. No approval was fabricated. The high/high blocked run remains
unchanged. The original failed-trial bundles are retained as historical evidence;
this is an assisted follow-up after a runner fix, not a clean delivered T02 pass.

Evidence: `resolver-low-recovery.log` and the saved low/low run beneath the retained
`autocode-campaign.VVUDqY` root documented below. Resolver receipts:
`201764e9fd9cba4dcdd2f33c762f690cf3b90b4bb877eb9940d067ea7c0b20ba`
and `5432bc7ab169f3e9cb1fca29001fe835ac2c72733696e07045c8129d1d5efb86`.

Before the live follow-up, **180 offline tests passed** across operational
recovery, resolver runtime/policy, activity, planning, report repair, core runner,
and explicit retry suites. Tests exercise one/two failed ordinary calls, separate
recovery accounting, durable consumption and pin checks, no implementation before
approval, operational-stop restart refusal, and preservation of real user decisions.
The final canonical gate completed in **980.240 seconds**: **1082 selected cases,
1069 passed, 13 skipped, zero failures and zero errors**. The 21 pre-existing
planning-v2 exclusions remain unchanged. Real-browser and isolated installed-wheel
checks were included. The skips remain explicit opt-in live-provider tests, not
delivery passes; this does not claim that all remaining live ladder cases have run.

`gate-resolver-final.log` and `evidence-resolver-final/` retain the final results.
The prior `gate-resolver.log` is retained as a failed intermediate run: one stale
manual-resume assertion failed in 12 subcases, one stage-count assertion omitted a
new resolver receipt, and three catalogue cases exposed missing production-helper
dependencies in dashboard VM fixtures. Those tests were repaired without disabling
the cap, dropping provider-history checks, or weakening UI behavior assertions.
No dashboard production code was changed by these test-fixture repairs.

Final wheel SHA-256:
`97ac3e989263baa9cfe737eef3031bb80a67da96eee2007a4ba9c22f54b9506a`.
Its 224-input source-manifest fingerprint is
`547ddcfc62fbaa4cff3dc5785f5cf7d363bab147d25ba60f13abcc8ff5ef5c45`.
Provenance and the frozen snapshot are retained as `wheel-build-resolver-final.json`
and `wheel-source-resolver-final/` under the campaign root. All 175 packaged source
files matched that snapshot. No source exclusion was added to make this run pass.

# Live campaign follow-up - 2026-09-26

The next campaign step added reproducible synthetic `BUGFIX-01` (plan T02) and
`FEATURE-01` (T03) cases, including protected original tests/data, exact behavioral
checks, source-scope checks, and regression tests that must fail the unchanged seed
and pass the corrected artifact. Both oracles operate on disposable scratch copies;
this is not an OS sandbox. The combined canonical harness/oracle check passed
**46 tests** (`tools.test_live_trial tools.test_campaign_oracles`, 35.405 seconds).
The full 1042-case baseline below predates these additions; a full post-addition
gate has not been run, and the 46 are not additional full-gate passes to sum into it.

OpenCode 1.18.32 catalogue preflight confirmed the configured routes advertise:
- `zai-coding-plan/glm-5.3`: low, high, max (not medium).
- `xiaomi-token-plan-sgp/mimo-v2.6-pro`: low, medium, high.

New campaign profiles pin those variants and only those two models. The old
`glm53-mimo` profile remains a historical record, not proof of GLM medium behavior.
New trials pin finite iteration/active/stage/tool/idle/milestone limits; subsequent
invocations do not reset them. Live human questions/approvals and operational pauses
are not answered, approved, or retried automatically. Each result records the
scored candidate revision and oracle hash; oracle/candidate changes during scoring
are errors, not successful delivery.

## Live attempts

| Case | Profile | Outcome | Delivery |
| --- | --- | --- | --- |
| T02 / BUGFIX-01 | `glm53-mimo-low` | `HONEST_BLOCKER`, `PAUSED_PLANNING_BUDGET` | Not delivered |
| T02 / BUGFIX-01 | `glm53-mimo-high` | `HONEST_BLOCKER`, `PAUSED_PLANNING_BUDGET` | Not delivered |

Both trials used seed tree `7ce131d9412be16e4d8ec6bb842680da7fdea5ed` and oracle
SHA-256 `c91b57fee816b3f6dde00e44d661d435a36fa0a081dab568256bf00f1c76f750`.
Each had eight iterations, 1800 active seconds, 600 stage seconds, 300 tool
seconds, 180 idle seconds, a 2700-second CLI-driving deadline, and the runtime's
unchanged two-call planning-review budget. Both exhausted that budget after the
MiMo reviewer produced non-terminal attempts and hit the idle watchdog twice.
GLM requirements/planning completed, but neither trial reached implementation.
Provider-side root cause is unverified; the observed timeout is not proof of a
model-quality failure or an authentication error.

There was no false completion, no protected-file change, and no surviving owned
trial runner/provider process was found after exit. The oracle's failed check was
the absent `test_regression.py`; its generic "scope or protected-file violation"
summary must not be read as evidence of an actual protected-file edit. These
positive delivery cases count as **zero delivered out of two attempts**, not as
passing negative tests. No quality ranking between reasoning levels is supported.

Evidence under the retained campaign root below:
- `model-preflight.json`: advertised routes/variants and caveats.
- `bugfix-low-01.log`, `live-evidence/BUGFIX-01/01/`: first attempt.
- `bugfix-high-01.log`, `live-evidence/BUGFIX-01/02/`: comparison attempt.
- `bugfix-low-01/project/.autocode/runs/20260926-155720-fix-only-the-blank-name-validation-bug-in-the-ex-001095cb/`.
- `bugfix-high-01/project/.autocode/runs/20260926-160741-fix-only-the-blank-name-validation-bug-in-the-ex-4d72cdc3/`.

**Blocked:** the saved pause requires an inspected, explicit planning-budget or
recovery decision. No additional review allowance, feedback cycle, or alternate
provider route was silently authorized. T03 has qualified offline controls but
no live attempt. T04-T11, three-pass qualification, the remaining comparison
profiles, the 13 skipped live-provider tests, and outstanding fault-matrix coverage
remain unverified. The 21 pre-existing planning-v2 exclusions remain unchanged.

# Testing campaign recovery baseline - 2026-09-26

The current working-tree canonical gate (`.venv/bin/python -B tools/run_suite.py
--verbosity 2`) completed in **1020.936 seconds**: **1042 selected test cases,
1029 passed, 13 skipped, zero failures and zero errors**. The 13 skips are the
explicit live-provider cases in `test_autoreview_products` (11) and
`test_build_remaining_products` (2); they are not delivery passes.

The existing exclusion file was unchanged: `tools.test_planning_artifacts` (8)
and `tools.test_planning_flow_v2` (13) remain excluded because their requested
planning-v2 runtime is not implemented. These **21 excluded tests** are separate
from the 1042 selected cases and remain unverified.

This run used isolated HOME/config/registry/artifact directories, cleared live
provider toggles, and a freshly built wheel for installed-CLI compatibility.
It included the actual browser lifecycle and accessibility suites, not just Node
DOM stubs. Scoped catalogue results were T12 **14 passed**, T13 **15 passed** with
the isolated wheel; these are included in the gate, not extra passes to add to it.
The documented UI-02/UI-03/UI-13 coverage limitations remain in force.

Repairs verified by this baseline:
- Restored bounded report repair (0-2 attempts; absent configuration means off),
  retained attempt counts on resume, and the three-attempt aggregate recovery cap.
- Strengthened abandonment tests around the intentional Builder retry route,
  retaining evidence, approvals, explicit-resume and fresh-validation gates.
- Corrected explicit failure retries to preserve the complete failure ledger and
  permit only one in-memory authorization. A fourth identical failure pauses
  immediately; restart cannot reuse the audit record as a retry grant.
- Fixed short-socket isolation for real browsers and honest BLOCKED_ENV reporting;
  installed-wheel cases no longer depend on modifying the user's global CLI.
- Qualified live-driver remaining CLI deadline, owned-process cleanup, durable
  error evidence, and refusal to fabricate real human-review approval.

Retained local evidence root:
`/var/folders/n2/dv1vdhq91fj3dzzffg704bjh0000gn/T/kilo/autocode-campaign.VVUDqY/`.
`gate-foreground.log` contains the complete successful run. `gate.log` is an
earlier interrupted background attempt with no summary and is not passing evidence.
The precise reason for that background termination is unverified.

The frozen wheel-source fingerprint is
`818989d476af991d066ed7d1aefef8301c9b19d9ee1e93ce0eb1223d9fb2462b`;
wheel SHA-256 is
`cfad463ba72bd83aa4a215064d9b382c207844aa542e56a8b027174e5092cb7b`.
Build metadata and the per-file manifest are retained as `wheel-build-current.json`
and `wheel-source-current.sha256`. All 173 packaged source files matched the
frozen inputs. Dependencies were staged separately; test installation was offline.

**Campaign not complete:** no hosted model request was made in this recovery
pass. Live T02-T11, repeated qualification, reasoning-level comparisons, and the
remaining fault-matrix coverage still require evidence. The live driver's deadline
currently bounds CLI driving, not setup/oracle execution or cleanup grace; runtime
iteration/time budgets must be pinned before further live launches.
# Report-repair fix and bounded retest - 2026-09-26

The missing-report failure found by the earlier campaign is corrected locally:
terminal assistant text and parsed reports are preserved before validation,
archived with stable paths/hashes, and supplied inline with the exact error. A
later repair gets the latest rejected draft while the original execution history
remains immutable. Legacy recovery, tamper guards, and complete prompt limits
have regressions. See [report-only repair](docs/execution.md#report-only-repair).

The combined runner, provider, repair, planning, scenario-oracle, browser, and
live-harness suites passed **422 tests in 311.160 seconds**, with no skips.
The report-repair suite contains 43 tests, including 15 new regressions.

One fresh `BUGFIX-01` live trial used `glm53-mimo`, `--budget-stages 16`, and
`--timeout 1200`. It progressed to MiMo's `terra` Builder stage, unlike the earlier
planning-only attempt, but the deadline stopped it before delivery. Its verdict is
**ERROR**, and the independent oracle still fails **12/17 checks passed**, because
the greeting bug remains and the regression tests were not delivered. This is not
an end-to-end PASS or evidence that the workflow is now production-ready.

The live trace does exercise the corrected path: `glm_revise` was rejected at
`22:29:21Z` because a `code_refs` entry combined a file path with explanatory text.
Its report-only repair ran from `22:29:22.575632Z` to `22:30:34.020029Z` and was
accepted on the first attempt, about **71.4 seconds**. The repair read only
`.git/info/exclude` and the first 20 lines of the run state, plus four glob calls;
it did not read raw JSONL or old prompts to recover its report. MiMo finalized the
plan at `22:34:22Z`, the harness approved that exact plan, and Builder started at
`22:34:26Z`. Most of the 20-minute window was therefore still spent planning, leaving
approximately two minutes for Builder. The specific repair path improved; delivery
latency remains unresolved.

The completed audit confirms the archived rejected JSON was **27,265 bytes**, with
SHA-256 `bce635628560290118e6d6d73a5a131eedd99c9a4132520702f2bb8ed47c8d90`.
The repair handoff carried that exact full draft and error; the final decorated
prompt was **64,639 bytes**, within the 256 KiB cap. The accepted repair changed
only `code_refs` to bare paths and added a summary explanation; its contract and
responses were unchanged. All nine archived artifacts existed.

Timing identifies the remaining bottleneck more precisely: MiMo's initial plan
review took **484.718 seconds**, and finalization took **227.510 seconds**. Repair
was only **6.704%** of the 1,065.384 seconds of completed planning stages. Builder
performed ten inspection/baseline shell tools, including failed `rtk` calls and
fallbacks, but made no changes to `greet.py`, `test_greet.py`, or `README.md`.

Six finalized provider stages reported **522,200 input + 65,893 output = 588,093
tokens**. Six finished-step receipts from the unfinished Builder add **188,351
input + 1,545 output = 189,896 tokens**, giving an observed incomplete lower bound
of **777,989 tokens** for this retest. Input includes cached input and output
includes reasoning; those subsets must not be added again or treated as a dollar
charge. Builder's last step and final usage remain missing, not zero. No additional
live requests were made to complete this audit.

The driver stopped the CLI/provider descendants; a final process inspection found
none associated with this attempt. No deployment or full-matrix rerun was performed.
Evidence is retained at
`.tmp-autopilot-testkit/live-20260926-glm-mimo/repair-check/evidence/BUGFIX-01/01/`,
with the candidate under `repair-check/BUGFIX-01/project/` and run ID ending
`29d05f5c`. These artifacts are local and gitignored; the earlier campaign remains
unchanged below.

The trial used base commit `22a720049e739f996c7eec8e2cba9fcd52a1325a` plus uncommitted
repair/harness changes, not the original committed implementation. File SHA-256
identities taken before the run:

| File | SHA-256 |
| --- | --- |
| `tools/autocode.py` | `35216273b4fed36c9532cdcfee6a71d4e7d98a9941ba6482f9935d8d1f46cad7` |
| `tools/providers/opencode.py` | `9c0c97d829f72c54e0a009a243e2bfbd29a0eb18ca9358929a7c536bbc9096d3` |
| `tools/providers/command.py` | `607c55540ec243941e217af4211bb31893de803784fb4d5b701e83ba638192eb` |
| `tools/live_trial.py` | `1a949f7b274bccdf5497c2c93688fa99969968c2558314e8b2c7977f19f3adbc` |

# Task-type live baselines - 2026-09-26

User-authorized live trials used the existing `glm53-mimo` profile: GLM 5.3 for
requirements/planning/validation/completion and MiMo v2.6 Pro for plan review,
building and repair. This is one mixed-model workflow, not a head-to-head model
comparison. No deployment was authorized or attempted. **Zero of five scenarios
produced a completed delivery.** No builder, validator or completion stage was reached.

| Scenario | Route | Driving window | Observed outcome | Post-stop independent oracle |
| --- | --- | --- | --- | --- |
| BUGFIX-01 | run | 1,300s outer shell limit | Externally interrupted in planning; seed unchanged | FAIL, 12/17 checks passed |
| FEATURE-01 | run | 1,300s outer shell limit | Externally interrupted in report repair; seed unchanged | FAIL, 11/17 checks passed |
| ARCH-01 | run | 900s shared driver deadline | ERROR in discovery report repair; no deliverables | FAIL, 0/6 checks passed |
| PROGRAM-01 | program | 1,200s shared driver deadline | ERROR planning the first contracts workstream; nothing merged | FAIL, 1/21 checks passed |
| UI-01 | run | 900s shared driver deadline | ERROR in plan-review report repair; no HTML delivered | FAIL, 0/1 checks passed |

Check counts are the rows actually reached, not comparable model scores: missing
artifacts short-circuit some oracles, while unchanged seeds already satisfy some
checks. None of the runs claimed completion, so none is `FALSE_COMPLETE`.
The first two attempts did not finish the old driver's reporting path; their
external interruptions and separate score-only reports must not be relabeled as
completed live-trial runs. An additional, earlier BUGFIX-01 launch was aborted by
the managed background-process infrastructure and is retained separately.

## Source and harness provenance

- Runner/scenarios under test: PR #12 commit `22a720049e739f996c7eec8e2cba9fcd52a1325a`.
- First BUGFIX/FEATURE launches used the clean committed driver, SHA-256 `f1d330eb2aea7e1db5d20f639aed93985457b5bea8d4b3050d7708d678396209`.
- Those trials exposed that `--timeout` was renewed for every CLI invocation and exceptions left no final report. Their requested 1,200s driver limit therefore did not enforce a whole-driving-phase deadline; the 1,300s outer shell bound stopped them.
- ARCH/PROGRAM/UI used the same runner and scenario code with a dirty harness-only correction. Driver SHA-256: `1a949f7b274bccdf5497c2c93688fa99969968c2558314e8b2c7977f19f3adbc`, also saved in `live-trial.json`.
- The correction shares remaining time across CLI steps, kills provider descendants on timeout, preserves error reports and post-stop oracle checks, uses the documented human-review CLI flags, and stops at unhandled pauses rather than blindly resuming. It also removes a hardcoded four-milestone requirement from planning-budget feedback.
- Six real-Chromium UI reference tests passed before live execution. This establishes browser/oracle prerequisites, not success of the live UI candidate.
- OpenCode was `1.18.32`; models used the configured `zai-coding-plan/glm-5.3` and `xiaomi-token-plan-sgp/mimo-v2.6-pro` subscription routes. ARCH stopped before reaching MiMo; the other scored attempts invoked both models in planning roles.
- After the harness corrections and baseline-record updates, the combined offline
  runner/oracle/live-driver/core suites passed **312 tests in 199.438 seconds**, with
  Chromium enabled and no skips. `git diff --check` also passed. These regression
  results do not change the unsuccessful live-delivery outcomes above.

## Evidence and usage

All raw artifacts are retained locally under
`.tmp-autopilot-testkit/live-20260926-glm-mimo/` in the PR worktree. They are
gitignored and are not uploaded with this documentation:

- `evidence/BUGFIX-01/01/`: infrastructure-aborted initial launch, no finalized trial report.
- `evidence/BUGFIX-01/02/` and `BUGFIX-01-rerun/project/.autocode/runs/`: externally interrupted rerun.
- `evidence/FEATURE-01/01/` and `FEATURE-01/project/.autocode/runs/`: externally interrupted feature attempt.
- `score-only/BUGFIX-01/01/` and `score-only/FEATURE-01/01/`: independent scores of the unchanged delivered workspaces after interruption, not additional live-model attempts.
- `evidence/ARCH-01/01/`, `evidence/PROGRAM-01/01/`, `evidence/UI-01/01/`: deadline ERROR reports, source/profile/driver identity, raw trace and final checkpoint snapshots.

The following observed token subtotals include cached input and reasoning output
once, not as additional billable tokens. Missing usage is **not zero**, and these
are not final consumption or dollar-billing totals:

| Attempt | Finalized-stage input / output | Additional raw partial input / output |
| --- | ---: | ---: |
| Initial BUGFIX infrastructure interruption | 12,349 / 2,308 | 9,718 / 323 |
| BUGFIX rerun | 688,542 / 78,053 | 57,979 / 682 |
| FEATURE | 433,066 / 70,693 | 496,124 / 13,084 |
| ARCH | 122,030 / 57,812 | 498,406 / 9,871 |
| UI | 174,026 / 41,840 | 527,931 / 9,759 |
| PROGRAM contracts child | 399,660 / 73,358 | Finalize invocation incomplete; not included |

The observed lower bound across these six launches is **3,777,614 tokens**,
including cache reads and reasoning. Incomplete requests and any unreported usage
remain unknown. No further live retries were launched after these bounded attempts.
Final process checks found no surviving provider/CLI processes belonging to this
baseline root. The retained `RUNNING` checkpoint fields describe interrupted saved
state, not continuing model spend.

## Main findings

1. Planning and schema repair consumed the entire delivery budgets, including for
   the small bugfix. Offline workflow success did not predict model-driven delivery.
2. Repairs repeatedly searched for archived or missing report files, then tried to
   recover long reports from JSONL. `Ripgrep JSON record exceeded 65536 bytes`
   appeared in multiple attempts. Repair input should be materialized and bounded
   rather than requiring the model to reconstruct it from large event logs.
3. Generated plans introduced file-set checks incompatible with runner metadata
   (`.autocode/`, `__pycache__/`) and representation-sensitive requirement comparisons
   (literal `\\t` versus a tab). Guardrails must distinguish these from scope changes
   without silently weakening actual requirements.
4. The next useful experiment is after addressing these planning/repair bottlenecks,
   not simply extending the time limits or adding more program orchestration features.
   These results do not establish that either model cannot solve the underlying tasks.

## Bugfix and feature details

Both substantive attempts stopped at approximately `2026-09-26T19:25:55Z`, with
saved `PAUSED_INTERRUPTED`, exit `-15`, and `timed_out: false`. They were interrupted
by the outer command limit, not by a recorded provider-stage timeout. The bugfix
spent 1,289.750 saved active seconds and the feature 1,289.548; both remained in
iteration 1 with unapproved contracts and no changed candidate source files.

The bugfix revision first cited a transient `.autocode/active-processes.json` and
then attempted protected acceptance-criterion changes without a saved answer. Its
second repair asked whether exactly runner/cache artifacts could be excluded from
the no-new-files check; the harness recorded that answer. The next MiMo review
was unfinished when interrupted.

The feature clarified that the existing usage line must remain unchanged. A
revision then changed literal `\\t` text to tab characters in a protected criterion
and was rejected. Finalization subsequently included a question ID among concern
decisions, violating the one-decision-per-concern contract. That report repair
remained unfinished after 338.905 seconds. These are planning/report failures,
not observed errors in a model-delivered implementation.

## Program details

PROGRAM-01 ran from `2026-09-26T19:32:22Z` to `19:52:22Z`, using
`--mode program --budget-stages 32 --timeout 1200`, maximum parallelism 2, and
no deployment authorization. Only the `contracts` child started; all six other
workstreams stayed pending. The harness answered whether contract JSON should
contain exactly `type` and `required`; a discovery report dropping that unresolved
question was rejected and repaired. Both models subsequently participated in
planning, but `astra_finalize` was unfinished at the deadline. No workstream merged,
and the scored integration worktree was empty.

The retained parent status `WAITING` and child `RUNNING` are the last saved
checkpoints, not completion. The child's raw state is under
`PROGRAM-01/project/.autocode/worktrees/program-order-system-18494b-contracts-3964ecd6/.autocode/runs/20260926-123222-program-workstream-contracts-code-of-program-ord-94221163/`.
`evidence/PROGRAM-01/01/usage-audit.json` contains the tool-behavior audit, whose
dimension labels and zero-filled incomplete-stage totals must not be mistaken
for a delivery PASS or complete billing data.

# Architecture task-type live baseline - 2026-09-26

One authorized live `ARCH-01` attempt ran against the four-component
order-processing architecture task using the ordinary code runner (`mode=run`,
`target=code`). The profile was exactly `glm53-mimo`, with provider `opencode`
and joint planning:

- `zai-coding-plan/glm-5.3` (GLM below): requirements at `medium`, planner and validator at `high`, completion at `medium`.
- `xiaomi-token-plan-sgp/mimo-v2.6-pro` (MiMo below): reviewer and resolver at `high`, builder at `medium`.

Source provenance: commit `22a720049e739f996c7eec8e2cba9fcd52a1325a`
(`22a7200`), branch `fix/pr12-correctness`, dirty worktree. The dirty, patched
`tools/live_trial.py` used for this attempt had SHA-256
`1a949f7b274bccdf5497c2c93688fa99969968c2558314e8b2c7977f19f3adbc`.
The recorded source is therefore not a clean-commit-only baseline.

The invocation used `--budget-stages 16 --timeout 900` and
`--i-authorize-live-model-spend`, with workspace
`.tmp-autopilot-testkit/live-20260926-glm-mimo/ARCH-01`. It ran in a normal
blocking shell with a 1,100,000 ms outer timeout, not a background-process
manager. The driver shared the **900-second wall-clock budget** across its CLI
invocations. Recorded trial start was `2026-09-26T19:32:16.380688+00:00`;
result finish was `2026-09-26T19:47:16.503168+00:00`, an elapsed
**900.122480 seconds**.

**Outcome and delivery limits**

- Verdict: **ERROR**, `runner-driving wall-clock budget exhausted; stopped CLI and its provider descendants`.
- `result.json` records **0 checks, 0 failed** and `assertions.json` is empty. These error-path counts are not a pass. The post-exit oracle in `live-trial.json` records **0/6 passed, 6 failed**: the ADR, its four required sections, and valid `architecture/components.json` were absent. Further architecture checks were not reached.
- No architecture deliverables were produced; the candidate workspace contained only `.autocode/` and `.git/`. The run never reached review, builder, validator, or completion stages. There was no approved goal contract and the acceptance-criteria list remained empty.
- The first discovery report was rejected for dropping unresolved questions `Q1-BEHAVIORAL-WIRING` and `Q2-OWNS-SCOPE`. Its report-only repair restored the questions; the harness answered their proposed defaults (structural-only wiring and exactly `services/<id>/` ownership), then resumed within the same attempt.
- The second discovery report was rejected with `$: missing code_refs`. Its automatic `astra_discovery_report_repair-02` stage was unfinished at the deadline.
- Repair events show a missing archived `astra_discovery-02.json`, truncated reads of the report embedded in JSONL, repeated searches for a usable report copy, and `Ripgrep JSON record exceeded 65536 bytes`. No final repaired report was saved.
- The saved final and raw state remained `RUNNING / REPORT_REPAIR`, iteration 1, with the active repair stage last reporting 170.8 seconds elapsed. Persisted `active_seconds=721.9777953739995` accounts for the four finished invocations only, not full wall time or the unfinished repair. This persisted status does not indicate a surviving process.
- Post-exit checks found no command lines referencing this ARCH-01 workspace and none of its recorded active-stage PIDs (`96236`, `96371`, `1745`). No additional cleanup was needed. There was no second attempt, forced gate/resume outside the harness, manual candidate/source edit, or deployment; deployment was not authorized.

**Measured stage usage**

The actual invoked model was GLM for one requirements invocation (`medium`)
and four discovery/report-repair invocations (`high`). These are five stage
invocations, not a count of HTTP requests. MiMo was configured but never
invoked, so this attempt does not establish a completed two-model workflow.
Input below includes cached input; output includes reasoning. Cached and
reasoning subtotals must not be added again.

| Stage | Invoked model | Input tokens | Output tokens | Total tokens | Recording |
| --- | --- | ---: | ---: | ---: | --- |
| `requirements_gather-01` | GLM | 12,498 | 6,064 | 18,562 | Finished |
| `astra_discovery-01` | GLM | 36,307 | 20,171 | 56,478 | Finished invocation; report rejected |
| `astra_discovery_report_repair-01` | GLM | 35,882 | 16,834 | 52,716 | Finished |
| `astra_discovery-02` | GLM | 37,343 | 14,743 | 52,086 | Finished invocation; report rejected |
| `astra_discovery_report_repair-02` | GLM | 498,406 | 9,871 | 508,277 | Partial: 14 recorded `step_finish` events |
| **Observed subtotal, incomplete** | | **620,436** | **67,683** | **688,119** | Not a final run total |

The four finished invocations account for **179,842 tokens** from persisted
stage metrics. The unfinished repair has no final stage metrics/report; its
partial usage above is summed from raw events. A subsequent `step_start` has
no matching usage record, so remaining usage is **missing, not zero**. The
observed subtotal is a lower bound and includes **487,488 cached-input** and
**47,526 reasoning-output** tokens as subsets. Provider request/retry counts
are unavailable (`null`), not zero; no final billing or dollar-cost claim is made.

**Retained artifacts**

All paths in this list are relative to
`.tmp-autopilot-testkit/live-20260926-glm-mimo/` in this worktree:

- Evidence bundle: `evidence/ARCH-01/01/`, including `result.json`, `live-trial.json`, `environment.json`, `trace.jsonl`, `assertions.json`, `state.final.json`, `state.gate.1.json`, and `state.gate.3.json`.
- Raw run state: `ARCH-01/project/.autocode/runs/20260926-123216-design-the-architecture-for-a-small-order-proces-d82c1a21/state.json`.
- Stage artifacts: `ARCH-01/project/.autocode/runs/20260926-123216-design-the-architecture-for-a-small-order-proces-d82c1a21/iterations/001/`, including both archived discovery attempts, the finished requirements/repair artifacts, and `astra_discovery_report_repair-02.jsonl` used for partial accounting.
- Candidate workspace: `ARCH-01/project/`; only runner/Git metadata remained, with no delivered architecture, contracts, services, or documentation.

# UI task-type live baseline - 2026-09-26

One authorized live `UI-01` attempt ran against the frozen `design/spec.json`
reference using the ordinary code runner (`mode=run`, `target=code`). This was
not a program `ui` workstream or a live Figma build. The profile was exactly
`glm53-mimo`, with provider `opencode` and joint planning:

- `zai-coding-plan/glm-5.3` (GLM below): requirements at `medium`, planner and validator at `high`, completion at `medium`.
- `xiaomi-token-plan-sgp/mimo-v2.6-pro` (MiMo below): reviewer and resolver at `high`, builder at `medium`.

Source provenance: commit `22a720049e739f996c7eec8e2cba9fcd52a1325a`
(`22a7200`), branch `fix/pr12-correctness`, dirty worktree. The dirty, patched
`tools/live_trial.py` used for this attempt had SHA-256
`1a949f7b274bccdf5497c2c93688fa99969968c2558314e8b2c7977f19f3adbc`.
The recorded source is therefore not a clean-commit-only baseline.

The invocation used `--budget-stages 16 --timeout 900` and
`--i-authorize-live-model-spend`, with workspace
`.tmp-autopilot-testkit/live-20260926-glm-mimo/UI-01`. It ran in a normal blocking
shell with a 1,100,000 ms outer timeout, not a background-process manager. The
driver shared the **900-second wall-clock budget** across its CLI invocations.
Recorded trial start was `2026-09-26T19:31:59.507393+00:00`; result finish was
`2026-09-26T19:46:59.664267+00:00`, an elapsed **900.156874 seconds**.

**Outcome and delivery limits**

- Verdict: **ERROR**, `runner-driving wall-clock budget exhausted; stopped CLI and its provider descendants`.
- `result.json` records **0 checks, 0 failed** and `assertions.json` is empty. These error-path counts are not a pass. The post-exit oracle in `live-trial.json` records **0/1 passed**, with `index.html.present=false`.
- No `web/index.html` or `web/test_static.py` was delivered. The run never reached builder, validator, or completion stages; all nine acceptance criteria remained unverified. Candidate browser, responsive-layout, and interaction checks were not reached.
- The six passing UI reference controls were pre-existing prerequisite evidence, not passes by this live candidate, and were not rerun for this record.
- The harness answered the clarification about cancel from `PAUSED` using the proposed transition to `CANCELLED`, then resumed within the same attempt. The plan-review report was rejected with `$.concerns[0]: missing blocking`; its automatic `astra_challenge_report_repair` stage was unfinished at the deadline.
- Repair events show an unavailable report path and repeated `Ripgrep JSON record exceeded 65536 bytes` errors. The saved final and raw state remained `RUNNING / REPORT_REPAIR`, with the active repair stage last reporting 276.7 seconds elapsed. This persisted status does not indicate a surviving process.
- Post-exit checks found no command lines referencing this UI-01 project and none of its recorded provider/descendant PIDs. No additional cleanup was needed. There was no second attempt, forced gate/resume outside the harness, manual candidate/source edit, or deployment; deployment was not authorized.

**Measured stage usage**

The actual invoked models were GLM for one requirements call (`medium`) and two
planner calls (`high`), and MiMo for the review and repair calls (`high`). These
are five stage invocations, not a count of HTTP requests. Input below includes
cached input; output includes reasoning. Cached and reasoning subtotals must not
be added again.

| Stage | Invoked model | Input tokens | Output tokens | Total tokens | Recording |
| --- | --- | ---: | ---: | ---: | --- |
| `requirements_gather-01` | GLM | 18,296 | 5,809 | 24,105 | Finished |
| `astra_discovery-01` | GLM | 28,335 | 12,169 | 40,504 | Finished |
| `astra_discovery-02` | GLM | 30,890 | 9,798 | 40,688 | Finished |
| `astra_challenge-01` | MiMo | 96,505 | 14,064 | 110,569 | Finished invocation; report rejected |
| `astra_challenge_report_repair-01` | MiMo | 527,931 | 9,759 | 537,690 | Partial: 15 recorded `step_finish` events |
| **Observed subtotal, incomplete** | | **701,957** | **51,599** | **753,556** | Not a final run total |

The four finished invocations account for **215,866 tokens** from persisted
stage metrics. The unfinished repair has no final stage metrics/report; its
partial usage above is summed from raw events. A subsequent `step_start` has no
matching usage record, so remaining usage is **missing, not zero**. The observed
subtotal includes **559,936 cached-input** and **35,081 reasoning-output** tokens
as subsets. Provider request/retry counts are unavailable (`null`), not zero;
no final billing or dollar-cost claim is made.

**Retained artifacts**

All paths in this list are relative to
`.tmp-autopilot-testkit/live-20260926-glm-mimo/` in this worktree:

- Evidence bundle: `evidence/UI-01/01/`, including `result.json`, `live-trial.json`, `environment.json`, `trace.jsonl`, `assertions.json`, `state.final.json`, and the two `state.gate.*.json` snapshots.
- Raw run state: `UI-01/project/.autocode/runs/20260926-123159-implement-the-frozen-design-reference-design-spe-e073aef7/state.json`.
- Stage artifacts: `UI-01/project/.autocode/runs/20260926-123159-implement-the-frozen-design-reference-design-spe-e073aef7/iterations/001/`, including the requirements/discovery events, `archived-astra_challenge-01-df35a5/astra_challenge-01.jsonl`, and `astra_challenge_report_repair-01.jsonl` used for partial accounting.
- Candidate workspace: `UI-01/project/`; only the frozen design input and runner/Git metadata remained, with no delivered `web/` directory.

# Reliable completion baseline — 2026-09-21

The installed `autocode` command passed **10 workflow tests** in 80.909 seconds:

```sh
AUTOCODE_TEST_CLI=/Users/ankurkothari/.local/bin/autocode python3 -m unittest tools.test_planning.JointFlow tools.test_opencode.OpenCodeFlow
```

These exercise default joint planning, exact-plan approval, independent verification,
rework, unresolved decisions, saved-stage recovery, explicit plan revision, provider
quota handling, and completion. They use temporary Git projects and fake providers;
no live application run or real model request was started. This is a mechanics
baseline, not evidence of model-driven product delivery. Development priorities and
the remaining live-trial evidence are recorded in [RELIABILITY.md](RELIABILITY.md).

A separate source regression pass also passed **74 tests** in 117.777 seconds:

```sh
env -u AUTOCODE_TEST_CLI PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tools.test_subprocess tools.test_milestone_checkpoints tools.test_intervention_ordering tools.test_report_repair tools.test_final_workflow
```

It covers completion gates, milestone evidence, intervention ordering, invalid-report
recovery, and saved workflow routing. The first sandboxed attempt could not inspect
worker processes; the rerun passed with process inspection enabled. No guard was
disabled. All projects and provider responses were isolated fixtures; no runtime fix
was needed from these checks.

# Activity-aware timeout validation — 2026-09-20

Separate inactivity/tool deadlines, preserved hard caps, live activity diagnostics
and bounded recovery are implemented. The final offline suite passed **323 tests**;
three installed-wheel workflows also passed. See the validation record (`audits/activity-timeouts-2026-09-20/RESULTS.md` at tag `archive/pre-restructure-2026-09-26`)
for coverage, source fingerprints, deployment behavior and transport limitations.

# Milestone checkpoint validation — 2026-09-20

Enforced milestone evidence, bounded replanning, milestone budgets, status and
safe adoption are implemented. The full offline suite passed 261 tests; the final
targeted milestone suite passed 19 tests, and three installed-wheel workflow tests
passed. See the validation and rollout record (`audits/milestone-checkpoints-2026-09-20/RESULTS.md` at tag `archive/pre-restructure-2026-09-26`)
for exact coverage, limits, and the IdleCampus/ddia-tutor migration state.

# Repair validation — Autocode 0.5.4 — 2026-09-19

All five audited OpenCode integration defects are fixed. The runner also rechecks
completed work, detects executable-mode and submodule changes, preserves recovery
evidence across checkpoint failures, accounts for rejected/recovered stage time,
checks resumed session identity, and supports explicit stopped-stage abandonment.

- Full source suite: **120 tests passed**, 73.570 seconds.
  `python3 -m unittest tools/test_autocode.py tools/test_goals.py tools/test_subprocess.py tools/test_opencode.py tools/test_process.py`
- Offline wheel build and isolated install: passed; packaged runner sources matched
  the checkout byte-for-byte. **3 installed-wheel workflow tests passed**, 13.725 seconds.
- Global editable installation refreshed to **0.5.4**. Its complete OpenCode fixture
  workflow passed from an unrelated project: **1 test**, 3.752 seconds.
- Compilation, CLI help and changed-source whitespace checks: passed.
- Native OpenCode **1.18.31** checks confirmed preserved inline denies, detected
  custom-directory config drift and target-project model lookup. A real native bash
  worker was stopped on timeout and could not write after the workspace lock released.
- Native malformed-report recovery and an archive-checkpoint failure regression
  confirmed evidence remains recoverable and retries require explicit resumption.

The tests used temporary Git repositories, fake providers and a loopback server;
there were no hosted model requests in this repair pass. Process inspection was
enabled for the supervision tests. OpenCode permissions and process supervision
remain distinct from an OS sandbox. Existing application runs were not resumed.

Details and raw evidence: repair resolution (`audits/opencode-repair-2026-09-19/RESOLUTION.md` at tag `archive/pre-restructure-2026-09-26`).

# OpenCode integration validation — 2026-09-19 (historical)

OpenCode is the default engine for new runs; `--engine opencode` remains available
as an explicit selection. Its default role mapping is `openai/gpt-6-astra` for the
Plan Reviewer (Astra route), `openai/gpt-5.6-terra` for the Builder (Terra route),
and `zai-coding-plan/glm-5.3` for the Validator (Sol route), with separate
persisted sessions for each role.

Executed against the final source:

- `python3 -m unittest tools/test_autocode.py tools/test_goals.py tools/test_subprocess.py tools/test_opencode.py`:
  **100 tests passed**, 29.246 seconds. OpenCode subprocess flows omitted the
  engine flag to verify the new default; Codex flows selected `--engine codex`.
  The suite also verifies that OpenCode role prompts prohibit reading or searching
  parent directories, sibling projects, and ancestor instruction files.
- `python3 -m compileall -q tools`, direct syntax checks and CLI `--help`: passed.
- Offline wheel build with `pip wheel --no-deps --no-build-isolation --no-index`:
  passed. The wheel was installed into a temporary virtual environment, where
  **5 OpenCode CLI workflow tests passed**, 12.612 seconds.
- The opt-in `python3 tools/opencode_smoke.py --run-live` check passed against
  installed OpenCode **1.18.31**, using the existing provider authentication.

The live check made two small requests in a temporary empty Git workspace. The
Plan Reviewer request returned a valid JSON object through OpenAI. The Validator
request returned a valid JSON object through
Z.ai after executing a harmless `printf` command; its native event contained the
matching command, output and exit status zero. These checks verify the two provider
connections and native event/report handling. They do not constitute a complete
model-driven product build.

Offline coverage includes native command evidence and token accounting, malformed
or incomplete output rejection, terminal-message parsing, permission configuration,
config drift, metadata timeouts, completed-stage recovery, explicit session reuse,
distinct Builder/Validator sessions and refusal to change engines on an existing run. The
subprocess workflows cover approval, implementation, independent validation, rework,
limits and resumption using a fake OpenCode provider.

OpenCode review roles deny native edit tools, and the runner checks for source changes
after review stages. OpenCode does not provide Codex's OS sandbox; native tool
permissions remain responsible for shell and custom-tool access. No global OpenCode
configuration or authentication files were changed. This adapter supports OpenCode
1.x; it refuses 2.x rather than assuming protocol compatibility.

# Build-brief workflow validation — 2026-09-19 (historical)

The runner now follows the rough idea → Requirements conversation → approved build brief →
bounded Builder task → independent Validator validation → Completion Owner decision workflow.

Executed against the final source:

- `python3 -m unittest tools/test_autocode.py tools/test_goals.py tools/test_subprocess.py`:
  **82 tests passed**, 18.754 seconds.
- `python3 -m compileall -q tools` and CLI `--help`: passed. Final changed Python
  sources also passed direct syntax and trailing-whitespace checks.

The offline subprocess tests cover brief feedback and renewed approval, an intentionally
broken implementation followed by a Validator FAIL and a Completion Owner REWORK, successful revalidation,
artifact review in chat, pause before approval, an iteration-limit pause with the
correction task retained, and resuming without replaying completed work. They inspect
saved prompts to verify that execution roles receive the identical approved brief and
that the Validator receives the current task, full Builder report and matching workspace revision.
The fake Validator provider executes the greeting program's success and failure cases.

Unit coverage additionally checks complete brief fields and milestone coverage,
feedback provenance, stale task rejection, explicit end-to-end evidence, forged
evidence rejection, stale milestone evidence, no human-review request before passing
automated evidence, preservation of legacy v3 briefs and completed discovery output,
and honoring `--pause-after-stage` after chat answers.

The subprocess suite requires process inspection for the runner's duplicate-process
guard. It passed with approved escalation after the outer sandbox denied that check.
Tests use temporary Git workspaces and a fake Codex provider. No live model execution,
real project run or migration was performed for this change. These checks establish
runner behavior, not the quality of a real model's product questions or code review.

# Standalone validation — 2026-09-18 (historical)

The standalone source was extracted into an isolated temporary folder and extended
there. No task in the original project was launched, migrated, paused or restarted
by this work. No learner data, course material or application code was edited.

## Executed checks

- `python3 -m unittest tools/test_autocode.py tools/test_goals.py tools/test_subprocess.py`:
  **52 tests passed**, 6.191 seconds in the final full source run.
- `python3 -m compileall -q tools`: passed.
- Offline wheel build with `pip wheel --no-build-isolation --no-deps --no-index`:
  passed; Python 3.14.6 / setuptools 81.0.0 in this environment.
- Wheel installed into an isolated virtual environment with `--no-index --no-deps`.
- The installed `autocode` console entry point exercised from an unrelated Git
  workspace using `AUTOCODE_TEST_CLI` and `tools/test_subprocess.py`: passed.
- Installed `codex exec --help` and `codex exec resume --help` inspected for the
  model, sandbox, resume, schema, JSONL and output flags. Official
  [non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
  was also consulted. Help inspection is not a live provider compatibility test.

The subprocess test uses a deliberately fake Codex executable. It runs the actual
runner and role subprocesses, validates actual JSON schemas, writes a tiny greeting
program, executes valid/invalid-input checks, and uses actual CLI user events through
discovery, approval, implementation, Validator validation, human review and completion.
The same test runs against both source and the installed wheel. No network, provider
inference, credentials, paid model calls or production workspaces are involved.

The process guard correctly refused to run under the outer sandbox because `ps`
was denied. The offline subprocess tests were then run with approved escalation,
preserving the guard rather than disabling it.

## Gate coverage

Regression tests cover: initial read-only discovery; persisted answers; no repeat of
an answered question ID; answer/approval separation; exact displayed revision tokens;
edit invalidation; tampered/stale goal refusal; explicit delegation provenance;
material ambiguity/permission/contradiction/infeasibility/scope pauses; contract deltas;
stable criteria; all-criterion evidence; forged event references; source/evidence
drift; artifact-specific human review; blocking findings; deferred optional work;
refusal of extra implementation after current completion evidence; iteration/time/
usage/no-progress limits; atomic checkpoints; writer locks; reload after lock
acquisition; active/uncertain migration refusal; original task/session/artifact/answer
preservation; completed-stage recovery without replay; model/session/sandbox command
mapping; transport refusal; full command evidence and failure capture.

Two inherited tests were updated for intentional behavior changes: the old full-loop
test now records an explicit displayed-goal approval before execution, and the launch
test expects workspace-write sandboxing instead of `--approve-for-me`. Existing
assertions for independent evidence, checkpoint recovery and model/session identity
remain. Additional tests exposed and addressed fabricated criterion event references
and stale state reads before acquiring the lock.

## Limits

Live Requirements interviewing, Builder implementation and Validator semantic validation have not
been exercised against a real model in this extraction. Model output quality and
completeness of behavioral evidence still require an actual bounded trial. Headroom
is still disabled and unverified. No production migration or adoption was performed.

This is a local trusted-workspace tool. It enforces state transitions and approval
gates; it cannot prove that every relevant ambiguity was noticed or compile arbitrary
natural-language permissions into OS policy. Codex sandbox and connector settings
remain responsible for individual tool permissions. Windows-native support was not
tested; inherited POSIX locking/process inspection requires macOS/Linux or WSL.

## Retained compatibility surface

The `autocode-orchestrator` CLI entry point and `tools/autocode_orchestrator.py`
are kept as a thin alias over `autopilot` (`from .autopilot import *`). Nothing in
this repository invokes that command; it is retained only so external scripts and
documented invocations keep working. The saved stage name `orchestrator` is separate
and remains the live milestone scheduler — it is not this alias. Dropping the CLI
name would be a breaking change and was deferred.

## Extraction provenance

Initial source SHA-256 values recorded when copying the original runner:

| Original file | SHA-256 |
| --- | --- |
| `tools/autocode.py` | `6af6a4c7da274d59c317afa3e1789c550da417dd316f360fc4887c0c0b8b0d13` |
| `tools/autocode_support.py` | `d52ec563336856c375cf3d8c82ee0e67c6d233777b0f888b8d9714b0fd90f419` |
| `tools/test_autocode.py` | `cb2886f87f5e6527a1edde911d0c52dcc04e0a76c7c55fbf2525b3c090ea9f7d` |
| `tools/README-autocode.md` | `4dce80a64e6b4e6e6db923e22b373975cf21f258cca19c118b8b87291308a29b` |

The original project files changed independently during this work, including new
discovery-related code and schemas. Those changes were not overwritten, imported
into this extraction or claimed as this task's work. Consequently, the extraction
is based on the recorded starting snapshot, not byte-identical to the current
project runner. All patches in this task targeted the separate extraction folder.

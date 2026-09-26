# AutoCode execution-boundary audit — 2026-09-24

The later [remaining-product pass](autocode-remaining-products-2026-09-24.md)
closes the dashboard browser, executed C# parity, and live infeasible-plan checks.
It also records a corrected test-adapter naming assumption and the still-open issues.

## Fix-and-retest update (current)

The sections below this update are the **historical baseline**, not current results.
The supplied list has 34 scenarios, not 32. Changes remain on
`codex/autocode-blackbox-scenarios`; not merged or pushed.

### Production fixes

- **F1 fixed:** report repair preserves schema-valid command/result/history arrays
  and pending user decisions. Missing/malformed command fields can only be repaired
  using original execution events. The original forged-success CLI reproducer now
  rejects the repair. This does not prove arbitrary prose from an unparseable report
  is true; independent review remains necessary.
- **Pre-spawn crash fixed:** persisted launch intent can recover automatically only
  if the worker has no provider attempt and its locked worktree exactly matches the
  current parent batch, contract, commit and baseline files. Unexplained edits prevent
  replay. Both positive recovery and negative tampering tests pass.
- **F2/F3 prompts fixed:** create missing assigned outputs; use bare evidence IDs and
  filenames, putting explanations in summary/results instead.

### Live reruns

Both use actual **Luna medium Builders/report repair**, with scripted plan import and
intermediate review. They stop at candidates, not product approval.

- Dashboard: **PASS**, UI and documentation integrated, 115 seconds.
  Artifacts: `/private/tmp/autocode-live-dashboard-fixed-20260924`.
- Shared-contract client/server: **PASS**, shared prerequisite then separate workers
  and actual combined HTTP check, 169 seconds.
  Artifacts: `/private/tmp/autocode-live-clientserver-fixed-20260924`.

The new dashboard candidate was not browser-rendered again. The historical browser
check below belongs to the earlier retained UI, not this new candidate.

### Expanded scenario coverage

Combined verification: **134 executable tests passed in 258 seconds**. This includes
34 new execution/recovery test methods plus existing regression tests; it is not a
claim that all 34 requested behaviors have full end-to-end coverage. Python syntax
checks and `git diff --check` also pass.

Final inventory rerun: **134/134 tests pass** in 272 seconds. Scenario mapping:
**28 PASS, 3 PARTIAL (8/18/23), 3 GAP (15/21/22)**. Full test receipts and mapping:
`/private/tmp/autocode-build-scenario-matrix-fixed-20260924/results.json`.

The reproduction command below now runs both black-box modules and supporting
regression suites. Its `results.json` maps **every number 1–34** to test receipts.
`tests_successful` and `all_scenarios_verified` are separate: passing tests do not
erase scenario gaps.

| Scenario | New actual experiment / remaining limitation |
| --- | --- |
| 8 | Alive orphan refuses second writer; PARTIAL because exact timeout transition is not injected |
| 10 | Controller exits before spawn; pristine worker automatically starts exactly once |
| 11 | Exit after spawn before PID checkpoint; existing result recovered without replay |
| 12 | Exit after actual patch application; no duplicate application |
| 14 | Inject actual conflicting parent content at integration; retain both versions, no candidate |
| 17 | Actual five-retry implementation fails approved three-retry criterion |
| 19 | Normal CLI denial retained; repeated permission request neither reprompts nor writes source |
| 20 | Public-CLI autonomous rework subprocess test rerun |
| 23 | 21 durable file/check events and active CLI status; PARTIAL, not 21 rendered screens or named checkpoint UI |
| 24 | Retain first criterion/files/evidence; correction changes only missing regression marker |
| 25 | Both criteria rerun for new source; regression of old PASS yields overall FAIL |
| 29 | Original forged report-repair reproducer now rejected |
| 30 | Duplicate durable receipt after integration crash consumed once |
| 31 | Old failed receipt cannot replace successful retry candidate/source |
| 32 | Active edit refused; later revision invalidates approval; stopped-batch supersession covered separately |

Remaining gaps are explicit: **15** still permits worker-level BUILT for no progress;
**21/22** lack the requested configurable Luna → Sol High → pause policy. Existing
Terra escalation and bounded-failure guards pass their own tests but are not that
feature. **18** remains a scripted infeasibility response, not live reasoning.
Go migration still lacks executed C# parity. Successful candidates remain per-wave,
not global completion manifests. No safety or approval pause was bypassed.

---

## Historical baseline (before fixes)

Base: `e55fa5c` (master). Audit branch: `codex/autocode-blackbox-scenarios`.
Only test fixtures, test runners and this report were added. Production behavior was
not modified, and nothing from this audit was merged or pushed.

## What was actually exercised

The new tests enter through public `autoplanner`, `autocode_build` and, for required
intermediate checkpoints, `autoreview` CLI processes. A scripted provider imports
handwritten contracts; approval uses the actual displayed token and normal CLI.
Tests do not install fabricated runner state or call the scheduler directly.

The scheduler, worker processes, Git worktrees, report handling, ownership guards,
patch integration and persisted handoffs are real. Offline workers write prescribed
fixture code. Live trials replace only Builder/report-repair calls with real
`gpt-6-luna` at medium reasoning. Their plan-import and intermediate checkpoint
review providers remain scripted and execute the predeclared checks. These are
AutoCode trials, **not evaluations of AutoPlanner or live AutoReview**.

Each successful trial stops at a build candidate before final product approval.
Current AutoCode yields to AutoReview between dependency waves. Consequently,
"build an entire DAG without review until the end" is not the current unit boundary.

## Findings

### F1 — FAIL: report repair can rewrite claimed execution history

Scenario 29 is a reproducible failing CLI regression. The original Builder event has
`exit_code: 1`. Its otherwise valid report deliberately lacks a summary. Repair adds
the summary, but also replaces `commands_run` with a different, successful-looking
command and `results` with `All tests passed; exit code 0`.

AutoCode accepts that repaired report, marks the worker `BUILT`, integrates it and
exports a build candidate. The original failed event is retained, but the contradictory
claim is not rejected. The product is **not** marked `TASK_COMPLETE`; independent
review still remains. Preserving raw evidence is insufficient to satisfy the requested
"repair format, not history" invariant.

Reproducer: `BuildBlackbox.test_29_report_repair_cannot_invent_passing_checks`.
Evidence: `/private/tmp/autocode-build-blackbox-final-20260924/test_29_report_repair_cannot_invent_passing_checks-bc64r_vu/`.
The child under `project/.autocode/builders/675f1d14fd94/1/` contains both the archived
failed event and the accepted, contradictory implementation report.

### F2 — live Builder failure: unnecessary missing-file blocker

The dashboard documentation worker refused to create assigned `README.md` because
it did not already exist. It requested a corrected workspace instead. Creation was
within the handwritten task's scope. The UI sibling completed and was retained, but
the full build correctly paused rather than claiming an integrated result.

This is an observed model/prompt reliability failure, not proof of a scheduler bug.
No permission was granted on its behalf and its pause was not bypassed.

### F3 — live report-repair failure: annotated evidence paths never repaired

The client/server trial built the shared contract and launched separate server/client
workers. Their reporting/repair path stopped on invalid evidence references. A server
reference consisted of a real JSONL filename **plus an explanatory sentence**; the
runner treated the entire string as a filename. Repeated repair did not fix it and the
run paused. The failure is safe but prevents usable work from reaching integration.
The evidence also includes a sandbox binding failure followed by a successful HTTP
check; do not summarize this trial as "networking passed" or "integration passed."

### Gaps relative to the requested behavior

- The Builder escalation ladder in `tools/autocode_escalation.py` is Terra
  medium/high/xhigh/max. Explicit custom routes such as Luna are left unchanged.
  This is not the proposed ordinary retry → configured Sol High → pause policy.
- Durable stage recovery exists; arbitrary intra-milestone checkpoint queues for a
  21-screen implementation are not a delivered capability. Stage tests must not be
  counted as verification of that feature.
- No-op workers record `no_progress_batches`, but can still receive worker-level
  `BUILT` status and enter an unverified candidate. They do not thereby accept the
  milestone. If `BUILT` is meant to mean actual implementation progress, tighten it.
- A candidate handoff is per build wave, not a single all-milestones-completed manifest.
- `dotnet` is unavailable. Go fixture outputs were checked against handwritten
  expectations, not an executed C# reference. Behavioral migration parity is partial.

## Live product trials

| Product | Outcome | Actual observation |
| --- | --- | --- |
| A — Notes CLI | PASS | M1 storage → parallel M2 add/M3 list → M4 CLI. Executed persistent add/list against final candidate. Separate workers received completed prerequisite files. ~215 seconds. |
| B — Duplicate-safe API | PASS | Actual loopback HTTP server: 20 concurrent same-key/same-payload requests returned one logical result; changed payload returned 409. Explicit in-memory lifetime. ~144 seconds. |
| C — Monitoring dashboard | FAIL overall | UI built, documentation worker unnecessarily blocked; no integrated candidate. Retained UI separately passed browser Pause → Resume → Cancel and 375×812 visibility check. ~136 seconds. |
| D — Registry migration | PARTIAL, offline only | Six-task Go DAG executed; `go test ./registry` passed with expected A/de, B/ca and A/com outputs. No executed C# parity, no live Go Builder trial. |
| E — Shared-contract client/server | FAIL live; PASS offline | Live workers reached report-repair failures before integration. Scripted product completed actual HTTP integration. Live attempt ~243 seconds. |
| F — Deliberately broken plan | PASS for offline refusal variants | Declared overlap serialized; hidden out-of-scope write refused; scripted infeasible-plan request paused. No live impossible-library experiment. |

Live artifacts:

- A: `/private/tmp/autocode-build-live-20260924/test_01_notes_diamond_dependency_and_shared_inputs_03_04_27-bp6yc03_/`
- B: `/private/tmp/autocode-build-live-api-20260924/test_product_b_duplicate_safe_http_api-e_ra8mms/`
- C: `/private/tmp/autocode-build-live-dashboard-20260924/test_product_c_dashboard_candidate_requires_browser_check-27m75571/`
- E: `/private/tmp/autocode-build-live-clientserver-20260924/test_product_e_shared_contract_http_client_server-dxlnyjqb/`

Browser check of C used the retained M1 worktree, **not an integrated candidate**.
Observed RUNNING with Resume disabled; PAUSED with Pause disabled; RUNNING after
Resume; CANCELLED with all controls disabled. At viewport 375×812, body scroll width
was 375, status client/scroll width both 131, and the screenshot showed unclipped
status, controls and disclaimer. This does not cover 21 screen/state combinations.
The temporary loopback server and browser tab were closed afterward.

## Coverage of the 34 requested scenarios

"Supporting" below means existing internal/state-oriented tests were rerun; it does
not mean a new end-to-end black-box experiment reproduced the exact crash window.
"Partial" explicitly identifies the missing part. No claim of 34/34 black-box passes.

| # | Scenario | Evidence / limit |
| --- | --- | --- |
| 1 | Sequential/parallel notes | New CLI test + live A pass |
| 2 | Three independent tasks | New CLI test: three distinct overlapping worker processes/workspaces; independent file/behavior checks |
| 3 | Dependency blocks premature work | Notes CLI trace contains only M1 before checkpoint |
| 4 | Diamond | Notes + scripted client/server; final task observes both prerequisite outputs |
| 5 | Declared path overlap | New CLI test: serial fallback |
| 6 | Hidden ownership violation | New CLI test: no unauthorized parent edit, no candidate |
| 7 | One failed sibling | New CLI test: explicit retry relaunches M1 only; M2/M3 original results retained |
| 8 | Timed-out worker still alive | Supporting OWN-02/03; exact new CLI timeout/overlap experiment not performed |
| 9 | Result complete before controller crash | New CLI test SIGSTOPs test-owned controller, lets all three results become durable, kills controller, resumes; exactly three Builder starts |
| 10 | Crash before launch | Supporting CRH-01/02; exact new external crash injection not performed |
| 11 | Crash immediately after launch | Supporting CRH-03; exact new external pre-PID-checkpoint crash not performed |
| 12 | Crash during integration | Supporting dispatch patch-application recovery + CRH-11 |
| 13 | User source edit during work | New CLI test edits parent while worker is held; edit survives and integration pauses |
| 14 | Integration conflict | Supporting PAR-07; no new end-to-end conflicting-patch injection |
| 15 | Success without edits | New CLI test records no progress; worker-level BUILT semantic gap noted above |
| 16 | Missing required evidence | New CLI test pauses without exporting candidate or fabricating PASS |
| 17 | Builder changes requirements | New CLI test rejects altered contract hash. Silent behavioral drift (3 retries → 5) not separately exercised |
| 18 | Technically impossible plan | Scripted infeasible response pauses. Live impossible-library reasoning not tested |
| 19 | Permission required/denied | CLI pause verified; denial-and-repeated-request variant not rerun black-box |
| 20 | Ordinary failure retry | Explicit failed-worker recovery verified; autonomous implementation-failure/resolver loop not separately rerun here |
| 21 | Strong-model escalation | Requested policy differs from current configured ladder; supporting FIX-05 passes current behavior only |
| 22 | Strong-model failure then pause | Supporting bounded failure guards; exact requested model ladder not tested/implemented |
| 23 | Large milestone checkpoints | Capability gap; no 21-screen long-running trial |
| 24 | Later checkpoint failure | Stage-level retention supporting tests pass; sub-checkpoint recovery remains a gap |
| 25 | Fix regresses old criterion | Supporting FIX-09 and stale-evidence tests; no new live regression product |
| 26 | Incompatible individual contracts | New negative CLI product: route mismatch passes individual callable checks, fails actual combined HTTP check, produces validator FAIL and not completion |
| 27 | Shared contract first | Notes/live A + scripted client/server pass; live E blocked later on reporting |
| 28 | Malformed report after coding | New CLI test: one repair, no extra implementation launch |
| 29 | Repair rewrites history | FAIL — F1 |
| 30 | Duplicate delivery | CLI repeated-build no-op + supporting durable report reconciliation; not every delivery path fault-injected |
| 31 | Late superseded worker | Supporting stale-binding coverage only; no new A-late/B-authoritative external delivery test |
| 32 | Revision while active | Supporting stopped-batch supersession/plan-binding tests; exact live R1→R2 transition not performed |
| 33 | Redispatch completed task | New CLI repeated-build test: no new worker or changed candidate |
| 34 | Build output not completion | All new successful build tests assert candidate revision/contract and non-TASK_COMPLETE; current per-wave boundary noted above |

## Reproduction and test totals

Run the full new deterministic suite, preserving both passing and failing artifacts:

```sh
python -m tools.run_build_blackbox_audit --artifacts /private/tmp/autocode-build-audit
```

The suite intentionally returns nonzero while scenario 29 remains unfixed. No
`expectedFailure` marker converts that failure into a green result.
The final inventory is `/private/tmp/autocode-build-blackbox-complete-20260924/results.json`.

The final combined run completed **20 CLI black-box tests: 19 PASS / 1 FAIL** in
81 seconds. Scenario 29 is the failure. The inventory retains the complete failure
trace rather than hiding it. Four live product attempts yielded two passes (A/B)
and two blocked failures (C/E).

Supporting baseline suites: 78 tests passed (`test_dispatch`,
`test_assignment_scenarios`, `test_units`, `test_report_repair`). Another 76 passed
(`test_catalogue_t03`, `t06`, `t07`, `t08`, `t09`, `t10`). Some catalogue cases reuse
older tests; **154 passing tests is not 154 unique scenarios**, and none cancels F1.

For a selected live product test, set `BUILD_AUDIT_LIVE_CODEX` to the real Codex CLI
and `BUILD_AUDIT_ARTIFACTS` to a fresh evidence directory. Do not run adversarial
scripted-provider scenarios in live mode. Live Builder subprocesses have their
normal workspace-write sandbox; no approval or safety checks were bypassed.

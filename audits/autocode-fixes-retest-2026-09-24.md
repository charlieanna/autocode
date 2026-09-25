# AutoCode fixes and detailed retest

Branch: `codex/autocode-blackbox-scenarios` (isolated worktree). Not merged or pushed.
This supersedes the remaining-gap conclusions in the earlier two audit reports;
those baseline results and failed-run artifacts remain intact.

## Fixes

1. **Empty implementation is not BUILT.** A no-source-change implementation report
   is retained as no progress, cannot publish a build handoff, and follows the bounded
   retry policy. Integration independently rejects empty tree deltas, including old
   receipts labelled BUILT. Both serial and parallel paths are covered.
2. **Persisted retry policy.** Configured Builder → one ordinary retry → stronger
   Builder (default Sol High) → pause, scoped to the approved contract/milestone.
   Evidence replay does not spend another attempt; restart does not erase exhaustion.
   Ordinary route is restored on the next milestone. Explicit pins/custom providers
   are not overridden. Independent review failures pass through Autoresolver.
   The exhaustion check precedes milestone reassignment/stalled-review routing.
3. **Named dashboard checkpoints.** Implementation, observed tool completions,
   independent validation, completion review and acceptance criteria appear in the
   Now view and worker details. Saved progress survives restart; prior tasks are
   archived. Activity does not mean acceptance. Task/contract/source bindings prevent
   unrelated or unbound evidence from marking a criterion verified.
4. **Actual timeout/live-worker coverage.** A public-CLI test waits for the real
   stage watchdog to record a timeout, crashes the test controller during cleanup,
   verifies the provider remains alive, and proves resume starts no second writer.
   It does not fabricate the timeout by editing runner state.
5. **Evidence-scope prompt correction discovered live.** Serial and parallel
   Builders now receive a concrete run-local evidence directory and an explicit
   prohibition on unassigned top-level evidence files. Ownership enforcement is
   unchanged; violations still pause and require inspection.

Earlier report-repair truth-preservation and pristine pre-spawn recovery fixes are
also included in the retest. Format repair cannot replace valid failed commands,
results or a pending user decision with success. Recovery cannot authorize unknown
workspace edits or a second live writer.

## Automated results

- Full external-CLI audit/regression suite: **147 passed**, 256 seconds.
  Receipt: `/private/tmp/autocode-final-matrix-20260924/results.json`.
- Additional runtime/dashboard regression suite: **175 passed**, 46 seconds.
  Includes collector empty-receipt defense, configured OpenCode strong-model route,
  source/evidence guards, process ownership, dashboard consumer and checkpoint tests.
- Serial no-op exhaustion public-CLI regression: **passed**, 3 seconds.
- Final checkpoint/retry-policy suite after the last edge-case changes:
  **15 passed**, 21 seconds.
- Dashboard JavaScript rendering test: **passed**; named checkpoints and the
  non-acceptance disclaimer are explicitly asserted.
- `git diff --check` and dashboard JavaScript syntax check passed.

These suites overlap; their counts must not be summed as unique test cases.
The offline matrix is **32 PASS / 2 PARTIAL / 0 FAIL / 0 GAP** across 34 mapped
scenario IDs. Scenario 18 needs live infeasibility reasoning (covered separately
below); scenario 23 includes separate browser inspection and does not demonstrate
21 distinct application screens. The suite is not labelled all-live or all-verified.

The first broader run found three obsolete compatibility expectations: no-op BUILT,
the previous six-review stall policy, and a dashboard protocol fixture rewriting
identical source after reapproval. Tests now assert rejection/the bounded policy;
the protocol fixture makes an actual source revision without weakening its approval
or human-review checks. A separate dashboard test invocation initially lacked its
required import path; the corrected 175-test invocation passed.

## Fresh live product trials

Actual Builders used Luna Medium. Plans were handwritten and imported through the
public planner/approval CLI with a scripted provider. Intermediate review decisions
were scripted, while product commands, HTTP checks, processes and integration ran
for real. These isolate AutoCode rather than evaluate live planner/reviewer quality.

| Product | Outcome |
| --- | --- |
| A: Notes CLI | PASS: prerequisite wave, independent add/list workers, integrated CLI behavior |
| B: Duplicate-safe API | PASS: actual HTTP concurrency/idempotency checks |
| C: Monitoring dashboard | PASS: live integrated candidate plus 28 browser checks |
| D: C# → Go registry | PASS: executed C# oracle agrees with Go on seven fixed cases |
| E: Shared-contract client/server | PASS on fresh retest: shared prerequisite, parallel components, real HTTP integration |
| F: Impossible SQLite plan | PASS by safe refusal: infeasibility retained, no integrated candidate or unauthorized workaround |

Artifacts:

- A/B: `/private/tmp/autocode-live-final-ab-20260924` (2 tests, 278 seconds).
- C and initial E: `/private/tmp/autocode-live-final-ce-20260924`.
- D/F: `/private/tmp/autocode-live-final-df-20260924` (2 tests, 295 seconds).
- E successful rerun: `/private/tmp/autocode-live-final-e-retest-20260924`
  (1 test, 157 seconds).

**Initial E failure retained:** Builder created `evidence/m4-integration-check.txt`
outside its `integration.py` assignment. The runner paused with INVALID_OUTPUT;
we did not approve, widen scope, or resume past that safety pause. After clarifying
the common Builder instructions, a fresh identical-plan trial passed. This reduces
prompt ambiguity but does not guarantee that a future model never violates scope.

## Browser checks

Fresh product C candidate:
`/private/tmp/autocode-live-final-ce-20260924/test_product_c_dashboard_candidate_requires_browser_check-ziov9kad/project/web/index.html`.
SHA-256 before handoff: `89b862eda482e6074498a1661ee155864d02c9fdd7b8ad1e7258ea78ec625b62`.

At widths 320, 375, 390, 480, 768, 1024 and 1440, tested four states:
RUNNING → PAUSED → RUNNING → CANCELLED. Asserted corresponding enabled/disabled
Pause/Resume/Cancel controls, no horizontal overflow, and unclipped status,
buttons and disclaimer. **28/28 passed**. Mobile screenshot at 375×812 was also
visually inspected. Browser tab, viewport override and loopback server were cleaned up.

Separately, the real AutoCode dashboard rendered the saved long-running fixture's
named checkpoint panel at desktop/mobile sizes: 21 observed completed tool events,
recorded implementation, pending independent validation/review, and unverified
criteria. It did not promote tool activity into passing acceptance evidence.
This is a small dashboard fixture, not 21 independently designed screens.

## Limits

- No merge, push, deployment or changes to the user's main checkout.
- Safety pauses, approval gates and permission boundaries remain intact.
- Live Builder success is not independent live AutoReview certification or a claim
  that all six runs reached user-approved TASK_COMPLETE.
- Temporary artifacts contain full prompts/reports/CLI receipts; baseline failures
  were preserved, not erased to make the final table green.

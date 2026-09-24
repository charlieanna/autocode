# Orchestration recovery fixes

Base: `origin/master` at `189b0f00347b65191d9306ffc0306bd713893ff2`.
Work was isolated from the existing dirty checkout and installed runner.

## Changes

- Timeout recovery preserves the failed task and effective limits, gives the
  Completion Owner concrete recovery instructions, and rejects an identical
  implementation assignment under unchanged limits. Existing retry ceilings,
  worker cleanup, partial-work preservation, and user-controlled limits remain.
  Startup reconciliation can also recover a durable timeout classified as an
  uncertain response, after the same stopped-worker and incomplete-turn checks.
- Exact repeated permission requests under the same approved contract reuse the
  original authenticated answer once. Refusals and conditional answers remain
  authoritative. A second repeat pauses for reconciliation rather than starting
  an unlimited review loop. Changed scope, changed contracts, missing provenance,
  and legacy answers without the full request are not automatically matched.
- Execution prompts scope Figma inspection to the bounded task. Nonvisual test,
  parser, and harness repairs can retain applicable design evidence; presentation
  changes and visual verification still require the relevant reference frames.
- A report whose only gap is one human acceptance criterion can enter the human
  review flow and complete after a real acceptance event. The independent report
  remains unchanged. All automated criteria, checks, full-flow evidence, current
  source, contract, task, and evidence hashes remain required.

## Validation

The expanded regression suite covers goal/permission handling, timeout recovery,
Figma handoffs, milestone checkpoints, CLI subprocess flows, final-review routing,
intervention ordering, and parallel orchestration: **218 tests passed**.

```sh
python -m unittest tools.test_goals tools.test_activity_runtime tools.test_figma_workflow tools.test_milestone_checkpoints tools.test_autocode tools.test_subprocess tools.test_final_workflow tools.test_workflow tools.test_intervention_ordering tools.test_orchestrator tools.test_milestone_policy -q
```

After refining timeout comparison to use the actual failed deadline (changing an
unrelated deadline must not permit the same replay), all **53 goal tests passed**
again. `git diff --check` also passed.

Two outdated activity-test fixtures were reproduced on untouched master and
corrected: the mock omitted the existing `startup_grace` argument, and its saved
role assertion did not allow the default Completion Owner route. The revised
checks still require every previously configured role to remain unchanged and
verify the expected startup grace.

Full-suite comparison used `python -m unittest discover -s tools -t . -p
'test_*.py' -q` on both checkouts. Untouched master ran 483 tests with 17
failing/error entries, including parameterized subtests. The initial candidate
run executed 490 tests with 12 such entries: 11 also occurred on master, and one
CLI test encountered a macOS `psutil` process-inspection `SystemError`. That CLI
test passed in the later expanded regression suite. The timeout-on-restart
failure shared with master was also fixed and passes in that expanded suite.

The broad suite is **not fully green**: existing provider-routing, migration,
and default-role fixture failures remain outside this patch. This comparison is
diagnostic, not a waiver or a claim that the entire suite passed. Full logs are
retained in the worktree's ignored `.autocode/verification/` directory.

## Limits

Exact-request reuse is not semantic permission inference. Related requests with
different wording must still be evaluated against saved answers by the model.
The timeout guard detects structurally identical plans; it cannot establish that
a reworded plan is meaningfully better. Model judgment and independent review
remain necessary.

## Follow-up: remaining engine issues

- Added `autocode compare-baseline`, a maintained comparator for completed Vitest
  default-reporter logs. It preserves complete diagnostic signatures and raw input
  hashes; rejects malformed, truncated, duplicate and unhandled-error output;
  reconciles unique failed files and failed tests with totals; and rejects shrinking
  or increasingly disabled test inventories. The optional dependency-prefix
  normalization only affects relative stack-frame prefixes before `node_modules`;
  package versions, numbers, assertion operands and source locations are retained.
  Comparison is evidence, not authorization for a waiver or a completion decision.
- Handoffs serialize without indentation and move bulky evidence indexes, historical
  validation indexes, legacy checkpoints and source maps into immutable hashed
  context artifacts. Recent index entries stay inline with retrieval paths. Current
  reports, requirements, saved answers, feedback, permissions and findings remain
  intact. Tests check archive retrieval and reject changed archives. Existing model
  sessions are not compacted or reset by this change.
- Shared status persistence now records stage transitions, blockers, completion and
  code-worker/batch heartbeats. CLI stderr and the dashboard task conversation show
  these updates. Parallel Builder state changes appear at checkpoints. Figma UI
  uses the same transition publisher. Heartbeats coalesce, the latest 100 messages
  survive restart, and notifications do not affect acceptance gates. These updates
  are local; there is no external ChatGPT/Codex chat push integration.

Follow-up validation:

- **232 runner tests passed** across the same expanded suite plus baseline/context/
  progress tests. A subsequent dependency-prefix regression and archive-tampering
  check passed in the **15-test** baseline/status/context suite.
- **28 dashboard conversation integration tests passed** with `PYTHONPATH=tools`.
  Initial standalone discovery without that source import path failed in the
  preexisting `dashboard_conversations` transport import. No provider was called.
- Four dashboard JavaScript suites passed: task decisions/conversation ordering,
  status rendering, refresh isolation and chat composer behavior. The added test
  verifies that saved progress messages appear chronologically with user messages.
- On existing IdleCampus evidence, both completed logs parsed and reconciled:
  baseline 78 failure identities, candidate 76. With explicitly equivalent checkout
  roots and dependency-prefix normalization, 76 signatures matched, zero were new
  or changed, and two were baseline-only. This is a comparator regression check,
  not a new acceptance or proof that absent tests recovered. Raw task artifacts
  were unchanged; the result is under `.autocode/verification/`.
- `git diff --check` passed. No live model runs were needed.

All engine changes remain local in this isolated worktree. The installed runner
and active IdleCampus task were not replaced. The broader preexisting provider/
migration/default-role failures described above remain outside this patch.

# A third of stage wall time produces nothing

**Seen:** 2026-11-11, measured across saved runs in this workspace to decide where to spend
effort on per-task stage efficiency. **First fix:** the duplicated object separator below.

## What was measured

Every `state.json` under a `.autocode` in this workspace whose latest stage finished on or after
2026-11-01: 58 runs, 1,016 stage launches, 155.0 h of `duration_seconds`. Stage code names were
mapped through `autocode_names.STAGES`, since most runs predate the rename. `orchestrator`,
`model_preflight` and the zero-duration `resolver` marker rows are excluded.

Split by launch outcome:

| outcome | launches | hours | share |
| --- | --- | --- | --- |
| accepted (real work) | 653 | 99.03 | 63.9% |
| rejected | 363 | 49.17 | 31.7% |
| report repair | 148 | 6.80 | 4.4% |

**36.1% of stage wall time produced no accepted result.** This is not an artifact of
fault-injection fixtures: measured separately, the 37 real-project runs are 36.4% non-productive
and the 21 `stall-test*`/`par-*`/`slugify-demo*` fixture runs are 34.9%.

Rejected time by recorded `rejection_reason`:

| cause | launches | hours | share |
| --- | --- | --- | --- |
| provider timeout / non-terminal | 49 | 16.23 | 10.5% |
| output not a JSON report | 32 | 8.12 | 5.2% |
| schema/field validation (`$: …`) | 34 | 7.59 | 4.9% |
| operator abandoned uncertain | 32 | 4.75 | 3.1% |
| stage hard time limit | 17 | 1.43 | 0.9% |
| evidence not authenticated | 5 | 1.26 | 0.8% |
| ~25 further contract/criteria causes | ~50 | ~5.0 | ~3.2% |

The first three plus report repair are one class: **the call ran and its answer was unusable** —
38.7 h, 25% of all stage wall time.

For reference, the accepted work is dominated by `regression_proof` (102 launches, 23.05 h), then
`review_plan` (16.68 h), `builder` (10.61 h), `tester` (6.96 h) and `review_completion` (5.99 h).
`requirements_gather` is 4.74 h (3.1%) over 120 launches, and `plan_task` did not launch at all.

## What this rules out

Two structural ideas were measured and dropped:

- Folding Requirements into the Planner's draft would reach at most 3.1% of wall time, on the one
  stage that talks to the person.
- Folding `plan_task` into the draft saves nothing: it had **zero** accepted launches in 58 runs.

Adaptive planning already collapses what it should, and `autocode_review_effort` already lowers
review levels — but on small, clear tasks since 2026-11-06 it lowered `review_plan` 14/33 and
`finalize_plan` 6/23, and **`tester` 0/28 and `review_completion` 0/31**. The recorded reasons:
37 kept as `not_small_clear` because the drafted plan came out large (28) or medium (9) even though
the brief was small — the rule working correctly — and 10 kept as `no_lower_level`, because
`zai-coding-plan/glm-5.3` offers low, high and max, so there is no level one step below high.

## The duplicated object separator

Of the 51 "not a JSON report" rejections with a surviving `.response.txt`, 10 are the same shape:
a nested object closes and the model duplicates the separator before the next key.

```
..."uncovered":[],"verdict":"PASS"},","status":"REWORK","task_id":"task-9086…
..."uncovered":[],"verdict":"PASS"},""status":"CONTINUE","task_id":"task-7815…
```

All 10 are `review_completion`, all on `zai-coding-plan/glm-5.3` through kilocode/opencode, and 7
are dated 2026-11-10 or 2026-11-11 — after the syntax-normalization path landed on 2026-11-08, so
this is current behaviour, not an old runtime. The turn ends with finish reason `stop` at
1,214–8,519 output tokens, so it is not an output-limit truncation and `output_cap.exhausted`
correctly returns False.

The report is otherwise complete: removing the stray text yields the same 18-key report with a
real `status` of CONTINUE, REWORK or BLOCKED. `autocode_report_syntax.stray_object_separators`
now corrects it through the existing `syntax_report` path, bounded at 4 occurrences, with the same
receipt and the same ordinary gates. Verified against the 10 saved responses: all 10 rejected
before, 7 recovered after. The other 3 begin with prose before the report, so their correction
would need a receipt that also names a start offset; they stay on the repair path.

The remaining shapes are deliberately not corrected. Eleven are unescaped quotation marks inside a
string value (`the lines "body one" and "body one too"`), which is genuinely ambiguous — there is
no way to tell a delimiter from content. Five are prose or a code fragment with no report. Two are
trailing commas, which the existing rule already covers, and two leaked tool-call or
`</system_warning>` markup.

## Not yet addressed

Provider timeouts (16.23 h) are the largest single cause and are untouched here. Whether they
cluster on one provider or route has not been measured.

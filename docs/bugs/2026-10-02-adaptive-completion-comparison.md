# Adaptive planning through completion

Adaptive planning remains opt-in. The September 30 comparison stopped at plan
approval and cannot establish cost or correctness for a completed build.

## Progressive approval regression

The October 2 full-catalog scripted comparison found three adaptive failures:
`progressive-learning-journey`, `progressive-cumulative-regression`, and
`progressive-split-learning-journey`. Fixed planning passed all three. With no
blocking challenge, adaptive planning showed the draft for approval after three
model stages. Approval refused it: initial progressive delegation requires an
accepted revision and final independent review, not just a challenge.

The fix retains revision and final review whenever a progressive candidate is
present. It preserves the existing approval witness, exact-contract and
delegation checks. Ordinary plans retain early approval. After the fix both
variants pass 52/54 catalog cases; each has one `NOT_EXERCISED` Resolver case
and one skipped hybrid live-Investigator case, with no missing attempts.
Scripted model stages are 446 fixed versus 333 adaptive; this is plumbing
evidence, not a token, dollar or model-quality result.

An added to-do negative control reproduces the historical missing-brackets
failure. Its own three delivered tests pass, but the independent brief oracle
fails exactly the three output checks (7/10). The complete adaptive fake run is
correctly classified `FALSE_COMPLETE`, not a success in the comparison.

## Reproducible comparison

`scenarios/run.py build-compare` repeats both variants through completion, uses
the original catalog briefs and independent oracles, alternates arm order, and
retains every failure, timeout and missing attempt. `--prepare` saves the exact
protocol without calling a model; `--rebuild` reconstructs reports without
rerunning attempts. See [the commands and limits](../../scenarios/README.md#fixed-versus-adaptive-through-completion).

API estimates use the frozen October 2 standard text rate card, separate fresh
input/cache reads/cache writes/output, include reasoning and failed calls,
deduplicate replayed finishes, and apply the long-context surcharge per request.
Unknown rates and unfinished usage stay unpriced. Cost per pass includes failed
attempt spend. It is API repricing, not subscription pricing or an invoice.

Offline evidence is ignored under
`.scenario-runs/adaptive-build-comparison-20261002/{offline,offline-fixed}/`.
The approved live campaign completed four original catalog cases, two variants
and two repetitions (16 attempts), with identical models and caps. Its production
source is frozen separately with a file-hash manifest.

## Completed live comparison

All 16 scheduled attempts are present, without duplicates or replacement runs.
The final audit verifies all 264 frozen production-file hashes, the original
briefs and seed files, authorized model routes, and exact repricing of every
saved result. Each attempt had a 45-minute wall cap, 40-minute saved active-time
cap, ten-minute stage cap, six build iterations and 40 harness CLI actions.

| Policy | Completed jobs | Model stages | Report repairs | Known API estimate | Unpriced attempts |
| --- | ---: | ---: | ---: | ---: | ---: |
| Fixed | 2/8 | 114 | 25 | $12.6070 | 3/8 |
| Adaptive | 4/8 | 97 | 10 | $13.7648 | 2/8 |

Both total costs and cost per completed job remain unknown. The known estimates
are lower bounds, including all captured failed-attempt spend. Fewer stages do
not imply fewer API requests: OpenCode emitted 340 priced request finishes for
fixed and 416 for adaptive, which reached more implementation and review work.

The table retains every attempt. `BLOCKED` means the harness's `HONEST_BLOCKER`:
AutoCode did not complete, including when the independent delivery oracle was
green. An unknown estimate shows only known finished-request usage.

| Original case | Repeat | Fixed: result, oracle, API estimate | Adaptive: result, oracle, API estimate |
| --- | ---: | --- | --- |
| Greeting CLI | 1 | PASS, 12/12, $0.9622 | PASS, 12/12, $0.6106 |
| To-do CLI | 1 | BLOCKED, 7/10, $1.7838 | BLOCKED, 8/10, unknown; known $1.2651 |
| Timesheet by project | 1 | BLOCKED, 6/6, $1.9592 | PASS, 6/6, unknown; known $1.3832 |
| Parallel diamond | 1 | BLOCKED, 5/10, unknown; known $1.7405 | PASS, 10/10, $1.9018 |
| Greeting CLI | 2 | PASS, 12/12, $1.5935 | BLOCKED, 12/12, $1.1489 |
| To-do CLI | 2 | BLOCKED, 0/1, unknown; known $1.3487 | BLOCKED, 10/10, $2.6925 |
| Timesheet by project | 2 | BLOCKED, 3/6, unknown; known $1.2719 | BLOCKED, 6/6, $3.1559 |
| Parallel diamond | 2 | BLOCKED, 7/10, $1.9471 | PASS, 10/10, $1.6069 |

Only greeting repeat 1 is both fully priced and successful in both arms:
adaptive saves $0.3517 (36.55%) and 695.6 seconds (43.65%). That single pair
does not establish a general saving. The final adaptive diamond preserves all
four milestones, their exact owned paths and prerequisite graph; its middle
Builders ran in an isolated parallel batch. The shorter planning path did not
collapse the requested dependency structure.

Across the $26.3718 of known usage, planning accounts for $12.8932 (48.9%),
Builder $4.8904 (18.5%), Validator $3.9093 (14.8%), Completion Owner $1.8647
(7.1%), Resolver $2.0799 (7.9%) and Investigator $0.7343 (2.8%). Explicit
report-repair stages cost $3.5372 (13.4%), a subset of those role totals. These
are API cost shares of captured usage, not token shares or complete-run totals.

The largest observed cost candidates are repeated planning/structured-report
repair and routine repair tasks sent to the strong Resolver. The two Astra
Resolver calls account for 34.3% and 36.7% of their respective adaptive to-do
and timesheet attempt costs. A cheaper routing policy still needs a paired
quality test; this campaign keeps models fixed and cannot establish one.
The final diamond also needed a $0.0600 Completion Owner follow-up after it
requested validation already covered by current independent evidence. That
is a smaller prompting candidate, not grounds to remove final review.

Adaptive remains opt-in. This is promising completion evidence under the
tested caps, with too few workloads, missing usage and clarification confounds
to justify always enabling it or changing model assignments. The two corrected
gates have regression and replay proof; only the progressive correction
was included in the frozen live source. Further live comparisons should keep
clarification answers consistent and strengthen the oracles for the edge cases
that completion review caught.

Full saved evidence remains ignored under
`.scenario-runs/adaptive-build-comparison-20261002/live/20261002T105043Z-build-compare-build-comparison-upk4n5x3/`;
`report.md`, `progress.md`, `finished-analysis.json` and `final-audit.json`
retain the protocol, results, usage limitations and audit.

## Declared negative probes blocked correct completion

The second adaptive greeting attempt delivered code that passed all 12 original
oracle checks. Its approved plan explicitly required six direct CLI probes with
exit codes `0/2/2/0/0/0`. The Validator's two cited commands independently checked
the regression tests and exact CLI bytes and exited zero. The runner nevertheless
added the plan's raw commands as checks expected to exit zero. Correct usage
errors returning 2 rejected three reports and led to a paused investigation.

The fix turns an explicit terminal exit-code declaration into shell assertions
for the corresponding quoted commands. All probes still run; a wrong zero exit
for a required usage error fails. Mismatched status counts, ambiguous quoted
snippets and statuses outside 0–255 are refused. Natural-language checks outside
this narrow syntax remain with the Validator. Reported checks in a PASS still
must exit zero, and ordinary test commands retain that requirement.

Re-executing the original stopped run's report against its unchanged delivered
files reproduces the rejection with the frozen runner. The patched runner
accepts all nine distinct reported and plan-derived checks. Source-file hashes
match before and after both replays; neither makes model calls. Regression tests
also reject a CLI returning zero for usage errors and a Validator falsely
claiming zero. Complete scripted greeting builds with the problematic plan now
finish in both planning modes, without report repair.

The recorded attempt cost $1.1489 at the frozen API rates, including $0.3950
(34.4%) for subsequent Validator repair and Investigator calls. This identifies
the cost of the observed failure path; it is not a measured live saving after
the fix. The approved 16-attempt campaign ran against source
`7c598602fc4746d58cf2e8a9b89df4e560f45cfa`, which predates this replay fix.

The patched full catalog retains the same 52 PASS, one `NOT_EXERCISED` and one
`SKIPPED` per mode. Eight complete-build regression tests also pass, covering
negative probes, broken deliveries, literal output and progressive approval.
The 2,588-test full unit/integration gate exposed a preexisting startup fixture
using the old underscore report name, plus installed-package checks whose shared
interpreter lacked `autocode_cli`. The configuration module's 12 checks pass with
an isolated local installation. The nine startup checks pass after locating the
retained report independently of its stage slug and synchronizing the fake
provider's failure with its recorded process registration. This fixes a test
race without relaxing the runtime's uncertain-execution or retry limits.
The final affected gate passes 472 tests across 35 modules; the final code also
passes CI on macOS and Ubuntu.

## Live interpretation constraints

The driver answers clarification questions with the model's proposed default
and records those answers. These can change the target away from the original
brief oracle. Both fixed to-do attempts selected a default allowing bare status
words. The first approved plan and Builder then used bare `open` and `done`,
while the original oracle requires `[open]` and `[done]`. Its 7/10 oracle score
is not evidence that the Builder violated its approved plan, nor a valid
format-quality comparison against an arm with no such answer. The second fixed
attempt never reached a Builder. Both stopped before completion; that is a
separate outcome. Future quality comparisons need consistent clarification
answers that preserve the intended catalog behavior; the current attempts and
their original scores remain retained.

The second adaptive to-do attempt passed all ten catalog checks but the
Completion Owner found newline-containing task text violating the single-line
listing contract and a conforming large positive integer ID failing Python's
default integer conversion limit. It stopped before repair completed. The
second adaptive timesheet attempt passed all six catalog checks but initially
failed the runner's required regression proof because test names did not map to
the approved cases; it also used temporary fixtures outside the permitted
workspace. Repair reached a second passing Validator before the cap, without
final completion review. These green oracles are not completed jobs, and the
independent review/proof gates cannot be removed based on this comparison.

An interrupted model request without a final usage event leaves that attempt's
total API cost unknown. Known finished-request spend remains included as a lower
bound, including in failed attempts. An incomplete dollar total cannot establish
an overall saving.

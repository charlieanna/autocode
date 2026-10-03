# Evidence-bound Completion Owner repair assignments (#280)

A current Completion Owner REWORK previously always started a Resolver call,
even when the accepted report already contained an actionable repair. The runner
can now assign the first ordinary repair directly for a single serial milestone.
`autocode_rework_policy` makes this decision without another model call.

## Admission and fallback

Admission requires the unchanged approved task, criteria and affected paths; one
successful initial Builder; an independent, current Validator's executed FAIL;
and complete requirements, validation instructions and blocking defect evidence.
The loader seals normalized reports and original event/report files. Routing
verifies those hashes, runner identities and the accepted Validator body before
reusing the existing queue, retry and assignment guards. Runner-owned `check:N`
aliases are resolved for the comparison without rewriting the sealed report.

Incomplete or ambiguous tasks, repeat failures, prior recovery, report repair,
parallel/integrated work and progressive runs keep the Resolver path. Modified
evidence is refused. A pending human decision holds the transition before queuing
can clear it. An operator must resolve that decision through the existing controls.

The shortcut charges one ordinary retry through the existing Builder policy.
It records `direct_rework_assignments` in the public status view, with source and
assigned task identities, evidence hashes and the charge. This receipt establishes
assignment provenance; it is not a fabricated Resolver diagnosis or proof of
completion. Fresh required runner checks, independent Validator and Completion
Owner acceptance remain mandatory. Saved routes and reasoning settings are kept.

## Regression coverage

`completion-rework-direct` drives a real public CLI with scripted model responses
and actually executed greeting checks. Its oracle rejects the seed, a repair that
still accepts empty input, and a submission that weakens the tests. CLI tests cover
first repair, incomplete/ambiguous fallback, a broken repeated repair, pinned retry
exhaustion, and process death immediately before and after assignment persistence.
Both restart boundaries require one assignment/charge and no repeated reviewer
call. Policy tests additionally exercise tampered evidence, identity/scope changes,
foreign-run and symlink evidence, approval, milestone holds and human decisions.

The audit reproduced and fixed two issues in the initial implementation: accepted
Validator aliases differed from the still-correct sealed report, and fallback
queuing could consume an unresolved human request. The latter regression calls the
real queue implementation rather than a simplified queue double. The hold uses
actual waiting states, questions, requests and proposals; a historical approval
display by itself does not block an already approved running task.

A native Completion Owner then returned a complete repair with criterion status
`blocked`. Treating that non-passing status as a definition change unnecessarily
sent it to the Resolver. The final policy allows `unverified` and `blocked`
updates while preserving exact criterion identity and refusing new `verified`
claims. The original live fallback attempt is retained in the evidence.

## Live qualification protocol

The October 3 campaign compares immutable master `0a36c1e8` with this change on
the same small greeting fixture. Each arm independently prepares its approved
contract through the public CLI. Planning, the first defective Builder and its
executed failing Validator check are scripted. The first pair also scripts the
initial Completion REWORK; the second pair uses a native initial Completion
Owner. Every subsequent diagnosis, repair, validation and completion call is
native. These are hybrid repair experiments, not wholly live planning/build runs.

Routes are identical across arms: Astra High Resolver, GLM 5.3 Medium Builder,
Sol High Validator and Sol Medium Completion Owner. Sessions start fresh at the
measured boundary in both arms. Each arm has the same 900-second active,
300-second stage, 1,200-second wall and three-iteration limits. The independent
five-case oracle and protected test file are unchanged. All downstream native
calls, including extra Completion rounds, count toward the measurements.

The campaign preserves the original second candidate, which completed through
the Resolver, plus the final candidate retest after the `blocked` status fix.
Raw provider streams are retained unchanged. Costs use the frozen October 2
API rate card; they are API-equivalent estimates, not subscription invoices.
The first baseline's accounting adapter omitted model metadata; its corrected
estimate is reconstructed from the original raw streams, with the original
unpriced result retained. No failed attempt is discarded or reported as zero cost.

| Arm | Initial REWORK | Direct repairs | Native stages | Native requests | API-equivalent USD | Wall seconds |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Pair 1 baseline | Scripted | 0 | 4 | 19 | 0.99871288 | 460.62 |
| Pair 1 candidate | Scripted | 1 | 4 | 16 | 0.33295680 | 378.36 |
| Pair 2 original candidate | Native | 0 | 6 | 21 | 0.93281468 | 562.32 |
| Pair 2 baseline | Native | 0 | 6 | 25 | 1.04295620 | 666.05 |
| Pair 2 final candidate retest | Native | 1 | 5 | 18 | 0.33246100 | 362.69 |

All five arms reached current `TASK_COMPLETE`, passed the independent oracle and
fresh runner replay, and preserved the original tests and saved settings. The
audited contract bodies, role routes and limits match across all arms. Every
native request was priced: 99 requests, $3.63990156 total including the original
candidate fallback. Saved evidence hashes and final runtime hashes were rechecked.

Each admitted candidate avoided one Resolver launch. Pair 1's extra Completion
round offset that reduction in total stage count; the final Pair 2 candidate used
five stages versus the baseline's six. The final native Owner again reported
`blocked`, exercising the corrected admission rule. Only one narrow fixture was
tested, with one final native-REWORK candidate retest; these results do not measure
eligibility on general tasks or establish a whole-run saving percentage.

## Final verification

- Frozen candidate: changed-module gate passed 1,186 tests across 77 modules,
  including all seven public repair/restart tests and the architecture gate.
- A separate compatibility recheck passed 81 tests, including the previously
  failing findings, dependency, recovery and carry-forward cases. The two CLI
  tests that exceeded their unchanged 60-second limits in the concurrent full
  run both passed unchanged on recheck.
- The development full-suite run executed 2,914 tests across 214 modules and
  found failures in eight modules. Each failing case was subsequently covered
  by the passing frozen gate or explicit rechecks above. This is not a claim
  that the original full-suite invocation was green.
- The fake catalog produced 54 PASS, one NOT_EXERCISED and one SKIPPED. The
  refund-window fault was not reached; the skipped citation case requires a
  native Investigator. The scenario harness passed all 173 tests. Neither
  non-passing catalog classification is counted as a PASS.

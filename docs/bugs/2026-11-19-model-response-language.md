# Keep model-authored prose consistent across stages

The Kilo run `20261119-120551-vscode-1aab94` answered its English task in
English during Requirements, then drafted Chinese plan prose. The independent
Plan Reviewer accepted that content. The VS Code renderer faithfully displayed
the canonical plan; translating it there would change the material being approved.
The public support bundle has manifest
`aa8d6a2918d3166707f6ca321ee6611541d34bfaa3f9c743d5c25b6094e11e26`.

The cause was inconsistent instructions. Requirements and chat prompts specified
English; other stages, progressive planning and report repairs had no shared
response-language rule. A common text fragment alone would miss prompt builders
that do not include that fragment.

`autocode_response_language` now supplies one versioned policy at the shared
launch preparation step, before backend adaptation and native prompt sealing.
Newly authored prose defaults to clear English and honors an explicit request
for another response language. Artifact language and quoted source language do
not silently change that preference. Source quotes, literals, code, protocol
values and already approved or sealed material retain their original bytes.
Role-specific clarity instructions no longer impose a conflicting language.
This adds guidance, not a language detector or a new report-rejection gate.

The regression failed before the change across 13 stages, ordinary and repair
launches, and native and non-native preparation. It checks that handoff bytes
stay exact, explicit language requests remain available, quoted headings cannot
suppress the policy, repeated preparation stays idempotent and markerless
prompts stay markerless. Actual runner and native-image tests check that the
policy reaches provider adaptation before prompt sealing. Packaged imports load
the same resource. The 58 changed golden renderings were compared against master
and contained only the seven intended language-wording substitutions.

The bounded live task in the isolated VS Code project `language-fixture`, run
`20261119-155028-vscode-5789d5`, used the saved Kilo GLM-5.3 and independent
GPT-6 routes. It was configured for 5,400 active seconds, 900 seconds per stage
and 240 idle seconds, with `--max-cost-usd 0`. Its English request combined
Unicode names with exact greeting strings. Requirements, two automatic report
repairs and the canonical plan retained English and the exact Unicode literals.
GPT-6 Sol independently approved the plan. The operating agent reviewed its full
terms and approved revision 2 under owner delegation, with canonical body hash
`0bd40f7b0108bc56a34b87d117bffd3a89b8f6b326816d63ba3d1757d0431d72`;
the review Markdown hashes to
`2d15a63cc090f14b4212bb94b01de1880341e8cf1138a538070f7845a24a74b0`.
The run reached `TASK_COMPLETE` with `completion_current=true` and current
authenticated evidence: all four criteria passed, three tests failed before and
passed after the change, three checks replayed cleanly and the changed production
line's mutant was killed. The diff changes only the final punctuation in
`greet.py` and the three unittest cases in `tests/test_greet.py`. The independent
Tester checked nine name variants, including Unicode and whitespace. Builder,
Tester and Completion Reviewer evidence retained English. One Builder report
repair settled missing evidence references without repeating implementation.

The exact-plan approval used the public TaskRun interface after Mac screen/input
access stopped working. Once access returned, the actual VS Code window showed
the completed result, 4/4 criteria, two changed files, independent stage outcomes
and the retained automatic recovery history. This was an agent review under the
owner's delegation, not a claim of personal owner approval.

The run took 8m58s of execution and recorded $0.941506 ($0.630651 reported plus
$0.310855 estimated), with no unpriced stages; this is not an invoice. Its redacted
support bundle has manifest
`31444eec854ddeefaccdaa13b665982aea3086835da257f73aab564f09e40313`.
The shared prompt identity is
`0d7b684702480c50fda9249e057687826378c484f9ca1e8b38b85b3f4575f697`.
PR #3909 records exact-head CI and qualification limits; this one bounded flow
does not claim an all-model or all-language reliability sweep.

The PR was merged at `2105daae27` while CI was still running. Its coverage
advisory subsequently reported 15/16 changed statements executed, with the
packaged launch import as the one gap. The packaged regression now launches
through the packaged preparation module and asserts it uses the packaged
language policy, so a silent fallback to flat imports cannot mask a broken
installed entry. Standard-library tracing confirms that import executes.
This test and evidence update changes no runtime or prompt bytes and needs no
repeat paid run. The scenario prerequisite failure and post-merge master
corrections remain tracked in the maintained PR verification evidence.

The affected local selection passed 58 modules but exceeded an operator-set
300-second deadline on the repair module. That module passed all 122 tests on
master in 340 seconds; the candidate exposed one boundary fixture that omitted
the new shared guidance from its size calculation. Counting that guidance in
the fixture keeps the exact 256 KiB production cap and the refusal-before-launch
assertions intact. All 113 repair unit tests pass after that fixture correction;
the nine public CLI checks passed in the earlier full-module run.

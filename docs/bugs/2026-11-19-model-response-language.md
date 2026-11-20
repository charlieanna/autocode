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
terms and approved revision 2 under owner delegation, token
`r2:0bd40f7b0108bc56a34b87d117bffd3a89b8f6b326816d63ba3d1757d0431d72`;
the review Markdown hashes to
`2d15a63cc090f14b4212bb94b01de1880341e8cf1138a538070f7845a24a74b0`.
This records the planning checkpoint, not a claim that execution or final editor
verification passed. Subsequent run outcomes and exact-head CI are recorded in
PR #3909's maintained verification evidence.

The affected local selection passed 58 modules but exceeded an operator-set
300-second deadline on the repair module. That module passed all 122 tests on
master in 340 seconds; the candidate exposed one boundary fixture that omitted
the new shared guidance from its size calculation. Counting that guidance in
the fixture keeps the exact 256 KiB production cap and the refusal-before-launch
assertions intact. All 113 repair unit tests pass after that fixture correction;
the nine public CLI checks passed in the earlier full-module run.

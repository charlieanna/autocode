# Base-patch containment (#362)

An operator patch could manufacture a raw regression PASS even though the
candidate did not contain its behavior changes. The original check counted
added lines anywhere in a candidate file and ignored deletions. Three real
proof comparisons reproduced this: removing the original guard, replacing its
return with text from an unrelated function, and adding a duplicate return
before the guard. In each, the new test passed on the original and candidate,
but failed on the instrumented original. All three incorrectly produced PASS.
This demonstrates a raw proof defect, not a complete falsely delivered bug-fix run.

The correction applies the captured patch to a private Git index and compares
its actual blobs with the original and candidate. Every addition and deletion
must belong to an ordered candidate edit at the same original source location.
One candidate addition cannot satisfy repeated patch additions. Line anchors
are refined to exact tokens, including whitespace: replacing `os.rename` with
an injectable call remains valid when the real fix removes a surrounding
`try`/`except`, changing the call's indentation. The two genuine seam controls
preserve this case and the case that retains the handler and re-raises.

Git supplies canonical changed paths, including quoted names and deletions;
patches still cannot change tests. The caller's index and worktree are untouched.
File deletions and mode changes must exist in the candidate. New files, binary
files, non-UTF-8 files and type changes require exact matching content. Candidate
symlink parents are refused. Large repetitive text replacements that cannot be
compared within the matching bound remain UNVERIFIED; use a narrower patch or
prove the regression through the original API.

Hash binding and the mandatory semantic review reason remain. Syntactic
containment cannot establish that instrumentation preserves behavior: the
Tester and Completion Reviewer must still inspect that separately.

## Verification

The focused tests execute the public regression proof against real Git fixtures
and unittest processes, plus pure containment cases and file/path protections.
Before the fix, fresh live OpenCode/Sol and GLM each executed the same five-case
matrix. Sol completed its analysis; GLM hit the original 240-second stage cap
without an accepted answer. MiMo hit that cap before executing the matrix.
Those incomplete runs remain recorded, without increased budgets or retries.
Fresh final-code runs used the same case bytes, brief, proof driver and caps
(600 seconds total, 240 per stage, 120 idle, 3 iterations). Native OpenCode
exports confirmed the actual model IDs; all source and runtime hashes remained
unchanged:

| Actual model | Matrix execution | Accepted analysis |
| --- | --- | --- |
| `openai/gpt-6-sol` | All five outcomes correct | TASK_COMPLETE |
| `zai-coding-plan/glm-5.3` | No driver execution | PAUSED_JOB_FAILURE: 120-second inactivity limit |
| `xiaomi-token-plan-sgp/mimo-v2.6-pro` | All five outcomes correct | PAUSED_JOB_FAILURE: 120-second inactivity limit |

For both completed matrices, all three false-proof cases are UNVERIFIED before
instrumented tests run. Both genuine seams PASS with a named fail-to-pass test
and the semantic review warning. Sol independently explained each case and the
remaining semantic-review limit. It also retained a failed read-only fixture
assertion and its corrected assertion; no source was edited. TASK_COMPLETE here
means the Analyst's answer was accepted, not delivery of a complete bug-fix project.

GLM's earlier preflight stop used the same model for the unused Planner and
Analyst. Its before run resumed with the unused Planner set to Luna; all final
runs set that unused role explicitly up front. Analyst models and effective
execution caps did not change. Neither final timeout was retried or counted as
an accepted analysis. Evidence remains under ignored
`.scenario-runs/base-patch-proof-299/`; no run artifacts are committed.

Final deterministic gates: 30 changed-file tests across 3 modules (including
the 26 focused tests and architecture); 65 adjacent proof tests with 3 existing
skips; fake catalog 54 PASS, 1 existing NOT_EXERCISED and 1 live-only Investigator
SKIPPED. Initial fixture/assertion errors and a mistyped supplemental module name
remain in the ignored logs; the corrected commands passed. The full suite was
not rerun for this localized admission change.

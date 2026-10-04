# Bug-fix tests that use a seam the fix adds (#299)

In etcd #22498 the fix fsynced a directory through a package variable it added,
and the approved tests spied on that variable. On the unfixed code the test
package did not compile, so the regression proof failed with a generic
"write the test against behavior that exists before the fix". The operator then
rewrote the tests to expect a log line that only the fix printed. That passed
the proof, but so did a fix with the real fsync call removed and the log kept.
A log line is not proof of the behavior, and a compile error is not a
reproduction.

The proof stays FAIL. It now names the seam: identifiers the base run reports
missing (Go `undefined: X`, Python `cannot import name 'X'`, ...) that the
candidate's source change adds and its tests use (`autocode_proof_seam`). The
reason says what to do next. Drive the real failure path through APIs that
exist before the fix and assert the behavior itself, such as an error, a result
or saved state. If no such API reaches the path, report that the bug needs an
instrumentation-only base patch. The Builder, Investigator and planning prompts
say the same before any test is written. For the etcd case, the owner found
such a probe: a destination directory that is writable but not readable makes
the post-rename directory open fail, and the same test builds and runs on the
unfixed and the fixed code.

Python runtime setup failures now have per-test attribution. The old runtime
could accept an unchanged, broken `save()` after the candidate added an unused
`replace_file` alias: the regression test only mocked and called that alias.
On base it failed in `unittest.mock`, before `save()` ran; on the candidate it
passed. Fresh native OpenCode Sol, GLM and MiMo calls each reproduced the PASS
label alongside an actual `save()` call that still swallowed the rename error.

`autocode_test_setup` recognizes explicit missing mock targets and test-origin
import errors in unittest tracebacks and pytest JUnit failures. Every observed
frame must belong to test code or `unittest.mock`; product frames, unknown
helpers and chained exceptions remain unclassified. Recognized setup failures
stay in the saved test results, with their names and reasons, but are excluded
from bug-fix `fail_to_pass`. A required case cannot borrow another test's valid
behavior proof. The diagnostic asks for an existing-API test or separately
approved instrumentation. Genuine application `AttributeError`s and direct
public-attribute assertions remain eligible. Feature `new_behavior` proof
continues to allow newly introduced APIs.

The real five-case control now rejects import-only and runtime mock hooks,
rejects a broken implementation with an unused mocked hook, accepts an actual
existing-API fix, and rejects the broken implementation under that behavior
test. These controls use actual Git trees and test processes. Sol completed a
fresh live review and ran all 19 focused tests (including pytest and Go) plus
the five controls. MiMo ran the same tests and controls but timed out before a
final review. GLM's broader code review timed out before executing the requested
tests. These incomplete reviews remain recorded; they are not passes. A separate
focused GLM Analyst run subsequently completed: it executed the unchanged broken-
application probe, observed FAIL with no fail-to-pass tests, and independently
confirmed the still-broken application behavior. The controller replayed its
three executable claims successfully. This qualifies the execution path, not
the timed-out code review. All runs retained the original 240s stage / 120s idle
limits and unchanged source/test/runtime hashes.

An operator base patch (`--base-patch PATH`, `autocode_base_patch`) covers a bug
that can only be observed through a seam: it adds just the seam to the original
code, so the seam test runs and fails there. The runner cannot tell whether a
patch only adds instrumentation, so it is bounded: pinned by hash, no test files,
it must apply to the base and its edits must occur at corresponding original
source locations in the final change. Every proof carries a review reason for
the Tester and Completion Reviewer. This is a syntactic containment check, not
proof that instrumentation preserves behavior. The original whole-file line
check was insufficient; see [the #362 correction](2026-10-04-base-patch-containment.md).

Still open:

- General attribution across other frameworks, hidden helper frames and
  ambiguous runtime failures. This change is intentionally narrow. Missing-name
  failures outside the recognized setup cases retain the review warning; it
  does not promise that every test using a new hook is automatically rejected.
- Native OpenCode review admission rejects even permitted new `review/tests/`
  evidence (#332). Sol and GLM reproduced this separately during the before
  checks; existing source/test/runtime hashes stayed unchanged.

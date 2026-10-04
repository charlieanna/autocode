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

Still open:

- An operator-supplied base patch for a new run. The runner cannot tell whether
  a patch only adds instrumentation. Requiring the candidate to contain the
  patch limits what the Builder controls, but a patch that changes behavior
  would still let the rest of the change flip tests without fixing the bug.
  Today `review_reasons` reach only the Validator and the Completion Owner, so
  no person is guaranteed to read the patch.
- A Python test that reaches the seam only at run time
  (`mock.patch.object(store, "replace_file")`) runs and errors on the unfixed
  code, so it counts as fail-to-pass. A negative control with the fix reverted
  still fails that test. Rejecting such tests would need per-test error
  attribution, which could also reject genuine `AttributeError` reproductions.

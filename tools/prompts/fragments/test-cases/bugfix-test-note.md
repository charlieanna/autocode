
BUG FIX TESTS: each regression test must build and run on the unfixed code. A test of behavior the fix
restores must fail there because of the bug; a guard: (preserve) test must pass there and after the fix.
Do not make a test import or reference anything the fix adds (a new function, package variable, hook or
injectable seam): on the unfixed code such a test only fails to compile or import, which is not a
reproduction, and adding the seam with the fix does not change that. Drive the real failure path through
public APIs that exist before the fix (for example a real file, directory or input that makes the failing
operation fail) and assert the behavior itself: the returned error, the result, the saved state. A log line
or message alone does not prove the behavior.

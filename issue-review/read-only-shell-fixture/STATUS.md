# Read-only shell containment test fixture

The AC3 test in `tests.test_read_only_shell_input` calls `containment.prepare`
directly and did not include the interpreter's dynamically linked dependencies.
On macOS with Homebrew Python, this hid OpenSSL from the sandbox and the test
failed before report-check started. The fixture now supplies the read-only
roots and symlink hops from the same toolchain discovery used by the ordinary
contained provider launch. The test assertions and containment policy are
unchanged. This correction changes the fixture only, not a product decision or
what the test requires.

On current master `6b803f17ea359252f06a27377757575680648eb3`, AC3 failed with
`libssl.3.dylib` blocked by Seatbelt; AC1 and AC2 passed. An independent control
of that same AC3 with discovered runtime roots and links passed. On this branch,
`tests.test_read_only_shell_input`, `tests.test_handoff_injection`,
`tests.test_report_check` and `tests.test_toolchain` passed 27 tests on macOS.
Ruff check and format check passed for the changed test, and mypy passed for all
990 source files. CI remains to be run. Issue #3954 remains open: a passing test
on this host does not authenticate the original run's reported cause or
implement its recovery.

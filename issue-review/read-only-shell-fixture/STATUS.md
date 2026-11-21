# Native Kilo runtime discovery and read-only shell test

The ordinary contained provider launch discovers its declared tools and Python
runtime dependencies before creating its sandbox. Native Kilo did not share
that discovery: its whole-client and shell profiles received interpreter roots
but not dynamically linked dependencies. On macOS with Homebrew Python, the
kernel blocked `libssl.3.dylib` and even `python -c 'import ssl'` failed under
the native client policy. The incident's AC3 test fixture made the same omission.

Native Kilo now discovers the runtime once before launch and supplies the same
read-only roots and symlink hops to both boundaries. Linux's whole-client mount
planner records discovered symlinks, verifies their host targets before
replaying a policy, and refuses links into hidden authority. Saved policies
without links retain their original shape. No source write or hidden-run access
is granted. The shell test fixture uses the same discovery and keeps its
original containment and assertions.

On master `6b803f17ea359252f06a27377757575680648eb3`, the new offline native
client-policy regression failed because Seatbelt blocked OpenSSL. The candidate
passes it on actual macOS Seatbelt, alongside the copied-source write-denial
controls. AC3 likewise failed on master and passes with the original assertions.
The focused Kilo, Linux-policy, shell, toolchain, architecture and state suites
passed 63 tests with six Linux-only skips on macOS. Ruff lint/format passed for
`tools/ tests/ scenarios/`, and mypy passed for 990 source files. The new real
Linux symlink regression is selected by `linux-tool-containment`; Docker's
daemon is unavailable on this Mac, so its actual Linux execution awaits CI.
The final client/shell wiring and Linux policy replay controls passed nine
additional focused tests. Two earlier runs of the new mocked wiring control
failed on missing fixture fields and passed after the fixture was completed.

Issue #3954 remains open: these tests do not authenticate the original run's
reported host cause or implement recovery. The later fresh-host observation is
not itself evidence that a project test passed.

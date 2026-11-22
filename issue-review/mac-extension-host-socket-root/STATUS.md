# macOS VS Code fixture socket path (#4088)

The macOS Extension Host closed before opening a window for the first-Go and
protected-directory cases. Browser diagnostics show VS Code rejecting a profile
IPC socket longer than 103 bytes with `listen EINVAL`. The case reproduced with
the normal macOS per-user `TMPDIR`. Both actual cases passed on the integrated
proof-gates checkout with a short fixture root and the same VS Code, Node 22,
Python and runtime.

Eight stock host fixtures on master used their own `os.tmpdir()` allocation.
They now use the existing `privateHostRoot` helper. The regression exercises
actual quota and Kilo host setup callbacks, requires that helper and checks that
a setup failure retains evidence without stopping the operator's inherited host.
Both new assertions failed on pre-change master `b06e3d4268`. All four focused
fixture and stock cleanup cases pass. The extension build succeeded, and its
unit and webview selection passed 806 tests with 22 explicit skips.

Exact-candidate actual macOS execution with the normally inherited `TMPDIR`
and GitHub required CI are pending. The earlier actual macOS pass used the
short-root workaround on the proof-gates checkout.

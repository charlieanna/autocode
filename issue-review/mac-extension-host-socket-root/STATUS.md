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

The actual macOS VS Code Extension Host passed the first-Go end-to-end test
on product-equivalent commit `e69d87bc7`, with the normal inherited per-user
`TMPDIR`, the shared helper selecting the private profile root, and normal
owned cleanup. Ruff and mypy (999 files) passed. GitHub required CI is
pending. The evidence log is `/private/tmp/autocode-mac-socket-root-exact-host.log`.

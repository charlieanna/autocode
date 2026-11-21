# Job route editor-host readiness

The #4022 Linux host run at 6645afdfa30ec222b8a99dbf40a02d43e2145f12 passed the synthetic Kilo case, but the existing Codex host test saw a route input while the chat still rendered “Opening this chat…” and asserted its explanation too early. The preserved log is `/private/tmp/autocode-4022-extension-6645.log`. This is a test readiness race; the extension and core product code are unchanged.

The host test now waits until the route field is visible and both expected explanation strings are present in the same chat frame, before asserting or sending. A deterministic unit control rejects the observed opening state, a hidden field, and either missing explanation, and accepts the complete state. A mutation that reduces the readiness rule to the old field-only condition fails that control (`/private/tmp/autocode-route-readiness-old-predicate.log`). The original #4022 exact-head failure is the real-host pre-fix observation; the host fix needs Linux CI before merge.

On this branch, the full extension unit suite passed (784 passed, 22 skipped); repository-wide Ruff check and format check passed, and `mypy tools/` passed (993 source files). Actual Linux Extension Host and `ci-required` are pending on the PR head.

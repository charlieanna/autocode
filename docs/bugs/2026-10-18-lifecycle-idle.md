# Lifecycle-only events do not establish running tools

Issue: #1562; editor acceptance: #1528.

Live run `20261018-065129-vscode-0b0548` on `55f4bedd` used explicit
600-second stage/tool and 300-second idle limits. Two Plan Reviewer attempts
emitted only Kilo `step_start`, yet a persistent provider child suppressed idle
until the stage deadline. Redacted bundle manifest SHA-256:
`d923fce5df0ecb8177bf16f1e1e23bb7d99d91cc7275e0a43d5b95b02aeedc08`.

Lifecycle rows now renew provider idle once without unlocking process-fallback
tool inference. Substantive progress and explicit tool events keep their
existing deadlines. No limits, defaults or model routes change. This enforces
the configured bound; it does not establish why MiMo was silent or guarantee
that planning will complete.

Validation: 57 activity, idle-policy and mocked supervisor checks passed. Five
focused process tests passed on the supported Linux host, covering lifecycle-only
and empty-log idle expiry, completion-only tool grace, explicit tool deadline
and detached-writer cleanup. New fake-clock regressions expose the original
lifecycle fallback error. No additional live models were called.

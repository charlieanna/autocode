# VS Code dollar-cost visibility (#3832)

Base: master `e16d680d80e0a65b0d72ef5074fcb1698fdfaad0`.

The extension reduced the public cost ledger to an amount and completeness bit. It
displayed `$?`, dropped the core's missing-cost reasons, left unpriced stage cells
blank, and discarded a session's known subtotal when another chat was unpriced.
Details also coupled dollar completeness to token-attribution completeness and
presented empty accounting sums as provider-reported charges or estimates.

The fix reads the existing `cost.breakdown.unpriced` reasons in one shared display
helper. The chat meter, status tooltip and Details explain unavailable versus
partial versus complete recorded cost and separate provider amounts from dated
estimates. Unavailable dollars never hide known tokens or another chat's known
subtotal. Keyboard focus exposes the explanation; the meter wraps at sidebar
width instead of clipping tokens. Pricing, model routing and cap policy are unchanged.

Offline native Kilo/OpenCode controls pass positive, zero and absent request-cost
receipts through the actual event normalizer, request accounting and cost view.
Positive amounts survive a failed attempt and its report repair on a custom model
route; zero/absent amounts with no exact saved rate stay unknown. This does not
establish whether the original incident's provider supplied billing data: that
run's native receipts were not attached to the issue.

## Verification

- New display regressions fail on the base: unavailable/partial/complete cost,
  missing usage/model/rate, legacy missing reasons, unpriced attempts with no
  tokens, mixed priced/unpriced chats, and independently incomplete token data.
- The generated-webview regression covers accessible explanations, receipt changes
  with an unchanged amount, focus retention and clearing obsolete reasons. The
  sidebar wrapping regression failed with `white-space: nowrap` before correction.
- Local focused core/accounting and architecture checks: 99 passed. Native receipt
  controls are included. CI's changed selection passes 58 tests with normal process
  visibility. The sandbox initially blocked process enumeration in two CLI modules;
  both also failed on clean master there, and pass outside that sandbox. Extension
  typecheck, build and lint/format checks pass.
- Actual VS Code Development Host, native Kilo 7.8.27 installed: explicitly
  scripted core receipt cases show `Cost unavailable`, `$0.35+` partial, and `$0.50`
  complete recorded cost, each beside known tokens. Tab/Shift-Tab reaches the meter
  and exposes its explanation. Dark 2026 and Light Modern remain readable. Sidebar
  inspection caught clipping of the longer unavailable label; after correction all
  token text wraps visibly. Final editor inspection also confirms complete dollar
  coverage alongside incomplete token attribution without contradictory labels.

## Live model verification

A separate plan-only run was started through the actual extension on the isolated
standard-library greeting project. It retains the backend's selected GLM-5.3
Requirements/Planner and GPT-6 Sol independent-review routes, with 900 seconds
total, 240 seconds per stage, 120 seconds idle, the existing planning-call cap,
and test-policy `--max-cost-usd 0`. The first run stopped before any model launch because Kilo
reports API authentication for the saved OpenAI routes, whose policy requires
OAuth. No paid retry was made before classifying this preflight failure.

Run: `20261119-104924-vscode-1780bc`. Redacted preflight bundle manifest:
`8c30f86880637ea344610b0ea05c0dda08e1a4c5b036b1d1d6819a7feba8a526`.
The bundle stays outside the repository.

The owner clarified that Kilo holds the OpenAI API key. For the isolated follow-up,
the operator copied the existing provider profile and declared OpenAI `api` login
and pay-per-token billing instead of subscription OAuth. This retains a strict
login-mode check; model IDs, reasoning levels and backend credentials are unchanged.
The profile and a copy of the publicly listed stage choices live in a temporary
XDG config root used only by this fixture. User-global configuration is untouched.
The bundled Kilo profile's OAuth assumption remains a separate setup limitation.

Follow-up: `20261119-110654-vscode-facb9d`, started through the actual extension.
Requirements completed on GLM-5.3 low; the operating agent reviewed and confirmed
its exact greeting-punctuation scope under the AGENTS.md delegation. Native model
preflights returned positive dollar receipts. VS Code showed those provider amounts
separately from the dated GLM token estimate while Planner ran. The first Planner
attempt reached this test's 240-second deadline; its missing cost stays explicit,
and the core retained the attempt and automatically retried the same model under
its bounded timeout-recovery policy. The final reviewed-plan outcome and exact-head
CI are still required before merge.

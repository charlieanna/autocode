# 2026-11-07 hands-on TUI review: UX findings

**Severity:** mixed (each item below says)
**Status:** Open — findings record; the first two fixed items landed as pull requests
**Found:** 2026-11-07, hands-on session driving the full-screen `autocode chat`
(offline fixture provider, `tests/test_agent_step.fixture_workspace` +
`tests/test_taskrun.FIXTURE_OPTIONS`) in tmux at 100x30, 60x24, 40x14, 30x12,
20x8 and 180x40, on master `9628a7cfe`. The shipped screen tests
(`tests.test_chat_screens` 17/17, `tests.test_chat_attention_screen`,
`tests.test_chat_cursor`, `tests.test_chat_markdown`, `tests.test_chat_contrast`,
`tests.test_chat_client` 153/153) all pass; these are gaps the tests do not
cover.

## 1. WITHDRAWN — "keystrokes straddling a prompt boundary are split"

First recorded as a High finding: a typed `/plan` at the completion gate seemed
to lose its leading `/` and start a new task with the brief "plan". Re-examining
the probe showed the harness typed the commands **without** the slash
(`screen.type(cmd)` with `cmd="plan"`), so the TUI correctly treated the bare
word as the follow-up reply the gate prompt announces ("Any text starts a new
task in this chat"). No boundary race was demonstrated; the deliberate
type-ahead discard (`ChatTUI.ask` → `_discard_typeahead`) stands as designed and
tested. Kept here as a record because the retraction matters: verify the probe
before the finding.

## 2. Any stray text at the completion gate starts a new task (Medium, open)

"This task is complete. Anything else? Any text starts a new task in this chat"
is honored literally: a single stray character (observed: `n`, typed as a reflex
after `/undo`'s not-a-question message) becomes the brief of a new task with no
confirmation. A minimum-effort guard (confirm before a reply shorter than a few
characters starts a new task) would remove the trap without changing the flow.
Touching this changes the follow-up contract the view offers, so it needs a
core-side decision, not a client patch.

## 3. Recovery internals leak into the person's transcript (Medium, open)

When the run enters report-repair recovery, Notice blocks print raw schema
rejection text verbatim (`autocode_recovery_history._rejections` passes
`last_error` through), e.g. `Recovery · Investigator answer rejected (3 times)
— $: missing counterexample` and `Completed requirements_gather_report_repair
output was rejected ($: missing message_id; $: missing contract_token; …
unexpected fields: acceptance_tests, access_blockers, conflicts, constraints,
design_document, ignored_requirements, …)`. This is machinery language a person
cannot act on, which the AGENTS.md person-involvement rule says should not
reach them. A one-line person-readable summary with the detail kept in evidence
would fit the contract; the fix belongs where the notice is composed.

## 4. Footer hint truncation hides the quit/scroll hints at common widths (Low, fixed)

At 100 columns the gate footer `Requirements · <$0.001 · 20 tokens · enter send ·
\⏎ newline · / commands · ↑↓ history · pgup/pg…` clips the tail
(`tools/autocode_chat_tui.py::_hints` truncates after dropping only the worked
time). The `ctrl-d quit` hint — the only escape from the full-screen app — was
the first casualty, hidden exactly at the decision screens; it only fit on the
new-chat screen where there is no status prefix. Fixed by dropping the optional
hint segments (`\⏎ newline`, then `pgup/pgdn scroll`, then `↑↓ history`) before
truncating, so `/ commands` and `ctrl-d quit` are the last to go.

## 5. The `/` command menu has no more-entries indicator (Low, fixed)

`_command_menu` caps at `MENU_ROWS = 10` and scrolls a window over up to ~22
commands with no marker; with a run open the list ended at `/changeset` and the
rest (`/runs`, `/new`, `/mode`, `/engine`, `/connect`, `/stages`, `/repos`,
`/attention`, `/panel`, `/help`, `/quit`) were invisible with no hint they
exist. Fixed with a dim count row (`↓ N more; ↑↓ chooses`, `↑ N above` once
scrolled down) under the visible entries.

## 6. Tab status glyphs have no legend (Low, open)

`TAB_MARKS` renders `●` (working), `!` (needs you), `✓` (done) beside each tab
(` 1 plan !   2 live ●`) with no on-screen explanation; "unavailable" is spelled
out but the three symbols are not. A legend line on `/help` (or under the tab
bar when two or more chats are open) would close it.

## 7. Smaller items (open)

- Repeated refused commands each append an identical info block (three
  `▌ /tab  Tabs switch once AutoCode asks you something…` blocks stacked during
  one session); `coalesce=True` is used for the working notice but not these.
- Ctrl-T while a run is active only prints the refusal above; "Start a new chat
  in a new tab" is the advertised behavior in `/help`.
- At the completion gate, Enter on an empty composer exits the whole chat even
  while a `/diff` listing that says "Type /diff N…" is on screen; the
  finish-on-Enter rule is in the gate prompt, which the listing scrolls away.
- The status line suffix `(status read 0s ago, run state at 20:48:26 UTC)`
  consumes ~50 columns of the prime status row; at 60 columns it crowds out the
  actual headline, and the UTC clock is not the person's local time.
- Footer cost meters `chat $0 · session $0.0029+` (this run vs all open chats)
  are unlabeled; the distinction is only in the code.
- Composer and transcript wrap hard mid-word (`…login tes`/`t the…`; plan
  continuation lines fall back to the block indent rather than under their
  parent), which hurts scanning of wrapped plan text at 60 columns.
- On a 180-column terminal the transcript runs to ~130 columns with no reading
  measure; the welcome centers, the conversation does not.
- While the `/` menu is open the footer still says `↑↓ history`, but ↑↓ then
  selects menu entries (and Tab completes, unadvertised).

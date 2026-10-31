# TUI gaps from a live GLM+MiMo run (2026-10-31)

Findings from two live `glm-mimo-kilo` runs of `bugfix-cent-drift` on master
`a7b871bb1` — one headless through the scenario harness (PASS, 94 min, $2.07),
one through the full-screen chat TUI (`autocode chat --mode full`, TASK_COMPLETE,
~66 min, $2.11; oracle graded outside the run: 9/9 project tests, 14/14 hidden
tests, new tests fail on the seed). Most observed issues were fixed the same day
(`fix/tui-live-run-issues`); two remain open gaps.

## Fixed in `fix/tui-live-run-issues`

2. **`F8: attention unavailable` carried no reason.** The badge now names the
   underlying error.
3. **A launched run with no status view yet read as "Reading run status…"** while
   activity counters already ticked. The line now says "Run started; waiting for
   its first status view…" (status line and composer placeholder).
4. **A directory outside any Git checkout silently bootstrapped an empty task
   project** under `autocode-projects/`, so the run could not see the files
   already there and stopped to ask where the code should come from. A new chat
   in such a directory is now confirmed first (`guard_workspace`,
   `autocode_chat_client`).
5. **An issued request whose recovery names `--resolver-response
   provide_information` offered no in-chat action** (reported, not fixed). In the
   live run (an empty bootstrapped workspace asking where the code should come
   from), the chat fell through to a stop whose reason included the raw
   `AUTOCODE_RESOLVER_TOKEN=…` command, and the viewer exited. Extending the
   chat view's `resolver_response` offer to the `clarification` scope was tried
   and reverted: the needs table names `--answer` for clarification questions
   (pause_command), so the blanket offer hijacked every spec-first requirements
   review (nine TerminalChat regressions). The fix belongs in the needs table:
   the chat input offer should follow the command `pause_command.command` names
   for the exact need, and the one live-run stop that named the message command
   needs its own captured view before it is changed.
8. **A plain completed run rendered "current delivery unknown".**
   `delivery.verified_complete` is the dependency-consumer receipt and is absent
   for ordinary runs, and the view deliberately withholds the caller-supplied
   `completion_current` from the public projection
   (`tests/test_replay_transport.py::PublicReplayProjectionTests`). The verdict
   is already public as `view.efficiency.delivery.current_completion`
   (autocode_efficiency), so the chat's verification panel now reads that when
   no receipt exists: "Complete; current delivery verified" when the gate passes.
   The dependency receipt still wins when present.

## Intended behavior, not changed

1. **A refused start closes the full-screen chat and prints the error on the
   terminal.** Pinned by
   `tests/test_chat_client.py::TerminalChatTests::test_an_error_before_the_conversation_is_shown_after_the_full_screen_closes`
   (the message must land after the alternate screen closes). The launch mistakes
   in the live run were mine (self-build guard, workspace inside the repo); with
   the non-Git guard (fix 4) the third one is prevented too. Changing the
   error surface needs the owner's call, not a drive-by fix.

## Open gaps (reported, not worked around)

6. **Reasoning text is unavailable for GLM/MiMo through the Kilo Code provider.**
   The live pane shows "Reasoning stream activity received; text unavailable."
   The provider's event stream carries reasoning deltas as content hashes only,
   and `chat.log`'s redacted display text has nothing to show. A person watching
   the TUI can see *that* an agent reasons and which files it touches, but never
   what it thinks. Fixing this needs provider-side reasoning text (or an
   unhashed channel), not client rendering.

7. **A prose reply at a Requirements review costs an extra model round.** When
   the stop carries open questions, the prompt is free text ("You: ") by design —
   the model must read the answer. The `[y/N]` fast path exists only for the
   pure review stop (`chat.review`). Not changed: mapping prose like "yes,
   proceed" to the approval action would let free text approve, which the
   contract forbids.

Run evidence: `.scenario-runs/20261031T032814Z-bugfix-cent-drift-glm-mimo-kilo-u0wxners/`
and `.scenario-runs/tui-glm-mimo-20261031/` (ignored directories; ask the owner
before deleting).

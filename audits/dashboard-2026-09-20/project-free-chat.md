# Project-free chat and unified task conversation

Implemented directly in `/Users/ankurkothari/Documents/workspace/agent-console` on 2026-09-20. Autocode was not used to implement these changes.

## User flow

- Start a saved conversation with GLM without selecting a project. GLM asks questions and drafts the idea through OpenCode with tools denied and no repository attached.
- Keep conversations, unsent drafts, model choices, and selected views across reloads. Failed replies expose a retry action.
- Attach an existing project or create a new repository when ready for repository-aware joint planning. The original transcript follows the task; Astra reviews after attachment.
- Answer task questions and send direction from one composer. Messages display saved delivery state. Answering the last pending question continues planning without approving the plan.
- Read and approve the finalized plan through a button. Approval records the exact saved revision; starting implementation remains an explicit Continue action.
- Keep model controls in a disclosure, with conversations and tasks separate in navigation and projects available as task filters.

## Reliability

Conversation files and human-message receipts are written atomically. Provider turns, attachment claims, and task-message submission use cross-process locks. Request identifiers prevent duplicate submissions after a lost response or reload. Failed attachment retries use compare-and-swap so simultaneous retries launch once. Uncertain attachment state does not automatically relaunch. Task-storage paths and planning artifacts must stay within their registered workspace. Interrupted replies and receipts are reconciled visibly after restart.

## Verification

- Final Python suite: **110 tests passed** in 22.876 seconds. Includes concurrent retry/send cases, durable receipt writes, interrupted provider recovery, path containment, existing registry actions, model routing, and plan approval contracts.
- `node --check dashboard_app.js` passed.
- `node tests/test_planning_ui.js` passed, including payloads, question targeting, Markdown safety, chronological sorting, persistent request IDs, and slow-response polling.
- Browser test used **two actual OpenCode GLM-5.3 responses**: an initial clarification and a short exercise-tracker plan. Confirmed an unsent reply and the conversation survived reload.
- Attached that conversation to a disposable project using a **fake task runner**; verified transcript handoff, answer submission, automatic planning continuation, and approval without starting implementation. The task side of this test did not call real Astra or coding providers.
- Restarted the isolated dashboard and confirmed conversation, answer receipt, and approval remained visible.
- Checked desktop and 390px mobile layouts; mobile document width matched viewport width without horizontal overflow.
- Installed files exactly matched the tested staging files. The updated live dashboard on `http://127.0.0.1:8767/` loaded 24 existing tasks and 16 projects with no captured browser JavaScript errors.
- Restored the user's unsent exercise-tracker idea on the live starting screen. Did not submit it or alter existing running tasks. Stopped the isolated test server; left the live dashboard running.

This verifies real GLM chat and a simulated task handoff, not an end-to-end real coding run. Existing unrelated audit findings remain outside this change.

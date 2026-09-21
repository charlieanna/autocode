# Dashboard joint planning fix — 2026-09-20

Applied directly to agent-console, without using Autocode to perform the implementation.

New default browser tasks now pass `--engine opencode --joint-planning --no-chat`.
GLM conversation/draft/revisions and Terra implementation default to Z.ai GLM-5.3 through OpenCode. Astra plan review/finalization and Sol verification use Codex, with separate model controls. Existing runs retain their saved model routing. Legacy Codex creation now explicitly selects Codex and disables terminal chat.

The dashboard displays the saved planning mode, GLM model, per-role engine, and the actual author of the latest planning summary. Joint discovery report repairs retain GLM labels. Intermediate drafts cannot display the approval control; the finalized Astra plan does. GLM-first creation text switches correctly for explicitly selected legacy Codex mode.

Verification:
- 66 Python tests passed, including production runner parser/configuration contract checks with all provider subprocesses blocked.
- JavaScript syntax check and planning label/approval helper tests passed.
- Isolated browser fixture: New task → Talk to GLM → GLM question → Save answer → Continue → Astra final plan and approval control. Intermediate approval was hidden. The fixture cannot launch model providers.
- Browser command log verified the joint-planning flag. Fixture stopped and tab closed after testing.
- Applied files match staged tested files. Live dashboard restarted only after checking all dashboard-owned actions were idle. Independent task workers were not stopped or resumed.
- Live browser shows Talk to GLM and the four role selectors with real Z.ai catalogue choices. The user's exercise-tracker idea was preserved across reload; it was not submitted.

Scope: this fixes model routing and labels. Project-independent draft conversations and the broader audit findings remain separate work. No live provider end-to-end execution was performed for this fix.

# Task state and approval clarity

Implemented directly in the sibling `agent-console` dashboard, without using Autocode to implement changes.

## Changes

- Task links open a **Now** view. It shows the current workflow stage, the exact decision needed (or no approval pending), what to click, what follows that action, the current assignment, and the last saved step.
- The persistent header shows the current plan revision and its approval status, alongside the iteration counter. Iterations are described as work cycles, not completed milestones.
- GLM implementation is identified by the saved model assignment even in a legacy workflow that names its implementation role Terra.
- Current plan approval controls name the exact revision and appear near the top of the plan. Approved plans state that no plan approval is pending. A legacy draft cannot offer approval merely because joint planning is disabled; the saved state must actually request approval.
- Human output-review controls appear at the top of Checks, followed by evidence. Approval remains tied to the existing result token; Finish task remains a separate action.
- The approved plan scope and the current execution approach have distinct labels. Previous drafts and assignments remain in collapsed history.
- Saved answers now retain their original timestamps and question context and are sorted together with planning messages and chat receipts. Previously, all saved answers appeared before the planning messages regardless of date.
- Conversation messages display timestamps. Saved planning discussions are labeled and collapsed; Now is the source for current decisions.

## Verification

- Eight JavaScript test scripts passed, covering existing chat, routing, archive and project controls plus new cases for current approval authority, resume/start decisions, model labels, workflow phases, and chronological messages.
- A disposable browser fixture used a read-only DDIA snapshot plus synthetic question, plan approval and output-review tasks. It cannot invoke a runner or mutate real task state.
- Browser checks exercised Review plan → Approve plan revision 5 → Start building as separate actions. Human review exercised Review output → Approve C1 → Finish task → Complete. Answer question opened the corresponding question and reply box.
- At 390px the document had no horizontal overflow and the entire decision card remained readable. Default desktop layout was inspected; the temporary viewport override was reset.
- Live DDIA verification after installation showed a running worker, GLM implementing at iteration 16, approved plan revision 4, and no pending approval. No live task approval, pause, resume, or message was submitted.
- Final browser checks reported no console errors or warnings.

## Deployment

Installed `dashboard_app.js`, `dashboard.html`, `dashboard.css`, and `tests/test_task_decisions_ui.js` into `/Users/ankurkothari/Documents/workspace/agent-console`.

Original files and installation backups are retained under `/private/tmp/autocode-task-clarity.J3geTJ`. Installation checked original source bytes for concurrent edits, verified no dashboard actions or child processes were active, and restarted only the dashboard server on port 8767. Eight saved task records were preserved. No runner or approval protocol was changed.

Browser approval tests used synthetic responses; this change did not retest paid model output quality.

# Conversation-first dashboard redesign

Implemented directly in the sibling `agent-console` project. Autocode was not used to implement this change.

## Experience

- A compact sidebar and a single task workspace. Conversation is the default; Plan, Changes, Preview, Checks, and Activity open on demand.
- The composer stays visible while history scrolls. Earlier messages and long messages are collapsible, with the full text retained.
- Needs you shows the exact question, approval, or review requested, grouped by task. Paused work is a separate expandable list. Multiple pending questions appear one at a time, with the selected question explicitly matched to the reply box.
- All work includes project-free planning conversations and registered tasks. Attaching a project opens the same discussion and preserves an unsent planning draft.
- Header actions follow saved state: Answer, Review plan, Pause, Resume, Review output, or Finish task. Running tasks do not offer Continue. Approval still records the exact finalized plan revision; starting work is a separate action.
- Current goal titles replace long instructions where a validated goal exists. Models and project/archive controls are available in task settings. Successful archive/remove notices offer a temporary Undo.
- Changes shows steps that changed files, saved diffs, and bounded current-file previews for new files absent from a Git diff. Current file content is explicitly distinguished from a saved snapshot.
- Checks shows saved criterion results, verification commands, evidence references, and end-to-end results. This view does not run checks or claim that old evidence is newly verified.
- Preview embeds an explicitly entered local development address. It does not launch a development server. Only loopback HTTP(S) addresses on a different port are accepted; leaving the panel removes the frame.

## Verification

- Python regression suite: 148 tests, 147 passed and 1 skipped. Includes new path containment, symlink/FIFO rejection, bounded reads, current-file allowlisting, cross-origin protection, and archived-task evidence denial.
- Seven JavaScript checks passed, including state-specific actions, historical questions, exact approval gates, preview address validation, existing archive/restore, task routing, and idempotent chat requests.
- Browser lifecycle: real Autocode runner with **offline fake providers**, isolated registry and disposable Git project. Created project-free chat, sent a follow-up, attached a repository, answered a planning question, reviewed the GLM/Astra plan, approved it, and explicitly started implementation.
- While implementation was held at a fixture barrier, sent feedback and requested Pause. The receipt moved to Applied. Restarted the test dashboard, resumed, and confirmed the revised plan required a fresh approval.
- Resumed implementation, inspected saved verification, approved the fixture's human-review criterion, and used Finish task. The runner reached `TASK_COMPLETE`.
- Viewed the new `greet.py` through the recorded-files view. The app preview loaded a separate disposable browser fixture and returned `Hello, Ada!` after form input.
- Responsive browser checks at 390px and 720px: document width matched viewport width, the composer remained inside the viewport, and leaving Preview removed its iframe. Desktop layout was also visually inspected.

## Scope and deployment

Staging and original source backups: `/private/tmp/autocode-workspace-redesign-ro7c_zrf`.

Existing task checkpoints, model assignments, approval contracts, and provider processes are preserved. Source hashes are compared against the initial snapshot before installation so concurrent edits cannot be silently overwritten. Only the dashboard web process is reloaded after checking its action queue and child processes.

Installed and reloaded on the existing `http://127.0.0.1:8767` dashboard. Live checks confirmed all eight visible tasks and six registered projects were preserved. On the real Kubernetes task, switched between pending questions and verified that the displayed question matched the reply target; no answer was submitted. The browser reported no console errors or warnings during the final check.

This validates the UI lifecycle with fake providers; it is not a new end-to-end test of real paid model output quality.

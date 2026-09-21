# Task archives and planning retry clarity

Implemented in the sibling agent-console browser dashboard on 2026-09-20.

- Individual Archive task action with review dialog, Undo, and Archived tasks / Restore task.
- Separate durable archive store; task/project files, registry, history, and workers are unchanged.
- Archived runs and linked conversations disappear from active lists. Other runs in the same project remain visible. Deep links offer restoration; mutation routes require restoration first.
- Planning output validation failures now say Planning needs retry, explain the rejection, and label Continue as Retry planning. Technical details remain expandable. Retrying does not approve the plan.

Validation: 135 Python tests passed with one existing skip; six JavaScript test scripts passed. Browser fixture verified archive confirmation, sibling visibility, Archived tasks, restore, and persistence after reload. No real task was archived or resumed and no provider request was made. Only the idle dashboard server was restarted.

K8s diagnosis: the repaired GLM discovery report used basis=user_feedback with answer_id=conv-e10a403b-learning-correction. The runner had no matching saved user event. The validator rejected that reference and archived the failed attempt. Conversation context existed, but it was not a valid saved runner feedback event. The original discovery report had also failed milestone acceptance-criterion validation. No successful fresh planning retry has been verified.

Staging and previous live-file backup: /private/tmp/autocode-task-archive.r07r3J (live-backup subdirectory).

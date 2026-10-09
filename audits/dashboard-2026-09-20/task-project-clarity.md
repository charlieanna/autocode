# Task and project clarity

Updated the existing agent-console dashboard directly, without using Autocode to implement changes.

The former attention filter counted paused tasks, failed provider attempts, unavailable checkpoints, and ready-to-continue tasks as needing user input. The revised list separates actual questions and approvals from these stopped states. The inspected 24-task snapshot contains 8 waiting on the user, 7 paused or ready, 4 with reported activity, 3 completed, and 2 previews.

Projects group separate requests. Task rows now show a concise title, creation time, question or stop-reason preview, and a navigation action such as Answer 2 questions, Review plan, Review interruption, or Open to continue. The active project and status filter survive reloads in the URL. A project selector remains visible on narrow screens. Detail pages repeat the next action and retain the originating list scope when navigating back.

Completed status takes precedence over stale saved questions. Running and paused states similarly prevent stale requests from being presented as current questions. Output review requires an explicit human-review request with outstanding current-token reviews. An active stage is labeled reported activity, not confirmed running; its recorded time is visible. No process liveness check was added.

Verification:

- 110 Python regression tests passed in 31.133 seconds.
- JavaScript syntax, existing planning UI tests, and new task classification/identity/filter tests passed.
- A read-only local fixture using a snapshot of the existing task data verified project filtering, reload persistence, and navigation to IdleCampus's actual two-question wiring task. The fixture rejected every POST.
- Desktop and 390px mobile layouts were inspected; the mobile document width equaled the viewport width, with task reasons and project selection visible.
- Six installed files matched tested staging. Restarted only the dashboard after checking all workspace/run actions and conversation turns were idle. No Autocode task was resumed, answered, approved, deleted, or stopped.
- Verified the live dashboard still lists 24 tasks and the saved exercise-tracker conversation. Opened a fresh live tab after the pre-restart tab retained a connection error. IdleCampus shows its wiring task waiting on two questions separately from its mapping task's recorded activity, with no captured JavaScript errors.
- Stopped the disposable fixture server and left the live dashboard running on port 8767.

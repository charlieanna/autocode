# Browser dashboard audit — 20 September 2026

**Verdict:** The visual direction is coherent, but the dashboard is not yet reliable enough as the main place to supervise engineering work. The most serious problems can send a task to the wrong project, leave its controls permanently loading, or present stale verification as current success.

This audit found **14 confirmed defects**: three P1, ten P2, and one P3. Six broader design improvements follow below. This is a bounded audit of the current implementation, not a guarantee that every possible defect has been found.

Scope: `agent-console` frontend, Python server, runner adapter, task creation, project selection, polling, action feedback, recovery, status presentation, and responsive design. The real dashboard was inspected read-only. All mutation and failure tests used disposable projects and a fake runner. **No Autocode orchestration or model providers were used. No application source was changed.**

## Confirmed defects

### B01 · P1 · A retained typed path overrides the project the user selects

**Reproduction:** Create a fixture task using the manually entered `other-project/` path. On the next task, select `project` in the Project dropdown. Submit without clearing the old path. The new task is created under **other-project**, even though the dropdown displays **project**.

Both controls remain active, the form retains their previous values, and the server always prefers a nonempty `project` field. This can direct implementation into the wrong repository.

**Fix:** Keep a single selected workspace value. Choosing a dropdown item must clear the manual path, and entering a path must clear the dropdown. Show the resolved project immediately beside the submit action.

Source: [create form handler](/Users/ankurkothari/Documents/workspace/agent-console/dashboard_app.js:192), [workspace precedence](/Users/ankurkothari/Documents/workspace/agent-console/agent_console.py:318).

### B02 · P1 · Responses slower than the polling interval prevent task details from loading

**Reproduction:** Delay `/api/run` responses by 2.4 seconds. Open a task. Over a 32.6-second observation period the server received repeated detail requests, but the page remained at **Loading current task state…**, Continue stayed disabled, and the connection indicator still said **Live updates**.

Every two-second refresh increments `seq`. The next refresh invalidates the previous request before it can finish, so all slow responses are discarded. The same mechanism affects a slow task listing.

**Fix:** Use one in-flight refresh at a time and schedule the next poll after completion. Invalidate requests when the selected task changes, not merely because another timer tick occurs. Show the age of the last successful detail update and a real loading/error state.

Source: [refresh and polling](/Users/ankurkothari/Documents/workspace/agent-console/dashboard_app.js:343).

### B03 · P1 · Stale validation is presented as current verified completion

**Reproduction:** The fake status inspector returns `completion_current: false` for a saved `TASK_COMPLETE` task. The adapter drops that value. The task list still displays **Completed / Verified outcome**, and its old validation contributes to the **criteria passed** count.

The real runner's status interface already provides a freshness check, but the dashboard uses the stored status and validation without that qualification. Changed source or invalidated evidence can therefore look successfully verified.

**Fix:** Expose verification freshness and its checked revision/time. Treat historical completion separately from current completion. Do not label stale criteria as current passes. Use a bounded cache so freshness checks do not make every listing expensive.

Source: [stored criterion counts](/Users/ankurkothari/Documents/workspace/agent-console/agent_console.py:277), [status adapter](/Users/ankurkothari/Documents/workspace/agent-console/dashboard_backend.py:163), [completion badge](/Users/ankurkothari/Documents/workspace/agent-console/dashboard_app.js:66), [verified-outcome label](/Users/ankurkothari/Documents/workspace/agent-console/dashboard_app.js:177).

### B04 · P2 · Creation failures disappear from the task that just opened

**Reproduction:** The fake runner creates a checkpoint and then exits 1 with an explicit failure message. The browser opens that task, displaying **Ready to continue** and a promise that Astra's questions will appear. The task's action list is empty. Its failure is stored only in the workspace's creation log on the New task page.

Creation actions are keyed by workspace, while the task detail only retrieves actions keyed by run. Automatically opening the new task hides the diagnostic needed to understand why discovery stopped.

**Fix:** Correlate creation action IDs with the resulting run and include the startup command in that task's activity. Surface startup failure at the top of the task, with an explicit next step.

Source: [action keys](/Users/ankurkothari/Documents/workspace/agent-console/agent_console.py:299), [task actions endpoint](/Users/ankurkothari/Documents/workspace/agent-console/agent_console.py:398), [creation log rendering](/Users/ankurkothari/Documents/workspace/agent-console/dashboard_app.js:348).

### B05 · P2 · Selecting the legacy Codex engine does not select Codex

**Reproduction:** Capture the command generated by `create(..., engine='codex')`. It includes bare Codex model names but omits `--engine codex`. The current runner defaults new tasks to OpenCode, whose model validator requires `provider/model` identifiers. The optional Codex provider arguments also conflict with that default engine.

No real provider was launched to establish this: command capture and inspection of the runner's argument handling are sufficient.

**Fix:** Pass `--engine codex --no-chat` explicitly and add a command-contract test that checks the engine, not only the model flags.

Source: [Codex creation branch](/Users/ankurkothari/Documents/workspace/agent-console/agent_console.py:339), [runner engine selection](/Users/ankurkothari/Documents/workspace/autocode/tools/autocode.py:797).

### B06 · P2 · Undecodable command output leaves an action permanently “running”

**Reproduction:** Execute a disposable runner that writes byte `0xff` to stdout and exits. `communicate()` raises `UnicodeDecodeError`. The worker releases its reservation and records a finish time, but the action remains `status='running'`. The frontend treats that action as busy and disables Continue indefinitely.

**Fix:** Decode output with a defined replacement policy, and ensure every terminal exception produces a terminal action status. Retain diagnostic bytes or a safe decoded representation.

Source: [command execution](/Users/ankurkothari/Documents/workspace/agent-console/agent_console.py:305), [frontend busy check](/Users/ankurkothari/Documents/workspace/agent-console/dashboard_app.js:277).

### B07 · P2 · A valid noncanonical project path prevents automatic navigation

**Reproduction:** Enter `/private/tmp/dashboard-audit-20260920/other-project/`, including the trailing slash. The task is successfully created, but the page stays on New task with no inline error. The pending-task matcher compares the original input with the canonical path returned by discovery, and they never match.

Expanded `~` paths and symlink aliases have the same mismatch. This contributes to the impression that Talk to Astra did nothing and invites duplicate submissions.

**Fix:** Return a canonical workspace and a stable action/task identifier from creation; navigate using that identifier rather than matching goal text and raw path strings.

Source: [pending creation state](/Users/ankurkothari/Documents/workspace/agent-console/dashboard_app.js:201), [new task matching](/Users/ankurkothari/Documents/workspace/agent-console/dashboard_app.js:349).

### B08 · P2 · Read endpoints omit the Host protection used for mutations

**Reproduction:** Send `GET /api/runs` to the disposable server with `Host: untrusted.example`. It returns HTTP 200 with all fixture task data. POST routes reject that Host, but GET routes do not check it.

This leaves the read API without a server-side DNS-rebinding defense. Task prompts, project paths, saved state, and action logs are sensitive local information. A browser exploit was **not** attempted; its feasibility also depends on browser and network protections.

**Fix:** Validate the allowed loopback Host before serving all routes, including static content and read APIs. Retain the additional Origin checks for mutation routes. Add GET tests alongside the existing POST security tests.

Source: [Host validator](/Users/ankurkothari/Documents/workspace/agent-console/agent_console.py:382), [unguarded reads](/Users/ankurkothari/Documents/workspace/agent-console/agent_console.py:390).

### B09 · P2 · Polling can move the approval-field caret backwards while typing

**Reproduction:** Execute the shipped focus functions in an isolated DOM stand-in. The refresh captures caret position 3. The user types to position 6 while the response is in flight. Rendering replaces the approval input, and `restoreFocus` moves the caret back to position 3. Input text survives, but subsequent typing can be inserted in the wrong place.

Only focus changes increment the version; input and selection changes do not. This was a focused JavaScript reproduction, not a timing claim based on a screenshot.

**Fix:** Preserve the actual input node, or capture the latest caret immediately before replacing it. Track input/selection changes if retaining the version approach.

Source: [focus capture and restoration](/Users/ankurkothari/Documents/workspace/agent-console/dashboard_app.js:32), [approval input replacement](/Users/ankurkothari/Documents/workspace/agent-console/dashboard_app.js:253).

### B10 · P3 · Malformed workspace input drops the HTTP connection

**Reproduction:** POST an action with `workspace: null`. `Path(None)` raises an uncaught `TypeError`, and the client receives `RemoteDisconnected`, rather than a structured HTTP 400 response. The server itself remains running.

**Fix:** Validate field types at the API boundary. Return bounded, consistent JSON errors for invalid payloads and handle unexpected handler exceptions without silently closing the connection.

Source: [workspace lookup](/Users/ankurkothari/Documents/workspace/agent-console/agent_console.py:263), [POST exception handling](/Users/ankurkothari/Documents/workspace/agent-console/agent_console.py:400).

### B11 · P2 · Project summaries show global counts

**Reproduction:** Select the fixture `other-project`, which has two tasks, both waiting for input. The page heading changes to that project and displays two rows, but the summary reports **6 need your input / 1 completed** from the whole dashboard.

**Fix:** Compute the project summary from the selected project's tasks. Global totals may remain in the global navigation, clearly labeled as such.

Source: [counts calculated before filtering](/Users/ankurkothari/Documents/workspace/agent-console/dashboard_app.js:158).

### B12 · P2 · Failed or uncertain human input is hidden in a collapsed section

**Reproduction:** Make feedback submission return an unrecognizable receipt. The saved result is **Change · uncertain**, with an error and Retry control, but all of it is inside the closed Change request history disclosure. There is no visible top-level error, the inline error is empty, and the composer still says **Saved to this task**.

**Fix:** Keep a visible submission receipt beside the composer: sending, durably queued, applied, failed, or uncertain. Automatically expose failures and their retry action. Reserve “saved” for a confirmed durable receipt.

Source: [feedback response handling](/Users/ankurkothari/Documents/workspace/agent-console/dashboard_app.js:307), [history rendering](/Users/ankurkothari/Documents/workspace/agent-console/dashboard_app.js:326), [closed history section](/Users/ankurkothari/Documents/workspace/agent-console/dashboard.html:62).

### B13 · P2 · Recovery remains clickable after the selected task becomes unavailable

**Reproduction:** Open Recovery audit, then temporarily remove its fixture checkpoint. The next refresh displays **Selected run unavailable: run unavailable**, but **Recover saved work** remains visible and enabled in the old attention panel.

The backend still rejects invalid access, so this reproduction did not bypass the runner. The problem is a stale actionable screen claiming it can operate on unavailable state.

**Fix:** Clear or disable the attention panel and every task-specific action on invalidation. Do not retain event handlers from the last successful snapshot.

Source: [incomplete invalidation](/Users/ankurkothari/Documents/workspace/agent-console/dashboard_app.js:334).

### B14 · P2 · Small informational text fails minimum contrast

Computed styles in the real dashboard, on a white background:

| Text | Size | Color | Contrast |
| --- | --- | --- | --- |
| Current stage / model details | 11px | `#77817b` | 4.03:1 |
| Conversation subtitle | 10px | `#98a089` | 2.72:1 |
| Planner label | 8px | `#93a183` | 2.74:1 |

These are below the 4.5:1 requirement for normal informational text. [W3C contrast guidance](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).

**Fix:** Darken secondary text and make essential task metadata comfortably readable. Use 14–16px for ordinary task content and approximately 12–13px for secondary metadata as a design choice. The palette can remain green and neutral.

Source: [text styles](/Users/ankurkothari/Documents/workspace/agent-console/dashboard.css:2).

## Design assessment and recommended direction

The light canvas, restrained green palette, spacing, and consistent cards are a good foundation. In the inspected 390px approval view there was no horizontal overflow; its tabs measured about 28px high. The problem is primarily information hierarchy and interaction design, rather than a need for a different decorative theme.

1. **Make the next action the center of the task page.** Answer, Review brief, Pause, Resume, Inspect interruption, and Review artifact should be distinct states with a matching primary control. A generic Continue button across almost every state makes normal waiting, failure, and recovery feel alike. Keep the reason, responsible agent, last update, and next action together.

2. **Give tasks short identities.** The full user prompt is currently the title; real tasks become large paragraphs in the heading and indistinguishable truncated rows in the list. Use a short editable title, keep the original request in a disclosure, and show project, current objective, and updated time on each row. Search and project filtering should retain enough context to explain the visible counts.

3. **Make Conversation an actual record of the conversation.** Saved answers currently show an ID and answer without the original question. Feedback receipts live elsewhere. Preserve question-and-answer pairs, plan updates, user direction, and acknowledgements in chronological order. Keep the composer near the latest actionable exchange. Reflect the actual agent/workflow instead of describing every view as Astra planning.

4. **Make approvals about the work being approved.** A long revision hash is implementation detail. Bind an explicit “Approve revision 9” action to the exact displayed revision internally; show what changed, its scope, and acceptance criteria. Human artifact review should provide navigable evidence, diffs, or previews, not only a criterion name and an Approve button. Recovery needs inspectable evidence too: file paths alone are insufficient for a browser workflow.

5. **Show the effective model configuration and routing.** The dropdown defaults do not explain which model will be used. Existing mixed-engine tasks expose only a global engine and role model names, losing per-role engines/providers and workflow mode. Show each role's effective model and engine, and distinguish planning, implementation, escalation, and final audit according to the saved workflow.

6. **Preserve navigation and drafts.** There is no task URL or browser-history integration, and selected views/drafts live in JavaScript memory. Refresh returns the user to All tasks; tasks cannot be bookmarked or opened directly. Use stable task routes and explicit draft persistence. The small sidebar is workable, but essential status and actions need more readable typography at narrow widths.

## Architecture risks to address separately

These are code-supported scaling/operability concerns, not additional reproduced defects counted above:

- `/api/runs` transfers full plans, histories, stages, and briefs for every task every two seconds. Use a compact task-summary API and fetch detailed history on demand.
- Multiple views can perform overlapping status subprocesses; the short status cache has no per-run single-flight control. Combine that with the polling defect before increasing task volume.
- Workspace scanning has a depth limit but no directory-count budget and excludes only `.git`; dependency/build trees can make discovery unexpectedly expensive.
- Queues and command output are memory-only, and output capture is unbounded. A durable action journal with bounded logs would make restarts and failures understandable.
- A saved `active_stage` is not a live-worker heartbeat. “In progress” should distinguish a confirmed live worker from an old recorded stage.
- The compressed single-line handlers and styles make changes and review unnecessarily difficult. Extract explicit request validation, action state, and rendering components, without needing to introduce a large framework.

## Repair order and acceptance checks

**First:** B01–B03 and B08. Prevent wrong-project writes, restore reliable loading, make verification truthful, and protect read routes.

**Second:** B04–B07 and B09–B13. Establish stable task/action IDs, terminal action states, visible failure receipts, canonical project identity, and safe view invalidation.

**Third:** B14 and the design changes. Keep the visual theme; rebuild the page around a clear next action, a useful activity record, and evidence the user can actually inspect.

Acceptance checks should include slow and out-of-order responses; new task creation failing before and after checkpoint creation; dropdown/manual-path switching; trailing slashes and aliases; stale source/evidence; undecodable output; unavailable checkpoints; feedback timeouts; foreign Host reads; and typing while a refresh is in flight. Test the browser outcome, not only the presence of source-code strings.

## Evidence and limits

All **61 existing dashboard tests passed** during this audit. They did not detect these cases; several frontend checks assert source strings rather than execute browser interactions.

- [Backend reproductions](/Users/ankurkothari/Documents/workspace/autocode/audits/dashboard-2026-09-20/evidence/backend-results.json)
- [HTTP and creation-failure evidence](/Users/ankurkothari/Documents/workspace/autocode/audits/dashboard-2026-09-20/evidence/http-results.json)
- [Browser observations and contrast measurements](/Users/ankurkothari/Documents/workspace/autocode/audits/dashboard-2026-09-20/evidence/browser-results.json)
- [Focus reproduction](/Users/ankurkothari/Documents/workspace/autocode/audits/dashboard-2026-09-20/evidence/focus-results.json)
- [Audited source hashes](/Users/ankurkothari/Documents/workspace/autocode/audits/dashboard-2026-09-20/evidence/source-manifest.json)

The `evidence` directory also preserves the disposable fixture server, fake runner, and focused probes. They are investigation tools, not changes to the production test suite. No live provider authentication/billing tests, browser-based DNS-rebinding exploit, or full assistive-technology audit were performed. Existing running tasks were not resumed, paused, or otherwise changed.

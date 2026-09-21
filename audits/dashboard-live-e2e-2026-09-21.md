# Real-provider browser workflow — 2026-09-21

Status: in progress. This record must not be counted as a passing completion test
until the final result and independent application review are recorded below.

## Scope and identities

- Dashboard: `http://127.0.0.1:5191` (the user's running local dashboard).
- Conversation: `415fad15d27c421cb16620beba59e083`.
- Disposable project: `/private/tmp/pr-ready-autocode-20260921`.
- Run: `20260921-120638-dashboard-reliability-test-build-a-tiny-offline--2faf7a76`.
- Product: PR Ready, three accessible checklist items, 0/3–3/3 progress, a ready
  indicator, localStorage persistence, reset, invalid-state handling, and README.
- No additional product features or changes to existing user projects are in scope.

Conversation, project creation, initial task controls, and plan approval were exercised
through browser UI. The later CLI exceptions are recorded below. Filesystem reads corroborate durable state and
provider evidence. Application verification and a local preview server are allowed
for this disposable project. Providers are real, not injected fixtures.

## Observations

- GLM returned a scoped project-free plan through the live conversation.
- Create project and attach created a Git workspace, registered the task, preserved
  the conversation, and opened its task conversation.
- The first recorded planning command invokes OpenCode with
  `zai-coding-plan/glm-5.3`. Saved review roles are Astra `gpt-6-astra` and Sol
  `gpt-5.6-sol` through Codex. The implementation role is GLM through OpenCode.
- Pause was requested during planning through the browser. Its durable request ID
  is `f7a9c6f2-c542-41be-86ce-7a96a1e2f40c`, submitted at 19:06:57 UTC.
- GLM's real planning stage finished in 149.44 seconds. At 19:09:12 UTC the pause
  was applied, active stage cleared, and `astra_challenge` saved as the next step.
  Reloading showed Paused and Resume planning; the applied receipt survived.

## Failures exposed during the live test

1. Global dashboard polling timed out after the saved pause and disabled Resume.
   `/` still responded; `/api/runs` exceeded 20 seconds and `/api/run` exceeded
   15 seconds. The standalone registry and selected task status commands completed.
   A native stack sample showed request threads queuing on RLocks and repeated
   JSON/filesystem work. An isolated discovery probe reproduced repeated registry
   refreshes inside one traversal. The registry cache's two-second lifetime began
   before refresh finished, so a slow refresh could publish an already-expired
   cache. Fixed by timestamping completed refreshes and using one registry
   snapshot per discovery traversal. Real-registry reads now take 1.006 seconds
   initially and 0.427 seconds on repeat. The restarted live server returned
   `/api/runs` in 1.236 seconds and selected `/api/run` in 0.297 seconds.
   Selected-task rendering now proceeds independently of a delayed or failed
   global list request, so fresh task controls remain usable.
2. Paused status labelled pending Astra review as "Last recorded step", although
   the completed step was GLM planning. The UI now distinguishes Next step from
   Last completed step; deterministic UI regressions pass. Live reload showed
   "Next step · Astra · Challenging the plan" while paused.

The dashboard server was restarted at the paused test boundary. The browser's
Resume planning action succeeded on the same run, with durable pause
acknowledgment at 19:23:01 UTC. Astra's real Codex review completed successfully
in 157.70 seconds and GLM revision started. Neither discovery nor review was
replayed. The browser correctly showed GLM revising and a verified live worker.

Regression validation after the fixes: 166 dashboard Python tests passed, all
existing JavaScript suites passed, and the new independent refresh UI suite
passed. New cache tests cover slow success/failure, one snapshot per traversal,
and rejection of changed run identity. Syntax and whitespace checks passed.

Joint planning completed at 19:38:28 UTC, each of the four stages exactly once,
without retries or failures. Combined provider-stage time was 1,066.60 seconds
(17m47s), which is a material latency observation for this small test. Revision 4
contained one implementation milestone and ten criteria. The browser presented
the approval button only after Astra finalized it. Approval was saved at
19:39:03 UTC for token
`r4:f84a9e27a47ae999b0b5d54a7807d3549aa4eb1cb8e31c706961cf8b50f6476a`.
No application source or active writer existed at that approval checkpoint.
The separate Start building action then launched the real OpenCode GLM writer
(PID 20349), and the run entered EXECUTING.

The finalized plan explicitly requires local screen-reader review in AC6 and
requesting-user human sign-off in AC9. Automated browser checks must not be
misrepresented as those unperformed human checks.

### Live implementation failure and recovery

At 19:45:01 UTC the first GLM implementation attempt exited with code 0 but no
final report and no application files. `terra-01.jsonl` contains a step start and
a step finish with `reason="length"`; reported usage includes 31,953 reasoning
tokens and 47 output tokens. This is a provider generation-limit failure, not a
successful implementation. The runner entered `PAUSED_UNCERTAIN_STAGE` and the
browser correctly displayed Interrupted with Review recovery.

Through the browser, Inspect interrupted attempt showed the saved identity and
exit metadata. Recover saved work archived the uncertain attempt without
discarding files or approval, then the separate Resume task action started
`astra_review` (PID 33556). The four completed planning stages were not replayed.

The quiet attempt also exposed an activity-classification defect: a persistent
Pencil MCP server descendant was enough to label the worker `running_tool` even
with zero explicit active tool calls. Recognizable MCP-server executable names
are now excluded from that inference; explicit calls and genuine or unknown
command descendants retain tool deadlines. Executable arguments are not read,
and persisted process identities are unchanged. All 63 activity, process cleanup,
and runtime recovery tests pass. Generic Node-hosted helpers remain ambiguous;
mere process liveness is not evidence of productive activity. Existing workers
were not restarted to apply this source fix.

The installed OpenCode output ceiling is 32,000 tokens. The failed attempt's
31,953 reasoning plus 47 output tokens hit it exactly. The per-run supported
reasoning-effort setting is the intended scoped retry adjustment. The dashboard
currently exposes model selection but only displays saved reasoning effort;
setting it requires the supported CLI. This configuration-only exception must
not be counted as browser-only workflow coverage.

After Astra's recovery review, a second browser pause applied at the boundary
before the writer. The supported CLI `--terra-reasoning-effort low --show-goal
--no-chat` saved only that role's effort and launched no agent. Assertions checked
that the exact approved contract and all other roles were unchanged. The browser
displayed the saved low effort, and Resume task started the second attempt at
19:56:45 UTC with `--variant low` (PID 50162).

Output-limit reporting is now corrected: native `reason=length` yields a failed
terminal event with known usage, never successful completion. Runner pause and
reconciliation retain the specific cause, and the UI displays the saved cause
with its existing generic fallback. Known failed usage is counted without a
completed turn. All 68 targeted Python checks and three UI regression scripts
passed. The dashboard server was safely reloaded at the paused boundary (new
PID 49483); project workers were untouched.

IdleCampus and ddia-tutor were checked during diagnosis: their existing OpenCode
workers (13294 and 42456 respectively) and runner parents were alive, with updating
checkpoints. They were not interrupted by our initial idle-boundary dashboard reloads.
A ten-minute heartbeat now checks those two current runs, preserves healthy
workers, resumes safe unexpected stops within their approved scope, and reports
actionable failures or completion without repeated unchanged notifications.

### Completed implementation and browser checks

The lower-effort GLM attempt finished at 19:59:06 UTC in 141.18 seconds. It
preserved an initial 7-pass/6-fail test receipt, fixed the failures, and recorded
13/13 passing tests before handing off to Sol. The app, README, and tests exist.
Concurrent routing changes moved Sol to OpenCode `openai/gpt-5.6-sol`.

The README's loopback server was started at http://127.0.0.1:8000 (PID 56099).
The browser connected it through the dashboard Preview form and loaded the app
inside its iframe. Actual browser checks passed:

- Fresh 0/3, three unchecked boxes, readiness hidden.
- Clicking each associated label produced 1/3, 2/3, then 3/3 with readiness visible.
- Unchecking from 3/3 hid readiness immediately.
- A new tab and reload preserved the exact first-and-third checkbox subset.
- Reset returned to 0/3 and remained reset across reload.
- Space toggled checkboxes; Tab traversed all controls; checkbox and Reset focus
  outlines were solid and visible in computed styles; Space activated Reset.

Screen-reader announcements, browser-injected corrupt storage, offline browser
mode, and explicit requesting-user sign-off were not performed or claimed.

### Dashboard restart disconnected active runners

A subsequent dashboard replacement (PID 49483 to 59528) closed its pipe-based
runner output while Sol was active. The runner's progress print raised
`BrokenPipeError`, stopped verification, and saved PAUSED_INVALID_OUTPUT.
ddia-tutor later suffered the same failure at iteration 18. Neither attempt was
treated as complete. Their tracked workers were confirmed stopped before recovery.

The dashboard now launches runners in a separate session with stdout/stderr
written to unique files under `.autocode/dashboard-actions/<action-id>/`, rather
than pipes owned by the server. Full output survives while UI responses include
bounded tails. A real fake-worker regression terminates the dashboard's process
group and verifies that its child continues writing and completes. All 172
dashboard Python tests and all 13 JavaScript suites passed; diff checks passed.
The fix was deployed at an idle server boundary (dashboard PID 75618).

After browser tools became unavailable on the interrupted turn, the supported CLI
recovered stopped attempts `018/terra-01` for ddia-tutor and `002/sol-01` for the
disposable test. Both preserved the exact approved contracts and partial work,
then resumed with detached runners and durable logs. This is explicitly CLI
recovery coverage, not browser-only completion. ddia-tutor's recovery runner is
71631; the test's is 73912. IdleCampus's existing worker 13294 was left intact.
At 20:16 UTC, ddia-tutor had advanced through recovery to iteration 19 and launched
Terra PID 81153 with durable logs; IdleCampus PID 13294 remained alive. The test
was running Sol PID 77065 after recovery. The generated app's 13 tests also passed
an independent local rerun. A final 26-test provider-output/runtime regression
run passed, including malformed token containers and replayed terminal events.

## Final result

Passed so far: conversation, registration, saved pause, reload/resume, all four
joint-planning stages, browser plan approval, explicit build start, real
implementation with passing tests, browser preview and core interaction checks,
interrupted-attempt recovery, and isolated dashboard-restart survival.

Pending: completed independent verification, remaining human review gates,
and task completion. The end-to-end completion test is not yet a pass.

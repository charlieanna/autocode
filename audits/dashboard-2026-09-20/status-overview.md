# Project and task status overview

Implemented 2026-09-20 in the sibling agent-console dashboard, using the read-only Idlecampus monitor on port 5191 as the visual and information reference.

- Project pages feature their live task first, or their most recent open task when none has a verified worker. Other tasks remain listed with individual statuses.
- Task detail opens on Overview, with a prominent status, current or last recorded step, active agent, saved models and reasoning settings, iteration, objective, next human action, log/checkpoint freshness, recent tool activity, stage history, and acceptance counts.
- Conversation, plan/history, and execution/reviews remain separate tabs. Project folder and removal controls are under Project details.
- Saved workflow routing determines labels: Idlecampus correctly shows GLM implementing, Sol as targeted escalation, and Astra for the final audit.
- Process checks compare saved PID/start identity, with a conservative provider/run argument fallback for legacy states. Exited or unverified workers are distinguished from verified running workers. Process inspection is cached for three seconds.
- Activity reads are bounded to a run-contained file and return allowed tool metadata only. Provider prose, reasoning, raw commands, patches, and command output are excluded.
- Connection failures mark the visible overview as a last observation. Missing or removed tasks hide the old overview.

Validation: Python suite ran 131 tests successfully (one skipped); five new process/log tests cover PID reuse, exited/zombie workers, unavailable process inspection, legacy identity matching, log containment/redaction, finished stages, and process-table caching. Existing JavaScript planning, task classification, project removal, and navigation checks passed; classification checks now cover live versus exited workers and actual saved workflow labels.

Browser verification used a read-only preview that rejects all POST requests. Checked the real Idlecampus worker, project summary, Overview/Conversation tab switching, and a 390px layout without horizontal overflow. Installed the reviewed files, restarted only the idle dashboard web server, and verified the live task endpoint. No task state, provider configuration, or worker was modified.

Changed dashboard files: dashboard_monitor.py, dashboard_backend.py, dashboard_chat.py, dashboard_app.js, dashboard.html, dashboard.css, tests/test_monitor.py, tests/test_task_clarity.js.

Staging and read-only preview: /private/tmp/autocode-status-overview.ArJXry.

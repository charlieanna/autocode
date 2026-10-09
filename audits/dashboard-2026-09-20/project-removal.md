# Dashboard project removal

Implemented and installed on the local dashboard on 2026-09-20. No real projects were removed.

## Behavior

- Remove project is available in project task lists, task detail, and Workspace settings.
- A confirmation shows the full folder path and explains that removal hides the project, its tasks, and linked conversations. Repository files, history, registry entries, and running workers remain intact.
- Undo and Settings → Removed projects restore visibility. Exclusions persist across dashboard restarts and prevent newly registered runs from reappearing until restoration.
- Removed task and linked-conversation URLs offer restoration and block new mutations. Saved conversations remain intact.
- Atomic, locked preference storage validates paths and rejects unsafe or corrupt store files. HTTP origin protection applies to project actions.

## Installed files

All files are in `/Users/ankurkothari/Documents/workspace/agent-console`:

- `agent_console.py`
- `dashboard.css`
- `dashboard.html`
- `dashboard_app.js`
- `README.md`
- `dashboard_projects.py` (new)
- `dashboard_project_controls.py` (new)
- `tests/test_project_store.py` (new)
- `tests/test_project_removal.py` (new)
- `tests/test_project_ui.js` (new)

The existing working-tree edits were preserved. Installation checked that the original files had not changed concurrently, then verified installed bytes against the reviewed staging files.

## Verification

- Full Python suite: 129 tests passed. After final path-validation and create-guard refinements, all 7 project-removal integration tests passed again; all 12 store tests passed after the saved-path idempotence refinement.
- JavaScript syntax and planning, task-clarity, and project-removal test scripts passed.
- Isolated browser test: cancel, remove, undo, reload persistence, removed task deep link, linked-conversation disappearance/restoration, and restoration from Settings passed. The unrelated demo project remained visible and both demo repository files remained intact; no runner commands were launched.
- Mobile Settings checked at 390 × 844: full paths wrapped and controls stayed accessible without horizontal overflow.
- Restarted live dashboard on port 8767. Live API retained the same 16 projects, 24 tasks, and 2 conversations; removed-project list remained empty. Verified live Settings displays removal controls and restoration section.

Test artifacts and logs: `/private/tmp/dashboard-project-removal-20260920`. The disposable browser fixture was stopped after verification. The live dashboard remains running.

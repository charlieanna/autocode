# Browser dashboard

[← Back to README](../README.md)

Autocode includes a local browser dashboard for planning work with the Requirements Planner, approving
the lead-reviewed plan, choosing each execution role's model and reasoning level,
following active tasks, and sending feedback or a pause
request at a safe boundary. It reads the same local run registry as the command
line tool, so tasks started from a terminal appear automatically.

## Start it

After installing this checkout, start it from any directory:

```sh
autocode-dashboard --port 8767
```

Or run it directly from a checkout:

```sh
python3 tools/dashboard/agent_console.py --port 8767
```

Open the printed loopback URL. Projects contain multiple conversations. Use a project’s **+** button to start
a conversation scoped to its repository and instructions. The top New conversation
action names its current project. Conversations without a project remain in
**No project**, and can be attached explicitly when ready.

**Workspace setup** opens a chat with dependency checks, model settings and
project creation. It never collects credentials or runs installers. New projects
receive an empty initial Git commit; attaching an existing project requires a
clean committed repository. Older scoped chats without a saved folder identity
ask you to confirm the displayed folder in chat. Confirmation preserves history
and starts no model work. A known replacement folder remains blocked until the
original is restored.

When `--watch-root` is used, discovery stops at each Git project boundary and
skips generated or internal trees such as `.git`, `.autocode`, `node_modules`,
and virtual environments. List responses also keep only the compact stage data
needed by the task index, so broad workspace roots remain safe to poll from the
browser without serializing complete provider payloads or process histories.
Overlapping task-index polls share the same in-progress snapshot, and each
snapshot reuses one watched-project discovery result instead of building a
queue of duplicate scans.

## Preview and saved screenshots

Preview remembers a loopback app address per project in this browser. Open
Preview from any conversation in that project to reuse it. Existing task-level
addresses remain available. AutoCode starts no development server; start your
app separately and enter its address once. A completed code-changing step
refreshes the preview; ordinary status polling preserves the running frame.

A Validator image reference appears in chat beside its requirement and recorded
source revision. Open the image or follow its link to Checks. These are saved
results, not new visual acceptance. When a recorded image fingerprint exists,
the dashboard refuses changed bytes. Missing images, unsupported formats, stale
report IDs, symlinks and paths outside the selected task/project are refused.
No image card approves a requirement or starts a task.

## What you can do

The conversation contains questions, exact-plan approval, output acceptance and
recovery actions. Approving a reviewed plan and starting its build are separate
actions. An amber marker beside a conversation means it requires your reply;
opening it does not dismiss the request. A completed conversation shows a tick.

Archive and Remove project change visibility and preserve files and checkpoints.
**Delete permanently** is a separate explicit action in Archived. Its chat
confirmation lists the selected files and owned Git resources. Live or uncertain
workers, changed selections and shared resources prevent deletion. Partial
cleanup retains a retry receipt and never treats a recreated resource as owned.

Saved code checkpoints offer comparison and **Go back to here** in chat. An
approved restore requires a stopped run and creates a new branch and paused
continuation. It preserves the previous branch and history; later verification
does not carry over as proof for the restored version.

Choose initial reasoning under **Models & reasoning** when starting a conversation.
For a saved task, open its **•••** menu and use **Reasoning for next steps**. The
dashboard changes the saved per-role setting only at a stage boundary; it never
interrupts or mutates an in-flight provider request.

The dashboard uses `$AUTOCODE_HOME/dashboard` by default (or
`$AUTOCODE_DASHBOARD_HOME`) for conversations and reversible archive settings.
It binds only to `127.0.0.1`, starts no development server for task previews,
and uses documented Autocode commands for task changes.

## Conversation and artifact panes

Chat stays visible throughout planning, building, verification and recovery.
The **Work** pane shows the current task, recorded roles, factual task progress,
requirements and blockers. **Plan**, **Preview**, **Changes**, **Checks** and
**History** open beside it without replacing the conversation. On narrow screens,
Projects and Details open as drawers and preserve the chat draft. Workspace
options contains All work, Archived and display preferences.

Pause and Stop beside the composer finish the current step before preventing
further stages. Their pending labels do not claim the runner has stopped yet.
Compact task links and older `#task=…&run=…` links remain supported.

Saved status, worker liveness, artifact timestamps, and task-read freshness are
separate signals. A disconnected task retains its last successful view, labels it
stale, disables mutations, and offers **Retry status**. Refreshing the project list
does not make a failed task read look current. HTTP read failures never resume,
restart, or retry an agent stage.

## Project-specific counters

Project-specific counters are optional. Declare a read-only projection in the
project's `.autocode/dashboard.json` (not in runner state):

```json
{
  "version": 1,
  "metrics": [{
    "id": "mapping", "label": "Mapping coverage", "runs": ["EXACT-RUN-DIRECTORY-NAME"],
    "description": "Dispositioned records, not completion or mastery.",
    "source": "artifacts/coverage.json", "groups_pointer": "/areas",
    "completed_field": "done", "total_field": "total"
  }]
}
```

The referenced object contains named groups, each with integer counters or lists
whose lengths are counted. Sources must remain inside that project (including
after symlink resolution). Invalid/missing data is **unavailable**, never zero or
PASS. The artifact modification time is shown prominently. A cached projection
avoids repeatedly parsing large files; changes to the file or config invalidate
it. No commands, imports, provider requests, or raw content are executed by these
metric declarations. Removing the config removes the panel. An exact run-name
allowlist prevents another task inheriting unrelated progress.

## Dashboard verification

With localhost socket access:

```sh
python3 -m unittest discover -s tools/dashboard/tests -p 'test_*.py'
for test_file in tools/dashboard/tests/test_*.js; do node "$test_file" || exit 1; done
python3 tools/dashboard/tests/unified_browser_fixture.py
```

CI runs the Python suite and the Node tests on macOS and Linux, except
`test_m3_lifecycle_browser_ui.js` and `test_shell_a11y_ui.js`, which drive the
`agent-browser` CLI and need it installed.

The browser fixture uses disposable workspaces and cannot launch agents. The
packaged-import regression tests the installed entry-point layout, not just the
source-directory import path. Updating files does not reload a running dashboard:
restart only its server at an idle boundary, preserving the same environment and
storage paths. Do not interrupt queued dashboard actions or provider requests.
The autonomous runner is independent and does not need restarting.

## macOS app

`macos-app/` contains a native macOS app that hosts the same dashboard in its
own window. See [macOS app](macos-app.md).

See also: [Registry API](registry-api.md) · [Interventions](interventions.md) · [Models](models.md)

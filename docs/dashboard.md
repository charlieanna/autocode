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

Open the printed loopback URL. New conversations do not require a project: the Requirements Planner
can clarify the idea first, then the conversation can be attached to a Git
workspace for joint requirements planning and review.

When `--watch-root` is used, discovery stops at each Git project boundary and
skips generated or internal trees such as `.git`, `.autocode`, `node_modules`,
and virtual environments. List responses also keep only the compact stage data
needed by the task index, so broad workspace roots remain safe to poll from the
browser without serializing complete provider payloads or process histories.
Overlapping task-index polls share the same in-progress snapshot, and each
snapshot reuses one watched-project discovery result instead of building a
queue of duplicate scans.

## What you can do

A task's **Now** view presents the runner's
saved current stage, the exact user action required, plan-revision
approval, and output-review approval. Archiving tasks, conversations, or
projects only changes the dashboard's local visibility; it never deletes source
files or runner checkpoints.

Choose initial reasoning under **Models & reasoning** when starting a conversation.
For a saved task, open its **•••** menu and use **Reasoning for next steps**. The
dashboard changes the saved per-role setting only at a stage boundary; it never
interrupts or mutates an in-flight provider request.

The dashboard uses `$AUTOCODE_HOME/dashboard` by default (or
`$AUTOCODE_DASHBOARD_HOME`) for conversations and reversible archive settings.
It binds only to `127.0.0.1`, starts no development server for task previews,
and uses documented Autocode commands for task changes.

## One local dashboard, two layouts

The **Now** tab combines the live monitor with the project/task console:
verified worker identity, exact saved role/model settings, current objective,
filtered tool activity, review findings, acceptance counts, and artifact progress.
**Focus view** hides navigation without starting a different server. Dark and
light themes are local browser preferences. Task links use compact local
`#run=r-…` identifiers; old `#task=…&run=…` links remain supported. A focused
link can append `&focus=1` to either form.

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

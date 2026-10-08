# Task lanes and multiple tasks

[← Back to README](../README.md)

## Connected task lanes

Use `autocode tasks` (or `autocode-tasks`) when several complete Autocode tasks must
run in order or independently. Tasks inside one lane run sequentially in the same
worktree, so later tasks see earlier source changes. Different lanes use different
worktrees and may run concurrently. Every item invokes the normal UI or code loop;
the task runner does not replace planning, validation or completion gates.

```json
{
  "version": 1,
  "name": "dashboard release",
  "lanes": [
    {
      "id": "application",
      "tasks": [
        {"id": "design", "mode": "ui", "task": "Design the operations dashboard"},
        {"id": "build", "mode": "code", "task": "Build the dashboard", "ui_from": "design"},
        {"id": "polish", "mode": "code", "task": "Polish loading and error states"}
      ]
    },
    {
      "id": "documentation",
      "tasks": [
        {"id": "guide", "mode": "code", "task": "Write the operator guide"}
      ]
    }
  ]
}
```

```sh
autocode tasks flow.json --workspace /path/to/project --max-parallel 2
```

The command saves its checkpoint under `.autocode/task-flows/`. A code task can
pause at its normal plan-approval or intervention boundary; its `run_dir` appears in
the JSON result. Review or resume that ordinary Autocode run, then invoke the same
task-flow command again. Completed UI tasks can feed a later code task through
`ui_from`. `--dry-run` validates and previews the lanes without creating worktrees.

Parallel lanes intentionally remain separate branches. Autocode does not guess how
to merge parallel source changes. Put dependent tasks in one lane, or explicitly
merge completed branches before starting a task that combines them.

## Building components of an architecture in parallel

`autocode components ARCHITECTURE --workspace REPO` takes a design already
committed in `REPO` — `components.json`, `dependency_trace.json` and
`contracts/*.schema.json` — and builds each component as its own AutoCode task
run under `REPO/.autocode-components/<id>`, in parallel with any component it
does not depend on. A component owns only `components/<id>/`; this is checked
against its actual changes at integration, not only requested in its brief.

```sh
autocode components architecture --workspace /path/to/project --joint-planning
```

Without `--auto-approve`, a component that needs a plan approved, a question
answered, or a review accepted stops there; inspect and resume it directly with
`autocode --workspace /path/to/project --run-dir RUN_DIR --status`, using the
`run_dir` this command prints for that component. `--auto-approve` answers
those for every component automatically, including approving the displayed
plan — an explicit person's decision to delegate, not a default choice.

`--integrate TARGET` combines every finished component's changes into `TARGET`
(created fresh from the repository's HEAD if it does not exist yet), left
uncommitted for review, the same way a single AutoCode task leaves its own
work. An existing `TARGET` must be the top directory of a worktree of the same
repository; anything else is refused before a file is written. Extra flags for the underlying task runs (models, reasoning effort,
provider) go after `--options`, shell-quoted.

### A component with a Figma design

Design the screen before starting the component build. In `components.json`,
give that component a `ui_run` pointing to a completed, accepted AutoCode UI
run. Relative paths resolve from the architecture directory, not from the
component worktree:

```json
[
  {
    "id": "web",
    "description": "Team dashboard using the accepted screen design",
    "requirements": ["R1"],
    "depends_on": ["api"],
    "publishes_contracts": [],
    "consumes_contracts": ["team"],
    "ui_run": "../.autocode-ui/runs/ACCEPTED-RUN"
  },
  {
    "id": "api",
    "description": "Team API",
    "requirements": ["R2"],
    "depends_on": [],
    "publishes_contracts": ["team"],
    "consumes_contracts": []
  }
]
```

The UI run may live outside the repository. Its accepted handoff and artifacts
are validated before any component starts. The component receives its ownership
and contract brief plus the accepted UI brief through the ordinary `--ui-run`
build path. It still owns only `components/web/`, and plan approval and independent
implementation review remain required.

Alternatively, use `"figma_file": "https://www.figma.com/design/FILEKEY/Project"`
to supply an existing Figma reference. That URL alone does not establish an
accepted design run. Choose one field per component. Components without either
field keep their ordinary text brief.

Figma implementation requires the existing native Codex Figma workflow, ChatGPT
login and the connected Figma plugin; use `--engine codex` for this build.
Any other explicit `--engine` is rejected before components start. See
[Figma design and implementation](figma.md) for setup and visual verification.
Implementation and review inspect the live file because it can change remotely.
This command consumes an existing design; it does not create a design run.

Progress is saved in `.autocode-components/manifest.json` as each component
starts and stops. Running the same command again continues the build:
finished components are left alone, and a component that stopped for input
picks up from where it stopped, in the same worktree and run. You can answer or
approve that component's own run directly first, or pass `--auto-approve` the
second time. If `components.json`, a contract or an accepted UI handoff changed
since the saved build, the command refuses to resume, because the saved components
were built against the old contracts or designs; remove `.autocode-components/`
to rebuild from scratch. A worktree the manifest does not record is refused too,
rather than guessed at. Rerunning with the same `--integrate TARGET` is safe:
a component whose exact result `TARGET` already holds (the same content and
file mode at every path it changed) is left alone and listed in the summary's
`integration.already_applied`, and a component that finished since is added.
A component is applied only to a `TARGET` that holds none of its result yet,
so a rerun never applies a change twice. Otherwise integration stops at that
component and names the paths that differ. If `TARGET` was edited there or is
checked out at another commit, the message says to integrate into a new
target. If HEAD itself changed those paths since the component was built (for
example, an earlier integration was committed and then edited), a new target
would differ the same way, so the message says to rebuild the component:
remove its worktree `.autocode-components/<id>` and run the command again.

When the work is one requirement that must be split, built in parallel and
combined, use a [program](program.md) instead of lanes: workstreams declare
dependencies and ownership, dependents branch from the merged results of their
prerequisites, and completed workstreams are merged onto one integration branch
with conflicts paused for you.

### Declaring how a component runs

A row of `components.json` may carry an optional `runtime` block saying how that
component runs once the system is combined. It is checked before any component
starts, and it adds instructions to that component's brief, which tells each
Builder how its component will be run. `--run-local` starts the components this
way (see [Running the combined system locally](#running-the-combined-system-locally)).

For a runnable architecture request naming `components.json` and runtime, smoke or
Compose, the architecture Planner receives this runtime grammar and must deliver a
runtime block for every component plus `smoke.json` as design data. The smoke flow
must cross component boundaries, not merely check health. This is architecture work;
it does not start Docker or authorize deployment.

```json
{
  "id": "gateway",
  "description": "Forwards note requests to the store",
  "requirements": ["R2"],
  "depends_on": [],
  "publishes_contracts": [],
  "consumes_contracts": ["note"],
  "runtime": {
    "kind": "service",
    "port": 8002,
    "start": "python3 server.py",
    "health": "/health",
    "runtime_depends_on": ["store"],
    "env": {"GREETING": "hello"}
  }
}
```

There are four kinds:

- `service`: a long-lived HTTP server. It needs `port` and an HTTP `health` path.
- `worker`: a long-running process with no port, for example a queue consumer.
  It has no `port`; `health` is optional.
- `database`: built from its own `dockerfile` (a start command on the Python
  image cannot run a database). It needs `port`: the port is never published,
  but the database's Builder and the Builders of the components that connect
  to it are all told it, since none of them sees the others' code. `health` is
  a command and is required.
- `library`: never started on its own. Its block is exactly `{"kind": "library"}`,
  and nothing may name it in `runtime_depends_on`.

| Key | Kinds | Rules |
| --- | --- | --- |
| `kind` | all | Required: `service`, `worker`, `database` or `library`. |
| `port` | service, database (required for both) | The port inside the container: an integer from 1 to 65535. |
| `start` | service, worker | A shell command of one line, at most 1000 characters. It runs with `/bin/sh -c` in `/app`, in an image built from `python:3.12-slim` with `components/<id>/` copied to `/app`. `$PORT` in it is expanded inside the container. |
| `dockerfile` | service, worker, database (required) | A relative path inside `components/<id>/`, which is its build context: `/`-separated segments of letters, digits, `.`, `_` and `-`, with no empty, `.` or `..` segment, at most 128 characters. The Builder writes the file. A service or worker takes exactly one of `start` and `dockerfile`. |
| `health` | service (required) | An HTTP path that answers with a 2xx status once the service is ready: it starts with `/`, contains only letters, digits, `.`, `_`, `~`, `/` and `-`, has no `..`, and is at most 128 characters. |
| `health` | worker (optional), database (required) | A command run inside the container that exits 0 once it is ready: a JSON array of 1 to 32 nonempty one-line strings, such as `["pg_isready", "-U", "app"]`. |
| `runtime_depends_on` | service, worker, database | The components it connects to while running, each listed once. Each must be a service or a database, never itself, and the graph must have no cycle. It is separate from the build-time `depends_on`. |
| `env` | service, worker, database | At most 32 extra environment variables. Names are upper case (`A`-`Z`, digits and `_`, not starting with a digit, at most 64 characters); values are strings of one line, at most 1000 characters. `PORT` and the names generated for a runtime dependency are reserved. |

The block is refused if it has an unknown key, or if any string in it contains a
backtick, a NUL, a line break (CR, LF, VT, FF, NEL, U+2028, U+2029 and the
other characters Python splits lines on) or a lone surrogate (a `\ud800`-style
escape that is not half of a pair). A row key that looks like a misspelled `runtime` (`runtme`, `run_time`,
`runtimes`) is refused too; other unknown row keys are still ignored. The id of
a component that runs (service, worker or database) must be a lowercase DNS
label that starts with a letter (`a`-`z`, digits and `-`, at most 63 characters,
not ending in `-`), because it becomes its container's host name and part of
environment variable names. It must not be `localhost`, `ip6-localhost`,
`ip6-loopback`, `ip6-localnet`, `ip6-mcastprefix`, `ip6-allnodes` or
`ip6-allrouters`: inside every container those names mean the container itself,
so a component connecting to one would reach itself. Library ids follow the
ordinary component-id rule.
Each refusal names the component and the key, and the command stops with a usage
error before any component starts.

Each running component's container gets, in this order: `PORT` (when it has a
port); for each runtime dependency, its address; then its own `env`. A service
dependency `store` gives `STORE_URL=http://store:8001`. A database dependency
`db` gives `DB_HOST=db` and `DB_PORT=5432`. A `-` in
an id becomes `_`, so `link-api` gives `LINK_API_URL`. `env` values are used
literally: they are committed with the architecture, which a model may have
written, and nothing is taken from your own environment. Do not put real secrets
in them.

The Builder's brief gains these sentences right after the line naming the
directory it owns, before any contract lines:

- A service: it runs as a long-lived HTTP service in its own container, must
  listen on `0.0.0.0` (not only on `127.0.0.1` or `localhost`) at the port in
  `PORT`, and must answer `GET <health>` with a 2xx status once it is ready.
- A worker: it runs as a long-running process with no HTTP port and nothing
  connecting to it, and must keep running until it is stopped rather than exit
  when it is idle; with `health`, that command must exit 0 once it is ready.
- A database: other components reach it only over the combined system's
  internal network, never through a published port; it must accept connections
  on its `port` on `0.0.0.0`; its `health` command must exit 0 once it accepts
  connections.
- With `start`: its container is built from `python:3.12-slim` with
  `components/<id>/` copied into `/app`, and runs that command in `/app`. The
  command and any `health` command are quoted exactly as written, non-ASCII
  characters included.
- With `dockerfile`: provide that file; it can copy only files from inside
  `components/<id>/`, and its image must start the component.
- For each runtime dependency: reach it only through the variables above, never
  through a hard-coded host or port.
- With `env`: the extra variables its container gets.
- A library: it is not started on its own.

These sentences are obligations, so each component's Requirements stage traces
them like the rest of its brief. Declare runtime blocks before the first build.
A block lives inside `components.json`, so adding or editing one changes the
saved build's identity, and a later run refuses to resume ("the architecture
changed"); remove `.autocode-components/` to rebuild. Records without runtime
blocks build exactly as before.

### Declaring the smoke check

The architecture directory may also hold `smoke.json`, beside `components.json`
and `contracts/`: the HTTP requests that show the combined system works. It is
read only by `--run-local`, which starts the combined system on your machine
(see [Running the combined system locally](#running-the-combined-system-locally));
otherwise AutoCode ignores the file. It is not part of the saved build's
identity, so editing it never forces a rebuild.

```json
{
  "version": 1,
  "steps": [
    {"name": "create a note through the gateway", "service": "gateway", "method": "POST",
     "path": "/notes", "body": {"text": "hello"}, "expect_status": 201,
     "expect_json": {"text": "hello"}, "capture": {"note_id": "id"}},
    {"name": "read it back through the gateway", "service": "gateway", "method": "GET",
     "path": "/notes/{{note_id}}", "expect_status": 200, "expect_json": {"text": "hello"}},
    {"name": "the store holds it", "service": "store", "method": "GET",
     "path": "/notes/{{note_id}}", "expect_status": 200, "expect_json": {"text": "hello"}}
  ]
}
```

The file has exactly two keys: `version`, which is `1`, and `steps`, a list of 1
to 30 steps. Each step is one request:

| Key | Rules |
| --- | --- |
| `name` | Optional: 1 to 80 printable characters on one line, unique across steps. Defaults to `step N`. |
| `service` | Required: the id of a component of kind `service`. A worker has no port and a database's port is never published, so check a database through a service that uses it. |
| `method` | Required: `GET`, `POST`, `PUT`, `PATCH` or `DELETE`. |
| `path` | Required: starts with `/`, at most 300 characters of printable ASCII with no spaces, and no `://` or `@`. It may carry a query string. |
| `body` | Optional: any JSON value, sent as `application/json`. Not allowed on `GET` or `DELETE`. |
| `expect_status` | Required: the exact HTTP status expected, an integer from 100 to 599. |
| `expect_json` | Optional: the JSON the response must match. An object matches when each of its keys is in the response and matches there, so the response may carry more keys; lists and other values must be equal, and `true` is not `1`. |
| `capture` | Optional: `{"variable": "key"}` pairs. Each takes the value of a top-level key of the JSON response, which must be a string or an integer, for later steps. Variable names are lower case (`a`-`z`, digits and `_`, starting with a letter, at most 32 characters) and each is captured by one step only. |

A later step uses a captured value as `{{variable}}` in its `path` or in a string
value of its `body`; a step cannot use a value before an earlier step captures it, and
`{{` may not appear anywhere else: not in `name`, in `expect_json`, or in an object
key of `body`, where nothing is filled in. In the path the value is percent-encoded, `/`
included, so it stays one path segment or query value. In the body a string that
is exactly `{{variable}}` becomes the captured value itself, so an integer stays
an integer; inside a longer string the value is inserted as text.

Unknown keys, duplicate keys and anything outside these rules are refused,
naming the step and the key; `NaN`, `Infinity` and a number too large to send
(such as `1e999`) are refused as invalid JSON. Steps run in order, each request going to its
service's port on `127.0.0.1`, without a proxy and without following redirects,
and the check stops at the first step that fails. Nothing in `smoke.json` is run
as a command.

### Running the combined system locally

```sh
autocode components architecture --workspace /path/to/repo --integrate integration --run-local
```

`--run-local` needs `--integrate TARGET`, and Docker with Compose v2 (the
`docker compose` plugin, 2.17 or newer) and a running Docker daemon on this
machine. Before any component is built, the command refuses with a usage error
unless every component declares a runtime block, at least one is a `service`,
the runtime dependencies have a start order, `smoke.json` is valid and sends
requests only to services, `docker compose version --short` reports 2.17 or
newer, `docker version` reaches the daemon, and that daemon is local: its
endpoint is selected using Docker's precedence: nonempty `DOCKER_CONTEXT` first,
otherwise `DOCKER_HOST`, otherwise the current/default context resolved by
`docker context inspect`. Context resolution and rejection of a remote endpoint
happen before daemon contact. The validated endpoint must be a `unix://` or
`npipe://` socket. A daemon reached over `tcp://` or `ssh://` would
publish the ports on its own machine, where the checks on `127.0.0.1` cannot
reach them. `--keep-running` and `--health-timeout` are refused without
`--run-local`.

The resolved local socket is pinned with `docker --host <endpoint>` for daemon
checks and every subsequent startup, health/port query, log, teardown and recovery
command. Changing the ambient context does not redirect an in-progress run.
`autocode doctor` reports optional Docker, Compose and local-daemon readiness;
missing or unsupported setup is WARN-only, not a blocker for ordinary builds.
Doctor diagnoses setup but does not install Docker or start its daemon.

After every component has finished and been integrated cleanly (otherwise the
summary's `local_run.status` is `not_run`), it:

1. Checks that `TARGET` holds `components/<id>/` for each component that runs,
   and the `dockerfile` of each that declares one.
2. Writes the Compose file to
   `<workspace>/.autocode-components/.local-run/<project>/compose.json`, never into
   `TARGET` or any tracked file. The leading dot keeps it apart from component
   worktrees, `.autocode-components/<id>`, since no component id starts with a dot. `<project>` is a new Compose project name for
   each run, `autocode-` and 12 hex digits, passed with `-p`.
3. Starts the components in runtime-dependency layers, one layer at a time,
   with `docker --host <endpoint> compose -p <project> -f <file> up -d --build --no-deps <ids>`,
   and waits for the whole layer before starting the next: a service once
   `GET <health>` on its published loopback port (from `docker compose port`)
   answers 2xx; a worker or database with a `health` command once Compose
   reports it `healthy`; a worker without one once it is running. A container
   that exits or turns `unhealthy` fails at once, reported as `<id> is not
   running (state <state>)`. Each layer may take `--health-timeout SECONDS`
   (a positive, finite number; default 120) to become ready.
4. Runs the smoke steps in order against `127.0.0.1:<published port>`, stopping
   at the first that fails.
5. Tears the project down with
   `docker --host <endpoint> compose ... down -v --remove-orphans --rmi local`, on success, on
   failure and on Ctrl-C alike. This also removes the images the run built:
   every run is a new project, so they would otherwise pile up. `--keep-running`
   deliberately leaves it running instead and prints that `down` command to stop it.

A failed teardown or uncertain owned CLI-helper cleanup changes the result to
`failed`, even if every smoke step passed. `torn_down` stays false and
`cleanup_detail` explains why; `stop_command` is shell-quoted from the exact argv
(including endpoint, project and file) for recovery. Intentional `--keep-running`
is distinguished by `kept_running: true`, not misreported as successful teardown.

On a failure, the command prints which component or step failed and the last 50
lines of that component's logs (`docker compose logs --tail 50`). When no single
component is to blame (`up` failed for a layer of several components, or
`docker compose ps` failed while waiting on a layer), it prints the last 50
lines of each component in that layer. Progress goes
to standard error. The JSON summary gains a `local_run` key: `status` (`passed`,
`failed` or `not_run`), `project`, `compose_file`, `layers`, `ready`, `ports`
(the published host port of each service), `steps` (each step's `name`,
`service`, `method`, the `path` sent, the `status` received, `ok` and `detail`),
`failed_component`, `failed_step`, `detail`, `logs`, `torn_down` and
`stop_command`, `docker_endpoint`, `cleanup_detail` and `kept_running`. The exit
status is 1 when the local run did not pass, including cleanup failure.

## Multiple tasks in one project

New implementation tasks automatically get separate Git worktrees and branches,
so two terminal commands or dashboard conversations can use the same project:

```sh
autocode "Add billing history" --workspace /path/to/project
autocode "Fix search navigation" --workspace /path/to/project
```

Each task starts from the project's **committed HEAD**, on an `autocode/<task>-<id>`
branch under `.autocode/worktrees/`. The original checkout and its uncommitted
changes are retained. Commit changes first if new tasks should include them.
Dependencies and ignored environment files are not copied into the new worktree.
The command prints the task workspace and branch; the dashboard discovers its run
through the registry. Each worktree has its own runner lock, checkpoints, and code.
Two writers still cannot operate on the same worktree.

AutoCode's own directories (`.autocode/`, `.autocode-components/`, `.autocode-ui/`)
each hold a `.gitignore` containing `*`, so run state, logs and nested task worktrees
never appear in `git status` or get staged by `git add -A` in your checkout or in a
task worktree. Your own `.gitignore` is not touched, and a `.gitignore` you already
put in one of these directories is left as it is.

### When a task finishes

When a run in a task worktree completes, AutoCode commits the delivered source to the
task's branch (`autocode/<task>-<id>`, author `AutoCode <autocode@localhost>`) and
prints the branch and commit. Only the branch moves: the worktree is detached at the
commit it started from, with the delivered changes still in its files, so the
completion evidence (pinned to the worktree's HEAD) stays current and you can still
inspect or run the result there. Later work in the same worktree, such as a rework
after feedback, is committed on top at its next completion. Runs started with
`--in-place`, program workstreams and components are never committed this way.

Review and merge the branch like any other, for example `git merge autocode/<task>-<id>`
from your checkout. Then remove finished worktrees:

```sh
autocode clean-worktrees --workspace /path/to/project         # list what would be removed
autocode clean-worktrees --workspace /path/to/project --yes   # remove it
```

A worktree is removed only when every run in it is `TASK_COMPLETE`, its source is
exactly what its branch holds, and no runner holds its lock. Its `.autocode/`
records (run state, logs, evidence) are copied to `.autocode/archive/<worktree>/`
first; the branch is kept. Anything else is listed with the reason it is kept.
Worktrees recorded by an `autocode program` are left to that command. The
dashboard and registry show a removed worktree's runs as `workspace_missing`.

Resume from the project while the task is its only unfinished run, or from inside the
task worktree, or name the run from anywhere:

```sh
cd /path/to/project/.autocode/worktrees/TASK && autocode resume
autocode --run-dir /path/to/project/.autocode/worktrees/TASK/.autocode/runs/RUN
```

Existing runs retain their original checkout. `--in-place` explicitly starts a new
task in the selected checkout. Only one run's agents work in a checkout at a time:
a second run started there (or resumed there) while another run's agents are working
prints which run holds the checkout and exits with status 2, changing nothing. Run
the same command again once the other run stops, or start the task without
`--in-place` so it gets its own worktree. Answering, approving or giving feedback to
a waiting run launches no agent and is not blocked. AutoCode never merges branches;
see [When a task finishes](#when-a-task-finishes) for committing and removing worktrees.

See also: [Figma design](figma.md) · [Workflow](workflow.md)

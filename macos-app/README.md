# Autopilot — the Autocode macOS app

A native macOS app that hosts the Autocode browser dashboard in its own window,
with one rule: **every conversation and task action goes through the autopilot
controller**. When you chat with a task in the app, the dashboard executes
`python tools/autopilot.py …` from this checkout — messages, answers, delegated
defaults, plan approvals, review approvals, feedback requests, continue/resume,
and task creation from an attached conversation. Read-only status,
intervention, and registry JSON calls run through `autopilot` the same way.

The app is a thin, deterministic shell:

```text
Autopilot.app
  └─ WKWebView ──────────────► http://127.0.0.1:<free-port>   (loopback only)
       └─ dashboard server ──► python tools/autopilot.py …    (--runner flag)
```

Project-free "New conversation" chats talk directly to OpenCode planning (GLM),
as in the browser; attaching such a conversation to a project launches the task
through autopilot.

## Build and run

Requires macOS 13+, the Xcode Command Line Tools, and a Python interpreter with
`psutil` (the app probes, in order: the checkout's `.venv/bin/python`, the pipx
`autocode-supervisor` venv, then system `python3` locations).

```sh
cd macos-app
./build-app.sh          # compiles, bundles dist/Autopilot.app, ad-hoc signs it
open dist/Autopilot.app
```

`build-app.sh` bakes the current checkout path into the app as the default and
regenerates it on every build. Change the checkout later in the app's Settings
without rebuilding.

## Using the app

- **Chat** — task conversations, answers, approvals, feedback, and continue all
  execute through `autopilot`, exactly like the documented command-per-turn
  interface (`autopilot --workspace … --run-dir … --answer/--feedback/--approve-goal …`).
- **Reload Page (⌘R)** / **Open in Browser (⇧⌘B)** — refresh the view or hand the
  same loopback URL to a real browser.
- **Restart Dashboard Server (⇧⌘R)** — stops and restarts only the local server.
- **Settings** — checkout path (validated against `tools/autopilot.py`) and port
  (`0` picks a free port automatically, so it never collides with a
  terminal-started `autocode-dashboard`).
- The status bar shows the running URL, the chosen Python, and that the
  autopilot runner is active.

## Lifecycle and safety

- Quitting the app stops **only the dashboard server**. Autocode runner
  processes are spawned in their own process groups and keep working; their
  state and logs are saved in each run directory. Use the dashboard's pause
  controls first if you want a task stopped.
- Server stdout/stderr (combined) are appended to
  `~/.autocode/macos-app/dashboard.log`. The button on the failure screen
  reveals it in Finder.
- The server binds to `127.0.0.1` only and performs the same same-origin
  checks as the browser dashboard.
- The app enriches `PATH` with `~/.local/bin`, `/opt/homebrew/bin`, and
  `/usr/local/bin` so `git`, `opencode`, and provider CLIs resolve even when the
  app is launched from Finder/Dock with launchd's minimal environment.
- Interpreter probes and server startup are watchdogged. Occasional transient
  Python-startup stalls self-resolve, so the app retries automatically (up to
  three 25-second attempts) before surfacing a Retry-able failure. Diagnostics
  tagged `[autocode]` go to the unified log — filter for the process name
  `Autocode` in Console.app, or run the binary from a terminal to see them on
  stderr.
- On startup the app reaps dashboard servers left orphaned by a crashed
  previous instance (only processes running this checkout's autopilot runner
  whose parent is launchd). Terminal-launched dashboards are never touched.
- Conversations and archive settings live under `$AUTOCODE_HOME/dashboard`
  like the browser dashboard, and the stores are lock-protected, so the app and
  a terminal-started dashboard can coexist (each keeps its own action log).

## Development

```sh
cd macos-app
swift build && ./.build/debug/Autocode   # run from CLI for console output
```

Swift sources live in `Sources/Autocode/`:

| File | Role |
| --- | --- |
| `AutocodeApp.swift` | App entry, menus, quit cleanup |
| `DashboardServer.swift` | Interpreter discovery, server process, watchdogs, log |
| `WebView.swift` | WKWebView wrapper and navigation errors |
| `ContentView.swift` | Loading / ready / failure states and status bar |
| `SettingsView.swift` | Checkout and port settings |
| `BuildConfig.swift` | Generated default checkout path |

The app icon (`icon/AppIcon.icns`) is an aviation **attitude indicator**
(artificial horizon) — the universal autopilot symbol: sky over earth, pitch
ladder, amber aircraft wings, and a roll pointer on a dark avionics panel.
Regenerate with `swift icon/gen-icon.swift icon-master.png`; the build script
embeds it and registers `CFBundleIconFile`.

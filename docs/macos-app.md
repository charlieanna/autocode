# macOS app

[← Back to README](../README.md)

`macos-app/` contains a native macOS app that hosts the same dashboard in its
own window, with every conversation and task action routed through the
autopilot controller: the app starts the dashboard server with
`--runner tools/autopilot.py`, so messages, answers, approvals, feedback,
continue actions, and the status/intervention/registry reads all execute as
`python tools/autopilot.py …`. Project-free planning conversations still talk
directly to OpenCode, and attaching one to a project launches through
autopilot.

```sh
cd macos-app && ./build-app.sh   # builds dist/Autopilot.app (needs Xcode CLT)
open dist/Autopilot.app
```

The server binds to a free loopback port, and quitting the app stops only the
dashboard server; runner processes are independent and keep their saved state.
See [the macOS app readme](../macos-app/README.md) for details.

See also: [Browser dashboard](dashboard.md)

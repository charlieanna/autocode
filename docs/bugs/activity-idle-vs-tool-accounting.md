# Idle bound replaced by the tool bound (#1161)

A live run of `conformance-lifecycle` sat in `glm_revise_report_repair` for 71
minutes with `stage_timeout_seconds` 1200 and `idle_timeout_seconds` 300, no
provider output for 36 minutes, and a supervision receipt still reading
`phase: armed`, `cause: null`. Neither bound fired. The accounting modules are
unchanged on master, so the behavior is still as reported.

A provider's inactivity bound is not a clock. `ActivityMonitor` latches
`_had_event_activity` on the first recognized provider event, and a live,
non-MCP, non-zombie descendant then arms a process fallback. While that
fallback is armed there is no idle measurement at all: the elapsed value becomes
a tool elapsed, the idle branch of `_expired` is unreachable, and the supervisor
accounts against `tool_timeout_seconds` instead of the operator's idle limit.
A fresh completion re-bases the fallback start, so intermittent output defers
even the tool bound. This is why the scripted `provider-idle-hang` fake trips
idle while a wrapper-launched provider does not: the hybrid tool and the
configured `report_file` routes supervise the wrapper, leaving the real engine a
grandchild, which is exactly the shape that arms the fallback.

The stage hard cap is a separate question. Both the in-process deadline and the
keeper's deadline arm from the same truthiness test on the stage timeout, so
surviving 71 minutes also requires the timeout to have reached that launch
falsy. Nothing retained in the repository distinguishes the two, because the run
directory was not committed.

The fallback is deliberate: a bare descendant is not proof of a running tool,
which is what keeps a hung transport with a quiet log from burning the whole
tool budget. The unguarded case is the converse, a descendant that outlives the
last provider event with no open tool start. Deciding that is a policy question
about what a descendant proves, and any change must keep the existing controls
passing. A fake-clock regression that pins the current behavior and fails on the
correct one is one provider event, one live grandchild, the clock advanced past
the idle limit, expecting an idle expiry rather than a tool one.

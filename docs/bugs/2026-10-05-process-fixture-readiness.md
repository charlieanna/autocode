# Detached-worker fixture readiness

The stack release gate reproduced `test_normal_provider_exit_stops_background_writers`
failing on unchanged pinned master `82cd0446a5a8bf1d215cdb8a57b28f625abb1e41`
with Python 3.14.6. Process ownership was published before the fixture worker
wrote `worker.pid`; the test checkpoint returned without releasing the parent.
Later samples retained the same membership and correctly did not republish it,
so the waiting parent reached the unchanged 30-second stage watchdog.

The fixture now emits and waits for a bounded stdout readiness marker after
writing its pid, before starting supervision. Both the normal-exit assertion
and explicitly triggered timeout still require the detached writer to be owned
and stopped. Product deadlines, process supervision and cleanup are unchanged.
Failed fixture readiness also stops its owned process tree.

Qualification: normal exit and detached timeout controls, followed by the full
`tests.test_process` module. The pinned-master diagnostic is retained only in
ignored run evidence, not committed logs.

A later full-module rerun also exposed the unchanged watchdog test's fixed
0.4-second sleep as an exit-observation race under concurrent load. Its sampling
fixture now waits for the actual child exit with a three-second test guard before
sampling returns. The real stage cap remains 0.05 seconds, and the independent
SIGTERM-ignore/SIGKILL escalation control retains its original semantics.

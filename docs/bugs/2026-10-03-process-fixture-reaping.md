# Process fixture cleanup can race a correctly reaped child

The full integration gate caught `NoSuchProcess` in the process regression's
`finally` block: the child disappeared between `is_running()` and `status()`.
The preceding assertions had already verified its saved identity and that no
owned background writer remained live.

Fixture cleanup now tolerates only `NoSuchProcess` across status, kill and wait.
Permission denial and wait deadlines still fail. A deterministic control checks
each disappearance window, live-worker cleanup and permission-denial propagation;
the real process-tree assertions remain unchanged. Production code is unchanged.

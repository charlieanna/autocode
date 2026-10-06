# Owner-loss tests raced the keeper's first discovery sample

`HarnessOwnerLossTests` (from #536) failed on CI ubuntu with one surviving
process after a SIGKILL: `[PID] is not false`, no other detail — 3 failures in
8 executions on 2026-10-06, on three unrelated branches (#527, #547's PR run,
#548), passing on re-run. Both re-runs passed, but the coin flip keeps
blocking random PRs. Not reproducible on macOS; deterministic (2 of 2) in an
ubuntu:24.04 container.

## Mechanism

The fake codex provider is a direct child of the CLI with no per-command
supervision receipt; the only owner of its lifetime is the CLI's
`protect_owner` keeper, seeded with the CLI alone and discovering descendants
by sampling every 50 ms. Its `trigger('owner_lost')` SIGTERMs the CLI and a
2-second timer SIGKILLs `tree.known` — only what discovery has recorded.

The test's event barrier fires at the provider's first words and kills
immediately, so the kill can land inside the keeper's first sample window:
`known` still holds only the CLI. The SIGTERM-immune provider is then never
recorded, the CLI exits, the provider is reparented to init, and every later
escalation operates on an empty tree and reports clean success — the dumped
receipt shows `phase: stopped, cleanup_error: None` with `processes` holding
exactly one row, the CLI.

## Fix

The test now waits for the keeper's receipt to record the provider process
(the receipt is re-saved on every inventory change, so this is an event
barrier like the fifo) before faulting. It now exercises the guarantee the
system actually gives — a recorded tree is cleaned on owner loss — instead of
racing the first sample. The no-survivors assertion also names each survivor
(pid, ppid, status, cmdline), so a future failure says who escaped. Verified:
10/10 in the container that failed deterministically before, green on macOS.

## Residual exposure (not fixed here)

Owner loss that strikes inside the real first-sample window — before the
keeper records a freshly spawned provider — still strands that provider; the
receipt then claims clean cleanup. Production timing makes this far less
likely than the test's instant kill, but the airtight fix is recording the
spawn at `Popen` time (a supervision-protocol change: the launcher tells the
keeper the child's identity instead of relying on sampling), plus freezing
the root before interrupting it so the discovery root cannot exit first.
Filed as #554.

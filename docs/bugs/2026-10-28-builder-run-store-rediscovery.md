# Builder run-store rediscovery (#2435)

The completed paging TUI run `20261028-193924-chat-b246ca` on
`703b656b5a73fb8c653dd8ea2389eb6fb8221d2a` retained four consecutive native
turns reading a missing evidence directory and its ancestors before the first
capture. From the first step-start to the fourth step-finish was 17.707 seconds.
The Builder also tried to read mutable `state.json` in a step shared with useful
source reads; that read failed with a native `state.json (deleted)` ENOENT.
This does not prove the state file was intentionally unreadable. Bundle manifest:
`b38cc7508eefaffe468297f9affcc404d3886aebf6816b3f8aa0023bfae9d7e2`.

Builder guidance now identifies the supplied current task/approved contract as
its run context and directs it away from reconstructing that context through
mutable runner bookkeeping. It explains capture's existing behavior: missing
output parents are created by capture, so listing ancestors or preparing a
directory first is unnecessary. Relevant project instructions, source and cited
immutable evidence still need inspection; real capture failures still count.
No sandbox permission, verifier, approval, route or limit changes.

The regression executes the handoff's public capture command with absent output
parents and no state file, checks both successful and failed command evidence,
and rejects a duplicate receipt before executing its command. This proves the
instructions match capture behavior, not that a live model follows them. Native
adoption and any end-to-end timing improvement remain to be measured; 17.707
seconds is an observed interval, not a guaranteed saving or the full #2113 gap.

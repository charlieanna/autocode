# Verified delivery waits were escalated as human permission questions

A dashboard integration milestone needed a separately running Planner task's accepted
source. Although transport was already authorized, its missing snapshot became a
permission question asking the user to supply manifests and review pins. There was no
persisted producer/consumer link or worker to perform the handoff after completion.

An exact-request CLI dependency binding now records the authorized producer and file
allowlist, replaces that question with a dependency wait, and permits a task-run driver
to transport current independently accepted evidence and resume integration. Review and
source pins are rechecked; missing evidence never grants approval or completion.

The CLI regression exercises two real task runs with a fake provider, restart while
waiting, stale producer source, replayed transport, corrupt delivery rejection, and
automatic consumer continuation. The dashboard classifies the wait without requesting
a user decision.

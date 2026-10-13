# Session custody and keeper cleanup CI failures

The suite failures shared by PRs #1191–#1194 exposed two runtime defects and
test doubles that had not followed the session admission interface.

Verification preparation recorded the owner without its native session ID,
although command supervision authenticated the session-aware launch identity.
Preparation now records that ID. Replay compares the authenticated launch
owner and terminal inventory through the same compact birth-identity projection;
the other custody, receipt and publication checks remain required.

Whole-host PID churn could exhaust three scans even when a command's admitted
session was empty. A scan can ignore a changed PID only after native inspection
proves it foreign. Unclassified departures, vanished additions and denied
inspection remain unknown. A typed transient scan failure gets at most half a
second to produce the same exact proof; keeper deadline and signal threads keep
running. Each scan rechecks leader births and replaces stale observations before
expanding group ownership, so a reused leader cannot restore revoked authority.

The cleanup receipt is written before the keeper exits and releases its checkout
lease. The kill-window regression waits for both within its existing time bound.
Test doubles now implement session inventory and the workspace-aware lifeline
interface without inventing admission evidence.

Focused verification covers preparation/publication success and refusal, fake
clocks and ownership races, real process cleanup with fake providers, and the
original unrelated-process churn reproduction. No live models are needed to
exercise these deterministic process and custody failures.

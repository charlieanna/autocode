# A tool-free review recovery could not see its prior tool results

The ISO-week trial `20261105-031041-fix-the-weekly-timesheet-report-dates-around-new-496267b7`
spent 122.9 seconds in Requirements and 297.1 seconds in the Planner. Its first
Plan Reviewer reached the configured 480-second limit without a report.
AutoResolver admitted a tool-free report recovery. That packet contained the
saved plan and reports, plus a path to the interrupted event log, while explicitly
forbidding log inspection. The completed reads and command outputs from the
first review were absent from the packet. The replacement reviewer therefore
could not use those observations, despite being told to reuse retained evidence.

Recovery prompts now include bounded completed tool observations from stopped,
archived reviews of the exact same contract and unchanged source. Both source
snapshots must match the current revision; artifact paths must stay inside this
run. The original events remain intact, with their byte hash named alongside the
observations. Failed commands retain their exit status, truncation and omitted
rows are explicit, and assistant text is not promoted into tool evidence.

This supplies historical observations, not a completed review or verification
authority. Review admission, tool restrictions, accounting, exact-plan approval
and independent verification remain unchanged. Explicit abandonment can receive
the same observations without acquiring an automatic report-recovery grant.

The public planning-packet regression fails before the fix. Focused tests cover
source and contract changes, live/unretired attempts, containment, missing and
oversized artifacts, malformed events, failed commands, bounded output, ordinary
reviews and explicit abandonment. Five guard mutations are rejected by the tests.
The existing prompt renderings stay unchanged outside this recovery case.

The live trial was interrupted and its attempts retained. A passing continuation
and independent delivery oracle are still required before claiming this fixes
live completion or reduces its measured cost and latency.

# Native command identity during report repair (#2212)

Two advisories from the matched #2113 reviews reproduce through the public
check verifier. A completed native part first naming one command without an
integer exit, then a different command with exit zero, was accepted when a
report omitted command/exit metadata. The conflict scan had discarded the
first completed identity because its exit was missing. Conversely, an unrelated
sessionless row could make a valid part ambiguous even though native
normalization ignored that row, causing unnecessary report repair.

The scan now considers the same session-owned native rows as normalization and
tracks known command, supplied message and integer-exit facts separately.
Missing metadata can be filled by later updates for one operation; it never
supplies an exit, and conflicting known facts remain ambiguous. A final
normalized execution with an integer exit is still required. Receipt-only
providers, exact command matching, attempt ownership and atomic refusal remain.

Public TaskRun fault controls demonstrate both defects on unchanged master:
the conflict incorrectly completes, and sessionless noise prevents completion.
The corrected conflict stays uncompleted and never reaches Completion Reviewer;
noise and same-command refinement each finish after one report-only Tester
repair with fresh clean replay. Builder is not repeated, and Tester repair
executes no new command. These are model-free protocol faults, not claims of a
new naturally occurring live incident, latency reduction or provider bill.

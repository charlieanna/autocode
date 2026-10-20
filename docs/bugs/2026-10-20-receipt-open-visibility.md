# Initial receipt visibility during atomic replacement

Qualification encountered `check_ownership` failures with `receipt_phase=open` and
`receipt_reason=not_found`, even though the matching receipt was visible later.
The later file alone does not establish why every live interruption happened.

A separate credential-free, network-disabled container reproduced this failure
class with the exact `atomic_json` writer and single-descriptor receipt reader
from master `0ce7bd8f58051ca3a33b4d8172e23fb28640c092`. Two readers observed
2,385 initial-open failures in 23,686 reads on a fresh Mac `virtiofs` bind during
3,573 writes. The container-local `overlay` control observed zero failures in
314,385 reads during 5,054 writes. Both 26,804-byte synthetic receipts were
initialized and read successfully before the 20-second concurrent experiment;
writers reported no publication errors and never deleted the destination.

This differs from #1662's old separate-stat/read race: no descriptor existed yet
when the pathname lookup failed. The reader now makes at most five initial-open
attempts, with four 25 ms pauses (100 ms cumulative). It retries only
`FileNotFoundError` from that open. Once opened, the same descriptor must pass the
unchanged size, content, schema, nonce and process-identity checks. Persistent
absence and every other failed check still refuse ownership; no cached receipt,
admission, cleanup discharge or execution allowance is inferred from absence.

Deterministic tests fake the pauses and cover recovery to matching evidence,
persistent absence, foreign identities, malformed content and access denial.
The final exact-reader 20-second probe completed 8,040 bind reads and 4,299
writes with zero failed reads or publications, while exercising 821 real retry
pauses. The local control completed 391,502 reads and 5,651 writes with zero
errors or retries. An unchanged real Linux public-handoff regression also
passed. The 100 ms bound is cumulative requested retry wait, excluding file I/O
and scheduling latency. These experiments establish a reproducible failure
mechanism and bounded mitigation; successful qualification of the interrupted
live workflow remains separate.

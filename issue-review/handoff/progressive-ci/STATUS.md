# Progressive test fixture follow-up

The historical #3971 Linux suite exposed six progressive tests whose scripted
Tester PASS cited only approved checks. The same six failed on current master
`9e67329a` at the independent-probe admission, before their intended assertions.

The fixture now executes and cites a separate, asserting greeting probe with the
input `Grace`, alongside the approved suite. Both progressive harnesses record
the real command event; no product admission or verification gate changes.
The six reproduced tests pass on this branch. Broader module checks, Ruff,
mypy and exact-head required CI remain to be completed.

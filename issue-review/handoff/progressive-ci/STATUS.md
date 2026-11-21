# Progressive test fixture follow-up

The historical #3971 Linux suite exposed six progressive tests whose scripted
Tester PASS cited only approved checks. The same six failed on current master
`9e67329a` at the independent-probe admission, before their intended assertions.

The fixture now executes and cites separate, asserting greeting and invalid-name
probes with `Grace` and a tab-only name, alongside the approved suite. Both
progressive harnesses record the real command event; no product admission or
verification gate changes. The invalid-name control was added after independent
review; the harness refuses to cite this probe for any criterion other than C1.
Repeat the focused tests for the updated probe.
The six reproduced tests pass with the two-input probe on the branch. All 42
tests across both affected modules pass against master `6b803f17` with the
previous valid-name probe; the two-input change then passed the six reproduced
tests. Repository Ruff lint and format and mypy (990 source files) pass on the
integrated tree, and the updated probe passes its focused Ruff checks. Require
exact-head CI before merge.

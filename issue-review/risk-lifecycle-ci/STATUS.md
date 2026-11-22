CI attributed all nine failures in the #4050 Ubuntu lifecycle suite to its
pinned master base. See #4070 for the original job and attribution.

The first isolated correction exposed a second defect: the scripted Tester
could not supply a checked reason for a closing delimiter with no mutation
site. The complete fixture change preserves exact source and test pins and
requires actual parent behavior evidence before supplying each reason.
The separate assertion controls fail on master and pass with the fixture.

On master bf75a2c9 plus this fix, 205 checks passed including fixture and CI
selection controls and the public lease queue reference with hard-death,
contention, and delivery invalidation. The full lifecycle module on the earlier
base is still under observation. The current draft requires CI before merge.

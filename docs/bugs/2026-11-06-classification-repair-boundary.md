# Investigator report repair abandoned at a run boundary

A completed Investigator can return a report that needs bounded report-only repair.
If a run-wide limit intervenes before that repair launches, the controller previously
abandoned the classification because its original provider attempt had already run.
The public view then asked for a plan change while the unfinished repair prevented
another classification retry. A time limit had become an unrelated plan gate.

The controller now preserves the same classification and its pending report repair
at time, cost, iteration and transport boundaries. Ordinary report admission still
checks source identity, evidence pins and repair limits. Other failures still abandon
the classification normally.

For a checkpoint already abandoned by the old behavior, the existing token-bound
classification action can continue its remaining report-only repair. It checks the
retained boundary failure, exact request, attempts, source, artifacts and settings.
It keeps the original reservation and repair count; the old failed history snapshot
is recorded in the recovery event. It neither launches a fresh investigation nor
renews Builder retries, approves a plan or raises a spending limit.

Regression coverage exercises the controller boundaries and the public CLI through
independent verification and completion, including recovery of an older checkpoint.
Negative cases cover exhausted repairs, unrelated failures, older reports, changed
attempt ownership and disabled investigation. The unchanged runtime fails the new
boundary and recovery regressions.

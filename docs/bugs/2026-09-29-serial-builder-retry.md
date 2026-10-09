# Exhausted serial Builder had no explicit recovery path

After the Resolver prepared a repair, the serial Builder policy could pause
before that repair became the current assignment. Ordinary resume correctly
preserved the spent allowance, but `--retry-builder` only accepted a parallel
batch. The operator could not authorize the diagnosed repair without changing
the contract or editing private state.

`--resume-paused --retry-builder M2` now admits one explicit serial retry after
checking current approval, milestone identity, stopped workers and reconciled
attempts. It records the grant in the existing Builder decision and user-event
histories. It preserves failures, model pins, configured limits and validation.
If a Resolver proposal awaits assignment, the normal Resolver path checks and
assigns it before the Builder runs. A subsequent failure exhausts the lane
again; ordinary resume still does not create another allowance.

The CLI handles this explicit recovery before publishing a generic operator
question. When a question was already issued, it retains the receipt verified
before resume bookkeeping, so an audited resolver-epoch event cannot invalidate
the recovery's own pause evidence. An invalid milestone selection leaves the
original pause intact. CLI regressions cover both pause forms, one failed
retry followed by another stop, preserved model pins, and a successful repair
through independent validation.

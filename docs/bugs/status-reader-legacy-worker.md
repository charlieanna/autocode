# Status reads can block plan approval

The legacy process guard treated any Python invocation mentioning `autocode.py`
as a writer. A concurrent `TaskRun.status()` could therefore block plan approval
with `PAUSED_WORKSPACE_BUSY`, although `--status` returns before the writer lock
and never launches a provider.

A reproduction without models confirmed the defect on master `9049a29b` and
Arena source `5b0d3a31`: capture the public client's status argv, supply it as a
synthetic process listing, and attempt approval through the CLI boundary. The
approval stops before the build unit. The SymPy attempt named PID 87292, but its
argv and birth identity were not retained and it had exited before inspection.
That historical PID's identity remains unknown; the reproduction establishes
the mechanism, not the cause of that particular attempt's error.

The guard now exempts only canonical `--status` or `--dry-run` commands with
both named absolute paths, optionally `--inspect-evidence` with status. Unknown
or mutating options, task text, ambiguous process text, `--show-goal`, provider
commands, and owned process markers keep their existing protection. In
particular, `--request-milestone-checkpoints` writes before the status shortcut
and must never receive this exemption.
Process-listing continuation lines stay attached to their PID entry and keep
that ambiguous entry blocking; malformed leading rows refuse inspection.

Focused coverage: `python -m unittest tests.test_legacy_process`.

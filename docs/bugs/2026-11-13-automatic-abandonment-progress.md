# Automatic abandonment announced a manual resume (#3351)

Output-cap recovery and permitted-route recovery both call the shared abandonment
boundary with `actor="runner"`. That boundary saves `PAUSED_STAGE_ABANDONED`
before deleting the original artifacts, then the caller saves its continuation.
`autocode_status.persist` announced the intermediate checkpoint as a manual pause
and retained that message in progress history, even when the next save continued
automatically. The saved live-response fixture in
`tests/fixtures/output_cap/mimo-requirements-reasoning-length.jsonl` reproduces it
without a provider call.

The status message now describes the completed action: “Attempt set aside for
automatic recovery; partial work retained.” It applies only when the latest
abandonment event names the recovery checkpoint's exact attempt and its actor is
the runner. A current published question takes precedence. Manual, legacy,
unidentified and stale events keep the existing pause message. The caller still
names the actual next action, either a retry or an Investigator handoff.

The intermediate checkpoint and its stop reason are unchanged. If the continuation
save fails, the public view still exposes the pause and its resume action, while
the archived attempt and its accounted usage remain durable. No state field,
client action, approval, recovery bound or model route changed.

## Verification

Base: `f35298a459`. Before the runtime edit, the new regressions reproduced the
misleading message. With the final tests and that base's `autocode_status` loaded
in memory (the only changed runtime file), six assertions fail. The explicit
manual-pause and published-question controls still pass.

The candidate passes 96 focused tests:

```sh
$PY -m unittest tests.test_status_updates tests.test_output_cap_recovery tests.test_route_recovery
$PY -m unittest tests.test_activity_log tests.test_execution_checkpoints \
  tests.test_role_names tests.test_roles tests.test_usage
```

The recovery tests use the real dispatcher, abandonment, checkpoint writer and
public status projection, with recorded provider output and a fixed source
snapshot. They inspect emitted stdout/stderr, saved progress history, preserved
artifacts, accounted attempts and named resume actions. A write fault proves the
crash checkpoint stays usable. No provider is launched; these deterministic
checks run on macOS and are not a live-provider qualification.

A standard-library trace over six targeted regressions executes all nine changed
executable body lines. The function declaration, docstring, comments and blank
lines have no separately tested behavior. Eight in-memory mutations are caught
by assertion failures: ignoring the actor or attempt identity, accepting missing
identity, selecting the oldest event, treating an unrelated event as abandonment,
masking other pauses or a published question, and restoring the misleading text.
No mutated runtime source was written to the checkout.

Repository-wide Ruff lint, format and `git diff --check` pass. Linux CI remains
the qualification source for any supervised-provider checks selected by CI.

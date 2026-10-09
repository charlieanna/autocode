# A new run bypasses an orphaned Builder's checkout ownership

## Fix (2026-09-30)

Providers inherit the checkout's lock descriptor, so a controller crash before
the first durable process receipt cannot free the checkout for a second Builder.
The controller closes its copy without explicitly unlocking surviving children.
`autocode_checkout_lock.exclusive` also checks the previous holder's durable provider
receipt under the checkout lock before replacing its metadata. Live processes
are matched by saved birth identity. An unreadable receipt remains busy; dead
or reused PIDs do not block a new run. Releasing a controller retains the holder
while a provider is alive. The cross-run orphan attack and all lifecycle controls
now pass, including the forced pre-receipt crash and consuming a finished orphan's
report without another Builder. The first receipt-only fix missed the startup
race; its failed combined run is retained in the ignored evidence directory.

Historical reproduction on the unfixed revision follows.

Confirmed against `628b257df5ed6c07d00d3d59a94ee44fc8a078db` using the real CLI
and an offline scripted provider. No saved AutoCode state was edited.

If a controller crashes while its Builder remains alive, resuming that same run
correctly refuses to start a competing writer. Starting a **new** run with
`--in-place` in the same checkout bypasses the exclusion: it plans normally,
accepts its own valid approval, and launches another Builder while the original
Builder is still alive. Two different live provider processes were observed with
the same working directory and different run IDs. The test holds both at a
provider handshake before their writes; it proves concurrent authorization, not
that source corruption has already occurred. Releasing real Builders would let
them race over the same source and verification evidence.

Reproduce from the repository root with a Python interpreter containing psutil:

```sh
.venv/bin/python -m unittest \
  scenarios.test_adversarial_lifecycle.LifecycleAttacks.test_active_run_blocks_new_run_in_same_checkout \
  scenarios.test_adversarial_lifecycle.LifecycleAttacks.test_live_orphan_blocks_new_run_in_same_checkout -v
```

The first test is the control: an active controller blocks a second run. The
second starts a greeting scenario normally, approves its plan through the CLI,
waits for the Builder's FIFO handshake, kills only the captured controller,
then starts and approves another run in that disposable checkout. It fails
visibly when both Builder identities are live. Process IDs, birth identities,
working directories and run IDs are saved in `overlapping-builders.json` beneath
the test's ignored `.scenario-runs/adversarial/` directory. Cleanup signals only
captured process identities. No live model calls are made.

The ownership checks have different scopes. `tools/autocode.py:3096` and `:3099`
call `assert_no_legacy_process` with the selected run directory.
`tools/autocode_support.py:47` looks for that run's process marker, and `:71–84`
matches process command lines against that run's path. A new run therefore does
not inspect the old run's live provider. The checkout lock acquired at
`tools/autocode.py:3708` is held by the controller in
`tools/autocode_checkout_lock.py:62–77`; killing that controller releases its
flock while the provider survives. The next controller acquires the now-free
lock and overwrites its holder metadata.

Checkout ownership must remain effective until every provider capable of
writing there has stopped, including providers owned by a different run ID.
Any fix should retain the passing controls for active-controller exclusion,
same-run orphan exclusion, and consumption of a finished orphan's report
without repeating the Builder. The failing test remains an ordinary assertion,
not an expected failure.

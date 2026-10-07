# Every AutoResolver evaluation refused a contract sealed with brief acceptance

Found by the #452 live qualification run (`greenfield-todo-cli`, profile
`glm53-mimo`, 2026-10-06): after a harness timeout killed the CLI mid-validation,
the run paused `PAUSED_RESOLVER` with "invalid contract or declarative input".
Setting the killed attempt aside and relaunching the Tester reproduced the same
refusal — a permanent loop: no resolver evaluation could ever run again.

## Cause

#522 seals `goal_contract.body.brief_acceptance` (the independently reviewed
original-brief observations). `autocode_goals`' body schema knows the key, but
`autocode_resolver._validate_body` required the body's key set to be exactly
`_REQUIRED` or exactly `_REQUIRED | {"initial_task"}`. A contract carrying
`brief_acceptance` matched neither, so every resolver request — including the
operational recovery after the kill — was refused before evaluation. Offline
fixtures never combined a sealed body with a resolver evaluation, so nothing
caught it; the fake reference run completes without ever needing the resolver
after sealing.

## Fix

`_validate_body` now requires every required key and rejects unknown keys, and
lets known-optional keys combine freely (`_REQUIRED <= keys and not keys -
_KNOWN`, with `risk_acceptance` also known — the goals schema names both). The
previous exact-set form made any future body extension a live landmine: the
contract legitimately carries the key, so refusing evaluation can never be the
right answer. Unknown keys still fail closed.

## Regression

`tests.test_resolver_unit.test_runner_sealed_acceptance_keys_do_not_block_resolution`
evaluates a body sealed with `brief_acceptance` (and one also carrying
`risk_acceptance`), and keeps the unknown-key escalation.

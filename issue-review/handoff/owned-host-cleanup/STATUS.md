# Owned editor-host cleanup

Base: master f5c47648d7. Merged as 7a569cd735f3f3134be103b40b98b41ca1c7bf4e
after exact-head e0bbddf5f4e90383cc1ac45197e35e7a37799fea passed
`ci-required`, the Ubuntu suite, actual Linux VS Code Extension Host, lint and
diff coverage. The editor host explicitly passed the active-writer refusal and
subsequent accepted answer; log `/private/tmp/autocode-4036-e0bb-editor.log`.

The quota host test assigned its environment before constructing its offline
fixture but inherited `AUTOCODE_HOME` until that fixture succeeded. A failed
setup therefore ran warm-host stop and status against the inherited registry.
Its teardown could also stop after one cleanup error, leaving later owned
resources unexamined. This is a test-harness defect; the product's host
lifecycle is unchanged.

The fixture now chooses its own app registry and config before the first
subprocess, retains HOME and CODEX_HOME unchanged, validates the returned
registry, and runs warm-host cleanup only after ownership is established.
Owned writer release, editor close, host stop and host check are attempted
independently; the original test failure and cleanup failures are aggregated,
and failed fixtures are retained.

A deterministic VM control runs the actual host test callback with the fixture
subprocess forced to fail. All subprocesses are stubbed; no real host stop or
model call occurs. On master it fails because stop and status are attempted for
`/synthetic/inherited/operator` (`/private/tmp/autocode-owned-host-master-regression.o24Dfn/result.log`);
on the candidate no host call occurs and the failed fixture is retained. Four
additional cleanup-order and ownership controls pass; the full extension suite
passed 798 tests (22 skipped), and typecheck passed. The genuine offline
quota fixture also succeeded with the private registry preselected
(`/private/tmp/autocode-private-registry-probe.log`).

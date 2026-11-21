# Scripted verification event identity (#4049)

The offline scenario provider used truncated command text for non-progressive
check-event IDs and a command-only hash for progressive ones. Distinct commands
with a common prefix and repeated identical commands could therefore share an ID.
The SemVer M1 rehearsal stopped because two real zero-exit checks had the same
ID; its Validator correctly refused ambiguous evidence. The failed attempt and
original event files remain at `/private/tmp/autocode-semver-reference-host-preflight-8/`
in the investigation environment.

Each `run_verify` call now creates an ID from a per-provider-invocation ordinal
and the command hash. It emits the actual command, exit and output unchanged.
Report-only repairs keep their original event references. This does not change
product evidence admission; ambiguous old event IDs still fail closed.

The focused real-subprocess regression covers distinct common-prefix commands,
repeating the same command, ordinary and progressive modes, consumer
`verify_checks`, the unique historical reference, refusal of a duplicated
historical ID, and report-only repair without a new execution. It fails on
master af28 and passes on the candidate. The CI selector retains it whenever
`scenarios/harness/fake_codex.py` changes.

Focused tests passed (161 across the identity, selection, event-contract and report-envelope suites); Ruff and mypy passed. The exact current-head CI and merged state are pending. This fixture-only fix
is not proof that the SemVer task or its VS Code extension run completed.

# Non-executable deliveries must fail checks without crashing the oracle

The Codex-only rerun of `port-policy-go` at `b0e8d8ea` delivered a root Go library
and a CLI under `cmd/policy`. The oracle built the root with
`go build -o policy.bin .`, which succeeds but writes a non-executable Go archive.
Attempting to execute it raised `PermissionError`, discarding the oracle checks.
An executable file with an invalid format similarly raised `OSError` (ENOEXEC).

The oracle command helper now returns exit 126 and the OS diagnostic for launch
errors, retaining exit 127 for a missing executable. The Go oracle includes that
diagnostic in failing vectors. The scenario brief explicitly requires a root
`main` package and its new library-layout negative control must fail normally.
This does not turn the original live result into a pass or alter its delivery.

The same campaign found prose replayed as shell arguments. Current master already
contains its fix in `2dae8e4b`; the exact two live sentences and their backtick
control are added to the verification-plan regressions here.

Regression evidence: both execution-error cases and the new catalog negative
control failed before the change, then passed after it. The original live run
and supplemental checks remain in the ignored scenario evidence directory.

## Archive reference skipped corrupt directory payloads

The live Completion Owner caught a separate archive defect: trailing-slash
entries were created without reading their payload. The scenario reference had
the same omission and the hidden oracle only corrupted regular-file payloads.
The reference now consumes directory data in bounded chunks so zipfile checks
its CRC and compression before publishing the staged destination. New hidden
cases cover stored and deflated corruption with absent and existing-empty
destinations, including staging cleanup. The previous reference is retained as
`broken/ignores-directory-crc`. The new case fails before the fix and passes
after it; the broken variant still fails.

## macOS CI fixture maintenance race

CI exposed an unrelated race in the permanent-delete endpoint fixture: Git's
background maintenance removed `.git/objects/maintenance.lock` while `copytree`
was copying the shared seed. Disable automatic maintenance and GC in that
disposable seed before its first commit; copies inherit the same setting.
No dashboard production behavior changes.

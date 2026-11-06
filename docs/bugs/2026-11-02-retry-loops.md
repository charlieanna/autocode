# Avoidable retry loops (2026-11-02)

Measured on the live etcd run (five recoveries, $4.09, 16 min) and six live master runs
(`claude-tiers`, one run each, $5 cap). Causes found and fixed here:

1. **Tester-only changed lines.** A changed line with no mutation outcome (def headers,
   imports, non-literal assignments, all Go/JS/TS lines) cannot be marked tested by any
   Builder test, yet the Builder was sent back up to three times, then the Investigator.
   Such a PASS now goes back to the Tester for one `changed_code_exceptions` decision per line.
2. **Go nested modules.** Coverage paths of a nested `go.mod` did not map to repository files.
3. **Capture from a subfolder.** A relative `.autocode/evidence/x.json` created a stray
   `.autocode` in the subfolder; the Builder attempt was rejected for files outside its paths.
4. **Shortened capture output.** Testers piped capture through `| tail`, `| grep` or
   `>/dev/null`, dropping the receipt line the runner authenticates ("No independently
   executed Validator tool event matches receipt"). Five of eight Tester rejections in the
   baseline runs. Capture now prints `{"autocode_receipt_sha256": ...}` on stderr, which
   authenticates the exact receipt bytes.
5. **Investigator probes** that ran a new test the runner's copy did not contain: the
   prompt and error now say to write the test inside the probe.

Run evidence is in the PR description; run directories are not committed.

## Follow-up (2026-11-06)

Live before/after, `claude-tiers`, one run each, $5 cap (master, then the fix):

| Scenario | Master | Fix |
| --- | --- | --- |
| bugfix-go-interval-boundary | PASS $2.59, 5.9 min | PASS $2.55, 5.8 min |
| bugfix-vitest-half-open-bookings | PASS $2.12, 6.2 min | PASS $3.13, 7.5 min |
| feature-go-first-package | PASS $3.46, 10.2 min | PASS $3.01, 6.9 min |
| feature-vitest-ledger-subtotals | blocked at cap $5.02 | PASS $1.72, 4.7 min |
| parallel-diamond | blocked $4.36 (replan) | PASS $3.52, 9.5 min |
| bugfix-monorepo-package | blocked at cap $5.05 | blocked $2.52 |

The Vitest bug fix regressed because its Tester ran capture with `2>&1 | grep -E '"exit_code"|Tests ' | cut -c1-300`:
the receipt line was cut and the attestation line was filtered out. The attestation line now leads with
`"exit_code"`, so a filter that keeps exit-code lines keeps it whole; when a filter keeps nothing
(`2>&1 | grep -o ...`), the rejection names that filter so the fresh Tester attempt runs capture unfiltered.

Still open: the monorepo run (without `--package`) checks the suite with root `unittest discover`, which
finds 0 tests when the package folders have no `__init__.py`, and asks a person to allow one.

Testers' bare `event:` checks were refused as "not supported by an exact executed Validator event" in five
reports: each cited a command it had run only inside a longer Bash call (`cmd 2>&1 | tail -15`, `a; b; c`),
so that command's own exit code is unknown. They are still refused; the rejection now names the call, the
Claude prompt says to run each cited command as its own call, and the exact retry still recognizes the
explained message (`autocode_report_retry.retryable`).

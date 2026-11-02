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

# Exact output transport

AutoCode can shorten verified passing unittest output while retaining every
failure, diagnostic and original byte. New runs use `--tool-output-mode
conservative`; use `--tool-output-mode raw` to display everything. This policy
applies to AutoCode's `capture` and `output` commands. Native provider tools
are not intercepted, and their permissions stay unchanged.

```sh
autocode "Fix the parser" --workspace /path/to/project --tool-output-mode raw
```

Saved runs retain their policy. Runs created before this setting use raw mode.
Change a saved policy only at a reconciled pause, with `--resume-paused
--tool-output-mode raw` (or `conservative`). An active or uncertain attempt
must be reconciled first. A single capture can always request raw output with
`--no-compress`.

## Commands

Run these inside the target project. Every capture needs a unique receipt name:

```sh
autocode capture --output .autocode/evidence/tests-001.json -- python -m unittest -v
autocode capture --no-compress --output .autocode/evidence/tests-002.json -- python -m unittest -v
autocode output read src/parser.py --start-line 40 --end-line 90
autocode output read src/parser.py --known-sha256 FULL_FILE_SHA256
autocode output retrieve FULL_FILE_SHA256 --raw --start-line 40 --end-line 90
autocode output retrieve FULL_FILE_SHA256 --raw
autocode output status
```

Only `python[version] -m unittest ...` with a recognized completion footer and
matching exit status is filtered. Repeated diagnostics stay verbatim. Unknown
commands, incomplete runs and unrecognized output stay raw. The displayed
omission markers identify original line ranges. JSON is kept as original text;
binary output is base64 encoded in JSON and retrievable as exact bytes.

`output read` returns exact lines, full-file SHA-256, original byte length and
omitted ranges. Conservative reads default to 200 lines; raw mode returns the
whole file unless an explicit range is requested. The complete original is
retained before any omission. `--known-sha256` is an explicit assertion that
the caller already knows that exact file identity; it is not a conversation
cache inferred by AutoCode. An unchanged result includes a retrieval command.
A changed file returns its new content and identity. Its earlier version stays
retrievable, even after the file is deleted or a process restarts.

`capture --known-output-sha256 HASH` can omit an identical display after exact
retrieval is verified. It **still executes the command**, returns its fresh exit
status and creates a fresh receipt. References, shortened displays and earlier
PASS results never substitute for current verification or independent acceptance.

## Recovery and retained data

The full command log remains beside its receipt. Exact copies live in
`.autocode/output/blobs`, named by their full SHA-256. Existing artifacts are
never overwritten or evicted automatically. Partial writes cannot become
valid retained artifacts. Corrupt or unavailable storage makes a read/capture
fall back to complete output; retrieving corrupt bytes fails explicitly.
A killed capture leaves its partial log without a completion receipt and
refuses reuse of that filename. Use a fresh name for a new execution.

Operation measurements live in `.autocode/output/operations`. The runner tags
them with the provider attempt and saves completed-stage totals; they survive
process restarts. They are best-effort observations, not proof. Do not delete
artifacts while a run or model session may still reference them.

This feature does not summarize conversations or code, rewrite existing
history, change session rotation, or shorten approved requirements, decisions,
permissions, model settings, unresolved findings or proof obligations.

## Measurement

`autocode output status` shows source/display bytes (including JSON metadata),
retrieval calls and unreadable measurement records. Byte differences can be
negative. They are not token counts or money saved.

`autocode --status` adds two fields under `view`:

- `output_transport`: saved display mode and completed-stage tool measurements;
  native tool calls are outside this measurement.
- `request_context`: the latest completed stage's individual request input
  counters and observed peak, including cache reads and writes. OpenCode
  `step_finish` events provide these counters. A native Codex cumulative turn
  total is not treated as a per-request context measurement.

The existing `usage` field remains cumulative. Missing counters are unavailable,
not zero. Active requests are outside the completed-stage projection. Comparing
raw and filtered runs must include retrieval/rework, cache usage, output tokens,
latency and failed attempts. API-equivalent repricing is an estimate, not
subscription billing, and a smaller display does not guarantee a cheaper run.

## Filter selection

RTK v0.51.0 was evaluated with the repeated-failure fixture from issue #214.
Its generic `rtk test` retained an exact recoverable original and exit 1, but
the displayed result omitted both tracebacks and both assertion messages.
AutoCode therefore uses the narrower unittest filter above. RTK's other filters
are not qualified by this test. See [RTK's configuration and retrieval guide](https://github.com/rtk-ai/rtk/blob/v0.51.0/docs/guide/getting-started/configuration.md).

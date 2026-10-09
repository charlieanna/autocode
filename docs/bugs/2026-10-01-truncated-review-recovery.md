# Truncated read-only review recovery

OpenCode can stop a Validator or Completion Owner response with finish reason
`length` before it emits a complete report. AutoCode recorded the provider usage
and paused the run, even when the stage had already run its checks and the source
was unchanged. Recovering by rerunning the review stage would repeat work and could
produce different evidence.

AutoCode now recognizes this case only for `sol`, `astra_review`, and
`astra_checkpoint` when the provider reports an output-token limit, exits cleanly,
has stopped, and the source snapshot is unchanged. It retains the original events
and usage, then queues a bounded report-only repair from the incomplete response
and pinned check evidence. The repair cannot run checks or change files. An
incomplete or exhausted repair remains paused with the evidence preserved.

This does not recover arbitrary malformed responses or provider failures. It is
not a substitute for a live-model campaign; the regression coverage uses a fake
OpenCode stream with the same `length` finish signal.

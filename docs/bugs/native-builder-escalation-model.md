# Native Builder escalation needs a bare OpenAI model ID

The 2026-10-05 hard Arena cohort `hard-baseline-5b0d3a31-20261005`
stopped the `pytest-stop-fixture-teardown` attempt
`4fea59a894d74a84bb9d987ee7e9a76d` after 2120.34 seconds. Its independent
oracle passed all nine check groups, but AutoCode did not accept the task:
the final runner status was `WAITING_FOR_USER`, with Arena verdict `STOPPED`.

The third Builder attempt used native Codex with a ChatGPT account. Builder
retry policy selected `openai/gpt-6-sol` at `xhigh`, retaining engine `codex`.
The provider rejected that model ID with HTTP 400 before any Builder command:
“The 'openai/gpt-6-sol' model is not supported when using Codex with a ChatGPT
account.” Earlier oracle passes do not turn this stopped attempt into a pass.

Builder escalation now removes only the `openai/` prefix for engine `codex`;
OpenCode retains its qualified model ID. Role engine overrides take precedence
over the run engine. Pins, provider restrictions, retry limits and checker
replacement policy stay the same. The cross-model dispatch guard recognizes
bare `gpt-*` and `openai/gpt-*` as the same exact GPT tier, so a pinned checker
cannot grade an escalated native Builder through a spelling difference.

Focused policy and guard tests cover these routes. No live rerun was performed,
and this change does not qualify the frozen cohort. The existing checker
replacement policy can still leave a qualified GLM replacement on a native
Codex checker route; that provider compatibility risk needs separate validation.

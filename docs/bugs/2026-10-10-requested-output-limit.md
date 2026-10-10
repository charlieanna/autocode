# Requested output limits on OpenCode's Codex route

AutoCode's length-stop explanation claimed its launch setting capped every response
and that increasing it would allow more output. A genuine OpenCode 1.18.33 run on
master `65cc3ed` passed a deliberately small `OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX`
to the unchanged native binary using `openai/gpt-6-luna`, medium effort. Both independently paired
native responses exceeded that setting. Requirements completed and the run paused at
its requested checkpoint.
No genuine length stop or cap-error display was exercised.

The pinned [OpenAI Codex plugin](https://github.com/anomalyco/opencode/blob/v1.18.33/packages/opencode/src/plugin/openai/codex.ts#L569-L573)
clears `maxOutputTokens` after the normal request code derives it. AutoCode's saved
`output_token_cap` records the requested setting or native default, rather than an
independently verified effective limit. Its explanation now distinguishes requested
settings from observed usage and qualifies advice to increase the setting. Metadata,
authentication, plugin configuration, accounting and recovery guards are unchanged.

This corrects AutoCode's explanation; it does not enforce a hard provider token budget
or reproduce the separate trailing-event classification concern in PR #938.

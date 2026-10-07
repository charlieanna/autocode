# A Claude-provider Builder ran out of attempts with no stronger model (#185)

**Seen:** 6 of 9 live runs of `discuss-then-design-then-build` on master after #614 (claude-tiers,
2026-10-07) stopped at `PAUSED_BUILDER_RETRY_LIMIT`: 4 in the design turn, 2 in the build turn. The
Builder runs Haiku. The Builder retry policy gives one ordinary retry and then one stronger attempt on
`openai/gpt-6-sol`, which the Claude provider cannot serve. Since 2026-09-29 a provider that lists its
models without the strong one gets no stronger attempt: one more ordinary retry, then the pause "this
run's provider offers no stronger Builder model". So every Claude run had three Haiku attempts and no
way up. `--builder-strong-model claude-sonnet-5-5` did not help: the Builder escalated to
`openai/claude-sonnet-5-5`, a name the tool cannot serve, and the Sonnet Tester stayed on Sonnet.

**Fix:** a tool registered with a TOML file may name its own stronger Builder model and the model its
checkers move to, in a `[builder_retry]` table (`strong_model`, `checker_model`, optional
`strong_effort`). The Claude example declares Sonnet for its Haiku Builder and Opus for checking that
attempt, so the Sonnet Tester moves to Opus for the rest of the milestone. On a tool whose Builder model
has no `provider/` prefix, the Builder escalates only when every checker on the strong model can move: it
runs exactly that model, is neither pinned nor on another provider, and the checker model is one the run
already routes a role to. Otherwise the run pauses before any route changes, and the stop reason says
why. A config without the table, and a tool that spells its models `provider/model` (KiloCode), keep the
earlier policy unchanged.

An adversarial review of a first version found that its new checks changed tools without the table: a
listed tool with GPT-6 Sol but not GLM refused every new run, and a KiloCode run given a bare
`--builder-strong-model` lost its `openai/` prefix. Both checks were removed, and the escalation rules
now apply only where the Builder's own model name is bare.

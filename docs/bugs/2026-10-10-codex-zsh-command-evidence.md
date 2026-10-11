# Codex zsh -c command evidence

Live Codex 0.161.0 conformance runs on GPT 6.1 Sol and GPT 6 Astra executed
both required probe commands with exits 0 and 7. The unchanged conformance
contract rejected their command evidence because native events used
`/bin/zsh -c`, while the shared matcher recognized only login-shell forms.

Recognize a well-formed, complete three-argument zsh invocation using `-c`.
Compare the entire unwrapped program text. Do not discard positional arguments,
operator chains, redirects or differences in quoted operators. The new form
does not use the legacy malformed-quote recovery for login-shell events.

Pure command-matching tests and the public conformance assessment cover valid
wrappers, real success/failure exit accounting and rejected extra executable
content. The conformance oracle is unchanged. Failed native probes remain
failed; qualification requires fresh native runs after the fix's gates pass.

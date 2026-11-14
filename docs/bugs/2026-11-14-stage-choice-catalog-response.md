# A saved stage choice must return its validated catalogue

`autocode stages set ... --json` validated a choice against the connected
backend's model catalogue, saved it, then queried the backend again to render
the response. If that second listing failed or became unavailable, a successful
save returned an empty options list and a discovery error. It also repeated
model-free setup work before the editor could show the saved choice.

The save now builds its response from the catalogue and defaults it already
validated, together with the complete choices it wrote. Models without explicit
levels still save their own default as no level; retained stages still constrain
which models their checkers may run. Each subsequent operation discovers afresh.
No listing retry bound, client timeout, model route or public field changes.

The regressions make the first discovery succeed and any second discovery fail,
then check the public JSON response and the saved choices. They also retain a
stage saved while discovery was running, check its independence restrictions,
and verify a later operation still refuses a failed discovery before saving.
These deterministic checks launch no backend or model. They do not establish
real-provider latency or actual editor verification.
